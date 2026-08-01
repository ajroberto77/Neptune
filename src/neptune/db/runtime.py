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

import threading
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from neptune.config import settings
from neptune.db.base import (
    SessionLocal,
    init_db,
    init_macro_db,
    init_securities_db,
    macro_engine,
    make_engine,
    portfolio_engine,
    securities_engine,
)
from neptune.settings_store import ConnectionRole
from neptune.settings_store.service import ConnectionSettingsService

# url -> engine. Pre-seeded with the env engines so default URLs reuse them. NOTE: the macro
# engine gets its OWN cache below — portfolio/securities/macro can resolve to the SAME url
# string (the shared in-memory SQLite in tests) yet are distinct engines/databases, so a single
# url-keyed dict would clobber one with another.
_engines: dict[str, object] = {
    settings.portfolio_url: portfolio_engine,
    settings.securities_url: securities_engine,
}
_macro_engines: dict[str, object] = {settings.macro_url: macro_engine}
# Engines whose schema has been ensured. The env securities/macro engines are created at app
# startup (lifespan → init_*_db), so they start here.
_schema_ready: set[int] = {id(securities_engine), id(macro_engine)}


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


def _macro_engine_for(url: str):
    """Get (or build + cache) the macro engine for ``url``, ensuring its schema exists the
    first time we point at a brand-new database. Uses the macro-only cache so it never
    collides with the securities engine on a shared url string."""
    engine = _macro_engines.get(url)
    if engine is None:
        engine = make_engine(url)
        _macro_engines[url] = engine
    if id(engine) not in _schema_ready:
        init_macro_db(engine)  # idempotent create_all on a freshly-pointed DB
        _schema_ready.add(id(engine))
    return engine


@contextmanager
def macro_session(portfolio_session: Session):
    """A macro-DB session against the *resolved* macro engine: the connection saved in
    Settings if present, else the environment."""
    url = ConnectionSettingsService(portfolio_session).resolve_url(ConnectionRole.MACRO)
    engine = _macro_engine_for(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session


# The portfolio DB is different from the other three roles: it physically holds the
# db_connections table, so it can't resolve its OWN target by reading a row from itself.
# It has therefore always been env-only, fixed at process start — repointing it required
# editing .env and restarting. That's a real gap on the plain API path (Electron already
# closes it: saving there rewrites its own config file and respawns the whole backend).
# ``repoint_portfolio`` closes it without a restart: SessionLocal is one long-lived object
# every existing caller already imported (see _RebindableSessionmaker's docstring), so
# swapping its target engine takes effect for every future request immediately.
_portfolio_lock = threading.Lock()


def repoint_portfolio(url: str):
    """Point the running app's portfolio-DB session factory at ``url``, live.

    Test-connects first — a bad host/port/credential never partially swaps anything; the
    old target keeps serving until a new one proves reachable. On success, ensures the
    target's schema exists (a genuinely fresh database gets its tables the same way a
    fresh install would) and swaps ``SessionLocal`` to it. Returns the new engine.

    This does not migrate data — repointing to a different database is exactly that, a
    different origin of truth, not a copy of the current one. Roles whose own connection
    is stored IN the portfolio DB (SECURITIES/MACRO/UNIVERSE) fall back to their env
    defaults on the new target unless re-saved there; the caller is responsible for any
    higher-level bootstrapping (e.g. re-seeding ownership scaffolding) since that lives
    alongside the other startup logic, not here.
    """
    new_engine = make_engine(url)
    try:
        with new_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        new_engine.dispose()
        raise

    with _portfolio_lock:
        init_db(target_engine=new_engine)  # idempotent create_all + additive bridges
        SessionLocal.rebind(new_engine)
    return new_engine
