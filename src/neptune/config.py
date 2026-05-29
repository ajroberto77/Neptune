"""Application configuration.

Everything is driven by environment variables so the same code targets Postgres
(canonical) or SQLite (tests) by changing only ``DATABASE_URL``.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Quant constants live here so the hard invariants
    (EWMA lambda, beta tolerance) have a single source of truth."""

    model_config = SettingsConfigDict(env_prefix="NEPTUNE_", env_file=".env", extra="ignore")

    # Persistence. Default is in-memory SQLite so the app/tests run with zero infra;
    # docker-compose + .env point this at Postgres for real use.
    database_url: str = "sqlite+pysqlite:///:memory:"

    # --- Quant Engine constants (HARD invariants; see CLAUDE.md) ---
    ewma_lambda: float = 0.94          # EWMA decay for beta estimation
    beta_lookback: int = 252           # trading days in the estimation window
    beta_tol: float = 0.05             # |net portfolio beta| hard constraint
    max_position_weight: float = 0.15  # position-size ceiling as fraction of long AUM
    factor_limit: float = 0.20         # per-factor exposure limit (Size/Value/Momentum)


# Note: read DATABASE_URL without the NEPTUNE_ prefix for convenience/compat.
import os  # noqa: E402


def get_settings() -> Settings:
    overrides: dict[str, str] = {}
    if "DATABASE_URL" in os.environ:
        overrides["database_url"] = os.environ["DATABASE_URL"]
    return Settings(**overrides)


settings = get_settings()
