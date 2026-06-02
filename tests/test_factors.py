"""Factor decomposition tests on synthetic factor-driven returns."""
from __future__ import annotations

import numpy as np
import pytest

from neptune.quant.factors import (
    factor_loadings,
    portfolio_factor_exposure,
    rolling_factor_loadings,
)


def _factor_panel(n=200, seed=21):
    rng = np.random.default_rng(seed)
    return {
        "MKT": rng.normal(0, 0.01, n),
        "SMB": rng.normal(0, 0.008, n),
        "HML": rng.normal(0, 0.008, n),
        "MOM": rng.normal(0, 0.009, n),
    }


def test_loadings_recovered_from_synthetic_returns():
    factors = _factor_panel()
    true = {"MKT": 1.1, "SMB": 0.30, "HML": -0.20, "MOM": 0.15}
    asset = sum(true[f] * factors[f] for f in factors)  # exact linear combination

    result = factor_loadings(asset, factors, window=200)

    for f, expected in true.items():
        assert result.loadings[f] == pytest.approx(expected, abs=1e-8)


def test_rolling_factor_loadings_match_single_window_fit():
    """The vectorized rolling loadings must equal factor_loadings on the trailing window at each
    index, so the materialized loading series is identical to computing on the fly."""
    rng = np.random.default_rng(9)
    n, window = 300, 60
    factors = {
        "MKT": rng.normal(0, 0.01, n), "SMB": rng.normal(0, 0.008, n),
        "HML": rng.normal(0, 0.008, n), "MOM": rng.normal(0, 0.009, n),
    }
    asset = (1.1 * factors["MKT"] + 0.3 * factors["SMB"] - 0.2 * factors["HML"]
             + 0.15 * factors["MOM"] + rng.normal(0, 0.003, n))
    roll = rolling_factor_loadings(asset, factors, window=window)

    assert np.isnan(roll.loadings["MKT"][2])  # < params obs → NaN
    for i in (80, 150, 299):  # full-window indices match the single-window multivariate fit
        ref = factor_loadings(asset[: i + 1], {f: s[: i + 1] for f, s in factors.items()},
                              window=window)
        for f in factors:
            assert roll.loadings[f][i] == pytest.approx(ref.loadings[f], abs=1e-6)
        assert int(roll.n_obs[i]) == window


def test_portfolio_exposure_is_notional_weighted():
    # A long with loading +0.4 and an equal short with loading +0.4 net to zero.
    long_aum = 1_000_000.0
    positions = [
        (long_aum, {"SMB": 0.40}),          # long
        (-long_aum, {"SMB": 0.40}),         # short of equal notional
    ]
    exposure = portfolio_factor_exposure(positions, long_aum)
    assert exposure["SMB"] == pytest.approx(0.0, abs=1e-12)


def test_short_reduces_positive_exposure():
    long_aum = 1_000_000.0
    positions = [(long_aum, {"HML": 0.50}), (-400_000.0, {"HML": 0.50})]
    exposure = portfolio_factor_exposure(positions, long_aum)
    # (1.0 - 0.4) * 0.5 = 0.30
    assert exposure["HML"] == pytest.approx(0.30, abs=1e-12)
