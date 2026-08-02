"""HTTP client for FRED + ALFRED (St. Louis Fed) — Phase-1d macro ingest.

One free key serves both (https://fredaccount.stlouisfed.org/apikeys):

* **FRED** ``series/observations`` → the current value per date (MARKET series — not revised).
* **ALFRED** = the same endpoint with a ``realtime_start``/``realtime_end`` window. Asking for
  the full realtime range returns ONE row per (reference_date, realtime period), i.e. every
  vintage: ``realtime_start`` is the date that value became knowable → our ``vintage_date``.

``requests`` is imported lazily so the package (and the test suite) imports with no network;
unit tests inject a fake ``session`` and never touch the wire.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

FRED_BASE = "https://api.stlouisfed.org/fred"
# ALFRED realtime bounds: the FRED-documented earliest/longest range → all vintages.
FRED_EARLIEST_REALTIME = "1776-07-04"
FRED_MAX_REALTIME = "9999-12-31"
# FRED encodes a missing observation as a literal ".".
_MISSING = {".", "", None}


@dataclass(frozen=True)
class ObservationPoint:
    obs_date: date
    value: float


@dataclass(frozen=True)
class VintagePoint:
    reference_date: date
    vintage_date: date
    value: float


class MacroProvider(Protocol):
    """Fetch macro time-series from an external data source (FRED/ALFRED today; extensible)."""

    def observations(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[ObservationPoint]: ...

    def vintage_observations(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[VintagePoint]: ...


class RecordedMacroProvider:
    """In-memory macro provider for offline dev/tests."""

    def __init__(
        self,
        observations: dict[str, list[ObservationPoint]] | None = None,
        vintages: dict[str, list[VintagePoint]] | None = None,
    ):
        self._observations = observations or {}
        self._vintages = vintages or {}

    def observations(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[ObservationPoint]:
        pts = self._observations.get(series_id, [])
        if start is not None:
            pts = [p for p in pts if p.obs_date >= start]
        if end is not None:
            pts = [p for p in pts if p.obs_date <= end]
        return pts

    def vintage_observations(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[VintagePoint]:
        pts = self._vintages.get(series_id, [])
        if start is not None:
            pts = [p for p in pts if p.reference_date >= start]
        if end is not None:
            pts = [p for p in pts if p.reference_date <= end]
        return pts


class FredProvider:
    """FRED + ALFRED reader. ``session`` defaults to ``requests`` (lazy); tests pass a fake
    with a ``.get(url, params=, timeout=)`` returning an object with ``.raise_for_status()``
    and ``.json()``."""

    def __init__(self, api_key: str, *, base_url: str = FRED_BASE, session=None, timeout: int = 30):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session = session
        self.timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "api_key": self.api_key, "file_type": "json"}
        session = self._session
        if session is None:
            import requests  # lazy: keeps the module import network-free

            session = requests
        resp = session.get(f"{self.base_url}/{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def observations(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[ObservationPoint]:
        """Current value per date (MARKET). Missing (".") observations are skipped."""
        params: dict = {"series_id": series_id, "sort_order": "asc"}
        if start is not None:
            params["observation_start"] = start.isoformat()
        if end is not None:
            params["observation_end"] = end.isoformat()
        data = self._get("series/observations", params)
        out: list[ObservationPoint] = []
        for o in data.get("observations", []):
            v = o.get("value")
            if v in _MISSING:
                continue
            out.append(ObservationPoint(date.fromisoformat(o["date"]), float(v)))
        return out

    def vintage_observations(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[VintagePoint]:
        """Every vintage (ALFRED): one point per (reference_date, vintage_date). ``vintage_date``
        is the observation's ``realtime_start`` — the date that value became knowable."""
        params: dict = {
            "series_id": series_id,
            "sort_order": "asc",
            "realtime_start": FRED_EARLIEST_REALTIME,
            "realtime_end": FRED_MAX_REALTIME,
        }
        if start is not None:
            params["observation_start"] = start.isoformat()
        if end is not None:
            params["observation_end"] = end.isoformat()
        data = self._get("series/observations", params)
        out: list[VintagePoint] = []
        for o in data.get("observations", []):
            v = o.get("value")
            if v in _MISSING:
                continue
            out.append(
                VintagePoint(
                    date.fromisoformat(o["date"]),
                    date.fromisoformat(o["realtime_start"]),
                    float(v),
                )
            )
        return out
