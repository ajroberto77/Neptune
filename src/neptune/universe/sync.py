"""Project the universe identity slice into ``neptune_securities.securities``.

This is the app-level link (no cross-DB FK; see ``docs/data_architecture.md``). It reads
a ``UniverseSource`` (the read-only ``cato_securities`` adapter, or a recorded fixture)
and upserts a thin ``Security`` row per instrument — keyed on ``instrument_id``. Existing
rows are updated in place (so ticker renames flow through); ``last_synced_at`` refreshes
via the model's ``onupdate``. Also projects issuer classifications (SIC, Ken French
12-industry) for every synced security that has an ``entity_cik``, into
``security_classifications`` — a second, batched read (classification is keyed by CIK,
one issuer can own several instruments) rather than a join baked into the main sync loop.
Returns the number of ``Security`` rows synced.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from neptune.securities.models import Security, SecurityClassification
from neptune.universe import UniverseSecurity, UniverseSource


def _apply(row: Security, u: UniverseSecurity, source: str) -> None:
    row.ticker = u.ticker
    row.security_name = u.security_name
    row.security_type = u.security_type
    row.cusip = u.cusip
    row.isin = u.isin
    row.composite_figi = u.composite_figi
    row.primary_exch_code = u.primary_exch_code
    row.entity_cik = u.entity_cik
    row.source = source


def _sync_classifications(
    session: Session, source: UniverseSource, cik_to_instrument: dict[str, int], source_tag: str
) -> None:
    ciks = sorted(cik_to_instrument)
    if not ciks:
        return
    for rec in source.classifications(ciks):
        instrument_id = cik_to_instrument.get(rec.entity_cik)
        if instrument_id is None:
            continue  # a CIK the source returned that we didn't ask about — ignore
        row = session.scalar(
            select(SecurityClassification).where(
                SecurityClassification.instrument_id == instrument_id,
                SecurityClassification.scheme == rec.scheme,
                SecurityClassification.level == rec.level,
            )
        )
        if row is None:
            row = SecurityClassification(
                instrument_id=instrument_id, scheme=rec.scheme, level=rec.level
            )
            session.add(row)
        row.code = rec.code
        row.description = rec.description
        row.source = source_tag
    session.commit()


def sync_universe_projection(
    session: Session,
    source: UniverseSource,
    *,
    investable_only: bool = True,
    source_tag: str = "cato_securities",
) -> int:
    """Upsert the universe projection into ``securities`` (and ``security_classifications``
    for every synced security with an ``entity_cik``). ``session`` is a **securities-DB**
    session. With ``investable_only`` (default) only the investable set is projected; pass
    False to project everything the source exposes via that method."""
    universe = source.investable_universe()
    synced = 0
    cik_to_instrument: dict[str, int] = {}
    for u in universe:
        if investable_only and not u.is_investable:
            continue
        row = session.get(Security, u.instrument_id)
        if row is None:
            row = Security(instrument_id=u.instrument_id)
            session.add(row)
        _apply(row, u, source_tag)
        synced += 1
        if u.entity_cik:
            cik_to_instrument[u.entity_cik] = u.instrument_id
    session.commit()
    _sync_classifications(session, source, cik_to_instrument, source_tag)
    return synced
