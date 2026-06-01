"""Live book analytics: run the beta pipeline and factor regression over a portfolio.

This is the Risk Interface wiring that connects domain positions to the Quant Engine
using a market-data source. Per position:
  * the market beta is the PM ``forward_beta`` when set, else the full pipeline
    (EWMA+Dimson raw beta -> Vasicek shrinkage), with the Vasicek prior variance taken
    from the cross-section of raw betas in the book;
  * factor loadings always come from the regression (a forward beta overrides only the
    market beta, never the factor loadings).
"""
from __future__ import annotations

from dataclasses import dataclass

from neptune.data.market import SyntheticMarketData
from neptune.data.source import MarketData
from neptune.domain.models import Portfolio, ShortType
from neptune.quant.beta import (
    cross_sectional_prior_var,
    raw_beta_ewma_dimson,
    vasicek_shrinkage,
)
from neptune.quant.factors import factor_loadings
from neptune.quant.optimizer import Candidate, compute_residual

DEFAULT_PRIOR_VAR = 0.10  # fallback when the book is too small for a cross-section


@dataclass
class PositionMetrics:
    ticker: str
    beta: float
    beta_raw: float
    beta_method: str          # "forward_override" or "pipeline"
    weight: float             # Vasicek weight (1.0 for forward override)
    loadings: dict[str, float]


def compute_metrics(
    portfolio: Portfolio,
    market_data: MarketData,
    default_prior_var: float = DEFAULT_PRIOR_VAR,
) -> dict[str, PositionMetrics]:
    """Two-pass live metrics: raw betas first (to set the Vasicek prior), then shrink."""
    market = market_data.market_returns()
    factors = market_data.factor_returns()

    raws = {p.ticker: raw_beta_ewma_dimson(market_data.ticker_returns(p.ticker), market)
            for p in portfolio.positions}

    raw_betas = [r.beta_raw for r in raws.values()]
    prior_var = (
        cross_sectional_prior_var(raw_betas) if len(raw_betas) >= 2 else default_prior_var
    )

    metrics: dict[str, PositionMetrics] = {}
    for p in portfolio.positions:
        raw = raws[p.ticker]
        if p.forward_beta is not None:
            beta, method, w = float(p.forward_beta), "forward_override", 1.0
        else:
            beta, w = vasicek_shrinkage(raw.beta_raw, raw.var_ols, prior_var)
            method = "pipeline"
        loadings = factor_loadings(market_data.ticker_returns(p.ticker), factors).loadings
        metrics[p.ticker] = PositionMetrics(
            ticker=p.ticker, beta=beta, beta_raw=raw.beta_raw,
            beta_method=method, weight=w, loadings=loadings,
        )
    return metrics


def net_metrics(
    portfolio: Portfolio, metrics: dict[str, PositionMetrics]
) -> tuple[float, dict[str, float]]:
    """Net beta and net factor exposures across the WHOLE book (normalized by long AUM)."""
    inputs = [
        (p.signed_notional, metrics[p.ticker].beta, metrics[p.ticker].loadings)
        for p in portfolio.positions
    ]
    return compute_residual(inputs, portfolio.long_aum)


def residual_metrics(
    portfolio: Portfolio, metrics: dict[str, PositionMetrics]
) -> tuple[float, dict[str, float]]:
    """Residual beta/factors the systematic short book must neutralize: long book +
    discretionary shorts only (systematic shorts are what the optimizer re-proposes)."""
    inputs = [
        (p.signed_notional, metrics[p.ticker].beta, metrics[p.ticker].loadings)
        for p in portfolio.positions
        if p.short_type is not ShortType.SYSTEMATIC
    ]
    return compute_residual(inputs, portfolio.long_aum)


def live_universe(market_data: SyntheticMarketData, tickers: list[str]) -> list[Candidate]:
    """Build shortable-universe candidates with live betas and factor loadings."""
    market = market_data.market_returns()
    factors = market_data.factor_returns()
    raws = {t: raw_beta_ewma_dimson(market_data.ticker_returns(t), market) for t in tickers}
    prior_var = (
        cross_sectional_prior_var([r.beta_raw for r in raws.values()])
        if len(raws) >= 2
        else DEFAULT_PRIOR_VAR
    )
    candidates: list[Candidate] = []
    for t in tickers:
        beta, _ = vasicek_shrinkage(raws[t].beta_raw, raws[t].var_ols, prior_var)
        loadings = factor_loadings(market_data.ticker_returns(t), factors).loadings
        sector = market_data.spec_for(t).sector
        candidates.append(Candidate(ticker=t, beta=beta, loadings=loadings, sector=sector))
    return candidates
