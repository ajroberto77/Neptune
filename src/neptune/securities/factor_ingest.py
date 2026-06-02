"""Ingest a factor provider's panel into ``neptune_securities.factor_returns``.

Upserts daily factor returns keyed on ``(factor, ts, source)`` so re-running is idempotent
and multiple sources coexist (append-only, I-07). Mirrors the price ingest; the session is
a securities-DB session.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from neptune.securities.factor_providers import FactorProvider
from neptune.securities.models import FactorReturn


def ingest_factors(
    session: Session,
    provider: FactorProvider,
    start: date,
    end: date,
) -> dict[str, int]:
    """Upsert the factor panel for ``[start, end]``. Returns per-factor row counts."""
    observations = provider.fetch(start, end)
    counts: dict[str, int] = defaultdict(int)
    for obs in observations:
        row = session.scalar(
            select(FactorReturn).where(
                FactorReturn.factor == obs.factor,
                FactorReturn.ts == obs.ts,
                FactorReturn.source == provider.source,
            )
        )
        if row is None:
            row = FactorReturn(factor=obs.factor, ts=obs.ts, source=provider.source)
            session.add(row)
        row.ret = obs.ret
        counts[obs.factor] += 1
    session.commit()
    return dict(counts)
