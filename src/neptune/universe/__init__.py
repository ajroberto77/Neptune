"""Read-only adapter for the shared ``cato_securities`` universe.

The universe is the firm's securities master (instruments / legal entities / ultimate
parents / identifiers). Neptune reads it **read-only** and never writes back (consistent
with the Fundamental Layer being read-only input). It resolves ticker ↔ instrument_id,
pulls the investable universe, and projects a thin identity slice into Neptune's own
``neptune_securities.securities`` table (the cross-DB link anchor).

The link key is ``instruments.instrument_id`` — the stable bigint surrogate. ``ticker`` is
UNIQUE upstream but mutable, so we never key on it.

Two implementations:

* ``SqlUniverse`` — runs SELECTs against a real ``cato_securities`` connection (its own
  read-only SQLAlchemy engine). Built against the schema in
  ``cato_securities_schema.md``; uses ``text()`` queries so no writable ORM is mapped.
* ``RecordedUniverse`` — an in-memory list of ``UniverseSecurity`` rows, so the projection
  sync and resolver are testable with no network/DB (this sandbox can't reach the real one).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UniverseSecurity:
    """The identity slice Neptune projects from a ``cato_securities.instruments`` row."""

    instrument_id: int
    ticker: str | None
    security_name: str | None
    security_type: str | None
    cusip: str | None = None
    isin: str | None = None
    composite_figi: str | None = None
    primary_exch_code: str | None = None
    is_investable: bool = True
    # legal_entities.entity_cik (10-digit, zero-padded) — the join key into
    # entity_classifications. None if the instrument's issuer hasn't been enriched upstream.
    entity_cik: str | None = None


@dataclass(frozen=True)
class ClassificationRecord:
    """One issuer-classification value from ``cato_securities.entity_classifications``,
    keyed by CIK (not instrument_id — classification is per-issuer, an instrument's own
    identity is resolved back to it by the caller). Mirrors CATO's own ``(scheme, level)``
    shape: multiple records per CIK are expected and normal (e.g. one for SIC, one for
    KENFRENCH_12)."""

    entity_cik: str
    scheme: str
    level: str
    code: str | None
    description: str | None


class UniverseSource(Protocol):
    """The read-only operations Neptune needs from the universe."""

    def resolve_ticker(self, ticker: str) -> UniverseSecurity | None:
        """Resolve a ticker to its instrument (or None if unknown)."""
        ...

    def investable_universe(self) -> list[UniverseSecurity]:
        """All instruments Neptune may trade/short (the optimizer's candidate set)."""
        ...

    def classifications(self, ciks: list[str]) -> list[ClassificationRecord]:
        """Issuer classifications (SIC, Ken French 12-industry) for the given CIKs. Only
        ever called with CIKs already resolved from this same source's own
        ``investable_universe()``/``resolve_ticker()`` results."""
        ...


from neptune.universe.recorded import RecordedUniverse  # noqa: E402
from neptune.universe.sql import SqlUniverse  # noqa: E402
from neptune.universe.sync import sync_universe_projection  # noqa: E402

__all__ = [
    "UniverseSecurity",
    "ClassificationRecord",
    "UniverseSource",
    "RecordedUniverse",
    "SqlUniverse",
    "sync_universe_projection",
]
