"""Sector classification source: the app setting, the endpoints, and DbMarketData.sector()
resolving through whichever scheme is active. Yahoo is the default and (when active) reads
exactly the same Security.sector column as before this feature existed; SIC/KENFRENCH_12
read security_classifications, projected from cato_securities' entity_classifications by
neptune.universe.sync_universe_projection.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from neptune.api.main import app
from neptune.data.db_market import DbMarketData
from neptune.db.base import Base, SecuritiesBase, engine, securities_engine
from neptune.securities.models import Price, Security, SecurityClassification
from neptune.settings_store.app_settings import (
    DEFAULT_SECTOR_SOURCE,
    SECTOR_SOURCES,
    AppSettingsService,
)


# --- AppSettingsService -------------------------------------------------------------

def test_sector_source_defaults_then_persists(session):
    svc = AppSettingsService(session)
    assert svc.get_sector_source() == "YAHOO" == DEFAULT_SECTOR_SOURCE
    assert svc.set_sector_source("KENFRENCH_12") == "KENFRENCH_12"
    assert AppSettingsService(session).get_sector_source() == "KENFRENCH_12"


def test_sector_source_rejects_unknown_scheme(session):
    svc = AppSettingsService(session)
    try:
        svc.set_sector_source("GICS")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "GICS" in str(exc)
    # The rejected write never landed.
    assert svc.get_sector_source() == "YAHOO"


def test_sector_source_falls_back_to_default_if_stored_value_becomes_invalid(session):
    """A scheme that was valid when stored but isn't recognized anymore (e.g. a config
    rollback) degrades to the default rather than surfacing a stale/unknown value."""
    from neptune.db.models import AppSettingORM

    session.add(AppSettingORM(key="sector_source", value="SOME_RETIRED_SCHEME"))
    session.commit()
    assert AppSettingsService(session).get_sector_source() == DEFAULT_SECTOR_SOURCE


# --- API endpoints -------------------------------------------------------------------

def test_sector_source_endpoints_roundtrip():
    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as client:
        resp = client.get("/settings/sector-source").json()
        assert resp == {"scheme": "YAHOO", "available": list(SECTOR_SOURCES)}

        assert client.put("/settings/sector-source", json={"scheme": "SIC"}).json() == {
            "scheme": "SIC"
        }
        assert client.get("/settings/sector-source").json()["scheme"] == "SIC"

        bad = client.put("/settings/sector-source", json={"scheme": "NOT_A_SCHEME"})
        assert bad.status_code == 400


# --- DbMarketData.sector() resolution -------------------------------------------------

def _seed(securities_session, *, sector="Technology"):
    sec = Security(instrument_id=1, ticker="AAPL", sector=sector)
    securities_session.add(sec)
    securities_session.add(SecurityClassification(
        instrument_id=1, scheme="SIC", level="code", code="3571", description="Computers",
    ))
    securities_session.add(SecurityClassification(
        instrument_id=1, scheme="KENFRENCH_12", level="group", code="BusEq",
        description="Business Equipment",
    ))
    # DbMarketData's constructor needs >=2 benchmark (SPY) bars to build its return-date
    # index, regardless of what .sector() is being tested here.
    bench = Security(instrument_id=-1, ticker="SPY", sector=None)
    securities_session.add(bench)
    base = date.today() - timedelta(days=5)
    for i, px in enumerate((500.0, 501.0, 502.0)):
        securities_session.add(Price(instrument_id=-1, ts=base + timedelta(days=i), close=px, adj_close=px))
    securities_session.commit()


def test_sector_yahoo_is_the_default_and_unchanged_from_before_this_feature(securities_session):
    _seed(securities_session)
    md = DbMarketData(securities_session)  # sector_source omitted -> default YAHOO
    assert md.sector("AAPL") == "Technology"


def test_sector_resolves_through_sic_when_selected(securities_session):
    _seed(securities_session)
    md = DbMarketData(securities_session, sector_source="SIC")
    assert md.sector("AAPL") == "3571"


def test_sector_resolves_through_kenfrench12_when_selected(securities_session):
    _seed(securities_session)
    md = DbMarketData(securities_session, sector_source="KENFRENCH_12")
    assert md.sector("AAPL") == "BusEq"


def test_sector_none_when_ticker_has_no_classification_row_for_the_active_scheme(securities_session):
    # AAPL has Yahoo + SIC + KENFRENCH_12 seeded, but a second ticker with only Yahoo:
    _seed(securities_session)
    securities_session.add(Security(instrument_id=2, ticker="ZZZ", sector="Industrials"))
    securities_session.commit()

    md_sic = DbMarketData(securities_session, sector_source="SIC")
    assert md_sic.sector("AAPL") == "3571"
    assert md_sic.sector("ZZZ") is None  # no SIC row for ZZZ -> uncapped, not an error

    md_yahoo = DbMarketData(securities_session, sector_source="YAHOO")
    assert md_yahoo.sector("ZZZ") == "Industrials"  # Yahoo path unaffected either way
