# Neptune — Lessons (corrected patterns)

Record here every time the user corrects an approach, so future sessions and subagents
don't repeat the mistake. Newest first.

---

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
