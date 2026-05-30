"""P&L engine: cost-basis matching (FIFO/AVCO/Specific) and the four P&L dimensions."""
from __future__ import annotations

from datetime import date

import pytest

from neptune.pnl import (
    CostBasisMethod,
    Lot,
    PnL,
    position_pnl,
    reduce_position,
    unrealised_pnl,
)

LONG, SHORT = 1, -1
D1 = date(2026, 5, 1)
D2 = date(2026, 5, 15)
TODAY = date(2026, 5, 30)


def _two_lots():
    # 100 @ $10 (older), then 100 @ $12 (newer).
    return [Lot(100, 10.0, D1), Lot(100, 12.0, D2)]


# --- unrealised / four dimensions ------------------------------------------------

def test_unrealised_long_and_short():
    lots = _two_lots()
    # Long @ price 15: 100*(15-10) + 100*(15-12) = 500 + 300 = 800.
    assert unrealised_pnl(lots, 15.0, LONG) == pytest.approx(800.0)
    # Short @ price 15: signs flip -> -800.
    assert unrealised_pnl(lots, 15.0, SHORT) == pytest.approx(-800.0)


def test_four_dimensions_total_is_unrealised_plus_realised():
    lots = [Lot(100, 10.0, D1)]
    pnl = position_pnl(lots, current_price=15.0, prev_close=14.0, direction=LONG,
                       realised=250.0, as_of=TODAY)
    assert pnl.unrealised == pytest.approx(500.0)       # 100*(15-10)
    assert pnl.day == pytest.approx(100.0)              # 100*(15-14), lot predates today
    assert pnl.realised == pytest.approx(250.0)
    assert pnl.total == pytest.approx(750.0)            # unrealised + realised


def test_day_pnl_for_lot_opened_today_measures_from_entry():
    lots = [Lot(100, 14.5, TODAY)]  # opened today
    pnl = position_pnl(lots, current_price=15.0, prev_close=14.0, direction=LONG, as_of=TODAY)
    # Did not exist at prior close, so day P&L is from entry: 100*(15-14.5)=50, not 100.
    assert pnl.day == pytest.approx(50.0)


def test_pnl_addition_aggregates_books():
    a = PnL(1, 2, 3, 4)
    b = PnL(10, 20, 30, 40)
    assert (a + b) == PnL(11, 22, 33, 44)
    assert PnL.zero() == PnL(0, 0, 0, 0)


# --- FIFO ------------------------------------------------------------------------

def test_fifo_consumes_oldest_first():
    lots = _two_lots()
    # Sell 150 @ $13: 100 from the $10 lot + 50 from the $12 lot.
    remaining, realised = reduce_position(lots, 150, 13.0, LONG, CostBasisMethod.FIFO)
    # 100*(13-10) + 50*(13-12) = 300 + 50 = 350.
    assert realised == pytest.approx(350.0)
    # 50 shares of the $12 lot remain.
    assert len(remaining) == 1
    assert remaining[0].quantity == pytest.approx(50.0)
    assert remaining[0].entry_price == pytest.approx(12.0)


def test_fifo_short_cover():
    lots = [Lot(100, 20.0, D1)]
    # Cover 100 @ $18 on a short: 100*(18-20)*-1 = +200 (bought back cheaper).
    remaining, realised = reduce_position(lots, 100, 18.0, SHORT, CostBasisMethod.FIFO)
    assert realised == pytest.approx(200.0)
    assert remaining == []


# --- AVCO ------------------------------------------------------------------------

def test_avco_uses_average_cost():
    lots = _two_lots()  # avg cost = (1000+1200)/200 = 11.0
    remaining, realised = reduce_position(lots, 100, 13.0, LONG, CostBasisMethod.AVCO)
    assert realised == pytest.approx(100 * (13.0 - 11.0))  # 200
    # Remaining 100 shares collapse to one lot at the average cost.
    assert len(remaining) == 1
    assert remaining[0].entry_price == pytest.approx(11.0)
    assert remaining[0].quantity == pytest.approx(100.0)


# --- Specific lot ----------------------------------------------------------------

def test_specific_lot_closes_identified_lot():
    lots = _two_lots()
    # Close 100 of the newer $12 lot (index 1) @ $13: 100*(13-12)=100.
    remaining, realised = reduce_position(
        lots, 100, 13.0, LONG, CostBasisMethod.SPECIFIC, specific_index=1
    )
    assert realised == pytest.approx(100.0)
    assert len(remaining) == 1
    assert remaining[0].entry_price == pytest.approx(10.0)  # older lot untouched


def test_specific_requires_index():
    with pytest.raises(ValueError):
        reduce_position(_two_lots(), 10, 13.0, LONG, CostBasisMethod.SPECIFIC)


# --- guards ----------------------------------------------------------------------

def test_cannot_close_more_than_open():
    with pytest.raises(ValueError):
        reduce_position(_two_lots(), 300, 13.0, LONG)


def test_lot_validates_positive_quantity_and_price():
    with pytest.raises(ValueError):
        Lot(0, 10.0, D1)
    with pytest.raises(ValueError):
        Lot(100, 0.0, D1)
