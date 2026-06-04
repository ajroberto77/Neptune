"""Position Manager service.

Wraps the repository with the invariants that belong at the book level — notably I-05:
Neptune never shorts a name held long in a live portfolio (here enforced within the
portfolio; the cross-portfolio runtime join is deferred with Book-of-Books).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from neptune.db.repository import OrgRepository, PositionRepository, TransactionRepository
from neptune.domain.models import (
    LotEntry, Mandate, Portfolio, Position, Side, ShortType, TradeAction, TradeOrigin,
    Transaction,
)
from neptune.domain.org import PersonRole
from neptune.pnl import CostBasisMethod, Lot, reduce_position


class ConflictError(ValueError):
    """Raised when an action would violate a position-book invariant."""


class PositionService:
    def __init__(self, session: Session):
        self.repo = PositionRepository(session)
        self.org = OrgRepository(session)
        self.tx = TransactionRepository(session)

    def _record_tx(
        self, portfolio_id: str, ticker: str, action: TradeAction, quantity: float,
        price: float, trade_date: date, *, short_type: ShortType = ShortType.NA,
        origin: TradeOrigin = TradeOrigin.MANUAL, realized_pnl: float = 0.0,
        effect: str = "", fee_per_share: float = 0.0,
    ) -> int:
        """Append one row to the blotter (the executed-trade ledger)."""
        return self.tx.add(Transaction(
            ticker=ticker, action=action, quantity=quantity, price=price,
            trade_date=trade_date, short_type=short_type, origin=origin,
            realized_pnl=realized_pnl, effect=effect, fee_per_share=fee_per_share,
            portfolio_id=portfolio_id,
        ))

    def transactions(self, portfolio_id: str, *, limit: int | None = None) -> list[Transaction]:
        return self.tx.list_for(portfolio_id, limit=limit)

    def transactions_many(self, portfolio_ids: list[str], *, limit: int | None = None) -> list[Transaction]:
        return self.tx.list_many(portfolio_ids, limit=limit)

    def create_portfolio(self, portfolio_id: str, name: str, **kwargs) -> Portfolio:
        return self.repo.create_portfolio(portfolio_id, name, **kwargs)

    # --- Organization / ownership -------------------------------------------------
    def create_firm(self, firm_id: str, name: str, **kwargs):
        return self.org.create_firm(firm_id, name, **kwargs)

    def create_person(self, person_id: str, firm_id: str, name: str, role: PersonRole, **kwargs):
        return self.org.create_person(person_id, firm_id, name, role, **kwargs)

    def create_investor_entity(self, entity_id: str, firm_id: str, name: str, **kwargs):
        return self.org.create_investor_entity(entity_id, firm_id, name, **kwargs)

    def set_book_managers(self, portfolio_id: str, person_ids: list[str], is_lead: bool = True):
        return self.org.set_book_managers(portfolio_id, person_ids, is_lead)

    def effective_pm(self, portfolio_id: str, position: Position) -> str | None:
        """The PM responsible for a name: its own ``pm_id`` if set, else the book's first
        lead PM. Returns None if neither is assigned (e.g. the bare synthetic slice)."""
        if position.pm_id is not None:
            return position.pm_id
        portfolio = self.repo.get_portfolio(portfolio_id)
        if portfolio and portfolio.lead_pm_ids:
            return portfolio.lead_pm_ids[0]
        return None

    def get_portfolio(self, portfolio_id: str) -> Portfolio | None:
        return self.repo.get_portfolio(portfolio_id)

    def list_portfolios(self) -> list[Portfolio]:
        return self.repo.list_portfolios()

    def add_position(self, portfolio_id: str, position: Position) -> int:
        if position.side is Side.SHORT:
            book = self.repo.get_portfolio(portfolio_id)
            if book is not None and book.mandate is Mandate.LONG_ONLY:
                raise ConflictError(
                    f"{portfolio_id!r} is a LONG_ONLY book — shorting is not allowed."
                )
        existing = self.repo.list_positions(portfolio_id)
        self._check_long_short_conflict(position, existing)
        return self.repo.add_position(portfolio_id, position)

    def record_trade(
        self,
        portfolio_id: str,
        ticker: str,
        side: Side,
        short_type: ShortType,
        quantity: float,
        price: float,
        trade_date: date,
        sector: str | None = None,
        forward_beta: float | None = None,
        thesis: str | None = None,
        target: str | None = None,
        fee_per_share: float = 0.0,
    ) -> int:
        """Record an executed trade as a lot on the (ticker, side, short_type) position,
        aggregating into the open position for that name+book if one exists, else opening a
        new one. ``notional`` grows by the executed value (quantity × price), so it tracks
        book exposure for trade-tab positions. Returns the position id.

        This is the single entry path for ALL executions, including systematic-short hedges
        once approved: recording an execution is not auto-execution (no broker routing), and
        the book tag (``short_type``) keeps systematic and discretionary shorts distinct
        (I-03)."""
        if side is Side.SHORT:
            book = self.repo.get_portfolio(portfolio_id)
            if book is not None and book.mandate is Mandate.LONG_ONLY:
                raise ConflictError(
                    f"{portfolio_id!r} is a LONG_ONLY book — shorting is not allowed "
                    f"(no systematic hedge, no discretionary short)."
                )
        lot = LotEntry(quantity=quantity, entry_price=price, entry_date=trade_date,
                       fee_per_share=fee_per_share)
        existing = self.repo.list_positions(portfolio_id)
        probe = Position(
            ticker=ticker, side=side, notional=quantity * price, short_type=short_type
        )
        self._check_long_short_conflict(probe, existing)

        position_id = self.repo.find_position_id(portfolio_id, ticker, side, short_type)
        if position_id is not None:
            current = self.repo.get_position(position_id)
            self.repo.append_lot(position_id, lot, current.notional + quantity * price)
            return position_id

        return self.repo.add_position(
            portfolio_id,
            Position(
                ticker=ticker,
                side=side,
                notional=quantity * price,
                short_type=short_type,
                sector=sector,
                forward_beta=forward_beta,
                lots=[lot],
                thesis=thesis,
                target=target,
            ),
        )

    def clear_systematic_shorts(self, portfolio_id: str) -> int:
        """Remove every SYSTEMATIC short in the book and return how many were removed.

        The systematic short book is the optimizer's output, and a hedge proposal is computed
        as a FULL replacement (``residual_metrics`` excludes systematic shorts, so each propose
        re-sizes the whole hedge against the long book). Approving must therefore REPLACE the
        existing systematic book, not stack a second hedge on top of it — otherwise repeated
        propose→approve cycles double the short exposure. Discretionary shorts are never touched
        (I-03/I-04); only the system-owned systematic names are cleared."""
        removed = 0
        for p in self.repo.list_positions(portfolio_id):
            if p.side is Side.SHORT and p.short_type is ShortType.SYSTEMATIC and p.id is not None:
                if self.repo.delete_position(p.id):
                    removed += 1
        return removed

    def apply_systematic_hedge(
        self,
        portfolio_id: str,
        targets: dict[str, tuple[float, float]],
        cover_prices: dict[str, float],
        trade_date: date,
    ) -> dict:
        """Reconcile the systematic short book to a target basket by BOOKING TRADES, not by
        deleting positions. ``targets`` maps ticker → (target_shares, short_price); ``cover_prices``
        maps ticker → the mark to cover at (falls back to the position's average price, so a
        missing mark covers at cost = 0 realized).

        For each name the delta vs. the current systematic holding is traded:
          * target < held  → BUY-to-cover the difference (books realized P&L on the close),
          * target > held  → SELL-short the difference (adds to / opens the hedge),
          * equal          → no trade (the name stays untouched — no churn).

        Every leg writes a blotter row (``origin=HEDGE``). Discretionary shorts are never read
        or touched (I-03/I-04). Returns a summary: covered/opened counts and total realized P&L."""
        current = {
            p.ticker: p
            for p in self.repo.list_positions(portfolio_id)
            if p.side is Side.SHORT and p.short_type is ShortType.SYSTEMATIC and p.quantity > 0
        }
        covered = opened = 0
        realized_total = 0.0
        # 1) Covers first (names removed or reduced) — frees the round-trip realized P&L.
        for ticker, pos in current.items():
            tgt_shares = targets.get(ticker, (0.0, 0.0))[0]
            if tgt_shares < pos.quantity - 1e-9:
                cover_qty = pos.quantity - tgt_shares
                avg_price = pos.notional / pos.quantity if pos.quantity else 0.0
                cprice = cover_prices.get(ticker) or avg_price
                realized = self.reduce_position(pos.id, cover_qty, cprice, as_of=trade_date)
                realized_total += realized
                self._record_tx(
                    portfolio_id, ticker, TradeAction.BUY, cover_qty, cprice, trade_date,
                    short_type=ShortType.SYSTEMATIC, origin=TradeOrigin.HEDGE,
                    realized_pnl=realized,
                    effect="Cover systematic" if tgt_shares <= 1e-9 else "Reduce systematic",
                )
                covered += 1
        # 2) Then shorts (names added or increased).
        for ticker, (shares, sprice) in targets.items():
            cur_qty = current[ticker].quantity if ticker in current else 0.0
            if shares > cur_qty + 1e-9:
                add_qty = shares - cur_qty
                self.record_trade(
                    portfolio_id, ticker, Side.SHORT, ShortType.SYSTEMATIC,
                    add_qty, sprice, trade_date,
                )
                self._record_tx(
                    portfolio_id, ticker, TradeAction.SELL, add_qty, sprice, trade_date,
                    short_type=ShortType.SYSTEMATIC, origin=TradeOrigin.HEDGE,
                    effect="Sell short (hedge)" if cur_qty <= 1e-9 else "Increase systematic",
                )
                opened += 1
        return {"covered": covered, "opened": opened, "realized_pnl": realized_total}

    def apply_hedge_legs(
        self,
        portfolio_id: str,
        legs: list[tuple[str, TradeAction, float, float]],
        trade_date: date,
    ) -> dict:
        """Book an EXPLICIT list of hedge legs (what-you-see-is-what-gets-booked), each
        ``(ticker, action, shares, price)``: a SELL opens/increases the systematic short; a
        BUY covers/reduces it (booking realized P&L at ``price``, the cover mark). Used by the
        delta-aware Trade grid, where the rows already represent the reconciliation against the
        live hedge. Only the systematic book is touched (I-03/I-04); nothing is routed (§2)."""
        current = {
            p.ticker: p
            for p in self.repo.list_positions(portfolio_id)
            if p.side is Side.SHORT and p.short_type is ShortType.SYSTEMATIC and p.quantity > 0
        }
        covered = opened = 0
        realized_total = 0.0
        for ticker, action, shares, price in legs:
            if shares <= 0:
                continue
            if action is TradeAction.SELL:  # open / increase the systematic short
                self.record_trade(
                    portfolio_id, ticker, Side.SHORT, ShortType.SYSTEMATIC,
                    shares, price, trade_date,
                )
                self._record_tx(
                    portfolio_id, ticker, TradeAction.SELL, shares, price, trade_date,
                    short_type=ShortType.SYSTEMATIC, origin=TradeOrigin.HEDGE,
                    effect="Sell short (hedge)" if ticker not in current else "Increase systematic",
                )
                opened += 1
            else:  # BUY = cover / reduce the systematic short
                pos = current.get(ticker)
                if pos is None:
                    continue  # nothing live to cover (already flat) — skip silently
                cover_qty = min(shares, pos.quantity)
                realized = self.reduce_position(pos.id, cover_qty, price, as_of=trade_date)
                realized_total += realized
                self._record_tx(
                    portfolio_id, ticker, TradeAction.BUY, cover_qty, price, trade_date,
                    short_type=ShortType.SYSTEMATIC, origin=TradeOrigin.HEDGE,
                    realized_pnl=realized,
                    effect="Cover systematic" if cover_qty >= pos.quantity - 1e-9 else "Reduce systematic",
                )
                covered += 1
        return {"covered": covered, "opened": opened, "realized_pnl": realized_total}

    def book_trade(
        self,
        portfolio_id: str,
        ticker: str,
        action: TradeAction,
        quantity: float,
        price: float,
        trade_date: date,
        sector: str | None = None,
        thesis: str | None = None,
        target: str | None = None,
        fee_per_share: float = 0.0,
    ) -> int:
        """Book a manual Buy/Sell. Direction is DERIVED from the current holding (netting),
        so the desk never picks a side or a "book":

          * BUY  → cover an open discretionary short first, then open/add a LONG with any
                   remainder (crossing zero flips short → long).
          * SELL → reduce an open long first, then open/add a DISCRETIONARY SHORT with any
                   remainder (crossing zero flips long → short).

        Manual trades only ever touch the long or the *discretionary* short — systematic
        shorts are the optimizer's hedge and are booked only via the hedge-approval path
        (invariant I-03). Returns the id of the position the remainder landed on (or the one
        that was reduced if the trade only closed).
        """
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        remaining = quantity
        if action is TradeAction.BUY:
            opposite = self.repo.find_position_id(
                portfolio_id, ticker, Side.SHORT, ShortType.DISCRETIONARY
            )
            open_side, open_short = Side.LONG, ShortType.NA
        else:
            opposite = self.repo.find_position_id(
                portfolio_id, ticker, Side.LONG, ShortType.NA
            )
            open_side, open_short = Side.SHORT, ShortType.DISCRETIONARY

        landed = opposite
        realized = 0.0
        closed_qty = 0.0
        if opposite is not None:  # net against the existing opposite-side position first
            held = self.repo.get_position(opposite).quantity
            close = min(remaining, held)
            if close > 0:
                # The fee on the shares that close the opposite position is a closing cost.
                realized += self.reduce_position(
                    opposite, close, price, as_of=trade_date,
                    exit_fee_per_share=fee_per_share,
                )
                closed_qty = close
                remaining -= close

        if remaining > 1e-9:  # remainder opens/adds the resulting side; its fee is cost basis
            landed = self.record_trade(
                portfolio_id, ticker, open_side, open_short, remaining, price, trade_date,
                sector=sector, thesis=thesis, target=target, fee_per_share=fee_per_share,
            )

        # Blotter row for the whole ticket: the book it touched is the side the remainder
        # opened, or (for a pure close) the opposite side that was reduced.
        if remaining > 1e-9:
            book_short = open_short
            effect = "Cover & open long" if closed_qty > 0 and open_side is Side.LONG else (
                "Reduce & open short" if closed_qty > 0 else
                ("Open/add long" if open_side is Side.LONG else "Open/add short")
            )
        else:  # the ticket only closed an existing position
            book_short = ShortType.DISCRETIONARY if action is TradeAction.BUY else ShortType.NA
            effect = "Cover short" if action is TradeAction.BUY else "Reduce long"
        self._record_tx(
            portfolio_id, ticker, action, quantity, price, trade_date,
            short_type=book_short, origin=TradeOrigin.MANUAL, realized_pnl=realized,
            effect=effect, fee_per_share=fee_per_share,
        )
        return landed

    def get_position(self, position_id: int) -> Position | None:
        return self.repo.get_position(position_id)

    def list_positions(self, portfolio_id: str) -> list[Position]:
        return self.repo.list_positions(portfolio_id)

    def reduce_position(
        self,
        position_id: int,
        quantity: float,
        exit_price: float,
        as_of: date | None = None,
        specific_index: int | None = None,
        exit_fee_per_share: float = 0.0,
    ) -> float:
        """Close ``quantity`` shares of a position by its cost-basis method, persisting
        the remaining lots and the new accumulated realised P&L. Returns the realised
        P&L of this reduction. ``exit_fee_per_share`` is the closing trade's own fee."""
        position = self.repo.get_position(position_id)
        if position is None:
            raise ValueError(f"position {position_id} not found")
        lots = [
            Lot(l.quantity, l.entry_price, l.entry_date, l.fee_per_share)
            for l in position.lots
        ]
        method = position.cost_basis_method or CostBasisMethod.FIFO
        total_qty = sum(l.quantity for l in position.lots)
        remaining, realised = reduce_position(
            lots, quantity, exit_price, position.direction, method, specific_index,
            exit_fee_per_share=exit_fee_per_share,
        )
        new_lots = [
            LotEntry(quantity=l.quantity, entry_price=l.entry_price, entry_date=l.entry_date,
                     fee_per_share=l.fee_per_share)
            for l in remaining
        ]
        # Scale notional down by the fraction of quantity closed, so a full close → 0
        # exposure. Notional-only positions (no lots) are left untouched.
        new_notional = None
        if total_qty > 0:
            remaining_qty = sum(l.quantity for l in remaining)
            new_notional = position.notional * (remaining_qty / total_qty)
        self.repo.replace_lots(
            position_id, new_lots, position.realised_pnl + realised, notional=new_notional
        )
        return realised

    def delete_position(self, position_id: int) -> bool:
        return self.repo.delete_position(position_id)

    @staticmethod
    def _check_long_short_conflict(new: Position, existing: list[Position]) -> None:
        """Invariant I-05: a ticker cannot be both long and short in the same book."""
        for p in existing:
            if p.ticker != new.ticker or p.notional == 0:
                continue  # a flat (fully-closed) position doesn't block the opposite side
            if {p.side, new.side} == {Side.LONG, Side.SHORT}:
                raise ConflictError(
                    f"{new.ticker} is already held {p.side.value}; cannot also be "
                    f"{new.side.value} in the same portfolio (invariant I-05)"
                )
