# Neptune — Data Architecture & Ingestion Plan

> **Status: PROPOSAL under active discussion. No implementation yet.** This document
> describes how Neptune moves from a fully simulated dataset to real historical market
> data, persisted in its **own** databases, with the securities universe sourced
> read-only from the shared `cato_securities` entity database. Nothing here is built; it
> is the design to approve before coding. Items marked **OPEN** are still being decided
> with the user.

---

## 1. Where we are today (verified)

- **No data ingestion exists.** There are zero network calls in the codebase. All
  prices/returns are fabricated in-memory by `SyntheticMarketData`
  (`src/neptune/data/market.py`) from a seeded RNG and a hardcoded `CATALOG` of "true"
  betas/loadings. They are recomputed every request and never stored.
- **Positions persistence is real but ephemeral.** SQLAlchemy ORM
  (`portfolios`/`positions`/`lots`) behind a `DATABASE_URL`, but it defaults to
  **in-memory SQLite** and is **seeded with a demo "golden" portfolio** on startup.
- **Not yet present:** Postgres running (the user's cluster is on their machine, not
  reachable from this sandbox), Alembic, TimescaleDB, any price-history table, and the
  `psycopg`/`alembic`/`yfinance` packages.
- **This sandbox has no usable outbound to Yahoo.** PyPI and GitHub are reachable (200),
  but Yahoo Finance returns `403` even with a browser User-Agent. Therefore ingestion
  code is *built and tested here against recorded/mocked responses*, but the actual
  historical **backfill must run on a networked machine** (the user's local box or a
  deployment), pointed at the real databases.

So: the **engine math is real and tested; the data feeding it is a simulation.** This
plan replaces the data source without changing the engine.

## 2. Target topology — THREE databases (decided)

Strict separation of ownership. One PostgreSQL cluster can host all of these (they are
just separate `CREATE DATABASE` namespaces); they do **not** have to be on the same
server. There is **no engine-level conflict** with the user's existing cluster — the only
real caveats are (a) TimescaleDB is an *extension* that must be installed per-database if
we want hypertables, and (b) Postgres cannot do cross-*database* joins or foreign keys,
which is why the link is handled as described in §3.

| Database | Owner | Holds | Neptune access |
|----------|-------|-------|----------------|
| `cato_securities` | **User (universe)** | entities / tickers / companies — ground truth | **read-only** |
| `neptune_securities` | Neptune | prices, dividends, corporate actions, trading calendar | read-write (ingestion) |
| `neptune_portfolios` | Neptune | investor entities, PMs, books, positions, lots, theses, proposals | read-write (app) |

```
┌──────────────────────────┐   read-only     ┌───────────────────────────┐   universe-id ref   ┌────────────────────────────┐
│  cato_securities         │  (UNIVERSE_      │  neptune_securities       │  (same surrogate    │  neptune_portfolios        │
│  (user's universe truth) │   DATABASE_URL)  │  Postgres (+Timescale opt)│   id stored as a    │  Postgres                  │
│  entities / tickers /    │ ───────────────▶ │  - securities (projection)│   column, app-      │  - investor_entity (tenant)│
│  companies / identifiers │                  │  - prices (hypertable)    │   resolved) ──────▶ │  - portfolio_manager       │
│  (other apps read too)   │                  │  - dividends / corp_act   │                     │  - book (= portfolio)      │
└──────────────────────────┘                  │  - trading_calendar       │                     │  - position / lot          │
        ▲  yfinance (swap later)              └───────────────────────────┘                     └────────────────────────────┘
        └── backfill/daily ingest ─────────────────────▲
```

Three env vars drive it:
- `UNIVERSE_DATABASE_URL` → `cato_securities` (**read-only**; SELECT-only role, dedicated
  read-only SQLAlchemy engine).
- `SECURITIES_DATABASE_URL` → `neptune_securities` (read/write; ingestion).
- `PORTFOLIO_DATABASE_URL` → `neptune_portfolios` (read/write; app).

## 3. Linking across the database boundary

Postgres cannot enforce a foreign key from `neptune_securities.prices` into
`cato_securities` (cross-database FK is impossible). Two ways to bridge it:

1. **App-level identity projection (recommended backbone).** Neptune syncs a *thin*
   `securities` dimension — the universe **surrogate id** + ticker + exchange + status —
   from `cato_securities` into `neptune_securities`, on ingest and/or nightly. All joins
   then happen *within* Neptune's own databases. Self-contained, fast, decoupled, works
   even if the universe DB is offline, strict separation intact. Cost: identity can be
   slightly stale between syncs (negligible for prices keyed to a stable surrogate id).
2. **`postgres_fdw` foreign tables (optional, later).** Expose `cato_securities` tables as
   live foreign tables inside `neptune_securities` so you can JOIN the universe directly in
   SQL. Pros: always-live, no copy. Cons: re-couples the two DBs (credentials + live
   connection inside `neptune_securities`), planner can mis-estimate big joins, still gives
   joins not referential integrity, more ops setup. Worth it only if live analyst joins
   against the universe become a real need.

**Decision:** projection first; FDW is an additive option, not a prerequisite. "Both" is a
fine end state. The `securities` projection table is the join anchor — a synced cache, NOT
a second source of truth.

**OPEN — universe link key.** The user is providing the `cato_securities` schema. The link
column should be the universe's **stable surrogate id** (survives ticker renames / M&A /
re-listings) if one exists; otherwise ticker+exchange or a market id (FIGI/CUSIP/ISIN).
The projection/sync is written against that once the schema lands.

## 4. `neptune_portfolios` schema — multi-tenant from day one

A subscription/public risk-management version is a stated possibility, so the **tenant
boundary is baked in now** (retrofitting tenancy later is brutal).

```
investor_entity        ← the TENANT. Iridium internal = one row; each subscriber = another.
  │                       Isolation boundary: every query filters by entity_id.
  ├── portfolio_manager ← a person who runs books (belongs to one entity)
  │
  └── book  (= portfolio) ← managed sleeve; FK entity_id, FK manager_id,
        │                    base_currency, is_paper, mandate
        └── position       ← FK book_id, universe-id reference (the §3 link),
              │              side, notional, short_type, forward_beta, thesis, target
              └── lot       ← FK position_id (FIFO cost basis)
```

- `investor_entity` is the hard isolation key; carries subscription tier / feature flags.
  Every table below it carries `entity_id`; a subscriber only ever sees their own rows.
- `book` is the existing `PortfolioORM` extended with `entity_id` + `manager_id`.
- **Book-of-Books** (the firm-level `±0.030` beta limit in CLAUDE.md) is modeled as an
  aggregation of books under an `investor_entity` (optionally a `book_group` if a PM runs
  several books needing a sub-roll-up).

**OPEN — PM cardinality & Book-of-Books grouping.** One manager per book, or many PMs per
book with per-PM beta attribution? (Decides whether `manager_id` sits only on `book` or
also on `position`.) And: is rolling up all books under an entity enough for the
firm-level limit, or is an explicit `book_group` table wanted?

## 5. `neptune_securities` schema (time-series + reference)

Owned entirely by Neptune, via **Alembic migrations** (autogenerate + reviewed).

- `securities` — the §3 projection: universe surrogate id (PK link), ticker, exchange,
  currency, status, last_synced_at. The local join anchor for all facts below.
- `prices` — `(security_id, ts, open, high, low, close, adj_close, volume, source)`,
  a **TimescaleDB hypertable** on `ts` when the extension is present, else a plain
  partitioned/indexed table. The historical backfill target.
- `dividends` — cash/stock distributions with ex/pay dates.
- `corporate_actions` — splits, symbol changes, delistings (so adj_close is reproducible).
- `trading_calendar` — exchange sessions, for gap detection and return alignment.
- Later (roadmap): `beta_snapshots`, `pl_snapshots`, `firm_snapshots`.

Migration mechanics: `alembic` + a Timescale-aware step (`create_hypertable`) **guarded**
so plain Postgres (and SQLite CI) still works where Timescale isn't installed.

## 6. Securities universe adapter (read-only into `cato_securities`)

- A thin **read-only adapter** (`src/neptune/universe/`) with its own SQLAlchemy engine
  bound to `UNIVERSE_DATABASE_URL`. No ORM models that could imply writes — `text()`
  SELECTs or read-mapped tables only.
- Responsibilities: resolve **ticker ↔ surrogate id**, fetch the **investable/shortable
  universe** the optimizer scans, and pull identity metadata (name, sector, exchange,
  currency). This replaces the hardcoded `CATALOG` / `default_universe_tickers`, and feeds
  the §3 projection sync into `neptune_securities`.
- **Invariant fit:** consistent with "the Fundamental Layer is read-only input." The
  universe master is upstream truth; Neptune consumes, never mutates.

## 7. Price ingestion — provider abstraction + yfinance

A clean seam so the engine never knows where prices come from, and Bloomberg/Finnhub can
replace yfinance later without touching quant code.

```
src/neptune/marketdata/
  provider.py            # PriceProvider protocol: get_history(ticker, start, end) -> OHLCV frame
  yfinance_provider.py   # first implementation (free, daily OHLCV)
  ingest.py              # backfill + incremental: provider -> normalize -> upsert into prices
```

- **`PriceProvider`** is a `typing.Protocol`; yfinance is impl #1. Tests use a
  `RecordedProvider` (fixture frames) so they run offline in this sandbox.
- **Ingestion** resolves tickers via the universe adapter, pulls history, normalizes to
  the `prices` schema, and **upserts** (idempotent, append-only with `source` tag — no
  silent overwrite, consistent with invariant I-07).
- **Scheduling:** a Celery task (`celery`/`redis` already pinned) for nightly increments;
  backfill is a one-off management command the user runs locally. Stale-price circuit
  breaker per the roadmap is a later add.

## 8. Wire the engine to stored data (no math change)

- Introduce a `MarketData` **Protocol** matching what the engine already calls:
  `market_returns()`, `factor_returns()`, `ticker_returns()`, `current_price()`,
  `prev_close()`, `price_series()`, `spec_for()`.
- `SyntheticMarketData` becomes one implementation (kept for tests + offline dev).
- New `DbMarketData` reads the `prices` hypertable and computes returns from real closes.
  Factor returns: start with a stored/Ken-French factor table or a market proxy (SPY) for
  MKT and defer real SMB/HML/MOM to the factor-data task.
- `config` selects the implementation (`NEPTUNE_MARKET_DATA=synthetic|db`). The API holds a
  `MarketData` instance instead of `SyntheticMarketData()` directly — a one-line swap at
  the composition root; **the quant engine, optimizer, P&L, and stress code are untouched.**

## 9. Build order (each step independently testable, offline)

1. **Persistence foundation** — add `psycopg`/`alembic`; Alembic setup; split into the
   two Neptune databases (`neptune_securities`, `neptune_portfolios`) with separate engines
   + migration histories; multi-tenant portfolio schema; `prices` hypertable (guarded).
   (Tests still use SQLite; Postgres verified via docker-compose.) **← starting here.**
2. **Universe read-only adapter** — against the real `cato_securities` schema (need the
   table/column spec); offline fixtures. Replaces `CATALOG`/universe; feeds the projection.
3. **PriceProvider + yfinance + ingest** — interface, yfinance impl, idempotent upsert;
   backfill command (run by the user on a networked machine).
4. **`MarketData` protocol + `DbMarketData`** — wire the engine to stored prices behind
   config; synthetic stays the test/offline default.

Stress/optimizer/P&L all keep working throughout because they depend on the `MarketData`
interface, not the source.

## 10. What's needed before coding past step 1

1. **`cato_securities` schema** — table + column names for entity, ticker, identifiers,
   and the investable/shortable flag, plus the surrogate id (settles §3 link column). The
   user is providing this.
2. **PM cardinality & Book-of-Books grouping** — the §4 OPEN item.
3. **Connection shape** — confirm a SELECT-only role for `UNIVERSE_DATABASE_URL`.
4. **Where backfill runs** — confirmed: not this sandbox; the user runs the one-off pull
   locally / in deployment.
5. **Factor data** — for real SMB/HML/MOM: pull Ken French (roadmap source) in ingestion,
   or proxy/defer initially? (MKT can use SPY immediately.)

## 11. Out of scope for this data step (deferred)

Bloomberg/Finnhub providers, intraday/WebSocket streaming, stale-price circuit breaker,
`beta_snapshots`/`pl_snapshots` hypertables, multi-currency FX rates, and the full
Book-of-Books firm aggregation. All slot in after the historical-daily path is solid.
