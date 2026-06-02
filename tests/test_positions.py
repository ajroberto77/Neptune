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


def test_clear_systematic_shorts_replaces_not_stacks(session):
    """Approving a hedge must REPLACE the systematic short book, not add to it. Booking the same
    basket twice via clear→record yields ONE position per name (not doubled), and discretionary
    shorts are never touched."""
    from datetime import date

    service = PositionService(session)
    service.create_portfolio("PH", "Hedge Book")
    service.add_position("PH", Position("LONGX", Side.LONG, 5_000_000))
    service.add_position(
        "PH", Position("DISCX", Side.SHORT, 500_000, short_type=ShortType.DISCRETIONARY)
    )

    def book_basket():
        service.clear_systematic_shorts("PH")  # replace, not stack
        service.record_trade("PH", "BSX", Side.SHORT, ShortType.SYSTEMATIC, 1000, 50.0, date.today())
        service.record_trade("PH", "MSFT", Side.SHORT, ShortType.SYSTEMATIC, 200, 400.0, date.today())

    book_basket()
    book_basket()  # second propose→approve cycle must not double the systematic book

    positions = {(p.ticker): p for p in service.list_positions("PH") if p.notional > 0}
    sys = [p for p in positions.values() if p.short_type is ShortType.SYSTEMATIC]
    assert {p.ticker for p in sys} == {"BSX", "MSFT"}  # exactly the basket, not 2× rows
    bsx = positions["BSX"]
    assert sum(l.quantity for l in bsx.lots) == 1000  # NOT 2000 — replaced, not aggregated
    assert "DISCX" in positions  # discretionary short untouched (I-03/I-04)
    assert positions["DISCX"].short_type is ShortType.DISCRETIONARY


def test_long_only_book_rejects_shorting(session):
    """A LONG_ONLY mandate blocks any short (systematic hedge or discretionary) at the booking
    layer — longs are fine, shorts raise. The other books keep their default LONG_SHORT mandate."""
    from datetime import date
    from neptune.domain.models import Mandate, TradeAction

    service = PositionService(session)
    service.create_portfolio("LO", "Long Only Book", mandate=Mandate.LONG_ONLY.value)
    assert service.get_portfolio("LO").mandate is Mandate.LONG_ONLY

    # Longs are allowed.
    service.record_trade("LO", "AAA", Side.LONG, ShortType.NA, 100, 50.0, date.today())
    # Any short is rejected — systematic and discretionary alike.
    with pytest.raises(ConflictError):
        service.record_trade("LO", "BSX", Side.SHORT, ShortType.SYSTEMATIC, 100, 50.0, date.today())
    # A manual SELL beyond the long would open a discretionary short → also rejected.
    with pytest.raises(ConflictError):
        service.book_trade("LO", "AAA", TradeAction.SELL, 500, 50.0, date.today())

    service.create_portfolio("LS", "Hedged Book")  # default mandate
    assert service.get_portfolio("LS").mandate is Mandate.LONG_SHORT


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


def test_fee_per_share_is_stored_and_in_cost_basis(session):
    """Execution price and fee/share are stored SEPARATELY; the realized cost basis is their
    sum. A long bought at $50 with $2/sh fee, marked at $50, loses the fee."""
    from datetime import date
    from neptune.domain.models import TradeAction
    from neptune.pnl import Lot, unrealised_pnl

    service = PositionService(session)
    service.create_portfolio("PF", "Fee Book")
    service.book_trade("PF", "AAA", TradeAction.BUY, 100, 50.0, date.today(), fee_per_share=2.0)
    lot = service.list_positions("PF")[0].lots[0]
    assert lot.entry_price == pytest.approx(50.0)   # clean execution price stored
    assert lot.fee_per_share == pytest.approx(2.0)  # fee stored separately
    # Realized cost basis is 52 for a long: marking at the $50 execution price loses the fee.
    pnl = unrealised_pnl([Lot(100, 50.0, date.today(), 2.0)], current_price=50.0, direction=1)
    assert pnl == pytest.approx(-200.0)             # 100 * (50 - 52)
