# CLAUDE.md — Neptune Hard Invariants

Neptune is Iridium Capital Management's quantitative risk intelligence platform.
This file defines **HARD invariants** that **every session and every subagent MUST
respect**. These are not suggestions. If a task appears to require violating one of
these, stop and raise it with the user instead.

The authoritative product spec is [`Neptune_Roadmap.md`](./Neptune_Roadmap.md).
This file governs *how* we build; the roadmap governs *what* we build.

---

## 1. Three-layer architecture — never blurred

Neptune is built as three strictly separated layers. Code, data, and responsibilities
must not bleed across them.

1. **Quant Engine** — the automated math: beta pipeline, factor decomposition, the
   hedge optimizer. Pure functions over arrays/dataclasses. **No I/O, no DB, no
   network.** Lives in `src/neptune/quant/`.
2. **Risk Interface** — translates the math for humans: net-beta status, factor
   OK/WATCH/BREACH classification, framed recommendations. Lives in
   `src/neptune/risk/` and the frontend. It *reads* engine output; it does not
   re-implement the math.
3. **Fundamental Layer** — target selection and investment thesis. **The system
   NEVER touches, automates, generates, or mutates this.** It is read-only *input*
   to Neptune (e.g. a position's `thesis` / `target` fields). No module may write to
   or auto-generate fundamental content. Humans own it entirely.

## 2. The system never auto-executes

Neptune **recommends; a human approves.** Every optimizer output is a *proposal* with
a pending status. **No code path may route an order to any broker, OMS, or execution
venue.** (Roadmap invariants I-01, I-08.)

## 3. Net portfolio beta hard constraint: `|β| ≤ 0.05`

Net portfolio beta must satisfy `|β| ≤ 0.05`. This is enforced as a hard constraint
in the optimizer and **asserted in tests** with golden-number fixtures. Firm-level
limit is tighter (`±0.030`) and applies once Book-of-Books exists.

## 4. Beta pipeline — fixed order, Vasicek is the FINAL step

1. **Raw beta** — a single 252-day **EWMA-weighted regression** (λ = 0.94, weights
   `λ^k` newest-first, normalized) of stock excess returns on market excess returns,
   with the **Dimson lead/lag market terms (k = −1, 0, +1) folded into the same
   regression**. Raw β = sum of the contemporaneous + lag + lead market coefficients.
   The regression also yields the estimation variance `σ²_OLS`.
2. **Vasicek shrinkage** — applied to the raw estimate as the **final model step**:
   `β = w · β_raw + (1 − w) · 1.0`, with `w = σ²_prior / (σ²_prior + σ²_OLS)`.
3. **Forward beta override** — a per-position, PM-overridable `forward_beta`
   **supersedes the entire pipeline** for that position (post-catalyst situations).

> Dimson terms are estimated *inside* the EWMA regression — never applied as a
> post-shrinkage adjustment. Vasicek is always last among model steps.

## 5. The short book is a hedge, not alpha

The **systematic short book exists ONLY to neutralize long-book market beta** — it is
never a source of alpha or an expression of a market view. Systematic and
discretionary shorts are **never conflated** in any report (I-03). The optimizer reads
discretionary shorts as inputs and may *suggest* resizes, but **never mutates them**
(I-04).

## 6. Stack & layout

- **Backend (Python):** FastAPI, NumPy/SciPy/cvxpy, Postgres (SQLAlchemy + Alembic),
  Celery/Redis.
- **Frontend:** React + Tailwind (Vite + TypeScript).
- **Layout:** `src/` layout — the package is `src/neptune/`.
- **Persistence:** Postgres is the canonical DB (`DATABASE_URL`-driven). SQLite
  in-memory is the test fallback behind the same SQLAlchemy interface.

## 7. Working agreement

- **Read-only subagents verify; the main session edits.** The `quant-researcher`,
  `test-runner`, and `code-reviewer` agents in `.claude/agents/` have no write access.
- **Nothing is marked done until its tests pass.**
- **When the user corrects something, record the pattern in
  [`tasks/lessons.md`](./tasks/lessons.md).**
- The build plan lives in [`tasks/todo.md`](./tasks/todo.md), in roadmap module order.
