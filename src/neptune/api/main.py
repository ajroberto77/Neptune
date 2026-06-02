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

import numpy as np
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
from neptune.domain.models import (
    BookType, LotEntry, Mandate, Portfolio, Position, Side, ShortType, TradeAction,
)
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
from neptune.risk import backtest as backtest_engine
from neptune.risk import beta_store
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
from neptune.securities.models import FactorReturn
from neptune.securities.providers import YFinanceProvider
from neptune.universe import RecordedUniverse, SqlUniverse, UniverseSecurity, sync_universe_projection

# Synthetic market data — the fallback (and the candidate-universe source for hedging,
# which still runs on a synthetic shortable universe).
MARKET_DATA = SyntheticMarketData()
UNIVERSE_TICKERS = default_universe_tickers(60)

# Default price/factor backfill window: ~7 years. The beta pipeline needs 252 trailing days,
# so this leaves ~6 years of dates with a full lookback — the history a walk-forward hedge
# backtest replays over (deeper history = more out-of-sample data to calibrate against). A
# specific start/end (or `years`) in the ingest request still overrides this.
DEFAULT_BACKFILL_DAYS = 365 * 7 + 30


def _shortable_universe(md):
    """The hedge candidate universe matching the market-data source: the REAL backfilled
    names (with pipeline betas + stored sectors) when on ``DbMarketData``, else the synthetic
    fallback universe. Both are built from the same source so betas are mutually consistent."""
    if isinstance(md, DbMarketData):
        return analytics.db_universe(
            md, min_adv_usd=settings.min_adv_usd, adv_window=settings.adv_window
        )
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


# Read-only roll-up views (virtual portfolios). You cannot trade against a roll-up — pick a
# real book. Consolidated = every book; the two group roll-ups slice by mandate so the desk can
# see the hedged (Long/Short) book and the Long-Only book separately.
CONSOLIDATED_ID = "__consolidated__"
LONGSHORT_GROUP_ID = "__long_short__"
LONGONLY_GROUP_ID = "__long_only__"
ROLLUP_IDS = {CONSOLIDATED_ID, LONGSHORT_GROUP_ID, LONGONLY_GROUP_ID}


def _require_portfolio(service: PositionService, portfolio_id: str):
    """A real, mutable book. Rejects the roll-up sentinels — you trade into a real book."""
    if portfolio_id in ROLLUP_IDS:
        raise HTTPException(
            status_code=409,
            detail="Roll-up views are read-only; select a specific portfolio to trade.",
        )
    portfolio = service.get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    return portfolio


def _rollup_portfolio(service: PositionService, portfolio_id: str) -> Portfolio | None:
    """The virtual portfolio for a roll-up sentinel, or None if ``portfolio_id`` isn't one.

    Consolidated concatenates every book. The group roll-ups filter by mandate: Long/Short =
    the hedged books (these must hold to net-beta neutrality); Long Only = the long-only books
    (intentionally long-beta — carved out of the consolidated balance check). The roll-up
    carries the mandate so the risk view knows whether neutrality even applies."""
    if portfolio_id not in ROLLUP_IDS:
        return None
    books = service.list_portfolios()
    if portfolio_id == LONGSHORT_GROUP_ID:
        books = [b for b in books if b.mandate is Mandate.LONG_SHORT]
        name, mandate = "Long / Short", Mandate.LONG_SHORT
    elif portfolio_id == LONGONLY_GROUP_ID:
        books = [b for b in books if b.mandate is Mandate.LONG_ONLY]
        name, mandate = "Long Only", Mandate.LONG_ONLY
    else:
        name, mandate = "Consolidated", Mandate.LONG_SHORT
    positions = [p for b in books for p in b.positions]
    return Portfolio(id=portfolio_id, name=name, positions=positions, mandate=mandate)


def _resolve_portfolio(service: PositionService, portfolio_id: str):
    """A book for read/analysis: a roll-up sentinel yields its virtual portfolio, else a real book."""
    rollup = _rollup_portfolio(service, portfolio_id)
    return rollup if rollup is not None else _require_portfolio(service, portfolio_id)


# --- endpoints -------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "beta_tol": settings.beta_tol}


@app.post("/securities/betas/rebuild")
def rebuild_betas(session: Session = Depends(get_session)):
    """Recompute the MATERIALIZED daily beta series for the whole universe in one sweep and store
    it. Idempotent; normally runs automatically after ingest, exposed here for a manual rebuild
    (e.g. after changing the prior or benchmark). Returns rows written."""
    with securities_session(session) as sec:
        try:
            written = beta_store.rebuild_betas(sec, benchmark=settings.benchmark)
        except TickerNotFound as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"betas_written": written, "benchmark": settings.benchmark}


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


class BetaDiagIn(BaseModel):
    """Tickers to introspect (e.g. one whose beta looks off)."""

    tickers: list[str]


@app.post("/securities/beta-diagnostics")
def beta_diagnostics(body: BetaDiagIn, session: Session = Depends(get_session)):
    """Why is a name's beta what it is? For each ticker report the REGRESSION INPUTS — stored
    bar count, date span vs the benchmark, observations actually used, forward-filled gap days
    (zero-return days inside the window), and raw vs shrunk beta — so a surprising beta can be
    traced to data (short/gappy history, span shorter than SPY) vs a genuine low/high fit.
    Read-only; no solver, no writes."""
    from neptune.quant.beta import raw_beta as _raw, vasicek_shrinkage as _shrink
    from neptune.risk.analytics import DEFAULT_PRIOR_VAR, MIN_BETA_OBS

    tickers = [t.strip().upper() for t in body.tickers if t.strip()]
    with securities_session(session) as sec:
        try:
            md = DbMarketData(sec, benchmark=settings.benchmark)
        except TickerNotFound as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        market = md.market_returns()
        bench_series = md._series(settings.benchmark)
        bench = {
            "ticker": settings.benchmark,
            "bars": len(bench_series),
            "first_bar": bench_series[0][0].isoformat(),
            "last_bar": bench_series[-1][0].isoformat(),
            "obs_used": int(min(market.shape[0], 252)),
        }

        raws: dict[str, object] = {}
        for t in tickers:
            try:
                raws[t] = _raw(md.ticker_returns(t), market)
            except (ValueError, TickerNotFound):
                raws[t] = None
        # The SAME fixed market-level prior the book and universe shrink against (stable, so the
        # diagnostic's shrunk β equals what the Portfolio tab shows for the name).
        prior_var = DEFAULT_PRIOR_VAR

        rows = []
        for t in tickers:
            r = raws[t]
            try:
                series = md._series(t)
                rets = md.ticker_returns(t)
            except TickerNotFound:
                rows.append({"ticker": t, "status": "no_prices",
                             "note": "no stored prices for this ticker"})
                continue
            window = rets[-252:] if rets.shape[0] > 252 else rets
            gap_days = int((np.abs(window) < 1e-12).sum())  # forward-filled / flat days
            row = {
                "ticker": t,
                "bars": len(series),
                "first_bar": series[0][0].isoformat() if series else None,
                "last_bar": series[-1][0].isoformat() if series else None,
                "obs_used": int(r.n_obs) if r is not None else 0,
                "gap_days": gap_days,  # zero-return days in the window (gaps muted toward 0 beta)
                "starts_after_benchmark": bool(series and series[0][0] > bench_series[0][0]),
            }
            if r is None:
                row["status"] = "insufficient_data"
                row["note"] = "too few observations to regress (<3)"
            elif r.n_obs < MIN_BETA_OBS:
                row["status"] = "insufficient_data"
                row["beta_raw"] = round(r.beta_raw, 4)
                row["note"] = f"only {r.n_obs} obs (<{MIN_BETA_OBS}); beta held at 1.0 prior"
            else:
                shrunk, w = _shrink(r.beta_raw, r.var_ols, prior_var)
                row["status"] = "ok"
                row["beta_raw"] = round(r.beta_raw, 4)
                row["beta"] = round(shrunk, 4)
                row["var_ols"] = float(r.var_ols)
                row["vasicek_weight"] = round(w, 4)
            rows.append(row)

        return {"benchmark": bench, "min_obs": MIN_BETA_OBS, "names": rows}


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
    mandate: str = "LONG_SHORT"  # or "LONG_ONLY" (no shorting)
    firm_id: str | None = None
    investor_entity_id: str | None = None
    lead_pm_ids: list[str] = Field(default_factory=list)


@app.get("/portfolios")
def list_portfolios(session: Session = Depends(get_session)):
    """All books, for the portfolio switcher and the Total Book rollup. ``mandate`` drives the
    grouped switcher (Long/Short vs Long Only) and the consolidated beta-balance carve-out."""
    svc = PositionService(session)
    return [
        {"id": p.id, "name": p.name, "mandate": p.mandate.value, "firm_id": p.firm_id,
         "investor_entity_id": p.investor_entity_id, "lead_pm_ids": p.lead_pm_ids}
        for p in svc.list_portfolios()
    ]


@app.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioIn, session: Session = Depends(get_session)):
    svc = PositionService(session)
    if svc.get_portfolio(body.id) is not None:
        raise HTTPException(status_code=409, detail=f"portfolio {body.id} already exists")
    mandate = Mandate(body.mandate) if body.mandate else Mandate.LONG_SHORT
    kwargs = {k: v for k, v in (
        ("firm_id", body.firm_id), ("investor_entity_id", body.investor_entity_id),
        ("lead_pm_ids", body.lead_pm_ids),
    ) if v}
    p = svc.create_portfolio(body.id, body.name, mandate=mandate.value, **kwargs)
    return {"id": p.id, "name": p.name, "mandate": p.mandate.value}


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
    from sqlalchemy import func

    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    # The beta-balance check is "adjusted for long-only": a long-only book is intentionally
    # long-beta, so the Consolidated roll-up measures neutrality over the HEDGED (long/short)
    # books only — the long-only longs show in the totals but never trip a breach. A long-only
    # *view itself* (the Long Only roll-up or a real long-only book) is reported as informational:
    # neutrality is not required, so its net long beta is expected, not a breach.
    long_only_view = portfolio.mandate is Mandate.LONG_ONLY
    if portfolio_id == CONSOLIDATED_ID:
        beta_portfolio = Portfolio(
            id=portfolio_id, name="Consolidated",
            positions=[p for b in service.list_portfolios()
                       if b.mandate is Mandate.LONG_SHORT for p in b.positions],
        )
    else:
        beta_portfolio = portfolio

    # Firm-level roll-ups (Consolidated / Long-Short) are held to the tighter firm target
    # (CLAUDE.md §3); a single book uses the per-book |β| ≤ 0.05.
    is_firm_view = portfolio_id in (CONSOLIDATED_ID, LONGSHORT_GROUP_ID)
    tol = settings.firm_beta_tol if is_firm_view else settings.beta_tol

    panel = {"loaded": False, "last_date": None, "stale": True}
    with market_data_for(session, beta_portfolio) as md:
        metrics = analytics.compute_metrics(beta_portfolio, md)
        # Stale-panel guard: if the style-factor panel isn't loaded (or lags the prices), the
        # hedge is effectively BETA-ONLY — surface it rather than silently matching nothing.
        factors_present = md.factor_returns()
        panel["loaded"] = all(f in factors_present for f in STYLE_FACTORS)
        sess = getattr(md, "session", None)
        if not panel["loaded"]:
            panel["stale"] = True
        elif sess is None:
            panel["stale"] = False  # synthetic/fallback: factors present, can't be stale
        else:
            last = sess.scalar(select(func.max(FactorReturn.ts)))
            rdates = md.return_dates() if hasattr(md, "return_dates") else []
            panel["last_date"] = last.isoformat() if last else None
            panel["stale"] = bool(last is None or (rdates and (rdates[-1] - last).days > 7))
    net_beta, net_factors = analytics.net_metrics(beta_portfolio, metrics)
    # Market is shown by the net-beta gauge; the factor table covers the style factors.
    style_factors = {f: net_factors[f] for f in STYLE_FACTORS}
    summary = summarize(
        net_beta=net_beta,
        factor_exposures=style_factors,
        long_aum=beta_portfolio.long_aum,
        beta_tol=tol,
        factor_limit=settings.factor_limit,
    )
    headline = (
        "Long-only mandate — net long beta is expected (no neutrality constraint)."
        if long_only_view else summary.headline()
    )
    if not long_only_view and panel["stale"]:
        headline = "⚠ Factor panel not loaded/stale — hedge is BETA-ONLY (style not matched). " + headline
    return {
        "portfolio_id": portfolio_id,
        "mandate": portfolio.mandate.value,
        # Long-only views are NOT held to neutrality — the UI shows their beta as informational.
        "beta_neutral_required": not long_only_view,
        "firm_view": is_firm_view,
        "factor_panel": panel,
        "net_beta": summary.net_beta,
        "beta_tol": summary.beta_tol,
        "beta_status": "INFO" if long_only_view else summary.beta_status,
        "beta_neutral": summary.beta_neutral,
        "long_aum": summary.long_aum,
        "headline": headline,
        "factors": [
            {"factor": f.factor, "exposure": f.exposure, "limit": f.limit, "status": f.status}
            for f in summary.factors
        ],
    }


@app.get("/portfolios/{portfolio_id}/beta-history")
def portfolio_beta_history(
    portfolio_id: str,
    points: int = Query(default=26, ge=2, le=104, description="Number of sample dates."),
    step: int = Query(default=5, ge=1, le=63, description="Trading days between samples (5=weekly)."),
    session: Session = Depends(get_session),
):
    """Beta consistency over time: the book's NET beta walk-forward (current weights held fixed),
    plus each holding's beta history and drift stats. Reads the MATERIALIZED daily betas from the
    DB (rebuilt per ingest); only falls back to an on-the-fly compute if a name isn't materialized
    yet. Read-only."""
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    holdings = [p for p in portfolio.positions if p.notional]
    with securities_session(session) as sec:
        try:
            md = DbMarketData(sec, benchmark=settings.benchmark)
        except TickerNotFound as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        # Net series: prefer stored betas; compute on the fly only if nothing is materialized.
        net = beta_store.portfolio_net_history(sec, portfolio, settings.benchmark, points, step)
        if net is None:
            net = analytics.portfolio_beta_history(md, portfolio, points=points, step=step)
        positions = []
        for p in holdings:
            series = beta_store.ticker_history_sampled(
                sec, p.ticker, settings.benchmark, points, step
            )
            if series is None:  # not materialized yet — fall back to computing it
                try:
                    series = analytics.ticker_beta_history(md, p.ticker, points=points, step=step)
                except TickerNotFound:
                    series = []
            positions.append({
                "ticker": p.ticker, "side": p.side.value, "short_type": p.short_type.value,
                "notional": round(p.notional, 2),
                "stats": analytics.consistency_stats([pt["beta"] for pt in series]),
                "series": series,
            })
    return {
        "portfolio_id": portfolio_id, "lookback": 252, "points": points, "step": step,
        "net": net,
        "net_stats": analytics.consistency_stats([pt["net_beta"] for pt in net]),
        "positions": positions,
    }


@app.get("/portfolios/{portfolio_id}/hedge-backtest")
def hedge_backtest(
    portfolio_id: str,
    points: int = Query(default=24, ge=2, le=120, description="Number of rebalances."),
    step: int = Query(default=21, ge=5, le=63, description="Trading days between rebalances (21=monthly)."),
    session: Session = Depends(get_session),
):
    """Walk-forward HEDGE backtest: re-optimize the hedge at each historical rebalance, hold it
    forward, and measure the REALIZED net beta — the control metric the constraint levels are
    calibrated against. Returns the per-rebalance series + summary (RMSE, coverage, turnover)."""
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    if portfolio.mandate is Mandate.LONG_ONLY:
        raise HTTPException(status_code=422, detail="Long-only books are not hedged.")
    with securities_session(session) as sec:
        try:
            md = DbMarketData(sec, benchmark=settings.benchmark)
        except TickerNotFound as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        # The backtest reads stored daily betas; materialize once if the table is empty.
        if beta_store.latest_stored_date(sec, settings.benchmark) is None:
            beta_store.rebuild_betas(sec, benchmark=settings.benchmark)
        if not beta_store.loadings_materialized(sec):
            beta_store.rebuild_loadings(sec, benchmark=settings.benchmark)  # no-op without a panel
        res = backtest_engine.hedge_backtest(
            md, portfolio, md.available_tickers(),
            rebalance_step=step, points=points,
            beta_tol=settings.beta_tol, factor_limit=settings.factor_limit,
            max_position_weight=settings.max_position_weight, sector_limit=settings.sector_limit,
            beta_add_budget=settings.beta_add_budget, n_cap=settings.target_hedge_names,
        )
    return {
        "portfolio_id": portfolio_id, "tol": res.tol, "rebalance_step": step,
        "rmse": res.rmse, "coverage": res.coverage,
        "mean_abs_realized": res.mean_abs_realized, "mean_turnover": res.mean_turnover,
        "n_rebalances": res.n_rebalances,
        "points": [
            {"date": p.date, "target_net_beta": p.target_net_beta,
             "realized_net_beta": p.realized_net_beta, "n_names": p.n_names, "turnover": p.turnover}
            for p in res.points
        ],
    }


@app.get("/portfolios/{portfolio_id}/hedge-backtest/calibrate")
def calibrate_hedge(
    portfolio_id: str,
    points: int = Query(default=18, ge=2, le=60),
    step: int = Query(default=21, ge=5, le=63),
    session: Session = Depends(get_session),
):
    """Sweep ``beta_add_budget`` across the backtest and report realized control vs. cost for
    each level (RMSE, coverage, turnover) — the curve to pick the budget from. Heavier than the
    single backtest (one replay per budget); keep ``points`` modest."""
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    if portfolio.mandate is Mandate.LONG_ONLY:
        raise HTTPException(status_code=422, detail="Long-only books are not hedged.")
    with securities_session(session) as sec:
        try:
            md = DbMarketData(sec, benchmark=settings.benchmark)
        except TickerNotFound as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        # Each budget replays the backtest; stored betas make that cheap. Materialize if empty.
        if beta_store.latest_stored_date(sec, settings.benchmark) is None:
            beta_store.rebuild_betas(sec, benchmark=settings.benchmark)
        if not beta_store.loadings_materialized(sec):
            beta_store.rebuild_loadings(sec, benchmark=settings.benchmark)  # no-op without a panel
        rows = backtest_engine.calibrate_beta_add_budget(
            md, portfolio, md.available_tickers(),
            rebalance_step=step, points=points,
            beta_tol=settings.beta_tol, factor_limit=settings.factor_limit,
            max_position_weight=settings.max_position_weight, sector_limit=settings.sector_limit,
            n_cap=settings.target_hedge_names,
        )
    return {"portfolio_id": portfolio_id, "tol": settings.beta_tol,
            "current_budget": settings.beta_add_budget, "grid": rows}


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
    beta_add_budget: float | None = Query(
        default=None, ge=0.0, le=1.0,
        description="Max net market beta the shorts may ADD (negative-beta-short fence). "
                    "Default = beta_add_fraction × beta_tol.",
    ),
    session: Session = Depends(get_session),
):
    service = PositionService(session)
    portfolio = _resolve_portfolio(service, portfolio_id)
    if beta_add_budget is None:
        beta_add_budget = settings.beta_add_budget
    if portfolio.mandate is Mandate.LONG_ONLY:
        raise HTTPException(
            status_code=422,
            detail="Long-only books are not hedged (shorting is disallowed by mandate).",
        )
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
                beta_add_budget=beta_add_budget,  # fence negative-beta shorts (Option A; per-run)
            )
            # Aim for a DIVERSIFIED basket of ~target names (the variance penalty spreads weight
            # across many moderate names; the cap sets the count). Defaults to
            # settings.target_hedge_names; an explicit max_names overrides it. Capped runs are
            # soft, so net_beta_after reports whether neutrality was achieved.
            target = max_names if max_names is not None else settings.target_hedge_names
            proposal = optimize_hedge_capped(n_cap=target, **common)
            # Capture the mark for each proposed short so the UI can show shares-to-short.
            prices = {s.ticker: _safe_price(md, s.ticker) for s in proposal.positions}
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
            {"ticker": s.ticker,
             "shares": (round(s.notional / prices[s.ticker]) if prices.get(s.ticker) else None),
             "price": prices.get(s.ticker),
             "notional": round(s.notional, 2), "beta": s.beta, "sector": s.sector}
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


class HedgeShortIn(BaseModel):
    ticker: str
    shares: float = Field(gt=0)
    price: float = Field(gt=0)


class HedgeApproveIn(BaseModel):
    """Approve a whole proposed hedge basket. Each name is booked as a SYSTEMATIC short — kept
    distinct from discretionary shorts (I-03), and recorded, never routed to a broker (I-01)."""

    shorts: list[HedgeShortIn]


@app.post("/portfolios/{portfolio_id}/hedge/approve", status_code=201)
def approve_hedge(portfolio_id: str, body: HedgeApproveIn,
                  session: Session = Depends(get_session)):
    service = PositionService(session)
    book = _require_portfolio(service, portfolio_id)  # real book only (rejects roll-ups)
    if book.mandate is Mandate.LONG_ONLY:
        raise HTTPException(
            status_code=422,
            detail="Long-only books cannot book a systematic hedge (shorting disallowed).",
        )
    # A hedge proposal is a FULL replacement for the systematic book (residual_metrics excludes
    # systematic shorts, so the optimizer re-sizes the whole hedge against the long book). Clear
    # the existing systematic shorts first, so approving REPLACES the hedge rather than stacking a
    # second one on top — otherwise repeated propose→approve cycles double the short exposure.
    replaced = service.clear_systematic_shorts(portfolio_id)
    # Dedupe by ticker (last wins) so an accidental duplicate row can't aggregate into 2× a name.
    by_ticker = {s.ticker: s for s in body.shorts}
    booked = 0
    for s in by_ticker.values():
        service.record_trade(
            portfolio_id, s.ticker, Side.SHORT, ShortType.SYSTEMATIC,
            s.shares, s.price, date.today(),
        )
        booked += 1
    return {"booked": booked, "replaced": replaced, "portfolio_id": portfolio_id}


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
            beta_add_budget=settings.beta_add_budget,
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
    """A price-ingestion request. Tickers default to the whole projected universe; the window
    defaults to the ~7-year backfill (enough for a deep walk-forward backtest). ``years`` is a
    convenience to request a specific depth; an explicit ``start`` still wins."""

    tickers: list[str] | None = None
    start: date | None = None
    end: date | None = None
    years: float | None = Field(default=None, gt=0, le=25)


@app.post("/securities/ingest")
def ingest_prices(body: IngestIn, session: Session = Depends(get_session)):
    """Backfill OHLCV/dividends/splits from yfinance into the securities DB for the given
    tickers (or the whole projection). Requires network + the optional yfinance package, so
    it returns 503 when the feed is unavailable rather than failing opaquely."""
    end = body.end or date.today()
    days = round(body.years * 365) + 30 if body.years else DEFAULT_BACKFILL_DAYS
    start = body.start or (end - timedelta(days=days))
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
        # Materialize the daily beta series for what we just ingested (best-effort): betas live
        # in the DB and are recomputed in one sweep here, never on the fly per request.
        betas_written = 0
        try:
            ingested = [r["ticker"] for r in results if r["ticker"] != settings.benchmark]
            if ingested:
                betas_written = beta_store.rebuild_betas(
                    sec_session, benchmark=settings.benchmark, tickers=ingested
                )
                # Style loadings too (no-op until the factor panel is ingested).
                beta_store.rebuild_loadings(
                    sec_session, benchmark=settings.benchmark, tickers=ingested
                )
        except Exception:  # noqa: BLE001 — never fail an ingest because a sweep hiccuped
            betas_written = 0
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "ingested": results,
        "errors": errors,
        "betas_written": betas_written,
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
        # The panel just changed → (re)materialize style loadings for the whole universe.
        loadings_written = 0
        try:
            loadings_written = beta_store.rebuild_loadings(sec_session, benchmark=settings.benchmark)
        except Exception:  # noqa: BLE001 — never fail the panel ingest on a loadings hiccup
            loadings_written = 0
    return {"start": start.isoformat(), "end": end.isoformat(), "counts": counts,
            "loadings_written": loadings_written}
