"""Stress engine: scenario shocks (factor-model P&L, by book) and parametric VaR/ES."""
from __future__ import annotations

import math

import numpy as np
import pytest

from neptune.stress import (
    RISK_FACTORS,
    Scenario,
    StressExposure,
    apply_scenario,
    dollar_factor_exposures,
    historical_var,
    monte_carlo_var,
    parametric_var,
    position_shock_return,
    _norm_ppf,
)

LONG = "LONG"
SYS = "SYSTEMATIC_SHORT"
DISC = "DISCRETIONARY_SHORT"


def _book():
    # A long with beta 1.2, a systematic short hedge beta 1.0, a discretionary short.
    return [
        StressExposure("AAA", LONG, signed_notional=1_000_000, beta=1.2,
                       loadings={"SMB": 0.1}),
        StressExposure("HDG", SYS, signed_notional=-800_000, beta=1.0),
        StressExposure("DDD", DISC, signed_notional=-200_000, beta=0.9,
                       loadings={"HML": 0.2}),
    ]


# --- scenario shocks -------------------------------------------------------------

def test_position_shock_return_factor_model():
    e = StressExposure("AAA", LONG, 1_000_000, beta=1.2, loadings={"SMB": 0.1, "HML": -0.05})
    s = Scenario("x", market_shock=-0.10, factor_shocks={"SMB": -0.03, "HML": 0.02})
    # 1.2*-0.10 + 0.1*-0.03 + -0.05*0.02 = -0.12 - 0.003 - 0.001 = -0.124
    assert position_shock_return(e, s) == pytest.approx(-0.124)


def test_market_down_hurts_long_helps_short():
    res = apply_scenario(_book(), Scenario("Market -10%", market_shock=-0.10))
    # Long: 1,000,000 * (1.2 * -0.10) = -120,000.
    assert res.by_position["AAA"] == pytest.approx(-120_000)
    # Systematic short: -800,000 * (1.0 * -0.10) = +80,000 (gains when market falls).
    assert res.by_position["HDG"] == pytest.approx(80_000)
    # Discretionary short: -200,000 * (0.9 * -0.10) = +18,000.
    assert res.by_position["DDD"] == pytest.approx(18_000)
    assert res.total_pnl == pytest.approx(-22_000)


def test_scenario_split_by_book_sums_to_total_and_separates_shorts():
    res = apply_scenario(_book(), Scenario("Market -10%", market_shock=-0.10))
    # Books are reported separately (invariant I-03).
    assert set(res.by_book) == {LONG, SYS, DISC}
    assert res.by_book[SYS] == pytest.approx(80_000)
    assert res.by_book[DISC] == pytest.approx(18_000)
    assert sum(res.by_book.values()) == pytest.approx(res.total_pnl)


def test_near_neutral_book_has_small_market_shock_pnl():
    # Long 1.0 dollar-beta vs short 1.0 dollar-beta -> net ~0 -> tiny market P&L.
    book = [
        StressExposure("L", LONG, 1_000_000, beta=1.0),
        StressExposure("S", SYS, -1_000_000, beta=1.0),
    ]
    res = apply_scenario(book, Scenario("Market -20%", market_shock=-0.20))
    assert res.net_beta_dollar_before == pytest.approx(0.0)
    assert res.total_pnl == pytest.approx(0.0)


# --- dollar factor exposures -----------------------------------------------------

def test_dollar_factor_exposures():
    e = dollar_factor_exposures(_book(), RISK_FACTORS)
    # MKT = 1,000,000*1.2 + -800,000*1.0 + -200,000*0.9 = 1,200,000 - 800,000 - 180,000 = 220,000
    assert e[0] == pytest.approx(220_000)
    # SMB = 1,000,000*0.1 = 100,000 ; HML = -200,000*0.2 = -40,000 ; MOM = 0
    assert e[1] == pytest.approx(100_000)
    assert e[2] == pytest.approx(-40_000)
    assert e[3] == pytest.approx(0.0)


# --- parametric VaR / ES ---------------------------------------------------------

def test_var_single_factor_matches_closed_form():
    # One position, market-only, diagonal covariance with daily vol 2%.
    book = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    daily_var = 0.02 ** 2
    cov = np.diag([daily_var, 0.0, 0.0, 0.0])
    res = parametric_var(book, cov, confidence=0.95, horizon_days=1)
    # sigma = |1,000,000| * 0.02 = 20,000 ; VaR = 1.645 * 20,000.
    assert res.volatility == pytest.approx(20_000, rel=1e-9)
    z = _norm_ppf(0.95)
    assert res.var == pytest.approx(z * 20_000, rel=1e-9)
    # ES = phi(z)/(1-c) * sigma > VaR.
    es = (math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)) / 0.05 * 20_000
    assert res.expected_shortfall == pytest.approx(es, rel=1e-9)
    assert res.expected_shortfall > res.var


def test_var_scales_with_sqrt_horizon():
    book = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    cov = np.diag([0.02 ** 2, 0.0, 0.0, 0.0])
    one = parametric_var(book, cov, horizon_days=1)
    ten = parametric_var(book, cov, horizon_days=10)
    assert ten.var == pytest.approx(one.var * math.sqrt(10), rel=1e-9)


def test_hedged_book_has_lower_var_than_long_alone():
    long_only = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    hedged = long_only + [StressExposure("S", SYS, -900_000, beta=1.0)]
    cov = np.diag([0.02 ** 2, 0.0, 0.0, 0.0])
    assert parametric_var(hedged, cov).var < parametric_var(long_only, cov).var


def test_var_rejects_bad_confidence_and_shape():
    book = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    with pytest.raises(ValueError):
        parametric_var(book, np.eye(4), confidence=1.5)
    with pytest.raises(ValueError):
        parametric_var(book, np.eye(3))  # wrong size for 4 factors


def test_norm_ppf_known_quantiles():
    assert _norm_ppf(0.95) == pytest.approx(1.6448536, abs=1e-5)
    assert _norm_ppf(0.975) == pytest.approx(1.9599640, abs=1e-5)
    assert _norm_ppf(0.99) == pytest.approx(2.3263479, abs=1e-5)


# --- historical & Monte-Carlo VaR ------------------------------------------------

def test_historical_var_reads_empirical_quantile():
    # Single market-only $1 dollar-beta position so P&L_t == market_return_t (in $).
    book = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    # 100 days of factor returns; only MKT moves, a known spread.
    rng = np.random.default_rng(0)
    mkt = rng.normal(0.0, 0.02, 200)
    returns = np.zeros((200, len(RISK_FACTORS)))
    returns[:, 0] = mkt
    res = historical_var(book, returns, confidence=0.95, horizon_days=1)
    # P&L = 1,000,000 * mkt; loss = -P&L. VaR is the 95th pct of losses.
    losses = -(1_000_000 * mkt)
    assert res.var == pytest.approx(np.quantile(losses, 0.95), rel=1e-9)
    # ES is the mean of losses at or beyond VaR, >= VaR.
    assert res.expected_shortfall >= res.var
    assert res.method == "historical"
    assert res.n_observations == 200


def test_historical_var_short_hedge_lowers_risk():
    rng = np.random.default_rng(1)
    returns = np.zeros((300, len(RISK_FACTORS)))
    returns[:, 0] = rng.normal(0, 0.02, 300)
    long_only = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    hedged = long_only + [StressExposure("S", SYS, -900_000, beta=1.0)]
    assert historical_var(hedged, returns).var < historical_var(long_only, returns).var


def test_monte_carlo_var_is_deterministic_and_close_to_parametric():
    book = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    cov = np.diag([0.02 ** 2, 0.0, 0.0, 0.0])
    a = monte_carlo_var(book, cov, confidence=0.95, n_draws=100_000, seed=42)
    b = monte_carlo_var(book, cov, confidence=0.95, n_draws=100_000, seed=42)
    assert a.var == b.var  # deterministic given seed
    assert a.method == "monte_carlo" and a.n_observations == 100_000
    # With enough draws, MC VaR converges to the parametric (normal) VaR.
    p = parametric_var(book, cov, confidence=0.95)
    assert a.var == pytest.approx(p.var, rel=0.03)
    assert a.expected_shortfall >= a.var


def test_historical_var_rejects_bad_shape_and_confidence():
    book = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    with pytest.raises(ValueError):
        historical_var(book, np.zeros((10, 3)))  # wrong factor count
    with pytest.raises(ValueError):
        historical_var(book, np.zeros((10, 4)), confidence=0.4)


def test_monte_carlo_rejects_bad_draws():
    book = [StressExposure("L", LONG, 1_000_000, beta=1.0)]
    with pytest.raises(ValueError):
        monte_carlo_var(book, np.eye(4), n_draws=0)


def test_scenario_uses_downside_beta_on_selloff_upside_on_rally():
    """With down_beta > up_beta, a -10% selloff loses MORE than a +10% rally gains — the
    asymmetry a single beta hides. Without directional betas, the two stay mirror images."""
    asym = StressExposure("AAA", LONG, signed_notional=1_000_000, beta=1.0,
                          down_beta=1.5, up_beta=0.5)
    down = position_shock_return(asym, Scenario("d", market_shock=-0.10))
    up = position_shock_return(asym, Scenario("u", market_shock=0.10))
    assert down == pytest.approx(-0.15)   # 1.5 * -0.10
    assert up == pytest.approx(0.05)      # 0.5 * +0.10
    assert abs(down) > abs(up)

    sym = StressExposure("BBB", LONG, signed_notional=1_000_000, beta=1.0)
    d = position_shock_return(sym, Scenario("d", market_shock=-0.10))
    u = position_shock_return(sym, Scenario("u", market_shock=0.10))
    assert d == pytest.approx(-u)         # symmetric when no directional betas
