"""SQL-backed read-only universe source against a real ``cato_securities`` database.

Uses ``text()`` SELECTs only — no ORM is mapped against the universe, so there is no code
path that could write to it (it is upstream truth; Neptune never mutates it). The engine
should be created from a SELECT-only role.

Schema reference (``cato_securities_schema.md``): ``instruments`` is the security table;
``instrument_id`` (bigint PK) is the stable surrogate link key; ``ticker`` is UNIQUE but
mutable. There is no explicit ``is_investable`` column in the seeded schema, so we treat
common stock with a non-null ticker as investable; this predicate is the one knob to
revisit when the universe gains an explicit flag.
"""
from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from neptune.universe import ClassificationRecord, UniverseSecurity

# Selecting the identity slice we project. One LEFT JOIN to legal_entities for entity_cik
# (a single scalar per instrument, cheap); everything else stays a single-table SELECT off
# ``instruments`` as before.
_SELECT_COLS = """
    i.instrument_id, i.ticker, i.security_name, i.security_type,
    i.cusip, i.isin, i.composite_figi, i.primary_exch_code, le.entity_cik
"""
_FROM_INSTRUMENTS = "FROM instruments i LEFT JOIN legal_entities le ON le.entity_id = i.entity_id"

# "Investable" predicate — see module docstring. Common stock with a ticker.
_INVESTABLE_PREDICATE = "i.ticker IS NOT NULL AND i.security_type = 'Common Stock'"

# Only classification schemes Neptune actually surfaces as selectable sector sources
# (see settings_store's sector_source setting). CATO's own YAHOO fallback tier is skipped —
# Neptune already sources Yahoo sector data directly (securities/providers.py), so pulling
# CATO's copy of the same thing would just be a second, redundant path to the same value.
_CLASSIFICATION_SCHEMES = ("SIC", "KENFRENCH_12")


def _row_to_security(row, *, is_investable: bool) -> UniverseSecurity:
    m = row._mapping
    return UniverseSecurity(
        instrument_id=m["instrument_id"],
        ticker=m["ticker"],
        security_name=m["security_name"],
        security_type=m["security_type"],
        cusip=m["cusip"],
        isin=m["isin"],
        composite_figi=m["composite_figi"],
        primary_exch_code=m["primary_exch_code"],
        is_investable=is_investable,
        entity_cik=m["entity_cik"],
    )


class SqlUniverse:
    """Read-only universe adapter bound to a ``cato_securities`` engine."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def resolve_ticker(self, ticker: str) -> UniverseSecurity | None:
        sql = text(
            f"SELECT {_SELECT_COLS} {_FROM_INSTRUMENTS} WHERE i.ticker = :t LIMIT 1"
        )
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"t": ticker}).fetchone()
        if row is None:
            return None
        # Re-evaluate the investable predicate on the resolved row.
        investable = (
            row._mapping["ticker"] is not None
            and row._mapping["security_type"] == "Common Stock"
        )
        return _row_to_security(row, is_investable=investable)

    def investable_universe(self) -> list[UniverseSecurity]:
        sql = text(
            f"SELECT {_SELECT_COLS} {_FROM_INSTRUMENTS} WHERE {_INVESTABLE_PREDICATE}"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [_row_to_security(r, is_investable=True) for r in rows]

    def classifications(self, ciks: list[str]) -> list[ClassificationRecord]:
        if not ciks:
            return []
        sql = text(
            "SELECT le.entity_cik AS entity_cik, ec.scheme AS scheme, ec.level AS level, "
            "ec.code AS code, ec.description AS description "
            "FROM entity_classifications ec "
            "JOIN legal_entities le ON le.entity_id = ec.entity_id "
            "WHERE le.entity_cik IN :ciks AND ec.scheme IN :schemes"
        ).bindparams(bindparam("ciks", expanding=True), bindparam("schemes", expanding=True))
        with self.engine.connect() as conn:
            rows = conn.execute(
                sql, {"ciks": list(ciks), "schemes": list(_CLASSIFICATION_SCHEMES)}
            ).fetchall()
        return [
            ClassificationRecord(
                entity_cik=r._mapping["entity_cik"],
                scheme=r._mapping["scheme"],
                level=r._mapping["level"],
                code=r._mapping["code"],
                description=r._mapping["description"],
            )
            for r in rows
        ]
