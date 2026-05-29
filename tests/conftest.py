"""Test fixtures. Forces an in-memory SQLite database before the app imports its
engine, so tests never touch Postgres."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest  # noqa: E402

from neptune.db.base import SessionLocal, engine, init_db  # noqa: E402


@pytest.fixture()
def session():
    """A session against a freshly created in-memory schema."""
    init_db(engine)
    with SessionLocal() as s:
        yield s
