"""Application configuration.

Everything is driven by environment variables so the same code targets Postgres
(canonical) or SQLite (tests) by changing only the database URLs.
"""
from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Quant constants live here so the hard invariants
    (EWMA lambda, beta tolerance) have a single source of truth."""

    model_config = SettingsConfigDict(env_prefix="NEPTUNE_", env_file=".env", extra="ignore")

    # Persistence. Default is in-memory SQLite so the app/tests run with zero infra;
    # docker-compose + .env point these at Postgres for real use.
    #
    # Neptune owns TWO databases (strict separation; see docs/data_architecture.md):
    #   * portfolio_database_url  — investor entities / books / positions / lots (app)
    #   * securities_database_url — prices / dividends / corporate actions (market data)
    # and reads ONE external database READ-ONLY:
    #   * universe_database_url   — cato_securities master (instruments/identifiers)
    #
    # ``database_url`` is the legacy single-DB knob. The two Neptune URLs fall back to it
    # so existing single-DB setups (and the SQLite test fallback) keep working unchanged;
    # point them at distinct Postgres databases for the production three-DB topology.
    database_url: str = "sqlite+pysqlite:///:memory:"
    portfolio_database_url: str | None = None
    securities_database_url: str | None = None
    # Read-only link to the shared securities universe. None = not configured (the
    # synthetic CATALOG/universe is used instead, e.g. tests and offline dev).
    universe_database_url: str | None = None

    # --- Quant Engine constants (HARD invariants; see CLAUDE.md) ---
    ewma_lambda: float = 0.94          # EWMA decay for beta estimation
    beta_lookback: int = 252           # trading days in the estimation window
    beta_tol: float = 0.05             # |net portfolio beta| hard constraint
    benchmark: str = "SPY"             # market-beta benchmark (NEPTUNE_BENCHMARK)
    # Always-on price refresh: the server re-pulls latest prices every N minutes (0 = off).
    # Runtime-overridable + persisted via /settings/price-refresh; this is the default.
    price_refresh_minutes: int = 10
    # Seed the golden DEMO positions (AAA/BBB/CCC/DDD) on startup. True for first-run/tests.
    # Set NEPTUNE_SEED_DEMO_POSITIONS=false for a real book: stops seeding AND removes any
    # existing demo names (so a real benchmark can price the whole book — see market_data_for).
    seed_demo_positions: bool = False  # production starts clean; tests force it on (conftest)
    max_position_weight: float = 0.15  # position-size ceiling as fraction of long AUM
    factor_limit: float = 0.20         # per-factor exposure limit (Size/Value/Momentum)
    # Sector concentration: flag any GICS sector exceeding this fraction of total short
    # notional. Default 0.30 (tighter than the roadmap's 0.40); PM-adjustable in the GUI.
    sector_limit: float = 0.30

    @property
    def portfolio_url(self) -> str:
        """Resolved URL for Neptune's portfolio database (falls back to ``database_url``)."""
        return self.portfolio_database_url or self.database_url

    @property
    def securities_url(self) -> str:
        """Resolved URL for Neptune's market-data database (falls back to ``database_url``)."""
        return self.securities_database_url or self.database_url


# Read the bare (un-prefixed) connection env vars for convenience/compat, so deployments
# can set DATABASE_URL / PORTFOLIO_DATABASE_URL / SECURITIES_DATABASE_URL /
# UNIVERSE_DATABASE_URL without the NEPTUNE_ prefix.
_URL_ENV = {
    "database_url": "DATABASE_URL",
    "portfolio_database_url": "PORTFOLIO_DATABASE_URL",
    "securities_database_url": "SECURITIES_DATABASE_URL",
    "universe_database_url": "UNIVERSE_DATABASE_URL",
}


def _dotenv_values(path: str = ".env") -> dict[str, str]:
    """Minimal ``.env`` parser for the bare connection names. pydantic-settings only reads
    ``.env`` for the ``NEPTUNE_``-prefixed fields, so without this a ``.env`` copied from
    ``.env.example`` (which uses bare ``DATABASE_URL=…``) would be silently ignored and the
    app would keep using the in-memory SQLite default. No extra dependency — just the four
    keys we care about."""
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def get_settings() -> Settings:
    """Resolve settings with precedence: shell environment > ``.env`` file > defaults.
    Shell-first keeps tests (which set ``DATABASE_URL`` in ``os.environ``) authoritative."""
    dotenv = _dotenv_values()
    overrides: dict[str, str] = {}
    for field, env in _URL_ENV.items():
        if env in os.environ:
            overrides[field] = os.environ[env]
        elif env in dotenv:
            overrides[field] = dotenv[env]
    return Settings(**overrides)


settings = get_settings()
