"""Golden-number tests for the beta pipeline (252-day OLS raw beta -> Vasicek shrinkage)."""
from __future__ import annotations

import numpy as np
import pytest

from neptune.data.fixtures import make_market_returns, make_stock_returns
from neptune.quant.beta import (
    beta_pipeline,
    cross_sectional_prior_var,
    raw_beta,
    vasicek_shrinkage,
)

BETA_TRUE = 1.3
VAR_PRIOR = 0.08


def _reference_raw_beta(stock, market, lookback=252) -> float:
    """Independent OLS slope via cov/var — a different numerical path than the engine's
    normal-equations solver."""
    n = min(stock.shape[0], market.shape[0])
    s, m = stock[-n:], market[-n:]
    if s.shape[0] > lookback:
        s, m = s[-lookback:], m[-lookback:]
    return float(np.cov(s, m, bias=True)[0, 1] / np.var(m))


def test_noise_free_recovers_true_beta_exactly():
    market = make_market_returns(n=300, seed=7)
    stock = make_stock_returns(market, beta_true=BETA_TRUE, noise_std=0.0)

    result = beta_pipeline(stock, market, var_prior=VAR_PRIOR)

    # Exact linear data -> OLS recovers beta, variance ~0, Vasicek w ~1.
    assert result.beta_raw == pytest.approx(BETA_TRUE, abs=1e-8)
    assert result.var_ols == pytest.approx(0.0, abs=1e-12)
    assert result.weight == pytest.approx(1.0, abs=1e-9)
    assert result.beta == pytest.approx(BETA_TRUE, abs=1e-8)


def test_known_noise_exercises_vasicek_shrinkage():
    market = make_market_returns(n=300, seed=7)
    stock = make_stock_returns(market, beta_true=BETA_TRUE, noise_std=0.02, seed=11)

    result = beta_pipeline(stock, market, var_prior=VAR_PRIOR)

    # Noise makes the estimate uncertain, so shrinkage is genuinely active.
    assert result.var_ols > 0.0
    assert 0.0 < result.weight < 1.0

    # Raw beta matches an independent OLS path.
    assert result.beta_raw == pytest.approx(_reference_raw_beta(stock, market), rel=1e-9)

    # Shrunk beta lies strictly between the raw estimate and the prior mean of 1.0.
    assert min(result.beta_raw, 1.0) < result.beta < max(result.beta_raw, 1.0)

    # Vasicek wiring is internally consistent: beta = w*raw + (1-w)*1.0.
    expected = result.weight * result.beta_raw + (1 - result.weight) * 1.0
    assert result.beta == pytest.approx(expected, rel=1e-12)


def test_noisier_estimate_shrinks_harder():
    market = make_market_returns(n=300, seed=7)
    low = beta_pipeline(make_stock_returns(market, BETA_TRUE, 0.01, seed=3), market, VAR_PRIOR)
    high = beta_pipeline(make_stock_returns(market, BETA_TRUE, 0.05, seed=3), market, VAR_PRIOR)
    # More noise -> larger var_ols -> smaller weight -> closer to the prior (1.0).
    assert high.weight < low.weight
    assert abs(high.beta - 1.0) < abs(low.beta - 1.0)


def test_ols_recovers_true_beta_far_better_than_a_short_window():
    """The point of the §4 revision: a 1-year OLS is accurate even with realistic noise."""
    market = make_market_returns(n=400, seed=2)
    stock = make_stock_returns(market, beta_true=0.55, noise_std=0.012, seed=9)
    raw = raw_beta(stock, market)
    assert raw.beta_raw == pytest.approx(0.55, abs=0.1)  # close to truth, not sign-flipped


def test_forward_override_supersedes_pipeline():
    market = make_market_returns(n=300, seed=7)
    stock = make_stock_returns(market, beta_true=BETA_TRUE, noise_std=0.02)
    result = beta_pipeline(stock, market, var_prior=VAR_PRIOR, forward_beta=0.42)
    assert result.method == "forward_override"
    assert result.beta == 0.42


def test_vasicek_requires_positive_prior():
    with pytest.raises(ValueError):
        vasicek_shrinkage(1.2, var_ols=0.01, var_prior=0.0)


def test_raw_beta_returns_estimation_variance():
    market = make_market_returns(n=300, seed=7)
    stock = make_stock_returns(market, BETA_TRUE, noise_std=0.03, seed=5)
    raw = raw_beta(stock, market)
    assert raw.var_ols > 0
    assert raw.n_obs <= 252


def test_cross_sectional_prior_var():
    # Vasicek prior = sample variance of the cross-section of raw betas.
    betas = np.array([0.8, 1.0, 1.2, 1.4])
    assert cross_sectional_prior_var(betas) == pytest.approx(np.var(betas, ddof=1))
    with pytest.raises(ValueError):
        cross_sectional_prior_var(np.array([1.0]))


def test_semibetas_capture_down_up_asymmetry():
    """A name engineered to move 2x with down-markets and 0.5x with up-markets should yield
    down_beta ~ 2 and up_beta ~ 0.5 — and neither should touch the pipeline beta."""
    from neptune.quant.beta import semibetas

    rng = np.random.default_rng(0)
    market = rng.normal(0, 0.01, 400)
    stock = np.where(market < 0, 2.0 * market, 0.5 * market)
    sb = semibetas(stock, market, fallback=1.0)
    assert sb.down_beta == pytest.approx(2.0, abs=1e-6)
    assert sb.up_beta == pytest.approx(0.5, abs=1e-6)


def test_semibetas_fall_back_on_thin_side():
    """If one side has too few observations, that side returns the fallback (pipeline) beta."""
    from neptune.quant.beta import semibetas

    market = np.array([-0.02, -0.01, -0.03, -0.015, 0.01])  # only 1 up day
    stock = market * 1.5
    sb = semibetas(market * 1.5, market, fallback=0.9, min_obs=3)
    assert sb.down_beta == pytest.approx(1.5, abs=1e-9)
    assert sb.up_beta == 0.9  # thin up side -> fallback
