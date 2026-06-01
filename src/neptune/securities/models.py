"""ORM models for the securities (market-data) database.

The schema is dialect-agnostic so SQLite (tests) and Postgres (production) behave
identically; on Postgres the ``prices`` table is additionally converted to a TimescaleDB
hypertable by a guarded migration step (plain ``create_all`` already yields a working
relational table everywhere).

**The link to the universe.** ``Security.instrument_id`` is the *same surrogate id* as
``cato_securities.instruments.instrument_id`` — the stable bigint that survives ticker
renames / M&A / re-listings. We deliberately do NOT key on ticker (UNIQUE upstream but
mutable). Postgres cannot enforce a cross-*database* foreign key, so ``securities`` is an
app-synced projection of the universe (``neptune.universe`` reads it read-only and copies
the identity slice in); every market-data fact below FKs to ``securities.instrument_id``,
so all joins stay inside this one database.

**Append-only (invariant I-07).** Facts carry a ``source`` tag and are upserted, never
silently overwritten; multiple sources for the same ``(security, date)`` coexist.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from neptune.db.base import SecuritiesBase

# Surrogate autoincrement PK type: BIGINT on Postgres (room for large fact tables),
# but plain INTEGER on SQLite — SQLite only auto-increments a column declared exactly
# ``INTEGER PRIMARY KEY``, so a bare BIGINT PK would fail to populate in tests.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class CorporateActionType(str, Enum):
    """The corporate actions that make raw historical prices need adjustment."""

    SPLIT = "SPLIT"                  # forward/reverse share split — adjusts price & volume
    SYMBOL_CHANGE = "SYMBOL_CHANGE"  # ticker change (instrument_id is unchanged upstream)
    DELISTING = "DELISTING"          # security stops trading
    SPINOFF = "SPINOFF"              # distribution of a new security


class Security(SecuritiesBase):
    """Projection of one ``cato_securities.instruments`` row — the local join anchor.

    A synced *cache* of upstream identity, NOT a second source of truth. ``instrument_id``
    is the universe surrogate id (set explicitly from the universe; never autoincremented).
    """

    __tablename__ = "securities"

    # The universe surrogate id — primary key here, mirrored from upstream (not generated).
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    ticker: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    security_name: Mapped[str | None] = mapped_column(String, nullable=True)
    security_type: Mapped[str | None] = mapped_column(String, nullable=True)  # 'Common Stock'…
    # Identifiers carried for cross-reference (all nullable; upstream may not have enriched).
    cusip: Mapped[str | None] = mapped_column(String, nullable=True)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    composite_figi: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_exch_code: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False once delisted
    # Provenance of the projection sync.
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    # Refreshed on every projection re-sync (INSERT or UPDATE), so it always reflects the
    # *last* sync — drives staleness checks against the upstream universe.
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["Price"]] = relationship(back_populates="security")
    dividends: Mapped[list["Dividend"]] = relationship(back_populates="security")
    corporate_actions: Mapped[list["CorporateAction"]] = relationship(
        back_populates="security"
    )


class Price(SecuritiesBase):
    """One daily OHLCV bar from one source. The historical-backfill target.

    Raw ``close`` is what the provider reported; ``adj_close`` is split/dividend-adjusted
    (yfinance supplies it directly; otherwise it is reconstructed from ``corporate_actions``
    + ``dividends``). Keeping both means adjustments are reproducible and never destructive.
    On Postgres this becomes a TimescaleDB hypertable partitioned on ``ts``.
    """

    __tablename__ = "prices"
    # Append-only with a source tag: the same (security, day) may exist from >1 source.
    __table_args__ = (
        UniqueConstraint("instrument_id", "ts", "source", name="uq_price_sec_ts_source"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("securities.instrument_id"), nullable=False, index=True
    )
    ts: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="yfinance")

    security: Mapped[Security] = relationship(back_populates="prices")


class Dividend(SecuritiesBase):
    """A cash (or stock) distribution. Drives total-return adjustment of historical prices."""

    __tablename__ = "dividends"
    __table_args__ = (
        UniqueConstraint("instrument_id", "ex_date", "source", name="uq_div_sec_ex_source"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("securities.instrument_id"), nullable=False, index=True
    )
    ex_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)  # per-share, in currency
    currency: Mapped[str] = mapped_column(String, default="USD")
    source: Mapped[str] = mapped_column(String, nullable=False, default="yfinance")

    security: Mapped[Security] = relationship(back_populates="dividends")


class CorporateAction(SecuritiesBase):
    """A split / symbol change / delisting / spinoff — what makes raw prices need adjusting.

    For a split, ``ratio`` is the share multiplier (2.0 for a 2:1 forward split, 0.1 for a
    1:10 reverse split): adjusted_price = raw_price / cumulative_ratio_after(effective_date),
    adjusted_volume = raw_volume * cumulative_ratio. ``old_value``/``new_value`` carry the
    before/after for symbol changes and similar.
    """

    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "effective_date", "action_type", "source",
            name="uq_ca_sec_date_type_source",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("securities.instrument_id"), nullable=False, index=True
    )
    action_type: Mapped[CorporateActionType] = mapped_column(
        SAEnum(CorporateActionType), nullable=False
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ratio: Mapped[float | None] = mapped_column(Float, nullable=True)  # split multiplier
    old_value: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. old ticker
    new_value: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. new ticker
    source: Mapped[str] = mapped_column(String, nullable=False, default="yfinance")

    security: Mapped[Security] = relationship(back_populates="corporate_actions")


class TradingDay(SecuritiesBase):
    """An exchange trading session. Used for gap detection and return alignment."""

    __tablename__ = "trading_calendar"
    __table_args__ = (
        UniqueConstraint("exchange", "session_date", name="uq_calendar_exch_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)


class FactorReturn(SecuritiesBase):
    """One daily factor return (the Ken French SMB/HML/MOM panel, plus Mkt-RF/RF).

    Not per-security — the factor panel is market-wide — so there's no FK. ``ret`` is a
    **decimal** return (0.0012, not the percent the Ken French files ship); the ingest
    layer divides by 100. Append-only with a source tag (I-07): the same ``(factor, day)``
    from multiple vintages/sources coexists, keyed by the unique constraint below.
    """

    __tablename__ = "factor_returns"
    __table_args__ = (
        UniqueConstraint("factor", "ts", "source", name="uq_factor_ts_source"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    factor: Mapped[str] = mapped_column(String, nullable=False, index=True)  # SMB/HML/MOM/…
    ts: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ret: Mapped[float] = mapped_column(Float, nullable=False)  # decimal daily return
    source: Mapped[str] = mapped_column(String, nullable=False, default="ken_french")
