"""Project the universe identity slice into ``neptune_securities.securities``.

This is the app-level link (no cross-DB FK; see ``docs/data_architecture.md``). It reads
a ``UniverseSource`` (the read-only ``cato_securities`` adapter, or a recorded fixture)
and upserts a thin ``Security`` row per instrument — keyed on ``instrument_id``. Existing
rows are updated in place (so ticker renames flow through); ``last_synced_at`` refreshes
via the model's ``onupdate``. Returns the number of rows synced.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from neptune.securities.models import Security
from neptune.universe import UniverseSecurity, UniverseSource


def _apply(row: Security, u: UniverseSecurity, source: str) -> None:
    row.ticker = u.ticker
    row.security_name = u.security_name
    row.security_type = u.security_type
    row.cusip = u.cusip
    row.isin = u.isin
    row.composite_figi = u.composite_figi
    row.primary_exch_code = u.primary_exch_code
    row.source = source


def sync_universe_projection(
    session: Session,
    source: UniverseSource,
    *,
    investable_only: bool = True,
    source_tag: str = "cato_securities",
) -> int:
    """Upsert the universe projection into ``securities``. ``session`` is a
    **securities-DB** session. With ``investable_only`` (default) only the investable set
    is projected; pass False to project everything the source exposes via that method."""
    universe = source.investable_universe()
    synced = 0
    for u in universe:
        if investable_only and not u.is_investable:
            continue
        row = session.get(Security, u.instrument_id)
        if row is None:
            row = Security(instrument_id=u.instrument_id)
            session.add(row)
        _apply(row, u, source_tag)
        synced += 1
    session.commit()
    return synced
