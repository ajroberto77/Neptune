"""Live-repointing the portfolio DB (no process restart).

Unlike SECURITIES/MACRO/UNIVERSE, the portfolio DB can't resolve its own target from a
stored row (the row lives in the database being pointed to), so it has always been
env-only, fixed at process start -- repointing it required editing .env and restarting.
These tests prove the fix in three independently-testable layers:

* ``repoint_portfolio`` itself (the risky primitive: test-connect, schema init, live
  engine swap) -- exercised directly against a real sqlite file, since ConnectionConfig
  (and therefore the HTTP endpoint) only assembles Postgres-shaped URLs and this sandbox
  has no reachable Postgres server.
* ``write_env_var`` -- pure file manipulation, no app/DB involved.
* the HTTP endpoint's orchestration (test-connect failure -> 400; on success, re-seed
  ownership scaffolding, persist the row, write .env, shape the response) -- the failure
  path needs no infra (a bad host really doesn't connect); the success path monkeypatches
  ``repoint_portfolio`` to redirect at a real sqlite file rather than the unreachable
  Postgres URL the request body describes, so orchestration is verified against a REAL
  swap without needing Postgres.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import neptune.api.main as api_main
import neptune.db.base as db_base
from neptune.api.main import app
from neptune.config import write_env_var
from neptune.db.base import Base, SecuritiesBase, engine, securities_engine
from neptune.db.runtime import repoint_portfolio
from neptune.positions.service import PositionService


@pytest.fixture(autouse=True)
def _restore_portfolio_engine():
    """SessionLocal is a process-global singleton these tests deliberately rebind --
    without restoring it, a repoint here would permanently redirect every OTHER test in
    the suite at a throwaway sqlite file. Always put it back, pass or fail."""
    original = db_base.SessionLocal.bind
    try:
        yield
    finally:
        db_base.SessionLocal.rebind(original)


# --- repoint_portfolio: the core primitive ---------------------------------------------

def test_repoint_rejects_an_unreachable_target_without_touching_anything(tmp_path):
    before = db_base.SessionLocal.bind
    bad_dir = tmp_path / "does-not-exist"  # sqlite can't create a file in a missing dir
    with pytest.raises(Exception):
        repoint_portfolio(f"sqlite+pysqlite:///{bad_dir}/x.db")
    assert db_base.SessionLocal.bind is before


def test_repoint_swaps_the_live_session_factory_and_persists_to_disk(tmp_path):
    target = tmp_path / "portfolio.db"
    new_engine = repoint_portfolio(f"sqlite+pysqlite:///{target}")

    assert target.exists()  # the file was created and its schema initialized
    assert db_base.SessionLocal.bind is new_engine

    # Write through the rebound factory...
    with db_base.SessionLocal() as session:
        PositionService(session).create_firm("ACME", "Acme Capital", is_internal=True)
        session.commit()

    # ...and read it back through a COMPLETELY independent connection to the same file,
    # proving this is a real, separately-addressable database, not an in-memory fixture.
    check_engine = db_base.make_engine(f"sqlite+pysqlite:///{target}")
    with check_engine.connect() as conn:
        row = conn.execute(
            text("SELECT name FROM management_firms WHERE id = 'ACME'")
        ).first()
    assert row is not None and row[0] == "Acme Capital"
    check_engine.dispose()


def test_repoint_is_idempotent_schema_init_on_an_existing_target(tmp_path):
    """Repointing twice at the same (now non-fresh) target must not choke on tables that
    already exist -- create_all is idempotent, and this proves it end-to-end here too."""
    target = tmp_path / "portfolio.db"
    repoint_portfolio(f"sqlite+pysqlite:///{target}")
    second = repoint_portfolio(f"sqlite+pysqlite:///{target}")
    assert db_base.SessionLocal.bind is second


# --- write_env_var: pure file manipulation ----------------------------------------------

def test_write_env_var_creates_a_new_file(tmp_path):
    path = tmp_path / ".env"
    write_env_var("PORTFOLIO_DATABASE_URL", "postgresql://a/b", path=str(path))
    assert path.read_text() == "PORTFOLIO_DATABASE_URL=postgresql://a/b\n"


def test_write_env_var_replaces_the_bare_key_and_preserves_everything_else(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n"
        "OTHER_VAR=keep-me\n"
        "PORTFOLIO_DATABASE_URL=old-value\n"
        "TRAILING=also-keep\n"
    )
    write_env_var("PORTFOLIO_DATABASE_URL", "new-value", path=str(path))
    assert path.read_text().splitlines() == [
        "# a comment",
        "OTHER_VAR=keep-me",
        "PORTFOLIO_DATABASE_URL=new-value",
        "TRAILING=also-keep",
    ]


def test_write_env_var_prefers_the_prefixed_key_when_that_is_what_the_file_uses(tmp_path):
    path = tmp_path / ".env"
    path.write_text("NEPTUNE_PORTFOLIO_DATABASE_URL=old-value\n")
    write_env_var("PORTFOLIO_DATABASE_URL", "new-value", path=str(path))
    # Updates the key that was actually there -- doesn't ALSO add a bare duplicate.
    assert path.read_text().splitlines() == ["NEPTUNE_PORTFOLIO_DATABASE_URL=new-value"]


def test_write_env_var_is_atomic_no_leftover_tmp_file(tmp_path):
    path = tmp_path / ".env"
    write_env_var("PORTFOLIO_DATABASE_URL", "v", path=str(path))
    assert [p for p in os.listdir(tmp_path) if p != ".env"] == []


# --- HTTP endpoint orchestration ---------------------------------------------------------

@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as c:
        yield c


def test_endpoint_rejects_an_unreachable_target(client):
    """A real unreachable Postgres host needs no mocking -- it genuinely won't connect."""
    before = db_base.SessionLocal.bind
    resp = client.put(
        "/settings/connections/PORTFOLIO",
        json={
            "host": "no-such-host.invalid", "port": 5432,
            "database": "x", "username": "x", "password": "x",
        },
    )
    assert resp.status_code == 400
    assert db_base.SessionLocal.bind is before
    assert client.get("/settings/connections").status_code == 200  # still fully usable


def test_endpoint_orchestrates_reseed_persist_and_env_write_on_success(client, tmp_path, monkeypatch):
    """ConnectionConfig only assembles Postgres-shaped URLs, and this sandbox has no
    reachable Postgres -- so this test monkeypatches ``repoint_portfolio`` to redirect at
    a real sqlite file instead of the (unreachable) Postgres URL the request describes.
    ``repoint_portfolio`` itself is already proven correct above; this test verifies the
    endpoint's OWN responsibilities: re-seeding ownership scaffolding on the fresh target,
    persisting the connection row there, writing .env, and shaping the response."""
    target = tmp_path / "http_success.db"

    def fake_repoint(_url):
        return repoint_portfolio(f"sqlite+pysqlite:///{target}")

    monkeypatch.setattr(api_main, "repoint_portfolio", fake_repoint)
    env_path = tmp_path / ".env"
    monkeypatch.setattr(
        api_main, "write_env_var",
        lambda name, value: write_env_var(name, value, path=str(env_path)),
    )

    resp = client.put(
        "/settings/connections/PORTFOLIO",
        json={
            "host": "pg.internal", "port": 5432, "database": "neptune_portfolios_v2",
            "username": "neptune", "password": "supersecret",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reconnected"] is True
    assert body["env_updated"] is True
    assert body["has_password"] is True
    assert "supersecret" not in resp.text  # never echoed

    # Ownership scaffolding was re-seeded on the fresh target.
    assert any(f["id"] == "IRIDIUM" for f in client.get("/firms").json())

    # The connection row was recorded on the NEW target, not the one we left.
    portfolio_row = next(
        r for r in client.get("/settings/connections").json() if r["role"] == "PORTFOLIO"
    )
    assert portfolio_row["database"] == "neptune_portfolios_v2"

    # .env durability: the real writer ran against our redirected path.
    assert "neptune_portfolios_v2" in env_path.read_text()
