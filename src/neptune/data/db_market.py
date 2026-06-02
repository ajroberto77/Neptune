"""Database-backed market data — the read side that closes the ingest loop.

Reads stored prices from ``neptune_securities`` and presents them through the same
``MarketData`` interface ``SyntheticMarketData`` implements, so the beta pipeline, factor
regression, and P&L engine run on *real* prices with no engine changes.

Design:

* **Returns** use ``adj_close`` (split/dividend-adjusted total return); **marks**
  (``current_price`` / ``prev_close``) use raw ``close`` — lots were traded at actual
  prices, so they're valued against actual prices, not adjusted ones.
* **Alignment.** The benchmark's trading dates are the canonical index. Every series —
  market and per-ticker — is computed over that one index (a ticker missing a session is
  forward-filled, so its return that day is 0), guaranteeing the equal-length, date-aligned
  arrays the regression contract requires.
* **Factors.** ``factor_returns`` currently returns only ``{"MKT": market}`` — real SMB/
  HML/MOM await the Ken French ingestion (roadmap: "Ken French factor loading"). So this
  source drives real *betas* today; factor decomposition stays on the synthetic source
  until the factor panel is loaded.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from neptune.securities.models import FactorReturn, Price, Security

# The style factors Neptune sources from Ken French (MKT comes from the SPY benchmark).
# ALL must be present for the panel to be considered loaded — a partial factor model is worse
# than none, so otherwise we fall back to MKT-only.
from neptune.quant.factors import STYLE_FACTORS as _STYLE_FACTORS  # FF5 + Momentum


class TickerNotFound(LookupError):
    """Raised when a ticker has no projected security or no prices in the window."""


def _simple_returns(prices: np.ndarray) -> np.ndarray:
    """Period-over-period simple returns; length is ``len(prices) - 1``, newest last."""
    if prices.size < 2:
        return np.array([], dtype=float)
    return np.diff(prices) / prices[:-1]


class DbMarketData:
    """``MarketData`` backed by stored prices.

    ``benchmark`` is the market proxy (SPY by default). ``lookback`` optionally caps the
    window to the most recent N returns (None = all stored history). ``source`` optionally
    restricts to a single price source; when None and a ``(security, day)`` has rows from
    several sources, the last by source name wins (deterministic).
    """

    def __init__(
        self,
        session: Session,
        benchmark: str = "SPY",
        lookback: int | None = None,
        source: str | None = None,
    ):
        self.session = session
        self.benchmark = benchmark
        self.lookback = lookback
        self.source = source
        self._series_cache: dict[str, list[tuple[date, float, float]]] = {}

        bench = self._series(benchmark)
        if len(bench) < 2:
            raise TickerNotFound(
                f"benchmark {benchmark!r} needs >=2 prices to form a return series; "
                f"found {len(bench)} (ingest prices first)"
            )
        self._dates = [ts for ts, _adj, _close in bench]
        if lookback is not None:
            self._dates = self._dates[-(lookback + 1):]
        self._market = _simple_returns(
            np.array([self._adj_on(benchmark, d) for d in self._dates], dtype=float)
        )

    # --- loading ------------------------------------------------------------------

    def _series(self, ticker: str) -> list[tuple[date, float, float]]:
        """Sorted ``[(ts, adj_close, close)]`` for a ticker, deduped per day. Cached."""
        if ticker in self._series_cache:
            return self._series_cache[ticker]
        instrument_id = self.session.scalar(
            select(Security.instrument_id).where(Security.ticker == ticker)
        )
        if instrument_id is None:
            raise TickerNotFound(f"ticker {ticker!r} not in securities projection")
        q = select(Price.ts, Price.adj_close, Price.close).where(
            Price.instrument_id == instrument_id
        )
        if self.source is not None:
            q = q.where(Price.source == self.source)
        # Order by (ts, source) so that, with multiple sources, the last write per day is
        # deterministic. adj_close falls back to raw close if a source left it null.
        q = q.order_by(Price.ts, Price.source)
        by_day: dict[date, tuple[float, float]] = {}
        for ts, adj_close, close in self.session.execute(q).all():
            by_day[ts] = (adj_close if adj_close is not None else close, close)
        series = [(ts, adj, close) for ts, (adj, close) in sorted(by_day.items())]
        self._series_cache[ticker] = series
        return series

    def _adj_aligned(self, ticker: str) -> np.ndarray:
        """A ticker's adj_close aligned to the canonical (benchmark) date index, with
        forward-fill for missing sessions and back-fill of any leading gap."""
        adj_by_day = {ts: adj for ts, adj, _close in self._series(ticker)}
        out: list[float | None] = []
        last: float | None = None
        for d in self._dates:
            if d in adj_by_day:
                last = adj_by_day[d]
            out.append(last)
        first = next((x for x in out if x is not None), None)
        if first is None:
            raise TickerNotFound(f"ticker {ticker!r} has no prices in the window")
        return np.array([x if x is not None else first for x in out], dtype=float)

    def _adj_on(self, ticker: str, d: date) -> float:
        for ts, adj, _close in self._series(ticker):
            if ts == d:
                return adj
        raise KeyError(d)

    # --- MarketData interface -----------------------------------------------------

    def market_returns(self) -> np.ndarray:
        return self._market

    def factor_returns(self) -> dict[str, np.ndarray]:
        """``{MKT, SMB, HML, MOM}`` once the Ken French panel is ingested; ``{MKT}`` only
        until then. MKT is the SPY benchmark; the style factors are read from
        ``factor_returns`` and aligned to the same return dates as the market series (a
        missing session is a 0 return — these are returns, not levels)."""
        factors: dict[str, np.ndarray] = {"MKT": self._market}
        style = self._style_factors()
        if style is not None:
            factors.update(style)
        return factors

    def _style_factors(self) -> dict[str, np.ndarray] | None:
        """Load SMB/HML/MOM aligned to the market return dates, or None if the panel is
        not fully present in the window (then the caller keeps MKT-only)."""
        if len(self._dates) < 2:
            return None
        target_dates = self._dates[1:]  # market_returns[i] is the move into dates[i+1]
        lo, hi = target_dates[0], target_dates[-1]
        rows = self.session.execute(
            select(FactorReturn.factor, FactorReturn.ts, FactorReturn.ret).where(
                FactorReturn.factor.in_(_STYLE_FACTORS),
                FactorReturn.ts >= lo,
                FactorReturn.ts <= hi,
            )
        ).all()
        by_factor: dict[str, dict[date, float]] = {f: {} for f in _STYLE_FACTORS}
        for factor, ts, ret in rows:
            by_factor[factor][ts] = ret
        if not all(by_factor[f] for f in _STYLE_FACTORS):
            return None  # incomplete panel → MKT-only fallback
        return {
            f: np.array([by_factor[f].get(d, 0.0) for d in target_dates], dtype=float)
            for f in _STYLE_FACTORS
        }

    def ticker_returns(self, ticker: str) -> np.ndarray:
        return _simple_returns(self._adj_aligned(ticker))

    def current_price(self, ticker: str) -> float:
        """Latest raw close (the mark)."""
        series = self._series(ticker)
        if not series:
            raise TickerNotFound(f"ticker {ticker!r} has no prices")
        return float(series[-1][2])

    def prev_close(self, ticker: str) -> float:
        """Prior raw close (for day P&L); equals current when only one bar exists."""
        series = self._series(ticker)
        if not series:
            raise TickerNotFound(f"ticker {ticker!r} has no prices")
        return float(series[-2][2] if len(series) >= 2 else series[-1][2])

    def mark_date(self, ticker: str) -> date:
        """The trading date of the current mark (the latest stored bar). Anchors day P&L to the
        data, not the wall clock: a lot opened on or after this date is same-session."""
        series = self._series(ticker)
        if not series:
            raise TickerNotFound(f"ticker {ticker!r} has no prices")
        return series[-1][0]

    # --- Universe enumeration (for the real shortable universe) --------------------

    def available_tickers(self, min_bars: int = 30) -> list[str]:
        """Backfilled tickers with at least ``min_bars`` real price bars, excluding the
        benchmark — the candidate set for the shortable universe. The bar-count floor matters
        because short series get forward-filled to the benchmark index, so a 3-bar name would
        otherwise pass through as a (meaningless, near-zero-beta) candidate."""
        from sqlalchemy import func

        q = (
            select(Security.ticker)
            .join(Price, Price.instrument_id == Security.instrument_id)
            .where(Security.ticker.isnot(None), Security.ticker != self.benchmark)
        )
        if self.source is not None:
            q = q.where(Price.source == self.source)
        rows = self.session.execute(
            q.group_by(Security.ticker).having(func.count(Price.ts) >= min_bars)
        ).all()
        return sorted(t for (t,) in rows)

    def sector(self, ticker: str) -> str | None:
        """The Security's stored sector (None until enriched), for the sector cap."""
        return self.session.scalar(
            select(Security.sector).where(Security.ticker == ticker)
        )
