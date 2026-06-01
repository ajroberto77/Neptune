"""Runtime resolution of database engines from the configurable connection settings.

The **portfolio** engine is the bootstrap: built from the environment at import time, since
it physically holds the settings table (you must connect to it before you can read any saved
connection). The **securities** engine, by contrast, is resolved at *use* time — a connection
saved on the Settings page overrides the environment, exactly as the universe already does.
This lets Neptune's securities DB be re-pointed (host or port) from the UI while testing,
without editing ``.env`` and restarting. (Eventually CATO and Neptune live on separate
servers; this is the seam that makes that a config change, not a code change.)

Engines are cached by URL and the cache is pre-seeded with the env-built engines, so the
default URLs reuse them — notably the shared in-memory SQLite the tests run on, which must
never be rebuilt into a separate empty database. A connection edit changes the URL string
(host/port/credentials all live in it), so the next resolution builds a fresh engine
automatically; no explicit cache invalidation needed.
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from neptune.config import settings
from neptune.db.base import (
    init_securities_db,
    make_engine,
    portfolio_engine,
    securities_engine,
)
from neptune.settings_store import ConnectionRole
from neptune.settings_store.service import ConnectionSettingsService

# url -> engine. Pre-seeded with the env engines so default URLs reuse them.
_engines: dict[str, object] = {
    settings.portfolio_url: portfolio_engine,
    settings.securities_url: securities_engine,
}
# Engines whose schema has been ensured. The env securities engine is created at app
# startup (lifespan → init_securities_db), so it starts here.
_schema_ready: set[int] = {id(securities_engine)}


def _securities_engine_for(url: str):
    """Get (or build + cache) the securities engine for ``url``, ensuring its schema exists
    the first time we point at a brand-new database."""
    engine = _engines.get(url)
    if engine is None:
        engine = make_engine(url)
        _engines[url] = engine
    if id(engine) not in _schema_ready:
        init_securities_db(engine)  # idempotent create_all on a freshly-pointed DB
        _schema_ready.add(id(engine))
    return engine


@contextmanager
def securities_session(portfolio_session: Session):
    """A securities-DB session against the *resolved* securities engine: the connection
    saved in Settings if present, else the environment. ``portfolio_session`` reads the
    stored settings (they live in the portfolio DB)."""
    url = ConnectionSettingsService(portfolio_session).resolve_url(ConnectionRole.SECURITIES)
    engine = _securities_engine_for(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session
