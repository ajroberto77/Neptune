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
