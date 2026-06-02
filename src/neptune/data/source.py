"""The market-data interface the Quant Engine / Risk Interface consume.

Both ``SyntheticMarketData`` (generated) and ``DbMarketData`` (read from stored prices)
satisfy this Protocol, so the engine is agnostic to where returns come from — swapping one
for the other is a constructor change, no engine edits (CLAUDE.md §1: the engine is pure
functions over arrays).

**The array contract.** Every return series is date-aligned, equal-length, and newest-last
— exactly what ``raw_beta_ewma_dimson(returns, market)`` and ``factor_loadings(returns,
factors)`` rely on (a stock's returns and the market/factor series must line up index for
index). Implementations own that alignment; the engine just consumes the arrays.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np


class MarketData(Protocol):
    """Returns + marks the engine needs. ``market_returns`` is the benchmark (SPY) series;
    ``factor_returns`` maps factor name → series (its keys define the factor model);
    ``ticker_returns`` is a single name aligned to the market index. ``current_price`` /
    ``prev_close`` are the raw (unadjusted) marks the P&L engine values lots against."""

    def market_returns(self) -> np.ndarray:
        ...

    def factor_returns(self) -> dict[str, np.ndarray]:
        ...

    def ticker_returns(self, ticker: str) -> np.ndarray:
        ...

    def current_price(self, ticker: str) -> float:
        ...

    def prev_close(self, ticker: str) -> float:
        ...

    def mark_date(self, ticker: str):
        """The trading date the current mark belongs to — anchors day P&L to the data's latest
        session rather than the wall clock (so a same-day trade is detected across timezones)."""
        ...
