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
- [ ] 🔵 Auth & RBAC (CIO/PM/Analyst/Admin)
- [ ] 🔵 Tacitus / CATO / Mercury connectors

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
- [ ] Multi-tenant `neptune_portfolios` schema: `investor_entity` (tenant) → `portfolio_manager`
      → `book` → `position` → `lot`; migrate existing portfolio models under it
      (OPEN: PM cardinality + Book-of-Books grouping — awaiting user)
- [ ] Universe read-only adapter (`src/neptune/universe/`) against cato_securities +
      projection sync into `securities` (keyed on `instrument_id`)
- [ ] `PriceProvider` protocol + yfinance impl + idempotent ingest (backfill run off-sandbox)
- [ ] `MarketData` protocol + `DbMarketData`; wire engine to stored prices behind config
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
