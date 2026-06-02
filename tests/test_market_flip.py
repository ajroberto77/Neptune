"""The DbMarketData flip: the API uses real prices when the benchmark + every portfolio
ticker are present, else falls back to synthetic; benchmarks (SPY) are ingestable."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from neptune.api.main import MARKET_DATA, market_data_for
from neptune.data.db_market import DbMarketData
from neptune.data.market import SyntheticMarketData
from neptune.domain.models import Side, ShortType
from neptune.positions.service import PositionService
from neptune.securities.ingest import ingest_ticker
from neptune.securities.models import Price, Security
from neptune.securities.providers import PriceBar, RecordedProvider, SecurityHistory

START = date(2025, 1, 1)


def _seed_prices(securities_session, ticker, iid, returns, base=100.0):
    securities_session.add(
        Security(instrument_id=iid, ticker=ticker, security_type="Common Stock")
    )
    prices = [base]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    for i, px in enumerate(prices):
        d = date.fromordinal(START.toordinal() + i)
        securities_session.add(
            Price(instrument_id=iid, ts=d, close=px, adj_close=px, source="yfinance")
        )
    securities_session.commit()


def test_uses_db_market_data_when_all_present(session, securities_session):
    synth = SyntheticMarketData()
    _seed_prices(securities_session, "SPY", 1, synth.market_returns())  # benchmark
    _seed_prices(securities_session, "AAA", 2, synth.ticker_returns("AAA"))
    svc = PositionService(session)
    svc.create_portfolio("X", "X")
    svc.record_trade("X", "AAA", Side.LONG, ShortType.NA, 10, 100.0, date(2025, 1, 2))
    portfolio = svc.get_portfolio("X")

    with market_data_for(session, portfolio) as md:
        assert isinstance(md, DbMarketData)  # real prices drive the engine


def test_stays_on_real_data_when_a_name_lacks_prices(session, securities_session):
    # Benchmark present, but one name has no stored prices. The book STAYS on the real source
    # (no synthetic contamination of the whole book); the unpriced name is handled gracefully.
    from neptune.risk import analytics

    _seed_prices(securities_session, "SPY", 1, SyntheticMarketData().market_returns())
    svc = PositionService(session)
    svc.create_portfolio("X", "X")
    svc.record_trade("X", "ZZZ", Side.LONG, ShortType.NA, 10, 100.0, date(2025, 1, 2))
    portfolio = svc.get_portfolio("X")

    with market_data_for(session, portfolio) as md:
        assert md is not MARKET_DATA          # real DbMarketData, NOT synthetic
        assert isinstance(md, DbMarketData)
        metrics = analytics.compute_metrics(portfolio, md)
        assert metrics["ZZZ"].beta_method == "insufficient_data"  # graceful, no crash


def test_falls_back_when_benchmark_absent(session, securities_session):
    # AAA present but no SPY → no benchmark → synthetic.
    _seed_prices(securities_session, "AAA", 2, SyntheticMarketData().ticker_returns("AAA"))
    svc = PositionService(session)
    svc.create_portfolio("X", "X")
    svc.record_trade("X", "AAA", Side.LONG, ShortType.NA, 10, 100.0, date(2025, 1, 2))
    portfolio = svc.get_portfolio("X")

    with market_data_for(session, portfolio) as md:
        assert md is MARKET_DATA


def test_benchmark_is_ingestable_outside_the_universe(securities_session):
    # SPY (an ETF) isn't in the projection; create_if_missing lets it be ingested with a
    # stable NEGATIVE instrument_id (never collides with universe surrogates).
    history = SecurityHistory(
        prices=[
            PriceBar(ts=date(2025, 1, 2), close=400.0, adj_close=400.0),
            PriceBar(ts=date(2025, 1, 3), close=401.0, adj_close=401.0),
        ]
    )
    provider = RecordedProvider({"SPY": history})
    res = ingest_ticker(
        securities_session, provider, "SPY", START, date(2025, 1, 31), create_if_missing=True
    )
    assert res.prices == 2
    sec = securities_session.scalar(select(Security).where(Security.ticker == "SPY"))
    assert sec is not None and sec.instrument_id < 0  # negative = non-universe benchmark


def test_ingest_unknown_still_raises_without_create_flag(securities_session):
    import pytest

    provider = RecordedProvider({})
    with pytest.raises(LookupError):
        ingest_ticker(securities_session, provider, "NOPE", START, date(2025, 1, 31))


def test_refresh_prices_reports_feed_unavailable_offline():
    # /refresh-prices needs yfinance (absent here) → clean 503, not a 500.
    from fastapi.testclient import TestClient
    from neptune.api.main import app
    from neptune.db.base import Base, SecuritiesBase, engine, securities_engine

    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as client:
        r = client.post("/portfolios/IRIDIUM-CORE/refresh-prices")
        assert r.status_code == 503
        assert "yfinance" in r.json()["detail"]
