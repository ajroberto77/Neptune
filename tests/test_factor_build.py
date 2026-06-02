"""Golden-number tests for the price-only factor construction (quant engine, pure)."""
import numpy as np

from neptune.quant.factor_build import (
    long_short_returns,
    rolling_amihud,
    rolling_idio_vol,
    sector_excess_returns,
)


def test_long_short_low_minus_high_lags_the_score():
    # 4 names, 3 dates; frac=0.3 -> 1 name per leg. Baskets use the LAGGED score column.
    scores = np.array([[1, 4], [2, 3], [3, 2], [4, 1]], dtype=float)  # cols are t-1 for t=1,2
    rets = np.array(
        [[0.0, 0.10, 0.05], [0.0, 0.20, 0.06], [0.0, 0.30, 0.07], [0.0, 0.40, 0.08]]
    )
    # pad scores to T=3 (col for the last date is unused — factor[T-1] uses col T-2)
    S = np.column_stack([scores[:, 0], scores[:, 1], scores[:, 1]])
    f = long_short_returns(rets, S, frac=0.3)
    # t=1: low=name0 (score 1), high=name3 (score 4): 0.10 - 0.40
    # t=2: low=name3 (score 1), high=name0 (score 4): 0.08 - 0.05
    assert np.isnan(f[0])  # no prior cross-section -> no basket
    assert np.isclose(f[1], 0.10 - 0.40)
    assert np.isclose(f[2], 0.08 - 0.05)


def test_bab_scales_each_leg_to_unit_beta():
    S = np.array([[1.0, 4.0, 4.0], [2, 3, 3], [3, 2, 2], [4, 1, 1]])
    rets = np.array(
        [[0.0, 0.10, 0.05], [0.0, 0.20, 0.06], [0.0, 0.30, 0.07], [0.0, 0.40, 0.08]]
    )
    betas = np.array([[0.5, 2.0, 2.0], [1, 1, 1], [1, 1, 1], [2.0, 0.5, 0.5]])
    f = long_short_returns(rets, S, frac=0.3, betas=betas)
    # t=1: low=name0 (β 0.5): 0.10/0.5=0.20 ; high=name3 (β 2): 0.40/2=0.20 -> 0
    # t=2: low=name3 (β 0.5): 0.08/0.5=0.16 ; high=name0 (β 2): 0.05/2=0.025 -> 0.135
    assert np.isclose(f[1], 0.0)
    assert np.isclose(f[2], 0.16 - 0.025)


def test_bab_skips_dates_with_degenerate_leg_beta():
    # A near-zero / negative low-beta leg would explode or sign-flip under 1/β — skip the date.
    S = np.array([[1.0, 1.0], [2, 2], [3, 3], [4, 4]])
    rets = np.array([[0.0, 0.10], [0.0, 0.20], [0.0, 0.30], [0.0, 0.40]])
    betas = np.array([[0.05, 0.05], [1, 1], [1, 1], [2.0, 2.0]])  # low leg β=0.05 < floor
    f = long_short_returns(rets, S, frac=0.3, betas=betas, beta_floor=0.25)
    assert np.isnan(f[1])
    betas_neg = np.array([[-0.5, -0.5], [1, 1], [1, 1], [2.0, 2.0]])
    assert np.isnan(long_short_returns(rets, S, frac=0.3, betas=betas_neg)[1])


def test_rolling_idio_vol_zero_when_market_explains_returns():
    mkt = np.array([0.01, -0.02, 0.03, -0.01, 0.02])
    asset = 2.0 * mkt  # perfectly explained -> residual 0
    iv = rolling_idio_vol(asset, mkt, window=4)
    assert np.isnan(iv[0]) and np.isnan(iv[2])  # window not yet full
    assert np.isclose(iv[3], 0.0, atol=1e-9)
    assert np.isclose(iv[4], 0.0, atol=1e-9)


def test_rolling_amihud_mean_of_impact_and_skips_zero_volume():
    r = np.array([0.02, -0.04, 0.06])
    dv = np.array([1.0, 2.0, 3.0])
    a = rolling_amihud(r, dv, window=2)
    assert np.isnan(a[0])
    assert np.isclose(a[1], 0.02) and np.isclose(a[2], 0.02)
    # zero dollar-volume day is dropped, not div-by-zero
    a2 = rolling_amihud(np.array([0.02, 0.04]), np.array([0.0, 2.0]), window=1)
    assert np.isnan(a2[0]) and np.isclose(a2[1], 0.02)


def test_sector_excess_is_equal_weight_basket_minus_market():
    mkt = np.array([0.01, 0.02])
    rets = {"A": np.array([0.03, 0.04]), "B": np.array([0.05, 0.06]),
            "C": np.array([0.07, 0.08])}
    secs = {"A": "Technology", "B": "Technology", "C": "Energy", "D": None}
    out = sector_excess_returns(rets, secs, mkt)
    assert set(out) == {"TECHNOLOGY", "ENERGY"}
    np.testing.assert_allclose(out["TECHNOLOGY"], [0.03, 0.03])  # mean(A,B)-mkt
    np.testing.assert_allclose(out["ENERGY"], [0.06, 0.06])
