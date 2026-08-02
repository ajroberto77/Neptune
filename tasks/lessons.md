# Neptune — Lessons (corrected patterns)

Record here every time the user corrects an approach, so future sessions and subagents
don't repeat the mistake. Newest first.

---

---

## 2026-08-02 — Not every boundary needs a Protocol; only build one where a swap is real

**Pattern (architecture audit, Phase 2):** Asked to audit the DB-access and Electron-IPC
boundaries the same way Phase 1 (provider DI, below) audited data sources. The honest
finding for BOTH was "don't build a Protocol here":

- **DB access**: `PositionService`/`ConnectionSettingsService`/`macro/repository.py` already
  sit between the API and the ORM, and route handlers correctly call into them for the bulk
  of the CRUD surface. There's no second storage backend anywhere in sight — no comment, no
  partial implementation, no roadmap doc suggesting positions/portfolios/settings might move
  off SQLAlchemy/Postgres. Building a `PositionStore` Protocol with a `RecordedPositionStore`
  fixture, when the real implementation would just wrap the same SQLAlchemy repository
  internally, is speculative complexity with no plausible second backend.
- **Electron IPC**: `isElectron`/bridge-existence checks are already concentrated in one file
  (`Settings.tsx`) plus three small, self-contained satellites — not smeared across the UI.
  Electron-vs-browser is a static fact known once at page load, not a runtime-swapped axis
  any caller chooses between. A formal `PlatformBridge` interface with two class
  implementations would formalize something nothing actually varies along.

**What WAS real, in both cases, was narrower and different in kind than a missing interface:**
a handful of inline SQL queries in `api/main.py` that should've been in the repository layer
(mechanical — move the code, no new abstraction), and a genuine correctness bug (the
`neptune-config.json` vs `db_connections`-table duality silently letting a stale stored row
override a fresh config with zero error or UI signal — a real bug, not a modularity nicety).

**Pattern:** When auditing a codebase for "should this be behind an interface," the
deciding question is Phase 1's own test — is there a *plausible second implementation*, not
just "is this code technically coupled to one library." A repository class being internally
SQLAlchemy-specific is fine as long as callers don't know that. Don't manufacture interface
ceremony for symmetry with a DIFFERENT boundary that legitimately needed one. And always
verify a "fix" that spans processes (here: Electron main process → spawned Python sidecar →
Postgres) against the REAL stack, not just by reading the code — the config-sync fix was
tested by actually starting uvicorn against a real Postgres DB and confirming the rows landed
correctly, not just by trusting the code looked right.

---

## 2026-07-11 — Construct providers via factory, never inside route handlers or job bodies

**Pattern (architecture audit):** When a `Protocol` exists for an external-data service
(`PriceProvider`, `FactorProvider`, `MacroProvider`), never call `ConcreteImpl()` inside a
route handler body or an APScheduler job. That hardwires the handler to one third-party
library and makes tests require live network access — the Protocol is no longer doing its job.

The correct pattern:
1. A **shared factory function** (e.g. `build_price_provider(session)` in `neptune/providers.py`)
   is the single construction point. It reads credentials if needed and returns the concrete impl.
2. **FastAPI Depends generators** (`get_price_provider`, etc.) wrap the factory — handlers declare
   `provider: PriceProvider = Depends(get_price_provider)` and never construct anything.
3. **APScheduler jobs** can't use FastAPI Depends, so they call the factory directly inside the
   job body (already receives a `Session`). Same factory; no duplication.
4. When the swap comes (yfinance → Bloomberg B-PIPE), only the factory changes. No handler bodies
   are touched.

**Also applied:** The macro `build_fred_provider(session)` factory that lived in `macro/ingest.py`
was a one-off deviation of this pattern — the route handler called it and caught the RuntimeError
inline. After the fix: `get_macro_provider` Depends raises `HTTPException(400)` directly, which
FastAPI surfaces with the correct status code; `macro/ingest.py` functions accept `MacroProvider`
(the Protocol), making them independent of the concrete FRED implementation.

---

## 2026-06-03 — Factors are an extensible POOL (data), not a fixed `STYLE_FACTORS` constant — and we don't hedge all of them

**Correction (PM, direction-setting — design deferred, captured so we resume cleanly):** I built
the factor program as a hardcoded `STYLE_FACTORS` tuple (FF5+MOM) as the single source of truth,
with monitor factors (IVOL/BAB/AMIHUD/SECTOR_*) bolted on and a config-gated *binary promotion*
flag to move one into the neutralized set. That's the wrong mental model. The intended shape:

1. **Factors-as-data, not code.** A first-class, extensible **registry** holding BOTH accepted/
   academic factors (FF5, MOM) AND ones the firm **discovers** in-house, each with a lifecycle
   (candidate → validated → accepted → deprecated). Generalize the existing `FactorDefinition`
   registry + `FactorReturn` store; retire the `STYLE_FACTORS` constant as the source of truth.
2. **Don't neutralize the whole pool.** The production risk model is a **selected subset** of the
   registry — "hedge everything we can compute" is wrong.
3. **A factor optimizer over the pool** chooses which factors enter the risk model / get
   neutralized, replacing the fixed tuple + binary promote flag.

**Invariant §5 framing to hold:** "don't hedge all factors" ⇒ we deliberately RETAIN exposure to
some. Retained factor exposure is a factor *bet*, and §5 says the systematic short book is ONLY a
hedge of unrewarded risk, never alpha/a view. So the clean split is: the **PM's long book** owns
the rewarded bets (discretionary/fundamental); the **factor optimizer identifies the *unrewarded*
risk in the pool and neutralizes only that.** Do NOT let "factor optimizer from the pool" drift
into a factor-*investing* / tilt-picking engine — that's a different mandate and brushes §5/§2.

**Open decisions (PM is revisiting — do not assume):**
- What the factor optimizer decides: risk-model *selection* (parsimonious non-collinear spanning
  set) vs hedge/keep *partition* (neutralize unrewarded vs retain intended) vs both (staged).
- Who owns the neutralize-vs-retain call: math-proposes/human-approves vs data-driven default
  (premium significance) vs human-tagged registry policy.

**Pattern:** When a domain has a growing catalog the firm extends over time (factors, scenarios,
signals), model it as a registry of data with a lifecycle + a selection layer — not a hardcoded
constant that every module imports. And keep the §5 line sharp: the hedge neutralizes *unrewarded*
risk; intended exposures are the PM's, never the optimizer's.

---

## 2026-06-01 — "Book" = portfolio; long/short is a position attribute; trade ticket is Buy/Sell

**Correction (PM):** The trade/position model was wrong from a desk perspective. The fixes:

1. **A book IS a portfolio**, not a sub-bucket. The UI wrongly split one portfolio into
   "Long Book / Systematic Short / Discretionary Short" *books*. The portfolio is the book;
   a position simply shows whether it's **long or short**. Multiple portfolios (books) come
   later via a portfolio dropdown on the ticket.
2. **The trade ticket is just Buy / Sell** (+ Cash/Swap instrument). The action's meaning is
   *derived from the current position* ("the allocation"): Buy→initiate/add long, or
   buy-to-cover a short; Sell→reduce/close long, or sell-to-short; crossing zero flips side.
   Do NOT make the user pick a "book"/side.
3. **Systematic vs discretionary short is preserved (invariant I-03) but as an origin TAG on
   the short line**, not a user-selectable book: manual trades are always discretionary;
   systematic shorts come ONLY from an approved hedge-optimizer proposal. Manual Buy/Sell
   therefore only ever touches the LONG or DISCRETIONARY-SHORT position, never SYSTEMATIC.
4. **Day P&L must equal Unrealised for a same-day trade** — a lot opened today had no prior
   close, so its day-P&L reference is the entry price. (The engine already supported this via
   `position_pnl(as_of=...)`; the caller just wasn't passing `as_of`.)
5. Spell it **"unrealized"** (American), not "unrealised".

**Pattern:** Model trades the way a trading desk thinks — direction is an attribute of the
position derived from buy/sell netting against the current holding, not a category the user
files into. Keep invariant distinctions (systematic vs discretionary) as provenance metadata,
not as primary user-facing structure.

## 2026-05-30 — Fall back to inline review when subagents are unavailable

**Pattern (not a user correction, but worth keeping):** The read-only `quant-researcher`
and `code-reviewer` subagents both failed repeatedly with transient `API Error: 529
Overloaded` (zero tool uses, no result). Relaunching just burned time. The fix: when
subagents are down, do the verification **inline in the main session** instead of looping
on relaunches — it's bounded work the main session can do directly:
- *Numerical correctness*: write a short first-principles script that hand-computes the
  expected numbers and compares (see the P&L check that validated FIFO/AVCO/Specific, the
  four dimensions, and the book-split sum-to-total invariant).
- *Invariant/structure review*: targeted `grep`/import-smoke checks for layer purity (no
  db/api/network imports in pure modules), import cycles, execution paths
  (broker/OMS/order), Fundamental-Layer writes (`thesis`/`target`), and ORM
  cascade/ordering.
Don't block a verified, test-green commit waiting on a flaky subagent.

## 2026-05-30 — Never commit/push on a red suite; verify behavior, not just "tests pass"

**Correction (self-caught after a stop-hook nudge):** While adding the adaptive
complexity-frontier, a large parallel tool batch committed and pushed a change with a
**failing** `test_api.py` and a fix that didn't actually work. Two failures compounded:

1. **Committed on red.** A push went out (`a7c42c5`) with the backend suite failing.
   HARD rule (CLAUDE.md §7): *nothing is marked done until its tests pass* — that
   includes never committing/pushing on a red suite. Don't batch the commit in the same
   parallel block as the verification; run tests, read the result, *then* commit.
2. **"Natural support" was the wrong measure.** The frontier caps were derived from
   `count(|weight| > 1e-6)` of the uncapped soft QP. That QP sprinkles negligible weights
   across the *entire* universe, so support came back as N (=60) and the caps were all
   above the ~7 names where neutralization happens — identical, degenerate rows. Fix:
   measure the **neutralization threshold** (smallest top-ranked prefix that achieves
   |net beta| <= tol, via binary search) and straddle it.

**Pattern:** For optimizer/threshold features, verify the *behavior* on the real
production inputs (here: the live 60-name universe), not just that unit tests on a
hand-built fixture pass — the fixture and production can exercise different regimes.
Keep the commit step out of parallel batches that also run the tests.

## 2026-05-29 — Beta pipeline ordering: Vasicek is the FINAL step

**Correction:** The beta pipeline is **not** EWMA → Vasicek → Dimson applied as three
sequential transforms. The correct construction is:

1. Estimate the **raw beta** with a single EWMA-weighted regression (λ=0.94, 252-day)
   that **folds the Dimson lead/lag market terms (k = −1, 0, +1) into the same
   regression**. Raw β = sum of contemporaneous + lag + lead market coefficients.
2. Apply **Vasicek shrinkage as the final model step** to that raw estimate.
3. The **forward-beta override** still supersedes everything for a given position.

**Why:** Dimson is part of the beta *estimation* (it corrects the regression for
asynchronous/illiquid pricing), so it belongs inside the regression — not as a
post-shrinkage adjustment. Vasicek shrinks the *final* estimate toward 1.0, so it must
come last. Shrinkage weight `w = σ²_prior / (σ²_prior + σ²_OLS)` depends on the
regression's estimation variance; golden fixtures must include a **known-noise** case so
`0 < w < 1` and the shrinkage is actually exercised (not just the `w ≈ 1` degenerate
case).

**Pattern:** When a "pipeline" of statistical steps is described, check whether a step is
part of *estimation* (belongs inside the regression) vs. a *transform of the estimate*
(applied after). Don't blindly chain them in the order listed.

---

## Don't state an inferred cause as fact — especially about the user's data

**Context:** The hedge universe came back empty. I told the user it was because they'd
"only backfilled their own positions." I had never inspected their DB; I inferred it from
the symptom. They corrected me: 128k price rows, ~540 companies, ~250 days each.

**Pattern:** When a symptom has several possible causes and the deciding evidence lives in
data I can't see (the user's DB, their env), say which causes are possible and how to tell
them apart — or add a diagnostic that surfaces the truth. Never narrate one hypothesis as
the established reason. For the universe specifically, the real gates are silent: (1)
`market_data_for` is all-or-nothing — one unpriced position (or an unpriced benchmark)
drops the WHOLE book to the synthetic 60-name source; (2) `available_tickers` needs the
`Security`∩`Price` join (projection synced + matching instrument_ids) with >=30 bars. A
populated price table alone is not sufficient.

---

## Don't fabricate observations to satisfy an array-shape contract — it biases the estimate

**Context:** WEN's beta read ~0.00 and several energy names (XOM, CVX, OKE) read negative.
Real betas, obviously not. Root cause was in `DbMarketData._adj_aligned`: every per-ticker
series was forced onto the full benchmark date index, and the leading region *before a name's
first real bar* was **back-filled with that first price**. A constant price is a zero return,
so a name with a shorter/gappier history than SPY got hundreds of fabricated zero-return days
prepended. Regressing those against a moving market drags β toward 0 — and worse, the
fabricated zeros shrink `var_ols`, so Vasicek reads the wrong number as *precise* and doesn't
pull it back to 1.0. Confidently wrong. The optimizer then "diversified" into those artificial
low/negative-beta names, so shorting them actually *added* market exposure (net long).

**Fix:** a name is regressed only over its **own real window** — start the series at its first
real bar, drop the leading gap (interior gaps still forward-fill). It stays a contiguous tail
of the benchmark index, so `align()` tail-matches it to the market and they remain date-aligned;
the name is just regressed on fewer, real observations (and a genuinely thin name then has a
large `var_ols`, so Vasicek correctly shrinks it toward 1.0 instead of pinning it at 0).

**Pattern:** Forward/back-fill is a *display/alignment* convenience, never an input to an
estimator. Padding to a fixed length with synthetic values silently injects zero-variance,
zero-covariance rows that pull slopes toward zero and falsely tighten standard errors. When a
regression needs aligned arrays, align by intersecting *real* observations — don't manufacture
rows to make the shapes match.

---

## Sign of a hedge's beta-adjusted notional must reflect the position direction

**Context:** The Hedge tab showed every proposed short's beta-adjusted notional as
`notional × beta` (positive), so the basket total read +$14.9M — implying the "hedge" added
market exposure, contradicting the net-β-after of ~0 the backend computed. A short of a
positive-beta name *removes* market exposure: its beta-adjusted notional is **negative**
(`−notional × beta`), matching the Portfolio tab's `sign(p)·notional·β` convention.

**Pattern:** Any per-name exposure number on a short book must carry the short sign, or the
totals contradict the (correctly signed) net-beta figure and look nonsensical. Mirror the
existing signed convention rather than re-deriving an unsigned one per view.

---

## Beta shrinkage prior must be book-independent; hedge approval must replace, not stack

**Context (full audit triggered by "it doubled the position / the long beta-adj changed after I
traded"):** Two independent bugs compounded into a wildly over-hedged, self-inconsistent book.

**Bug 1 — book-dependent Vasicek prior (unstable betas).** `compute_metrics` estimated the
Vasicek prior from the *current book's own* cross-section of raw betas. With 3 longs the prior
was tiny → heavy shrinkage toward 1.0 → inflated long betas (PZZA 0.80); after booking 35 shorts
the 38-name prior was large → little shrinkage → the SAME longs re-priced (PZZA 0.61). So a name's
beta moved the instant you traded, and the optimizer (which sized the hedge against the inflated
propose-time residual) no longer matched the book after booking. Fix: shrink against a FIXED,
market-level prior constant (`DEFAULT_PRIOR_VAR`), used by the book AND the universe candidates so
they share one frame. A name's beta is now a pure function of its own returns.

**Bug 2 — approval stacked hedges (the "doubling").** `residual_metrics` excludes systematic
shorts, so every Propose sizes a FULL replacement hedge against the long book — but `approve_hedge`
booked additively via `record_trade`, and the frontend booked one row at a time. Two propose→approve
cycles → two full hedges → ~2× short notional ($29.5M sized → $58.9M booked). Fix: approval is now
atomic and REPLACING — `clear_systematic_shorts` wipes the old systematic book, then the basket is
booked once (deduped by ticker); the frontend sends the whole basket in a single call. Discretionary
shorts are never touched (I-03/I-04).

**Patterns:**
- A per-name estimate must never depend on the composition of the set it's displayed in. If
  shrinkage/normalization pulls from "the current book," the number silently changes when the book
  changes. Anchor priors to a stable, exogenous reference (the market/universe), not the live book.
- When an optimizer output is computed as a full REPLACEMENT (residual excludes the thing being
  re-proposed), the apply step must replace too. Mixing "propose as replacement" with "apply as
  increment" double-counts on every cycle. Make apply idempotent (clear-then-book, deduped).
