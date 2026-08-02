"""Always-on factor-panel refresh: the shared ingest+rebuild job, the persisted interval,
the settings endpoints, and graceful degradation when APScheduler isn't installed."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from neptune.api.main import app
from neptune.db.base import Base, SecuritiesBase, engine, securities_engine
from neptune.scheduling import factor_scheduler
from neptune.scheduling.factors import refresh_factor_panel
from neptune.securities.factor_providers import FactorObservation, RecordedFactorProvider
from neptune.securities.models import FactorReturn, Price, Security
from neptune.settings_store.app_settings import AppSettingsService


def _seed_prices(session, ticker, iid, n_days, base=100.0):
    session.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock"))
    start = date.today() - timedelta(days=n_days - 1)
    for i in range(n_days):
        session.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                          close=base, adj_close=base, source="yfinance"))
    session.commit()


# --- the refresh job: manual endpoint and scheduled job share one implementation -----

def test_refresh_factor_panel_ingests_and_rebuilds_loadings(securities_session):
    _seed_prices(securities_session, "SPY", 1, 20)
    last_return_date = date.today() - timedelta(days=1)
    obs = [
        FactorObservation(factor=f, ts=last_return_date, ret=0.001)
        for f in ("SMB", "HML", "RMW", "CMA", "MOM")
    ]
    provider = RecordedFactorProvider(obs, source="ken_french")
    result = refresh_factor_panel(
        securities_session, provider,
        last_return_date - timedelta(days=1), last_return_date,
        benchmark="SPY",
    )
    assert result["counts"]  # something was ingested
    rows = securities_session.query(FactorReturn).count()
    assert rows == 5  # one row per style factor


# --- persisted interval -----------------------------------------------------------

def test_factor_interval_defaults_then_persists(session):
    svc = AppSettingsService(session)
    assert svc.get_factor_refresh_minutes() == 1440  # config default (once/day)
    assert svc.set_factor_refresh_minutes(60) == 60
    assert AppSettingsService(session).get_factor_refresh_minutes() == 60
    assert svc.set_factor_refresh_minutes(-5) == 0  # clamped (0 = off)


# --- settings endpoints -----------------------------------------------------------

def test_factor_refresh_endpoints_roundtrip():
    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as client:
        assert client.get("/settings/factor-refresh").json() == {"minutes": 1440}
        assert client.put("/settings/factor-refresh", json={"minutes": 60}).json() == {"minutes": 60}
        assert client.get("/settings/factor-refresh").json() == {"minutes": 60}
        assert client.put("/settings/factor-refresh", json={"minutes": 99999}).status_code == 422


# --- graceful degradation ---------------------------------------------------------

def test_factor_scheduler_disabled_without_apscheduler():
    # APScheduler isn't installed in CI → start returns None, reschedule is a safe no-op.
    assert factor_scheduler.start_scheduler() is None
    factor_scheduler.reschedule(5)  # must not raise
