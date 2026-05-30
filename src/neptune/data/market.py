"""Deterministic synthetic market data.

Provides a market (SPY-proxy) return series, a Ken-French-style factor panel
(MKT/SMB/HML/MOM), and per-ticker return histories generated from a catalog of
"true" betas and factor loadings plus seeded noise. This lets the beta pipeline and
factor regression run for real with no network access (roadmap: synthetic prices are
sufficient for engine/UI development). The estimators recover the true parameters up
to the injected noise — exactly what exercises Vasicek shrinkage.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from neptune.quant.factors import FACTORS

MARKET_VOL = 0.01
# Low idiosyncratic noise so the EWMA(lambda=0.94) estimator — which has a small
# effective sample — still recovers betas near the truth with only light Vasicek
# shrinkage. Heavy-shrinkage behavior is covered by the known-noise fixture in
# test_beta.py.
DEFAULT_NOISE = 0.0015


@dataclass(frozen=True)
class TickerSpec:
    """Generative parameters for a synthetic ticker."""

    true_beta: float
    loadings: dict[str, float] = field(default_factory=dict)  # SMB/HML/MOM
    noise: float = DEFAULT_NOISE


# Catalog covering the golden portfolio names and the shortable universe. Loadings are
# kept modest (|load| <= 0.15) so residual factor exposures stay within the +/-0.20
# limit and the optimizer remains feasible.
CATALOG: dict[str, TickerSpec] = {
    "AAA": TickerSpec(1.20, {"SMB": 0.10, "HML": -0.05, "MOM": 0.08}),
    "BBB": TickerSpec(0.90, {"SMB": -0.05, "HML": 0.10, "MOM": -0.04}),
    "CCC": TickerSpec(1.50, {"SMB": 0.12, "HML": -0.10, "MOM": 0.10}),
    "DDD": TickerSpec(1.00, {"SMB": 0.05, "HML": 0.05, "MOM": 0.00}),
    "HDG1": TickerSpec(1.10, {"SMB": 0.06, "HML": -0.04, "MOM": 0.05}),
    "HDG2": TickerSpec(0.95, {"SMB": -0.04, "HML": 0.06, "MOM": -0.03}),
    "HDG3": TickerSpec(1.25, {"SMB": 0.10, "HML": -0.08, "MOM": 0.07}),
    "HDG4": TickerSpec(0.85, {"SMB": -0.06, "HML": 0.08, "MOM": -0.05}),
    "HDG5": TickerSpec(1.05, {"SMB": 0.03, "HML": 0.02, "MOM": 0.04}),
    "HDG6": TickerSpec(1.15, {"SMB": 0.07, "HML": -0.05, "MOM": 0.06}),
    "HDG7": TickerSpec(0.90, {"SMB": -0.05, "HML": 0.07, "MOM": -0.04}),
    "HDG8": TickerSpec(1.20, {"SMB": 0.09, "HML": -0.06, "MOM": 0.08}),
}


def _seed_for(ticker: str) -> int:
    """A stable per-ticker seed derived from the ticker symbol."""
    digest = hashlib.sha256(ticker.encode()).hexdigest()
    return int(digest[:8], 16)


def default_universe_tickers(n: int = 60) -> list[str]:
    """A synthetic shortable universe of ``n`` names (U0001..). Deterministic; their
    betas/loadings come from the per-ticker generator in ``spec_for``."""
    return [f"U{i:04d}" for i in range(1, n + 1)]


class SyntheticMarketData:
    """A reproducible market + factor + per-ticker return generator."""

    def __init__(self, n: int = 300, seed: int = 20260530):
        self.n = n
        rng = np.random.default_rng(seed)
        # Market (MKT) and the orthogonal style factors, all independent.
        self._market = rng.normal(0.0, MARKET_VOL, n)
        self._factors: dict[str, np.ndarray] = {"MKT": self._market}
        for f in ("SMB", "HML", "MOM"):
            self._factors[f] = rng.normal(0.0, 0.008, n)

    def market_returns(self) -> np.ndarray:
        return self._market

    def factor_returns(self) -> dict[str, np.ndarray]:
        return {f: self._factors[f] for f in FACTORS}

    def spec_for(self, ticker: str) -> TickerSpec:
        if ticker in CATALOG:
            return CATALOG[ticker]
        # Unknown tickers: deterministic, near-market, lightly loaded.
        rng = np.random.default_rng(_seed_for(ticker))
        beta = float(np.clip(rng.normal(1.0, 0.25), 0.4, 1.8))
        loadings = {f: round(float(rng.normal(0.0, 0.06)), 4) for f in ("SMB", "HML", "MOM")}
        return TickerSpec(beta, loadings)

    def ticker_returns(self, ticker: str) -> np.ndarray:
        """Synthetic returns r = beta*MKT + sum(load_f * factor_f) + noise."""
        spec = self.spec_for(ticker)
        r = spec.true_beta * self._market
        for f, load in spec.loadings.items():
            if f in self._factors:
                r = r + load * self._factors[f]
        if spec.noise > 0:
            noise_rng = np.random.default_rng(_seed_for(ticker) ^ 0x9E3779B9)
            r = r + noise_rng.normal(0.0, spec.noise, self.n)
        return r
