"""Ingest a provider's history into the securities (market-data) database.

Upserts prices / dividends / corporate actions, keyed on each table's
``(instrument_id, …, source)`` unique constraint, so re-running is idempotent and never
duplicates (append-only with a source tag, invariant I-07). When the provider doesn't
supply ``adj_close``, it is reconstructed from the splits/dividends in the same batch via
``reconstruct_adj_close`` — so the stored adjusted series is reproducible.

The session here is a **securities-DB** session. ``Security.instrument_id`` must already
exist (projected from the universe by ``neptune.universe.sync``); ingest fetches by ticker
through that projection and FKs every fact to it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from neptune.securities.adjust import reconstruct_adj_close
from neptune.securities.models import (
    CorporateAction,
    Dividend,
    Price,
    Security,
)
from neptune.securities.providers import PriceProvider, SecurityHistory


@dataclass
class IngestResult:
    """Per-security counts from one ingest run."""

    instrument_id: int
    ticker: str | None
    prices: int = 0
    dividends: int = 0
    corporate_actions: int = 0


def ingest_history(
    session: Session,
    security: Security,
    history: SecurityHistory,
    source: str,
) -> IngestResult:
    """Upsert one security's history. Idempotent: existing ``(…, source)`` rows are
    updated in place, new ones inserted. Returns the rows touched."""
    result = IngestResult(instrument_id=security.instrument_id, ticker=security.ticker)

    # Reconstruct adj_close for any bar the source left blank (uses this batch's events).
    adj = reconstruct_adj_close(
        history.prices, history.dividends, history.corporate_actions
    )

    for bar in history.prices:
        row = session.scalar(
            select(Price).where(
                Price.instrument_id == security.instrument_id,
                Price.ts == bar.ts,
                Price.source == source,
            )
        )
        if row is None:
            row = Price(instrument_id=security.instrument_id, ts=bar.ts, source=source)
            session.add(row)
        row.open = bar.open
        row.high = bar.high
        row.low = bar.low
        row.close = bar.close
        # Prefer the source's adj_close; else the reconstruction; never store a null
        # adjusted close (a downstream landmine for the beta pipeline) — fall back to raw.
        row.adj_close = (
            bar.adj_close if bar.adj_close is not None else adj.get(bar.ts, bar.close)
        )
        row.volume = bar.volume
        result.prices += 1

    for div in history.dividends:
        row = session.scalar(
            select(Dividend).where(
                Dividend.instrument_id == security.instrument_id,
                Dividend.ex_date == div.ex_date,
                Dividend.source == source,
            )
        )
        if row is None:
            row = Dividend(
                instrument_id=security.instrument_id, ex_date=div.ex_date, source=source
            )
            session.add(row)
        row.pay_date = div.pay_date
        row.amount = div.amount
        row.currency = div.currency
        result.dividends += 1

    for ca in history.corporate_actions:
        row = session.scalar(
            select(CorporateAction).where(
                CorporateAction.instrument_id == security.instrument_id,
                CorporateAction.effective_date == ca.effective_date,
                CorporateAction.action_type == ca.action_type,
                CorporateAction.source == source,
            )
        )
        if row is None:
            row = CorporateAction(
                instrument_id=security.instrument_id,
                effective_date=ca.effective_date,
                action_type=ca.action_type,
                source=source,
            )
            session.add(row)
        row.ratio = ca.ratio
        row.old_value = ca.old_value
        row.new_value = ca.new_value
        result.corporate_actions += 1

    session.commit()
    return result


def has_sufficient_history(session: Session, ticker: str, min_bars: int = 200) -> bool:
    """Whether ``ticker`` already has at least ``min_bars`` stored price rows — used to skip
    a redundant re-pull when a name is already well-priced."""
    iid = session.scalar(select(Security.instrument_id).where(Security.ticker == ticker))
    if iid is None:
        return False
    bars = session.scalar(select(func.count(Price.ts)).where(Price.instrument_id == iid)) or 0
    return bars >= min_bars


def count_projected_securities(session: Session) -> int:
    """How many securities exist in the local projection, independent of pricing."""
    return session.scalar(select(func.count(Security.instrument_id))) or 0


def projected_tickers(session: Session) -> list[str]:
    """Every ticker in the local projection (no pricing requirement) — the default backfill
    target when a caller doesn't name specific tickers."""
    return [
        t for (t,) in session.execute(
            select(Security.ticker).where(Security.ticker.is_not(None))
        ).all()
    ]


def tickers_with_min_bars(
    session: Session, *, min_bars: int = 30, exclude: str | None = None
) -> list[str]:
    """Tickers (sorted) with at least ``min_bars`` stored price rows, optionally excluding one
    (e.g. the benchmark, reported separately in the caller's diagnostics)."""
    stmt = (
        select(Security.ticker)
        .join(Price, Price.instrument_id == Security.instrument_id)
        .where(Security.ticker.isnot(None))
        .group_by(Security.ticker)
        .having(func.count(Price.ts) >= min_bars)
    )
    if exclude is not None:
        stmt = stmt.where(Security.ticker != exclude)
    rows = session.execute(stmt).all()
    return sorted(t for (t,) in rows)


def ingest_ticker(
    session: Session,
    provider: PriceProvider,
    ticker: str,
    start: date,
    end: date,
    create_if_missing: bool = False,
) -> IngestResult:
    """Resolve ``ticker`` to its ``Security`` and ingest its history. If the ticker isn't in
    the projection: raise ``LookupError`` (default), or — when ``create_if_missing`` — create
    a minimal local Security for it. The latter is how benchmarks/ETFs (e.g. SPY) that live
    outside the common-stock universe get a home: they're tagged with a stable NEGATIVE
    instrument_id (never collides with the universe's positive surrogates) and source
    ``"manual"``, so they're clearly not universe-sourced."""
    security = session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        if not create_if_missing:
            raise LookupError(
                f"ticker {ticker!r} not in securities projection — sync the universe first"
            )
        security = Security(
            instrument_id=_local_instrument_id(ticker),
            ticker=ticker,
            source="manual",
        )
        session.add(security)
        session.commit()
    # Enrich the sector (best-effort) for the hard sector-concentration cap, if the provider
    # supports it and we don't already have one. This is now a FALLBACK, not the primary
    # source: neptune.universe.sync_universe_projection already fills this in from CATO's own
    # Yahoo-tier classification when it has one (see that module's docstring) — this only
    # fires for names CATO hasn't covered (e.g. benchmarks/ETFs outside the universe) or a
    # ticker that hasn't been through a universe sync yet. Never fatal to the price ingest.
    if security.sector is None and hasattr(provider, "fetch_sector"):
        sector = provider.fetch_sector(ticker)
        if sector:
            security.sector = sector
            session.commit()
    history = provider.fetch(ticker, start, end)
    return ingest_history(session, security, history, provider.source)


def _local_instrument_id(ticker: str) -> int:
    """A stable negative surrogate id for a non-universe security (benchmark/ETF). Negative
    so it can never collide with the universe's positive ``instrument_id``s; stable (sha1,
    not Python's randomized ``hash``) so re-ingesting the same ticker reuses the row."""
    import hashlib

    digest = hashlib.sha1(ticker.encode()).hexdigest()
    return -(int(digest[:12], 16) % 2_000_000_000)
