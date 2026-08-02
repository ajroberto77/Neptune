"""Guard helper for TimescaleDB-specific migrations.

Mirrors the dialect-check pattern already used by ``db/base.py``'s ``_ensure_enum_values``
(Postgres-only, additive, a safe no-op everywhere else) — but TimescaleDB isn't a base
Postgres feature, so this also probes whether the extension is actually installed. Both
SQLite (every test) and a plain Postgres deployment that hasn't run
``CREATE EXTENSION timescaledb`` take the same safe no-op branch.
"""
from __future__ import annotations

from sqlalchemy import text


def timescaledb_available(bind) -> bool:
    """True only for a live Postgres connection with the ``timescaledb`` extension actually
    installed (``CREATE EXTENSION timescaledb`` already run). False for SQLite and for plain
    Postgres without the extension — both are meant to keep ``prices`` as an ordinary table."""
    if bind.dialect.name != "postgresql":
        return False
    row = bind.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    return row is not None
