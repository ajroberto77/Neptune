# Neptune — Quantitative Risk Intelligence Platform

**Iridium Capital Management**

> **Build status: Not started — greenfield.** Nothing in this document has been
> implemented yet. This is the specification and build plan for a project being
> started from scratch. Every task is at "Not Started." No Neptune code,
> schema, infrastructure, or UI exists; assume an empty repository.

---

## 1. Overview

### Project description

Neptune is Iridium's quantitative risk intelligence platform — a cloud-based system that maintains real-time beta neutrality on the long book, decomposes factor exposures, optimises the systematic short book via a universe-scanning MIQP solver, and tracks live P&L across all positions. The Optimizer Engine is Neptune's core proprietary module: a two-pass solver that first accounts for discretionary shorts and then selects and sizes the systematic short book to neutralise residual beta and factor risk.

### Iridium product suite

| Project | Type | Description | Status |
|---|---|---|---|
| CATO | Desktop (Electron) | SEC EDGAR activism intelligence, insider trading, proxy governance data | Active |
| Mercury | Web / Cloud | EDGAR XBRL financial data connector; fundamental metrics for the investable universe | Active |
| Neptune | Cloud | Quantitative risk intelligence and portfolio management platform | **Planned — not started** |
| Tacitus | Cloud (SaaS) | Quantitative activist screener and Persona Engine | Planning |

### Neptune objectives

| # | Objective | Description |
|---|---|---|
| 1 | Beta Neutrality Engine | Maintain real-time market neutrality on the long book via continuous OLS/Vasicek beta estimation (see `CLAUDE.md` §4 — revised from the original EWMA/Dimson design below after it produced unstable, sign-flipped betas in production) and systematic short book construction. |
| 2 | Universe-Scanning Optimizer | Scan the Russell 3000 shortable universe and solve a two-pass MIQP to select and size the systematic short book that minimises residual beta and factor tracking error. |
| 3 | Multi-Factor Exposure Monitor | Decompose portfolio exposures across five factors (Market, Size, Value, Momentum, Sector) and flag breaches against per-portfolio limits. |
| 4 | Live P&L Engine | Mark-to-market P&L across all positions with FIFO/AVCO/Specific-Lot cost basis tracking, split by Long / Systematic Short / Discretionary Short. |
| 5 | Paper Portfolio Engine | Clone any live portfolio, diverge from it, and run the full risk and hedging stack on paper — enabling PM experimentation and analyst idea evaluation. |
| 6 | Book of Books | Aggregate risk view across all live portfolios: notional-weighted firm-level beta, factor exposures, and cross-portfolio netting. |
| 7 | CATO / Tacitus Integration | Receive short candidate flags from Tacitus screener; supply risk overlay (borrow cost, short interest, liquidity) back to Tacitus and CATO. |

---

## 2. Build Roadmap

Phased buildout plan. All phases subject to revision. Every task below is **Not Started**.

Each task is listed as: **Task name** · workstream · priority · effort · _depends on_, followed by its description and any notes.

### Phase 0 — Foundation

**Define cloud stack** · Architecture · Critical · 2w · _depends on: none_
Select cloud provider (AWS/GCP/Azure), database engine (PostgreSQL + TimescaleDB), task queue (Celery + Redis), and API framework (FastAPI). Define dev/staging/prod environments. _Note: recommend AWS + RDS + ElastiCache._

**PostgreSQL + TimescaleDB schema** · Architecture · Critical · 1w · _depends on: cloud stack selected_
Design the Neptune schema: `portfolios`, `positions`, `lots`, `beta_snapshots` (hypertable), `factor_exposures`, `optimizer_runs`, `optimizer_positions`, `pl_snapshots` (hypertable), `firm_snapshots` (hypertable), `universe_members`, `universe_filters`, `ideas`, `audit_log`.

**Auth & RBAC design** · Architecture · Critical · 1w · _depends on: none_
JWT + OAuth2 with four roles: CIO (Book of Books default), PM (assigned portfolios), Analyst (paper portfolios), Admin. All endpoints gated via `require_role()` dependency.

**requirements.txt & Docker** · Architecture · Critical · 1w · _depends on: none_
Pin all backend dependencies in `requirements.txt` (FastAPI, SQLAlchemy, cvxpy, Celery, yfinance, etc.). Docker Compose for local dev; Windows-compatible (`requirements.txt` install, not `pyproject.toml`). _Note: use requirements.txt — avoids setuptools.backends.legacy errors on Windows._

**Tacitus connector — receive** · Integrations · High · 1w · _depends on: schema complete_
Build inbound API endpoint to receive short candidate flags and conviction scores from Tacitus. Store in `neptune.tacitus_signals` table with `run_id` and timestamp. _Note: define data contract jointly with Tacitus team._

**CATO connector — risk overlay push** · Integrations · Medium · 1w · _depends on: schema complete_
Build outbound push to CATO: Neptune sends per-name risk overlay (borrow cost, short interest %, liquidity score, volatility) for any ticker CATO flags as an activism candidate.

### Phase 1 — Beta Engine

**yfinance price ingestion** · Market Data · Critical · 2w · _depends on: Phase 0 complete_
Build Celery task for daily price ingestion via yfinance. Store OHLCV in TimescaleDB. Stale price circuit breaker: 10s liquid, 60s OTC. Phase 2+: Bloomberg B-PIPE/SAPI. _Note: synthetic prices sufficient for engine/UI dev; Finnhub/Alpaca for live feed testing._

**EWMA beta computation** · Beta Engine · Critical · 2w · _depends on: price ingestion live_
252-day exponentially weighted regression vs SPY. Lambda = 0.94. Run nightly via Celery after market close. Store per-position per-portfolio in `beta_snapshots` hypertable. _Note: default beta method._

**Vasicek shrinkage** · Beta Engine · High · 1w · _depends on: EWMA live_
β_vasicek = w·β_ewma + (1−w)·1.0, where w is inversely proportional to estimation variance. Bloomberg "Adjusted Beta" equivalent.

**Dimson adjustment** · Beta Engine · High · 1w · _depends on: EWMA live_
Sum of lagged/lead market return coefficients for k = −1, 0, +1. Critical for illiquid activist targets with asynchronous price responses.

**Forward beta override** · Beta Engine · High · 0.5w · _depends on: EWMA live_
PM-set per-position forward beta override. Overrides all model outputs for that position in the optimizer. Logged to `audit_log` with before/after state.

**Ken French factor loading** · Factor Data · Critical · 2w · _depends on: price ingestion live_
Pull FF5 + Momentum factor data from Ken French Data Library. Compute per-security factor loadings using rolling 60-day regression. Store in `factor_exposures` table. _Note: factor data source is the Kenneth French Data Library (free)._

### Phase 2 — Universe & Optimizer

**Universe construction** · Universe · Critical · 2w · _depends on: Phase 1 complete_
Build investable shortable universe from Russell 3000. Apply configurable filters: `min_adv_30d` ($10M), `min_market_cap` ($500M), `max_borrow_cost_bps` (50), `max_short_interest_pct` (20%). Hard-exclude any ticker held long in any live portfolio. _Note: filter changes require CIO approval before taking effect._

**Universe filter change approval flow** · Universe · High · 1w · _depends on: universe construction_
PM proposes filter change → stored as pending in `universe_filter_changes` → CIO approves/rejects → approved changes take effect from next scheduled run. Full audit trail.

**Two-pass QP optimizer (unconstrained)** · Optimizer · Critical · 3w · _depends on: universe live, factor loadings_
Pass 1: compute residual beta and factor exposures after long book + discretionary shorts. Pass 2: unconstrained QP to select and size systematic short book minimising tracking error. Use cvxpy + CLARABEL. _Note: normalize dollar quantities by long_aum before passing to solver for numerical conditioning._

**MIQP capped runs (complexity frontier)** · Optimizer · High · 2w · _depends on: QP optimizer live_
Extension: add binary selection variables and position count cap (N≤50/20/10) to produce capped MIQP runs. Use cvxpy GLPK_MI. Compute complexity-quality frontier: tracking error, net beta, factor breaches at each cap level. _Note: greedy+QP approximation as fallback if GLPK_MI unavailable._

**Sector concentration flagging** · Optimizer · High · 1w · _depends on: QP optimizer live_
Flag if any GICS sector exceeds 40% of total short notional (soft warning on unconstrained, hard constraint option on capped runs). Store sector breakdown per `optimizer_run`.

**Hedge recommendation & approval flow** · Optimizer · Critical · 2w · _depends on: QP optimizer live_
Optimizer output stored as `hedge_recommendations` with status pending. PM approves/rejects per run (or per position). CIO can approve discretionary shorts. Nightly Celery schedule + on-demand PM trigger. _Note: API always returns cached results — no synchronous optimization in request handlers._

### Phase 3 — P&L Engine

**Position Manager CRUD** · P&L · Critical · 2w · _depends on: Phase 2 complete_
FastAPI endpoints for position entry, sizing, and lot tracking. Support FIFO (default), AVCO, and Specific Lot cost basis per position. Track every lot with entry price, date, quantity, and cost basis method.

**Live P&L computation** · P&L · Critical · 3w · _depends on: Position Manager_
Four simultaneous P&L dimensions per position: Day P&L, Total (ITD) P&L, Unrealised P&L, Realised P&L. FX P&L always tracked separately. Reporting split by Long / Systematic Short / Discretionary Short.

**WebSocket price pipeline** · P&L · Critical · 2w · _depends on: Position Manager_
Price feed → Redis pub/sub → Position Calculator → TimescaleDB → WebSocket push to frontend. Target <400ms end-to-end. Stale price circuit breaker enforced at calculator layer. _Note: Phase 1 yfinance polling; Phase 2 Bloomberg streaming._

**Multi-currency support** · P&L · Medium · 1w · _depends on: Live P&L_
FX P&L always tracked as a separate column. All P&L reported in portfolio base currency (default USD). FX-adjusted beta calculations deferred to Phase 2+. _Note: initial build is USD-denominated US equities only._

### Phase 4 — Live Dashboard

**React scaffold (Fluent 2 + Deep Ocean)** · UI · Critical · 2w · _depends on: Phase 3 complete_
React 18 + TypeScript + Tailwind CSS + `@fluentui/react-components`. Design system: Fluent 2 layout (module tabs top, sidebar sub-nav, gear icon for settings). Color palette: Deep Ocean (bg `#0a0e1a`, accent `#3b82f6`, secondary `#818cf8`). Typography: Outfit (display), Inter (body), IBM Plex Mono (numerics). _Note: reference CATO shell architecture for layout grammar._

**Live blotter UI** · UI · Critical · 3w · _depends on: React scaffold_
Blotter tab with Long Book / Systematic Short Book / Discretionary Short Book sub-panels. Columns: Ticker, Name, Beta, Notional, Day P&L, Total P&L, Unrealised P&L, Realised P&L, Cost Basis Method. IBM Plex Mono for all numeric columns.

**Risk dashboard** · UI · Critical · 3w · _depends on: React scaffold, Optimizer_
Risk tab: Beta monitor (net beta gauge, tolerance band), factor exposure table (5 factors with OK/WATCH/BREACH badges), optimizer output panel (frontier chart, unconstrained vs capped comparison), universe filter status.

**Hedge approval UI** · UI · High · 2w · _depends on: React scaffold, Optimizer_
Optimizer tab: PM reviews unconstrained and capped optimizer runs side-by-side. Approve/reject individual positions or full runs. Pending approvals count displayed in top bar. CIO override available.

### Phase 5 — Paper Portfolios & Book of Books

**Portfolio clone endpoint** · Paper Engine · Critical · 1w · _depends on: Phase 4 complete_
`POST /portfolios/{id}/clone`: snapshot all longs, all discretionary shorts, and current systematic short book at a timestamp. Paper flag (`is_paper=true`) propagates through all downstream tables. _Note: paper portfolios never route orders anywhere._

**Paper portfolio optimizer integration** · Paper Engine · Critical · 2w · _depends on: clone endpoint_
Optimizer re-runs automatically on the paper book after every analyst position change. Analyst cannot directly modify the systematic short book — only indirectly via long book changes. This is the learning mechanism.

**P&L divergence view** · Paper Engine · High · 2w · _depends on: paper optimizer_
Track paper P&L vs live from clone date. Per-position contribution and factor attribution. PM can see how analyst decisions would have performed vs the live book.

**Multi-portfolio aggregation engine** · Book of Books · Critical · 2w · _depends on: Phase 4 complete_
Firm-level beta: β_firm = Σ(N_i × β_i) / Σ(N_long) across all live portfolios. Notional-weighted factor exposures. Cross-portfolio netting for same-ticker long/short across portfolios. _Note: paper portfolios excluded from firm-level aggregation by default._

**Book of Books dashboard UI** · Book of Books · Critical · 3w · _depends on: aggregation engine_
CIO default landing page: Portfolio Matrix (every live portfolio as row; columns: Net Beta, Beta Status, Long Notional, Systematic Short Notional, Discretionary Short Notional, Hedge Ratio, 5 factor exposures). Click row to drill into portfolio. Firm Summary Strip persistent in header.

---

## 3. Risk & Factor Model

### Beta estimation methods

> **Reconciled with `CLAUDE.md` §4 (PM-approved revision).** The EWMA + Dimson design below
> was the original Phase 1 plan. In production it weighted only ~32 effective observations,
> and its collinear lead/lag terms produced unstable, sometimes sign-flipped betas (a
> high-beta tech name estimated negative), which broke the hedge. It was replaced with a
> plain 252-day OLS regression, Vasicek shrinkage still last. Rows below are kept for
> history; **EWMA** and **Dimson Adjustment** are marked superseded/removed rather than
> deleted, so past decisions stay traceable.

| Method | Description | Formula / Parameters | Data Source | Phase | Notes |
|---|---|---|---|---|---|
| OLS Regression | Plain regression of stock returns on market returns. Raw β = the market slope; also yields the estimation variance σ²_OLS. | 252-day (≈1-year) lookback, unweighted | yfinance / Bloomberg | Phase 1 | **Current method** — replaced EWMA below |
| ~~EWMA~~ | ~~Exponentially weighted regression vs SPY/benchmark. Fast to respond to regime changes.~~ | ~~λ=0.94, 252-day lookback~~ | yfinance / Bloomberg | Phase 1 | **Superseded** — see note above |
| Vasicek Shrinkage | β_vasicek = w·β_raw + (1−w)·1.0, w inversely proportional to estimation variance. | w = σ²_prior / (σ²_prior + σ²_OLS) | Derived from raw OLS beta | Phase 1 | Bloomberg "Adjusted Beta" equivalent; always the final model step |
| ~~Dimson Adjustment~~ | ~~Sum of lagged and leading market return coefficients. Handles illiquid stocks.~~ | ~~β_dimson = Σβ_lag(k) for k=−1, 0, +1~~ | yfinance / Bloomberg | Phase 1 | **Removed** — see note above |
| Forward Override | PM-set manual beta override per position. Overrides all model outputs for that position. | PM input — stored in `positions.forward_beta` | Manual entry | Phase 1 | Logged to audit_log with before/after state |
| Kalman Filter | Beta as a latent state variable updated with each new return observation. | State-space model; Kalman gain auto-tunes | yfinance / Bloomberg | Phase 3+ | Current best practice — deferred post-Phase 1 |

### Five-factor model

| Factor | Description | Data Source | Direction | Portfolio Limit (default) | Firm Limit | Notes |
|---|---|---|---|---|---|---|
| Market (Rm−Rf) | Sensitivity to broad market returns (benchmark: SPY). | Ken French / yfinance | Minimise abs exposure | Net β within ±0.050 | ±0.030 | Primary optimizer objective |
| Size (SMB) | Small-minus-big: exposure to size premium. | Ken French Data Library | Neutral | ±0.20 | ±0.15 | |
| Value (HML) | High-minus-low: book-to-market exposure. | Ken French Data Library | Neutral | ±0.20 | ±0.15 | |
| Momentum (MOM) | Prior 12-month return momentum exposure. | Ken French Data Library | Neutral | ±0.20 | ±0.15 | |
| Sector (GICS) | GICS sector concentration in the short book. | GICS classifications | Max 40% per sector | 40% of short notional | 35% | Soft warning on unconstrained; hard constraint on capped runs |

---

## 4. Optimizer Specification

Two-pass MIQP universe-scanning optimizer · cvxpy + CLARABEL / GLPK_MI.

### Two-pass optimizer logic

**Pass 1 — Compute residual exposures.** Sum dollar-beta and factor exposures across the long book. Add discretionary short contributions (their notionals are negative, so they reduce the residual burden). Residual = what the systematic short book must neutralise.

**Pass 2 — Optimize systematic short book.** Universe-scanning QP/MIQP: select securities from the filtered shortable universe and size them to minimise tracking error vs. residual. Primary output: unconstrained optimal book (no position count limit). Secondary: capped runs at N≤50/20/10 for complexity-quality frontier.

**Secondary — Discretionary suggestions.** Flag discretionary shorts where a resize would improve hedge efficiency by >10%. Stored as `recommendation_type = DISCRETIONARY_SUGGESTION`. Never auto-applied. PM sees these as advisory annotations on their positions.

### Optimizer constraints

| Constraint | Type | Formula / Rule | Default Value | Configurable | Applies To | Notes |
|---|---|---|---|---|---|---|
| Beta neutrality | Hard | \|residual_beta_dollar − Σ(w_i × β_i)\| ≤ tol × long_aum | tol = 0.050 | Yes — per portfolio | All runs | Primary optimization target |
| Factor limits | Hard | \|residual_factor[f] − Σ(w_i × load_i[f])\| ≤ limit[f] | ±0.20 per factor | Yes — per factor | All runs | Five factors independently |
| Position size floor | Hard | w_i ≥ 0 | 0 (no short floor) | No | All runs | Positions are notional (positive = short notional) |
| Position size ceiling | Hard | w_i ≤ max_pos_wt × long_aum | 15% of long AUM | Yes | All runs | |
| Position count cap | MIQP | z_i ∈ {0,1}; w_i ≤ z_i × max_pos_wt × long_aum; Σz_i ≤ N_cap | Unconstrained (no cap) | Yes — PM selects N | Capped runs only | Makes problem MIQP; use GLPK_MI solver |
| Sector concentration | Soft | Any GICS sector ≤ 40% of total short notional | 40% flag threshold | Yes | All runs | Hard constraint option on capped runs if PM enables |
| Long-book exclusion | Hard | No ticker held long in any live portfolio | Always enforced | No | All runs | Enforced by runtime join — not static list |
| Borrow constraint | Hard | max_borrow_cost_bps filter applied in universe construction | 50 bps | Yes — CIO approval | Universe filter | Applied pre-optimization in universe filter step |

---

## 5. Integration Specifications

Data flows between Neptune, CATO, Mercury, and Tacitus. (← IN = into Neptune; → OUT = out of Neptune.)

### CATO

| Direction | Data Element | Content | Mechanism | Usage in Neptune |
|---|---|---|---|---|
| ← IN | Short opportunity flags | Named companies flagged by CATO activism campaigns as potential re-targets; board composition changes | Weekly ETL from CATO `activism.db` | Supplementary signal for discretionary short sourcing |
| ← IN | Governance signals | Board tenure, classified board, poison pill, say-on-pay outcomes — for cross-referencing systematic short candidates | Weekly ETL from CATO `proxy.db` | |
| → OUT | Risk overlay per ticker | For any ticker active in a CATO campaign: borrow cost %, short interest % of float, liquidity score, 30-day volatility | Neptune API → CATO API or direct DB | Enables CATO to display risk context on company pages |
| → OUT | Short book composition | Current systematic short book tickers and notionals — CATO can flag if Neptune is short a name that is also an active campaign target | Post-run push | Cross-system conflict detection |

### Tacitus

| Direction | Data Element | Content | Mechanism | Usage in Neptune |
|---|---|---|---|---|
| ← IN | Short candidate list | Companies scoring high on inverted Tacitus conviction score: ticker, composite score (inverted), signal breakdown, Persona match | Weekly pull from Tacitus API post-run | Short idea sourcing and systematic book population |
| ← IN | Long conviction scores | Full universe composite scores from Tacitus. Usable as factor inputs; high-conviction longs may warrant different beta targets. | Weekly pull | |
| ← IN | Activist catalyst flags | Names where Tacitus detects rising activist score trend — Neptune can pre-position or flag for risk review. | Weekly pull | Forward-looking signal — use cautiously |
| → OUT | Risk overlay metrics | Borrow cost %, short interest % of float, liquidity score, volatility — applied by Tacitus as risk-adjustment multiplier to final scores | Weekly push to Tacitus API post-run | |
| → OUT | Position sizing context | Neptune's portfolio constraint view — ensures Tacitus short flags are sized within actual portfolio limits | On-demand API call from Tacitus | |

### Mercury

| Direction | Data Element | Content | Mechanism | Usage in Neptune |
|---|---|---|---|---|
| ← IN | XBRL financial signals | Revenue, EBITDA, EBITDA margin, ROIC, FCF, net debt, EV, P/B, EV/EBITDA — current and 8-quarter history. Used for factor loading computation. | Weekly ETL from Mercury XBRL pipeline | Mercury is the primary source of financial data for the factor model |
| ← IN | Peer benchmark data | Sector × cap-tier peer medians pre-computed in Mercury. Used for factor normalization. | Weekly ETL | |
| ← IN | TSR data | 1Y and 3Y total shareholder return vs peer median. Used as momentum factor input. | Weekly ETL | |
| → OUT | Neptune risk overlay | Per-name risk metrics available in Mercury company views: beta estimate, factor loadings, short interest overlay. | Post-run push to Mercury enrichment API | |

---

## 6. Roles, Access & System Invariants

### Roles & access control

| Role | Default View | Portfolio Access | Can Approve | Cannot Do | Notes |
|---|---|---|---|---|---|
| CIO | Book of Books | All portfolios (read) | Hedge recommendations; universe filter changes; discretionary shorts | Cannot enter positions directly | Book of Books is default landing page |
| PM | Assigned portfolios | Full access to assigned only | Analyst ideas; discretionary shorts within mandate; forward beta overrides | Cannot modify another PM's portfolio | Can create and assign paper portfolios to analysts |
| Analyst | Paper portfolios | Assigned paper portfolios only | Nothing — submit ideas for PM review | Cannot modify live positions; cannot approve anything | Can manage own paper book within PM-defined permissions |
| Admin | All | Full access — all portfolios | All | N/A | Portfolio and user configuration; system-level settings |

### System invariants — Neptune never does these

| # | Invariant | Rationale |
|---|---|---|
| I-01 | Executes trades autonomously | Neptune recommends and sizes. Humans approve every position before execution. The optimizer output is a proposal, not a command. |
| I-02 | Uses stale prices silently | Stale price circuit breaker enforced at the Position Calculator layer (10s liquid, 60s OTC). Any stale price triggers an alert; affected P&L cells are flagged as stale, not silently recomputed. |
| I-03 | Conflates systematic and discretionary shorts | The `short_type` field flows through every layer: positions, P&L, factor exposures, optimizer runs. These two books are never aggregated into a single "short book" figure in any report. |
| I-04 | Lets the optimizer modify discretionary positions | Pass 1 reads discretionary positions as inputs. The optimizer may suggest resizes (DISCRETIONARY_SUGGESTION), but cannot apply them. The PM acts on suggestions explicitly. |
| I-05 | Shorts a name held long in any live portfolio | Hard exclusion enforced by runtime join against all live portfolios — not a static list. Conflict flagged immediately if a long position is opened in a current systematic short. |
| I-06 | Applies unapproved universe filter changes | Filter change requests are stored as pending. The existing config remains active until the CIO explicitly approves. Old and new configs are timestamped in `universe_filter_changes`. |
| I-07 | Overwrites financial history | All P&L, beta, and optimizer tables are append-only. Corrections are new rows with a `corrected_by` reference. The `audit_log` captures every state transition with before/after JSON. |
| I-08 | Routes paper portfolio orders anywhere | Paper portfolios run the complete Neptune stack but are flagged `is_paper=True` throughout. No execution pathway exists; paper positions cannot be submitted to any broker or order management system. |
