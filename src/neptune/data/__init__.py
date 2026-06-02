"""Sample data for the vertical slice.

Deterministic synthetic prices/returns and a committed golden portfolio. No network
access (roadmap: synthetic prices are sufficient for engine/UI development).
"""
from neptune.data.fixtures import (
    GOLDEN_PORTFOLIO,
    golden_candidates,
    golden_positions,
    load_golden_portfolio,
    make_market_returns,
    make_stock_returns,
)

__all__ = [
    "GOLDEN_PORTFOLIO",
    "golden_candidates",
    "golden_positions",
    "load_golden_portfolio",
    "make_market_returns",
    "make_stock_returns",
]
