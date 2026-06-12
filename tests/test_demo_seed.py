"""Demo-position seeding is gated: a real book starts empty and existing demo names get
cleaned up (so a real benchmark can price every name — the market_data_for all-or-nothing
rule). Default (with_demo_positions=True) is exercised everywhere else via the golden book."""
from __future__ import annotations

from neptune.api.main import remove_demo_positions, seed_golden
from neptune.data.fixtures import GOLDEN_PORTFOLIO
from neptune.positions.service import PositionService

PID = GOLDEN_PORTFOLIO["portfolio_id"]


def test_seed_without_demo_creates_structure_but_no_positions(session):
    seed_golden(session, with_demo_positions=False)
    portfolio = PositionService(session).get_portfolio(PID)
    assert portfolio is not None  # the book + ownership graph still exist
    assert len(portfolio.positions) == 0  # ...but no demo names


def test_seed_without_book_creates_only_org_scaffolding(session):
    # Production start: no book at all (the user is forced to add one), but the ownership
    # scaffolding IS seeded so the add-portfolio pickers have a firm/entity/PM to choose.
    seed_golden(session, with_demo_positions=False, with_book=False)
    svc = PositionService(session)
    assert svc.get_portfolio(PID) is None  # no book seeded
    assert svc.list_portfolios() == []
    assert svc.get_firm("IRIDIUM") is not None
    assert {p.id for p in svc.list_people()} == {"pm-iridium"}
    assert {e.id for e in svc.list_investor_entities()} == {"IRIDIUM-FUND"}


def test_seed_scaffolding_is_idempotent(session):
    # Re-running the bookless seed (every startup) must not double-insert the firm/PM/entity.
    seed_golden(session, with_demo_positions=False, with_book=False)
    seed_golden(session, with_demo_positions=False, with_book=False)
    svc = PositionService(session)
    assert len(svc.list_firms()) == 1
    assert len(svc.list_people()) == 1
    assert len(svc.list_investor_entities()) == 1


def test_remove_demo_positions_is_idempotent(session):
    seed_golden(session, with_demo_positions=True)
    svc = PositionService(session)
    assert len(svc.get_portfolio(PID).positions) == 4  # AAA/BBB/CCC/DDD
    assert remove_demo_positions(session) == 4
    assert len(svc.get_portfolio(PID).positions) == 0
    assert remove_demo_positions(session) == 0  # nothing left — safe to run again


def test_cleanup_spares_real_positions(session):
    from datetime import date
    from neptune.domain.models import Side, ShortType

    seed_golden(session, with_demo_positions=True)
    svc = PositionService(session)
    svc.record_trade(PID, "PZZA", Side.LONG, ShortType.NA, 100, 45.0, date(2025, 1, 2))
    assert remove_demo_positions(session) == 4  # only the demo names
    tickers = [p.ticker for p in svc.get_portfolio(PID).positions]
    assert tickers == ["PZZA"]  # the real position survives
