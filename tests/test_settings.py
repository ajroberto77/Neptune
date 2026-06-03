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


def test_connections_list_reports_all_roles(client):
    rows = client.get("/settings/connections").json()
    roles = {r["role"] for r in rows}
    assert roles == {"PORTFOLIO", "SECURITIES", "MACRO", "UNIVERSE"}
    # The portfolio DB is flagged as the env-driven bootstrap.
    portfolio = next(r for r in rows if r["role"] == "PORTFOLIO")
    assert portfolio["bootstrap"] is True


def test_credentials_status_starts_unset(client):
    rows = client.get("/settings/credentials").json()
    fred = next(r for r in rows if r["provider"] == "FRED")
    assert fred == {"provider": "FRED", "has_key": False, "source": "none"}


def test_set_credential_is_write_only_and_resolves(client):
    # Storing a key never echoes it back; status flips to stored.
    body = client.put("/settings/credentials/FRED", json={"api_key": "abc123secret"}).json()
    assert body == {"provider": "FRED", "has_key": True, "source": "stored"}
    assert "api_key" not in body and "abc123secret" not in str(body)
    # And it isn't exposed by the list endpoint either.
    listed = client.get("/settings/credentials").json()
    assert "abc123secret" not in str(listed)
    fred = next(r for r in listed if r["provider"] == "FRED")
    assert fred["has_key"] is True

    # The service resolves the actual key for the ingest layer (never serialized).
    from neptune.db.base import SessionLocal
    from neptune.settings_store.credentials import CredentialsService
    with SessionLocal() as s:
        assert CredentialsService(s).resolve_key("FRED") == "abc123secret"

    # Empty string clears it.
    cleared = client.put("/settings/credentials/FRED", json={"api_key": ""}).json()
    assert cleared["has_key"] is False


def test_set_credential_unknown_provider_404(client):
    r = client.put("/settings/credentials/BLOOMBERG", json={"api_key": "x"})
    assert r.status_code == 404


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


def test_beta_diagnostics_traces_low_beta_to_data(client):
    """The per-ticker beta diagnostic exposes the regression inputs: a full-history name returns
    a fitted beta ('ok'); a name with too little history is flagged insufficient_data and held at
    the 1.0 prior — so a surprising beta can be traced to data vs a genuine fit."""
    from datetime import date, timedelta

    import numpy as np
    from sqlalchemy.orm import Session

    from neptune.db.base import securities_engine
    from neptune.securities.models import Price, Security

    rng = np.random.default_rng(1)
    n = 200
    mkt = rng.normal(0.0, 0.01, n)

    def _seed(sec, iid, ticker, rets, base=50.0):
        sec.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock"))
        px = [base]
        for r in rets:
            px.append(px[-1] * (1 + r))
        start = date.today() - timedelta(days=len(px) + 1)
        for i, p in enumerate(px):
            sec.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                          close=p, adj_close=p, source="yfinance"))

    with Session(securities_engine) as sec:
        _seed(sec, 1, "SPY", mkt, base=400.0)
        _seed(sec, 2, "FULL", 1.20 * mkt + rng.normal(0, 0.004, n))  # full history, beta ~1.2
        _seed(sec, 3, "THIN", (1.20 * mkt)[-20:])                    # 20 bars — below the floor
        sec.commit()

    r = client.post("/securities/beta-diagnostics", json={"tickers": ["FULL", "THIN"]})
    assert r.status_code == 200
    body = r.json()
    assert body["benchmark"]["ticker"] == "SPY" and body["benchmark"]["bars"] >= 100
    by = {row["ticker"]: row for row in body["names"]}
    assert by["FULL"]["status"] == "ok"
    assert by["FULL"]["beta_raw"] == pytest.approx(1.20, abs=0.3)  # data-traced, not muted to 0
    assert by["THIN"]["status"] == "insufficient_data"
    assert by["THIN"]["obs_used"] < body["min_obs"]


def test_portfolio_beta_history_returns_net_and_per_position_series(client):
    """The beta-consistency endpoint returns a net-beta walk-forward plus per-holding beta
    histories with drift stats — date-stamped, point-in-time, over the benchmark's date axis."""
    from datetime import date, timedelta

    import numpy as np
    from sqlalchemy.orm import Session

    from neptune.db.base import securities_engine
    from neptune.securities.models import Price, Security

    rng = np.random.default_rng(3)
    n = 320
    mkt = rng.normal(0.0, 0.01, n)

    def _seed(sec, iid, ticker, rets, base=100.0):
        sec.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock"))
        px = [base]
        for r in rets:
            px.append(px[-1] * (1 + r))
        start = date.today() - timedelta(days=len(px) + 1)
        for i, p in enumerate(px):
            sec.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                          close=p, adj_close=p, source="yfinance"))

    with Session(securities_engine) as sec:
        _seed(sec, 1, "SPY", mkt, base=400.0)
        _seed(sec, 2, "HB", 1.20 * mkt + rng.normal(0, 0.004, n))  # ~1.2 beta name
        sec.commit()

    # A real book holding the priced name.
    client.post("/portfolios", json={"id": "BH", "name": "Beta Hist Book"})
    client.post("/portfolios/BH/positions", json={
        "ticker": "HB", "side": "LONG", "notional": 1_000_000.0,
    })

    r = client.get("/portfolios/BH/beta-history?points=10&step=10")
    assert r.status_code == 200
    body = r.json()
    assert len(body["net"]) >= 5
    assert all("date" in pt and "net_beta" in pt for pt in body["net"])
    assert body["net_stats"]["points"] == len(body["net"])
    hb = next(p for p in body["positions"] if p["ticker"] == "HB")
    assert len(hb["series"]) >= 5
    assert hb["stats"]["mean"] == pytest.approx(1.2, abs=0.3)  # recovers the name's ~1.2 beta
    assert hb["stats"]["range"] < 0.4  # stable name → low drift


def test_hedge_backtest_replays_and_controls_net_beta(client):
    """The walk-forward hedge backtest re-optimizes at each rebalance and reports the REALIZED
    net beta. With a clean universe that can hedge the long book, realized |β| should stay well
    inside tolerance most of the time (high coverage)."""
    from datetime import date, timedelta

    import numpy as np
    from sqlalchemy.orm import Session

    from neptune.db.base import securities_engine
    from neptune.securities.models import Price, Security

    rng = np.random.default_rng(7)
    n = 900  # ~3.5y of daily bars so the walk-forward has room
    mkt = rng.normal(0.0, 0.01, n)

    def _seed(sec, iid, ticker, betas_to_market, base=100.0):
        rets = betas_to_market * mkt + rng.normal(0, 0.004, n)
        sec.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock"))
        px = [base]
        for r in rets:
            px.append(px[-1] * (1 + r))
        start = date.today() - timedelta(days=len(px) + 1)
        for i, p in enumerate(px):
            sec.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                          close=p, adj_close=p, source="yfinance"))

    with Session(securities_engine) as sec:
        _seed(sec, 1, "SPY", np.ones(n))            # benchmark
        _seed(sec, 2, "LONGA", 1.1 * np.ones(n))    # the long we must hedge
        # A diversified shortable universe of positive-beta names (ample capacity to hedge to ~0).
        betas = [0.6, 0.8, 1.0, 1.2, 1.4, 0.9, 1.1, 0.7, 0.5, 1.3, 0.85, 1.05, 0.95, 1.15, 0.75, 1.25]
        for j, b in enumerate(betas, start=3):
            _seed(sec, j, f"H{j}", b * np.ones(n))
        sec.commit()

    client.post("/portfolios", json={"id": "BT", "name": "Backtest Book"})
    client.post("/portfolios/BT/positions", json={
        "ticker": "LONGA", "side": "LONG", "notional": 1_000_000.0,
    })

    r = client.get("/portfolios/BT/hedge-backtest?points=10&step=21")
    assert r.status_code == 200
    body = r.json()
    assert body["n_rebalances"] >= 5
    assert all("realized_net_beta" in p for p in body["points"])
    assert body["rmse"] < 0.05           # the process keeps realized net beta controlled
    assert body["coverage"] >= 0.8       # most rebalances land inside |β| <= tol


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
