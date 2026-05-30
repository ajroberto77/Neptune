"""Risk Interface: book-level P&L.

Marks each position's lots against the market-data source and aggregates the four P&L
dimensions, split by book (Long / Systematic Short / Discretionary Short). Systematic
and discretionary shorts are reported separately and never merged (invariant I-03).
"""
from __future__ import annotations

from dataclasses import dataclass

from neptune.data.market import SyntheticMarketData
from neptune.domain.models import BookType, Portfolio, Position
from neptune.pnl import Lot, PnL, position_pnl


def position_pnl_for(position: Position, market: SyntheticMarketData) -> PnL:
    """Mark one position's lots and return its four P&L dimensions. A position with no
    lots reports zero (notional-only positions carry no cost basis yet)."""
    if not position.lots:
        return PnL.zero()
    lots = [Lot(l.quantity, l.entry_price, l.entry_date) for l in position.lots]
    return position_pnl(
        lots=lots,
        current_price=market.current_price(position.ticker),
        prev_close=market.prev_close(position.ticker),
        direction=position.direction,
        realised=position.realised_pnl,
    )


@dataclass
class BookPnL:
    book: BookType
    pnl: PnL


@dataclass
class PortfolioPnL:
    """Total P&L plus a per-book breakdown."""

    total: PnL
    by_book: dict[BookType, PnL]


def portfolio_pnl(portfolio: Portfolio, market: SyntheticMarketData) -> PortfolioPnL:
    """Aggregate position P&L across the book, split by the three reporting books."""
    by_book: dict[BookType, PnL] = {b: PnL.zero() for b in BookType}
    total = PnL.zero()
    for position in portfolio.positions:
        pnl = position_pnl_for(position, market)
        by_book[position.book] = by_book[position.book] + pnl
        total = total + pnl
    return PortfolioPnL(total=total, by_book=by_book)
