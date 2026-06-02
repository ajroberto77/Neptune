"""Risk Interface: book-level P&L.

Marks each position's lots against the market-data source and aggregates the four P&L
dimensions, split by book (Long / Systematic Short / Discretionary Short). Systematic
and discretionary shorts are reported separately and never merged (invariant I-03).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from neptune.data.db_market import TickerNotFound
from neptune.data.source import MarketData
from neptune.domain.models import BookType, Portfolio, Position
from neptune.pnl import Lot, PnL, position_pnl


def position_pnl_for(position: Position, market: MarketData, as_of: date | None = None) -> PnL:
    """Mark one position's lots and return its four P&L dimensions. ``as_of`` (defaulting to
    today) lets a lot opened *today* measure its day P&L from its entry price rather than the
    prior close — so for a same-day trade day P&L == unrealised. A fully-closed position (no
    open lots) still reports its locked-in realised P&L."""
    if not position.lots:
        realised = position.realised_pnl
        return PnL(day=0.0, total=realised, unrealised=0.0, realised=realised)
    try:
        current_price = market.current_price(position.ticker)
        prev_close = market.prev_close(position.ticker)
    except TickerNotFound:
        # Not priced yet (graceful): no mark, so only locked-in realised P&L is known.
        realised = position.realised_pnl
        return PnL(day=0.0, total=realised, unrealised=0.0, realised=realised)
    lots = [
        Lot(l.quantity, l.entry_price, l.entry_date, l.fee_per_share) for l in position.lots
    ]
    # Anchor "today" to the data's latest mark date (not the wall clock), so a same-day trade
    # is recognized regardless of the client/server timezone. Falls back to today for sources
    # without a calendar.
    if as_of is None:
        mark = getattr(market, "mark_date", None)
        as_of = mark(position.ticker) if mark is not None else date.today()
    return position_pnl(
        lots=lots,
        current_price=current_price,
        prev_close=prev_close,
        direction=position.direction,
        realised=position.realised_pnl,
        as_of=as_of,
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


def portfolio_pnl(portfolio: Portfolio, market: MarketData) -> PortfolioPnL:
    """Aggregate position P&L across the book, split by the three reporting books."""
    by_book: dict[BookType, PnL] = {b: PnL.zero() for b in BookType}
    total = PnL.zero()
    for position in portfolio.positions:
        pnl = position_pnl_for(position, market)
        by_book[position.book] = by_book[position.book] + pnl
        total = total + pnl
    return PortfolioPnL(total=total, by_book=by_book)
