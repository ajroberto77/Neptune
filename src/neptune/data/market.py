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

from neptune.quant.factors import FACTORS, STYLE_FACTORS

MARKET_VOL = 0.01
# Low idiosyncratic noise so the EWMA(lambda=0.94) estimator — which has a small
# effective sample — still recovers betas near the truth with only light Vasicek
# shrinkage. Heavy-shrinkage behavior is covered by the known-noise fixture in
# test_beta.py.
DEFAULT_NOISE = 0.0015


# GICS-style sectors used to tag synthetic names for concentration flagging.
SECTORS = (
    "Technology",
    "Financials",
    "Healthcare",
    "Energy",
    "Consumer",
    "Industrials",
)


@dataclass(frozen=True)
class TickerSpec:
    """Generative parameters for a synthetic ticker."""

    true_beta: float
    loadings: dict[str, float] = field(default_factory=dict)  # SMB/HML/MOM
    sector: str = "Technology"
    noise: float = DEFAULT_NOISE


# Catalog covering the golden portfolio names and the shortable universe. Loadings are
# kept modest (|load| <= 0.15) so residual factor exposures stay within the +/-0.20
# limit and the optimizer remains feasible.
CATALOG: dict[str, TickerSpec] = {
    "AAA": TickerSpec(1.20, {"SMB": 0.10, "HML": -0.05, "MOM": 0.08}, "Technology"),
    "BBB": TickerSpec(0.90, {"SMB": -0.05, "HML": 0.10, "MOM": -0.04}, "Financials"),
    "CCC": TickerSpec(1.50, {"SMB": 0.12, "HML": -0.10, "MOM": 0.10}, "Technology"),
    "DDD": TickerSpec(1.00, {"SMB": 0.05, "HML": 0.05, "MOM": 0.00}, "Healthcare"),
    "HDG1": TickerSpec(1.10, {"SMB": 0.06, "HML": -0.04, "MOM": 0.05}, "Technology"),
    "HDG2": TickerSpec(0.95, {"SMB": -0.04, "HML": 0.06, "MOM": -0.03}, "Financials"),
    "HDG3": TickerSpec(1.25, {"SMB": 0.10, "HML": -0.08, "MOM": 0.07}, "Technology"),
    "HDG4": TickerSpec(0.85, {"SMB": -0.06, "HML": 0.08, "MOM": -0.05}, "Healthcare"),
    "HDG5": TickerSpec(1.05, {"SMB": 0.03, "HML": 0.02, "MOM": 0.04}, "Energy"),
    "HDG6": TickerSpec(1.15, {"SMB": 0.07, "HML": -0.05, "MOM": 0.06}, "Consumer"),
    "HDG7": TickerSpec(0.90, {"SMB": -0.05, "HML": 0.07, "MOM": -0.04}, "Industrials"),
    "HDG8": TickerSpec(1.20, {"SMB": 0.09, "HML": -0.06, "MOM": 0.08}, "Technology"),
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
        for f in STYLE_FACTORS:
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
        loadings = {f: round(float(rng.normal(0.0, 0.06)), 4) for f in STYLE_FACTORS}
        sector = SECTORS[_seed_for(ticker) % len(SECTORS)]
        return TickerSpec(beta, loadings, sector)

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

    def price_series(self, ticker: str, base_price: float = 100.0) -> np.ndarray:
        """A deterministic synthetic close-price series, base_price compounded by the
        ticker's returns. Used by the P&L engine for marks."""
        returns = self.ticker_returns(ticker)
        return base_price * np.cumprod(1.0 + returns)

    def current_price(self, ticker: str) -> float:
        """The latest synthetic close (the mark)."""
        return float(self.price_series(ticker)[-1])

    def prev_close(self, ticker: str) -> float:
        """The prior synthetic close (for day P&L)."""
        return float(self.price_series(ticker)[-2])

