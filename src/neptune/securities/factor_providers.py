"""Factor-data providers — the Ken French side of factor ingestion (roadmap: "Ken French
factor loading", Factor Data, Critical).

A ``FactorProvider`` fetches the daily factor panel (SMB/HML/MOM, plus Mkt-RF/RF) over a
date range. Two implementations, mirroring the price providers:

* ``KenFrenchProvider`` — the real feed via ``pandas_datareader``'s ``famafrench`` reader,
  imported lazily so the package (and the test suite) needs neither the library nor the
  network at import time. Ken French ships returns in **percent**; we divide by 100 to a
  decimal, matching the price-return convention the engine uses.
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
    """The real Ken French Data Library feed via ``pandas_datareader`` (lazy import)."""

    source = "ken_french"

    # The two daily datasets in the famafrench reader.
    FACTORS_DATASET = "F-F_Research_Data_Factors_daily"
    MOMENTUM_DATASET = "F-F_Momentum_Factor_daily"

    def fetch(self, start: date, end: date) -> list[FactorObservation]:
        try:
            import pandas_datareader.data as web
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "pandas_datareader is not installed; `pip install pandas-datareader` "
                "to enable Ken French factor ingest"
            ) from exc

        out: list[FactorObservation] = []
        # famafrench returns a dict of DataFrames; [0] is the daily panel, in percent.
        ff = web.DataReader(self.FACTORS_DATASET, "famafrench", start, end)[0]
        mom = web.DataReader(self.MOMENTUM_DATASET, "famafrench", start, end)[0]
        for frame in (ff, mom):
            cols = {c: KEN_FRENCH_TO_CANONICAL.get(str(c).strip(), None) for c in frame.columns}
            for idx, row in frame.iterrows():
                ts = idx.date() if hasattr(idx, "date") else idx
                for col, canonical in cols.items():
                    if canonical is None:
                        continue
                    out.append(
                        FactorObservation(factor=canonical, ts=ts, ret=float(row[col]) / 100.0)
                    )
        return out
