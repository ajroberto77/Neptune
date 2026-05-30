"""Position Manager service.

Wraps the repository with the invariants that belong at the book level — notably I-05:
Neptune never shorts a name held long in a live portfolio (here enforced within the
portfolio; the cross-portfolio runtime join is deferred with Book-of-Books).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from neptune.db.repository import PositionRepository
from neptune.domain.models import LotEntry, Portfolio, Position, Side, ShortType
from neptune.pnl import CostBasisMethod, Lot, reduce_position


class ConflictError(ValueError):
    """Raised when an action would violate a position-book invariant."""


class PositionService:
    def __init__(self, session: Session):
        self.repo = PositionRepository(session)

    def create_portfolio(self, portfolio_id: str, name: str, **kwargs) -> Portfolio:
        return self.repo.create_portfolio(portfolio_id, name, **kwargs)

    def get_portfolio(self, portfolio_id: str) -> Portfolio | None:
        return self.repo.get_portfolio(portfolio_id)

    def add_position(self, portfolio_id: str, position: Position) -> int:
        existing = self.repo.list_positions(portfolio_id)
        self._check_long_short_conflict(position, existing)
        return self.repo.add_position(portfolio_id, position)

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
        remaining, realised = reduce_position(
            lots, quantity, exit_price, position.direction, method, specific_index
        )
        new_lots = [
            LotEntry(quantity=l.quantity, entry_price=l.entry_price, entry_date=l.entry_date)
            for l in remaining
        ]
        self.repo.replace_lots(position_id, new_lots, position.realised_pnl + realised)
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
