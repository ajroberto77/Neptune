"""The factor-panel refresh job: re-pull the Ken French FF5+MOM panel and rebuild style
loadings for the whole universe.

Pure-ish and testable — takes an open securities session and a provider. The scheduler wraps
this; the manual ``POST /factors/ingest`` endpoint does the same for an explicit window, so
the two paths can never drift apart.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from neptune.risk import beta_store
from neptune.securities.factor_ingest import ingest_factors
from neptune.securities.factor_providers import FactorProvider


def refresh_factor_panel(
    sec_session: Session,
    provider: FactorProvider,
    start: date,
    end: date,
    *,
    benchmark: str,
) -> dict:
    """Ingest the factor panel for ``[start, end]`` and rematerialize style loadings for the
    whole universe. Raises ``RuntimeError`` if ``pandas_datareader``/the provider isn't
    installed, or any other exception on a feed/network failure — the caller decides how to
    translate that (``HTTPException`` for the manual endpoint, log-and-swallow for the
    scheduler)."""
    counts = ingest_factors(sec_session, provider, start, end)
    # The panel just changed → (re)materialize style loadings for the whole universe.
    loadings_written = 0
    try:
        loadings_written = beta_store.rebuild_loadings(sec_session, benchmark=benchmark)
    except Exception:  # noqa: BLE001 — never fail the panel ingest on a loadings hiccup
        loadings_written = 0
    return {"counts": counts, "loadings_written": loadings_written}
