"""``DbMarketData`` — the ``MarketData`` implementation backed by stored prices.

Reads the ``adj_close`` series ingested into ``neptune_securities.prices`` and turns them
into the date-aligned, newest-last return arrays the beta/factor engine consumes. It is a
drop-in for ``SyntheticMarketData`` (same Protocol), so swapping the engine onto real data
is a constructor change — no engine edits (CLAUDE.md §1).

Design choices, all deliberate:

* **The market (SPY) defines the date axis.** ``market_returns()`` is computed from the
  market ticker's adjusted closes over the trailing ``lookback`` window; every
  ``ticker_returns()`` is aligned to *that same* axis so a stock and the market line up
  index-for-index (the engine's hard requirement). Names with gaps are forward-filled on
  the axis (a missing day → 0 return), which is a documented first-cut policy; the precise
  fix is trading-calendar alignment, tracked as a follow-up.
* **Total-return inputs, raw marks.** Returns use ``adj_close`` (split/dividend adjusted —
  correct for beta). ``current_price`` / ``prev_close`` use the raw unadjusted ``close``,
  because the P&L engine values real lots against the actual traded price.
* **One source per series.** Facts are append-only and source-tagged, so a ``(security,
  day)`` can exist from several sources; for a clean return series we pick exactly one
  (``source``, default ``"yfinance"``; else the lexicographically-first available).
* **Factors are not in price data.** ``factor_returns()`` needs the Ken French panel
  (SMB/HML/MOM), a separate ingest. Until that exists you inject a panel; absent one,
  ``factor_returns()`` raises ``FactorDataUnavailable`` rather than silently returning
  zeros (which would hide factor risk).
"""
from __future__ import annotations

from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from neptune.config import settings
from neptune.securities.models import Price, Security


class NoPriceData(LookupError):
    """Raised when a ticker has no (or too little) stored price history to form returns."""


class FactorDataUnavailable(NotImplementedError):
    """Raised when factor returns are requested but no factor panel has been provided
    (the Ken French ingest isn't wired yet)."""


class DbMarketData:
    """Market data read from the securities database.

    ``factors`` is an optional pre-aligned factor panel (name → return array matching the
    market axis length); when omitted, ``factor_returns`` raises ``FactorDataUnavailable``.
    """

    def __init__(
        self,
        session: Session,
        *,
        market_ticker: str = "SPY",
        lookback: int = settings.beta_lookback,
        source: str = "yfinance",
        factors: dict[str, np.ndarray] | None = None,
    ):
        self.session = session
        self.market_ticker = market_ticker
        self.lookback = lookback
        self.source = source
        self._factors = factors
        # The market series fixes the shared date axis used by every ticker.
        market_prices = self._adj_prices(market_ticker)
        if len(market_prices) < 2:
            raise NoPriceData(
                f"market ticker {market_ticker!r} has < 2 stored prices — backfill first"
            )
        # Bound to the trailing window: lookback+1 closes yield `lookback` returns.
        self._axis = sorted(market_prices)[-(self.lookback + 1):]
        self._market = self._returns_on_axis(market_prices)

    # --- MarketData protocol ------------------------------------------------------

    def market_returns(self) -> np.ndarray:
        return self._market

    def factor_returns(self) -> dict[str, np.ndarray]:
        if self._factors is None:
            raise FactorDataUnavailable(
                "no factor panel provided — Ken French (SMB/HML/MOM) ingest not wired yet"
            )
        return self._factors

    def ticker_returns(self, ticker: str) -> np.ndarray:
        return self._returns_on_axis(self._adj_prices(ticker))

    def current_price(self, ticker: str) -> float:
        """Latest raw (unadjusted) close — the mark the P&L engine values lots against."""
        return self._raw_closes(ticker)[-1]

    def prev_close(self, ticker: str) -> float:
        closes = self._raw_closes(ticker)
        if len(closes) < 2:
            raise NoPriceData(f"{ticker!r} has < 2 closes for a prior mark")
        return closes[-2]

    # --- internals ----------------------------------------------------------------

    def _rows(self, ticker: str) -> list[Price]:
        """All price rows for ``ticker`` from the chosen source, ascending by date."""
        sec = self.session.scalar(select(Security).where(Security.ticker == ticker))
        if sec is None:
            raise NoPriceData(f"ticker {ticker!r} not in the securities projection")
        rows = self.session.scalars(
            select(Price).where(Price.instrument_id == sec.instrument_id)
        ).all()
        if not rows:
            raise NoPriceData(f"no stored prices for {ticker!r} — backfill first")
        sources = {r.source for r in rows}
        src = self.source if self.source in sources else sorted(sources)[0]
        return sorted((r for r in rows if r.source == src), key=lambda r: r.ts)

    def _adj_prices(self, ticker: str) -> dict[date, float]:
        """``{ts: adj_close}`` (falling back to raw close if adjusted is missing)."""
        return {
            r.ts: (r.adj_close if r.adj_close is not None else r.close)
            for r in self._rows(ticker)
        }

    def _raw_closes(self, ticker: str) -> list[float]:
        return [r.close for r in self._rows(ticker)]

    def _returns_on_axis(self, prices: dict[date, float]) -> np.ndarray:
        """Align ``prices`` to the market date axis (forward-fill gaps, back-fill any
        leading gap with the first known price) and return simple returns, newest-last."""
        aligned: list[float | None] = []
        last: float | None = None
        for d in self._axis:
            if d in prices:
                last = prices[d]
            aligned.append(last)
        if aligned and aligned[0] is None:
            first_known = next((v for v in aligned if v is not None), None)
            if first_known is None:
                raise NoPriceData("no overlapping prices with the market axis")
            aligned = [v if v is not None else first_known for v in aligned]
        arr = np.asarray(aligned, dtype=float)
        return np.diff(arr) / arr[:-1]
