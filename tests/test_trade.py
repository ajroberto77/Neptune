"""Trade workflow: record_trade aggregates lots into a position by name+book, maintains
notional, respects the long/short invariant, and closing scales notional down."""
from __future__ import annotations

from datetime import date

import pytest

from neptune.domain.models import Side, ShortType
from neptune.positions.service import ConflictError, PositionService


def _service(session) -> PositionService:
    svc = PositionService(session)
    svc.create_portfolio("BOOK", "Test Book")
    return svc


def test_record_trade_opens_a_position(session):
    svc = _service(session)
    pid = svc.record_trade("BOOK", "AAPL", Side.LONG, ShortType.NA, 100, 50.0, date(2026, 1, 2))
    pos = svc.get_position(pid)
    assert pos.ticker == "AAPL" and pos.side is Side.LONG
    assert pos.quantity == 100
    assert pos.notional == pytest.approx(5_000.0)  # 100 × 50


def test_repeat_trades_aggregate_into_one_position(session):
    svc = _service(session)
    pid1 = svc.record_trade("BOOK", "AAPL", Side.LONG, ShortType.NA, 100, 50.0, date(2026, 1, 2))
    pid2 = svc.record_trade("BOOK", "AAPL", Side.LONG, ShortType.NA, 50, 60.0, date(2026, 1, 3))
    assert pid1 == pid2  # same name+book → one position
    assert len(svc.list_positions("BOOK")) == 1  # not two rows (risk keys by ticker)
    pos = svc.get_position(pid1)
    assert pos.quantity == 150
    assert pos.notional == pytest.approx(5_000.0 + 3_000.0)  # both lots' value


def test_systematic_short_is_recordable_and_tagged(session):
    # Recording an approved hedge's execution is allowed; the book tag is preserved.
    svc = _service(session)
    pid = svc.record_trade("BOOK", "XYZ", Side.SHORT, ShortType.SYSTEMATIC, 10, 20.0, date(2026, 1, 2))
    pos = svc.get_position(pid)
    assert pos.side is Side.SHORT and pos.short_type is ShortType.SYSTEMATIC
    assert pos.book.value == "SYSTEMATIC_SHORT"


def test_long_short_conflict_is_blocked(session):
    # Invariant I-05: a name can't be both long and short in one portfolio.
    svc = _service(session)
    svc.record_trade("BOOK", "AAPL", Side.LONG, ShortType.NA, 100, 50.0, date(2026, 1, 2))
    with pytest.raises(ConflictError):
        svc.record_trade("BOOK", "AAPL", Side.SHORT, ShortType.DISCRETIONARY, 10, 50.0, date(2026, 1, 3))


def test_close_scales_notional_to_zero(session):
    svc = _service(session)
    pid = svc.record_trade("BOOK", "AAPL", Side.LONG, ShortType.NA, 100, 50.0, date(2026, 1, 2))
    # Close half → notional halves.
    svc.reduce_position(pid, 50, 60.0)
    assert svc.get_position(pid).notional == pytest.approx(2_500.0)
    # Close the rest → notional zero, no lots left.
    svc.reduce_position(pid, 50, 60.0)
    pos = svc.get_position(pid)
    assert pos.notional == pytest.approx(0.0)
    assert pos.quantity == 0


def test_transaction_endpoint_records_and_lists():
    # End-to-end against the seeded IRIDIUM-CORE portfolio.
    from fastapi.testclient import TestClient
    from neptune.api.main import app
    from neptune.db.base import Base, SecuritiesBase, engine, securities_engine

    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as client:
        url = "/portfolios/IRIDIUM-CORE"
        r = client.post(
            f"{url}/transactions",
            json={"ticker": "NVDA", "book": "LONG", "quantity": 10, "price": 100.0,
                  "trade_date": "2026-01-02"},
        )
        assert r.status_code == 201
        positions = client.get(f"{url}/positions").json()
        nvda = next(p for p in positions if p["ticker"] == "NVDA")
        assert nvda["id"] is not None
        assert nvda["quantity"] == 10
        assert nvda["book"] == "LONG"
