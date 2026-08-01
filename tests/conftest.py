"""Test fixtures. Forces an in-memory SQLite database before the app imports its
engine, so tests never touch Postgres."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest  # noqa: E402

from neptune.config import settings  # noqa: E402
from neptune.db.base import (  # noqa: E402
    MacroSession,
    SecuritiesSession,
    SessionLocal,
    engine,
    init_db,
    init_macro_db,
    init_securities_db,
    macro_engine,
    securities_engine,
)


@pytest.fixture(autouse=True)
def _seed_demo_for_tests():
    """Production no longer seeds the fake demo book by default; the test suite exercises it
    (the golden AAA/BBB/CCC/DDD positions), so force it on for every test."""
    prev = settings.seed_demo_positions
    settings.seed_demo_positions = True
    yield
    settings.seed_demo_positions = prev


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


@pytest.fixture()
def macro_session():
    """A session against a freshly created in-memory macro schema (own engine/DB), dropped +
    recreated per test so series/observations/vintages don't leak between tests."""
    from neptune.db.base import MacroBase
    from neptune.macro import models  # noqa: F401  (register mappers)

    MacroBase.metadata.drop_all(bind=macro_engine)
    init_macro_db(macro_engine)
    with MacroSession() as s:
        yield s
