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
