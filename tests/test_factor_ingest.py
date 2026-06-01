"""Ken French factor ingestion: provider window, idempotent upsert, and the DbMarketData
panel (MKT-only fallback → full {MKT,SMB,HML,MOM} once ingested, aligned to the market)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from neptune.data.db_market import DbMarketData
from neptune.quant.factors import FACTORS, factor_loadings
from neptune.securities.factor_ingest import ingest_factors
from neptune.securities.factor_providers import (
    FactorObservation,
    RecordedFactorProvider,
    parse_french_daily_csv,
)
from neptune.securities.models import FactorReturn, Price, Security


def _dates(n: int, start=date(2026, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _seed_prices(session, ticker, iid, dates, closes, source="yfinance"):
    session.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock"))
    for ts, c in zip(dates, closes):
        session.add(Price(instrument_id=iid, ts=ts, close=c, adj_close=c, source=source))
    session.commit()


def _panel(dates, values: dict[str, list[float]], source="ken_french"):
    """Build FactorObservations for the given factor→daily-returns map."""
    obs = []
    for factor, series in values.items():
        for ts, r in zip(dates, series):
            obs.append(FactorObservation(factor=factor, ts=ts, ret=r))
    return RecordedFactorProvider(obs, source=source)


# --- provider + ingest ------------------------------------------------------------

def test_recorded_provider_filters_window():
    ds = _dates(5)
    prov = _panel(ds, {"SMB": [0.001, 0.002, -0.001, 0.0, 0.003]})
    got = prov.fetch(ds[1], ds[3])
    assert {o.ts for o in got} == {ds[1], ds[2], ds[3]}


def test_ingest_writes_and_is_idempotent(securities_session):
    ds = _dates(3)
    prov = _panel(ds, {"SMB": [0.001, 0.002, 0.003], "HML": [0.0, -0.001, 0.002]})
    counts = ingest_factors(securities_session, prov, ds[0], ds[-1])
    assert counts == {"SMB": 3, "HML": 3}
    ingest_factors(securities_session, prov, ds[0], ds[-1])  # again
    rows = securities_session.scalars(select(FactorReturn)).all()
    assert len(rows) == 6  # 2 factors × 3 days, upserted not duplicated


def test_ingest_source_tag_coexists(securities_session):
    ds = _dates(2)
    ingest_factors(securities_session, _panel(ds, {"SMB": [0.01, 0.02]}, source="ken_french"),
                   ds[0], ds[-1])
    ingest_factors(securities_session, _panel(ds, {"SMB": [0.05, 0.06]}, source="other"),
                   ds[0], ds[-1])
    rows = securities_session.scalars(select(FactorReturn)).all()
    assert {r.source for r in rows} == {"ken_french", "other"}


# --- Ken French CSV parsing (the real-feed format, without the network) -----------

# Mirrors the published daily files: prose header, column row, YYYYMMDD rows in percent,
# blank line, then a copyright footer.
_FACTORS_CSV = """\
This file was created by CMPT_ME_BEME_RETS using the 202312 CRSP database.

,Mkt-RF,SMB,HML,RF
20240102,  0.50, -0.24,  0.28, 0.021
20240103, -1.10,  0.05, -0.15, 0.021

Copyright 2024 Kenneth R. French
"""

_MOMENTUM_CSV = """\
This file was created ... Momentum Factor (Mom).

,Mom
20240102,  0.33
20240103, -0.40

 Copyright 2024 Kenneth R. French
"""


def test_parse_factors_csv_percent_to_decimal_and_footer_stop():
    obs = parse_french_daily_csv(_FACTORS_CSV)
    # 2 days × {Mkt-RF→MKT_RF, SMB, HML, RF} = 8; footer/blank lines ignored.
    assert len(obs) == 8
    by = {(o.factor, o.ts): o.ret for o in obs}
    assert by[("MKT_RF", date(2024, 1, 2))] == pytest.approx(0.005)  # 0.50% → 0.005
    assert by[("SMB", date(2024, 1, 3))] == pytest.approx(0.0005)
    assert by[("RF", date(2024, 1, 2))] == pytest.approx(0.00021)
    assert {o.factor for o in obs} == {"MKT_RF", "SMB", "HML", "RF"}


def test_parse_momentum_csv_maps_mom_to_canonical():
    obs = parse_french_daily_csv(_MOMENTUM_CSV)
    assert {o.factor for o in obs} == {"MOM"}
    by = {o.ts: o.ret for o in obs}
    assert by[date(2024, 1, 2)] == pytest.approx(0.0033)
    assert by[date(2024, 1, 3)] == pytest.approx(-0.004)


def test_parse_handles_prose_only_without_data():
    # No column/data rows → no observations, and no crash.
    assert parse_french_daily_csv("just a note\nno table here\n") == []


# --- DbMarketData panel -----------------------------------------------------------

def test_factor_returns_mkt_only_without_panel(securities_session):
    ds = _dates(4)
    _seed_prices(securities_session, "SPY", 1, ds, [100.0, 101.0, 102.0, 103.0])
    md = DbMarketData(securities_session, benchmark="SPY")
    # No factor panel ingested → MKT only.
    assert set(md.factor_returns()) == {"MKT"}


def test_factor_returns_full_panel_aligned(securities_session):
    ds = _dates(4)
    _seed_prices(securities_session, "SPY", 1, ds, [100.0, 101.0, 102.0, 103.0])
    # Factor returns are dated on the "to" days of each market move: ds[1:].
    prov = _panel(ds, {
        "SMB": [0.0, 0.001, 0.002, 0.003],
        "HML": [0.0, -0.001, 0.0, 0.002],
        "MOM": [0.0, 0.004, -0.002, 0.001],
    })
    ingest_factors(securities_session, prov, ds[0], ds[-1])

    md = DbMarketData(securities_session, benchmark="SPY")
    factors = md.factor_returns()
    assert set(factors) == set(FACTORS)  # MKT + SMB + HML + MOM
    # Each factor series is the same length as the market series (the regression contract).
    for name in FACTORS:
        assert len(factors[name]) == len(md.market_returns())
    # SMB aligned to ds[1:] → [0.001, 0.002, 0.003].
    np.testing.assert_allclose(factors["SMB"], [0.001, 0.002, 0.003])


def test_incomplete_panel_falls_back_to_mkt_only(securities_session):
    ds = _dates(4)
    _seed_prices(securities_session, "SPY", 1, ds, [100.0, 101.0, 102.0, 103.0])
    # Only SMB present — half a factor model is worse than none → MKT-only.
    ingest_factors(securities_session, _panel(ds, {"SMB": [0.0, 0.001, 0.002, 0.003]}),
                   ds[0], ds[-1])
    md = DbMarketData(securities_session, benchmark="SPY")
    assert set(md.factor_returns()) == {"MKT"}


def test_engine_recovers_factor_loading_from_stored_panel(securities_session):
    # A stock whose returns are market + 0.8*SMB should regress to ~0.8 on SMB.
    rng = np.random.default_rng(1)
    n = 80
    ds = _dates(n + 1)
    rm = rng.normal(0.0, 0.01, n)
    smb = rng.normal(0.0, 0.01, n)
    hml = rng.normal(0.0, 0.01, n)
    mom = rng.normal(0.0, 0.01, n)
    stock = rm + 0.8 * smb  # loads 0.8 on SMB, 1.0 on market, 0 on HML/MOM

    def _compound(returns):
        p = [100.0]
        for r in returns:
            p.append(p[-1] * (1 + r))
        return p

    _seed_prices(securities_session, "SPY", 1, ds, _compound(rm))
    _seed_prices(securities_session, "AAA", 2, ds, _compound(stock))
    # Factor panel dated on ds[1:] (the move-to days).
    prov = _panel(ds[1:], {"SMB": list(smb), "HML": list(hml), "MOM": list(mom)})
    ingest_factors(securities_session, prov, ds[1], ds[-1])

    md = DbMarketData(securities_session, benchmark="SPY")
    loads = factor_loadings(md.ticker_returns("AAA"), md.factor_returns(), window=n).loadings
    assert loads["SMB"] == pytest.approx(0.8, abs=0.05)
    assert loads["HML"] == pytest.approx(0.0, abs=0.05)
