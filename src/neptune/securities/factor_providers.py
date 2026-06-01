"""Factor-data providers — the Ken French side of factor ingestion (roadmap: "Ken French
factor loading", Factor Data, Critical).

A ``FactorProvider`` fetches the daily factor panel (SMB/HML/MOM, plus Mkt-RF/RF) over a
date range. Two implementations, mirroring the price providers:

* ``KenFrenchProvider`` — the real feed, fetched straight from the Ken French Data Library's
  published CSV zips over HTTPS (stdlib ``urllib`` + ``zipfile``; no third-party reader). We
  parse the CSVs ourselves, which keeps us off ``pandas_datareader`` — a stale library that
  breaks on modern pandas. Network/zip work happens lazily inside ``fetch`` so the package
  (and the test suite) needs neither the network nor heavy imports at import time. Ken French
  ships returns in **percent**; we divide by 100 to a decimal, matching the engine's
  price-return convention.
* ``RecordedFactorProvider`` — in-memory observations for offline dev/tests.

Providers return plain ``FactorObservation`` DTOs; writing rows is the ingest layer's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

# Canonical factor names Neptune stores. The engine's style factors are SMB/HML/MOM
# (see quant/factors.py); MKT comes from the SPY benchmark, not this panel. Mkt-RF and RF
# are stored too so excess-return work is possible later without a re-ingest.
KEN_FRENCH_TO_CANONICAL = {
    "Mkt-RF": "MKT_RF",
    "SMB": "SMB",
    "HML": "HML",
    "RF": "RF",
    "Mom": "MOM",
    "Mom   ": "MOM",  # the momentum file's column often carries trailing whitespace
}


@dataclass(frozen=True)
class FactorObservation:
    """One factor's return on one day, as a decimal (0.0012, not 0.12%)."""

    factor: str
    ts: date
    ret: float


class FactorProvider(Protocol):
    source: str

    def fetch(self, start: date, end: date) -> list[FactorObservation]:
        ...


class RecordedFactorProvider:
    """In-memory factor panel for offline dev/tests; returns the slice in ``[start, end]``."""

    def __init__(self, observations: list[FactorObservation], source: str = "recorded"):
        self._observations = list(observations)
        self.source = source

    def fetch(self, start: date, end: date) -> list[FactorObservation]:
        return [o for o in self._observations if start <= o.ts <= end]


class KenFrenchProvider:
    """The real Ken French Data Library feed, read straight from its published CSV zips."""

    source = "ken_french"

    BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
    # The two daily CSV zips: the 3 research factors (+ Mkt-RF/RF) and the momentum factor.
    FACTOR_ZIPS = (
        "F-F_Research_Data_Factors_daily_CSV.zip",
        "F-F_Momentum_Factor_daily_CSV.zip",
    )

    def fetch(self, start: date, end: date) -> list[FactorObservation]:
        out: list[FactorObservation] = []
        for zip_name in self.FACTOR_ZIPS:
            out.extend(parse_french_daily_csv(self._download_csv(zip_name)))
        return [o for o in out if start <= o.ts <= end]

    def _download_csv(self, zip_name: str) -> str:
        """Download a Ken French CSV zip over HTTPS and return its single CSV member as text.
        A feed/network failure raises ``RuntimeError`` so the API surfaces a clean 503."""
        import io
        import urllib.request
        import zipfile

        url = f"{self.BASE_URL}/{zip_name}"
        try:
            # A conventional browser UA — some CDNs 403 unknown agents.
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; Neptune/1.0)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed HTTPS host
                raw = resp.read()
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
                return zf.read(member).decode("latin-1")
        except (OSError, zipfile.BadZipFile, StopIteration) as exc:
            raise RuntimeError(
                f"could not fetch Ken French factor data from {url}: {exc}"
            ) from exc


def parse_french_daily_csv(text: str) -> list[FactorObservation]:
    """Parse one Ken French *daily* CSV into ``FactorObservation``s. The files lead with a
    prose header, then a column row (``,Mkt-RF,SMB,HML,RF`` or ``,Mom``), then ``YYYYMMDD``
    rows in **percent**, then a blank line + copyright footer. We find the column row, read
    the dated rows, and stop at the first non-dated line once data has started."""
    out: list[FactorObservation] = []
    columns: list[str | None] | None = None  # canonical names aligned to the data columns
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if columns is None:
            # Header row: empty leading cell, the rest are known Ken French column names.
            if cells[0] == "" and any(c in KEN_FRENCH_TO_CANONICAL for c in cells[1:]):
                columns = [KEN_FRENCH_TO_CANONICAL.get(c) for c in cells[1:]]
            continue
        token = cells[0]
        if len(token) == 8 and token.isdigit():  # a daily YYYYMMDD row
            ts = date(int(token[:4]), int(token[4:6]), int(token[6:8]))
            for canonical, raw in zip(columns, cells[1:]):
                if canonical is None or raw == "":
                    continue
                try:
                    out.append(FactorObservation(canonical, ts, float(raw) / 100.0))
                except ValueError:  # a missing value sentinel — skip the cell
                    continue
        elif out:  # past the daily block (blank line / footer) — done with this file
            break
    return out
