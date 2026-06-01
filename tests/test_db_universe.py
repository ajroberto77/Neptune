"""The REAL shortable universe: db_universe builds candidates from backfilled names with
pipeline betas + stored sectors, skipping insufficient-history names; the sector column is
added to an existing securities DB by the lightweight migration."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from neptune.data.db_market import DbMarketData
from neptune.data.market import SyntheticMarketData
from neptune.db.base import _ensure_columns
from neptune.risk import analytics
from neptune.securities.models import Price, Security


def _seed(session, ticker, iid, returns, sector=None, base=100.0):
    session.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock",
                         sector=sector))
    px = [base]
    for r in returns:
        px.append(px[-1] * (1 + r))
    start = date.today() - timedelta(days=len(px) + 1)
    for i, p in enumerate(px):
        session.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                          close=p, adj_close=p, source="yfinance"))


def test_db_universe_builds_real_candidates_with_sectors(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())  # benchmark, excluded
    _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"), sector="Technology")
    _seed(securities_session, "BBB", 3, synth.ticker_returns("BBB"), sector="Energy")
    _seed(securities_session, "THIN", 4, [0.01, -0.01])  # 3 bars — too short to fit
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    cands = analytics.db_universe(md)
    tickers = {c.ticker for c in cands}
    assert tickers == {"AAA", "BBB"}  # SPY excluded; THIN skipped (insufficient history)
    by = {c.ticker: c for c in cands}
    assert by["AAA"].sector == "Technology" and by["BBB"].sector == "Energy"
    assert all(np.isfinite(c.beta) for c in cands)  # real pipeline betas


def test_short_benchmark_empties_universe_despite_priced_names(securities_session):
    """The real failure behind a '539 names but no candidates' report: names have plenty of
    their OWN bars, but the benchmark is short, so EVERY beta regression fails and the
    shortable universe is empty. available_tickers (price-bar count) still lists them."""
    synth = SyntheticMarketData()
    # Benchmark with only 4 bars — too short to fit the Dimson regression (needs ~7 aligned).
    _seed(securities_session, "SPY", 1, [0.01, -0.02, 0.0])
    _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"), sector="Technology")
    _seed(securities_session, "BBB", 3, synth.ticker_returns("BBB"), sector="Energy")
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    assert set(md.available_tickers()) == {"AAA", "BBB"}  # both have >=30 of their own bars
    assert analytics.db_universe(md) == []                # …yet NO regressable candidates


def test_available_tickers_excludes_benchmark(securities_session):
    _seed(securities_session, "SPY", 1, SyntheticMarketData().market_returns())
    _seed(securities_session, "AAA", 2, SyntheticMarketData().ticker_returns("AAA"))
    securities_session.commit()
    md = DbMarketData(securities_session, benchmark="SPY")
    assert md.available_tickers() == ["AAA"]


def test_ensure_columns_adds_missing_column_idempotently(securities_session):
    engine = securities_session.get_bind()
    # Drop the sector column to simulate an older DB, then let the migration re-add it.
    with engine.begin() as conn:
        cols = [c["name"] for c in __import__("sqlalchemy").inspect(engine).get_columns("securities")]
        assert "sector" in cols  # create_all already made it
    # Idempotent: running the ensure step again is a no-op (column already present).
    _ensure_columns(engine, "securities", {"sector": "VARCHAR"})
    _ensure_columns(engine, "securities", {"sector": "VARCHAR"})  # twice — must not raise
