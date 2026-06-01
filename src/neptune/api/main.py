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

from contextlib import asynccontextmanager

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
from neptune.db.base import SessionLocal, init_db, init_securities_db, make_engine
from neptune.db.runtime import securities_session
from neptune.domain.models import BookType, LotEntry, Position, Side, ShortType
from neptune.domain.org import PersonRole
from neptune.pnl import CostBasisMethod, PnL
from neptune.quant.optimizer import InfeasibleHedge, complexity_frontier, optimize_hedge
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
from neptune.securities.models import Security
from neptune.securities.providers import YFinanceProvider
from neptune.universe import RecordedUniverse, SqlUniverse, UniverseSecurity, sync_universe_projection

# One shared synthetic market-data source feeds the live beta/factor pipeline.
MARKET_DATA = SyntheticMarketData()
UNIVERSE_TICKERS = default_universe_tickers(60)


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
    return {"day": p.day, "total": p.total, "unrealised": p.unrealised, "realised": p.realised}


# --- lifespan: create tables + seed the golden portfolio -------------------------

def seed_golden(session: Session) -> None:
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
    for pos in golden_positions():
        service.add_position(pid, pos)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_securities_db()  # create the market-data schema too (idempotent)
    with SessionLocal() as session:
        seed_golden(session)
    yield


app = FastAPI(title="Neptune", version="0.1.0", lifespan=lifespan)


def get_session():
    with SessionLocal() as session:
        yield session


def _require_portfolio(service: PositionService, portfolio_id: str):
    portfolio = service.get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    return portfolio


# --- endpoints -------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "beta_tol": settings.beta_tol}


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
    return {"id": position_id}


@app.get("/portfolios/{portfolio_id}/positions")
def list_positions(portfolio_id: str, session: Session = Depends(get_session)):
    service = PositionService(session)
    portfolio = _require_portfolio(service, portfolio_id)
    metrics = analytics.compute_metrics(portfolio, MARKET_DATA)
    # Compute the book's lead PM once (not per position) — the per-name fallback.
    lead_pm = portfolio.lead_pm_ids[0] if portfolio.lead_pm_ids else None
    return [
        {
            "ticker": p.ticker,
            "side": p.side.value,
            "short_type": p.short_type.value,
            "book": p.book.value,
            "notional": p.notional,
            "beta": round(metrics[p.ticker].beta, 4),
            "beta_method": metrics[p.ticker].beta_method,
            "cost_basis_method": (p.cost_basis_method or CostBasisMethod.FIFO).value,
            # Per-name coverage; pm falls back to the book's lead PM.
            "pm_id": p.pm_id or lead_pm,
            "analyst_id": p.analyst_id,
            "pnl": _pnl_dict(pnl_engine.position_pnl_for(p, MARKET_DATA)),
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
    return {"position_id": position_id, "realised_pnl": realised}


@app.get("/portfolios/{portfolio_id}/pnl")
def portfolio_pnl(portfolio_id: str, session: Session = Depends(get_session)):
    """Four P&L dimensions for the book, split by Long / Systematic / Discretionary."""
    service = PositionService(session)
    portfolio = _require_portfolio(service, portfolio_id)
    result = pnl_engine.portfolio_pnl(portfolio, MARKET_DATA)
    return {
        "portfolio_id": portfolio_id,
        "total": _pnl_dict(result.total),
        "by_book": {book.value: _pnl_dict(p) for book, p in result.by_book.items()},
    }


@app.get("/portfolios/{portfolio_id}/risk")
def risk_summary(portfolio_id: str, session: Session = Depends(get_session)):
    service = PositionService(session)
    portfolio = _require_portfolio(service, portfolio_id)
    metrics = analytics.compute_metrics(portfolio, MARKET_DATA)
    net_beta, net_factors = analytics.net_metrics(portfolio, metrics)
    # Market is shown by the net-beta gauge; the factor table covers the style factors.
    style_factors = {f: net_factors[f] for f in ("SMB", "HML", "MOM")}
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
    session: Session = Depends(get_session),
):
    service = PositionService(session)
    portfolio = _require_portfolio(service, portfolio_id)
    metrics = analytics.compute_metrics(portfolio, MARKET_DATA)
    residual_beta, residual_factors = analytics.residual_metrics(portfolio, metrics)
    long_tickers = {p.ticker for p in portfolio.longs}
    universe = analytics.live_universe(MARKET_DATA, UNIVERSE_TICKERS)
    try:
        proposal = optimize_hedge(
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
    except (InfeasibleHedge, ValueError) as exc:
        # Cannot hedge to neutral with this universe — a domain state, not a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    portfolio = _require_portfolio(service, portfolio_id)
    metrics = analytics.compute_metrics(portfolio, MARKET_DATA)
    residual_beta, residual_factors = analytics.residual_metrics(portfolio, metrics)
    long_tickers = {p.ticker for p in portfolio.longs}
    universe = analytics.live_universe(MARKET_DATA, UNIVERSE_TICKERS)
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
    portfolio = _require_portfolio(service, portfolio_id)

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
    start = body.start or (end - timedelta(days=400))  # ~252 trading days of cushion
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
        # Per-ticker failures are collected, not fatal: ingest is idempotent and
        # source-tagged, so a partial run is safe to resume. A missing-yfinance
        # RuntimeError is global, though — fail fast with 503 rather than N times.
        errors = []
        for ticker in tickers:
            try:
                res = ingest_ticker(sec_session, provider, ticker, start, end)
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
    securities DB. Requires network + the optional pandas-datareader package, so it returns
    503 when the feed is unavailable rather than failing opaquely."""
    end = body.end or date.today()
    start = body.start or (end - timedelta(days=400))
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
