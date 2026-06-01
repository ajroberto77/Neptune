"""DbMarketData: returns/marks read from stored prices, aligned to the benchmark index."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from neptune.data.db_market import DbMarketData, TickerNotFound
from neptune.quant.beta import raw_beta_ewma_dimson
from neptune.securities.models import Price, Security


def _dates(n: int, start=date(2026, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _seed(session, ticker, instrument_id, dates, closes, adj=None, source="yfinance"):
    session.add(Security(instrument_id=instrument_id, ticker=ticker, security_type="Common Stock"))
    adj = adj if adj is not None else closes
    for ts, c, a in zip(dates, closes, adj):
        session.add(Price(instrument_id=instrument_id, ts=ts, close=c, adj_close=a, source=source))
    session.commit()


def _compound(base, returns):
    """Price path from a base price and a returns array."""
    p = [base]
    for r in returns:
        p.append(p[-1] * (1 + r))
    return p


def test_market_returns_are_pct_change_of_benchmark(securities_session):
    ds = _dates(4)
    _seed(securities_session, "SPY", 1, ds, [100.0, 101.0, 100.0, 102.0])
    md = DbMarketData(securities_session, benchmark="SPY")
    expected = np.array([0.01, -1 / 101, 2 / 100])
    np.testing.assert_allclose(md.market_returns(), expected, rtol=1e-9)
    assert md.factor_returns() == {"MKT": md.market_returns()}  # MKT-only until Ken French


def test_ticker_returns_aligned_and_equal_length(securities_session):
    ds = _dates(4)
    _seed(securities_session, "SPY", 1, ds, [100.0, 101.0, 102.0, 103.0])
    _seed(securities_session, "AAA", 2, ds, [50.0, 51.0, 50.0, 52.0])
    md = DbMarketData(securities_session, benchmark="SPY")
    # Same length as the market series — the regression contract.
    assert len(md.ticker_returns("AAA")) == len(md.market_returns())


def test_forward_fill_for_a_missing_session(securities_session):
    ds = _dates(4)
    _seed(securities_session, "SPY", 1, ds, [100.0, 101.0, 102.0, 103.0])
    # AAA is missing the day-3 (index 2) session entirely.
    _seed(securities_session, "AAA", 2, [ds[0], ds[1], ds[3]], [50.0, 55.0, 60.0])
    md = DbMarketData(securities_session, benchmark="SPY")
    r = md.ticker_returns("AAA")
    assert len(r) == 3
    # Day index 2 had no price → forward-filled → 0 return that day.
    assert r[1] == pytest.approx(0.0)


def test_marks_use_raw_close_not_adjusted(securities_session):
    ds = _dates(3)
    # Adjusted series differs from raw close; marks must use raw close.
    _seed(securities_session, "AAA", 2, ds, [48.0, 50.0, 51.0], adj=[24.0, 25.0, 51.0])
    _seed(securities_session, "SPY", 1, ds, [100.0, 101.0, 102.0])
    md = DbMarketData(securities_session, benchmark="SPY")
    assert md.current_price("AAA") == 51.0
    assert md.prev_close("AAA") == 50.0


def test_recovers_beta_from_stored_prices(securities_session):
    rng = np.random.default_rng(0)
    rm = rng.normal(0.0, 0.01, 80)
    ds = _dates(len(rm) + 1)
    _seed(securities_session, "SPY", 1, ds, _compound(100.0, rm))
    # AAA return = 1.2 * market return, exactly → beta should recover ~1.2.
    _seed(securities_session, "AAA", 2, ds, _compound(100.0, 1.2 * rm))
    md = DbMarketData(securities_session, benchmark="SPY")
    raw = raw_beta_ewma_dimson(md.ticker_returns("AAA"), md.market_returns())
    assert raw.beta_raw == pytest.approx(1.2, abs=0.03)


def test_lookback_caps_the_window(securities_session):
    ds = _dates(10)
    _seed(securities_session, "SPY", 1, ds, [100.0 + i for i in range(10)])
    md = DbMarketData(securities_session, benchmark="SPY", lookback=4)
    assert len(md.market_returns()) == 4  # last 4 returns (5 prices)


def test_unknown_ticker_raises(securities_session):
    _seed(securities_session, "SPY", 1, _dates(3), [100.0, 101.0, 102.0])
    md = DbMarketData(securities_session, benchmark="SPY")
    with pytest.raises(TickerNotFound):
        md.ticker_returns("NOPE")


def test_benchmark_needs_two_prices(securities_session):
    _seed(securities_session, "SPY", 1, _dates(1), [100.0])
    with pytest.raises(TickerNotFound):
        DbMarketData(securities_session, benchmark="SPY")


def test_source_filter_selects_one_feed(securities_session):
    ds = _dates(3)
    _seed(securities_session, "SPY", 1, ds, [100.0, 101.0, 102.0], source="yfinance")
    # Add a second source for the same security/days with different prices.
    for ts, c in zip(ds, [200.0, 202.0, 204.0]):
        securities_session.add(
            Price(instrument_id=1, ts=ts, close=c, adj_close=c, source="bloomberg")
        )
    securities_session.commit()
    md = DbMarketData(securities_session, benchmark="SPY", source="bloomberg")
    assert md.current_price("SPY") == 204.0
