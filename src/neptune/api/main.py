"""Neptune API — vertical slice.

Endpoints:
  POST /portfolios/{id}/positions      enter a long/short position
  GET  /portfolios/{id}/positions      list positions
  GET  /portfolios/{id}/risk           net beta + factor exposures (Risk Interface)
  POST /portfolios/{id}/hedge/propose  optimizer proposes a systematic short basket

On startup the golden portfolio is seeded if the database is empty, so the slice is
runnable out of the box. Nothing here executes orders — the optimizer returns a
PENDING_APPROVAL proposal (invariant I-01).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datetime import date, timedelta

from sqlalchemy import select

from neptune.config import settings
from neptune.data.fixtures import GOLDEN_PORTFOLIO, golden_positions
from neptune.data.market import SyntheticMarketData, default_universe_tickers
from neptune.data.db_market import DbMarketData, TickerNotFound
from neptune.scheduling import scheduler as price_scheduler
from neptune.scheduling.scheduler import shutdown_scheduler, start_scheduler
from neptune.settings_store.app_settings import AppSettingsService
from neptune.db.base import SessionLocal, init_db, init_securities_db, make_engine
from neptune.db.models import PositionORM
from neptune.db.runtime import securities_session
from neptune.domain.models import BookType, LotEntry, Portfolio, Position, Side, ShortType, TradeAction
from neptune.domain.org import PersonRole
from neptune.pnl import CostBasisMethod, PnL
from neptune.quant.factors import STYLE_FACTORS
from neptune.quant.optimizer import (
    InfeasibleHedge,
    complexity_frontier,
    optimize_hedge,
    optimize_hedge_capped,
)
from neptune.risk import analytics
from neptune.risk import pnl as pnl_engine
from neptune.risk import stress as stress_engine
from neptune.risk.summary import summarize
from neptune.stress import STANDARD_SCENARIOS, Scenario
from neptune.positions.service import ConflictError, PositionService
from neptune.settings_store import ConnectionRole
from neptune.settings_store.service import ConnectionSettingsService
from neptune.securities.ingest import ingest_ticker
from neptune.securities.factor_ingest import ingest_factors
from neptune.securities.factor_providers import KenFrenchProvider
from neptune.securities.models import Price, Security
from neptune.securities.providers import YFinanceProvider
from neptune.universe import RecordedUniverse, SqlUniverse, UniverseSecurity, sync_universe_projection

# Synthetic market data — the fallback (and the candidate-universe source for hedging,
# which still runs on a synthetic shortable universe).
MARKET_DATA = SyntheticMarketData()
UNIVERSE_TICKERS = default_universe_tickers(60)

# Default price/factor backfill window: ~3 years. The beta pipeline needs 252 trailing days,
# so 3 years leaves ~1.5–2 years of dates with a full lookback — the history a walk-forward
# backtest replays over. (A specific start/end in the request still overrides this.)
DEFAULT_BACKFILL_DAYS = 365 * 3 + 30


def _shortable_universe(md):
    """The hedge candidate universe matching the market-data source: the REAL backfilled
    names (with pipeline betas + stored sectors) when on ``DbMarketData``, else the synthetic
    fallback universe. Both are built from the same source so betas are mutually consistent."""
    if isinstance(md, DbMarketData):
        return analytics.db_universe(md)
    return analytics.live_universe(md, UNIVERSE_TICKERS)


@contextmanager
def market_data_for(session: Session, portfolio):
    """Yield the market-data source for a portfolio. As long as the BENCHMARK is priced, the
    real ``DbMarketData`` source is used — individual names that aren't priced yet are handled
    gracefully downstream (sentinel beta, no mark), NOT by swapping the whole book to fake data.
    Synthetic data is the fallback ONLY when there is no real benchmark at all (a fresh DB or
    the offline test harness). The securities session is held open because reads are lazy."""
    sec_cm = securities_session(session)
    sec = sec_cm.__enter__()
    try:
        md = DbMarketData(sec, benchmark=settings.benchmark)  # raises iff benchmark unpriced
    except TickerNotFound:
        sec_cm.__exit__(None, None, None)
        yield MARKET_DATA  # no real benchmark → synthetic (fresh DB / tests only)
        return
    try:
        yield md
    finally:
        sec_cm.__exit__(None, None, None)


def _try_backfill_prices(session: Session, *tickers: str) -> None:
    """Best-effort: pull a full price history for newly-traded names so a real book stays on
    REAL market data instead of silently falling back to synthetic when a fresh ticker is added.
    Idempotent and non-fatal: names already well-priced are skipped, and any feed/network/
    missing-yfinance failure is swallowed (the trade is booked; pricing retries from Settings)."""
    from sqlalchemy import func

    end = date.today()
    start = end - timedelta(days=DEFAULT_BACKFILL_DAYS)
    provider = YFinanceProvider()
    try:
        with securities_session(session) as sec:
            for t in dict.fromkeys(tickers):
                iid = sec.scalar(select(Security.instrument_id).where(Security.ticker == t))
                if iid is not None:
                    bars = sec.scalar(
                        select(func.count(Price.ts)).where(Price.instrument_id == iid)
                    ) or 0
                    if bars >= 200:
                        continue  # already has enough history — don't re-pull
                try:
                    ingest_ticker(sec, provider, t, start, end, create_if_missing=True)
                except Exception:  # noqa: BLE001 — per-ticker feed error, non-fatal
                    continue
    except Exception:  # noqa: BLE001 — securities session/provider unavailable
        return


# --- request/response schemas ----------------------------------------------------

class LotIn(BaseModel):
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    entry_date: date


class PositionIn(BaseModel):
    ticker: str
    side: Side
    notional: float = Field(gt=0)
    short_type: ShortType = ShortType.NA
    forward_beta: float | None = None
    sector: str | None = None
    cost_basis_method: CostBasisMethod = CostBasisMethod.FIFO
    lots: list[LotIn] = Field(default_factory=list)
    pm_id: str | None = None
    analyst_id: str | None = None
    thesis: str | None = None
    target: str | None = None


class ReduceIn(BaseModel):
    quantity: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    as_of: date | None = None
    specific_index: int | None = None


class ScenarioIn(BaseModel):
    name: str
    market_shock: float = 0.0
    factor_shocks: dict[str, float] = Field(default_factory=dict)


class StressIn(BaseModel):
    """Custom scenarios to run in addition to the standard set, plus VaR parameters."""

    scenarios: list[ScenarioIn] = Field(default_factory=list)
    confidence: float = Field(default=0.95, gt=0.5, lt=1.0)
    horizon_days: int = Field(default=1, ge=1)


def _to_domain(p: PositionIn) -> Position:
    return Position(
        ticker=p.ticker,
        side=p.side,
        notional=p.notional,
        short_type=p.short_type,
        forward_beta=p.forward_beta,
        sector=p.sector,
        cost_basis_method=p.cost_basis_method,
        pm_id=p.pm_id,
        analyst_id=p.analyst_id,
        lots=[LotEntry(quantity=l.quantity, entry_price=l.entry_price,
                       entry_date=l.entry_date) for l in p.lots],
        thesis=p.thesis,
        target=p.target,
    )


def _pnl_dict(p: PnL) -> dict:
    return {"day": p.day, "total": p.total, "unrealized": p.unrealised, "realized": p.realised}


# --- lifespan: create tables + seed the golden portfolio -------------------------

def seed_golden(session: Session, *, with_demo_positions: bool = True) -> None:
    """Ensure the golden portfolio + ownership graph exist. Demo positions
    (AAA/BBB/CCC/DDD) are seeded only when ``with_demo_positions`` — a real book starts empty
    so a real benchmark can price every name (see ``market_data_for``)."""
    service = PositionService(session)
    pid = GOLDEN_PORTFOLIO["portfolio_id"]
    if service.get_portfolio(pid) is not None:
        return
    # The golden book belongs to Iridium (the internal management firm), runs for an
    # internal investor entity, and is led by one PM — exercising the ownership graph.
    service.create_firm("IRIDIUM", "Iridium Capital Management", is_internal=True)
    service.create_person("pm-iridium", "IRIDIUM", "Lead PM", PersonRole.PM)
    service.create_investor_entity("IRIDIUM-FUND", "IRIDIUM", "Iridium Master Fund")
    service.create_portfolio(
        pid, GOLDEN_PORTFOLIO["name"],
        firm_id="IRIDIUM", investor_entity_id="IRIDIUM-FUND", lead_pm_ids=["pm-iridium"],
    )
    if with_demo_positions:
        for pos in golden_positions():
            service.add_position(pid, pos)


def remove_demo_positions(session: Session) -> int:
    """Delete any golden DEMO positions (by ticker) from the golden portfolio. Idempotent —
    the cleanup for an existing book when demo seeding is turned off. Returns the count removed
    (lots cascade-delete with the position)."""
    pid = GOLDEN_PORTFOLIO["portfolio_id"]
    demo_tickers = {p.ticker for p in golden_positions()}
    rows = (
        session.query(PositionORM)
        .filter(PositionORM.portfolio_id == pid, PositionORM.ticker.in_(demo_tickers))
        .all()
    )
    for row in rows:
        session.delete(row)
    session.commit()
    return len(rows)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_securities_db()  # create the market-data schema too (idempotent)
    with SessionLocal() as session:
        seed_golden(session, with_demo_positions=settings.seed_demo_positions)
        if not settings.seed_demo_positions:
            removed = remove_demo_positions(session)  # clean an existing book
            if removed:
                logging.getLogger(__name__).info("removed %d demo position(s)", removed)
    start_scheduler()  # always-on price refresh (no-op if apscheduler isn't installed)
    yield
    shutdown_scheduler()


app = FastAPI(title="Neptune", version="0.1.0", lifespan=lifespan)


def get_session():
    with SessionLocal() as session:
        yield session


# The consolidated ("Consolidated") view: every book's positions rolled into one virtual
# portfolio. A read/analysis-only target — you cannot trade against it (pick a real book).
CONSOLIDATED_ID = "__consolidated__"


def _require_portfolio(service: PositionService, portfolio_id: str):
    """A real, mutable book. Rejects the consolidated sentinel — you trade into a real book."""
    if portfolio_id == CONSOLIDATED_ID:
        raise HTTPException(
            status_code=409,
            detail="Consolidated is a read-only roll-up; select a specific portfolio to trade.",
        )
    portfolio = service.get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    return portfolio


def _resolve_portfolio(service: PositionService, portfolio_id: str):
    """A book for read/analysis. The consolidated sentinel yields a virtual portfolio that
    concatenates every book's positions (the firm roll-up); otherwise a real book."""
    if portfolio_id == CONSOLIDATED_ID:
        positions = [p for b in service.list_portfolios() for p in b.positions]
        return Portfolio(id=CONSOLIDATED_ID, name="Consolidated", positions=positions)
    return _require_portfolio(service, portfolio_id)


# --- endpoints -------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "beta_tol": settings.beta_tol}


@app.get("/securities/health")
def securities_health(session: Session = Depends(get_session)):
    """Universe/benchmark data health for the UI: benchmark bar count, projected names, names
    with enough price history, names that actually produce a beta (regression fits against the
    benchmark), and whether the style-factor panel is loaded. Read-only; runs against an empty
    book so the numbers are universe-level (not portfolio-specific)."""
    empty = Portfolio(id="__health__", name="health", positions=[])
    with securities_session(session) as sec:
        out = {"benchmark": settings.benchmark}
        out.update(_universe_diag(sec, empty))
        return out


@app.get("/portfolios/{portfolio_id}/hedge/diagnostics")
def hedge_diagnostics(portfolio_id: str, session: Session = Depends(get_session)):
    """Explain WHY the hedge universe is what it is — the silent gates made visible.

    Reports which market-data source is actually in force for this book, the benchmark's
    bar count, how many backfilled names are usable hedge candidates, and how many survive
    after excluding the book's own longs. Critically, it names any unpriced position: a
    single one (or an unpriced benchmark) drops the ENTIRE book to the synthetic 60-name
    source, silently ignoring every real backfilled name. Read-only; never runs the solver.
    """
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    out = {"portfolio_id": portfolio_id, "benchmark": settings.benchmark}
    with securities_session(session) as sec:
        out.update(_universe_diag(sec, portfolio))
    return out


def _universe_diag(sec: Session, portfolio) -> dict:
    """The universe gate, made visible: which market-data source is in force and why, plus
    the candidate counts. Takes an open securities session; pure read."""
    from sqlalchemy import func

    long_tickers = {p.ticker for p in portfolio.longs}
    out: dict = {}
    # Raw projection∩prices count (independent of the benchmark), so we can report the real
    # universe size even when the benchmark itself is unpriced.
    out["securities_projected"] = sec.scalar(select(func.count(Security.instrument_id))) or 0
    usable = sec.execute(
        select(Security.ticker)
        .join(Price, Price.instrument_id == Security.instrument_id)
        .where(Security.ticker.isnot(None), Security.ticker != settings.benchmark)
        .group_by(Security.ticker)
        .having(func.count(Price.ts) >= 30)
    ).all()
    usable_tickers = sorted(t for (t,) in usable)
    out["names_with_30plus_bars"] = len(usable_tickers)
    out["sample"] = usable_tickers[:25]

    try:
        md = DbMarketData(sec, benchmark=settings.benchmark)
    except TickerNotFound as exc:
        out["source"] = "synthetic"
        out["reason"] = (
            f"benchmark {settings.benchmark!r} is not priced ({exc}); the whole book "
            f"falls back to the synthetic 60-name universe — your "
            f"{len(usable_tickers)} backfilled names are IGNORED for hedging."
        )
        return out

    benchmark_bars = len(md._series(settings.benchmark))
    out["benchmark_bars"] = benchmark_bars
    unpriced = sorted({p.ticker for p in portfolio.positions if _ticker_unpriced(md, p.ticker)})
    out["unpriced_positions"] = unpriced
    out["factor_panel"] = ("MKT+" + "+".join(STYLE_FACTORS)) if md._style_factors() else "MKT-only"

    # The TRUE shortable universe: names whose beta regression actually fits against the
    # benchmark — not merely names with >=30 of their own bars. The gap between these two is
    # the usual culprit: a short/misaligned benchmark fails EVERY regression, so price-bar
    # counts look healthy while the real universe is empty.
    universe = analytics.db_universe(md)
    regressable = {c.ticker for c in universe}
    out["names_with_computable_beta"] = len(regressable)
    candidates = [t for t in regressable if t not in long_tickers]
    out["candidates_after_excluding_longs"] = len(candidates)

    if unpriced:
        out["source"] = "synthetic"
        out["reason"] = (
            f"{len(unpriced)} position(s) have no stored prices "
            f"({', '.join(unpriced[:5])}{'…' if len(unpriced) > 5 else ''}); the whole book "
            f"falls back to the synthetic universe, so your {len(usable_tickers)} backfilled "
            f"names are IGNORED. Backfill these names (or remove them) to hedge real."
        )
    elif not regressable and usable_tickers:
        out["source"] = "db"
        out["reason"] = (
            f"{len(usable_tickers)} names have prices but NONE produce a beta — the benchmark "
            f"{settings.benchmark!r} has only {benchmark_bars} bars, so every regression fails. "
            f"Backfill {settings.benchmark!r} over the full window (it defines the date index)."
        )
    elif not candidates:
        out["source"] = "db"
        out["reason"] = (
            "real source is active but every regressable name is one of your longs — backfill "
            "more of the universe to get shortable candidates."
        )
    else:
        out["source"] = "db"
        out["reason"] = f"real backfilled universe in use ({len(candidates)} candidates)."
    return out


def _universe_diag_suffix(session: Session, portfolio) -> str:
    """A one-line diagnostic to append to an empty-universe hedge error."""
    try:
        with securities_session(session) as sec:
            d = _universe_diag(sec, portfolio)
    except Exception:  # noqa: BLE001 — diagnostics must never mask the original error
        return ""
    return (
        f" [diagnostics: source={d.get('source')}, "
        f"benchmark {settings.benchmark} bars={d.get('benchmark_bars', '?')}, "
        f"names with prices={d.get('names_with_30plus_bars')}, "
        f"names with a computable beta={d.get('names_with_computable_beta', 0)}, "
        f"candidates after excluding longs={d.get('candidates_after_excluding_longs', 0)}. "
        f"{d.get('reason', '')}]"
    )


def _ticker_unpriced(md: DbMarketData, ticker: str) -> bool:
    try:
        md.ticker_returns(ticker)
        return False
    except TickerNotFound:
        return True


def _safe_price(md, ticker: str) -> float | None:
    """The current mark, or None for a not-yet-priced name (no synthetic substitution)."""
    try:
        return round(md.current_price(ticker), 2)
    except TickerNotFound:
        return None


class PortfolioIn(BaseModel):
    """Create a portfolio (book). id is a short slug; the rest are optional ownership links."""

    id: str
    name: str
    firm_id: str | None = None
    investor_entity_id: str | None = None
    lead_pm_ids: list[str] = Field(default_factory=list)


@app.get("/portfolios")
def list_portfolios(session: Session = Depends(get_session)):
    """All books, for the portfolio switcher and the Total Book rollup."""
    svc = PositionService(session)
    return [
        {"id": p.id, "name": p.name, "firm_id": p.firm_id,
         "investor_entity_id": p.investor_entity_id, "lead_pm_ids": p.lead_pm_ids}
        for p in svc.list_portfolios()
    ]


@app.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioIn, session: Session = Depends(get_session)):
    svc = PositionService(session)
    if svc.get_portfolio(body.id) is not None:
        raise HTTPException(status_code=409, detail=f"portfolio {body.id} already exists")
    kwargs = {k: v for k, v in (
        ("firm_id", body.firm_id), ("investor_entity_id", body.investor_entity_id),
        ("lead_pm_ids", body.lead_pm_ids),
    ) if v}
    p = svc.create_portfolio(body.id, body.name, **kwargs)
    return {"id": p.id, "name": p.name}


class PersonIn(BaseModel):
    id: str
    firm_id: str
    name: str
    role: PersonRole
    email: str | None = None


@app.post("/people", status_code=201)
def create_person(body: PersonIn, session: Session = Depends(get_session)):
    """Register a firm person (PM / analyst / CIO / admin). Firm staff, not a client."""
    service = PositionService(session)
    try:
        service.create_person(body.id, body.firm_id, body.name, body.role, email=body.email)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="person id or firm invalid") from exc
    return {"id": body.id}


@app.post("/portfolios/{portfolio_id}/positions", status_code=201)
def add_position(portfolio_id: str, body: PositionIn, session: Session = Depends(get_session)):
    service = PositionService(session)
    _require_portfolio(service, portfolio_id)
    try:
        position_id = service.add_position(portfolio_id, _to_domain(body))
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _try_backfill_prices(session, body.ticker)
    return {"id": position_id}


class TransactionIn(BaseModel):
    """A manual executed trade: just a direction (BUY/SELL). The book is the portfolio; the
    side (long/short) and whether this opens/closes/covers is derived from the current
    holding by netting. sector/thesis/target are optional Fundamental-Layer inputs."""

    ticker: str
    action: TradeAction
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)  # execution (average) price per share
    fee_per_share: float = Field(default=0.0, ge=0)  # transaction fee per share
    trade_date: date
    sector: str | None = None
    thesis: str | None = None
    target: str | None = None


@app.post("/portfolios/{portfolio_id}/transactions", status_code=201)
def record_transaction(
    portfolio_id: str, body: TransactionIn, session: Session = Depends(get_session)
):
    """Book a manual Buy/Sell. Direction (long/short) and open/close/cover are derived from
    the current holding by netting — the desk picks only Buy or Sell. Systematic-short hedges
    are booked via the hedge-approval path, not here (invariant I-03)."""
    service = PositionService(session)
    _require_portfolio(service, portfolio_id)
    try:
        position_id = service.book_trade(
            portfolio_id, body.ticker, body.action, body.quantity, body.price,
            body.trade_date, sector=body.sector, thesis=body.thesis, target=body.target,
            fee_per_share=body.fee_per_share,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Price the new name immediately so the book stays on real data (never silently synthetic).
    _try_backfill_prices(session, body.ticker)
    return {"id": position_id}


@app.get("/portfolios/{portfolio_id}/positions")
def list_positions(portfolio_id: str, session: Session = Depends(get_session)):
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    # Compute the book's lead PM once (not per position) — the per-name fallback.
    lead_pm = portfolio.lead_pm_ids[0] if portfolio.lead_pm_ids else None
    with market_data_for(session, portfolio) as md:
        metrics = analytics.compute_metrics(portfolio, md)
        return [
            {
                "id": p.id,
                "ticker": p.ticker,
                "side": p.side.value,
                "short_type": p.short_type.value,
                "book": p.book.value,
                "notional": p.notional,
                "quantity": p.quantity,
                "price": _safe_price(md, p.ticker),
                "beta": round(metrics[p.ticker].beta, 4),
                "beta_method": metrics[p.ticker].beta_method,
                "cost_basis_method": (p.cost_basis_method or CostBasisMethod.FIFO).value,
                # Per-name coverage; pm falls back to the book's lead PM.
                "pm_id": p.pm_id or lead_pm,
                "analyst_id": p.analyst_id,
                "pnl": _pnl_dict(pnl_engine.position_pnl_for(p, md)),
            }
            for p in portfolio.positions
        ]


@app.post("/portfolios/{portfolio_id}/positions/{position_id}/reduce")
def reduce_position(
    portfolio_id: str,
    position_id: int,
    body: ReduceIn,
    session: Session = Depends(get_session),
):
    """Close part (or all) of a position by its cost-basis method (sell long / cover
    short). Returns the realised P&L of this reduction. Never routes an order anywhere —
    it records the lot accounting a human has decided on."""
    service = PositionService(session)
    _require_portfolio(service, portfolio_id)
    position = service.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail=f"position {position_id} not found")
    try:
        realised = service.reduce_position(
            position_id, body.quantity, body.exit_price, body.as_of, body.specific_index
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"position_id": position_id, "realized_pnl": realised}


@app.get("/portfolios/{portfolio_id}/pnl")
def portfolio_pnl(portfolio_id: str, session: Session = Depends(get_session)):
    """Four P&L dimensions for the book, split by Long / Systematic / Discretionary."""
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    with market_data_for(session, portfolio) as md:
        result = pnl_engine.portfolio_pnl(portfolio, md)
    return {
        "portfolio_id": portfolio_id,
        "total": _pnl_dict(result.total),
        "by_book": {book.value: _pnl_dict(p) for book, p in result.by_book.items()},
    }


@app.post("/portfolios/{portfolio_id}/refresh-prices")
def refresh_prices(portfolio_id: str, session: Session = Depends(get_session)):
    """Re-pull the latest prices for this book's tickers + the benchmark (a recent window,
    so today's live bar is updated), so day P&L reflects current prices. Requires yfinance;
    returns 503 if it's unavailable. Per-ticker failures are collected, not fatal."""
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    tickers = {p.ticker for p in portfolio.positions} | {settings.benchmark}
    end = date.today()
    start = end - timedelta(days=7)  # short window — just refresh the latest bars
    provider = YFinanceProvider()
    updated, errors = 0, []
    with securities_session(session) as sec_session:
        for ticker in tickers:
            try:
                res = ingest_ticker(
                    sec_session, provider, ticker, start, end, create_if_missing=True
                )
            except RuntimeError as exc:  # yfinance not installed — global condition
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception:  # noqa: BLE001 — per-ticker feed error, non-fatal
                errors.append(ticker)
            else:
                updated += res.prices
    return {"updated_bars": updated, "tickers": len(tickers), "errors": errors}


@app.get("/portfolios/{portfolio_id}/risk")
def risk_summary(portfolio_id: str, session: Session = Depends(get_session)):
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    with market_data_for(session, portfolio) as md:
        metrics = analytics.compute_metrics(portfolio, md)
    net_beta, net_factors = analytics.net_metrics(portfolio, metrics)
    # Market is shown by the net-beta gauge; the factor table covers the style factors.
    style_factors = {f: net_factors[f] for f in STYLE_FACTORS}
    summary = summarize(
        net_beta=net_beta,
        factor_exposures=style_factors,
        long_aum=portfolio.long_aum,
        beta_tol=settings.beta_tol,
        factor_limit=settings.factor_limit,
    )
    return {
        "portfolio_id": portfolio_id,
        "net_beta": summary.net_beta,
        "beta_tol": summary.beta_tol,
        "beta_status": summary.beta_status,
        "beta_neutral": summary.beta_neutral,
        "long_aum": summary.long_aum,
        "headline": summary.headline(),
        "factors": [
            {"factor": f.factor, "exposure": f.exposure, "limit": f.limit, "status": f.status}
            for f in summary.factors
        ],
    }


@app.post("/portfolios/{portfolio_id}/hedge/propose")
def propose_hedge(
    portfolio_id: str,
    sector_limit: float = Query(
        default=settings.sector_limit, gt=0.0, le=1.0,
        description="Flag any GICS sector exceeding this fraction of short notional.",
    ),
    max_names: int | None = Query(
        default=None, ge=1,
        description="Hard cap on the number of hedge names. None = the natural sparse basket.",
    ),
    session: Session = Depends(get_session),
):
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    long_tickers = {p.ticker for p in portfolio.longs}
    try:
        with market_data_for(session, portfolio) as md:
            metrics = analytics.compute_metrics(portfolio, md)
            residual_beta, residual_factors = analytics.residual_metrics(portfolio, metrics)
            universe = _shortable_universe(md)
            common = dict(
                residual_beta=residual_beta,
                residual_factors=residual_factors,
                universe=universe,
                long_aum=portfolio.long_aum,
                beta_tol=settings.beta_tol,
                factor_limit=settings.factor_limit,
                max_position_weight=settings.max_position_weight,
                sector_limit=sector_limit,
                excluded_tickers=long_tickers,
            )
            # Default: the natural sparse basket (L1 gross penalty). With an explicit max_names
            # the user trades exactness for a hard name-count cap (capped runs are soft, so the
            # proposal's net_beta_after reports whether neutrality was still achieved).
            proposal = (
                optimize_hedge_capped(n_cap=max_names, **common)
                if max_names is not None
                else optimize_hedge(**common)
            )
    except (InfeasibleHedge, ValueError) as exc:
        # Cannot hedge to neutral with this universe — a domain state, not a 500. Append the
        # live universe diagnostics so the message is self-explanatory (why is it empty?).
        detail = str(exc)
        if "shortable candidates" in detail:
            detail += _universe_diag_suffix(session, portfolio)
        raise HTTPException(status_code=422, detail=detail) from exc
    return {
        "portfolio_id": portfolio_id,
        "status": proposal.status,
        "net_beta_before": proposal.net_beta_before,
        "net_beta_after": proposal.net_beta_after,
        "long_aum": proposal.long_aum,
        "proposed_shorts": [
            {"ticker": s.ticker, "notional": round(s.notional, 2), "beta": s.beta,
             "sector": s.sector}
            for s in proposal.positions
        ],
        "sector_limit": proposal.sector_limit,
        "sector_breaches": proposal.sector_breaches,
        "sectors": [
            {"sector": s.sector, "notional": round(s.notional, 2),
             "fraction": s.fraction, "limit": s.limit, "breach": s.breach}
            for s in proposal.sectors
        ],
    }


@app.post("/portfolios/{portfolio_id}/hedge/frontier")
def hedge_frontier(portfolio_id: str, session: Session = Depends(get_session)):
    """Complexity-quality frontier: capped runs (N<=10/20/50) showing the trade-off
    between position count and hedge quality (tracking error / net beta)."""
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    long_tickers = {p.ticker for p in portfolio.longs}
    with market_data_for(session, portfolio) as md:
        metrics = analytics.compute_metrics(portfolio, md)
        residual_beta, residual_factors = analytics.residual_metrics(portfolio, metrics)
        universe = _shortable_universe(md)
        runs = complexity_frontier(
            residual_beta=residual_beta,
            residual_factors=residual_factors,
            universe=universe,
            long_aum=portfolio.long_aum,
            beta_tol=settings.beta_tol,
            factor_limit=settings.factor_limit,
            max_position_weight=settings.max_position_weight,
            excluded_tickers=long_tickers,
        )
    return {
        "portfolio_id": portfolio_id,
        "net_beta_before": residual_beta,
        "frontier": [
            {
                "n_cap": r.n_cap,
                "n_selected": r.n_selected,
                "net_beta_after": r.net_beta_after,
                "tracking_error": r.tracking_error,
                "beta_within_tol": r.beta_within_tol,
                "solver_status": r.solver_status,
            }
            for r in runs
        ],
    }


@app.post("/portfolios/{portfolio_id}/stress")
def stress(
    portfolio_id: str,
    body: StressIn | None = None,
    session: Session = Depends(get_session),
):
    """Scenario shocks (P&L impact split by book) plus VaR/ES by three methods
    (parametric, historical simulation, Monte Carlo). The standard scenario library
    always runs; custom scenarios in the body are appended."""
    body = body or StressIn()
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)

    scenarios = list(STANDARD_SCENARIOS) + [
        Scenario(name=s.name, market_shock=s.market_shock, factor_shocks=s.factor_shocks)
        for s in body.scenarios
    ]
    results = stress_engine.run_scenarios(portfolio, MARKET_DATA, scenarios)

    def _var(method: str):
        v = stress_engine.value_at_risk(
            portfolio, MARKET_DATA, confidence=body.confidence,
            horizon_days=body.horizon_days, method=method,
        )
        return {
            "method": v.method,
            "confidence": v.confidence,
            "horizon_days": v.horizon_days,
            "volatility": v.volatility,
            "var": v.var,
            "expected_shortfall": v.expected_shortfall,
            "n_observations": v.n_observations,
        }

    var_methods = [_var(m) for m in ("parametric", "historical", "monte_carlo")]
    return {
        "portfolio_id": portfolio_id,
        "scenarios": [
            {
                "name": r.name,
                "market_shock": r.market_shock,
                "total_pnl": r.total_pnl,
                "by_book": r.by_book,
            }
            for r in results
        ],
        # `var` stays the parametric result for backward compatibility; `var_methods`
        # carries all three for side-by-side comparison.
        "var": var_methods[0],
        "var_methods": var_methods,
    }


# --- Settings: configurable database connections ---------------------------------

class ConnectionIn(BaseModel):
    host: str
    port: int = 5432
    database: str
    username: str
    # Write-only: omit/null to leave the stored password unchanged; "" clears it.
    password: str | None = None
    sslmode: str | None = None
    driver: str | None = None


class PriceRefreshIn(BaseModel):
    """The always-on price-refresh interval in minutes (0 = disabled, max 1 day)."""

    minutes: int = Field(ge=0, le=1440)


@app.get("/settings/price-refresh")
def get_price_refresh(session: Session = Depends(get_session)):
    """Current server-side price-refresh interval (minutes; 0 = off)."""
    return {"minutes": AppSettingsService(session).get_price_refresh_minutes()}


@app.put("/settings/price-refresh")
def set_price_refresh(body: PriceRefreshIn, session: Session = Depends(get_session)):
    """Persist the interval and reschedule the running job (live, no restart)."""
    minutes = AppSettingsService(session).set_price_refresh_minutes(body.minutes)
    price_scheduler.reschedule(minutes)
    return {"minutes": minutes}


@app.get("/settings/connections")
def list_connections(session: Session = Depends(get_session)):
    """All configured DB connections, password-masked. Roles with no row fall back to env."""
    svc = ConnectionSettingsService(session)
    stored = {c.role: c.masked() for c in svc.list_all()}
    # Always report all three roles so the UI can render a complete form.
    out = []
    for role in ConnectionRole:
        entry = stored.get(role, {"role": role.value, "configured": False})
        entry.setdefault("configured", True)
        # The portfolio DB is the env-driven bootstrap; flag it so the UI marks it
        # restart-only.
        entry["bootstrap"] = role is ConnectionRole.PORTFOLIO
        out.append(entry)
    return out


@app.put("/settings/connections/{role}")
def upsert_connection(
    role: ConnectionRole, body: ConnectionIn, session: Session = Depends(get_session)
):
    svc = ConnectionSettingsService(session)
    cfg = svc.upsert(
        role, host=body.host, port=body.port, database=body.database,
        username=body.username, password=body.password, sslmode=body.sslmode,
        driver=body.driver,
    )
    return cfg.masked()


@app.post("/settings/connections/{role}/test")
def test_connection(role: ConnectionRole, session: Session = Depends(get_session)):
    """Open a throwaway connection to the resolved URL and run SELECT 1. Never exposes
    the password; returns ok/false plus a sanitized error message."""
    svc = ConnectionSettingsService(session)
    url = svc.resolve_url(role)
    if not url:
        raise HTTPException(status_code=400, detail=f"no connection configured for {role.value}")
    try:
        eng = make_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # noqa: BLE001 — surface a sanitized message, not the URL
        return {"role": role.value, "ok": False, "error": type(exc).__name__}
    return {"role": role.value, "ok": True}


@app.post("/settings/universe/sync")
def sync_universe(session: Session = Depends(get_session)):
    """Project the cato_securities universe into neptune_securities.securities. Reads the
    UNIVERSE connection read-only; if none is configured, falls back to the synthetic
    universe so the action is always runnable offline."""
    svc = ConnectionSettingsService(session)
    url = svc.resolve_url(ConnectionRole.UNIVERSE)
    if url:
        source = SqlUniverse(make_engine(url))
    else:
        # Offline / unconfigured: project the synthetic catalog so the table is populated.
        rows = [
            UniverseSecurity(
                instrument_id=abs(hash(t)) % 10_000_000, ticker=t,
                security_name=f"{t} Synthetic", security_type="Common Stock",
            )
            for t in UNIVERSE_TICKERS
        ]
        source = RecordedUniverse(rows)
    with securities_session(session) as sec_session:
        try:
            n = sync_universe_projection(sec_session, source)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"universe sync failed: {type(exc).__name__}") from exc
    return {"synced": n, "source": "cato_securities" if url else "synthetic"}


class IngestIn(BaseModel):
    """A price-ingestion request. Tickers default to the whole projected universe; the
    window defaults to a 252-day-plus lookback ending today (enough for the beta pipeline)."""

    tickers: list[str] | None = None
    start: date | None = None
    end: date | None = None


@app.post("/securities/ingest")
def ingest_prices(body: IngestIn, session: Session = Depends(get_session)):
    """Backfill OHLCV/dividends/splits from yfinance into the securities DB for the given
    tickers (or the whole projection). Requires network + the optional yfinance package, so
    it returns 503 when the feed is unavailable rather than failing opaquely."""
    end = body.end or date.today()
    start = body.start or (end - timedelta(days=DEFAULT_BACKFILL_DAYS))  # ~3 years
    provider = YFinanceProvider()
    results = []
    with securities_session(session) as sec_session:
        tickers = body.tickers or [
            s.ticker
            for s in sec_session.scalars(
                select(Security).where(Security.ticker.is_not(None))
            ).all()
        ]
        if not tickers:
            raise HTTPException(
                status_code=409, detail="no securities to ingest — sync the universe first"
            )
        # ALWAYS backfill the benchmark over the full window: it defines the date index for
        # every beta regression, so a short benchmark empties the whole hedge universe. The
        # 7-day "Refresh now" must never be its only source. Dedupe, benchmark first.
        tickers = list(dict.fromkeys([settings.benchmark, *tickers]))
        # Per-ticker failures are collected, not fatal: ingest is idempotent and
        # source-tagged, so a partial run is safe to resume. A missing-yfinance
        # RuntimeError is global, though — fail fast with 503 rather than N times.
        errors = []
        for ticker in tickers:
            try:
                # create_if_missing lets benchmarks/ETFs (e.g. SPY) be pulled even though
                # they're outside the common-stock universe projection.
                res = ingest_ticker(
                    sec_session, provider, ticker, start, end, create_if_missing=True
                )
            except RuntimeError as exc:  # yfinance not installed — applies to every ticker
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except LookupError as exc:
                errors.append({"ticker": ticker, "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 — feed/network error, sanitized
                errors.append({"ticker": ticker, "error": type(exc).__name__})
                continue
            results.append(
                {"ticker": res.ticker, "prices": res.prices,
                 "dividends": res.dividends, "corporate_actions": res.corporate_actions}
            )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "ingested": results,
        "errors": errors,
    }


class FactorIngestIn(BaseModel):
    """A factor-ingestion request. Window defaults to a ~400-day lookback ending today."""

    start: date | None = None
    end: date | None = None


@app.post("/factors/ingest")
def ingest_factor_panel(body: FactorIngestIn, session: Session = Depends(get_session)):
    """Backfill the Ken French daily factor panel (SMB/HML/MOM, plus Mkt-RF/RF) into the
    securities DB. Fetched from the Ken French Data Library's CSV zips over HTTPS, so it
    returns 503 when the feed is unreachable rather than failing opaquely."""
    end = body.end or date.today()
    start = body.start or (end - timedelta(days=DEFAULT_BACKFILL_DAYS))
    provider = KenFrenchProvider()
    with securities_session(session) as sec_session:
        try:
            counts = ingest_factors(sec_session, provider, start, end)
        except RuntimeError as exc:  # pandas-datareader not installed
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — feed/network error, sanitized
            raise HTTPException(
                status_code=502, detail=f"factor ingest failed: {type(exc).__name__}"
            ) from exc
    return {"start": start.isoformat(), "end": end.isoformat(), "counts": counts}
