"""SQLAlchemy engine/session wiring for Neptune's databases.

Neptune owns two databases (strict separation; see ``docs/data_architecture.md``):

* **portfolio** — investor entities, PMs, books, positions, lots. ``PortfolioBase``.
* **securities** — prices, dividends, corporate actions, the universe projection.
  ``SecuritiesBase``.

Each has its own declarative base (so ``create_all`` targets the right tables on the
right engine) and its own engine/session factory, both driven by ``settings``. Swapping
SQLite (tests) for Postgres (production) is a URL change; the ORM models are
dialect-agnostic.

Backward compatibility: ``Base`` / ``engine`` / ``SessionLocal`` / ``init_db`` refer to
the **portfolio** database (the original single-DB slice), so existing imports and the
test fixtures keep working unchanged. The securities database adds parallel
``SecuritiesBase`` / ``securities_engine`` / ``SecuritiesSession`` / ``init_securities_db``.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from neptune.config import settings


def _enable_sqlite_fks(dbapi_conn, _record):
    """SQLite ships with foreign-key enforcement OFF. Turn it ON per-connection so the
    test backend rejects dangling FKs (e.g. a position.pm_id with no people row) the same
    way Postgres does — otherwise tests give false confidence about referential safety."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class PortfolioBase(DeclarativeBase):
    """Declarative base for the portfolio database (entities/books/positions/lots)."""


class SecuritiesBase(DeclarativeBase):
    """Declarative base for the securities database (prices/dividends/corp actions)."""


class MacroBase(DeclarativeBase):
    """Declarative base for the macro database (rates/credit + economic series).
    See ``docs/macro_data.md``."""


# Backward-compatible alias: the original slice imported ``Base`` for portfolio models.
Base = PortfolioBase


def make_engine(url: str | None = None):
    db_url = url or settings.portfolio_url
    kwargs: dict = {"future": True}
    if db_url.startswith("sqlite"):
        # check_same_thread: FastAPI/test threads share a connection.
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in db_url:
            # A single shared in-memory DB across all sessions (otherwise each
            # connection gets its own empty database).
            kwargs["poolclass"] = StaticPool
    eng = create_engine(db_url, **kwargs)
    if db_url.startswith("sqlite"):
        event.listen(eng, "connect", _enable_sqlite_fks)
    return eng


class _RebindableSessionmaker:
    """A sessionmaker whose target engine can be swapped in place.

    Every existing ``from neptune.db.base import SessionLocal`` binds to THIS ONE OBJECT
    at import time (api/main.py, scheduling/scheduler.py). Reassigning the module-level
    ``SessionLocal`` name later would be invisible to those callers — Python copies the
    reference at import time, so a later ``db.base.SessionLocal = x`` doesn't reach them.
    Mutating this object's internal engine instead is visible everywhere immediately,
    since every caller holds a reference to the same object. This is what lets the
    portfolio DB be re-pointed live, from a request handler, with no process restart —
    see ``db.runtime.repoint_portfolio``.
    """

    def __init__(self, engine):
        self._engine = engine
        self._factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def __call__(self, *args, **kwargs):
        return self._factory(*args, **kwargs)

    @property
    def bind(self):
        """The engine sessions are currently created against."""
        return self._engine

    def rebind(self, engine) -> None:
        self._engine = engine
        self._factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)


# --- Portfolio database (the canonical app DB; default target) -------------------
portfolio_engine = make_engine(settings.portfolio_url)
SessionLocal = _RebindableSessionmaker(portfolio_engine)

# Backward-compatible aliases. NOTE: ``engine`` is a snapshot taken at import time, not a
# live reference — after ``repoint_portfolio`` swaps the session factory, ``engine`` here
# still points at the ORIGINAL database. Nothing in this codebase queries it directly
# (grep confirms only ``SessionLocal()`` is ever used to talk to the portfolio DB); it
# exists purely as `init_db`'s default parameter, which a repoint always overrides
# explicitly. Prefer ``SessionLocal.bind`` if you need the current engine.
engine = portfolio_engine
PortfolioSession = SessionLocal

# --- Securities database (market data) -------------------------------------------
securities_engine = make_engine(settings.securities_url)
SecuritiesSession = sessionmaker(bind=securities_engine, expire_on_commit=False, future=True)

# --- Macro database (rates/credit + economic series) -----------------------------
macro_engine = make_engine(settings.macro_url)
MacroSession = sessionmaker(bind=macro_engine, expire_on_commit=False, future=True)


def init_db(target_engine=engine) -> None:
    """Create the **portfolio** tables. For the slice we use ``create_all``; production
    uses Alembic. Default target is the portfolio engine; tests pass an explicit one."""
    from neptune.db import models  # noqa: F401  (register portfolio mappers)
    from neptune.settings_store import ConnectionRole

    PortfolioBase.metadata.create_all(bind=target_engine)
    # Additive bridge until Alembic: a deployed portfolio DB picks up new columns.
    _ensure_columns(target_engine, "lots", {"fee_per_share": "FLOAT DEFAULT 0"})
    _ensure_columns(target_engine, "portfolios", {"mandate": "VARCHAR DEFAULT 'LONG_SHORT'"})
    # A DB created before MACRO existed has a native Postgres enum lacking that label;
    # create_all never adds values to an existing enum type, so querying the MACRO role
    # would error. Backfill any missing labels (Postgres-only; no-op on SQLite).
    _ensure_enum_values(target_engine, "connectionrole", [r.value for r in ConnectionRole])


def init_securities_db(target_engine=securities_engine) -> None:
    """Create the **securities** tables (prices/dividends/corporate actions/projection).
    Hypertable conversion (TimescaleDB) is an additive Postgres-only migration step;
    plain ``create_all`` yields a working relational schema everywhere (incl. SQLite)."""
    from neptune.securities import models  # noqa: F401  (register securities mappers)

    SecuritiesBase.metadata.create_all(bind=target_engine)
    # No Alembic yet: `create_all` won't add columns to an EXISTING table. Bridge that for
    # additive columns so a deployed securities DB picks up new fields without a manual migration.
    _ensure_columns(target_engine, "securities", {"sector": "VARCHAR"})


def init_macro_db(target_engine=macro_engine) -> None:
    """Create the **macro** tables (series registry + observations + vintages + release
    calendar). Like the securities DB, ``create_all`` yields a working relational schema
    everywhere; on Postgres the observation/vintage fact tables are TimescaleDB-hypertable
    candidates via a later guarded migration."""
    from neptune.macro import models  # noqa: F401  (register macro mappers)

    MacroBase.metadata.create_all(bind=target_engine)


def _ensure_enum_values(target_engine, type_name: str, values: list[str]) -> None:
    """Lightweight additive migration for native Postgres ENUM types: ``ALTER TYPE ... ADD
    VALUE IF NOT EXISTS`` for any label missing from an EXISTING enum type. Needed because
    ``create_all`` never alters an enum type once it exists, so a DB created before a new
    enum member was added (e.g. the MACRO connection role) would reject queries on that value.
    Postgres-only and idempotent; a no-op on SQLite (which stores enums as plain text)."""
    if target_engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    # ADD VALUE cannot run inside a transaction block, so use an AUTOCOMMIT connection.
    with target_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": type_name}
        ).first()
        if not exists:
            return  # fresh DB: create_all already built the type with every current label
        for v in values:
            conn.execute(text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{v}'"))


def _ensure_columns(target_engine, table: str, columns: dict[str, str]) -> None:
    """Lightweight additive migration: ALTER TABLE ADD COLUMN for any of ``columns`` missing
    from ``table``. Idempotent; works on SQLite and Postgres. Interim until Alembic lands."""
    from sqlalchemy import inspect, text

    insp = inspect(target_engine)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    with target_engine.begin() as conn:
        for name, ddl_type in columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
