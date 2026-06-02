"""Walk-forward hedge backtest — the engine that turns 'what's the right level?' into a measured
curve instead of a judgment call.

Unlike ``analytics.portfolio_beta_history`` (which holds the CURRENT short basket fixed and just
shows beta drift), this RE-OPTIMIZES the hedge at each historical rebalance date and measures how
well the *process* would have controlled net beta out-of-sample:

  At each rebalance date t:
    1. point-in-time betas/loadings for the long book + discretionary shorts (the residual) and
       for the shortable universe, using ONLY returns up to t (no look-ahead);
    2. run the optimizer → a systematic short basket (weights as fractions of long AUM at t);
    3. hold that basket fixed to the next rebalance t+1;
    4. measure the REALIZED net beta at t+1 — the same weights, but betas re-estimated at t+1.
       The gap between the target (≈0 at t) and the realized value at t+1 is beta drift, the thing
       the |β| limit and the beta-add fence are meant to control.

Summary stats (realized net-beta RMSE, the share of dates inside a tolerance band, and hedge
turnover) are the control metrics the constraint levels should be calibrated against — never the
combined book's return/IR (the short book is a hedge, not alpha — CLAUDE.md §5).

The long book is held at its CURRENT holdings (Neptune does not store position history); this
isolates the hedging process, which is exactly what we calibrate. Pure over a market-data source
and a portfolio; no I/O of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from neptune.data.db_market import TickerNotFound
from neptune.domain.models import Portfolio, ShortType
from neptune.quant.beta import DEFAULT_LOOKBACK, raw_beta, vasicek_shrinkage
from neptune.quant.factors import STYLE_FACTORS
from neptune.quant.optimizer import Candidate, compute_residual, optimize_hedge_capped
from neptune.quant.optimizer import InfeasibleHedge
from neptune.risk.analytics import DEFAULT_PRIOR_VAR, MIN_BETA_OBS


@dataclass
class RebalancePoint:
    """One rebalance in the walk-forward."""

    date: str
    target_net_beta: float       # net beta the optimizer achieved at t (≈0)
    realized_net_beta: float     # same basket, betas re-estimated at the NEXT rebalance
    n_names: int                 # systematic shorts selected at t
    turnover: float              # Σ|Δweight| vs the previous rebalance (0 at the first)


@dataclass
class BacktestResult:
    points: list[RebalancePoint] = field(default_factory=list)
    rmse: float = 0.0            # RMSE of realized net beta across rebalances
    coverage: float = 0.0        # share of rebalances with |realized net beta| <= tol
    mean_abs_realized: float = 0.0
    mean_turnover: float = 0.0
    tol: float = 0.05
    n_rebalances: int = 0


def _beta_loadings_asof(
    rets: np.ndarray, market: np.ndarray, factor_series: dict[str, np.ndarray],
    end: int, var_prior: float, lookback: int,
) -> tuple[float, dict[str, float], int] | None:
    """Point-in-time (beta, style loadings, n_obs) for one name as of return-index ``end``,
    using only data up to ``end``. None if there isn't enough history to fit."""
    s = rets[: end + 1]
    m = market[: end + 1]
    if s.shape[0] < MIN_BETA_OBS:
        return None
    rb = raw_beta(s, m, lookback)
    if rb.n_obs < MIN_BETA_OBS:
        return None
    beta, _ = vasicek_shrinkage(rb.beta_raw, rb.var_ols, var_prior)
    loadings: dict[str, float] = {}
    win = min(s.shape[0], lookback)
    sw = s[-win:]
    for f, fs in factor_series.items():
        fw = fs[: end + 1][-win:]
        if fw.shape[0] != sw.shape[0] or float(np.var(fw)) == 0.0:
            continue
        loadings[f] = float(np.cov(sw, fw, bias=True)[0, 1] / np.var(fw))
    return beta, loadings, rb.n_obs


def hedge_backtest(
    market_data,
    portfolio: Portfolio,
    universe_tickers: list[str],
    *,
    lookback: int = DEFAULT_LOOKBACK,
    rebalance_step: int = 21,   # ~monthly
    points: int = 24,           # number of rebalances (newest-back)
    beta_tol: float = 0.05,
    factor_limit: float = 0.20,
    max_position_weight: float = 0.15,
    sector_limit: float = 0.30,
    beta_add_budget: float | None = None,
    n_cap: int = 35,
) -> BacktestResult:
    """Replay the hedging process over history; see module docstring. ``universe_tickers`` is the
    shortable set (current longs are excluded automatically). Returns realized-net-beta stats."""
    market = np.asarray(market_data.market_returns(), dtype=float)
    dates = list(market_data.return_dates())
    n = min(market.shape[0], len(dates))
    if n < MIN_BETA_OBS + rebalance_step:
        return BacktestResult(tol=beta_tol)
    market, dates = market[-n:], dates[-n:]
    factor_series = {
        f: np.asarray(s, dtype=float)[-n:]
        for f, s in market_data.factor_returns().items() if f in STYLE_FACTORS
    }

    long_aum = portfolio.long_aum or 0.0
    if long_aum <= 0:
        return BacktestResult(tol=beta_tol)
    long_tickers = {p.ticker for p in portfolio.longs}

    # Cache benchmark-aligned return series (tail of the common axis) for every name we touch.
    cache: dict[str, np.ndarray | None] = {}
    def series(ticker: str) -> np.ndarray | None:
        if ticker not in cache:
            try:
                r = np.asarray(market_data.ticker_returns(ticker), dtype=float)
                cache[ticker] = r[-n:] if r.shape[0] > n else r
            except TickerNotFound:
                cache[ticker] = None
        return cache[ticker]

    def name_index(r: np.ndarray, end: int) -> int:
        return end - (n - r.shape[0])  # the name's own index for axis position `end`

    # Rebalance indices: the most recent `points`, stepped, each needing a NEXT index to realize.
    last = n - 1 - rebalance_step  # leave room to measure forward
    ends = sorted({max(0, last - rebalance_step * k) for k in range(max(points, 1))})
    ends = [e for e in ends if e >= MIN_BETA_OBS]

    pq = portfolio  # alias
    rebal: list[RebalancePoint] = []
    prev_weights: dict[str, float] = {}
    realized: list[float] = []

    for end in ends:
        nxt = min(end + rebalance_step, n - 1)

        # --- residual from the long book + discretionary shorts, as of `end` ---
        resid_positions: list[tuple[float, float, dict[str, float]]] = []
        for p in pq.positions:
            if p.short_type is ShortType.SYSTEMATIC:
                continue  # the systematic book is what we re-propose
            r = series(p.ticker)
            beta, loadings = 1.0, {}
            if r is not None and r.shape[0]:
                bl = _beta_loadings_asof(r, market, factor_series, name_index(r, end),
                                         DEFAULT_PRIOR_VAR, lookback)
                if bl is not None:
                    beta, loadings, _ = bl
            resid_positions.append((p.signed_notional, beta, loadings))
        residual_beta, residual_factors = compute_residual(resid_positions, long_aum)

        # --- candidate universe as of `end` ---
        cands: list[Candidate] = []
        for t in universe_tickers:
            if t in long_tickers:
                continue
            r = series(t)
            if r is None or not r.shape[0]:
                continue
            bl = _beta_loadings_asof(r, market, factor_series, name_index(r, end),
                                     DEFAULT_PRIOR_VAR, lookback)
            if bl is None:
                continue
            beta, loadings, _ = bl
            idx = name_index(r, end)
            variance = float(np.var(r[: idx + 1][-lookback:])) if idx >= 1 else 1.0
            cands.append(Candidate(ticker=t, beta=beta, loadings=loadings, variance=variance))
        if len(cands) < 2:
            continue

        try:
            proposal = optimize_hedge_capped(
                residual_beta=residual_beta, residual_factors=residual_factors,
                universe=cands, long_aum=long_aum, n_cap=n_cap, beta_tol=beta_tol,
                factor_limit=factor_limit, max_position_weight=max_position_weight,
                sector_limit=sector_limit, beta_add_budget=beta_add_budget,
            )
        except (InfeasibleHedge, ValueError):
            continue

        weights = {s.ticker: s.weight for s in proposal.positions}

        # --- realize: same weights, betas re-estimated at the NEXT rebalance ---
        realized_beta_adj = 0.0
        # long/discretionary side at t+1
        for p in pq.positions:
            if p.short_type is ShortType.SYSTEMATIC:
                continue
            r = series(p.ticker)
            beta = 1.0
            if r is not None and r.shape[0]:
                bl = _beta_loadings_asof(r, market, factor_series, name_index(r, nxt),
                                         DEFAULT_PRIOR_VAR, lookback)
                if bl is not None:
                    beta = bl[0]
            realized_beta_adj += p.signed_notional * beta
        # the proposed shorts at t+1 (short → negative signed notional)
        for tkr, w in weights.items():
            r = series(tkr)
            beta = 1.0
            if r is not None and r.shape[0]:
                bl = _beta_loadings_asof(r, market, factor_series, name_index(r, nxt),
                                         DEFAULT_PRIOR_VAR, lookback)
                if bl is not None:
                    beta = bl[0]
            realized_beta_adj += -(w * long_aum) * beta
        realized_net = realized_beta_adj / long_aum

        turnover = sum(
            abs(weights.get(t, 0.0) - prev_weights.get(t, 0.0))
            for t in set(weights) | set(prev_weights)
        ) if prev_weights else 0.0

        rebal.append(RebalancePoint(
            date=dates[end].isoformat(),
            target_net_beta=round(proposal.net_beta_after, 4),
            realized_net_beta=round(realized_net, 4),
            n_names=len(weights),
            turnover=round(turnover, 4),
        ))
        realized.append(realized_net)
        prev_weights = weights

    if not realized:
        return BacktestResult(tol=beta_tol)
    arr = np.asarray(realized, dtype=float)
    turns = [p.turnover for p in rebal[1:]]  # first has no prior
    return BacktestResult(
        points=rebal,
        rmse=round(float(np.sqrt(np.mean(arr**2))), 4),
        coverage=round(float(np.mean(np.abs(arr) <= beta_tol)), 4),
        mean_abs_realized=round(float(np.mean(np.abs(arr))), 4),
        mean_turnover=round(float(np.mean(turns)) if turns else 0.0, 4),
        tol=beta_tol,
        n_rebalances=len(rebal),
    )


def calibrate_beta_add_budget(
    market_data, portfolio: Portfolio, universe_tickers: list[str],
    budgets: tuple[float, ...] = (0.005, 0.01, 0.015, 0.02, 0.03),
    **kwargs,
) -> list[dict]:
    """Sweep ``beta_add_budget`` and report the realized control vs. cost trade-off for each:
    realized net-beta RMSE, coverage (share inside tol), and mean turnover. The calibration rule
    is to pick the LOOSEST budget on the flat part of the RMSE curve that still passes the
    coverage target — tighter only adds turnover without improving control."""
    out = []
    for b in budgets:
        res = hedge_backtest(market_data, portfolio, universe_tickers,
                             beta_add_budget=b, **kwargs)
        out.append({
            "beta_add_budget": b,
            "rmse": res.rmse,
            "coverage": res.coverage,
            "mean_abs_realized": res.mean_abs_realized,
            "mean_turnover": res.mean_turnover,
            "n_rebalances": res.n_rebalances,
        })
    return out
