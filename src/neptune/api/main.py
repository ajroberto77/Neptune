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

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from neptune.config import settings
from neptune.data.fixtures import GOLDEN_PORTFOLIO, golden_candidates, golden_positions
from neptune.db.base import SessionLocal, init_db
from neptune.domain.models import Position, Side, ShortType
from neptune.quant.optimizer import InfeasibleHedge, compute_residual, optimize_hedge
from neptune.risk import book
from neptune.risk.summary import summarize
from neptune.positions.service import ConflictError, PositionService


# --- request/response schemas ----------------------------------------------------

class PositionIn(BaseModel):
    ticker: str
    side: Side
    notional: float = Field(gt=0)
    short_type: ShortType = ShortType.NA
    forward_beta: float | None = None
    sector: str | None = None
    thesis: str | None = None
    target: str | None = None


def _to_domain(p: PositionIn) -> Position:
    return Position(
        ticker=p.ticker,
        side=p.side,
        notional=p.notional,
        short_type=p.short_type,
        forward_beta=p.forward_beta,
        sector=p.sector,
        thesis=p.thesis,
        target=p.target,
    )


# --- lifespan: create tables + seed the golden portfolio -------------------------

def seed_golden(session: Session) -> None:
    service = PositionService(session)
    pid = GOLDEN_PORTFOLIO["portfolio_id"]
    if service.get_portfolio(pid) is not None:
        return
    service.create_portfolio(pid, GOLDEN_PORTFOLIO["name"])
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
    _require_portfolio(service, portfolio_id)
    return [
        {
            "ticker": p.ticker,
            "side": p.side.value,
            "notional": p.notional,
            "short_type": p.short_type.value,
            "beta": book.position_beta(p),
        }
        for p in service.list_positions(portfolio_id)
    ]


@app.get("/portfolios/{portfolio_id}/risk")
def risk_summary(portfolio_id: str, session: Session = Depends(get_session)):
    service = PositionService(session)
    portfolio = _require_portfolio(service, portfolio_id)
    nb = book.net_beta(portfolio)
    # Factor exposures are zero in the slice; compute via residual_inputs for structure.
    _, residual_factors = compute_residual(book.residual_inputs(portfolio), portfolio.long_aum)
    summary = summarize(
        net_beta=nb,
        factor_exposures=residual_factors,
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
def propose_hedge(portfolio_id: str, session: Session = Depends(get_session)):
    service = PositionService(session)
    portfolio = _require_portfolio(service, portfolio_id)
    residual_beta, residual_factors = compute_residual(
        book.residual_inputs(portfolio), portfolio.long_aum
    )
    long_tickers = {p.ticker for p in portfolio.longs}
    try:
        proposal = optimize_hedge(
            residual_beta=residual_beta,
            residual_factors=residual_factors,
            universe=golden_candidates(),
            long_aum=portfolio.long_aum,
            beta_tol=settings.beta_tol,
            factor_limit=settings.factor_limit,
            max_position_weight=settings.max_position_weight,
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
            {"ticker": s.ticker, "notional": round(s.notional, 2), "beta": s.beta}
            for s in proposal.positions
        ],
    }
