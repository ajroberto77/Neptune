"""Deterministic synthetic data and the golden-portfolio loader."""
from __future__ import annotations

import json
from datetime import date
from importlib import resources

import numpy as np

from neptune.domain.models import LotEntry, Position, Side, ShortType
from neptune.pnl import CostBasisMethod
from neptune.quant.optimizer import Candidate

# Lots open at the synthetic base price (100.0) on this date, so the seeded book shows
# real P&L when marked against the current synthetic close.
GOLDEN_LOT_DATE = date(2026, 1, 2)
GOLDEN_BASE_PRICE = 100.0


def load_golden_portfolio() -> dict:
    """Load the committed golden portfolio fixture as a dict."""
    text = resources.files("neptune.data").joinpath("golden_portfolio.json").read_text()
    return json.loads(text)


GOLDEN_PORTFOLIO = load_golden_portfolio()


def golden_positions() -> list[Position]:
    """Domain positions for the golden portfolio."""
    out: list[Position] = []
    for p in GOLDEN_PORTFOLIO["positions"]:
        side = Side(p["side"])
        short_type = ShortType(p.get("short_type", "NA")) if side is Side.SHORT else ShortType.NA
        # One opening lot at the base price; quantity implied by notional.
        quantity = p["notional"] / GOLDEN_BASE_PRICE
        out.append(
            Position(
                ticker=p["ticker"],
                side=side,
                notional=p["notional"],
                short_type=short_type,
                forward_beta=p.get("forward_beta"),
                cost_basis_method=CostBasisMethod.FIFO,
                lots=[LotEntry(quantity=quantity, entry_price=GOLDEN_BASE_PRICE,
                               entry_date=GOLDEN_LOT_DATE)],
                thesis=p.get("thesis"),
            )
        )
    return out


def golden_candidates() -> list[Candidate]:
    """Shortable universe for the golden portfolio (zero factor loadings)."""
    return [Candidate(ticker=u["ticker"], beta=u["beta"]) for u in GOLDEN_PORTFOLIO["universe"]]


# --- Synthetic returns for the beta-pipeline tests -------------------------------

def make_market_returns(n: int = 300, seed: int = 7, vol: float = 0.01) -> np.ndarray:
    """A reproducible synthetic market return series."""
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, vol, size=n)


def make_stock_returns(
    market: np.ndarray,
    beta_true: float,
    noise_std: float = 0.0,
    seed: int = 11,
) -> np.ndarray:
    """Synthetic stock returns r = beta_true * market (+ optional seeded noise).

    With noise_std=0 the EWMA+Dimson regression recovers beta_true exactly and the
    estimation variance is ~0 (Vasicek weight w ~ 1). With noise_std>0 the estimation
    variance is strictly positive, so Vasicek shrinkage is genuinely exercised.
    """
    market = np.asarray(market, dtype=float)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, noise_std, size=market.shape[0]) if noise_std > 0 else 0.0
    return beta_true * market + noise
