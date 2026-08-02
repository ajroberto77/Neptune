"""timescaledb_available(): the guard that keeps the prices-hypertable migration a safe
no-op everywhere except a real Postgres with the timescaledb extension installed."""
from __future__ import annotations

from unittest.mock import MagicMock

from neptune.db.timescale import timescaledb_available


def test_false_on_the_real_sqlite_test_engine(securities_session):
    # Every test runs on SQLite (see conftest.py) -- this is what makes the migration's
    # guarded upgrade()/downgrade() fully inert wherever the suite runs.
    bind = securities_session.get_bind()
    assert timescaledb_available(bind) is False


def test_false_on_postgres_without_the_extension():
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    bind.execute.return_value.first.return_value = None  # no pg_extension row
    assert timescaledb_available(bind) is False


def test_true_on_postgres_with_the_extension():
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    bind.execute.return_value.first.return_value = (1,)  # a pg_extension row exists
    assert timescaledb_available(bind) is True
