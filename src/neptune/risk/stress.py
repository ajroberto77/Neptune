"""Risk Interface: drive the Stress Engine from a live portfolio.

Builds the per-position exposures (signed notional, beta, factor loadings) and the
risk-factor covariance from market data, then runs scenarios and parametric VaR/ES.
Pure-math lives in ``neptune.stress``; this module is the wiring.
"""
from __future__ import annotations

import numpy as np

from neptune.data.source import MarketData
from neptune.domain.models import Portfolio
from neptune.quant.beta import semibetas
from neptune.risk import analytics
from neptune.stress import (
    RISK_FACTORS,
    Scenario,
    ScenarioResult,
    StressExposure,
    VaRResult,
    apply_scenario,
    historical_var,
    monte_carlo_var,
    parametric_var,
)


def build_exposures(
    portfolio: Portfolio, market: MarketData
) -> list[StressExposure]:
    """Per-position stress exposures, using the live beta pipeline + factor loadings. For
    pipeline-beta names we also attach downside/upside betas (stress-only) so selloff and
    rally scenarios differ; forward-override and insufficient-data names stay symmetric."""
    metrics = analytics.compute_metrics(portfolio, market)
    mkt = market.market_returns()
    exposures: list[StressExposure] = []
    for p in portfolio.positions:
        m = metrics[p.ticker]
        down = up = None
        if m.beta_method == "pipeline":
            sb = semibetas(market.ticker_returns(p.ticker), mkt, fallback=m.beta)
            down, up = sb.down_beta, sb.up_beta
        exposures.append(
            StressExposure(
                ticker=p.ticker,
                book=p.book.value,
                signed_notional=p.signed_notional,
                beta=m.beta,
                loadings=m.loadings,
                down_beta=down,
                up_beta=up,
            )
        )
    return exposures


def risk_factor_matrix(
    market: MarketData, factors: tuple[str, ...] = RISK_FACTORS
) -> np.ndarray:
    """(T, F) matrix of the risk factors' periodic returns — rows are days, columns are
    factors in ``RISK_FACTORS`` order. The input to historical simulation."""
    series = market.factor_returns()
    return np.column_stack([series[f] for f in factors])


def risk_factor_covariance(
    market: MarketData, factors: tuple[str, ...] = RISK_FACTORS
) -> np.ndarray:
    """Sample covariance of the risk factors' return series (MKT + style factors)."""
    return np.cov(risk_factor_matrix(market, factors), rowvar=False)


def run_scenarios(
    portfolio: Portfolio, market: MarketData, scenarios: list[Scenario]
) -> list[ScenarioResult]:
    exposures = build_exposures(portfolio, market)
    return [apply_scenario(exposures, s) for s in scenarios]


def value_at_risk(
    portfolio: Portfolio,
    market: MarketData,
    confidence: float = 0.95,
    horizon_days: int = 1,
    method: str = "parametric",
    n_draws: int = 50_000,
    seed: int = 12345,
) -> VaRResult:
    """VaR/ES by ``method``: 'parametric' (variance-covariance), 'historical'
    (replay each historical factor-return day), or 'monte_carlo' (draws from the factor
    covariance)."""
    exposures = build_exposures(portfolio, market)
    if method == "historical":
        return historical_var(
            exposures, risk_factor_matrix(market), confidence, horizon_days
        )
    if method == "monte_carlo":
        return monte_carlo_var(
            exposures, risk_factor_covariance(market), confidence, horizon_days,
            n_draws=n_draws, seed=seed,
        )
    if method == "parametric":
        return parametric_var(exposures, risk_factor_covariance(market), confidence,
                              horizon_days)
    raise ValueError(f"unknown VaR method: {method!r}")
