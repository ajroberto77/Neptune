"""Repository: translates between ORM rows and domain dataclasses."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from neptune.db.models import (
    BookManagerORM,
    InvestorEntityORM,
    LotORM,
    ManagementFirmORM,
    PersonORM,
    PortfolioORM,
    PositionORM,
)
from neptune.domain.models import LotEntry, Portfolio, Position
from neptune.domain.org import InvestorEntity, ManagementFirm, Person, PersonRole
from neptune.pnl import CostBasisMethod


def _to_domain_position(row: PositionORM) -> Position:
    return Position(
        ticker=row.ticker,
        side=row.side,
        notional=row.notional,
        short_type=row.short_type,
        forward_beta=row.forward_beta,
        sector=row.sector,
        cost_basis_method=row.cost_basis_method,
        realised_pnl=row.realised_pnl,
        pm_id=row.pm_id,
        analyst_id=row.analyst_id,
        lots=[
            LotEntry(quantity=l.quantity, entry_price=l.entry_price, entry_date=l.entry_date)
            for l in row.lots
        ],
        thesis=row.thesis,
        target=row.target,
    )


def _to_domain_portfolio(row: PortfolioORM) -> Portfolio:
    return Portfolio(
        id=row.id,
        name=row.name,
        base_currency=row.base_currency,
        is_paper=row.is_paper,
        firm_id=row.firm_id,
        investor_entity_id=row.investor_entity_id,
        lead_pm_ids=[m.person_id for m in row.managers if m.is_lead],
        positions=[_to_domain_position(p) for p in row.positions],
    )


class PositionRepository:
    """CRUD over portfolios and positions."""

    def __init__(self, session: Session):
        self.session = session

    def create_portfolio(self, portfolio_id: str, name: str, **kwargs) -> Portfolio:
        # lead_pm_ids is a relationship convenience, not a column — pop it and create the
        # book_manager link rows after the portfolio row exists.
        lead_pm_ids = kwargs.pop("lead_pm_ids", None) or []
        row = PortfolioORM(id=portfolio_id, name=name, **kwargs)
        self.session.add(row)
        self.session.flush()
        for pid in lead_pm_ids:
            self.session.add(
                BookManagerORM(portfolio_id=portfolio_id, person_id=pid, is_lead=True)
            )
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
            pm_id=position.pm_id,
            analyst_id=position.analyst_id,
            cost_basis_method=position.cost_basis_method or CostBasisMethod.FIFO,
            realised_pnl=position.realised_pnl,
            thesis=position.thesis,
            target=position.target,
        )
        row.lots = [
            LotORM(quantity=l.quantity, entry_price=l.entry_price, entry_date=l.entry_date)
            for l in position.lots
        ]
        self.session.add(row)
        self.session.commit()
        return row.id

    def get_position(self, position_id: int) -> Position | None:
        row = self.session.get(PositionORM, position_id)
        return _to_domain_position(row) if row else None

    def list_positions(self, portfolio_id: str) -> list[Position]:
        rows = self.session.scalars(
            select(PositionORM).where(PositionORM.portfolio_id == portfolio_id)
        ).all()
        return [_to_domain_position(r) for r in rows]

    def replace_lots(
        self, position_id: int, lots: list[LotEntry], realised_pnl: float
    ) -> None:
        """Overwrite a position's open lots and its accumulated realised P&L. Used after
        a reduction recomputes both."""
        row = self.session.get(PositionORM, position_id)
        if row is None:
            raise ValueError(f"position {position_id} not found")
        row.lots = [
            LotORM(quantity=l.quantity, entry_price=l.entry_price, entry_date=l.entry_date)
            for l in lots
        ]
        row.realised_pnl = realised_pnl
        self.session.commit()

    def delete_position(self, position_id: int) -> bool:
        row = self.session.get(PositionORM, position_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True


class OrgRepository:
    """CRUD over the ownership graph: firms, people, investor entities, book managers."""

    def __init__(self, session: Session):
        self.session = session

    def create_firm(
        self, firm_id: str, name: str, is_internal: bool = False,
        subscription_tier: str | None = None,
    ) -> ManagementFirm:
        row = ManagementFirmORM(
            id=firm_id, name=name, is_internal=is_internal,
            subscription_tier=subscription_tier,
        )
        self.session.add(row)
        self.session.commit()
        return ManagementFirm(row.id, row.name, row.is_internal, row.subscription_tier)

    def create_person(
        self, person_id: str, firm_id: str, name: str, role: PersonRole,
        email: str | None = None,
    ) -> Person:
        row = PersonORM(id=person_id, firm_id=firm_id, name=name, role=role, email=email)
        self.session.add(row)
        self.session.commit()
        return Person(row.id, row.firm_id, row.name, row.role, row.email, row.is_active)

    def create_investor_entity(
        self, entity_id: str, firm_id: str, name: str, base_currency: str = "USD",
    ) -> InvestorEntity:
        row = InvestorEntityORM(
            id=entity_id, firm_id=firm_id, name=name, base_currency=base_currency
        )
        self.session.add(row)
        self.session.commit()
        return InvestorEntity(row.id, row.firm_id, row.name, row.base_currency)

    def set_book_managers(
        self, portfolio_id: str, person_ids: list[str], is_lead: bool = True
    ) -> None:
        """Add management links for a book (idempotent on the (book, person) unique key)."""
        existing = {
            m.person_id
            for m in self.session.scalars(
                select(BookManagerORM).where(BookManagerORM.portfolio_id == portfolio_id)
            ).all()
        }
        for pid in person_ids:
            if pid not in existing:
                self.session.add(
                    BookManagerORM(portfolio_id=portfolio_id, person_id=pid, is_lead=is_lead)
                )
        self.session.commit()
