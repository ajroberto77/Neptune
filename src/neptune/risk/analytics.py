"""Live book analytics: run the beta pipeline and factor regression over a portfolio.

This is the Risk Interface wiring that connects domain positions to the Quant Engine
using a market-data source. Per position:
  * the market beta is the PM ``forward_beta`` when set, else the full pipeline
    (252-day OLS raw beta -> Vasicek shrinkage), with the Vasicek prior variance taken
    from the cross-section of raw betas in the book;
  * factor loadings always come from the regression (a forward beta overrides only the
    market beta, never the factor loadings).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neptune.data.db_market import TickerNotFound
from neptune.data.market import SyntheticMarketData
from neptune.data.source import MarketData
from neptune.domain.models import Portfolio, ShortType
from neptune.quant.beta import (
    RawBetaResult,
    raw_beta,
    vasicek_shrinkage,
)
from neptune.quant.factors import factor_loadings
from neptune.quant.optimizer import Candidate, compute_residual

# The Vasicek shrinkage prior variance — a FIXED, market-level constant (cross-sectional beta
# dispersion across equities, σ≈0.5 ⇒ var≈0.25). It is deliberately NOT estimated from the
# current book: a book-derived prior makes every name's beta depend on what else you hold (a
# 3-name book over-shrinks toward 1.0; a 38-name book barely shrinks), so the same long silently
# re-prices the instant you trade — and the hedge, sized against the book at propose time, no
# longer matches the book after booking. A fixed prior keeps betas stable and book/candidates in
# one frame, so a correct hedge actually drives Net beta to ~0 and STAYS there.
DEFAULT_PRIOR_VAR = 0.25

# Minimum REAL return observations before a beta is considered trustworthy. raw_beta only
# needs 3 to solve the regression, but a slope fit on a handful of days is noise — and after
# the leading-backfill fix a late-ingested name legitimately has a short real window. Below
# this floor the Risk Interface reports the name as ``insufficient_data`` (beta held at the
# 1.0 prior) and the optimizer won't pick it as a hedge, rather than quoting a flaky beta.
# ~3 trading months; the math floor (n>=3) stays in the engine, this is the reporting policy.
MIN_BETA_OBS = 60


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
    prior_var: float | None = None,
) -> dict[str, PositionMetrics]:
    """Two-pass live metrics: raw betas first (to set the Vasicek prior), then shrink.

    ``prior_var`` is the Vasicek prior to shrink against. **Pass the STABLE universe-level prior
    (``shortable_prior_var``) here** so a name's beta depends only on its own returns, never on
    what else is in the book. If left None, the prior is estimated from the book's OWN cross-
    section — which makes betas drift as positions are added/removed (a 3-name book over-shrinks
    toward 1.0; a 38-name book barely shrinks), so the same long re-prices the instant you trade.
    That book-dependent path is kept only for the synthetic/test sources that have no universe."""
    market = market_data.market_returns()
    factors = market_data.factor_returns()

    # A name with too little price history for the beta regression yet gets a sentinel
    # raw beta (prior 1.0, infinite estimation variance) instead of crashing — Vasicek then
    # shrinks it fully to the prior. Real prices/P&L still work; only the beta waits for data.
    raws, insufficient = {}, set()
    for p in portfolio.positions:
        try:
            r = raw_beta(market_data.ticker_returns(p.ticker), market)
        except (ValueError, TickerNotFound):
            r = None  # too little history (ValueError) OR no stored prices (TickerNotFound)
        if r is None or r.n_obs < MIN_BETA_OBS:
            # No usable history, or fewer than MIN_BETA_OBS real days: sentinel prior-1.0 beta,
            # fully shrunk, flagged insufficient_data — never crash or quote a flaky slope.
            raws[p.ticker] = RawBetaResult(
                beta_raw=1.0, var_ols=float("inf"), coefficients=np.array([]), n_obs=0
            )
            insufficient.add(p.ticker)
        else:
            raws[p.ticker] = r

    # The Vasicek prior: the FIXED market-level constant (default_prior_var) unless a caller
    # passes one explicitly. Never the book's own cross-section — that would make a name's beta
    # depend on book composition and re-price every long the moment you trade.
    if prior_var is None:
        prior_var = default_prior_var

    metrics: dict[str, PositionMetrics] = {}
    for p in portfolio.positions:
        raw = raws[p.ticker]
        if p.forward_beta is not None:
            beta, method, w = float(p.forward_beta), "forward_override", 1.0
        elif p.ticker in insufficient:
            beta, method, w = 1.0, "insufficient_data", 0.0
        else:
            beta, w = vasicek_shrinkage(raw.beta_raw, raw.var_ols, prior_var)
            method = "pipeline"
        # Unpriced/insufficient names have no return series to regress → empty loadings.
        loadings = (
            {} if p.ticker in insufficient
            else factor_loadings(market_data.ticker_returns(p.ticker), factors).loadings
        )
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
    raws = {t: raw_beta(market_data.ticker_returns(t), market) for t in tickers}
    prior_var = DEFAULT_PRIOR_VAR  # fixed market-level prior — same frame as the book (compute_metrics)
    candidates: list[Candidate] = []
    for t in tickers:
        beta, _ = vasicek_shrinkage(raws[t].beta_raw, raws[t].var_ols, prior_var)
        rets = market_data.ticker_returns(t)
        loadings = factor_loadings(rets, factors).loadings
        sector = market_data.spec_for(t).sector
        variance = float(np.var(rets)) if rets.size else 1.0
        candidates.append(
            Candidate(ticker=t, beta=beta, loadings=loadings, sector=sector, variance=variance)
        )
    return candidates


def db_universe(market_data, tickers: list[str] | None = None) -> list[Candidate]:
    """The REAL shortable universe: build candidates from backfilled names (a ``DbMarketData``
    source) with their pipeline betas, factor loadings, and stored sectors. Unlike the
    synthetic ``live_universe``, names with too little price history to fit the OLS
    regression are skipped (they can't be sized), and the sector comes from the securities DB.
    """
    if tickers is None:
        tickers = market_data.available_tickers()
    market = market_data.market_returns()
    factors = market_data.factor_returns()

    raws = {}
    for t in tickers:
        try:
            r = raw_beta(market_data.ticker_returns(t), market)
        except (ValueError, TickerNotFound):
            continue  # no history / no prices — not a usable hedge candidate
        if r.n_obs < MIN_BETA_OBS:
            continue  # too few real days for a trustworthy beta — don't short on a flaky slope
        raws[t] = r
    if not raws:
        return []
    prior_var = DEFAULT_PRIOR_VAR  # fixed market-level prior — same frame as the book (compute_metrics)
    candidates: list[Candidate] = []
    for t, raw in raws.items():
        beta, _ = vasicek_shrinkage(raw.beta_raw, raw.var_ols, prior_var)
        rets = market_data.ticker_returns(t)
        loadings = factor_loadings(rets, factors).loadings
        variance = float(np.var(rets)) if rets.size else 1.0  # risk proxy for diversification
        candidates.append(
            Candidate(ticker=t, beta=beta, loadings=loadings,
                      sector=market_data.sector(t), variance=variance)
        )
    return candidates
