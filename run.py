#!/usr/bin/env python3
"""Run the whole Neptune dev stack with ONE command:

    python run.py

Starts both servers and streams their logs together:
  * [api]  FastAPI backend via uvicorn on http://localhost:8000  (--reload)
  * [web]  Vite frontend dev server on http://localhost:5176 (proxies API calls to :8000)

Open the UI at http://localhost:5176. Press Ctrl-C once to stop both.

Notes
-----
* Run it with the SAME Python you installed the backend into (activate your venv first), so
  `-m uvicorn` resolves. `src/` is forced onto PYTHONPATH so `neptune` imports even without an
  editable install.
* On first run it installs the frontend deps (`npm install`) if `frontend/node_modules` is
  missing. `npm` must be on PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
API_URL = "http://localhost:8000"
WEB_URL = "http://localhost:5176"


def _pump(proc: subprocess.Popen, label: str) -> None:
    """Forward a child's combined output to our stdout, line by line, with a [label] prefix."""
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        sys.stdout.write(f"[{label}] {line}")
        sys.stdout.flush()


def main() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("error: `npm` not found on PATH — install Node.js to run the frontend.")
        return 1

    # First-run convenience: install frontend deps if they're missing.
    if not (FRONTEND / "node_modules").exists():
        print("[web] node_modules missing — running `npm install` (one-time)…")
        if subprocess.run([npm, "install"], cwd=FRONTEND).returncode != 0:
            print("error: `npm install` failed.")
            return 1

    # Backend: same interpreter; src on PYTHONPATH so `neptune` imports without an editable install.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])

    procs: list[tuple[subprocess.Popen, str]] = []
    common = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        procs.append((
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "neptune.api.main:app", "--reload",
                 "--port", "8000"],
                cwd=ROOT, env=env, **common,
            ),
            "api",
        ))
        procs.append((
            subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND, **common),
            "web",
        ))
    except OSError as exc:
        print(f"error launching a server: {exc}")
        for p, _ in procs:
            p.terminate()
        return 1

    for proc, label in procs:
        threading.Thread(target=_pump, args=(proc, label), daemon=True).start()

    print(f"\nNeptune is starting →  UI {WEB_URL}   API {API_URL}")
    print("Press Ctrl-C to stop both.\n")

    try:
        # Run until one of them exits (a crash) or the user interrupts.
        while all(p.poll() is None for p, _ in procs):
            time.sleep(0.3)
        for p, label in procs:
            if p.poll() is not None:
                print(f"\n[{label}] exited (code {p.returncode}) — shutting down the other.")
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        for p, _ in procs:
            if p.poll() is None:
                p.terminate()
        for p, _ in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
