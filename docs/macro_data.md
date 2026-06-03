# Neptune — Macro-Data Database (design)

> **Status: DESIGN APPROVED — decisions locked 2026-06-03; implementation pending.**
> Macro/economic data gets its **own Neptune-owned database** (the PM originally weighed
> putting it in Mercury and decided against it). This doc is the spec to build against. It
> mirrors the existing multi-engine, dialect-agnostic, append-only-with-`source` patterns
> already in `src/neptune/db/base.py` and `src/neptune/securities/models.py`.

---

## 1. Decision & layer placement

- A **new Neptune-owned database** for macro series (rates / credit / economic data),
  alongside the existing portfolio, securities, and universe DBs. Same multi-engine wiring.
- It is a **data-layer input only** (`CLAUDE.md` §1). The pure Quant Engine
  (`src/neptune/quant/`) must never import it; a `macro/repository.py` hands the engine
  plain arrays, exactly as prices do today.
- **Use is human-facing regime / scenario / stress context only** (`CLAUDE.md` §5). Macro
  data must **not** become an input that mutates the beta pipeline (§4) or gives the short
  book a market view. It informs a human; it never auto-adjusts the hedge.

## 2. Data typing — two storage axes + analytical flags

The schema flags every series so the analysis layer can pick the correct transform and
**refuse invalid operations** (YoY of an already-rate series; regression/z-score on a
non-stationary level; treating a flow like a stock). Flags live on the registry row.

| flag | values | governs |
|---|---|---|
| `series_class` | `MARKET` / `ECON` | **storage** — is it revised? (drives which fact table) |
| `category` | RATES, CREDIT, INFLATION, GROWTH, LABOR, HOUSING, SENTIMENT, MONETARY, FISCAL, TRADE, FX, COMMODITY, VOLATILITY, FIN_CONDITIONS | topical grouping (the PM's rates/credit-vs-economic view) |
| `observation_type` | `STOCK` / `FLOW` | level-at-a-point vs over-a-period → aggregation rule |
| `value_type` | LEVEL, PRICE, INDEX, RATIO, RATE, RATE_OF_CHANGE, DIFFUSION, SPREAD, YIELD, COUNT, CURRENCY | statistical nature → which transforms are valid (`PRICE` = market price level: commodities/FX, distinct from an interest `RATE` or a `$`-aggregate `LEVEL`) |
| `stationarity` | `STATIONARY` / `NONSTATIONARY` / `UNKNOWN` | is the raw series regression/z-score-ready |
| `transform_op` | NONE, DIFF, LOG_DIFF, PCT_CHANGE, ANNUALIZE, ZSCORE | the change *operator* — **constrained by `value_type`** (see below) |
| `transform_horizon` | NONE, POP, YOY | the *horizon* the operator spans (period-over-period vs year-over-year) |
| `seasonal_adjustment` | `SA` / `NSA` / `NA` | seasonal handling before MoM/YoY |
| `frequency` | DAILY, WEEKLY, MONTHLY, QUARTERLY | alignment / resampling |
| `units`, `country`, `currency`, `source`, `source_code`, `is_vintaged` | — | display, scope, provenance |

**The two storage classes:**
- **`MARKET`** — continuously priced, **never revised** (UST curve, SOFR, credit OAS,
  breakevens, FX, vol, commodities). One value per date. **No vintage dimension.**
- **`ECON`** — released on a schedule and **revised** (GDP, CPI, unemployment, payrolls,
  ISM). **Point-in-time / vintage storage is mandatory** (see §4).

> **Raw stays canonical (locked decision #3).** Fact tables store the **rawest canonical
> form** (CPI index, employment *level*). Transforms (YoY, DIFF→"new jobs", annualized
> growth, z-score) are applied **on read by the risk layer** — never materialized into the
> fact tables, never inside the engine.

**Operator + horizon, not a single "YoY".** "YoY" names a *horizon* (12 months), not an
operation — and the operation depends on `value_type`. A YoY on a price **index** (CPI) is a
`PCT_CHANGE` → that *is* inflation. A YoY on an already-a-**rate** series (unemployment) is a
`DIFF` → a change in **percentage points**; you never percent-change a rate. A series that
arrives **already differenced** (`value_type=RATE_OF_CHANGE`, e.g. a CPI-YoY series) gets
`transform_op=NONE` so the layer doesn't **double-difference** it. Same 12-month horizon,
different math per type:

| series | `value_type` | YoY resolves to | result |
|---|---|---|---|
| CPI_HEADLINE | INDEX (non-stationary) | `PCT_CHANGE @ YOY` | inflation %, e.g. +3.2% |
| UNRATE | RATE (stationary) | `DIFF @ YOY` | ±pp, e.g. +0.4pp (often used raw) |
| WTI / GBPUSD | PRICE (non-stationary) | `PCT_CHANGE @ YOY` | YoY price change %, e.g. +18% (a genuine two-price operation) |
| CPI_YOY (pre-differenced) | RATE_OF_CHANGE | `NONE` | already inflation — don't transform |

**Validation keys off `value_type`:** INDEX/LEVEL → `PCT_CHANGE`/`LOG_DIFF`;
PRICE (commodities/FX) → `LOG_DIFF`/`PCT_CHANGE`; RATE/RATIO/YIELD/SPREAD → `DIFF` in native
units; RATE_OF_CHANGE/DIFFUSION → `NONE`. The
risk layer **refuses** `PCT_CHANGE` on a rate and **refuses any second transform** on a
`RATE_OF_CHANGE` series.

## 3. Storage schema

New package `src/neptune/macro/`, new declarative base `MacroBase`, new `macro_engine` /
`MacroSession` / `init_macro_db` mirroring the securities DB. Idioms copied verbatim:
`BigIntPK = BigInteger().with_variant(Integer, "sqlite")`; `source`-tagged
`UniqueConstraint`; append-only/upsert; `Date` for daily ("completed closes only");
**registry separate from numbers** (like `FactorDefinition` vs `FactorReturn`).

- **`macro_series`** (registry) — one row per indicator, carrying all the §2 flags
  (`series_id` PK mnemonic, `series_class`, `category`, `observation_type`, `value_type`,
  `stationarity`, `default_transform`, `seasonal_adjustment`, `frequency`, `units`,
  `source`, `source_code`, `country`, `currency`, `is_active`, `last_synced_at`).
- **`macro_observations`** (MARKET values) — flat, append-only. Natural key
  `(series_id, obs_date, source)`. Columns: `obs_date` (Date), `value` (Float).
- **`macro_vintages`** (ECON values) — point-in-time. Natural key
  `(series_id, reference_date, vintage_date, source)`. `reference_date` = period described
  (period start, e.g. `2009-03-01`); `vintage_date` = the date that value became knowable.
- **`macro_release_calendar`** (optional) — scheduled ECON release datetimes so the risk
  layer can flag "NFP prints in 2h" and align as-of reads to release timing.

Engine wiring adds `macro_database_url` + a resolved `macro_url` property + a
`MACRO_DATABASE_URL` bare-env alias in `config.py`, and a `MACRO` role in
`settings_store` `ConnectionRole` (read/write, Neptune-owned — like `SECURITIES`).
On Postgres, `macro_observations`/`macro_vintages` are TimescaleDB-hypertable candidates
(guarded additive migration, same as `prices`).

## 4. Revision & restatement handling — append-only, never mutate

A value, once recorded as known on a date, is **immutable**. We never `UPDATE`/`DELETE`.

- **A revision to a prior period** = a **new row**, same `reference_date`, new
  `vintage_date`, new value. The old value stays, tagged with its old `vintage_date`.
- **A benchmark / annual restatement** (BLS payroll benchmark, BEA comprehensive GDP
  revision, seasonal re-estimation that rewrites *every* past month of an SA series) =
  **bulk new rows sharing one `vintage_date`** across many `reference_date`s. No
  special-casing — a batch insert.
- **MARKET corrections** (rare vendor fix) just upsert `macro_observations` on
  `(series, date, source)`; not vintaged.

Reads are all derived from the append-only history:
- **Latest** = `max(vintage_date)` per `reference_date`.
- **First print** = `min(vintage_date)` per `reference_date` (markets react to the first
  print, so it is analytically first-class).
- **As-of `d`** (look-ahead-safe) = `reference_date ≤ d AND vintage_date ≤ d`, take latest
  vintage per period — reconstructs the panel exactly as it stood on `d`.

Operational rules:
- **Derive `latest`/`first-print`; do not store mutable flags** (a stored `is_latest` goes
  stale on the next revision). If the dashboard needs speed, add a *materialized view*.
- **Building vintages from a latest-only source** (FRED current): on each sync, diff the
  incoming value per `reference_date` against stored latest; on change/new, insert a vintage
  row dated with the provider's realtime date if given, else the **sync date** (errs
  *later* than true release → never leaks a value earlier than knowable).

## 5. Locked decisions (2026-06-03)

1. **Backfill to 2000** for now.
2. **Full vintage depth** — store the complete ALFRED revision history where available
   (SA series are the heavy ones; accepted).
3. **Transforms in the risk layer**; raw canonical values in the fact tables; engine pure.
4. **Enriched analytical type flags** on the registry (§2) — yes.
5. **End-of-day only** for now — daily EOD pull for MARKET (today's live bar excluded),
   event-driven on ECON release dates. No intraday ⇒ free feeds; **CDX/MOVE deferred** to a
   later paid phase (free **OAS + VIX** are the Phase-1 credit/vol proxies).

## 6. Indicator catalog

**Phase-1 core — MARKET (daily):** UST `3M/2Y/10Y/30Y` + `2s10s` slope; `SOFR`,
`FEDFUNDS` (EFFR), `FF_TARGET` (policy target), `SHORT_RATE` (derived funding splice);
`IG_OAS`, `HY_OAS`; `BREAKEVEN_10Y`; `VIX`; **commodities `WTI`, `BRENT`, `GOLD`, `NATGAS`**;
**FX `DXY`, `EURUSD`, `USDJPY`, `GBPUSD`**.

> **Short-rate continuity (spliced & derived series).** SOFR only exists from 2018-04, but
> the backfill runs to 2000, so the short end needs continuity handling:
> - **`FF_TARGET`** (policy target, *ingested*) — the same concept across a FRED code change:
>   `DFEDTAR` (single target → 2008) then `DFEDTARU` (range upper, 2008→). The registry's
>   `source_code` holds an **ordered, comma-separated code list** (`"DFEDTAR,DFEDTARU"`); ingest
>   merges them by date, later code winning at the boundary. No spread adjustment — it's one
>   real series assembled from two codes.
> - **`SHORT_RATE`** (continuous funding rate, *derived*, NOT ingested) — a **spread-adjusted
>   splice** of `FEDFUNDS` (EFFR, pre-2018-04) and `SOFR` (2018-04→), built on read in
>   `risk/macro_derive.py`. EFFR (unsecured) and SOFR (secured repo) are *different* rates, so a
>   naïve concatenation injects an artificial step at the join; we shift pre-join EFFR by the
>   mean overlap spread. Kept as a transparent risk-layer derivation, not stored data
>   (`source="derived"`, no `source_code`).
**Phase-1 core — ECON (vintaged):** `CPI_HEADLINE`, `CPI_CORE`, `PCE_CORE`, `UNRATE`,
`PAYEMS` (level; DIFF→new jobs), `GDP`, `ISM_MFG`, `ICSA` (claims).

**Later:** full UST curve, `CDX_IG/HY`, `MOVE`, swaps, **daily copper (COMEX)** + more FX;
**roll-adjusted / total-return commodity indices (GSCI/BCOM, paid)**; `RETAIL_SALES`,
`INDPRO`, `HOUSING_STARTS`, `UMICH_SENT`. **Global extension** (non-US rates/FX, ECB/BoJ)
is additive via the `country`/`currency` columns once a non-US book exists.

> **Commodity series note.** Stored as **spot / front-month** (the canonical level), flagged
> `value_type=PRICE`. Roll-adjusted total-return commodity indices (GSCI/BCOM) are a later,
> typically paid, refinement. Daily **copper** is paid (COMEX/Bloomberg); FRED carries a
> monthly global copper price, so copper enters monthly-free now, daily-paid later.

Representative typing (store raw + flag + transform-on-read):

| series | class | category | obs_type | value_type | stationary | units | transform (op @ horizon) |
|---|---|---|---|---|---|---|---|
| UST_10Y | MARKET | RATES | STOCK | YIELD | yes | percent | NONE |
| IG_OAS | MARKET | CREDIT | STOCK | SPREAD | yes | bp | NONE |
| VIX | MARKET | VOLATILITY | STOCK | INDEX | yes | index | NONE |
| CPI_HEADLINE | ECON | INFLATION | STOCK | INDEX | no | index | PCT_CHANGE @ YOY (→ inflation) |
| UNRATE | ECON | LABOR | STOCK | RATE | yes | percent | NONE (level) or DIFF @ YOY |
| PAYEMS | ECON | LABOR | STOCK | LEVEL | no | thousands | DIFF @ POP (→ new jobs) |
| GDP | ECON | GROWTH | FLOW | LEVEL | no | $bn | LOG_DIFF @ POP, ANNUALIZE |
| ISM_MFG | ECON | ACTIVITY | — | DIFFUSION | yes | index | NONE |
| WTI | MARKET | COMMODITY | STOCK | PRICE | no | USD/bbl | LOG_DIFF @ POP |
| GOLD | MARKET | COMMODITY | STOCK | PRICE | no | USD/oz | LOG_DIFF @ POP |
| GBPUSD | MARKET | FX | STOCK | PRICE | no | px | LOG_DIFF @ POP |

## 7. Data sources

| Tier | Source | Use | Vintages |
|---|---|---|---|
| Free | **FRED** | latest value for nearly everything (rates, OAS, breakevens, VIX, CPI/PCE/UNRATE/PAYEMS/GDP) | no |
| Free | **ALFRED** | the point-in-time / vintage source for all ECON series — canonical | **yes** |
| Free | **US Treasury, Fed H.15** | authoritative par yields & policy/funding rates | no |
| Free | **BLS / BEA** | direct CPI/payrolls/unemployment (BLS), GDP/PCE (BEA) | partial (ALFRED cleaner) |
| Paid | ICE / Bloomberg / Refinitiv / Haver | CDX levels, MOVE, intraday, global-with-vintages | varies |

**Class → source:** MARKET/rates → Treasury + FRED/H.15; MARKET/credit → FRED ICE BofA
OAS; MARKET/breakevens·VIX → FRED; **MARKET/commodities → FRED** (`DCOILWTICO` WTI,
`DCOILBRENTEU` Brent, Henry Hub natural gas, LBMA gold; daily copper is paid, monthly copper
free on FRED); **MARKET/FX → FRED H.10** (daily USD pairs). ECON latest → FRED (or BLS/BEA
direct); **ECON vintages → ALFRED** (canonical), Haver as paid fallback. All Phase-1 core is
obtainable **free** via FRED + ALFRED + Treasury + BLS/BEA (daily copper + TR commodity
indices are the only Phase-1-adjacent paid items, both deferred).

## 8. Open / future (non-blocking)

- Global (non-US) rates/FX and the trigger to add them.
- Paid feeds (CDX/MOVE/intraday) if/when budgeted.
- Reconcile `Neptune_Roadmap.md:160` (still cites the superseded EWMA+Dimson beta) with the
  authoritative `CLAUDE.md` §4 pipeline — separate from this work, flagged so regime docs
  don't cite the wrong method.
