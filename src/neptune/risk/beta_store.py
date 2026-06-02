"""Materialize and read the daily beta series.

Daily betas are deterministic given prices + the FIXED Vasicek prior (a name's beta no longer
depends on what else is in the book), so they're computed ONCE per ingest in a single vectorized
sweep and stored in ``securities.betas`` — never recomputed on the fly per request. Read paths
(beta history, the hedge backtest) read the stored series.

Rules (per the PM):
* a beta is stored ONLY for dates with a full trailing ``lookback`` window — so 1000 trading days
  of prices yield ~750 days of beta; a name with < ``lookback`` of its own history gets nothing
  until it has enough;
* the sweep is idempotent — re-run after each ingest to extend the series and fill as more (or
  previously-missing) history lands.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from neptune.data.db_market import DbMarketData, TickerNotFound
from neptune.quant.beta import DEFAULT_LOOKBACK, rolling_ols
from neptune.quant.factors import STYLE_FACTORS, rolling_factor_loadings
from neptune.risk.analytics import DEFAULT_PRIOR_VAR
from neptune.securities.models import Beta, FactorLoading, Security

DEFAULT_LOADING_WINDOW = 60      # style-loading regression window (matches factor_loadings)
DEFAULT_FACTOR_MODEL = "FF5MOM"  # the factor set/window these loadings were fit against


def rebuild_betas(
    session: Session,
    benchmark: str = "SPY",
    lookback: int = DEFAULT_LOOKBACK,
    prior_var: float = DEFAULT_PRIOR_VAR,
    tickers: list[str] | None = None,
) -> int:
    """Recompute and store the daily beta series for ``tickers`` (or every backfilled name) vs
    ``benchmark``. One vectorized rolling-OLS pass per name; only full-window dates are written.
    Idempotent: each name's rows for this benchmark are replaced. Returns rows written."""
    md = DbMarketData(session, benchmark=benchmark)
    market = np.asarray(md.market_returns(), dtype=float)
    dates = md.return_dates()
    n = min(market.shape[0], len(dates))
    if n < lookback:
        return 0
    market, dates = market[-n:], dates[-n:]

    names = tickers if tickers is not None else md.available_tickers()
    id_by_ticker = dict(
        session.execute(
            select(Security.ticker, Security.instrument_id).where(Security.ticker.in_(names))
        ).all()
    )

    written = 0
    for ticker in names:
        iid = id_by_ticker.get(ticker)
        if iid is None:
            continue
        # Always clear this name's stored series first (idempotent; handles shrink/refill).
        session.execute(
            delete(Beta).where(Beta.instrument_id == iid, Beta.benchmark == benchmark)
        )
        try:
            rets = np.asarray(md.ticker_returns(ticker), dtype=float)
        except TickerNotFound:
            continue
        k = min(rets.shape[0], n)
        if k < lookback:
            continue  # not enough of the name's own history for a single full window
        r, m, nd = rets[-k:], market[-k:], dates[-k:]
        roll = rolling_ols(r, m, lookback)
        # Full-window dates only: n_obs == lookback and a finite fit.
        full = (roll.n_obs >= lookback) & np.isfinite(roll.beta_raw) & np.isfinite(roll.var_ols)
        if not full.any():
            continue
        w = prior_var / (prior_var + roll.var_ols)          # Vasicek weight (vectorized)
        beta = w * roll.beta_raw + (1.0 - w) * 1.0
        rows = [
            {
                "instrument_id": iid, "ts": nd[i], "benchmark": benchmark, "lookback": lookback,
                "beta": float(beta[i]), "beta_raw": float(roll.beta_raw[i]),
                "var_ols": float(roll.var_ols[i]), "n_obs": int(roll.n_obs[i]),
                "prior_var": prior_var,
            }
            for i in np.nonzero(full)[0]
        ]
        if rows:
            session.bulk_insert_mappings(Beta, rows)
            written += len(rows)
    session.commit()
    return written


def rebuild_loadings(
    session: Session,
    benchmark: str = "SPY",
    window: int = DEFAULT_LOADING_WINDOW,
    model: str = DEFAULT_FACTOR_MODEL,
    tickers: list[str] | None = None,
) -> int:
    """Recompute and store the daily STYLE-FACTOR loadings for ``tickers`` (or every backfilled
    name) via the multivariate factor regression. No-op (returns 0) until the factor return panel
    is loaded — without it the hedge is beta-only. Full-window dates only; idempotent per name."""
    md = DbMarketData(session, benchmark=benchmark)
    factors = md.factor_returns()
    style = [f for f in STYLE_FACTORS if f in factors]
    if not style:
        return 0  # factor panel not ingested yet — nothing to materialize
    dates = md.return_dates()
    if len(dates) < window:
        return 0

    names = tickers if tickers is not None else md.available_tickers()
    id_by_ticker = dict(
        session.execute(
            select(Security.ticker, Security.instrument_id).where(Security.ticker.in_(names))
        ).all()
    )
    written = 0
    for ticker in names:
        iid = id_by_ticker.get(ticker)
        if iid is None:
            continue
        session.execute(
            delete(FactorLoading).where(
                FactorLoading.instrument_id == iid, FactorLoading.model == model
            )
        )
        try:
            rets = md.ticker_returns(ticker)
        except TickerNotFound:
            continue
        if np.asarray(rets, dtype=float).shape[0] < window:
            continue
        roll = rolling_factor_loadings(rets, factors, window)
        nd = dates[-roll.n_obs.shape[0]:]  # the name's dates, aligned to the loading series
        full = roll.n_obs >= window
        if not full.any():
            continue
        rows = []
        for i in np.nonzero(full)[0]:
            for f in style:
                val = roll.loadings[f][i]
                if np.isfinite(val):
                    rows.append({
                        "instrument_id": iid, "ts": nd[i], "factor": f, "model": model,
                        "loading": float(val), "window": window, "n_obs": int(roll.n_obs[i]),
                    })
        if rows:
            session.bulk_insert_mappings(FactorLoading, rows)
            written += len(rows)
    session.commit()
    return written


def stored_loadings_asof(
    session: Session, as_of: date, model: str = DEFAULT_FACTOR_MODEL
) -> dict[str, dict[str, float]]:
    """Every name's stored style loadings AS OF ``as_of`` → {ticker: {factor: loading}}. One
    query; empty if loadings aren't materialized (caller treats as beta-only)."""
    rows = session.execute(
        select(Security.ticker, FactorLoading.factor, FactorLoading.loading)
        .join(FactorLoading, FactorLoading.instrument_id == Security.instrument_id)
        .where(FactorLoading.ts == as_of, FactorLoading.model == model)
    ).all()
    out: dict[str, dict[str, float]] = {}
    for ticker, factor, loading in rows:
        out.setdefault(ticker, {})[factor] = float(loading)
    return out


def stored_loadings_latest(
    session: Session, model: str = DEFAULT_FACTOR_MODEL
) -> dict[str, dict[str, float]]:
    """Each name's MOST RECENT stored loadings → {ticker: {factor: loading}} (for the live
    universe). Empty if nothing is materialized."""
    latest = session.scalar(
        select(FactorLoading.ts).where(FactorLoading.model == model)
        .order_by(FactorLoading.ts.desc()).limit(1)
    )
    return stored_loadings_asof(session, latest, model) if latest is not None else {}


def loadings_materialized(session: Session, model: str = DEFAULT_FACTOR_MODEL) -> bool:
    """Whether any style loadings are stored (used to decide a lazy rebuild)."""
    return session.scalar(
        select(FactorLoading.ts).where(FactorLoading.model == model).limit(1)
    ) is not None


def stored_beta_series(
    session: Session, ticker: str, benchmark: str = "SPY"
) -> list[tuple[date, float, float, int]]:
    """The stored daily series for a name: ``[(ts, beta, beta_raw, n_obs)]`` oldest→newest."""
    iid = session.scalar(select(Security.instrument_id).where(Security.ticker == ticker))
    if iid is None:
        return []
    rows = session.execute(
        select(Beta.ts, Beta.beta, Beta.beta_raw, Beta.n_obs)
        .where(Beta.instrument_id == iid, Beta.benchmark == benchmark)
        .order_by(Beta.ts)
    ).all()
    return [(ts, float(b), float(br), int(no)) for ts, b, br, no in rows]


def stored_betas_asof(
    session: Session, as_of: date, benchmark: str = "SPY"
) -> dict[str, float]:
    """Every name's stored beta AS OF ``as_of`` (the row dated exactly ``as_of``) → {ticker: beta}.
    The point-in-time snapshot the backtest reads at each rebalance — one query, no regressions."""
    rows = session.execute(
        select(Security.ticker, Beta.beta)
        .join(Beta, Beta.instrument_id == Security.instrument_id)
        .where(Beta.ts == as_of, Beta.benchmark == benchmark)
    ).all()
    return {t: float(b) for t, b in rows}


def ticker_history_sampled(
    session: Session, ticker: str, benchmark: str, points: int, step: int
) -> list[dict] | None:
    """Stored daily series for ``ticker`` down-sampled to the most recent ``points`` every
    ``step`` rows (for the beta-history chart). None if the name isn't materialized yet."""
    series = stored_beta_series(session, ticker, benchmark)
    if not series:
        return None
    idxs = sorted({max(0, len(series) - 1 - step * k) for k in range(max(points, 1))})
    return [
        {"date": series[i][0].isoformat(), "beta": round(series[i][1], 4),
         "beta_raw": round(series[i][2], 4), "n_obs": series[i][3]}
        for i in idxs
    ]


def portfolio_net_history(
    session: Session, portfolio, benchmark: str, points: int, step: int
) -> list[dict] | None:
    """The book's NET beta over time from STORED betas, current weights fixed. A holding with no
    stored beta at a date falls back to the 1.0 prior (same as the live book). None if nothing in
    the book is materialized yet (caller computes on the fly)."""
    long_aum = portfolio.long_aum or 0.0
    if long_aum <= 0:
        return None
    maps: dict[str, dict] = {}
    all_dates: set = set()
    for p in portfolio.positions:
        if p.ticker not in maps:
            maps[p.ticker] = {ts: beta for ts, beta, _raw, _n in
                              stored_beta_series(session, p.ticker, benchmark)}
            all_dates |= set(maps[p.ticker])
    if not all_dates:
        return None
    axis = sorted(all_dates)
    idxs = sorted({max(0, len(axis) - 1 - step * k) for k in range(max(points, 1))})
    out = []
    for i in idxs:
        d = axis[i]
        nba = sum(p.signed_notional * maps[p.ticker].get(d, 1.0) for p in portfolio.positions)
        out.append({"date": d.isoformat(), "net_beta": round(nba / long_aum, 4)})
    return out


def latest_stored_date(session: Session, benchmark: str = "SPY") -> date | None:
    """The most recent date with any stored beta (for staleness checks / 'as of' display)."""
    return session.scalar(
        select(Beta.ts).where(Beta.benchmark == benchmark).order_by(Beta.ts.desc()).limit(1)
    )
