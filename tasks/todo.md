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
**Decisions (2026-06-01):**

- [ ] **Authentication — delegate via OIDC; never home-grown passwords.** All three apps are
      OIDC clients of the firm's corporate IdP (Entra ID if on M365, Google if on Workspace),
      or a self-hosted Keycloak if independence is wanted. Apps *validate* IdP-issued tokens
      against the provider's JWKS; no app stores passwords or issues its own login tokens.
- [ ] **Entitlements live in `iridium_users`, keyed by the OIDC `sub` claim** (stable subject
      id — not email, which can change). Division of labor: IdP = authentication + coarse
      identity; `iridium_users` = the rich, queryable authorization model (users ↔ per-platform
      roles ↔ entitlements, e.g. PM↔assigned books). Don't cram per-book entitlements into IdP
      groups.
- [ ] **A dedicated Identity & Access service owns `iridium_users`** (sole writer; owns schema +
      migrations; exposes an API for "roles/entitlements for this `sub` on platform X"; hosts
      the admin/provisioning UI; integrates with the IdP). Apps are **clients** — they call the
      API, never the raw schema. (Identity has writes + is security-sensitive, so NOT the pure
      shared-DB read pattern used for the read-only `cato_securities` universe.)
- [ ] **Neptune's client behavior:** validate the OIDC token (authn) → fetch roles/entitlements
      from the Identity service (authz) → gate endpoints with `require_role()` → keep a
      read-only local projection of its users for domain joins/attribution (`pm_id`/`analyst_id`).
- [ ] **Still to detail (cross-platform, not Neptune-only):** the `iridium_users` schema, the
      Identity service's API surface + repo/ownership, and which concrete IdP is adopted.

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
      blotter shows P&L columns (`test_pnl.py`, `test_api.py`, `Portfolio.test.tsx`)
- [x] Trade workflow (the single entry path for all executions): `record_trade` service
      method aggregates lots into the open position for a (ticker, book) or opens a new one,
      growing `notional` by executed value; closing scales `notional` down (full close → 0).
      `POST /portfolios/{id}/transactions` (book = LONG / DISCRETIONARY_SHORT /
      SYSTEMATIC_SHORT → side+short_type); `/positions` now exposes `id`+`quantity`. React
      **Trade** tab: record-transaction form + per-position Close. Systematic-short executions
      recordable here (origination stays with the optimizer; book tag keeps I-03).
      `test_trade.py` + `Trade.test.tsx`.
- [~] **DbMarketData flip** (fix for nonsense P&L/price on real tickers): `market_data_for()`
      picks real `DbMarketData` when the benchmark + EVERY portfolio ticker have stored prices,
      else synthetic (all-or-nothing per book — a real benchmark can't price a synthetic name;
      keeps the seeded demo + tests synthetic). Wired into `/positions`, `/risk`, `/pnl`.
      Benchmark **SPY** ingestable via `create_if_missing` (negative instrument_id, outside the
      universe); `NEPTUNE_BENCHMARK` configurable. `test_market_flip.py`.
      REMAINING: flip Stress; flip the hedge optimizer once a REAL shortable universe replaces
      the synthetic `live_universe` candidate set.
- [ ] Trade: **transaction fees → blended basis.** Add a fee input on the transaction; fold
      it into cost basis (correctly for longs AND shorts — fees always reduce P&L), and show
      the fee-inclusive blended basis. (Avg execution price already shown.)
- [x] **Live pricing — manual + always-on.** `POST /portfolios/{id}/refresh-prices` re-pulls
      a recent window for the book's tickers + benchmark (updates today's live bar). An
      always-on server-side scheduler (APScheduler, lazy/optional — degrades cleanly if absent)
      refreshes ALL tracked tickers + benchmark every N minutes even with no browser open.
      Interval is persisted (`app_settings` kv) + runtime-reschedulable via
      `GET/PUT /settings/price-refresh` (default `NEPTUNE_PRICE_REFRESH_MINUTES`=10, 0=off).
      The Portfolio tab reads/writes the server interval and re-displays on that cadence +
      "Refresh now". `test_price_scheduler.py`, `test_market_flip.py`, `Portfolio.test.tsx`.
      LATER: intraday last-price via yfinance fast_info (today it re-pulls daily bars).
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
- [x] Blotter shows per-name current price (from the active market-data source — synthetic
      until the `DbMarketData` flip, then the real backfilled mark).
- [ ] 🔵 Customizable blotter columns (user can add/remove/reorder columns, persisted).
      Deferred — current price landed first per user priority (2026-06-01).
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

## Trade-model rework (PM correction 2026-06-01) — see lessons.md

- [x] **Phase 1: book = portfolio; Buy/Sell netting; Longs/Shorts view.** Trade ticket is
      Ticker + Buy/Sell + qty/price/date; `book_trade` derives the effect from the holding
      (initiate/add/reduce/close/cover, flips through zero). Manual trades touch only the long
      or the *discretionary* short; systematic shorts stay optimizer-only (I-03). Portfolio tab
      → Longs/Shorts sections with a systematic/discretionary tag on shorts; flat positions
      hidden. Day P&L = Unrealized for same-day trades (pass `as_of`); realized P&L survives a
      full close. "unrealized"/"realized" spelling at the API + UI. (`test_trade.py`,
      `test_analytics.py`, `Portfolio.test.tsx`, `Trade.test.tsx`.)
- [ ] **Phase 2: Cash vs Swap instrument + swap financing.** Add an `instrument` field
      (needs a schema migration — no Alembic yet) and model swap funding/borrow accrual in the
      P&L engine. Deferred from Phase 1 to avoid breaking existing DBs.

## Factor program (PM, 2026-06) — see the factor-research brief

- [x] 🟢 Materialized daily **betas** (`betas` table, vectorized rolling OLS, one-pass sweep
      on ingest) and **style loadings** (`factor_loadings` table, `model` tag) — read by the
      hedge backtest + propose path instead of computing on the fly.
- [x] 🟢 **Build price-only differentiating factors — Stage 1 (construction).** IVOL, BAB,
      Amihud, and per-sector SECTOR_* daily return series (`quant/factor_build.py`, pure; lagged
      baskets, BAB beta-floor) persisted to `factor_returns` (source `neptune`) + a
      `factor_definitions` registry row (`risk/factor_build.py`). Quant-reviewed.
- [x] 🟢 **Stage 2 (monitor) — PM decision: REPORT-ONLY, not neutralized.** IVOL/BAB/AMIHUD are
      monitored (net notional-weighted exposure) and per-sector net weight reported; the optimizer
      is UNTOUCHED and the sector concentration cap remains the sector control. `monitor_report`
      (`risk/factor_build.py`) + `GET /portfolios/{id}/factor-monitor` + a Factor Monitor panel on
      the Risk tab. Neptune factors are deliberately kept OUT of `factor_returns()`/`FACTORS` so
      they never enter the optimizer's loadings regression. Factors rebuilt on price ingest.
- [x] 🟢 **Promotion path (config-gated, default OFF).** A PM lists factors in
      `settings.promoted_factors` (e.g. `NEPTUNE_PROMOTED_FACTORS="BAB,IVOL"`); promoted monitor
      factors then flow end-to-end — `DbMarketData.factor_returns()` merges them (NaN pre-basket
      history 0-filled so the cumsum rolling regression isn't poisoned), `rebuild_loadings`
      materializes their loadings, the optimizer NEUTRALIZES them via a threaded `hedge_factors`
      set (same hard `factor_limit`), and the risk summary classifies them. Empty default ⇒ the
      model is exactly FF5+MOM, optimizer untouched (asserted). Per-factor limits + a dedicated
      `model` tag are a later refinement.
- [x] 🟢 **Covariance hedge objective (net-book minimum variance).** Quant-recommended: the
      approvable proposal minimizes the NET BOOK's variance via a factor-model covariance
      Σ = BᵀFB + D (`factor_covariance` = PSD-projected F over [MKT,*hedge_factors];
      `Candidate.idio_var` = D from `residual_variance`). Objective `quad_form(net_full,F) + Σdᵢxᵢ²`
      has NO return term → pure risk reduction, never a view (§5); the hard |β|≤0.05 + factor
      limits are unchanged. Applied to HARD solves only (the soft frontier keeps tracking-error so
      it still answers "how many names reach neutral"). Auto-falls back to the diagonal
      diversification penalty when the panel isn't loaded (`covariance_objective` toggle, default on).
- [ ] 🔵 **Fundamentals feed (Mercury; ingest interim).** REQUIRED for:
      * **value-weighting** the sector factor (needs market cap = price × **shares outstanding**;
        until then the sector factor is **equal-weighted** — a documented proxy);
      * self-building **HML / RMW / CMA** (book equity, operating profitability, asset growth) —
        until then we INGEST these from Ken French;
      * **QMJ** (quality) factor.
      Interim: pull shares-outstanding / basic fundamentals (yfinance) to enable VW sector +
      characteristic scores; replace with the Mercury feed when available. Point-in-time
      correctness matters (avoid look-ahead) — Mercury > yfinance for that.
- [ ] 🔵 Ingest the Ken French FF5+MOM panel operationally + a **stale-panel guard** (Risk
      Interface) so the hedge never silently degrades to beta-only.
- [ ] 🔵 Loading-window decision: decouple the style-loading window from the 252-day market beta
      (research flagged 60 daily obs as thin for 6 slopes); consider shrinkage on the loadings.

## Macro-data database (PM, 2026-06-03) — see [`docs/macro_data.md`](../docs/macro_data.md)

> New **Neptune-owned** macro DB (rates/credit + economic data). Data-layer input only; the
> pure engine never imports it; human-facing regime/scenario context only (§1/§5). Decisions
> locked: backfill to 2000, full ALFRED vintage depth, transforms in the risk layer (raw stays
> canonical), enriched analytical type flags, EOD-only (CDX/MOVE deferred to a paid phase).

- [ ] **Phase 1a — schema + engine wiring (no network; fully testable).** `MacroBase` /
      `macro_engine` / `MacroSession` / `init_macro_db` in `db/base.py`; `macro_database_url`
      + `macro_url` + `MACRO_DATABASE_URL` alias in `config.py`; `MACRO` `ConnectionRole`.
      `src/neptune/macro/models.py`: `macro_series` registry (all §2 type flags),
      `macro_observations` (MARKET, flat), `macro_vintages` (ECON, point-in-time),
      `macro_release_calendar`. Mirror securities idioms (`BigIntPK`, source-tagged uniqueness).
- [ ] **Phase 1b — repository (revision logic; fully testable).** `macro/repository.py`:
      append-only insert (revision = new vintage row; benchmark restatement = bulk same
      `vintage_date`), MARKET upsert, and the three reads — `latest`, `first_print`, `as_of(d)`.
      Tests: as-of/latest/first-print correctness, look-ahead safety, layer purity (no `quant`
      imports). Build-vintages-by-diffing a latest-only source.
- [ ] **Phase 1c — Phase-1 indicator registry seed** (the §6 core catalog as data) +
      risk-layer transform helpers (YoY / DIFF→"new jobs" / annualized), guarded by the
      `value_type`/`stationarity` flags.
- [ ] 🔵 **Phase 1d — ingest (needs network + FRED/ALFRED keys).** FRED + ALFRED + Treasury
      clients; EOD daily MARKET pull (live bar excluded) + event-driven ECON-release pull;
      Celery schedule. Backfill to 2000.
- [ ] 🔵 Global (non-US) rates/FX; paid feeds (CDX/MOVE/intraday) if budgeted.
