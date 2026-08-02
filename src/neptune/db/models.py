"""ORM models for the portfolio database. The schema stays dialect-agnostic so SQLite
(tests) and Postgres (production) behave identically.

Ownership / multi-tenancy (see ``domain/org.py`` and ``docs/data_architecture.md``):

    management_firm        ← TENANT / isolation root (the investment-management firm)
      ├── person           ← PM / ANALYST / CIO / ADMIN — FIRM staff (not the client)
      └── investor_entity  ← a client account the firm manages
            └── portfolio  (= book) ← belongs to an investor_entity; has lead PM(s)
                  └── position      ← optional per-name pm_id / analyst_id
                        └── lot

NOTE: append-only history (invariant I-07) is NOT yet enforced in the slice —
``delete_position`` hard-deletes; the append-only audit trail (`corrected_by` rows +
`audit_log`) is a deferred phase."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from neptune.db.base import Base
from neptune.domain.org import PersonRole
from neptune.pnl import CostBasisMethod
from neptune.settings_store import ConnectionRole
from neptune.domain.models import Instrument, Side, ShortType, TradeAction, TradeOrigin


class ManagementFirmORM(Base):
    """The investment-management firm — tenant / isolation root."""

    __tablename__ = "management_firms"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    subscription_tier: Mapped[str | None] = mapped_column(String, nullable=True)

    people: Mapped[list["PersonORM"]] = relationship(
        back_populates="firm", cascade="all, delete-orphan"
    )
    investor_entities: Mapped[list["InvestorEntityORM"]] = relationship(
        back_populates="firm", cascade="all, delete-orphan"
    )


class PersonORM(Base):
    """A PM / analyst / CIO / admin employed by a firm. Firm staff — never a client."""

    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    firm_id: Mapped[str] = mapped_column(ForeignKey("management_firms.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[PersonRole] = mapped_column(SAEnum(PersonRole), nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    firm: Mapped[ManagementFirmORM] = relationship(back_populates="people")


class InvestorEntityORM(Base):
    """A client/account whose capital the firm manages. Books belong to one of these."""

    __tablename__ = "investor_entities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    firm_id: Mapped[str] = mapped_column(ForeignKey("management_firms.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_currency: Mapped[str] = mapped_column(String, default="USD")

    firm: Mapped[ManagementFirmORM] = relationship(back_populates="investor_entities")
    portfolios: Mapped[list["PortfolioORM"]] = relationship(back_populates="investor_entity")


class PortfolioORM(Base):
    """A book of positions. Belongs to an investor entity (the client); managed by the
    firm's lead PM(s) via ``BookManagerORM``. ``firm_id``/``investor_entity_id`` are
    nullable so the synthetic golden slice (which seeds a bare book) still works."""

    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_currency: Mapped[str] = mapped_column(String, default="USD")
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False)
    # Shorting mandate: "LONG_SHORT" (hedged, beta-constrained) or "LONG_ONLY" (no shorting).
    # Stored as a plain string (not a native enum) so the column-add migration stays
    # dialect-agnostic; the domain layer converts it to/from the Mandate enum.
    mandate: Mapped[str] = mapped_column(String, default="LONG_SHORT", nullable=False)
    firm_id: Mapped[str | None] = mapped_column(
        ForeignKey("management_firms.id"), nullable=True
    )
    investor_entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("investor_entities.id"), nullable=True
    )

    investor_entity: Mapped["InvestorEntityORM | None"] = relationship(
        back_populates="portfolios"
    )
    managers: Mapped[list["BookManagerORM"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan",
        order_by="BookManagerORM.id",  # stable order so "first lead PM" is deterministic
    )
    positions: Mapped[list["PositionORM"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class BookManagerORM(Base):
    """A book↔person management assignment. ``is_lead`` marks lead PM(s); co-PMs are >1
    lead row for the same book."""

    __tablename__ = "book_managers"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "person_id", name="uq_book_manager"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), nullable=False)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=True)

    portfolio: Mapped[PortfolioORM] = relationship(back_populates="managers")
    person: Mapped[PersonORM] = relationship()


class PositionORM(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[Side] = mapped_column(SAEnum(Side), nullable=False)
    notional: Mapped[float] = mapped_column(Float, nullable=False)
    short_type: Mapped[ShortType] = mapped_column(SAEnum(ShortType), default=ShortType.NA)
    # native_enum=False (unlike side/short_type above, native Postgres enums): a deliberate
    # deviation from this table's other enum columns, matching TransactionORM's convention
    # instead — adding a future value (e.g. a third instrument kind) never needs a Postgres
    # ALTER TYPE migration. See docs/database_interactions.md.
    instrument: Mapped[Instrument] = mapped_column(
        SAEnum(Instrument, native_enum=False), nullable=False, default=Instrument.CASH
    )
    forward_beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    # Per-name coverage — firm staff assigned to this name (optional; PM falls back to the
    # book's lead PM). These reference people.id; they carry no investment thesis content.
    pm_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    analyst_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    # P&L: cost-basis method and inception-to-date realised P&L accumulated from closes.
    cost_basis_method: Mapped[CostBasisMethod] = mapped_column(
        SAEnum(CostBasisMethod), default=CostBasisMethod.FIFO
    )
    realised_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    # Fundamental Layer — read-only to the system; persisted but never auto-generated.
    thesis: Mapped[str | None] = mapped_column(String, nullable=True)
    target: Mapped[str | None] = mapped_column(String, nullable=True)

    portfolio: Mapped[PortfolioORM] = relationship(back_populates="positions")
    lots: Mapped[list["LotORM"]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="LotORM.entry_date"
    )


class LotORM(Base):
    """An open lot for a position. Ordered by entry_date so FIFO matching is stable."""

    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)  # clean execution price
    fee_per_share: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)

    position: Mapped[PositionORM] = relationship(back_populates="lots")


class TransactionORM(Base):
    """An append-only record of an executed trade (the blotter). One row per booked ticket —
    a desk Buy/Sell or a hedge-approval cover/short. Recording an execution is NOT
    auto-execution (no broker routing, CLAUDE.md §2); this is the audit trail of what was
    booked. ``realized_pnl`` captures the P&L locked in by the closing portion of the ticket
    (0 for a pure open). Enum columns are stored as VARCHAR (``native_enum=False``) so adding
    a value later never needs a Postgres ALTER TYPE migration."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[TradeAction] = mapped_column(SAEnum(TradeAction, native_enum=False), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)  # execution price per share
    fee_per_share: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Tie-breaks ordering within a trade_date (e.g. covers before the new shorts in one approve).
    executed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    # Which short book the trade affected (SYSTEMATIC for hedge trades; NA for longs).
    short_type: Mapped[ShortType] = mapped_column(
        SAEnum(ShortType, native_enum=False), nullable=False, default=ShortType.NA
    )
    origin: Mapped[TradeOrigin] = mapped_column(
        SAEnum(TradeOrigin, native_enum=False), nullable=False, default=TradeOrigin.MANUAL
    )
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Human-readable effect, e.g. "Open long", "Cover short", "Sell short", "Cover systematic".
    effect: Mapped[str] = mapped_column(String, nullable=False, default="")


class DbConnectionORM(Base):
    """A configurable database connection target (see ``settings_store``).

    One row per role (PORTFOLIO / SECURITIES / UNIVERSE). Lives in the portfolio DB. The
    ``password`` is stored but NEVER returned by the API — reads go through the masked
    serializer. The PORTFOLIO row is informational (the app DB is the env-driven
    bootstrap); SECURITIES/UNIVERSE rows are applied live."""

    __tablename__ = "db_connections"

    role: Mapped[ConnectionRole] = mapped_column(SAEnum(ConnectionRole), primary_key=True)
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=5432)
    database: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False)
    password: Mapped[str | None] = mapped_column(String, nullable=True)  # write-only
    sslmode: Mapped[str | None] = mapped_column(String, nullable=True)
    driver: Mapped[str] = mapped_column(String, default="postgresql+psycopg")


class AppSettingORM(Base):
    """A persisted runtime app setting (key→value), e.g. the price-refresh interval. Lives in
    the portfolio DB; values are stored as strings and parsed by the reading service."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class ProviderCredentialORM(Base):
    """A stored API key for an external data provider (e.g. FRED). One row per provider; lives
    in the portfolio DB. The ``api_key`` is **write-only** — NEVER returned by the API; reads
    expose only whether a key is set. Same secret discipline as ``DbConnectionORM.password``."""

    __tablename__ = "provider_credentials"

    provider: Mapped[str] = mapped_column(String, primary_key=True)  # 'FRED'
    api_key: Mapped[str | None] = mapped_column(String, nullable=True)  # write-only
