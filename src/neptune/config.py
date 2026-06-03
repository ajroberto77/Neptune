"""Application configuration.

Everything is driven by environment variables so the same code targets Postgres
(canonical) or SQLite (tests) by changing only the database URLs.
"""
from __future__ import annotations

import os

from pydantic import Field, field_validator
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
    # Default hedge basket size: aim for a diversified basket of ~this many short names
    # (overridable per propose). A diversified hedge is many moderate names, not a few extremes.
    target_hedge_names: int = 35
    factor_limit: float = 0.20         # per-factor exposure limit (Size/Value/Momentum)
    # Sector concentration: flag any GICS sector exceeding this fraction of total short
    # notional. Default 0.30 (tighter than the roadmap's 0.40); PM-adjustable in the GUI.
    sector_limit: float = 0.30
    # Firm-level net-beta target (CLAUDE.md §3) — tighter than the per-book |β| ≤ 0.05; the
    # binding limit the hedge is ultimately governed by.
    firm_beta_tol: float = 0.030
    # Negative-beta-short fence (short-book research, Option A): cap the aggregate net market beta
    # the shorts may ADD by shorting negative-beta names — Σ max(0, −βᵢ·xᵢ) ≤ budget. Expressed as
    # a FRACTION of the binding beta limit (a nested sub-limit set well inside its parent, per the
    # research) rather than a free-standing number, so it tracks whatever limit is operative. ~1/3
    # of beta_tol ≈ 0.017 today; confirm empirically via the calibration backtest.
    beta_add_fraction: float = 0.33

    @property
    def beta_add_budget(self) -> float:
        """The fence level in beta units = fraction × the (per-book) binding limit."""
        return self.beta_add_fraction * self.beta_tol

    # Liquidity screen (short-book research, Option D): drop shortable names whose trailing
    # average daily DOLLAR volume is below this floor — you can't reliably short an illiquid
    # name. 0 = off. Borrow availability / hard-to-borrow needs an external feed (roadmap).
    min_adv_usd: float = 0.0
    adv_window: int = 63  # ~3 months of trading days for the ADV average

    # PROMOTION PATH (factor program Stage 2→3). Monitor factors (IVOL/BAB/AMIHUD) the PM has
    # chosen to NEUTRALIZE — they then enter the materialized loadings, the optimizer's hard
    # factor constraints, and the risk summary, exactly like the FF5+MOM style factors. EMPTY by
    # default: monitor-only, optimizer unchanged. A factor must exist in the panel (built by
    # build_neptune_factors) before promoting it. Set via NEPTUNE_PROMOTED_FACTORS="BAB,IVOL".
    promoted_factors: list[str] = Field(default_factory=list)

    # Covariance-based hedge objective: when on (and the factor panel is loaded), the optimizer
    # minimizes the NET BOOK's variance via a factor-model covariance Σ = BᵀFB + D instead of the
    # diagonal diversification penalty. Pure risk reduction — no return view (CLAUDE.md §5). Falls
    # back to the diagonal objective automatically when the panel isn't available.
    covariance_objective: bool = True

    @field_validator("promoted_factors", mode="before")
    @classmethod
    def _split_promoted(cls, v):
        """Accept a comma/space-separated string from the env (NEPTUNE_PROMOTED_FACTORS="BAB,IVOL")
        as well as a JSON/Python list."""
        if isinstance(v, str):
            return [t for t in v.replace(",", " ").split() if t]
        return v

    @property
    def promoted(self) -> tuple[str, ...]:
        """Normalized promoted-factor tuple (upper-cased, de-duplicated, order-preserving)."""
        seen: dict[str, None] = {}
        for f in self.promoted_factors:
            seen.setdefault(f.strip().upper(), None)
        return tuple(seen)

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
