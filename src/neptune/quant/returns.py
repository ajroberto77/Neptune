"""Price -> return transformations and series alignment helpers."""
from __future__ import annotations

import numpy as np


def simple_returns(prices: np.ndarray) -> np.ndarray:
    """Simple period returns r_t = p_t / p_{t-1} - 1 for a 1-D price series."""
    prices = np.asarray(prices, dtype=float)
    if prices.ndim != 1 or prices.size < 2:
        raise ValueError("prices must be a 1-D series with at least 2 observations")
    return prices[1:] / prices[:-1] - 1.0


def excess_returns(returns: np.ndarray, risk_free: float | np.ndarray = 0.0) -> np.ndarray:
    """Subtract the (per-period) risk-free rate. Defaults to 0 for the slice."""
    return np.asarray(returns, dtype=float) - risk_free


def align(*series: np.ndarray) -> tuple[np.ndarray, ...]:
    """Truncate each series to the common (shortest) length, keeping the most recent
    observations. Used to line up a stock with the market before regression."""
    arrays = [np.asarray(s, dtype=float) for s in series]
    n = min(a.shape[0] for a in arrays)
    return tuple(a[-n:] for a in arrays)
