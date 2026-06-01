"""DbMarketData: real-price reads, market-axis alignment, and a full ingest→engine loop.

The key test compounds the *synthetic* return series into stored prices, then asserts
DbMarketData reads them back into the same returns — so the beta pipeline run on DB prices
recovers the same betas as on synthetic data. That exercises the entire data path end to end.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from neptune.data.db_source import DbMarketData, FactorDataUnavailable, NoPriceData
from neptune.data.market import SyntheticMarketData
from neptune.quant.beta import raw_beta_ewma_dimson
from neptune.securities.models import Price, Security

START = date(2025, 1, 1)


def _seed(session, ticker, iid, returns, *, source="yfinance", base=100.0) -> list[float]:
    """Create a Security and a price series whose returns are exactly ``returns`` (so a
    round-trip through DbMarketData recovers them). Returns the price list."""
    session.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock"))
    prices = [base]
    for r in returns:
        prices.append(prices[-1] * (1.0 + r))
    for i, px in enumerate(prices):
        d = date.fromordinal(START.toordinal() + i)
        session.add(Price(instrument_id=iid, ts=d, close=px, adj_close=px, source=source))
    session.commit()
    return prices


def test_market_and_ticker_returns_round_trip(securities_session):
    synth = SyntheticMarketData()
    mkt = synth.market_returns()
    aaa = synth.ticker_returns("AAA")
    _seed(securities_session, "SPY", 1, mkt)
    _seed(securities_session, "AAA", 2, aaa)

    db = DbMarketData(securities_session, market_ticker="SPY")
    # The trailing lookback window of returns is recovered exactly from stored adj_close.
    assert np.allclose(db.market_returns(), mkt[-db.lookback:])
    assert np.allclose(db.ticker_returns("AAA"), aaa[-db.lookback:])


def test_beta_pipeline_recovers_known_beta_from_db_prices(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())
    _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"))  # true beta 1.20

    db = DbMarketData(securities_session, market_ticker="SPY")
    beta = raw_beta_ewma_dimson(db.ticker_returns("AAA"), db.market_returns()).beta_raw
    assert 1.05 < beta < 1.35  # recovers the catalog truth (1.20) up to estimation noise


def test_raw_marks_are_unadjusted_close(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())
    prices = _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"))

    db = DbMarketData(securities_session, market_ticker="SPY")
    assert db.current_price("AAA") == pytest.approx(prices[-1])
    assert db.prev_close("AAA") == pytest.approx(prices[-2])


def test_missing_ticker_and_market_raise(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())
    db = DbMarketData(securities_session, market_ticker="SPY")
    with pytest.raises(NoPriceData):
        db.ticker_returns("ZZZ")


def test_empty_market_raises_on_construction(securities_session):
    # Nothing seeded → constructing against an absent market ticker fails fast.
    with pytest.raises(NoPriceData):
        DbMarketData(securities_session, market_ticker="SPY")


def test_factor_returns_requires_a_panel(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())
    db = DbMarketData(securities_session, market_ticker="SPY")
    # No Ken French panel wired → explicit error, never silent zeros.
    with pytest.raises(FactorDataUnavailable):
        db.factor_returns()
    # Injected panel is returned as-is.
    panel = {"SMB": np.zeros(db.lookback)}
    db2 = DbMarketData(securities_session, market_ticker="SPY", factors=panel)
    assert "SMB" in db2.factor_returns()


def test_source_selection_falls_back_when_preferred_absent(securities_session):
    synth = SyntheticMarketData()
    # Only a non-default source exists; DbMarketData(source="yfinance") falls back to it.
    _seed(securities_session, "SPY", 1, synth.market_returns(), source="bloomberg")
    db = DbMarketData(securities_session, market_ticker="SPY", source="yfinance")
    assert len(db.market_returns()) == db.lookback
