"""Read/write access to the macro-data database (``docs/macro_data.md`` §4).

The contract: **append-only, never mutate.** A value once recorded as known on a date is
immutable. A *revision* to a prior period is a NEW vintage row (same ``reference_date``, new
``vintage_date``); a *benchmark restatement* is many new rows sharing one ``vintage_date``.
From that history every view is derived:

* ``latest``      — max(vintage_date) per reference_date.
* ``first_print`` — min(vintage_date) per reference_date (markets react to the first print).
* ``as_of(d)``    — vintage_date <= d, then latest vintage per reference_date: the panel
  exactly as it stood on date ``d`` (the look-ahead-safe read).

MARKET-class series are not revised: their values land flat in ``MacroObservation``, keyed
``(series, date, source)``, upserted.

Callers manage the transaction (commit/rollback). Writers ``flush`` so within-transaction
reads (e.g. the diff in ``ingest_latest``) see prior writes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from neptune.macro.models import (
    Frequency,
    MacroObservation,
    MacroSeries,
    MacroVintage,
    ObservationType,
    SeasonalAdjustment,
    SeriesClass,
    Stationarity,
    TransformHorizon,
    TransformOp,
    ValueType,
)

# --- Registry --------------------------------------------------------------------


def upsert_series(
    session: Session,
    series_id: str,
    *,
    series_class: SeriesClass,
    category: str,
    name: str,
    frequency: Frequency,
    units: str,
    value_type: ValueType,
    observation_type: ObservationType = ObservationType.NA,
    stationarity: Stationarity = Stationarity.UNKNOWN,
    transform_op: TransformOp = TransformOp.NONE,
    transform_horizon: TransformHorizon = TransformHorizon.NONE,
    seasonal_adjustment: SeasonalAdjustment = SeasonalAdjustment.NA,
    source: str,
    source_code: str | None = None,
    country: str = "US",
    currency: str | None = None,
    description: str = "",
) -> MacroSeries:
    """Register (or update) one indicator's metadata + analytical type flags. Idempotent."""
    obj = session.get(MacroSeries, series_id)
    fields = dict(
        series_class=series_class,
        category=category,
        name=name,
        frequency=frequency,
        units=units,
        value_type=value_type,
        observation_type=observation_type,
        stationarity=stationarity,
        transform_op=transform_op,
        transform_horizon=transform_horizon,
        seasonal_adjustment=seasonal_adjustment,
        source=source,
        source_code=source_code,
        country=country,
        currency=currency,
        description=description,
    )
    if obj is None:
        obj = MacroSeries(series_id=series_id, **fields)
        session.add(obj)
    else:
        for k, v in fields.items():
            setattr(obj, k, v)
    session.flush()
    return obj


def get_series(session: Session, series_id: str) -> MacroSeries | None:
    return session.get(MacroSeries, series_id)


def list_series(
    session: Session, *, series_class: SeriesClass | None = None, category: str | None = None
) -> list[MacroSeries]:
    stmt = select(MacroSeries)
    if series_class is not None:
        stmt = stmt.where(MacroSeries.series_class == series_class)
    if category is not None:
        stmt = stmt.where(MacroSeries.category == category)
    return list(session.execute(stmt.order_by(MacroSeries.series_id)).scalars())


def coverage(session: Session) -> dict[str, dict]:
    """Per-series ingestion coverage: ``{series_id: {"points": int, "last_date": date|None}}``.
    MARKET series count flat observations; ECON series count distinct reference_dates across
    their vintages (the number of periods known, not the number of revisions). One aggregate
    query per fact table — cheap regardless of catalog size."""
    out: dict[str, dict] = {}
    obs = session.execute(
        select(
            MacroObservation.series_id,
            func.count(MacroObservation.id),
            func.max(MacroObservation.obs_date),
        ).group_by(MacroObservation.series_id)
    ).all()
    for sid, n, last in obs:
        out[sid] = {"points": int(n), "last_date": last.isoformat() if last else None}
    vint = session.execute(
        select(
            MacroVintage.series_id,
            func.count(func.distinct(MacroVintage.reference_date)),
            func.max(MacroVintage.reference_date),
        ).group_by(MacroVintage.series_id)
    ).all()
    for sid, n, last in vint:
        out[sid] = {"points": int(n), "last_date": last.isoformat() if last else None}
    return out


# --- MARKET observations (flat, not revised) -------------------------------------


def record_observation(
    session: Session, series_id: str, obs_date: date, value: float, *, source: str = "FRED"
) -> MacroObservation:
    """Upsert a MARKET value on ``(series, date, source)``. A rare vendor correction updates
    in place; a different source for the same day coexists."""
    existing = session.execute(
        select(MacroObservation).where(
            MacroObservation.series_id == series_id,
            MacroObservation.obs_date == obs_date,
            MacroObservation.source == source,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = value
        session.flush()
        return existing
    obj = MacroObservation(series_id=series_id, obs_date=obs_date, value=value, source=source)
    session.add(obj)
    session.flush()
    return obj


def observations(
    session: Session,
    series_id: str,
    *,
    start: date | None = None,
    end: date | None = None,
    source: str | None = None,
) -> list[tuple[date, float]]:
    """MARKET series as ``(date, value)`` sorted ascending."""
    stmt = select(MacroObservation.obs_date, MacroObservation.value).where(
        MacroObservation.series_id == series_id
    )
    if start is not None:
        stmt = stmt.where(MacroObservation.obs_date >= start)
    if end is not None:
        stmt = stmt.where(MacroObservation.obs_date <= end)
    if source is not None:
        stmt = stmt.where(MacroObservation.source == source)
    stmt = stmt.order_by(MacroObservation.obs_date)
    return [(d, v) for d, v in session.execute(stmt).all()]


# --- ECON vintages (point-in-time) -----------------------------------------------


def record_vintage(
    session: Session,
    series_id: str,
    reference_date: date,
    vintage_date: date,
    value: float,
    *,
    source: str = "ALFRED",
) -> MacroVintage:
    """Append one ECON vintage row. Re-recording the SAME ``(series, ref, vintage, source)``
    is idempotent (updates the value — same release re-ingested); a DIFFERENT ``vintage_date``
    is a revision and lands as a new row, preserving the old one."""
    existing = session.execute(
        select(MacroVintage).where(
            MacroVintage.series_id == series_id,
            MacroVintage.reference_date == reference_date,
            MacroVintage.vintage_date == vintage_date,
            MacroVintage.source == source,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = value
        session.flush()
        return existing
    obj = MacroVintage(
        series_id=series_id,
        reference_date=reference_date,
        vintage_date=vintage_date,
        value=value,
        source=source,
    )
    session.add(obj)
    session.flush()
    return obj


def record_vintage_batch(
    session: Session,
    series_id: str,
    vintage_date: date,
    points: dict[date, float] | list[tuple[date, float]],
    *,
    source: str = "ALFRED",
) -> int:
    """A benchmark / annual restatement: many ``reference_date`` values published on one
    ``vintage_date``. Returns the number of rows written."""
    items = points.items() if isinstance(points, dict) else points
    n = 0
    for reference_date, value in items:
        record_vintage(session, series_id, reference_date, vintage_date, value, source=source)
        n += 1
    return n


def ingest_latest(
    session: Session,
    series_id: str,
    reference_date: date,
    value: float,
    asof: date,
    *,
    source: str = "FRED",
) -> bool:
    """Build vintages from a latest-only source (e.g. FRED current). Compare ``value`` against
    the stored latest for this ``reference_date``; only when it is NEW or CHANGED do we insert
    a vintage row dated ``asof`` (the sync date — errs *later* than true release, so it can
    never leak a value earlier than we could have known it). Returns True if a row was written.
    """
    current = session.execute(
        select(MacroVintage.value)
        .where(
            MacroVintage.series_id == series_id,
            MacroVintage.reference_date == reference_date,
        )
        .order_by(MacroVintage.vintage_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if current is not None and current == value:
        return False
    record_vintage(session, series_id, reference_date, asof, value, source=source)
    return True


def _reduce(rows: list[tuple[date, date, float]], *, keep: str) -> list[tuple[date, float]]:
    """Collapse (reference_date, vintage_date, value) rows — assumed ordered by
    (reference_date, vintage_date) ascending — to one value per reference_date.
    ``keep='last'`` → max vintage (latest); ``keep='first'`` → min vintage (first print)."""
    out: dict[date, float] = {}
    for ref, _vint, val in rows:
        if keep == "first" and ref in out:
            continue  # first vintage already kept
        out[ref] = val  # 'last': later vintage overwrites
    return sorted(out.items())


def _vintage_rows(
    session: Session, series_id: str, *, max_vintage: date | None, source: str | None
) -> list[tuple[date, date, float]]:
    stmt = select(
        MacroVintage.reference_date, MacroVintage.vintage_date, MacroVintage.value
    ).where(MacroVintage.series_id == series_id)
    if max_vintage is not None:
        stmt = stmt.where(MacroVintage.vintage_date <= max_vintage)
    if source is not None:
        stmt = stmt.where(MacroVintage.source == source)
    stmt = stmt.order_by(MacroVintage.reference_date, MacroVintage.vintage_date)
    return [(r, v, val) for r, v, val in session.execute(stmt).all()]


def latest(
    session: Session, series_id: str, *, source: str | None = None
) -> list[tuple[date, float]]:
    """Best-known value per reference_date as of today (max vintage)."""
    return _reduce(_vintage_rows(session, series_id, max_vintage=None, source=source), keep="last")


def first_print(
    session: Session, series_id: str, *, source: str | None = None
) -> list[tuple[date, float]]:
    """The first-published value per reference_date (min vintage)."""
    return _reduce(_vintage_rows(session, series_id, max_vintage=None, source=source), keep="first")


def as_of(
    session: Session, series_id: str, asof: date, *, source: str | None = None
) -> list[tuple[date, float]]:
    """The panel exactly as it stood on ``asof`` — only values whose ``vintage_date <= asof``,
    taking the latest such vintage per reference_date. This is the look-ahead-safe read."""
    return _reduce(_vintage_rows(session, series_id, max_vintage=asof, source=source), keep="last")
