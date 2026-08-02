"""factor_panel_status(): the centralized stale-panel guard. Proves the specific gap this
feature closes -- a LOADED style-factor panel (real FF5+MOM present) can still be STALE
(hasn't refreshed recently), and that must never be silently reported as fine."""
from __future__ import annotations

from datetime import date, timedelta

from neptune.data.db_market import DbMarketData
from neptune.risk import analytics
from neptune.securities.models import FactorReturn, Price, Security


def _seed_prices(session, ticker, iid, n_days, base=100.0):
    """n_days of flat-ish daily prices ending today, so return_dates()[-1] is today."""
    session.add(Security(instrument_id=iid, ticker=ticker, security_type="Common Stock"))
    start = date.today() - timedelta(days=n_days - 1)
    for i in range(n_days):
        session.add(Price(instrument_id=iid, ts=start + timedelta(days=i),
                          close=base, adj_close=base, source="yfinance"))


def _seed_factor_row(session, factor, ts, ret=0.001):
    session.add(FactorReturn(factor=factor, ts=ts, ret=ret, source="ken_french"))


def test_factor_panel_status_fresh(securities_session):
    _seed_prices(securities_session, "SPY", 1, 20)
    # DbMarketData excludes today's still-forming bar (completed closes only), so the last
    # usable return date is yesterday — that's what "fresh" means here.
    last_return_date = date.today() - timedelta(days=1)
    for f in ("SMB", "HML", "RMW", "CMA", "MOM"):
        _seed_factor_row(securities_session, f, last_return_date)
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    status = analytics.factor_panel_status(md)
    assert status["loaded"] is True
    assert status["stale"] is False
    assert status["last_date"] == last_return_date.isoformat()


def test_factor_panel_status_loaded_but_stale(securities_session):
    """The exact non-conflation case: real style-factor data exists (loaded=True) but the
    latest row is well behind the most recent price date -- must report stale=True, not
    silently pass as fresh just because the panel is present."""
    _seed_prices(securities_session, "SPY", 1, 20)
    old = date.today() - timedelta(days=15)
    for f in ("SMB", "HML", "RMW", "CMA", "MOM"):
        _seed_factor_row(securities_session, f, old)  # only near the start of the window
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    status = analytics.factor_panel_status(md)
    assert status["loaded"] is True  # a real panel IS present in the window
    assert status["stale"] is True  # but its latest row is >7 days behind today
    assert status["last_date"] == old.isoformat()


def test_factor_panel_status_not_loaded(securities_session):
    _seed_prices(securities_session, "SPY", 1, 20)
    securities_session.commit()

    md = DbMarketData(securities_session, benchmark="SPY")
    status = analytics.factor_panel_status(md)
    assert status["loaded"] is False
    assert status["stale"] is True  # "not loaded" is trivially "stale" (nothing to be fresh)
    assert status["last_date"] is None


def test_universe_diag_reports_stale_panel_distinct_from_unloaded():
    """End-to-end through /securities/health: a loaded-but-stale panel must set BOTH
    factor_panel (the loaded string) AND factor_panel_stale=True -- proving the two fields
    don't conflate "loaded" with "fresh", which is the actual gap this feature closes."""
    from fastapi.testclient import TestClient
    from neptune.api.main import app
    from neptune.db.base import Base, SecuritiesBase, engine, securities_engine
    from neptune.db.runtime import securities_session as sec_session_cm

    Base.metadata.drop_all(bind=engine)
    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    with TestClient(app) as client:
        from neptune.db.base import SessionLocal
        with SessionLocal() as portfolio_session, sec_session_cm(portfolio_session) as sec:
            _seed_prices(sec, "SPY", 1, 20)
            old = date.today() - timedelta(days=15)
            for f in ("SMB", "HML", "RMW", "CMA", "MOM"):
                _seed_factor_row(sec, f, old)
            sec.commit()

        health = client.get("/securities/health").json()
        assert health["factor_panel"] == "MKT+SMB+HML+RMW+CMA+MOM"  # genuinely loaded
        assert health["factor_panel_stale"] is True  # but stale -- not conflated as fresh
