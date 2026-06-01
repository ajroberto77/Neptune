"""Beta estimation pipeline.

The order is a HARD invariant (see CLAUDE.md, layer-4):

  1. Raw beta  -> a single EWMA-weighted regression (lambda=0.94, 252-day) with the
                  Dimson lead/lag market terms (k=-1,0,+1) folded into the SAME
                  regression. Raw beta = sum of contemporaneous + lag + lead market
                  coefficients. The regression also yields the estimation variance.
  2. Vasicek   -> shrink the raw estimate toward 1.0 as the FINAL model step:
                  beta = w*beta_raw + (1-w)*1.0,  w = var_prior / (var_prior + var_ols).
  3. Forward   -> a per-position forward_beta override supersedes the whole pipeline.

Dimson is part of *estimation* (it corrects the regression for asynchronous/illiquid
pricing), so it lives inside the regression — never as a post-shrinkage tweak.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neptune.quant.returns import align

DEFAULT_LAMBDA = 0.94
DEFAULT_LOOKBACK = 252
DEFAULT_LAGS = (-1, 0, 1)
PRIOR_MEAN = 1.0  # betas shrink toward the market mean of 1.0


@dataclass
class RawBetaResult:
    """Output of the EWMA + Dimson regression (step 1)."""

    beta_raw: float          # sum of contemporaneous + lag + lead market coefficients
    var_ols: float           # estimation variance of beta_raw (drives Vasicek weight)
    coefficients: np.ndarray  # [intercept, *market coefficients per lag]
    n_obs: int               # rows used after lag/lead trimming and lookback


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
    Vasicek. The pipeline beta (EWMA+Dimson → Vasicek → forward override) and the
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


def _ewma_weights(n: int, lam: float) -> np.ndarray:
    """EWMA weights for ``n`` chronological rows (oldest first, newest last).

    The newest row gets weight lambda^0, the previous lambda^1, etc. Weights are
    normalized to sum to ``n`` so the estimation variance keeps an OLS-like scale.
    """
    ages = np.arange(n - 1, -1, -1, dtype=float)  # oldest row has the largest age
    raw = lam ** ages
    return raw * (n / raw.sum())


def _dimson_design(stock: np.ndarray, market: np.ndarray, lags: tuple[int, ...]):
    """Build the Dimson design matrix: intercept + one market column per lag/lead.

    Column for offset ``k`` is the market return at t+k, so k<0 is a lagged market
    return (delayed reaction) and k>0 is a lead. Rows are trimmed to the range where
    every offset is defined, preserving chronological order.
    """
    n = stock.shape[0]
    lo, hi = max(0, -min(lags)), max(0, max(lags))
    start, stop = lo, n - hi            # valid t in [start, stop)
    if stop - start < len(lags) + 2:
        raise ValueError("not enough observations for the Dimson regression")
    y = stock[start:stop]
    cols = [np.ones(stop - start)]      # intercept
    for k in lags:
        cols.append(market[start + k:stop + k])
    return np.column_stack(cols), y


def raw_beta_ewma_dimson(
    stock_returns: np.ndarray,
    market_returns: np.ndarray,
    lam: float = DEFAULT_LAMBDA,
    lookback: int = DEFAULT_LOOKBACK,
    lags: tuple[int, ...] = DEFAULT_LAGS,
) -> RawBetaResult:
    """Step 1: EWMA-weighted Dimson regression; raw beta = sum of market coefficients."""
    stock, market = align(np.asarray(stock_returns, float), np.asarray(market_returns, float))
    X, y = _dimson_design(stock, market, lags)

    # Keep the most recent `lookback` rows (rows are chronological).
    if X.shape[0] > lookback:
        X, y = X[-lookback:], y[-lookback:]

    n, p = X.shape
    w = _ewma_weights(n, lam)
    W = np.diag(w)

    XtW = X.T @ W
    XtWX = XtW @ X
    XtWX_inv = np.linalg.inv(XtWX)
    coef = XtWX_inv @ (XtW @ y)

    # Market coefficients are everything except the intercept (column 0).
    selector = np.zeros(p)
    selector[1:] = 1.0
    beta_raw = float(selector @ coef)

    # Weighted residual variance with a (n - p) dof correction; weights sum to n.
    resid = y - X @ coef
    dof = max(n - p, 1)
    sigma2_resid = float((w * resid**2).sum() / dof)

    # Variance of the *sum* of the market coefficients: a^T Cov(coef) a.
    cov_coef = sigma2_resid * XtWX_inv
    var_ols = float(selector @ cov_coef @ selector)

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
    lam: float = DEFAULT_LAMBDA,
    lookback: int = DEFAULT_LOOKBACK,
    lags: tuple[int, ...] = DEFAULT_LAGS,
) -> BetaResult:
    """Run the full pipeline. A PM ``forward_beta`` supersedes everything (step 3)."""
    if forward_beta is not None:
        return BetaResult(
            beta=float(forward_beta),
            beta_raw=float(forward_beta),
            var_ols=0.0,
            weight=1.0,
            method="forward_override",
        )
    raw = raw_beta_ewma_dimson(stock_returns, market_returns, lam, lookback, lags)
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
