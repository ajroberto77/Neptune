"""yfinance-shaped price ingestion: back-adjustment, idempotent upsert, ticker resolution."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from neptune.securities.adjust import reconstruct_adj_close
from neptune.securities.ingest import ingest_history, ingest_ticker
from neptune.securities.models import CorporateActionType, Price, Security
from neptune.securities.providers import (
    CorporateActionRecord,
    DividendRecord,
    PriceBar,
    RecordedProvider,
    SecurityHistory,
)


def _seed_security(session, instrument_id=320193, ticker="AAPL") -> Security:
    sec = Security(instrument_id=instrument_id, ticker=ticker, security_type="Common Stock")
    session.add(sec)
    session.commit()
    return sec


# --- back-adjustment --------------------------------------------------------------

def test_split_back_adjustment():
    # 2:1 split effective day 3 → earlier closes halved, newest unchanged.
    bars = [
        PriceBar(ts=date(2026, 1, 1), close=100.0),
        PriceBar(ts=date(2026, 1, 2), close=102.0),
        PriceBar(ts=date(2026, 1, 3), close=50.0),
    ]
    actions = [CorporateActionRecord(CorporateActionType.SPLIT, date(2026, 1, 3), ratio=2.0)]
    adj = reconstruct_adj_close(bars, [], actions)
    assert adj[date(2026, 1, 3)] == 50.0   # newest: unadjusted
    assert adj[date(2026, 1, 1)] == 50.0   # 100 / 2
    assert adj[date(2026, 1, 2)] == 51.0   # 102 / 2


def test_reverse_split_back_adjustment():
    # 1:10 reverse split (ratio 0.1) effective day 2 → earlier prices scaled UP ×10.
    bars = [
        PriceBar(ts=date(2026, 1, 1), close=1.0),
        PriceBar(ts=date(2026, 1, 2), close=10.0),
    ]
    actions = [CorporateActionRecord(CorporateActionType.SPLIT, date(2026, 1, 2), ratio=0.1)]
    adj = reconstruct_adj_close(bars, [], actions)
    assert adj[date(2026, 1, 2)] == 10.0  # newest unadjusted
    assert adj[date(2026, 1, 1)] == pytest.approx(10.0)  # 1.0 / 0.1


def test_same_day_split_and_dividend():
    # Both effective day 2: each applies to strictly-earlier bars; dividend uses raw C_prev.
    bars = [
        PriceBar(ts=date(2026, 1, 1), close=100.0),
        PriceBar(ts=date(2026, 1, 2), close=50.0),
    ]
    divs = [DividendRecord(ex_date=date(2026, 1, 2), amount=1.0)]
    actions = [CorporateActionRecord(CorporateActionType.SPLIT, date(2026, 1, 2), ratio=2.0)]
    adj = reconstruct_adj_close(bars, divs, actions)
    assert adj[date(2026, 1, 2)] == 50.0
    # day1: /2 (split) then *(1 - 1/100) (dividend, C_prev = 100) = 50 * 0.99 = 49.5
    assert adj[date(2026, 1, 1)] == pytest.approx(49.5)


def test_dividend_back_adjustment():
    # $1 dividend ex day 2; C_prev = 100 → earlier prices * (1 - 1/100).
    bars = [
        PriceBar(ts=date(2026, 1, 1), close=100.0),
        PriceBar(ts=date(2026, 1, 2), close=100.0),
    ]
    divs = [DividendRecord(ex_date=date(2026, 1, 2), amount=1.0)]
    adj = reconstruct_adj_close(bars, divs, [])
    assert adj[date(2026, 1, 2)] == 100.0
    assert adj[date(2026, 1, 1)] == pytest.approx(99.0)


# --- ingest -----------------------------------------------------------------------

def _history() -> SecurityHistory:
    return SecurityHistory(
        prices=[
            PriceBar(ts=date(2026, 1, 1), close=100.0, volume=1_000),
            PriceBar(ts=date(2026, 1, 2), close=102.0, volume=1_100),
            PriceBar(ts=date(2026, 1, 3), close=50.0, volume=2_200),
        ],
        dividends=[DividendRecord(ex_date=date(2026, 1, 2), amount=0.5)],
        corporate_actions=[
            CorporateActionRecord(CorporateActionType.SPLIT, date(2026, 1, 3), ratio=2.0)
        ],
    )


def test_ingest_history_writes_facts_and_reconstructs_adj_close(securities_session):
    sec = _seed_security(securities_session)
    res = ingest_history(securities_session, sec, _history(), source="recorded")
    assert (res.prices, res.dividends, res.corporate_actions) == (3, 1, 1)
    prices = securities_session.scalars(
        select(Price).where(Price.instrument_id == sec.instrument_id).order_by(Price.ts)
    ).all()
    # adj_close was reconstructed (source supplied none): newest unadjusted, oldest scaled.
    assert prices[-1].adj_close == 50.0
    assert prices[0].adj_close < prices[0].close  # split + dividend pulled it below raw


def test_ingest_is_idempotent(securities_session):
    sec = _seed_security(securities_session)
    ingest_history(securities_session, sec, _history(), source="recorded")
    ingest_history(securities_session, sec, _history(), source="recorded")  # again
    rows = securities_session.scalars(
        select(Price).where(Price.instrument_id == sec.instrument_id)
    ).all()
    assert len(rows) == 3  # not 6 — upserted in place


def test_ingest_source_tag_coexists(securities_session):
    sec = _seed_security(securities_session)
    ingest_history(securities_session, sec, _history(), source="yfinance")
    ingest_history(securities_session, sec, _history(), source="bloomberg")
    rows = securities_session.scalars(
        select(Price).where(Price.instrument_id == sec.instrument_id)
    ).all()
    # Same (security, day) from two sources coexist (append-only, I-07).
    assert len(rows) == 6
    assert {r.source for r in rows} == {"yfinance", "bloomberg"}


def test_ingest_ticker_resolves_via_projection(securities_session):
    _seed_security(securities_session, instrument_id=789019, ticker="MSFT")
    provider = RecordedProvider({"MSFT": _history()})
    res = ingest_ticker(
        securities_session, provider, "MSFT", date(2026, 1, 1), date(2026, 1, 31)
    )
    assert res.ticker == "MSFT" and res.prices == 3


def test_ingest_ticker_unknown_raises(securities_session):
    provider = RecordedProvider({})
    with pytest.raises(LookupError):
        ingest_ticker(securities_session, provider, "NOPE", date(2026, 1, 1), date(2026, 1, 2))


def test_recorded_provider_filters_to_window(securities_session):
    provider = RecordedProvider({"AAPL": _history()})
    h = provider.fetch("AAPL", date(2026, 1, 1), date(2026, 1, 2))
    assert {b.ts for b in h.prices} == {date(2026, 1, 1), date(2026, 1, 2)}  # day 3 excluded
