"""Persistence + resolution for configurable database connections.

``ConnectionSettingsService`` reads/writes the ``db_connections`` rows in the portfolio
DB and resolves an effective URL for a role, preferring a stored connection over the
environment fallback in ``config.settings``.
"""
from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from neptune.config import settings
from neptune.db.models import DbConnectionORM
from neptune.settings_store import ConnectionConfig, ConnectionRole


def _to_config(row: DbConnectionORM) -> ConnectionConfig:
    return ConnectionConfig(
        role=row.role,
        host=row.host,
        port=row.port,
        database=row.database,
        username=row.username,
        password=row.password,
        sslmode=row.sslmode,
        driver=row.driver,
    )


def _url_to_masked(role: ConnectionRole, url: str) -> dict:
    """Parse a SQLAlchemy URL into the same masked dict shape as ``ConnectionConfig.masked()``
    — host/port/database/username/driver, with the password reduced to a presence flag. Used
    to pre-fill the Settings form from the env/.env URL when no row is stored."""
    u = make_url(url)
    return {
        "role": role.value,
        "host": u.host or "",
        "port": u.port or 5432,
        "database": u.database or "",
        "username": u.username or "",
        "sslmode": (u.query.get("sslmode") if u.query else None),
        "driver": u.drivername,
        "has_password": bool(u.password),
    }


# Env fallback per role, used when no row is stored.
_ENV_URL = {
    ConnectionRole.PORTFOLIO: lambda: settings.portfolio_url,
    ConnectionRole.SECURITIES: lambda: settings.securities_url,
    ConnectionRole.MACRO: lambda: settings.macro_url,
    ConnectionRole.UNIVERSE: lambda: settings.universe_database_url,
}


class ConnectionSettingsService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, role: ConnectionRole) -> ConnectionConfig | None:
        row = self.session.get(DbConnectionORM, role)
        return _to_config(row) if row else None

    def list_all(self) -> list[ConnectionConfig]:
        return [_to_config(r) for r in self.session.query(DbConnectionORM).all()]

    def upsert(
        self,
        role: ConnectionRole,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str | None = None,
        sslmode: str | None = None,
        driver: str | None = None,
    ) -> ConnectionConfig:
        """Create or update a connection. A ``password`` of ``None`` LEAVES the stored
        password unchanged (so the UI can save host/user edits without re-sending the
        secret); pass an empty string to deliberately clear it."""
        row = self.session.get(DbConnectionORM, role)
        if row is None:
            row = DbConnectionORM(role=role)
            self.session.add(row)
        row.host = host
        row.port = port
        row.database = database
        row.username = username
        if password is not None:
            row.password = password or None
        row.sslmode = sslmode
        if driver:
            row.driver = driver
        self.session.commit()
        return _to_config(row)

    def resolve_url(self, role: ConnectionRole) -> str | None:
        """The effective connection URL for a role: the stored connection if present,
        else the environment fallback. May be None for UNIVERSE when nothing is set
        (the synthetic universe is used instead)."""
        cfg = self.get(role)
        if cfg is not None:
            return cfg.url()
        return _ENV_URL[role]()

    def describe(self, role: ConnectionRole) -> dict:
        """A masked, form-ready view of the EFFECTIVE connection for a role: the stored row
        if present, else the host/port/database/username parsed from the env/.env URL so the
        Settings form pre-fills what the app is actually using. The password is never
        exposed; ``source`` ∈ {stored, env, none}."""
        cfg = self.get(role)
        if cfg is not None:
            return {**cfg.masked(), "configured": True, "source": "stored"}
        url = _ENV_URL[role]()
        if not url:
            return {"role": role.value, "configured": False, "source": "none"}
        return {**_url_to_masked(role, url), "configured": True, "source": "env"}
