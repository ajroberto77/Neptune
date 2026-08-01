"""Database connection settings — the configurable connection targets behind the
Settings page.

Neptune talks to three databases (see ``docs/data_architecture.md``); this module lets a
user re-point each from the UI, persisting the connection config in the **portfolio**
database. Two hard rules live here:

* **The portfolio DB is the bootstrap.** It physically stores these settings, so its own
  connection must come from the environment at startup — a stored ``portfolio`` row is
  informational and only takes effect on the next restart. The universe/securities
  connections can be rebuilt live.
* **The password is write-only.** It is persisted but NEVER returned by the API; reads
  mask it. Nothing here logs the secret.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote_plus


class ConnectionRole(str, Enum):
    """Which of Neptune's three database targets a connection row configures."""

    PORTFOLIO = "PORTFOLIO"    # app DB — bootstrap; env-driven at runtime
    SECURITIES = "SECURITIES"  # Neptune market-data DB (read/write)
    MACRO = "MACRO"            # Neptune macro-data DB (read/write)
    UNIVERSE = "UNIVERSE"      # cato_securities master (READ-ONLY)


# Per-role driver defaults. Postgres for the real targets; the value is the SQLAlchemy
# ``dialect+driver`` prefix used when assembling a URL.
DEFAULT_DRIVER = "postgresql+psycopg"


@dataclass
class ConnectionConfig:
    """A resolved connection target. ``password`` is present only when building a URL;
    the API layer never serializes it."""

    role: ConnectionRole
    host: str
    port: int
    database: str
    username: str
    password: str | None = None
    sslmode: str | None = None
    driver: str = DEFAULT_DRIVER

    def url(self) -> str:
        """Assemble a SQLAlchemy URL. Credentials are percent-encoded so passwords with
        special characters don't corrupt the URL."""
        user = quote_plus(self.username)
        auth = user
        if self.password:
            auth = f"{user}:{quote_plus(self.password)}"
        url = f"{self.driver}://{auth}@{self.host}:{self.port}/{self.database}"
        if self.sslmode:
            url += f"?sslmode={self.sslmode}"
        return url

    def masked(self) -> dict:
        """A dict safe to return over the API: every field except the password, plus a
        boolean flag indicating whether a password is stored."""
        return {
            "role": self.role.value,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "sslmode": self.sslmode,
            "driver": self.driver,
            "has_password": bool(self.password),
        }
