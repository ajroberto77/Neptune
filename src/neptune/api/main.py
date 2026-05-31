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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datetime import date

from neptune.config import settings
from neptune.data.fixtures import GOLDEN_PORTFOLIO, golden_positions
from neptune.data.market import SyntheticMarketData, default_universe_tickers
from neptune.db.base import SessionLocal, init_db
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
