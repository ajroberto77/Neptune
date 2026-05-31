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


# --- Portfolio database (the canonical app DB; default target) -------------------
portfolio_engine = make_engine(settings.portfolio_url)
PortfolioSession = sessionmaker(bind=portfolio_engine, expire_on_commit=False, future=True)

# Backward-compatible aliases.
engine = portfolio_engine
SessionLocal = PortfolioSession

# --- Securities database (market data) -------------------------------------------
securities_engine = make_engine(settings.securities_url)
SecuritiesSession = sessionmaker(bind=securities_engine, expire_on_commit=False, future=True)


def init_db(target_engine=engine) -> None:
    """Create the **portfolio** tables. For the slice we use ``create_all``; production
    uses Alembic. Default target is the portfolio engine; tests pass an explicit one."""
    from neptune.db import models  # noqa: F401  (register portfolio mappers)

    PortfolioBase.metadata.create_all(bind=target_engine)


def init_securities_db(target_engine=securities_engine) -> None:
    """Create the **securities** tables (prices/dividends/corporate actions/projection).
    Hypertable conversion (TimescaleDB) is an additive Postgres-only migration step;
    plain ``create_all`` yields a working relational schema everywhere (incl. SQLite)."""
    from neptune.securities import models  # noqa: F401  (register securities mappers)

    SecuritiesBase.metadata.create_all(bind=target_engine)
