# Neptune — Lessons (corrected patterns)

Record here every time the user corrects an approach, so future sessions and subagents
don't repeat the mistake. Newest first.

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
