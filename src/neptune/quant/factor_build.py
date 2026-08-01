"""Construction of Neptune's own (price-only) differentiating factors.

These are the factors we can build from the price/volume panel ALONE — no fundamentals —
so they're available before the Mercury feed lands. Each returns a **daily factor-return
series** (a long/short spread), the same shape as a Ken French panel column, so it flows
into ``factor_returns`` → materialized loadings → the optimizer exactly like FF5+MOM.

Pure functions over arrays (CLAUDE.md §1, Quant Engine): no I/O, no DB, no network. The
Risk-Interface builder in ``risk/factor_build.py`` pulls the panel from the DB, calls these,
and persists the result.

Factors built here:
  * **IVOL**  — low-minus-high idiosyncratic volatility (the low-vol anomaly).
  * **BAB**   — betting-against-beta: low-beta minus high-beta, each leg scaled to ~β=1.
  * **AMIHUD**— illiquid-minus-liquid (Amihud (2002) illiquidity premium).
  * **SECTOR**— per-sector equal-weight EXCESS return over the market (one series per sector).
                Equal-weighted because value-weighting needs market cap = price × shares
                outstanding (a fundamental) — upgraded to VW when the Mercury feed lands.

Look-ahead is avoided everywhere: the basket at date ``t`` is formed from the characteristic
known as of ``t-1`` (the score series is lagged), and returns are realized at ``t``.
"""
from __future__ import annotations

import numpy as np

from neptune.quant.returns import align

# The single-series price-only factors Neptune builds (sector factors are dynamic SECTOR_* names).
# Monitored, not neutralized (PM decision) — kept out of the optimizer's factor set.
NEPTUNE_FACTORS = ("IVOL", "BAB", "AMIHUD")


def _leg_indices(scores_tm1: np.ndarray, frac: float) -> tuple[np.ndarray, np.ndarray]:
    """Among names with a finite score at ``t-1``, the index sets of the bottom and top
    ``frac`` (each at least one name). Returns (low_idx, high_idx); empty arrays if < 2 names."""
    valid = np.nonzero(np.isfinite(scores_tm1))[0]
    if valid.size < 2:
        return np.array([], dtype=int), np.array([], dtype=int)
    order = valid[np.argsort(scores_tm1[valid], kind="stable")]
    # min(..., n//2) keeps the bottom and top legs DISJOINT even at a large frac.
    k = min(max(1, int(np.floor(frac * order.size))), order.size // 2)
    return order[:k], order[-k:]


def long_short_returns(
    returns: np.ndarray,
    scores: np.ndarray,
    *,
    frac: float = 0.3,
    low_minus_high: bool = True,
    betas: np.ndarray | None = None,
    beta_floor: float = 0.25,
) -> np.ndarray:
    """Daily long/short factor return from a per-name characteristic.

    ``returns`` and ``scores`` are ``(N, T)`` (N names, T dates), aligned on the same dates;
    ``scores`` may contain NaN where a name's characteristic is undefined. At each date ``t``
    the bottom/top ``frac`` baskets are picked by ``scores[:, t-1]`` (LAGGED — no look-ahead),
    equal-weighted, and the factor return is the **low-minus-high** spread (set
    ``low_minus_high=False`` to flip).

    When ``betas`` (same shape, lagged like the scores) is given, each leg's return is divided
    by the leg's equal-weight average beta — the betting-against-beta construction that levers
    the low-beta leg up and the high-beta leg down so each carries ~unit market beta. A leg whose
    average beta is not above ``beta_floor`` (a positive floor) is treated as degenerate and the
    whole date is skipped (NaN): dividing by a near-zero or negative leg beta would explode or
    sign-flip the leg — the same instability that retired the EWMA+Dimson pipeline (CLAUDE.md §4).

    Returns a length-``T`` series; an entry is NaN where no basket can be formed (the first
    date, or any date whose lagged cross-section has < 2 finite scores) so the caller persists a
    genuine return, never a fabricated zero."""
    R = np.asarray(returns, float)
    S = np.asarray(scores, float)
    if R.shape != S.shape:
        raise ValueError("returns and scores must have the same (N, T) shape")
    N, T = R.shape
    out = np.full(T, np.nan, dtype=float)
    sign = 1.0 if low_minus_high else -1.0
    for t in range(1, T):
        low, high = _leg_indices(S[:, t - 1], frac)
        if low.size == 0 or high.size == 0:
            continue

        def leg_return(idx: np.ndarray) -> float | None:
            r = float(np.mean(R[idx, t]))
            if betas is not None:
                b = float(np.mean(np.asarray(betas, float)[idx, t - 1]))
                if not (np.isfinite(b) and b > beta_floor):
                    return None  # degenerate leg beta — skip this date rather than explode it
                r = r / b
            return r

        lo_r, hi_r = leg_return(low), leg_return(high)
        if lo_r is None or hi_r is None:
            continue  # leaves NaN
        out[t] = sign * (lo_r - hi_r)
    return out


def rolling_idio_vol(
    asset_returns: np.ndarray, market_returns: np.ndarray, window: int = 60
) -> np.ndarray:
    """Per-date idiosyncratic volatility: the std of the residual from a trailing-``window`` OLS
    of the asset on [1, market]. Length-T series aligned to ``asset_returns``; NaN where the
    trailing window is shorter than ``window`` (or degenerate). The IVOL characteristic."""
    y, x = align(np.asarray(asset_returns, float), np.asarray(market_returns, float))
    n = y.shape[0]
    out = np.full(n, np.nan)
    for t in range(n):
        lo = t + 1 - window
        if lo < 0:
            continue
        yy, xx = y[lo : t + 1], x[lo : t + 1]
        X = np.column_stack([np.ones(window), xx])
        coef, *_ = np.linalg.lstsq(X, yy, rcond=None)
        resid = yy - X @ coef
        out[t] = float(np.std(resid))
    return out


def rolling_amihud(
    asset_returns: np.ndarray, dollar_volume: np.ndarray, window: int = 60
) -> np.ndarray:
    """Rolling Amihud illiquidity: the trailing-``window`` mean of |return| / dollar-volume
    (price impact per dollar traded). Length-T series; NaN where the window is short or has no
    positive volume. Higher = more illiquid. The AMIHUD characteristic."""
    r, dv = align(np.asarray(asset_returns, float), np.asarray(dollar_volume, float))
    n = r.shape[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        daily = np.where(dv > 0, np.abs(r) / dv, np.nan)
    out = np.full(n, np.nan)
    for t in range(n):
        lo = t + 1 - window
        if lo < 0:
            continue
        w = daily[lo : t + 1]
        finite = w[np.isfinite(w)]
        if finite.size:
            out[t] = float(np.mean(finite))
    return out


def sector_excess_returns(
    returns_by_ticker: dict[str, np.ndarray],
    sector_by_ticker: dict[str, str | None],
    market_returns: np.ndarray,
) -> dict[str, np.ndarray]:
    """Per-sector daily EXCESS return: the equal-weight basket of a sector's names minus the
    market. One length-T series per sector that has at least one name with a known sector.

    Equal-weighted (not value-weighted): VW needs market cap = price × shares outstanding, a
    fundamental we don't have until the Mercury feed — this is the documented interim proxy.
    Returns ``{sector: excess_series}``; sectors are uppercased, ``None``/blank ignored."""
    mkt = np.asarray(market_returns, float)
    T = mkt.shape[0]
    members: dict[str, list[np.ndarray]] = {}
    for ticker, sec in sector_by_ticker.items():
        if not sec:
            continue
        r = returns_by_ticker.get(ticker)
        if r is None:
            continue
        r = np.asarray(r, float)
        if r.shape[0] < T:
            continue
        members.setdefault(sec.upper(), []).append(r[-T:])
    out: dict[str, np.ndarray] = {}
    for sec, series in members.items():
        # nanmean: a name contributes only on dates it has a return (NaN before its first bar).
        with np.errstate(invalid="ignore"):
            basket = np.nanmean(np.vstack(series), axis=0)
        out[sec] = basket - mkt
    return out
