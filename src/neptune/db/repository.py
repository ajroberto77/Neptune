"""Repository: translates between ORM rows and domain dataclasses."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from neptune.db.models import PortfolioORM, PositionORM
from neptune.domain.models import Portfolio, Position


def _to_domain_position(row: PositionORM) -> Position:
    return Position(
        ticker=row.ticker,
        side=row.side,
        notional=row.notional,
        short_type=row.short_type,
        forward_beta=row.forward_beta,
        sector=row.sector,
        thesis=row.thesis,
        target=row.target,
    )


def _to_domain_portfolio(row: PortfolioORM) -> Portfolio:
    return Portfolio(
        id=row.id,
        name=row.name,
        base_currency=row.base_currency,
        is_paper=row.is_paper,
        positions=[_to_domain_position(p) for p in row.positions],
    )


class PositionRepository:
    """CRUD over portfolios and positions."""

    def __init__(self, session: Session):
        self.session = session

    def create_portfolio(self, portfolio_id: str, name: str, **kwargs) -> Portfolio:
        row = PortfolioORM(id=portfolio_id, name=name, **kwargs)
        self.session.add(row)
        self.session.commit()
        return _to_domain_portfolio(row)

    def get_portfolio(self, portfolio_id: str) -> Portfolio | None:
        row = self.session.get(PortfolioORM, portfolio_id)
        return _to_domain_portfolio(row) if row else None

    def add_position(self, portfolio_id: str, position: Position) -> int:
        row = PositionORM(
            portfolio_id=portfolio_id,
            ticker=position.ticker,
            side=position.side,
            notional=position.notional,
            short_type=position.short_type,
            forward_beta=position.forward_beta,
            sector=position.sector,
            thesis=position.thesis,
            target=position.target,
        )
        self.session.add(row)
        self.session.commit()
        return row.id

    def list_positions(self, portfolio_id: str) -> list[Position]:
        rows = self.session.scalars(
            select(PositionORM).where(PositionORM.portfolio_id == portfolio_id)
        ).all()
        return [_to_domain_position(r) for r in rows]

    def delete_position(self, position_id: int) -> bool:
        row = self.session.get(PositionORM, position_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True
