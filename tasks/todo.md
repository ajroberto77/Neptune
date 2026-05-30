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

## Phase 1 — Position Manager 🟢

- [x] 🟢 Domain models: `Side`, `ShortType`, `Position` (with read-only `thesis`/`target`,
      optional `forward_beta`), `Portfolio`
- [x] 🟢 SQLAlchemy ORM models (`portfolios`, `positions`) — dialect-agnostic
- [x] 🟢 `PositionRepository` + `PositionService` CRUD
- [x] 🟢 Tests: CRUD via in-memory SQLite (`test_positions.py`)
- [ ] 🔵 FIFO / AVCO / Specific-Lot cost basis tracking
- [ ] 🔵 Live P&L (Day / ITD / Unrealised / Realised), multi-currency

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
- [ ] 🔵 True MIQP (binary vars) via a MIP solver when available
- [ ] 🔵 Sector concentration flagging; hedge approval flow

## Phase 5 — Live Dashboard 🟢 (basic, themed multi-tab)

- [x] 🟢 React 18 + Vite + TS + Tailwind scaffold, Deep Ocean theme
- [x] 🟢 Tabs: Live Blotter, Risk Dashboard (net-beta gauge + factor table + proposed
      short basket), Hedge Approval
- [x] 🟢 API client wired to FastAPI; Vitest component test
- [x] 🟢 FastAPI: positions CRUD, `/risk`, `/hedge/propose` (+ `test_api.py`)
- [ ] 🔵 WebSocket price pipeline (<400ms); full P&L columns; Electron shell (fast-follow)

## Phase 6 — Stress Engine 🔵 (STUB in slice)

- [x] 🟢 Stub module raising `NotImplementedError` with roadmap reference
- [ ] 🔵 Scenario shocks, VaR/ES, factor-shock P&L

## Phase 7 — Book of Books 🔵 (STUB in slice)

- [x] 🟢 Stub module (firm-level aggregation placeholder)
- [ ] 🔵 `β_firm = Σ(Nᵢβᵢ)/ΣN_long`, cross-portfolio netting, CIO portfolio matrix
