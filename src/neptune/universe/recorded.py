"""In-memory universe source for offline dev and tests (no DB/network)."""
from __future__ import annotations

from neptune.universe import ClassificationRecord, UniverseSecurity


class RecordedUniverse:
    """A fixed list of instruments, behaving like the real universe for resolve/sync."""

    def __init__(
        self,
        rows: list[UniverseSecurity],
        classification_rows: list[ClassificationRecord] | None = None,
    ):
        self._rows = list(rows)
        self._by_ticker = {r.ticker: r for r in rows if r.ticker}
        self._classifications = list(classification_rows or [])

    def resolve_ticker(self, ticker: str) -> UniverseSecurity | None:
        return self._by_ticker.get(ticker)

    def investable_universe(self) -> list[UniverseSecurity]:
        return [r for r in self._rows if r.is_investable]

    def classifications(self, ciks: list[str]) -> list[ClassificationRecord]:
        wanted = set(ciks)
        return [c for c in self._classifications if c.entity_cik in wanted]
