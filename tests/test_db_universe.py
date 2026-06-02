"""The REAL shortable universe: db_universe builds candidates from backfilled names with
pipeline betas + stored sectors, skipping insufficient-history names; the sector column is
added to an existing securities DB by the lightweight migration."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
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
    # Benchmark with only 2 bars (1 return) — too short for the OLS beta regression (needs 3).
    _seed(securities_session, "SPY", 1, [0.01])
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


def test_short_history_name_regressed_over_its_real_window_not_backfilled_zeros(
    securities_session,
):
    """A name ingested LATER than the benchmark (short real history) must be regressed only over
    its own real bars. Back-filling the long leading gap with a constant first price would
    fabricate hundreds of zero-return days, collapsing the beta toward 0 (the WEN bug). Here a
    name with true beta 1.30 over a short tail must NOT read ~0."""
    rng = np.random.default_rng(0)
    n = 320
    mkt = rng.normal(0.0, 0.01, n)  # SPY daily returns
    spy_px = [100.0]
    for r in mkt:
        spy_px.append(spy_px[-1] * (1 + r))
    start = date.today() - timedelta(days=len(spy_px) + 1)
    securities_session.add(Security(instrument_id=1, ticker="SPY", security_type="Common Stock"))
    for i, p in enumerate(spy_px):
        securities_session.add(Price(instrument_id=1, ts=start + timedelta(days=i),
                                     close=p, adj_close=p, source="yfinance"))

    # "NEW" has real bars only for the most recent 90 sessions, with beta 1.30 to SPY there.
    real = 90
    securities_session.add(Security(instrument_id=2, ticker="NEW", security_type="Common Stock"))
    new_px = [40.0]
    for r in mkt[-real:]:
        new_px.append(new_px[-1] * (1 + 1.30 * r))
    # price[k] sits on the same date as spy_px[len(spy_px)-1-real+k]
    base_i = len(spy_px) - 1 - real
    for k, p in enumerate(new_px):
        securities_session.add(Price(instrument_id=2, ts=start + timedelta(days=base_i + k),
                                     close=p, adj_close=p, source="yfinance"))
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    from neptune.quant.beta import raw_beta
    rb = raw_beta(md.ticker_returns("NEW"), md.market_returns())
    assert rb.n_obs == real  # regressed over the real window only, not the full benchmark span
    assert rb.beta_raw == pytest.approx(1.30, abs=0.25)  # recovered, NOT diluted toward 0


def test_thin_real_window_flagged_insufficient_not_a_flaky_beta(securities_session):
    """A name whose real history is below MIN_BETA_OBS must be reported insufficient_data (beta
    held at the 1.0 prior) and excluded from the shortable universe — not quoted as a beta fit
    on a handful of days."""
    from neptune.risk.analytics import MIN_BETA_OBS, compute_metrics, db_universe
    from neptune.domain.models import Portfolio, Position, Side

    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())  # full benchmark history
    thin = list(synth.ticker_returns("AAA"))[: MIN_BETA_OBS - 20]  # below the floor
    _seed(securities_session, "THIN", 2, thin, sector="Technology")
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    metrics = compute_metrics(Portfolio(id="P", name="t",
                                        positions=[Position("THIN", Side.LONG, 1_000)]), md)
    assert metrics["THIN"].beta_method == "insufficient_data"
    assert metrics["THIN"].beta == 1.0
    assert [c.ticker for c in db_universe(md)] == []  # not a usable hedge candidate


def test_beta_uses_completed_closes_not_todays_live_bar(securities_session):
    """A beta as-of today is computed from CLOSES strictly before today, so an intraday price
    refresh (which rewrites today's bar) never moves it — only marks do."""
    from sqlalchemy import select as _select
    n = 12
    start = date.today() - timedelta(days=n - 1)  # bars on the last n days; last == today
    for iid, tkr, base in [(1, "SPY", 100.0), (2, "AAA", 50.0)]:
        securities_session.add(
            Security(instrument_id=iid, ticker=tkr, security_type="Common Stock")
        )
        for i in range(n):
            px = base + i
            securities_session.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                                         close=px, adj_close=px, source="yfinance"))
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    # Returns exclude today's bar: n bars → n-1 completed → n-2 returns.
    assert len(md.market_returns()) == n - 2
    assert md.current_price("SPY") == 100.0 + (n - 1)  # marks DO use today's bar

    # Rewrite TODAY's SPY bar (an intraday refresh) → returns unchanged, mark moves.
    today_bar = securities_session.scalar(
        _select(Price).where(Price.instrument_id == 1, Price.ts == start + timedelta(days=n - 1))
    )
    today_bar.close = 999.0
    today_bar.adj_close = 999.0
    securities_session.commit()
    md2 = DbMarketData(securities_session, benchmark="SPY")
    np.testing.assert_array_equal(md.market_returns(), md2.market_returns())  # beta stable
    assert md2.current_price("SPY") == 999.0  # but the mark reflects the live price
