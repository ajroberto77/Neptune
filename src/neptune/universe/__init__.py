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


class UniverseSource(Protocol):
    """The read-only operations Neptune needs from the universe."""

    def resolve_ticker(self, ticker: str) -> UniverseSecurity | None:
        """Resolve a ticker to its instrument (or None if unknown)."""
        ...

    def investable_universe(self) -> list[UniverseSecurity]:
        """All instruments Neptune may trade/short (the optimizer's candidate set)."""
        ...


from neptune.universe.recorded import RecordedUniverse  # noqa: E402
from neptune.universe.sql import SqlUniverse  # noqa: E402
from neptune.universe.sync import sync_universe_projection  # noqa: E402

__all__ = [
    "UniverseSecurity",
    "UniverseSource",
    "RecordedUniverse",
    "SqlUniverse",
    "sync_universe_projection",
]
