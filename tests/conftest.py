"""Test fixtures. Forces an in-memory SQLite database before the app imports its
engine, so tests never touch Postgres."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest  # noqa: E402

from neptune.db.base import (  # noqa: E402
    SecuritiesSession,
    SessionLocal,
    engine,
    init_db,
    init_securities_db,
    securities_engine,
)


@pytest.fixture()
def session():
    """A session against a freshly created in-memory portfolio schema.

    The in-memory DB is shared across the process (StaticPool), so drop + recreate to
    isolate each test (otherwise firm/entity/book rows leak between tests)."""
    from neptune.db.base import Base
    from neptune.db import models  # noqa: F401  (register mappers)

    Base.metadata.drop_all(bind=engine)
    init_db(engine)
    with SessionLocal() as s:
        yield s


@pytest.fixture()
def securities_session():
    """A session against a freshly created in-memory securities (market-data) schema.

    The in-memory DB is shared across the process (StaticPool), so drop + recreate to
    isolate each test (otherwise projected ``securities`` rows leak between tests)."""
    from neptune.db.base import SecuritiesBase
    from neptune.securities import models  # noqa: F401  (register mappers)

    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    init_securities_db(securities_engine)
    with SecuritiesSession() as s:
        yield s
