"""Universe adapter + projection sync tests (offline, in-memory)."""
from __future__ import annotations

from sqlalchemy import select

from neptune.securities.models import Security
from neptune.universe import RecordedUniverse, UniverseSecurity, sync_universe_projection

UNIVERSE = [
    UniverseSecurity(320193, "AAPL", "Apple Inc.", "Common Stock", primary_exch_code="UW"),
    UniverseSecurity(789019, "MSFT", "Microsoft Corp.", "Common Stock", primary_exch_code="UW"),
    # A non-common-stock / non-investable instrument: must be skipped by the sync.
    UniverseSecurity(111, "XYZ-BOND", "XYZ 5% 2030", "Senior Bond", is_investable=False),
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
