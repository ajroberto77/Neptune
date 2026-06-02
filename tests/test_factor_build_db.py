"""The Risk-Interface builder persists Neptune's price-only factors to factor_returns +
factor_definitions, idempotently, alongside (never overwriting) the Ken French panel."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from sqlalchemy import select

from neptune.data.db_market import DbMarketData
from neptune.data.market import SyntheticMarketData
from neptune.domain.models import Portfolio, Position, ShortType, Side
from neptune.risk.beta_store import rebuild_betas
from neptune.risk.factor_build import (
    NEPTUNE_SOURCE,
    build_neptune_factors,
    monitor_report,
)
from neptune.securities.models import FactorDefinition, FactorReturn, Price, Security


def _seed(session, ticker, iid, returns, sector=None, base=100.0, volume=1_000_000.0):
    session.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock",
                         sector=sector))
    px = [base]
    for r in returns:
        px.append(px[-1] * (1 + r))
    start = date.today() - timedelta(days=len(px) + 1)
    for i, p in enumerate(px):
        session.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                          close=p, adj_close=p, volume=volume, source="yfinance"))


def test_build_neptune_factors_persists_series_and_definitions(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())  # benchmark
    _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"), sector="Technology")
    _seed(securities_session, "BBB", 3, synth.ticker_returns("BBB"), sector="Technology")
    _seed(securities_session, "CCC", 4, synth.ticker_returns("CCC"), sector="Energy")
    _seed(securities_session, "DDD", 5, synth.ticker_returns("DDD"), sector="Energy")
    securities_session.commit()
    rebuild_betas(securities_session)  # BAB ranks on materialized betas

    written = build_neptune_factors(securities_session, window=60)

    # Every single-series factor plus the two sector factors got rows.
    for f in ("IVOL", "AMIHUD", "BAB"):
        assert written.get(f, 0) > 0
    assert "SECTOR_TECHNOLOGY" in written and "SECTOR_ENERGY" in written

    # Stored under the neptune source as finite decimal returns — Ken French source untouched.
    rows = securities_session.execute(
        select(FactorReturn.factor, FactorReturn.ret, FactorReturn.source)
        .where(FactorReturn.source == NEPTUNE_SOURCE)
    ).all()
    assert rows and all(np.isfinite(r) and abs(r) < 1.0 for _, r, _ in rows)
    assert all(src == NEPTUNE_SOURCE for *_, src in rows)

    # Registry rows describe construction + family/kind.
    defs = {d.factor: d for d in securities_session.execute(
        select(FactorDefinition)).scalars()}
    assert defs["IVOL"].family == "NEPTUNE" and defs["IVOL"].kind == "price"
    assert defs["SECTOR_TECHNOLOGY"].family == "SECTOR"
    assert "value-weighting pending" in defs["SECTOR_ENERGY"].method


def test_monitor_report_factor_and_sector_exposures(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())
    _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"), sector="Technology")
    _seed(securities_session, "BBB", 3, synth.ticker_returns("BBB"), sector="Technology")
    _seed(securities_session, "CCC", 4, synth.ticker_returns("CCC"), sector="Energy")
    _seed(securities_session, "DDD", 5, synth.ticker_returns("DDD"), sector="Energy")
    securities_session.commit()
    rebuild_betas(securities_session)
    build_neptune_factors(securities_session, window=60)

    md = DbMarketData(securities_session, benchmark="SPY")
    port = Portfolio(id="p", name="P", positions=[
        Position("AAA", Side.LONG, 1_000_000.0),
        Position("CCC", Side.SHORT, 400_000.0, short_type=ShortType.SYSTEMATIC),
    ])
    rep = monitor_report(port, md)

    assert rep["available"] is True
    assert set(rep["factors"]) == {"IVOL", "BAB", "AMIHUD"}
    assert all(np.isfinite(v) for v in rep["factors"].values())
    # Long Technology, short Energy -> net sector weights have those signs.
    assert rep["sectors"]["Technology"] > 0
    assert rep["sectors"]["Energy"] < 0


def test_monitor_report_unavailable_without_panel(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())
    _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"))  # no sector, no factors built
    securities_session.commit()
    md = DbMarketData(securities_session, benchmark="SPY")
    port = Portfolio(id="p", name="P",
                     positions=[Position("AAA", Side.LONG, 1_000_000.0)])
    rep = monitor_report(port, md)
    assert rep["available"] is False and rep["factors"] == {}


def test_build_is_idempotent(securities_session):
    synth = SyntheticMarketData()
    _seed(securities_session, "SPY", 1, synth.market_returns())
    _seed(securities_session, "AAA", 2, synth.ticker_returns("AAA"), sector="Technology")
    _seed(securities_session, "BBB", 3, synth.ticker_returns("BBB"), sector="Energy")
    securities_session.commit()

    first = build_neptune_factors(securities_session, window=60)
    count1 = len(securities_session.execute(
        select(FactorReturn).where(FactorReturn.source == NEPTUNE_SOURCE)).all())
    second = build_neptune_factors(securities_session, window=60)
    count2 = len(securities_session.execute(
        select(FactorReturn).where(FactorReturn.source == NEPTUNE_SOURCE)).all())
    assert first == second and count1 == count2  # rebuild replaces, no duplication
