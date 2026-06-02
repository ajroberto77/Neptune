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

from sqlalchemy import text
from sqlalchemy.engine import Engine

from neptune.universe import UniverseSecurity

# Selecting the identity slice we project. Joins are intentionally avoided (single table)
# so this stays cheap; name/exchange come straight off ``instruments``.
_SELECT_COLS = """
    instrument_id, ticker, security_name, security_type,
    cusip, isin, composite_figi, primary_exch_code
"""

# "Investable" predicate — see module docstring. Common stock with a ticker.
_INVESTABLE_PREDICATE = "ticker IS NOT NULL AND security_type = 'Common Stock'"


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
    )


class SqlUniverse:
    """Read-only universe adapter bound to a ``cato_securities`` engine."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def resolve_ticker(self, ticker: str) -> UniverseSecurity | None:
        sql = text(
            f"SELECT {_SELECT_COLS} FROM instruments WHERE ticker = :t LIMIT 1"
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
            f"SELECT {_SELECT_COLS} FROM instruments WHERE {_INVESTABLE_PREDICATE}"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [_row_to_security(r, is_investable=True) for r in rows]
