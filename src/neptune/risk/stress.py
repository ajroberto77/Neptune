"""Risk Interface: drive the Stress Engine from a live portfolio.

Builds the per-position exposures (signed notional, beta, factor loadings) and the
risk-factor covariance from market data, then runs scenarios and parametric VaR/ES.
Pure-math lives in ``neptune.stress``; this module is the wiring.
"""
from __future__ import annotations

import numpy as np

from neptune.data.market import SyntheticMarketData
from neptune.domain.models import Portfolio
from neptune.risk import analytics
from neptune.stress import (
    RISK_FACTORS,
    Scenario,
    ScenarioResult,
    StressExposure,
    VaRResult,
    apply_scenario,
    parametric_var,
)


def build_exposures(
    portfolio: Portfolio, market: SyntheticMarketData
) -> list[StressExposure]:
    """Per-position stress exposures, using the live beta pipeline + factor loadings."""
    metrics = analytics.compute_metrics(portfolio, market)
    return [
        StressExposure(
            ticker=p.ticker,
            book=p.book.value,
            signed_notional=p.signed_notional,
            beta=metrics[p.ticker].beta,
            loadings=metrics[p.ticker].loadings,
        )
        for p in portfolio.positions
    ]


def risk_factor_covariance(
    market: SyntheticMarketData, factors: tuple[str, ...] = RISK_FACTORS
) -> np.ndarray:
    """Sample covariance of the risk factors' return series (MKT + style factors)."""
    series = market.factor_returns()
    matrix = np.vstack([series[f] for f in factors])  # rows = factors
    return np.cov(matrix)


def run_scenarios(
    portfolio: Portfolio, market: SyntheticMarketData, scenarios: list[Scenario]
) -> list[ScenarioResult]:
    exposures = build_exposures(portfolio, market)
    return [apply_scenario(exposures, s) for s in scenarios]


def value_at_risk(
    portfolio: Portfolio,
    market: SyntheticMarketData,
    confidence: float = 0.95,
    horizon_days: int = 1,
) -> VaRResult:
    exposures = build_exposures(portfolio, market)
    cov = risk_factor_covariance(market)
    return parametric_var(exposures, cov, confidence, horizon_days)
