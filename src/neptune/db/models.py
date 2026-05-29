"""ORM models. The schema stays dialect-agnostic so SQLite (tests) and Postgres
(production) behave identically. NOTE: append-only history (invariant I-07) is NOT yet
enforced in the slice — ``delete_position`` hard-deletes; the append-only audit trail
(`corrected_by` rows + `audit_log`) is a deferred phase."""
from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from neptune.db.base import Base
from neptune.domain.models import Side, ShortType


class PortfolioORM(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_currency: Mapped[str] = mapped_column(String, default="USD")
    is_paper: Mapped[bool] = mapped_column(default=False)

    positions: Mapped[list["PositionORM"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PositionORM(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[Side] = mapped_column(SAEnum(Side), nullable=False)
    notional: Mapped[float] = mapped_column(Float, nullable=False)
    short_type: Mapped[ShortType] = mapped_column(SAEnum(ShortType), default=ShortType.NA)
    forward_beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    # Fundamental Layer — read-only to the system; persisted but never auto-generated.
    thesis: Mapped[str | None] = mapped_column(String, nullable=True)
    target: Mapped[str | None] = mapped_column(String, nullable=True)

    portfolio: Mapped[PortfolioORM] = relationship(back_populates="positions")
