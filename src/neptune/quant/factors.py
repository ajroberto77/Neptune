"""Factor decomposition.

Per-security factor loadings via OLS regression of asset excess returns on the factor
return series, then notional-weighted aggregation to a portfolio exposure. The slice
uses the four statistical factors Market (MKT), Size (SMB), Value (HML), Momentum
(MOM); the Sector factor in the roadmap is a concentration constraint, handled
separately (deferred).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neptune.quant.returns import align

FACTORS = ("MKT", "SMB", "HML", "MOM")


@dataclass
class FactorLoadings:
    """Per-security factor loadings (betas), keyed by factor name."""

    loadings: dict[str, float]
    n_obs: int


def factor_loadings(
    asset_returns: np.ndarray,
    factor_returns: dict[str, np.ndarray],
    window: int = 60,
) -> FactorLoadings:
    """OLS regression of asset returns on factor returns over the most recent
    ``window`` observations. Returns one loading per factor."""
    names = list(factor_returns.keys())
    series = align(np.asarray(asset_returns, float), *[factor_returns[n] for n in names])
    y, factor_series = series[0], series[1:]

    if y.shape[0] > window:
        y = y[-window:]
        factor_series = tuple(f[-window:] for f in factor_series)

    n = y.shape[0]
    X = np.column_stack([np.ones(n), *factor_series])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    # coef[0] is the intercept (alpha); the rest are factor loadings in `names` order.
    return FactorLoadings(loadings=dict(zip(names, coef[1:].tolist())), n_obs=n)


def portfolio_factor_exposure(
    positions: list[tuple[float, dict[str, float]]],
    long_aum: float,
) -> dict[str, float]:
    """Notional-weighted portfolio exposure per factor, normalized by long AUM.

    ``positions`` is a list of (signed_notional, loadings) tuples. Shorts carry a
    negative signed notional, so a short hedge reduces the net exposure.
    """
    if long_aum <= 0:
        raise ValueError("long_aum must be positive")
    exposure = {f: 0.0 for f in FACTORS}
    for signed_notional, loadings in positions:
        for factor, load in loadings.items():
            if factor in exposure:  # ignore keys outside the known factor set
                exposure[factor] += signed_notional * load
    return {f: v / long_aum for f, v in exposure.items()}
