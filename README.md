# Neptune

**Iridium Capital Management — quantitative risk intelligence platform.**

Neptune maintains real-time beta neutrality on a long book, decomposes factor
exposures, and constructs a *systematic short book purely as a hedge* via a two-pass
optimizer. It **recommends; a human approves** — nothing auto-executes.

The hard invariants every change must respect live in [`CLAUDE.md`](./CLAUDE.md); the
product spec is [`Neptune_Roadmap.md`](./Neptune_Roadmap.md); the build plan is in
[`tasks/todo.md`](./tasks/todo.md).

## What's in this vertical slice

A runnable end-to-end path: enter long/short positions → compute betas through the full
**EWMA + Dimson regression → Vasicek shrinkage** pipeline → factor decomposition → the
optimizer proposes a short basket under the **`|net beta| ≤ 0.05`** hard constraint → a
themed dashboard shows net beta and factor exposures. The Stress Engine and
multi-portfolio Book-of-Books are stubbed for later.

## Architecture (three layers, never blurred)

1. **Quant Engine** (`src/neptune/quant/`) — pure NumPy/SciPy/cvxpy math, no I/O.
2. **Risk Interface** (`src/neptune/risk/`, frontend) — translates the math for humans.
3. **Fundamental Layer** — target selection & thesis; **read-only input the system never
   touches**.

## Run the whole app — one command

```bash
python run.py
```

Starts **both** servers and streams their logs together, then open the UI at
http://localhost:5176:

* `[api]` FastAPI backend (uvicorn, `--reload`) on http://localhost:8000
* `[web]` Vite frontend on http://localhost:5176 (proxies API calls to the backend)

Press **Ctrl-C** once to stop both. Run it from your activated venv (so `uvicorn` resolves);
first run auto-installs the frontend deps if `frontend/node_modules` is missing (`npm`
required). To run the pieces separately, use the quickstarts below.

## Backend — quickstart

```bash
pip install -e ".[dev]"      # or: pip install -r requirements.txt
pytest                       # runs the golden-number test suite
uvicorn neptune.api.main:app --reload   # serves the API on :8000
```

The API seeds a golden demo portfolio on startup. Key endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/portfolios/{id}/risk` | net beta + factor exposures (OK/WATCH/BREACH) |
| `POST` | `/portfolios/{id}/positions` | enter a long/short position |
| `POST` | `/portfolios/{id}/hedge/propose` | optimizer proposes a hedge (PENDING_APPROVAL) |

### Persistence

Postgres is the canonical database (`DATABASE_URL`-driven); SQLite in-memory is the
test/dev fallback behind the same SQLAlchemy interface.

```bash
docker compose up -d         # postgres:16 + redis
export DATABASE_URL=postgresql+psycopg://neptune:neptune@localhost:5432/neptune
```

## Frontend

```bash
cd frontend
npm install
npm run dev                  # Vite dev server (Deep Ocean themed SPA)
npm test                     # Vitest
```

## Verification

`pytest` exercises the beta pipeline (noise-free recovers the true beta; a known-noise
fixture forces Vasicek's shrinkage weight strictly into (0, 1)), the optimizer (asserts
`|net beta| ≤ 0.05` on the golden portfolio), and the full API slice.
