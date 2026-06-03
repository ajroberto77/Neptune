"""ORM models for the macro-data database (``docs/macro_data.md``).

Two storage classes, split on the one property that dictates how the data must be stored —
**is the series revised after first publication?**

* ``MARKET`` — continuously priced, **not revised** (UST curve, SOFR, credit OAS,
  breakevens, FX, vol, commodities). One value per date → ``MacroObservation`` (flat).
* ``ECON`` — released on a schedule and **revised** (GDP, CPI, unemployment, payrolls,
  ISM). Point-in-time / vintage storage is mandatory → ``MacroVintage`` (ALFRED-style).

The registry (``MacroSeries``) is separate from the numbers (mirrors ``FactorDefinition``
vs ``FactorReturn`` in the securities DB) and carries the analytical **type flags** so the
risk layer can pick the right transform and refuse invalid ones (no YoY-% of a rate; no
regression/z-score on a non-stationary level; no double-differencing an already-changed
series). Fact tables store the **rawest canonical** value; transforms happen on read in the
risk layer (``neptune.risk.macro_transforms``), never here and never in the engine.

Append-only with a ``source`` tag, exactly like the securities facts: a value once recorded
as known on a date is immutable. A revision is a NEW vintage row (same ``reference_date``,
new ``vintage_date``); a benchmark restatement is many new rows sharing one ``vintage_date``.
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
from sqlalchemy.orm import Mapped, mapped_column

from neptune.db.base import MacroBase

# Same idiom as securities/models.py: BIGINT PK on Postgres, INTEGER on SQLite (which only
# auto-increments a column declared exactly ``INTEGER PRIMARY KEY``).
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class SeriesClass(str, Enum):
    """The storage axis: is this series revised after first print?"""

    MARKET = "MARKET"   # daily, NOT revised  -> MacroObservation (flat)
    ECON = "ECON"       # released & revised  -> MacroVintage (point-in-time)


class ObservationType(str, Enum):
    """Stock (level at a point) vs flow (over a period) — governs aggregation."""

    STOCK = "STOCK"
    FLOW = "FLOW"
    NA = "NA"           # neither (e.g. a diffusion index)


class ValueType(str, Enum):
    """The statistical nature of the number → which transforms are valid.

    ``PRICE`` is a market price level (commodities / FX) — distinct from an interest
    ``RATE`` or a ``$``-aggregate ``LEVEL`` — and like ``INDEX``/``LEVEL`` it trends, so its
    canonical transform is ``LOG_DIFF``/``PCT_CHANGE``. ``RATE_OF_CHANGE`` is a series that
    arrives ALREADY differenced (e.g. a CPI-YoY series): it must not be transformed again.
    """

    LEVEL = "LEVEL"                   # $-aggregate / count level (GDP, payroll level)
    PRICE = "PRICE"                   # market price level (commodities, FX)
    INDEX = "INDEX"                   # index level (CPI index, VIX)
    RATIO = "RATIO"
    RATE = "RATE"                     # already a percentage (unemployment, policy rate)
    RATE_OF_CHANGE = "RATE_OF_CHANGE"  # already a change/growth — do NOT re-transform
    DIFFUSION = "DIFFUSION"           # 50 = neutral (ISM/PMI)
    SPREAD = "SPREAD"                 # bp spread (credit OAS, curve slope)
    YIELD = "YIELD"                   # interest yield (UST curve)
    COUNT = "COUNT"                   # a count (jobless claims)
    CURRENCY = "CURRENCY"             # a currency amount


class Stationarity(str, Enum):
    """Is the RAW series regression/z-score-ready, or must it be transformed first?"""

    STATIONARY = "STATIONARY"
    NONSTATIONARY = "NONSTATIONARY"
    UNKNOWN = "UNKNOWN"


class TransformOp(str, Enum):
    """The change *operator* — constrained by ``ValueType`` (see macro_transforms)."""

    NONE = "NONE"
    DIFF = "DIFF"               # absolute change (units preserved; pp for a rate)
    LOG_DIFF = "LOG_DIFF"       # log change (≈ continuously-compounded return)
    PCT_CHANGE = "PCT_CHANGE"   # relative change (%)
    ANNUALIZE = "ANNUALIZE"     # annualize a period growth (composed by the risk layer)
    ZSCORE = "ZSCORE"           # standardize (stationary series only)


class TransformHorizon(str, Enum):
    """The *span* the operator covers. 'YoY' is a horizon, not an operation."""

    NONE = "NONE"
    POP = "POP"   # period-over-period (MoM / QoQ / daily)
    YOY = "YOY"   # year-over-year


class SeasonalAdjustment(str, Enum):
    SA = "SA"
    NSA = "NSA"
    NA = "NA"


class Frequency(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


class MacroSeries(MacroBase):
    """Registry: one row per macro indicator. Metadata + analytical type flags only — the
    numbers live in ``MacroObservation`` (MARKET) or ``MacroVintage`` (ECON)."""

    __tablename__ = "macro_series"

    series_id: Mapped[str] = mapped_column(String, primary_key=True)  # mnemonic, e.g. 'UST_10Y'
    series_class: Mapped[SeriesClass] = mapped_column(
        SAEnum(SeriesClass), nullable=False, index=True
    )
    # Topical grouping WITHIN a class (the PM's rates/credit-vs-economic view). Open-ended
    # string (the catalog documents the values: RATES|CREDIT|INFLATION|MONETARY|GROWTH|
    # LABOR|ACTIVITY|HOUSING|SENTIMENT|FX|COMMODITY|VOLATILITY|FIN_CONDITIONS|...).
    category: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    frequency: Mapped[Frequency] = mapped_column(SAEnum(Frequency), nullable=False)
    units: Mapped[str] = mapped_column(String, nullable=False)  # 'percent'|'bp'|'index'|'USD/bbl'…
    observation_type: Mapped[ObservationType] = mapped_column(
        SAEnum(ObservationType), nullable=False, default=ObservationType.NA
    )
    value_type: Mapped[ValueType] = mapped_column(SAEnum(ValueType), nullable=False)
    stationarity: Mapped[Stationarity] = mapped_column(
        SAEnum(Stationarity), nullable=False, default=Stationarity.UNKNOWN
    )
    # Canonical analysis-ready transform (raw stays canonical in the fact tables; the risk
    # layer applies this on read). Operator + horizon, because 'YoY' is a horizon not an op.
    transform_op: Mapped[TransformOp] = mapped_column(
        SAEnum(TransformOp), nullable=False, default=TransformOp.NONE
    )
    transform_horizon: Mapped[TransformHorizon] = mapped_column(
        SAEnum(TransformHorizon), nullable=False, default=TransformHorizon.NONE
    )
    seasonal_adjustment: Mapped[SeasonalAdjustment] = mapped_column(
        SAEnum(SeasonalAdjustment), nullable=False, default=SeasonalAdjustment.NA
    )
    source: Mapped[str] = mapped_column(String, nullable=False)           # primary provider
    source_code: Mapped[str | None] = mapped_column(String, nullable=True)  # provider's own code
    country: Mapped[str] = mapped_column(String, nullable=False, default="US")  # global hook
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_vintaged(self) -> bool:
        """Whether this series needs point-in-time storage — derived from the class, so it
        can never disagree with where the values actually live."""
        return self.series_class == SeriesClass.ECON


class MacroObservation(MacroBase):
    """MARKET-class values: daily, NOT revised. Keyed entity+date+source, append-only/upsert
    (same shape as securities ``Price`` / ``FactorReturn``). No vintage dimension."""

    __tablename__ = "macro_observations"
    __table_args__ = (
        UniqueConstraint(
            "series_id", "obs_date", "source", name="uq_macroobs_series_date_source"
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(
        ForeignKey("macro_series.series_id"), nullable=False, index=True
    )
    obs_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # completed close only
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="FRED")


class MacroVintage(MacroBase):
    """ECON-class values: released & REVISED → point-in-time (ALFRED-style).

    The 4-column natural key is the whole reason ECON gets its own table.
      ``reference_date`` = the period the number describes (period start, e.g. 2009-03-01)
      ``vintage_date``   = the date that value became knowable (first print or a revision)

    As-of read (look-ahead-safe): ``vintage_date <= asof`` then max(vintage_date) per
    reference_date. Latest = max(vintage_date) per ref; first print = min(vintage_date).
    """

    __tablename__ = "macro_vintages"
    __table_args__ = (
        UniqueConstraint(
            "series_id", "reference_date", "vintage_date", "source",
            name="uq_macrovint_series_ref_vint_source",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(
        ForeignKey("macro_series.series_id"), nullable=False, index=True
    )
    reference_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    vintage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="ALFRED")


class MacroReleaseCalendar(MacroBase):
    """Scheduled release datetimes for ECON series, so the risk layer can flag 'NFP prints in
    2h' and align as-of reads to release timing. Pure metadata; not required to read values."""

    __tablename__ = "macro_release_calendar"
    __table_args__ = (
        UniqueConstraint("series_id", "reference_date", name="uq_macrorel_series_ref"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(
        ForeignKey("macro_series.series_id"), nullable=False, index=True
    )
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_release_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
