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
    # Neptune-owned macro-data DB (rates/credit + economic series); see docs/macro_data.md.
    # Falls back to database_url like the others.
    macro_database_url: str | None = None

    # External data-provider API keys (env/.env fallback; the UI-stored key in
    # provider_credentials takes precedence — see settings_store.credentials). The same FRED
    # key serves ALFRED (vintages). Get one free at https://fredaccount.stlouisfed.org/apikeys
    fred_api_key: str | None = None
    # Read-only link to the shared securities universe. None = not configured (the
    # synthetic CATALOG/universe is used instead, e.g. tests and offline dev).
    universe_database_url: str | None = None

    # The API server's own bind address (uvicorn) — the single source of truth run.py's dev
    # launcher and the Electron main process read to know where Neptune's API actually
    # listens. Neptune's allocation is 8000 (unchanged); this just makes it configurable
    # instead of hardcoded across launcher scripts, per-app port allocation for the
    # upcoming identity service (OIDC loopback callbacks, JWT aud, CORS are all
    # scheme+host+port-specific, so each Iridium-suite app needs a stable one).
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # --- Quant Engine constants (HARD invariants; see CLAUDE.md) ---
    ewma_lambda: float = 0.94          # EWMA decay for beta estimation
    beta_lookback: int = 252           # trading days in the estimation window
    beta_tol: float = 0.05             # |net portfolio beta| hard constraint
    benchmark: str = "SPY"             # market-beta benchmark (NEPTUNE_BENCHMARK)
    # Always-on price refresh: the server re-pulls latest prices every N minutes (0 = off).
    # Runtime-overridable + persisted via /settings/price-refresh; this is the default.
    price_refresh_minutes: int = 10
    # Always-on factor-panel refresh (Ken French FF5+MOM): much lower frequency than prices
    # since the panel publishes far less often. Runtime-overridable + persisted via
    # /settings/factor-refresh; this is the default (once/day, 0 = off).
    factor_refresh_minutes: int = 1440
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

    @property
    def macro_url(self) -> str:
        """Resolved URL for Neptune's macro-data database (falls back to ``database_url``)."""
        return self.macro_database_url or self.database_url


# Read the bare (un-prefixed) connection env vars for convenience/compat, so deployments
# can set DATABASE_URL / PORTFOLIO_DATABASE_URL / SECURITIES_DATABASE_URL /
# UNIVERSE_DATABASE_URL / API_HOST / API_PORT without the NEPTUNE_ prefix. (The prefixed
# forms — NEPTUNE_API_HOST, NEPTUNE_API_PORT, etc. — already work natively via
# ``env_prefix`` above; this dict only adds the unprefixed fallback on top.)
_URL_ENV = {
    "database_url": "DATABASE_URL",
    "portfolio_database_url": "PORTFOLIO_DATABASE_URL",
    "securities_database_url": "SECURITIES_DATABASE_URL",
    "macro_database_url": "MACRO_DATABASE_URL",
    "universe_database_url": "UNIVERSE_DATABASE_URL",
    "api_host": "API_HOST",
    "api_port": "API_PORT",
}

# Bare (un-prefixed) env names for provider secrets, read the same way as the URLs above so a
# deployment can set FRED_API_KEY without the NEPTUNE_ prefix.
_SECRET_ENV = {
    "fred_api_key": "FRED_API_KEY",
}


def _dotenv_values(path: str = ".env") -> dict[str, str]:
    """Minimal ``.env`` parser for the bare connection/API names. pydantic-settings only
    reads ``.env`` for the ``NEPTUNE_``-prefixed fields, so without this a ``.env`` copied
    from ``.env.example`` (which uses bare ``DATABASE_URL=…``) would be silently ignored and
    the app would keep using the in-memory SQLite default. No extra dependency — just the
    handful of keys in ``_URL_ENV``/``_SECRET_ENV`` we care about.

    Read as ``utf-8-sig`` so a BOM is stripped: PowerShell's ``Set-Content -Encoding UTF8``
    writes a BOM, which would otherwise corrupt the FIRST key (e.g. ``\\ufeffPORTFOLIO_DATABASE_URL``)
    and make that one var silently fall back to the SQLite default."""
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip().lstrip("﻿")  # belt-and-suspenders: strip a stray BOM too
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def write_env_var(env_name: str, value: str, path: str = ".env") -> None:
    """Persist ``env_name=value`` into the ``.env`` file, so it survives a later restart.

    Used after a live re-point (see ``db.runtime.repoint_portfolio``) — the in-process
    engine swap takes effect immediately, but without this the change would be lost the
    next time the process starts, reverting silently to whatever ``.env``/the shell still
    say. Updates the FIRST existing line for either the bare name or its ``NEPTUNE_``-
    prefixed form (matching whichever convention the file already uses); appends a new
    bare line — the convention ``.env.example`` uses — only if neither is present. Every
    other line is left untouched. Writes atomically (temp file + ``os.replace``) so a
    crash mid-write can never leave a corrupt ``.env`` behind. Raises ``OSError`` if the
    file can't be written (e.g. read-only filesystem) — the caller decides whether that's
    fatal; a failed write here never affects the already-applied in-memory change.
    """
    prefixed = f"NEPTUNE_{env_name}"
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()

    def key_of(line: str) -> str | None:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            return None
        return s.partition("=")[0].strip()

    for i, line in enumerate(lines):
        k = key_of(line)
        if k in (env_name, prefixed):
            lines[i] = f"{k}={value}"
            break
    else:
        lines.append(f"{env_name}={value}")

    tmp_path = f"{path}.tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp_path, path)


def get_settings() -> Settings:
    """Resolve settings with precedence: shell environment > ``.env`` file > defaults.
    Shell-first keeps tests (which set ``DATABASE_URL`` in ``os.environ``) authoritative."""
    dotenv = _dotenv_values()
    overrides: dict[str, str] = {}
    for field, env in {**_URL_ENV, **_SECRET_ENV}.items():
        if env in os.environ:
            overrides[field] = os.environ[env]
        elif env in dotenv:
            overrides[field] = dotenv[env]
    return Settings(**overrides)


settings = get_settings()
