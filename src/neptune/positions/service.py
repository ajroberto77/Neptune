"""Position Manager service.

Wraps the repository with the invariants that belong at the book level — notably I-05:
Neptune never shorts a name held long in a live portfolio (here enforced within the
portfolio; the cross-portfolio runtime join is deferred with Book-of-Books).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from neptune.db.repository import OrgRepository, PositionRepository
from neptune.domain.models import LotEntry, Portfolio, Position, Side, ShortType
from neptune.domain.org import PersonRole
from neptune.pnl import CostBasisMethod, Lot, reduce_position


class ConflictError(ValueError):
    """Raised when an action would violate a position-book invariant."""


class PositionService:
    def __init__(self, session: Session):
        self.repo = PositionRepository(session)
        self.org = OrgRepository(session)

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

    def add_position(self, portfolio_id: str, position: Position) -> int:
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
    ) -> int:
        """Record an executed trade as a lot on the (ticker, side, short_type) position,
        aggregating into the open position for that name+book if one exists, else opening a
        new one. ``notional`` grows by the executed value (quantity × price), so it tracks
        book exposure for trade-tab positions. Returns the position id.

        This is the single entry path for ALL executions, including systematic-short hedges
        once approved: recording an execution is not auto-execution (no broker routing), and
        the book tag (``short_type``) keeps systematic and discretionary shorts distinct
        (I-03)."""
        lot = LotEntry(quantity=quantity, entry_price=price, entry_date=trade_date)
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
    ) -> float:
        """Close ``quantity`` shares of a position by its cost-basis method, persisting
        the remaining lots and the new accumulated realised P&L. Returns the realised
        P&L of this reduction."""
        position = self.repo.get_position(position_id)
        if position is None:
            raise ValueError(f"position {position_id} not found")
        lots = [Lot(l.quantity, l.entry_price, l.entry_date) for l in position.lots]
        method = position.cost_basis_method or CostBasisMethod.FIFO
        total_qty = sum(l.quantity for l in position.lots)
        remaining, realised = reduce_position(
            lots, quantity, exit_price, position.direction, method, specific_index
        )
        new_lots = [
            LotEntry(quantity=l.quantity, entry_price=l.entry_price, entry_date=l.entry_date)
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
            if p.ticker != new.ticker:
                continue
            if {p.side, new.side} == {Side.LONG, Side.SHORT}:
                raise ConflictError(
                    f"{new.ticker} is already held {p.side.value}; cannot also be "
                    f"{new.side.value} in the same portfolio (invariant I-05)"
                )
