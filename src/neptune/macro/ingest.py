"""Phase-1d macro ingest: pull the catalog's series from FRED/ALFRED into the macro DB.

Dispatch on ``series_class``:
* MARKET → ``provider.observations`` → ``record_observation`` (flat, upsert).
* ECON   → ``provider.vintage_observations`` → ``record_vintage`` (point-in-time; real vintage
  dates preserved, so revisions/restatements land as the append-only history they are).

Series with no ``source_code`` (e.g. ISM PMI — licensed, no free FRED code) are skipped.
Idempotent: re-running upserts MARKET values and de-dupes ECON vintages on their natural key.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from neptune.macro import repository as repo
from neptune.macro.models import MacroSeries, SeriesClass
from neptune.macro.providers import FredProvider
from neptune.settings_store.credentials import CredentialsService

DEFAULT_START = date(2000, 1, 1)  # backfill depth (locked decision: to 2000)
_FRED_SOURCES = {"FRED", "ALFRED"}  # series the FRED key can serve


def build_fred_provider(portfolio_session: Session) -> FredProvider:
    """A configured FRED client, keyed from the credentials store (UI-stored > env). Raises if
    no key is configured — surfaced as a 400 by the endpoint."""
    key = CredentialsService(portfolio_session).resolve_key("FRED")
    if not key:
        raise RuntimeError(
            "no FRED API key configured — set one in Settings → Data provider API keys"
        )
    return FredProvider(key)


def ingest_series(
    macro_sess: Session, series: MacroSeries, provider: FredProvider, *, start: date = DEFAULT_START
) -> int:
    """Pull and store one series. Returns the number of points written."""
    if not series.source_code or (series.source or "").upper() not in _FRED_SOURCES:
        return 0  # e.g. ISM (licensed; no free code)
    if series.series_class == SeriesClass.MARKET:
        points = provider.observations(series.source_code, start=start)
        for p in points:
            repo.record_observation(
                macro_sess, series.series_id, p.obs_date, p.value, source=series.source or "FRED"
            )
        return len(points)
    # ECON → vintages (ALFRED)
    vpoints = provider.vintage_observations(series.source_code, start=start)
    for vp in vpoints:
        repo.record_vintage(
            macro_sess, series.series_id, vp.reference_date, vp.vintage_date, vp.value,
            source=series.source or "ALFRED",
        )
    return len(vpoints)


def ingest_catalog(
    macro_sess: Session,
    provider: FredProvider,
    *,
    start: date = DEFAULT_START,
    only: set[str] | None = None,
) -> dict[str, int]:
    """Ingest every registered FRED/ALFRED series (optionally a ``only`` subset). Commits once.
    Returns ``{series_id: points_written}`` (series without a free code are omitted)."""
    counts: dict[str, int] = {}
    for series in repo.list_series(macro_sess):
        if only is not None and series.series_id not in only:
            continue
        if not series.source_code or (series.source or "").upper() not in _FRED_SOURCES:
            continue
        counts[series.series_id] = ingest_series(macro_sess, series, provider, start=start)
    macro_sess.commit()
    return counts
