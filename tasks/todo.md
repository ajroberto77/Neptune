# Neptune — Build Plan (tasks/todo.md)

Phased build plan in roadmap module order. The **first build is a runnable vertical
slice** (marked 🟢 SLICE). Later items are listed but unchecked. Nothing is marked done
until its tests pass.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done (tests pass) · 🟢 SLICE = in
first vertical slice · 🔵 LATER = deferred.

---

## Phase 0 — Foundation (slice scaffolding)

- [x] 🟢 `src/` layout package scaffolding (`pyproject.toml`, `src/neptune/`)
- [x] 🟢 `requirements.txt` (pinned, Windows-compatible) + `.env.example`
- [x] 🟢 `docker-compose.yml` (postgres:16 + redis) — canonical DB target
- [x] 🟢 `config.py` (pydantic-settings; `DATABASE_URL`, `LAMBDA=0.94`, `BETA_TOL=0.05`)
- [ ] 🔵 Alembic migrations (Postgres dialect) for the full schema
- [ ] 🔵 Tacitus / CATO / Mercury connectors

### Unified identity & access (cross-platform) 🔵

> **Decision (2026-06-01):** platform users, roles, and access are NOT built per-app. A
> new firm-wide **`iridium_users`** database (platform-neutral name — shared by CATO,
> Mercury, and Neptune) is the single source of who the firm's people are and what they can
> do on each platform. This supersedes the roadmap's Neptune-local "Auth & RBAC" item
> (JWT+OAuth2, roles CIO/PM/Analyst/Admin) by hoisting it out of Neptune. Same shape as the
> `cato_securities` universe: one authoritative store; each platform reads it and projects
> the slice it needs; Neptune never mutates it (read-only input).
>
> **NOT to be confused with `cato_identity`** — that is a *universe of people as research
> subjects* (activists, insiders, board members, execs): domain/reference data, the
> people-analogue of `cato_securities`. It has nothing to do with platform login/access.
> `iridium_users` = the firm's employees and their entitlements; `cato_identity` = external
> people the platforms analyze.

- [ ] **`iridium_users` — shared platform-user store** (separate, cross-repo initiative; not
      Neptune-owned): authoritative firm users (CIO/PM/Analyst/Admin) + per-platform
      roles/entitlements across CATO/Mercury/Neptune. New database; no relation to
      `cato_identity`.
- [ ] **Neptune's slice** (what lands in this repo):
      - read users/roles **read-only** from `iridium_users` (adapter + projection, mirroring
        the universe adapter); Neptune's `people` table becomes that projection, not a second
        source of truth.
      - `require_role()` FastAPI dependency gating endpoints to CIO/PM/Analyst/Admin per the
        roadmap's Roles & access-control matrix.
      - authn integration point (SSO/OIDC token validation) — mechanism owned centrally,
        Neptune just validates.
- [ ] **Open questions to resolve before building:** (a) authentication mechanism (OIDC/SSO
      provider vs. home-grown JWT)? (b) where do per-platform entitlements live — central in
      `iridium_users`, or central roles with each app mapping its own permissions? (c)
      `iridium_users` schema + which repo/service owns it.

## Phase 0.5 — Data layer / three-DB topology 🟡 (in progress; see docs/data_architecture.md)

- [x] Design doc: three-DB split (cato_securities read-only universe, neptune_securities
      market data, neptune_portfolios app) + app-level identity projection link
- [x] Multi-engine wiring: portfolio/securities/universe URLs in `config.py` (each falls
      back to `DATABASE_URL`); separate `SecuritiesBase`/engine/session in `db/base.py`
      with backward-compatible `Base`/`engine`/`SessionLocal`/`init_db` aliases
- [x] `neptune_securities` schema (`src/neptune/securities/models.py`): `securities`
      projection (PK = `instrument_id`, the cato surrogate), `prices` (raw+adj, source-tagged,
      append-only), `dividends`, `corporate_actions` (split ratio / symbol change / delisting),
      `trading_calendar`; `test_securities.py` (7 tests; full suite 84 passed)
- [x] Multi-tenant ownership graph: `management_firm` (tenant) → `person` (PM/Analyst/
      CIO/Admin firm staff) + `investor_entity` (client) → `portfolio`(=book, with lead
      PM(s) via `book_managers`, co-PMs supported) → `position` (per-name `pm_id`/`analyst_id`,
      lead-PM fallback). Layered onto the existing `portfolios` table (no Portfolio→Book
      rename); ownership columns nullable so the synthetic slice still runs (`test_org.py`)
- [ ] **Tenant isolation ENFORCEMENT** (row-level firm scoping on reads/writes) — deferred
      to Auth & RBAC. The schema models the firm as isolation root, but queries are not yet
      firm-scoped; a caller with a portfolio_id can read it regardless of firm. Wire real
      enforcement (+ cross-firm isolation tests) with the auth layer.
- [x] Universe read-only adapter (`src/neptune/universe/`): `UniverseSource` protocol,
      `SqlUniverse` (text() SELECTs against cato_securities, keyed on `instrument_id`),
      `RecordedUniverse` (offline fixtures), and `sync_universe_projection` upserting the
      identity slice into `securities` (`test_universe.py`)
- [x] Configurable DB connections + Settings page: `settings_store` (write-only password,
      URL builder, env fallback), `db_connections` table, `/settings/connections` CRUD +
      `/test` + `/settings/universe/sync` endpoints, and a React Settings tab (all three
      roles; portfolio flagged bootstrap/restart-only). `test_settings.py` + `Settings.test.tsx`
- [x] `PriceProvider` protocol + yfinance impl + idempotent ingest: `securities/providers.py`
      (DTOs, `RecordedProvider`, lazy-import `YFinanceProvider`), `securities/adjust.py`
      (reproducible split/dividend back-adjustment of `adj_close`), `securities/ingest.py`
      (upsert keyed on `(instrument_id, …, source)`, I-07), `POST /securities/ingest`
      (resilient per-ticker errors; 503 when yfinance absent), Settings "Backfill prices"
      button. `test_ingest.py` + endpoint tests. Live backfill itself runs off-sandbox.
- [~] `MarketData` protocol + `DbMarketData`: protocol in `data/source.py`; `DbMarketData`
      (`data/db_market.py`) reads `adj_close` from `neptune_securities`, aligns every ticker
      to the benchmark (SPY) date index, recovers real `market_returns`/`ticker_returns`
      (tested: raw beta pipeline recovers ~1.2 from stored prices). Raw `close` for P&L
      marks; deterministic multi-source dedup; optional `lookback`. REMAINING to go live:
      flip the API constructor from `SyntheticMarketData` to `DbMarketData` (behind config).
- [x] Ken French factor ingestion: `factor_returns` table; `factor_providers.py`
      (`KenFrenchProvider` lazy `pandas_datareader`, `RecordedFactorProvider`);
      `factor_ingest.py` (idempotent `(factor,ts,source)` upsert, I-07); `DbMarketData`
      now serves the full `{MKT,SMB,HML,MOM}` panel aligned to the market (MKT-only until
      the panel is fully loaded); `POST /factors/ingest` + Settings "Backfill factors".
      `test_factor_ingest.py` (incl. engine recovering a 0.8 SMB loading from stored data).
- [ ] Alembic migration histories per Neptune DB; guarded TimescaleDB hypertable on `prices`

## Phase 1 — Position Manager 🟢

- [x] 🟢 Domain models: `Side`, `ShortType`, `Position` (with read-only `thesis`/`target`,
      optional `forward_beta`), `Portfolio`
- [x] 🟢 SQLAlchemy ORM models (`portfolios`, `positions`) — dialect-agnostic
- [x] 🟢 `PositionRepository` + `PositionService` CRUD
- [x] 🟢 Tests: CRUD via in-memory SQLite (`test_positions.py`)
- [x] 🟢 FIFO / AVCO / Specific-Lot cost basis tracking — pure P&L engine (`src/neptune/pnl/`),
      lots persisted (`lots` table), `reduce_position` matches lots + accrues realised
- [x] 🟢 Live P&L (Day / ITD / Unrealised / Realised) split by Long / Systematic /
      Discretionary (`risk/pnl.py`); `/positions` carries per-name P&L, `/pnl` the book split;
      blotter shows P&L columns (`test_pnl.py`, `test_api.py`, `Blotter.test.tsx`)
- [ ] 🔵 Multi-currency (FX P&L tracked separately); WebSocket price pipeline (<400ms)

## Phase 2 — Beta Engine 🟢

- [x] 🟢 `returns.py` — prices → returns, alignment
- [x] 🟢 Raw beta: EWMA-weighted regression (λ=0.94, 252d) with **Dimson lead/lag (k=−1,0,+1)
      folded into the same regression**; returns β_raw and σ²_OLS
- [x] 🟢 **Vasicek shrinkage as the FINAL model step** (`w = σ²_prior/(σ²_prior+σ²_OLS)`)
- [x] 🟢 Forward-beta override supersedes the pipeline
- [x] 🟢 Golden-number tests: noise-free fixture (β recovered exactly, w≈1) AND
      known-noise fixture (σ²_OLS>0 so 0<w<1, shrinkage exercised) — `test_beta.py`
- [x] 🟢 Live wiring: synthetic market data (`data/market.py`) → pipeline over the book
      (`risk/analytics.py`); forward override per position, Vasicek prior from the book
      cross-section; betas surfaced in `/positions` and `/risk` (`test_analytics.py`)
- [ ] 🔵 yfinance ingestion, nightly Celery schedule, `beta_snapshots` hypertable
- [ ] 🔵 Kalman filter beta (Phase 3+)

## Phase 3 — Factor Decomposition 🟢

- [x] 🟢 `factors.py` — rolling FF5 + Momentum regression → per-security loadings
- [x] 🟢 Portfolio factor exposure (notional-weighted) + OK/WATCH/BREACH classification
- [x] 🟢 Tests on synthetic factor-driven returns (`test_factors.py`)
- [x] 🟢 Live wiring: per-position loadings from returns; net style-factor exposure
      (SMB/HML/MOM) surfaced in `/risk`; live universe loadings feed the optimizer
- [ ] 🔵 Ken French Data Library ingestion; `factor_exposures` table

## Phase 4 — Hedge Optimizer 🟢

- [x] 🟢 Pass 1: residual dollar-beta + factor exposure (long book + discretionary shorts)
- [x] 🟢 Pass 2: cvxpy QP — minimize tracking error s.t. `|net β| ≤ 0.05`, factor limits
      (±0.20), position ceiling (15% long AUM); long-held tickers excluded
- [x] 🟢 Output is a *proposal* (pending), never executed; fails closed if infeasible
- [x] 🟢 Tests: `|net β| ≤ 0.05` asserted on golden portfolio (`test_optimizer.py`)
- [x] 🟢 Capped runs (greedy+QP per roadmap) + complexity-quality frontier
      (`optimize_hedge_capped`, `complexity_frontier`); `/hedge/frontier` endpoint + UI panel.
      Caps are adaptive — derived from the uncapped *natural support* so the frontier shows a
      real trade-off on any book size (fixed N≤10/20/50 degenerate on a small book)
- [x] 🟢 Sector concentration flagging — soft warning on the proposal, PM-adjustable limit
      (default 0.30) via the `sector_limit` query param + GUI input (`SectorPanel`); breakdown
      and OK/BREACH badges in the dashboard
- [ ] 🔵 True MIQP (binary vars) via a MIP solver when available
- [ ] 🔵 Optional hard sector constraint on capped runs; hedge approval flow

## Phase 5 — Live Dashboard 🟢 (basic, themed multi-tab)

- [x] 🟢 React 18 + Vite + TS + Tailwind scaffold, Deep Ocean theme
- [x] 🟢 Tabs: Live Blotter, Risk Dashboard (net-beta gauge + factor table + proposed
      short basket), Hedge Approval
- [x] 🟢 API client wired to FastAPI; Vitest component test
- [x] 🟢 FastAPI: positions CRUD, `/risk`, `/hedge/propose` (+ `test_api.py`)
- [ ] 🔵 WebSocket price pipeline (<400ms); full P&L columns; Electron shell (fast-follow)

## Phase 6 — Stress Engine 🟢

- [x] 🟢 Pure engine (`src/neptune/stress/`): factor-model scenario shocks (market +
      style-factor moves → P&L split by book), net dollar factor exposures, parametric
      VaR + Expected Shortfall (own normal-quantile, no SciPy dep), standard scenario library
- [x] 🟢 Risk Interface (`risk/stress.py`): exposures + factor covariance from market data
- [x] 🟢 `/stress` endpoint (standard + custom scenarios, VaR confidence/horizon) + Stress tab
- [x] 🟢 Tests: scenario golden numbers, VaR closed-form + sqrt-horizon scaling, hedged<unhedged
      (`test_stress.py`, `test_api.py`, `Stress.test.tsx`)
- [x] 🟢 Historical-simulation VaR (replay factor-return days) + Monte-Carlo VaR (draws from
      the factor covariance); `/stress` returns all three methods side by side, compared in
      the Stress tab
- [ ] 🔵 Correlated multi-factor scenario library; fat-tailed MC (Student-t)

## Phase 7 — Book of Books 🔵 (STUB in slice)

- [x] 🟢 Stub module (firm-level aggregation placeholder)
- [ ] 🔵 `β_firm = Σ(Nᵢβᵢ)/ΣN_long`, cross-portfolio netting, CIO portfolio matrix
