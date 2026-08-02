"""Universe adapter + projection sync tests (offline, in-memory)."""
from __future__ import annotations

from sqlalchemy import select

from neptune.securities.models import Security, SecurityClassification
from neptune.universe import (
    ClassificationRecord,
    RecordedUniverse,
    UniverseSecurity,
    sync_universe_projection,
)

UNIVERSE = [
    UniverseSecurity(320193, "AAPL", "Apple Inc.", "Common Stock", primary_exch_code="UW"),
    UniverseSecurity(789019, "MSFT", "Microsoft Corp.", "Common Stock", primary_exch_code="UW"),
    # A non-common-stock / non-investable instrument: must be skipped by the sync.
    UniverseSecurity(111, "XYZ-BOND", "XYZ 5% 2030", "Senior Bond", is_investable=False),
]

UNIVERSE_WITH_CIKS = [
    UniverseSecurity(
        320193, "AAPL", "Apple Inc.", "Common Stock",
        primary_exch_code="UW", entity_cik="0000320193",
    ),
    UniverseSecurity(
        789019, "MSFT", "Microsoft Corp.", "Common Stock",
        primary_exch_code="UW", entity_cik="0000789019",
    ),
]

CLASSIFICATIONS = [
    ClassificationRecord("0000320193", "SIC", "code", "3571", "Electronic Computers"),
    ClassificationRecord("0000320193", "KENFRENCH_12", "group", "BusEq", "Business Equipment"),
    ClassificationRecord("0000789019", "SIC", "code", "7372", "Prepackaged Software"),
    # A CIK not in the synced universe at all -- must be ignored, not error.
    ClassificationRecord("9999999999", "SIC", "code", "0100", "Agriculture"),
]


def test_recorded_universe_resolves_and_filters():
    u = RecordedUniverse(UNIVERSE)
    assert u.resolve_ticker("AAPL").instrument_id == 320193
    assert u.resolve_ticker("NOPE") is None
    # Only investable names come back from the candidate set.
    inv = {s.ticker for s in u.investable_universe()}
    assert inv == {"AAPL", "MSFT"}


def test_sync_projects_investable_into_securities(securities_session):
    n = sync_universe_projection(securities_session, RecordedUniverse(UNIVERSE))
    assert n == 2  # the bond is excluded
    rows = securities_session.scalars(select(Security)).all()
    assert {r.instrument_id for r in rows} == {320193, 789019}
    aapl = securities_session.get(Security, 320193)
    assert aapl.ticker == "AAPL"
    assert aapl.source == "cato_securities"


def test_sync_is_idempotent_and_tracks_ticker_change(securities_session):
    sync_universe_projection(securities_session, RecordedUniverse(UNIVERSE))
    # Same instrument_id, renamed ticker (e.g. FB -> META): updated in place, not duplicated.
    renamed = [UniverseSecurity(320193, "AAPL2", "Apple Inc.", "Common Stock")]
    n = sync_universe_projection(securities_session, RecordedUniverse(renamed))
    assert n == 1
    rows = securities_session.scalars(select(Security)).all()
    # Still one row for 320193, ticker updated; MSFT from the first sync remains.
    assert sum(1 for r in rows if r.instrument_id == 320193) == 1
    assert securities_session.get(Security, 320193).ticker == "AAPL2"


def test_sync_writes_entity_cik(securities_session):
    sync_universe_projection(securities_session, RecordedUniverse(UNIVERSE_WITH_CIKS))
    assert securities_session.get(Security, 320193).entity_cik == "0000320193"
    assert securities_session.get(Security, 789019).entity_cik == "0000789019"


def test_sync_projects_classifications_keyed_by_instrument_not_cik(securities_session):
    source = RecordedUniverse(UNIVERSE_WITH_CIKS, CLASSIFICATIONS)
    sync_universe_projection(securities_session, source)

    rows = securities_session.scalars(select(SecurityClassification)).all()
    by_key = {(r.instrument_id, r.scheme, r.level): r for r in rows}

    # AAPL has both SIC and KENFRENCH_12 -- coexist, not overwrite each other.
    assert by_key[(320193, "SIC", "code")].code == "3571"
    assert by_key[(320193, "SIC", "code")].description == "Electronic Computers"
    assert by_key[(320193, "KENFRENCH_12", "group")].code == "BusEq"
    assert by_key[(789019, "SIC", "code")].code == "7372"

    # The CIK with no matching synced security was silently ignored, not a spurious row.
    assert all(r.instrument_id in (320193, 789019) for r in rows)
    assert len(rows) == 3


def test_sync_classifications_idempotent_updates_in_place(securities_session):
    source = RecordedUniverse(UNIVERSE_WITH_CIKS, CLASSIFICATIONS)
    sync_universe_projection(securities_session, source)

    # Re-sync with a revised SIC description for AAPL -- same (instrument, scheme, level)
    # key must update in place, not duplicate.
    revised = [
        ClassificationRecord("0000320193", "SIC", "code", "3571", "Electronic Computers, Revised"),
    ]
    sync_universe_projection(securities_session, RecordedUniverse(UNIVERSE_WITH_CIKS, revised))

    rows = securities_session.scalars(
        select(SecurityClassification).where(
            SecurityClassification.instrument_id == 320193,
            SecurityClassification.scheme == "SIC",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].description == "Electronic Computers, Revised"
