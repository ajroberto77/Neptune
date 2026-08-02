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
    DEFAULT_LOOKBACK,
    RawBetaResult,
    beta_history as _beta_history_engine,
    raw_beta,
    vasicek_shrinkage,
)
from neptune.quant.factors import (
    FACTORS,
    MARKET_FACTOR,
    STYLE_FACTORS,
    factor_covariance,
    factor_loadings,
    residual_variance,
)
from neptune.quant.optimizer import Candidate, compute_residual
from neptune.securities.factor_ingest import latest_factor_date

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
    portfolio: Portfolio, metrics: dict[str, PositionMetrics],
    factors: tuple[str, ...] = FACTORS,
) -> tuple[float, dict[str, float]]:
    """Net beta and net factor exposures across the WHOLE book (normalized by long AUM).
    ``factors`` is the known factor set (pass the promoted-extended set when factors are promoted)."""
    if portfolio.long_aum <= 0:
        return 0.0, {f: 0.0 for f in factors}
    inputs = [
        (p.signed_notional, metrics[p.ticker].beta, metrics[p.ticker].loadings)
        for p in portfolio.positions
    ]
    return compute_residual(inputs, portfolio.long_aum, factors=factors)


def hedge_factor_set(market_data) -> tuple[str, ...]:
    """The factors the optimizer NEUTRALIZES: FF5+MOM plus any PROMOTED monitor factors that are
    actually present in the panel. Falls back to the plain style set when the panel isn't loaded
    (beta-only hedge, candidates carry no style loadings → the factor constraints are trivial)."""
    factors = market_data.factor_returns()
    promoted = tuple(getattr(market_data, "promoted_factors", ()) or ())
    present = tuple(f for f in (*STYLE_FACTORS, *promoted) if f in factors)
    return present or STYLE_FACTORS


def factor_panel_status(market_data) -> dict:
    """``{"loaded": bool, "last_date": str|None, "stale": bool}`` — whether the style-factor
    panel is present and, if so, whether it's fresh. Centralizes the stale-panel guard so
    every caller (``/risk``, ``/hedge/diagnostics``, ``/securities/health``) agrees on what
    "stale" means, instead of each computing it (or not) independently.

    ``loaded=False`` means the panel isn't there at all (beta-only, not a staleness question).
    ``stale=True`` on a LOADED panel means real style loadings exist but are more than 7 days
    older than the market's latest return date — a real gap distinct from "not loaded," and
    one that must never be silently reported as fine just because ``loaded`` is True."""
    panel = {"loaded": False, "last_date": None, "stale": True}
    factors_present = market_data.factor_returns()
    panel["loaded"] = all(f in factors_present for f in STYLE_FACTORS)
    sess = getattr(market_data, "session", None)
    if not panel["loaded"]:
        panel["stale"] = True
    elif sess is None:
        panel["stale"] = False  # synthetic/fallback: factors present, can't be stale
    else:
        last = latest_factor_date(sess)
        rdates = market_data.return_dates() if hasattr(market_data, "return_dates") else []
        panel["last_date"] = last.isoformat() if last else None
        panel["stale"] = bool(last is None or (rdates and (rdates[-1] - last).days > 7))
    return panel


def factor_cov_for(market_data, hedge_factors: tuple[str, ...]) -> np.ndarray | None:
    """The factor-return covariance F over [MKT, *hedge_factors] for the covariance objective, or
    None when the panel isn't loaded (the optimizer then uses its diagonal diversification)."""
    return factor_covariance(market_data.factor_returns(), (MARKET_FACTOR, *hedge_factors))


def all_factors(market_data) -> tuple[str, ...]:
    """The full known factor set (MKT + neutralized factors) for residual accumulation."""
    return (MARKET_FACTOR, *hedge_factor_set(market_data))


def residual_metrics(
    portfolio: Portfolio, metrics: dict[str, PositionMetrics],
    factors: tuple[str, ...] = FACTORS,
) -> tuple[float, dict[str, float]]:
    """Residual beta/factors the systematic short book must neutralize: long book +
    discretionary shorts only (systematic shorts are what the optimizer re-proposes).
    ``factors`` is the known factor set (pass the promoted-extended set when factors are promoted)."""
    if portfolio.long_aum <= 0:
        return 0.0, {f: 0.0 for f in factors}
    inputs = [
        (p.signed_notional, metrics[p.ticker].beta, metrics[p.ticker].loadings)
        for p in portfolio.positions
        if p.short_type is not ShortType.SYSTEMATIC
    ]
    return compute_residual(inputs, portfolio.long_aum, factors=factors)


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
        idio = residual_variance(rets, factors, loadings)  # diagonal of Σ = BᵀFB + D
        candidates.append(
            Candidate(ticker=t, beta=beta, loadings=loadings, sector=sector,
                      variance=variance, idio_var=idio)
        )
    return candidates


def db_universe(
    market_data, tickers: list[str] | None = None,
    min_adv_usd: float = 0.0, adv_window: int = 63,
) -> list[Candidate]:
    """The REAL shortable universe: build candidates from backfilled names (a ``DbMarketData``
    source) with their pipeline betas, factor loadings, and stored sectors. Unlike the
    synthetic ``live_universe``, names with too little price history to fit the OLS
    regression are skipped (they can't be sized), and the sector comes from the securities DB.

    ``min_adv_usd`` > 0 applies the liquidity screen (Option D): a name whose trailing average
    daily dollar volume is below the floor is dropped (can't reliably short it). Names whose
    volume isn't stored are kept (unknown ≠ illiquid)."""
    if tickers is None:
        tickers = market_data.available_tickers()
    market = market_data.market_returns()
    factors = market_data.factor_returns()
    adv_of = getattr(market_data, "average_dollar_volume", None)

    raws = {}
    for t in tickers:
        if min_adv_usd > 0 and adv_of is not None:
            adv = adv_of(t, adv_window)
            if adv is not None and adv < min_adv_usd:
                continue  # too illiquid to short — liquidity screen
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
    # Prefer MATERIALIZED style loadings (latest stored) over recomputing the multivariate factor
    # regression for the whole universe on every propose; fall back to computing per name when a
    # name isn't materialized (or the panel isn't loaded → empty → beta-only).
    stored_loadings: dict[str, dict[str, float]] = {}
    sess = getattr(market_data, "session", None)
    if sess is not None:
        from neptune.risk import beta_store  # lazy import avoids a circular dependency
        stored_loadings = beta_store.stored_loadings_latest(sess)
    candidates: list[Candidate] = []
    for t, raw in raws.items():
        beta, _ = vasicek_shrinkage(raw.beta_raw, raw.var_ols, prior_var)
        rets = market_data.ticker_returns(t)
        loadings = stored_loadings.get(t)
        if loadings is None:
            loadings = factor_loadings(rets, factors).loadings if len(factors) > 1 else {}
        variance = float(np.var(rets)) if rets.size else 1.0  # risk proxy for diversification
        # Idiosyncratic variance (D in Σ = BᵀFB + D) from the SAME loadings actually carried —
        # invariant to the intercept, so it works on stored loadings without re-fitting alpha.
        idio = residual_variance(rets, factors, loadings)
        candidates.append(
            Candidate(ticker=t, beta=beta, loadings=loadings,
                      sector=market_data.sector(t), variance=variance, idio_var=idio)
        )
    return candidates


# --- beta consistency / walk-forward history ----------------------------------------

def consistency_stats(betas: list[float]) -> dict | None:
    """Summary stats over a beta time series: where it sits now and how much it has drifted."""
    if not betas:
        return None
    arr = np.asarray(betas, dtype=float)
    return {
        "last": round(float(arr[-1]), 4),
        "mean": round(float(arr.mean()), 4),
        "std": round(float(arr.std(ddof=1)) if arr.size > 1 else 0.0, 4),
        "min": round(float(arr.min()), 4),
        "max": round(float(arr.max()), 4),
        "range": round(float(arr.max() - arr.min()), 4),
        "points": int(arr.size),
    }


def ticker_beta_history(
    market_data, ticker: str,
    lookback: int = DEFAULT_LOOKBACK, points: int = 26, step: int = 5,
) -> list[dict]:
    """Point-in-time beta for one name over the recent past (date-stamped), for the consistency
    chart. Aligns the name to the benchmark's return-date axis and refits on trailing windows."""
    market = np.asarray(market_data.market_returns(), dtype=float)
    rets = np.asarray(market_data.ticker_returns(ticker), dtype=float)  # may raise TickerNotFound
    dates = list(market_data.return_dates())
    k = min(market.shape[0], rets.shape[0], len(dates))
    if k < 3:
        return []
    market, rets, dates = market[-k:], rets[-k:], dates[-k:]
    pts = _beta_history_engine(rets, market, DEFAULT_PRIOR_VAR, lookback, points, step)
    return [
        {"date": dates[p.end_index].isoformat(), "beta": round(p.beta, 4),
         "beta_raw": round(p.beta_raw, 4), "n_obs": p.n_obs}
        for p in pts
    ]


def portfolio_beta_history(
    market_data, portfolio,
    lookback: int = DEFAULT_LOOKBACK, points: int = 26, step: int = 5,
) -> list[dict]:
    """The book's NET beta over time, holding the CURRENT weights fixed and using each name's
    point-in-time beta on the benchmark's date axis. This isolates beta DRIFT (how the hedge's
    target moved as betas re-estimated) from position changes — answering 'is the net beta
    consistent over time?'. A name without enough trailing history at a sample date falls back to
    the 1.0 prior (same as the live book), so the line is never broken by thin names."""
    market = np.asarray(market_data.market_returns(), dtype=float)
    dates = list(market_data.return_dates())
    n = min(market.shape[0], len(dates))
    if n < 3:
        return []
    market, dates = market[-n:], dates[-n:]
    long_aum = portfolio.long_aum or 0.0
    if long_aum <= 0:
        return []

    # Cache each name's benchmark-aligned return series (tail of the common date axis).
    cache: dict[str, np.ndarray | None] = {}
    for p in portfolio.positions:
        if p.ticker in cache:
            continue
        try:
            r = np.asarray(market_data.ticker_returns(p.ticker), dtype=float)
            cache[p.ticker] = r[-n:] if r.shape[0] > n else r
        except TickerNotFound:
            cache[p.ticker] = None

    ends = sorted({max(0, n - 1 - step * k) for k in range(max(points, 1))})
    series: list[dict] = []
    for end in ends:
        net_beta_adj = 0.0
        for p in portfolio.positions:
            r = cache.get(p.ticker)
            beta = 1.0  # insufficient/unpriced names hold at the prior, like the live book
            if r is not None and r.shape[0]:
                offset = n - r.shape[0]              # name starts `offset` returns into the axis
                name_end = end - offset              # its index for this sample date
                if name_end + 1 >= MIN_BETA_OBS:
                    rb = raw_beta(r[: name_end + 1], market[offset: end + 1])
                    beta, _ = vasicek_shrinkage(rb.beta_raw, rb.var_ols, DEFAULT_PRIOR_VAR)
            net_beta_adj += p.signed_notional * beta
        series.append({"date": dates[end].isoformat(), "net_beta": round(net_beta_adj / long_aum, 4)})
    return series
