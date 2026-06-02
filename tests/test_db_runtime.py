"""Runtime securities-engine resolution: default reuses the env engine; a stored Settings
connection overrides the environment (the host/port-from-the-UI capability)."""
from __future__ import annotations

from sqlalchemy import create_engine, text

from neptune.db import runtime
from neptune.db.base import securities_engine
from neptune.settings_store.service import ConnectionSettingsService
from neptune.securities.models import Security


def test_default_resolves_to_env_securities_engine(session):
    # No stored SECURITIES connection → resolves to the env URL → reuses the SAME engine
    # the app/tests already use (so the shared in-memory DB isn't rebuilt empty).
    with runtime.securities_session(session) as s:
        assert s.get_bind() is securities_engine


def test_stored_connection_overrides_environment(tmp_path, session, monkeypatch):
    # Simulate a Settings-saved securities connection pointing at a different database.
    # (resolve_url builds a Postgres URL from the stored row; we substitute a sqlite file
    # so the override is exercisable offline.)
    override_url = f"sqlite+pysqlite:///{tmp_path / 'override_securities.db'}"
    monkeypatch.setattr(
        ConnectionSettingsService, "resolve_url", lambda self, role: override_url
    )

    with runtime.securities_session(session) as s:
        # Schema was auto-created on the freshly-pointed DB; write a row there.
        s.add(Security(instrument_id=99, ticker="OVR", security_type="Common Stock"))
        s.commit()

    # The row landed in the override database, not the in-memory default.
    eng = create_engine(override_url)
    with eng.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM securities WHERE instrument_id = 99")
        ).scalar()
    assert n == 1
