"""Always-on price refresh: the tracked-ticker job, the persisted interval, the settings
endpoints, and graceful degradation when APScheduler isn't installed."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from neptune.api.main import app
from neptune.db.base import Base, SecuritiesBase, engine, securities_engine
from neptune.domain.models import Side, ShortType
from neptune.positions.service import PositionService
from neptune.scheduling import scheduler as price_scheduler
from neptune.scheduling.prices import refresh_tracked_prices, tracked_tickers
from neptune.securities.models import Price, Security
from neptune.securities.providers import PriceBar, RecordedProvider, SecurityHistory
from neptune.settings_store.app_settings import AppSettingsService


def _history(*closes):
    # Bars dated within the refresh window (last few days) so the date filter keeps them.
    base = date.today() - timedelta(days=len(closes))
    bars = [PriceBar(ts=base + timedelta(days=i), close=c, adj_close=c)
            for i, c in enumerate(closes)]
    return SecurityHistory(prices=bars)


class _RaisingProvider:
    """Returns a recorded history per ticker; raises for any ticker not provided (so the
    job's per-ticker error collection is exercised)."""

    source = "test"

    def __init__(self, histories):
        self._h = histories

    def fetch(self, ticker, start, end):
        if ticker not in self._h:
            raise ValueError(f"no feed for {ticker}")
        return SecurityHistory(prices=[b for b in self._h[ticker].prices if start <= b.ts <= end])


# --- the refresh job --------------------------------------------------------------

def test_tracked_tickers_unions_positions_and_benchmark(session):
    svc = PositionService(session)
    svc.create_portfolio("P", "P")
    svc.record_trade("P", "PZZA", Side.LONG, ShortType.NA, 10, 33.0, date(2025, 1, 2))
    svc.record_trade("P", "AAPL", Side.LONG, ShortType.NA, 5, 200.0, date(2025, 1, 2))
    assert tracked_tickers(session, "SPY") == ["AAPL", "PZZA", "SPY"]


def test_refresh_tracked_prices_ingests_book_plus_benchmark(session, securities_session):
    svc = PositionService(session)
    svc.create_portfolio("P", "P")
    svc.record_trade("P", "PZZA", Side.LONG, ShortType.NA, 10, 33.0, date(2025, 1, 2))
    provider = RecordedProvider(
        {"PZZA": _history(33.0, 33.85), "SPY": _history(578.0, 580.0)}
    )
    result = refresh_tracked_prices(session, benchmark="SPY", provider=provider, days=30)
    assert result["tickers"] == 2 and result["updated_bars"] == 4 and result["errors"] == []
    # The benchmark was created on the fly (negative id) and its prices landed.
    spy = securities_session.query(Security).filter(Security.ticker == "SPY").one()
    assert spy.instrument_id < 0
    assert securities_session.query(Price).filter(Price.instrument_id == spy.instrument_id).count() == 2


def test_refresh_collects_per_ticker_errors(session, securities_session):
    svc = PositionService(session)
    svc.create_portfolio("P", "P")
    svc.record_trade("P", "GOOD", Side.LONG, ShortType.NA, 1, 10.0, date(2025, 1, 2))
    svc.record_trade("P", "BAD", Side.LONG, ShortType.NA, 1, 10.0, date(2025, 1, 2))
    provider = _RaisingProvider({"GOOD": _history(10.0, 11.0)})  # BAD raises
    result = refresh_tracked_prices(session, benchmark=None, provider=provider)
    assert "BAD" in result["errors"] and result["updated_bars"] == 2


# --- persisted interval -----------------------------------------------------------

def test_interval_defaults_then_persists(session):
    svc = AppSettingsService(session)
    assert svc.get_price_refresh_minutes() == 10  # config default
    assert svc.set_price_refresh_minutes(25) == 25
    assert AppSettingsService(session).get_price_refresh_minutes() == 25
    assert svc.set_price_refresh_minutes(-5) == 0  # clamped (0 = off)


# --- settings endpoints -----------------------------------------------------------

def test_price_refresh_endpoints_roundtrip():
    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as client:
        assert client.get("/settings/price-refresh").json() == {"minutes": 10}
        assert client.put("/settings/price-refresh", json={"minutes": 15}).json() == {"minutes": 15}
        assert client.get("/settings/price-refresh").json() == {"minutes": 15}
        assert client.put("/settings/price-refresh", json={"minutes": 9999}).status_code == 422


# --- graceful degradation ---------------------------------------------------------

def test_scheduler_disabled_without_apscheduler():
    # APScheduler isn't installed in CI → start returns None, reschedule is a safe no-op.
    assert price_scheduler.start_scheduler() is None
    price_scheduler.reschedule(5)  # must not raise
