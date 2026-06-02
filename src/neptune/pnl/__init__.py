"""P&L engine — pure cost-basis accounting.

Pure functions over lots and prices: no DB, no network, no I/O (mirrors the Quant
Engine's purity, CLAUDE.md layer 1). The Risk Interface and API consume this; they do
not re-implement the lot matching.

Conventions
-----------
* A ``Lot`` holds a positive share ``quantity`` opened at ``entry_price`` on
  ``entry_date``. Direction (long/short) is a property of the position, passed in as
  ``+1`` (long) or ``-1`` (short), so the same math serves both books.
* P&L per unit = ``quantity * (price - reference) * direction`` — a long gains when
  price rises, a short gains when price falls.
* Realised P&L comes from *reducing* a position (selling a long / covering a short),
  matched against open lots by the cost-basis method.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum


class CostBasisMethod(str, Enum):
    FIFO = "FIFO"          # consume oldest lots first (default)
    AVCO = "AVCO"          # average cost across all open lots
    SPECIFIC = "SPECIFIC"  # close a specifically identified lot


@dataclass(frozen=True)
class Lot:
    """An open lot: positive share quantity bought/shorted at a price on a date.

    ``entry_price`` is the clean EXECUTION price. ``fee_per_share`` is the transaction cost
    per share; the REALIZED cost basis is ``entry_price + fee_per_share`` for a long (you paid
    the fee) and ``entry_price - fee_per_share`` for a short (the fee reduced your proceeds) —
    i.e. ``entry_price + fee_per_share * direction``. So fees always reduce P&L."""

    quantity: float
    entry_price: float
    entry_date: date
    fee_per_share: float = 0.0

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("lot quantity must be positive")
        if self.entry_price <= 0:
            raise ValueError("lot entry_price must be positive")
        if self.fee_per_share < 0:
            raise ValueError("fee_per_share must be non-negative")


def cost_basis(lot: "Lot", direction: int) -> float:
    """The realized cost basis per share: execution price adjusted by the fee in the direction
    that costs money (long pays up, short receives less)."""
    return lot.entry_price + lot.fee_per_share * direction


@dataclass(frozen=True)
class PnL:
    """The four simultaneous P&L dimensions for a position or book."""

    day: float          # since the prior close (lots opened today: since entry)
    total: float        # inception-to-date = unrealised + realised
    unrealised: float   # mark-to-market on open lots
    realised: float     # locked in by closing lots

    def __add__(self, other: "PnL") -> "PnL":
        return PnL(
            day=self.day + other.day,
            total=self.total + other.total,
            unrealised=self.unrealised + other.unrealised,
            realised=self.realised + other.realised,
        )

    @staticmethod
    def zero() -> "PnL":
        return PnL(0.0, 0.0, 0.0, 0.0)


def open_quantity(lots: list[Lot]) -> float:
    return sum(lot.quantity for lot in lots)


def unrealised_pnl(lots: list[Lot], current_price: float, direction: int) -> float:
    """Mark-to-market on open lots: qty * (current - cost_basis) * direction (fee-inclusive)."""
    return sum(
        lot.quantity * (current_price - cost_basis(lot, direction)) * direction
        for lot in lots
    )


def day_pnl(
    lots: list[Lot],
    current_price: float,
    prev_close: float,
    direction: int,
    as_of: date | None = None,
) -> float:
    """P&L since the prior close. A lot opened on or after ``as_of`` (the current mark's
    session) did not exist at the prior close, so it is measured from its cost basis;
    older lots are measured from ``prev_close``."""
    total = 0.0
    for lot in lots:
        opened_this_session = as_of is not None and lot.entry_date >= as_of
        ref = cost_basis(lot, direction) if opened_this_session else prev_close
        total += lot.quantity * (current_price - ref) * direction
    return total


def position_pnl(
    lots: list[Lot],
    current_price: float,
    prev_close: float,
    direction: int,
    realised: float = 0.0,
    as_of: date | None = None,
) -> PnL:
    """All four P&L dimensions for one position. ``realised`` is the inception-to-date
    realised P&L accumulated from prior closes."""
    unrealised = unrealised_pnl(lots, current_price, direction)
    return PnL(
        day=day_pnl(lots, current_price, prev_close, direction, as_of),
        total=unrealised + realised,
        unrealised=unrealised,
        realised=realised,
    )


def reduce_position(
    lots: list[Lot],
    quantity: float,
    exit_price: float,
    direction: int,
    method: CostBasisMethod = CostBasisMethod.FIFO,
    specific_index: int | None = None,
    exit_fee_per_share: float = 0.0,
) -> tuple[list[Lot], float]:
    """Close ``quantity`` shares at ``exit_price``, matching open lots by ``method``.

    Returns the remaining open lots and the realised P&L. Does not mutate the input. Realised
    per matched share = ``(exit - cost_basis) * direction`` (the opening fee is in cost_basis)
    minus ``exit_fee_per_share`` (the closing trade's own fee — always a cost).
    """
    if quantity <= 0:
        raise ValueError("reduction quantity must be positive")
    available = open_quantity(lots)
    if quantity > available + 1e-9:
        raise ValueError(f"cannot close {quantity}; only {available} open")

    if method is CostBasisMethod.AVCO:
        return _reduce_avco(lots, quantity, exit_price, direction, exit_fee_per_share)
    if method is CostBasisMethod.SPECIFIC:
        if specific_index is None:
            raise ValueError("SPECIFIC method requires specific_index")
        return _reduce_specific(
            lots, quantity, exit_price, direction, specific_index, exit_fee_per_share
        )
    return _reduce_fifo(lots, quantity, exit_price, direction, exit_fee_per_share)


def _reduce_fifo(lots, quantity, exit_price, direction, exit_fee_per_share=0.0):
    remaining: list[Lot] = []
    realised = 0.0
    to_close = quantity
    for lot in lots:  # oldest first (insertion order)
        if to_close <= 1e-12:
            remaining.append(lot)
            continue
        take = min(lot.quantity, to_close)
        realised += take * (exit_price - cost_basis(lot, direction)) * direction
        realised -= take * exit_fee_per_share
        to_close -= take
        leftover = lot.quantity - take
        if leftover > 1e-12:
            remaining.append(replace(lot, quantity=leftover))
    return remaining, realised


def _reduce_specific(lots, quantity, exit_price, direction, specific_index, exit_fee_per_share=0.0):
    if not 0 <= specific_index < len(lots):
        raise ValueError("specific_index out of range")
    lot = lots[specific_index]
    if quantity > lot.quantity + 1e-9:
        raise ValueError("cannot close more than the identified lot holds")
    realised = quantity * (exit_price - cost_basis(lot, direction)) * direction
    realised -= quantity * exit_fee_per_share
    remaining = list(lots)
    leftover = lot.quantity - quantity
    if leftover > 1e-12:
        remaining[specific_index] = replace(lot, quantity=leftover)
    else:
        remaining.pop(specific_index)
    return remaining, realised


def _reduce_avco(lots, quantity, exit_price, direction, exit_fee_per_share=0.0):
    total_qty = open_quantity(lots)
    avg_basis = sum(lot.quantity * cost_basis(lot, direction) for lot in lots) / total_qty
    realised = quantity * (exit_price - avg_basis) * direction - quantity * exit_fee_per_share
    leftover = total_qty - quantity
    if leftover <= 1e-12:
        return [], realised
    # Collapse remaining holding to a single lot whose entry_price IS the fee-inclusive average
    # basis (fee already folded), keeping the earliest entry date.
    earliest = min(lot.entry_date for lot in lots)
    return [Lot(quantity=leftover, entry_price=avg_basis, entry_date=earliest)], realised
