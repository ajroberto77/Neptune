"""Database-backed market data — the read side that closes the ingest loop.

Reads stored prices from ``neptune_securities`` and presents them through the same
``MarketData`` interface ``SyntheticMarketData`` implements, so the beta pipeline, factor
regression, and P&L engine run on *real* prices with no engine changes.

Design:

* **Returns** use ``adj_close`` (split/dividend-adjusted total return); **marks**
  (``current_price`` / ``prev_close``) use raw ``close`` — lots were traded at actual
  prices, so they're valued against actual prices, not adjusted ones.
* **Alignment.** The benchmark's trading dates are the canonical index. A per-ticker series
  starts at that name's first real bar (the leading region before it has data is dropped, NOT
  back-filled — fabricating zero-return days there would collapse the name's beta) and runs to
  the latest session, forward-filling interior gaps. The result is a contiguous tail of the
  benchmark index, so ``align`` (returns.py) tail-matches it to the market and they stay
  date-aligned for the regression — the name is simply regressed over its own real window.
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
        promoted_factors: tuple[str, ...] | None = None,
    ):
        self.session = session
        self.benchmark = benchmark
        self.lookback = lookback
        self.source = source
        # Monitor factors PROMOTED into the neutralized model (default from settings). When
        # non-empty, factor_returns() includes them so the loadings regression + optimizer pick
        # them up; empty → the optimizer's factor set is exactly FF5+MOM (no behavior change).
        if promoted_factors is None:
            from neptune.config import settings as _settings
            promoted_factors = _settings.promoted
        self.promoted_factors = tuple(promoted_factors)
        self._series_cache: dict[str, list[tuple[date, float, float]]] = {}

        bench = self._series(benchmark)
        if len(bench) < 2:
            raise TickerNotFound(
                f"benchmark {benchmark!r} needs >=2 prices to form a return series; "
                f"found {len(bench)} (ingest prices first)"
            )
        # Betas/factor regressions use COMPLETED daily closes only: exclude any bar dated today
        # (the live, still-forming session). A beta as-of day T is computed from closes strictly
        # before T, so an intraday price refresh (which rewrites today's bar) never moves a beta
        # — it's stable within the day and only steps when a new close lands. Marks
        # (current_price / prev_close) still read the latest RAW bar, so day P&L reflects the
        # live price. Falls back to all bars if excluding today would leave too little history.
        today = date.today()
        dates = [ts for ts, _adj, _close in bench if ts < today]
        if len(dates) < 2:
            dates = [ts for ts, _adj, _close in bench]
        self._dates = dates
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
        """A ticker's adj_close over the canonical (benchmark) date index, starting at the
        name's FIRST real bar — interior missing sessions are forward-filled, but the leading
        region before the name has any data is DROPPED, not back-filled.

        Back-filling a leading gap with the first price would manufacture a long prefix of
        zero-return days (the price is held constant), and regressing those fabricated zeros
        against a moving market collapses the beta toward 0 *and* shrinks its estimation
        variance, so Vasicek treats the wrong number as precise. A name with a short or gappy
        history (e.g. one ingested later than the benchmark) must be regressed over its own
        real window only. The series is still a contiguous tail of the benchmark index, so it
        stays date-aligned with the market once ``align`` tail-matches them (returns.py)."""
        adj_by_day = {ts: adj for ts, adj, _close in self._series(ticker)}
        out: list[float] = []
        last: float | None = None
        for d in self._dates:
            if d in adj_by_day:
                last = adj_by_day[d]
            if last is not None:  # skip the leading region before the name's first real bar
                out.append(last)
        if not out:
            raise TickerNotFound(f"ticker {ticker!r} has no prices in the window")
        return np.array(out, dtype=float)

    def _adj_on(self, ticker: str, d: date) -> float:
        for ts, adj, _close in self._series(ticker):
            if ts == d:
                return adj
        raise KeyError(d)

    # --- MarketData interface -----------------------------------------------------

    def market_returns(self) -> np.ndarray:
        return self._market

    def return_dates(self) -> list[date]:
        """The trading dates aligned to ``market_returns()`` — ``return[i]`` is the move INTO
        ``return_dates()[i]``. The x-axis for the walk-forward beta history."""
        return list(self._dates[1:])

    def factor_returns(self) -> dict[str, np.ndarray]:
        """``{MKT, SMB, HML, MOM}`` once the Ken French panel is ingested; ``{MKT}`` only
        until then. MKT is the SPY benchmark; the style factors are read from
        ``factor_returns`` and aligned to the same return dates as the market series (a
        missing session is a 0 return — these are returns, not levels)."""
        factors: dict[str, np.ndarray] = {"MKT": self._market}
        style = self._style_factors()
        if style is not None:
            factors.update(style)
        # PROMOTED monitor factors join the neutralized model. Pre-basket dates (NaN in the raw
        # neptune series) are filled with 0.0 — a long-short factor with no basket yet earns no
        # return — so the cumsum-based rolling loadings regression isn't NaN-poisoned. WARM-UP
        # caveat: until a freshly-promoted factor has >= the loadings/cov window of REAL history,
        # that window still overlaps the zero-filled region, so its loadings and its diagonal in F
        # are damped toward 0. This never breaks neutrality (the hard factor constraint still
        # binds) — it only weakens the min-variance SHAPING for that factor until history accrues.
        # (A future refinement: gate promotion on sufficient real observations.)
        if self.promoted_factors:
            promoted = self.neptune_factor_returns()
            for f in self.promoted_factors:
                if f in promoted:
                    factors[f] = np.nan_to_num(promoted[f], nan=0.0)
        return factors

    def neptune_factor_returns(self) -> dict[str, np.ndarray]:
        """The price-only ``neptune``-sourced single factors (IVOL/BAB/AMIHUD) aligned to the
        market return dates, NaN on dates with no stored value (these series only start once the
        factor's baskets first form). Kept SEPARATE from ``factor_returns()`` on purpose: the
        PM monitors these but the optimizer does NOT neutralize them, so they must never enter
        the regression that produces the optimizer's loadings. Empty until built."""
        from neptune.quant.factor_build import NEPTUNE_FACTORS

        if len(self._dates) < 2:
            return {}
        target = self._dates[1:]
        lo, hi = target[0], target[-1]
        rows = self.session.execute(
            select(FactorReturn.factor, FactorReturn.ts, FactorReturn.ret).where(
                FactorReturn.factor.in_(NEPTUNE_FACTORS),
                FactorReturn.source == "neptune",
                FactorReturn.ts >= lo, FactorReturn.ts <= hi,
            )
        ).all()
        by_factor: dict[str, dict[date, float]] = {}
        for factor, ts, ret in rows:
            by_factor.setdefault(factor, {})[ts] = ret
        return {
            f: np.array([by_factor[f].get(d, np.nan) for d in target], dtype=float)
            for f in NEPTUNE_FACTORS if by_factor.get(f)
        }

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

    def aligned_returns(self, ticker: str) -> np.ndarray:
        """Returns over the FULL benchmark return-date axis (``len(return_dates())``), NaN before
        the name's first real bar; interior missing sessions forward-fill the price (zero return).
        Unlike ``ticker_returns`` (a contiguous tail), this keeps every name on the SAME axis so
        the factor builder can stack names into one ``(N, T)`` matrix."""
        adj_by_day = {ts: adj for ts, adj, _close in self._series(ticker)}
        last: float | None = None
        adj: list[float] = []
        for d in self._dates:
            if d in adj_by_day:
                last = adj_by_day[d]
            adj.append(last if last is not None else np.nan)
        arr = np.array(adj, dtype=float)
        with np.errstate(invalid="ignore"):
            return arr[1:] / arr[:-1] - 1.0  # NaN propagates before the first bar

    def aligned_dollar_volume(self, ticker: str) -> np.ndarray:
        """Daily dollar volume (close × volume) on the return-date axis — ``[i]`` is the volume on
        ``return_dates()[i]`` (the day the return is realized). NaN where volume isn't stored.
        (Unlike ``aligned_returns``, interior gaps are NOT forward-filled — a missing-volume day is
        NaN and simply drops out of the trailing Amihud mean, rather than being imputed.)"""
        iid = self.session.scalar(
            select(Security.instrument_id).where(Security.ticker == ticker)
        )
        dv_by_day: dict[date, float] = {}
        if iid is not None:
            for ts, close, vol in self.session.execute(
                select(Price.ts, Price.close, Price.volume).where(Price.instrument_id == iid)
            ).all():
                if close is not None and vol is not None:
                    dv_by_day[ts] = float(close) * float(vol)
        return np.array([dv_by_day.get(d, np.nan) for d in self._dates[1:]], dtype=float)

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

    def average_dollar_volume(self, ticker: str, window: int = 63) -> float | None:
        """Trailing average daily DOLLAR volume (close × volume) over the last ``window`` bars —
        the liquidity proxy for the short-universe screen. None when volume isn't stored (so the
        caller treats it as unknown rather than illiquid)."""
        series = self._series(ticker)
        if not series:
            return None
        iid = self.session.scalar(
            select(Security.instrument_id).where(Security.ticker == ticker)
        )
        if iid is None:
            return None
        rows = self.session.execute(
            select(Price.close, Price.volume)
            .where(Price.instrument_id == iid, Price.volume.isnot(None))
            .order_by(Price.ts.desc()).limit(window)
        ).all()
        if not rows:
            return None
        dollar = [float(c) * float(v) for c, v in rows if c is not None and v is not None]
        return float(np.mean(dollar)) if dollar else None
