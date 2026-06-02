"""Factor decomposition.

Per-security factor loadings via OLS regression of asset excess returns on the factor
return series, then notional-weighted aggregation to a portfolio exposure.

The model is **Fama-French 5 + Momentum**: Market (MKT) plus the style factors Size (SMB),
Value (HML), Profitability (RMW), Investment (CMA), and Momentum (MOM). MKT comes from the
SPY benchmark; the style factors come from the Ken French panel. ``STYLE_FACTORS`` is the
single source of truth — the optimizer, stress engine, db market data, and risk summary all
import it, so adding a factor here flows everywhere. (The Sector factor in the roadmap is a
concentration constraint, handled separately.)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neptune.quant.returns import align

MARKET_FACTOR = "MKT"
# The style factors carried alongside the market (Fama-French 5 + Momentum).
STYLE_FACTORS = ("SMB", "HML", "RMW", "CMA", "MOM")
FACTORS = (MARKET_FACTOR, *STYLE_FACTORS)


@dataclass
class FactorLoadings:
    """Per-security factor loadings (betas), keyed by factor name."""

    loadings: dict[str, float]
    n_obs: int


@dataclass(frozen=True)
class RollingFactorLoadings:
    """Trailing-window multivariate factor loadings at EVERY index (the materialized series)."""

    loadings: dict[str, np.ndarray]  # factor -> per-index loading (NaN where the window is short)
    n_obs: np.ndarray                # window length used at each index


def rolling_factor_loadings(
    asset_returns: np.ndarray,
    factor_returns: dict[str, np.ndarray],
    window: int = 60,
) -> RollingFactorLoadings:
    """Vectorized version of ``factor_loadings`` at every index in one pass — the engine behind
    materializing the daily loading series. For index ``i`` it fits the SAME multivariate OLS
    (asset on [1, factors]) over the trailing ``min(i+1, window)`` observations. Returns a per-index
    loading array per factor; indices with fewer observations than parameters are NaN.

    Built from cumulative sums of the design's pairwise products: the factor Gram matrix is shared
    across all names (only the asset cross-term differs), and the per-date systems are solved batched
    — so re-fitting at every date costs about one solve per date, not a regression per date."""
    names = list(factor_returns.keys())
    arrs = align(np.asarray(asset_returns, float),
                 *[np.asarray(factor_returns[nm], float) for nm in names])
    y = arrs[0]
    n = y.shape[0]
    if n == 0:
        return RollingFactorLoadings({nm: np.array([]) for nm in names}, np.array([], dtype=int))
    X = np.column_stack([np.ones(n), *arrs[1:]])  # n x p   (p = 1 intercept + k factors)
    p = X.shape[1]
    idx = np.arange(n)
    L = np.minimum(idx + 1, window)
    start = idx + 1 - L
    end = idx + 1

    XX = X[:, :, None] * X[:, None, :]   # n x p x p  (per-obs outer products)
    Xy = X * y[:, None]                  # n x p
    csXX = np.zeros((n + 1, p, p)); csXX[1:] = np.cumsum(XX, axis=0)
    csXy = np.zeros((n + 1, p));    csXy[1:] = np.cumsum(Xy, axis=0)
    G = csXX[end] - csXX[start]          # n x p x p  (window Gram X'X)
    b = csXy[end] - csXy[start]          # n x p      (window X'y)
    G = G + 1e-10 * np.eye(p)[None, :, :]  # tiny ridge → always invertible (window << degenerate)
    sol = np.linalg.solve(G, b[:, :, None])[:, :, 0]  # n x p  (intercept + loadings)

    invalid = L < max(p, 3)  # need at least as many obs as parameters to identify loadings
    out = {nm: np.where(invalid, np.nan, sol[:, j + 1]) for j, nm in enumerate(names)}
    return RollingFactorLoadings(loadings=out, n_obs=L.astype(int))


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
