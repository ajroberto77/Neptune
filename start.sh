#!/usr/bin/env bash
# Neptune launcher (macOS / Linux). Run with:  ./start.sh
# Uses the project's .venv directly — no activation required. Bootstraps on first run.
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "[setup] creating .venv ..."
    python3 -m venv .venv
fi

# Install deps only if a core import is missing (fast no-op on later runs).
if ! "$PY" -c "import fastapi, sqlalchemy, uvicorn, numpy, cvxpy" 2>/dev/null; then
    echo "[setup] installing dependencies (first run) ..."
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
fi

echo "[run] Neptune -> UI http://localhost:5173   API http://localhost:8000"
exec "$PY" run.py
