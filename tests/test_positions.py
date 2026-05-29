"""Position Manager CRUD tests (in-memory SQLite)."""
from __future__ import annotations

import pytest

from neptune.domain.models import Position, Side, ShortType
from neptune.positions.service import ConflictError, PositionService


def test_create_and_list_positions(session):
    service = PositionService(session)
    service.create_portfolio("P1", "Test Book")
    service.add_position("P1", Position("AAA", Side.LONG, 1_000_000, forward_beta=1.2))
    service.add_position(
        "P1", Position("DDD", Side.SHORT, 500_000, short_type=ShortType.DISCRETIONARY)
    )

    positions = service.list_positions("P1")
    assert {p.ticker for p in positions} == {"AAA", "DDD"}
    assert service.get_portfolio("P1").long_aum == 1_000_000


def test_fundamental_layer_round_trips(session):
    service = PositionService(session)
    service.create_portfolio("P2", "Thesis Book")
    service.add_position(
        "P2", Position("AAA", Side.LONG, 100, thesis="activist catalyst", target="break-up")
    )
    [p] = service.list_positions("P2")
    assert p.thesis == "activist catalyst"
    assert p.target == "break-up"


def test_long_short_conflict_rejected(session):
    service = PositionService(session)
    service.create_portfolio("P3", "Conflict Book")
    service.add_position("P3", Position("AAA", Side.LONG, 1_000_000, forward_beta=1.0))
    with pytest.raises(ConflictError):
        service.add_position(
            "P3", Position("AAA", Side.SHORT, 500_000, short_type=ShortType.SYSTEMATIC)
        )


def test_delete_position(session):
    service = PositionService(session)
    service.create_portfolio("P4", "Del Book")
    pid = service.add_position("P4", Position("AAA", Side.LONG, 100, forward_beta=1.0))
    assert service.delete_position(pid) is True
    assert service.list_positions("P4") == []
