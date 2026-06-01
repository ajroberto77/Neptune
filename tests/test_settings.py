"""Settings: configurable DB connections + universe-sync endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from neptune.api.main import app
from neptune.db.base import Base, SecuritiesBase, engine, securities_engine
from neptune.settings_store import ConnectionConfig, ConnectionRole


@pytest.fixture()
def client():
    # Reset both in-memory schemas so persisted db_connections rows don't leak between
    # tests (the app shares a process-global in-memory DB via StaticPool). Lifespan
    # re-creates + re-seeds the golden book on TestClient construction.
    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as c:
        yield c


def test_url_encodes_special_characters():
    cfg = ConnectionConfig(
        ConnectionRole.UNIVERSE, "h", 5434, "cato_securities", "ro",
        password="p@ss:w/rd", sslmode="require",
    )
    # Password special chars are percent-encoded; sslmode appended.
    assert "p%40ss%3Aw%2Frd" in cfg.url()
    assert cfg.url().endswith("?sslmode=require")
    # Masked view never includes the password but flags its presence.
    m = cfg.masked()
    assert "password" not in m
    assert m["has_password"] is True


def test_connections_list_reports_all_three_roles(client):
    rows = client.get("/settings/connections").json()
    roles = {r["role"] for r in rows}
    assert roles == {"PORTFOLIO", "SECURITIES", "UNIVERSE"}
    # The portfolio DB is flagged as the env-driven bootstrap.
    portfolio = next(r for r in rows if r["role"] == "PORTFOLIO")
    assert portfolio["bootstrap"] is True


def test_upsert_connection_never_returns_password(client):
    r = client.put(
        "/settings/connections/UNIVERSE",
        json={"host": "localhost", "port": 5434, "database": "cato_securities",
              "username": "readonly", "password": "secret", "sslmode": "require"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "password" not in body
    assert body["has_password"] is True
    assert body["host"] == "localhost" and body["database"] == "cato_securities"
    # And it shows up in the list as configured.
    rows = client.get("/settings/connections").json()
    uni = next(r for r in rows if r["role"] == "UNIVERSE")
    assert uni["has_password"] is True


def test_test_connection_fails_gracefully_for_bad_host(client):
    client.put(
        "/settings/connections/UNIVERSE",
        json={"host": "nonexistent.invalid", "port": 5434,
              "database": "cato_securities", "username": "ro", "password": "x"},
    )
    r = client.post("/settings/connections/UNIVERSE/test")
    assert r.status_code == 200
    body = r.json()
    # Connection fails, but the endpoint returns ok:false with a sanitized error — never
    # leaks the URL or password.
    assert body["ok"] is False
    assert "error" in body and "secret" not in str(body).lower()


def test_universe_sync_falls_back_to_synthetic_when_unconfigured(client):
    # No UNIVERSE connection configured for a fresh in-memory app → synthetic projection.
    r = client.post("/settings/universe/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "synthetic"
    assert body["synced"] > 0


def test_ingest_requires_a_populated_projection(client):
    # Fresh securities DB, nothing projected → clear 409, not an opaque crash.
    r = client.post("/securities/ingest", json={})
    assert r.status_code == 409


def test_ingest_reports_feed_unavailable_when_yfinance_missing(client):
    # Populate the projection, then ingest: yfinance isn't installed here, so the
    # endpoint must surface a clean 503 rather than a 500.
    client.post("/settings/universe/sync")
    r = client.post("/securities/ingest", json={"tickers": ["U0001"]})
    assert r.status_code == 503
    assert "yfinance" in r.json()["detail"]


def test_factor_ingest_reports_feed_unavailable(client, monkeypatch):
    # An unreachable Ken French feed → clean 503, not a 500. Force the download to fail so the
    # test is deterministic (never hits the real network, even on a connected machine).
    from neptune.securities.factor_providers import KenFrenchProvider

    def _boom(self, zip_name):
        raise RuntimeError(f"could not fetch Ken French factor data from {zip_name}: offline")

    monkeypatch.setattr(KenFrenchProvider, "_download_csv", _boom)
    r = client.post("/factors/ingest", json={})
    assert r.status_code == 503
    assert "Ken French" in r.json()["detail"]
