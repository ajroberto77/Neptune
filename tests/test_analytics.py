"""Live book analytics: the beta pipeline + factor regression wired over a portfolio."""
from __future__ import annotations

import pytest

from neptune.data.market import CATALOG, SyntheticMarketData
from neptune.domain.models import Portfolio, Position, Side, ShortType
from neptune.risk import analytics


def test_market_data_is_deterministic_and_shaped():
    a = SyntheticMarketData(n=300, seed=1)
    b = SyntheticMarketData(n=300, seed=1)
    assert a.market_returns().shape == (300,)
    assert (a.ticker_returns("AAA") == b.ticker_returns("AAA")).all()
    assert set(a.factor_returns().keys()) == {"MKT", "SMB", "HML", "RMW", "CMA", "MOM"}


class _ThinMarketData:
    """A market-data source with too few observations to fit the beta regression (OLS needs 3)."""

    def __init__(self, n=2):
        import numpy as np

        self._r = np.linspace(0.001, 0.004, n)

    def market_returns(self):
        return self._r

    def ticker_returns(self, ticker):
        return self._r

    def factor_returns(self):
        return {"MKT": self._r}


def test_compute_metrics_degrades_gracefully_on_thin_history():
    # Freshly-ingested names (e.g. SPY pulled via the 7-day refresh) have too little history
    # for the beta regression — it must NOT crash the endpoint; beta holds at the prior.
    portfolio = Portfolio(id="P", name="thin", positions=[Position("PZZA", Side.LONG, 1_000)])
    metrics = analytics.compute_metrics(portfolio, _ThinMarketData())
    assert metrics["PZZA"].beta == 1.0
    assert metrics["PZZA"].beta_method == "insufficient_data"
    assert metrics["PZZA"].weight == 0.0


def test_pipeline_recovers_true_beta_for_non_forward_positions():
    md = SyntheticMarketData()
    # No forward override -> beta comes from the EWMA+Dimson -> Vasicek pipeline.
    portfolio = Portfolio(
        id="P", name="live",
        positions=[
            Position("HDG3", Side.LONG, 1_000_000),  # true beta 1.25
            Position("HDG4", Side.LONG, 1_000_000),  # true beta 0.85
        ],
    )
    metrics = analytics.compute_metrics(portfolio, md)
    assert metrics["HDG3"].beta_method == "pipeline"
    # Recovered close to the catalog truth (shrunk slightly toward 1.0).
    assert metrics["HDG3"].beta == pytest.approx(CATALOG["HDG3"].true_beta, abs=0.1)
    assert metrics["HDG4"].beta == pytest.approx(CATALOG["HDG4"].true_beta, abs=0.1)
    # Style loadings are recovered with the right sign.
    assert metrics["HDG3"].loadings["SMB"] > 0
    assert metrics["HDG4"].loadings["SMB"] < 0


def test_position_beta_is_stable_under_book_composition():
    """A name's beta must depend only on its own returns, never on what else is in the book.
    Adding 35 other positions (longs and systematic shorts) must NOT move HDG3's beta — the
    Vasicek prior is a fixed market-level constant, not the book's own cross-section."""
    md = SyntheticMarketData()
    solo = Portfolio(id="P", name="solo", positions=[Position("HDG3", Side.LONG, 1_000_000)])
    others = [Position(t, Side.LONG, 1_000_000) for t in ["HDG1", "HDG2", "HDG4"]]
    others += [
        Position(t, Side.SHORT, 500_000, short_type=ShortType.SYSTEMATIC)
        for t in ["AAA", "BBB", "CCC", "DDD"]
    ]
    crowded = Portfolio(id="P", name="crowded",
                        positions=[Position("HDG3", Side.LONG, 1_000_000), *others])

    b_solo = analytics.compute_metrics(solo, md)["HDG3"].beta
    b_crowded = analytics.compute_metrics(crowded, md)["HDG3"].beta
    assert b_solo == pytest.approx(b_crowded, abs=1e-12)  # identical — book-independent


def test_forward_override_wins_but_loadings_still_computed():
    md = SyntheticMarketData()
    portfolio = Portfolio(
        id="P", name="fwd",
        positions=[
            Position("AAA", Side.LONG, 1_000_000, forward_beta=0.42),
            Position("BBB", Side.LONG, 1_000_000),
        ],
    )
    metrics = analytics.compute_metrics(portfolio, md)
    assert metrics["AAA"].beta == 0.42
    assert metrics["AAA"].beta_method == "forward_override"
    # Factor loadings are not overridden by a forward beta.
    assert metrics["AAA"].loadings  # non-empty, regression-derived


def test_net_metrics_uses_signed_notionals():
    md = SyntheticMarketData()
    portfolio = Portfolio(
        id="P", name="net",
        positions=[
            Position("AAA", Side.LONG, 1_000_000, forward_beta=1.2),
            Position("DDD", Side.SHORT, 500_000, short_type=ShortType.DISCRETIONARY,
                     forward_beta=1.0),
        ],
    )
    metrics = analytics.compute_metrics(portfolio, md)
    net_beta, net_factors = analytics.net_metrics(portfolio, metrics)
    # (1.2*1,000,000 - 1.0*500,000) / 1,000,000 = 0.7
    assert net_beta == pytest.approx(0.7, abs=1e-9)
    assert set(net_factors) >= {"SMB", "HML", "MOM"}


def test_live_universe_has_betas_and_loadings():
    md = SyntheticMarketData()
    universe = analytics.live_universe(md, ["HDG1", "HDG2", "HDG3"])
    assert len(universe) == 3
    for c in universe:
        assert c.beta > 0
        assert set(c.loadings) >= {"SMB", "HML", "MOM"}
