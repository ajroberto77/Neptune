#!/usr/bin/env python3
"""Backend entry point for the Neptune Electron shell.

The Electron main process (``main.js``) spawns this script as a sidecar, injecting the
database URLs and provider keys as environment variables (``PORTFOLIO_DATABASE_URL``,
``SECURITIES_DATABASE_URL``, ``MACRO_DATABASE_URL``, ``UNIVERSE_DATABASE_URL``,
``FRED_API_KEY``) and the bind address (``NEPTUNE_API_HOST`` / ``NEPTUNE_API_PORT``).
``neptune.config`` already reads those env vars, so there is nothing to wire here beyond
putting ``src/`` on the path and starting uvicorn.

Run standalone it falls back to the shell environment / ``.env`` (in-memory SQLite by
default), exactly like ``run.py``'s backend half — but bound to the Electron port.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo root is the parent of this scripts/ directory; src/ holds the ``neptune`` package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    import uvicorn

    host = os.environ.get("NEPTUNE_API_HOST", "127.0.0.1")
    port = int(os.environ.get("NEPTUNE_API_PORT", "8433"))
    # No --reload: the shell owns this process's lifecycle and restarts it on config save.
    uvicorn.run("neptune.api.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
