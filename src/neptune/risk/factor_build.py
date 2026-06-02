"""Risk-Interface builder for Neptune's price-only factors.

Pulls the price/volume/beta panel from the securities DB, calls the pure constructions in
``quant.factor_build``, and persists the resulting daily series to ``factor_returns`` (with the
``neptune`` source tag) plus a row per factor in ``factor_definitions``. This is the I/O wrapper;
the math lives in the quant engine (CLAUDE.md §1).

Idempotent: a rebuild deletes the existing ``neptune``-sourced rows for the factors it produces
and re-inserts — Ken-French-sourced rows (SMB/HML/…) are never touched. Run AFTER ``rebuild_betas``
so the BAB factor has materialized betas to rank on; without them BAB is simply skipped.
"""
from __future__ import annotations

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from neptune.data.db_market import DbMarketData
from neptune.quant.factor_build import (
    long_short_returns,
    rolling_amihud,
    rolling_idio_vol,
    sector_excess_returns,
)
from neptune.securities.models import Beta, FactorDefinition, FactorReturn, Security

NEPTUNE_SOURCE = "neptune"
DEFAULT_WINDOW = 60
DEFAULT_FRAC = 0.3

# Registry metadata for the single-series factors (SECTOR_* rows are added per sector at build).
_DEFINITIONS = {
    "IVOL": ("NEPTUNE", "price",
             "low-minus-high 60d idiosyncratic vol, equal-weight 30% tails, lagged",
             "Low-volatility anomaly: long low-idio-vol names, short high."),
    "BAB": ("NEPTUNE", "price",
            "low-minus-high beta, each leg scaled to ~unit beta, equal-weight 30% tails",
            "Betting-against-beta (Frazzini-Pedersen): long low-beta, short high-beta."),
    "AMIHUD": ("NEPTUNE", "price",
               "illiquid-minus-liquid Amihud (|ret|/$vol, 60d), equal-weight 30% tails",
               "Amihud (2002) illiquidity premium: long illiquid, short liquid."),
}


def _beta_matrix(session: Session, ids: list[int], dates, benchmark: str) -> np.ndarray:
    """Point-in-time materialized betas as an ``(N, T)`` matrix aligned to ``dates``; NaN where a
    name has no stored beta on a date."""
    by_iid: dict[int, dict] = {iid: {} for iid in ids}
    rows = session.execute(
        select(Beta.instrument_id, Beta.ts, Beta.beta).where(
            Beta.benchmark == benchmark, Beta.instrument_id.in_(ids)
        )
    ).all()
    for iid, ts, beta in rows:
        by_iid[iid][ts] = beta
    return np.array(
        [[by_iid[iid].get(d, np.nan) for d in dates] for iid in ids], dtype=float
    )


def _persist(session: Session, factor: str, dates, series: np.ndarray) -> int:
    """Replace the ``neptune``-sourced rows for one factor with the finite entries of ``series``."""
    session.execute(
        delete(FactorReturn).where(
            FactorReturn.factor == factor, FactorReturn.source == NEPTUNE_SOURCE
        )
    )
    rows = [
        {"factor": factor, "ts": d, "ret": float(v), "source": NEPTUNE_SOURCE}
        for d, v in zip(dates, series)
        if np.isfinite(v)
    ]
    if rows:
        session.bulk_insert_mappings(FactorReturn, rows)
    return len(rows)


def _register(session: Session, factor: str, family: str, kind: str, method: str, desc: str):
    session.merge(FactorDefinition(
        factor=factor, family=family, kind=kind, source=NEPTUNE_SOURCE,
        method=method, description=desc,
    ))


def build_neptune_factors(
    session: Session,
    benchmark: str = "SPY",
    window: int = DEFAULT_WINDOW,
    frac: float = DEFAULT_FRAC,
    tickers: list[str] | None = None,
) -> dict[str, int]:
    """Build and persist IVOL, BAB, AMIHUD, and per-sector SECTOR_* factor-return series.

    Returns ``{factor: rows_written}`` (a factor absent from the map was skipped — e.g. BAB with
    no materialized betas, or a sector with no members)."""
    md = DbMarketData(session, benchmark=benchmark)
    dates = md.return_dates()
    names = tickers if tickers is not None else md.available_tickers()
    names = [t for t in names if t != benchmark]
    if len(dates) <= window or not names:
        return {}

    market = md.market_returns()
    id_by_ticker = dict(session.execute(
        select(Security.ticker, Security.instrument_id).where(Security.ticker.in_(names))
    ).all())
    names = [t for t in names if t in id_by_ticker]
    R = np.vstack([md.aligned_returns(t) for t in names])  # (N, T) on the return-date axis

    written: dict[str, int] = {}

    # IVOL — low-minus-high idiosyncratic volatility.
    ivol = np.vstack([rolling_idio_vol(R[i], market, window) for i in range(len(names))])
    series = long_short_returns(R, ivol, frac=frac, low_minus_high=True)
    written["IVOL"] = _persist(session, "IVOL", dates, series)
    _register(session, "IVOL", *_DEFINITIONS["IVOL"])

    # AMIHUD — illiquid-minus-liquid (high Amihud is illiquid → high_minus_low).
    dv = np.vstack([md.aligned_dollar_volume(t) for t in names])
    amihud = np.vstack([rolling_amihud(R[i], dv[i], window) for i in range(len(names))])
    series = long_short_returns(R, amihud, frac=frac, low_minus_high=False)
    written["AMIHUD"] = _persist(session, "AMIHUD", dates, series)
    _register(session, "AMIHUD", *_DEFINITIONS["AMIHUD"])

    # BAB — betting-against-beta, ranked on the materialized point-in-time betas.
    betas = _beta_matrix(session, [id_by_ticker[t] for t in names], dates, benchmark)
    if np.isfinite(betas).any():
        series = long_short_returns(R, betas, frac=frac, low_minus_high=True, betas=betas)
        written["BAB"] = _persist(session, "BAB", dates, series)
        _register(session, "BAB", *_DEFINITIONS["BAB"])

    # SECTOR_* — equal-weight sector basket minus market (one series per populated sector).
    returns_by_ticker = {t: R[i] for i, t in enumerate(names)}
    sector_by_ticker = {t: md.sector(t) for t in names}
    for sec, excess in sector_excess_returns(returns_by_ticker, sector_by_ticker, market).items():
        factor = f"SECTOR_{sec.replace(' ', '_')}"
        n = _persist(session, factor, dates, excess)
        if n:
            written[factor] = n
            _register(
                session, factor, "SECTOR", "price",
                "equal-weight sector basket minus market (value-weighting pending fundamentals)",
                f"{sec.title()} sector excess return over the market.",
            )
    session.commit()
    return written
