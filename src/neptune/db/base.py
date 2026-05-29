"""SQLAlchemy engine/session wiring, driven entirely by ``settings.database_url``.

Swapping SQLite (tests) for Postgres (production) is a single env-var change; the ORM
models are dialect-agnostic.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from neptune.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    db_url = url or settings.database_url
    kwargs: dict = {"future": True}
    if db_url.startswith("sqlite"):
        # check_same_thread: FastAPI/test threads share a connection.
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in db_url:
            # A single shared in-memory DB across all sessions (otherwise each
            # connection gets its own empty database).
            kwargs["poolclass"] = StaticPool
    return create_engine(db_url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(target_engine=engine) -> None:
    """Create tables. For the slice we use metadata.create_all; production uses Alembic."""
    from neptune.db import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=target_engine)
