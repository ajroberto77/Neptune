"""Beta estimation pipeline.

The order is a HARD invariant (see CLAUDE.md, layer-4):

  1. Raw beta  -> a plain OLS regression of stock returns on market returns over the most
                  recent 252 trading days (~1 year). Raw beta = the market slope; the
                  regression also yields its estimation variance (var_ols).
  2. Vasicek   -> shrink the raw estimate toward 1.0 as the FINAL model step:
                  beta = w*beta_raw + (1-w)*1.0,  w = var_prior / (var_prior + var_ols).
  3. Forward   -> a per-position forward_beta override supersedes the whole pipeline.

(History: §4 originally specified an EWMA(λ=0.94)+Dimson lead/lag regression. That weighted
only ~32 effective observations and its collinear lead/lag terms produced unstable, sometimes
sign-flipped betas, so it was revised to the 1-year OLS above. Vasicek remains last.)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neptune.quant.returns import align

DEFAULT_LOOKBACK = 252
PRIOR_MEAN = 1.0  # betas shrink toward the market mean of 1.0


@dataclass
class RawBetaResult:
    """Output of the OLS beta regression (step 1)."""

    beta_raw: float          # the OLS market slope
    var_ols: float           # estimation variance of beta_raw (drives Vasicek weight)
    coefficients: np.ndarray  # [intercept, market slope]
    n_obs: int               # rows used after the lookback window


@dataclass
class BetaResult:
    """Final pipeline output (step 2/3)."""

    beta: float              # the beta to use downstream
    beta_raw: float          # pre-shrinkage raw beta
    var_ols: float
    weight: float            # Vasicek weight w in [0, 1]
    method: str              # "pipeline" or "forward_override"


@dataclass(frozen=True)
class SemiBeta:
    """Downside vs upside beta — a STRESS-ONLY diagnostic, never the model beta."""

    down_beta: float         # contemporaneous slope on down-market (market < 0) days
    up_beta: float           # contemporaneous slope on up-market (market > 0) days


def semibetas(
    stock: np.ndarray, market: np.ndarray, fallback: float, min_obs: int = 10
) -> SemiBeta:
    """Down/up-market beta: a plain contemporaneous OLS slope fit separately on down-market
    (``market < 0``) and up-market (``market > 0``) days. This captures how a name behaves in
    selloffs vs rallies — the asymmetry a single beta hides.

    It is deliberately NOT the beta pipeline: no EWMA weighting, no Dimson lead/lag, no
    Vasicek. The pipeline beta (252-day OLS → Vasicek → forward override) and the
    ``|β| ≤ 0.05`` hard constraint are untouched; this feeds the stress scenarios only.
    Degenerate subsets (fewer than ``min_obs`` days, or zero market variance) fall back to
    ``fallback`` (the pipeline beta), so a thin side never invents a spurious slope."""
    stock = np.asarray(stock, dtype=float)
    market = np.asarray(market, dtype=float)

    def _slope(mask: np.ndarray) -> float:
        m, s = market[mask], stock[mask]
        if m.size < min_obs:
            return fallback
        var = float(np.var(m))
        if var == 0.0:
            return fallback
        return float(np.cov(s, m, bias=True)[0, 1] / var)

    return SemiBeta(down_beta=_slope(market < 0.0), up_beta=_slope(market > 0.0))


def raw_beta(
    stock_returns: np.ndarray,
    market_returns: np.ndarray,
    lookback: int = DEFAULT_LOOKBACK,
) -> RawBetaResult:
    """Step 1: a plain OLS regression of stock returns on market returns over the most recent
    ``lookback`` (default 252 = ~1 year) trading days. Raw β is the market slope; ``var_ols`` is
    the OLS variance of that slope (the input to Vasicek shrinkage).

    This REPLACED the EWMA(λ=0.94) + Dimson lead/lag regression (CLAUDE.md §4, revised). That
    form weighted only ~32 effective observations, and its three collinear market terms
    (t−1, t, t+1) produced unstable, sometimes sign-flipped betas (e.g. a high-beta tech name
    estimated negative). A 1-year equal-weight OLS recovers stable, accurate betas and barely
    moves when a new day lands. Vasicek shrinkage remains the final model step.
    """
    stock, market = align(np.asarray(stock_returns, float), np.asarray(market_returns, float))
    if stock.shape[0] > lookback:
        stock, market = stock[-lookback:], market[-lookback:]
    n = stock.shape[0]
    if n < 3:
        raise ValueError("not enough observations for the beta regression")

    X = np.column_stack([np.ones(n), market])  # intercept + market
    XtX_inv = np.linalg.inv(X.T @ X)
    coef = XtX_inv @ (X.T @ stock)
    beta_raw = float(coef[1])

    resid = stock - X @ coef
    dof = max(n - 2, 1)
    sigma2 = float((resid**2).sum() / dof)
    var_ols = float(sigma2 * XtX_inv[1, 1])  # variance (SE²) of the OLS slope
    return RawBetaResult(beta_raw=beta_raw, var_ols=var_ols, coefficients=coef, n_obs=n)


def vasicek_shrinkage(
    beta_raw: float,
    var_ols: float,
    var_prior: float,
    prior_mean: float = PRIOR_MEAN,
) -> tuple[float, float]:
    """Step 2: shrink toward ``prior_mean`` (=1.0). Returns (shrunk_beta, weight w).

    w = var_prior / (var_prior + var_ols). A noisy raw estimate (large var_ols) gets a
    smaller w and is pulled harder toward 1.0; a precise estimate (var_ols->0) gives
    w->1 (no shrinkage).
    """
    if var_prior <= 0:
        raise ValueError("var_prior (cross-sectional beta dispersion) must be > 0")
    w = var_prior / (var_prior + var_ols)
    beta = w * beta_raw + (1.0 - w) * prior_mean
    return beta, w


def beta_pipeline(
    stock_returns: np.ndarray,
    market_returns: np.ndarray,
    var_prior: float,
    forward_beta: float | None = None,
    lookback: int = DEFAULT_LOOKBACK,
) -> BetaResult:
    """Run the full pipeline: 252-day OLS raw beta → Vasicek shrinkage. A PM ``forward_beta``
    supersedes everything (step 3)."""
    if forward_beta is not None:
        return BetaResult(
            beta=float(forward_beta),
            beta_raw=float(forward_beta),
            var_ols=0.0,
            weight=1.0,
            method="forward_override",
        )
    raw = raw_beta(stock_returns, market_returns, lookback)
    beta, w = vasicek_shrinkage(raw.beta_raw, raw.var_ols, var_prior)
    return BetaResult(
        beta=beta,
        beta_raw=raw.beta_raw,
        var_ols=raw.var_ols,
        weight=w,
        method="pipeline",
    )


def cross_sectional_prior_var(raw_betas: np.ndarray) -> float:
    """Vasicek prior variance = cross-sectional dispersion of raw betas across the
    universe. Used to derive the shrinkage weight when running a book of names."""
    arr = np.asarray(raw_betas, dtype=float)
    if arr.size < 2:
        raise ValueError("need at least 2 raw betas to estimate cross-sectional variance")
    return float(np.var(arr, ddof=1))


@dataclass(frozen=True)
class BetaPoint:
    """One sample of the walk-forward beta history."""

    end_index: int   # index into the (aligned) return arrays this fit ends on (inclusive)
    beta_raw: float  # raw OLS beta on the trailing window ending at end_index
    beta: float      # Vasicek-shrunk beta
    n_obs: int       # observations in the trailing window


def beta_history(
    stock_returns: np.ndarray,
    market_returns: np.ndarray,
    var_prior: float,
    lookback: int = DEFAULT_LOOKBACK,
    points: int = 26,
    step: int = 5,
    min_obs: int = 3,
) -> list[BetaPoint]:
    """Walk-forward beta: refit the 252-day OLS → Vasicek beta on the trailing ``lookback``
    window ending at each of the most recent ``points`` sample dates (sampled every ``step``
    returns). POINT-IN-TIME — each fit uses only returns up to its end index, so this is exactly
    what the beta WOULD have read on that date; plotting it shows how stable a name's beta is over
    time (and why a hedge sized last week can differ from today).

    ``stock_returns`` and ``market_returns`` must be date-aligned and equal length (the caller
    aligns and owns the date mapping). Samples with fewer than ``min_obs`` trailing observations
    are skipped. Returns oldest→newest."""
    stock = np.asarray(stock_returns, dtype=float)
    market = np.asarray(market_returns, dtype=float)
    if stock.shape[0] != market.shape[0]:
        raise ValueError("stock and market return series must be aligned and equal length")
    n = stock.shape[0]
    if n == 0:
        return []
    # Sample end indices: newest at n-1, stepping back, up to `points` samples (oldest first).
    ends = sorted({max(0, n - 1 - step * k) for k in range(max(points, 1))})
    out: list[BetaPoint] = []
    for end in ends:
        s, m = stock[: end + 1], market[: end + 1]
        if s.shape[0] < min_obs:
            continue
        rb = raw_beta(s, m, lookback)  # raw_beta keeps only the trailing `lookback` itself
        beta, _ = vasicek_shrinkage(rb.beta_raw, rb.var_ols, var_prior)
        out.append(BetaPoint(end_index=end, beta_raw=rb.beta_raw, beta=beta, n_obs=rb.n_obs))
    return out


@dataclass(frozen=True)
class RollingBeta:
    """Trailing-window OLS beta at EVERY index (the materialized daily series)."""

    beta_raw: np.ndarray  # OLS slope on the window ending at each index (NaN where invalid)
    var_ols: np.ndarray   # estimation variance of that slope
    n_obs: np.ndarray     # window length used at each index (int)


def rolling_ols(
    stock_returns: np.ndarray,
    market_returns: np.ndarray,
    lookback: int = DEFAULT_LOOKBACK,
) -> RollingBeta:
    """Vectorized trailing-window OLS beta at every index in ONE pass (no per-date Python loop) —
    the engine behind materializing the daily beta series after ingest. For index ``i`` the window
    is the trailing ``min(i+1, lookback)`` observations ending at ``i``. Returns ``beta_raw``,
    ``var_ols`` and ``n_obs`` arrays the length of the inputs; entries with fewer than 3 obs or
    zero market variance are NaN. Matches ``raw_beta``'s slope and var_ols on a full window.

    Window sums come from cumulative sums, so the whole series is O(n) regardless of lookback."""
    s = np.asarray(stock_returns, dtype=float)
    m = np.asarray(market_returns, dtype=float)
    if s.shape[0] != m.shape[0]:
        raise ValueError("stock and market return series must be aligned and equal length")
    n = s.shape[0]
    if n == 0:
        empty = np.array([])
        return RollingBeta(empty, empty, empty.astype(int))

    def _cumsum0(a: np.ndarray) -> np.ndarray:
        out = np.zeros(a.shape[0] + 1)
        out[1:] = np.cumsum(a)
        return out

    cm, cy, cmm, cyy, cmy = (_cumsum0(a) for a in (m, s, m * m, s * s, m * s))
    idx = np.arange(n)
    L = np.minimum(idx + 1, lookback)          # window length at each index
    start = idx + 1 - L                         # window start (inclusive), in 0..n
    end = idx + 1
    Lf = L.astype(float)
    Sx = cm[end] - cm[start]
    Sy = cy[end] - cy[start]
    Sxx = cmm[end] - cmm[start]
    Syy = cyy[end] - cyy[start]
    Sxy = cmy[end] - cmy[start]
    cxx = Sxx - Sx * Sx / Lf                    # centered Σ(m-mean)²
    cxy = Sxy - Sx * Sy / Lf
    cyy_c = Syy - Sy * Sy / Lf
    with np.errstate(divide="ignore", invalid="ignore"):
        beta = cxy / cxx
        dof = np.maximum(L - 2, 1).astype(float)
        sigma2 = (cyy_c - beta * cxy) / dof      # residual variance
        var_ols = sigma2 / cxx                   # = sigma2 * (XᵀX)⁻¹[1,1] for centered design
    invalid = (cxx <= 0) | (L < 3)
    beta = np.where(invalid, np.nan, beta)
    var_ols = np.where(invalid, np.nan, np.maximum(var_ols, 0.0))
    return RollingBeta(beta_raw=beta, var_ols=var_ols, n_obs=L.astype(int))
