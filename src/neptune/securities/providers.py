"""Price-data providers — the source side of yfinance ingestion (roadmap: "yfinance
price ingestion", Market Data, Critical).

A ``PriceProvider`` fetches a security's history (OHLCV + dividends + splits) for a date
range. Two implementations:

* ``YFinanceProvider`` — the real feed. yfinance is imported lazily so the package (and
  the whole test suite) imports with no network and no hard dependency; the import only
  happens when you actually fetch.
* ``RecordedProvider`` — a fixture holding pre-canned histories, so ingestion and the
  back-adjustment are fully testable offline (this sandbox has no network).

Providers return plain DTOs; turning them into rows (and reconstructing ``adj_close`` when
a source doesn't supply it) is the ingest layer's job — keeping providers I/O-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from neptune.securities.models import CorporateActionType


@dataclass(frozen=True)
class PriceBar:
    """One daily OHLCV bar. ``adj_close`` may be None — the ingest layer reconstructs it
    from splits/dividends when a provider doesn't supply it."""

    ts: date
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    adj_close: float | None = None
    volume: float | None = None


@dataclass(frozen=True)
class DividendRecord:
    ex_date: date
    amount: float  # per-share, in currency
    pay_date: date | None = None
    currency: str = "USD"


@dataclass(frozen=True)
class CorporateActionRecord:
    action_type: CorporateActionType
    effective_date: date
    ratio: float | None = None      # split multiplier (2.0 = 2:1 forward, 0.1 = 1:10 reverse)
    old_value: str | None = None
    new_value: str | None = None


@dataclass(frozen=True)
class SecurityHistory:
    """Everything a provider returns for one security over a window."""

    prices: list[PriceBar] = field(default_factory=list)
    dividends: list[DividendRecord] = field(default_factory=list)
    corporate_actions: list[CorporateActionRecord] = field(default_factory=list)


class PriceProvider(Protocol):
    """Fetch one security's history over ``[start, end]``. ``source`` tags the rows so
    facts from different providers coexist (append-only, invariant I-07)."""

    source: str

    def fetch(self, ticker: str, start: date, end: date) -> SecurityHistory:
        ...


class RecordedProvider:
    """In-memory provider for offline dev/tests. Holds a full history per ticker and
    returns the slice inside ``[start, end]`` on fetch (so date-window logic is exercised)."""

    def __init__(self, histories: dict[str, SecurityHistory], source: str = "recorded"):
        self._histories = histories
        self.source = source

    def fetch(self, ticker: str, start: date, end: date) -> SecurityHistory:
        h = self._histories.get(ticker)
        if h is None:
            return SecurityHistory()
        return SecurityHistory(
            prices=[b for b in h.prices if start <= b.ts <= end],
            dividends=[d for d in h.dividends if start <= d.ex_date <= end],
            corporate_actions=[
                c for c in h.corporate_actions if start <= c.effective_date <= end
            ],
        )


class YFinanceProvider:
    """The real yfinance feed. ``yfinance`` is imported on first ``fetch`` only, so this
    module never requires the package at import time. US equities, daily bars."""

    source = "yfinance"

    def fetch(self, ticker: str, start: date, end: date) -> SecurityHistory:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError(
                "yfinance is not installed; `pip install yfinance` to enable live ingest"
            ) from exc

        # auto_adjust=False keeps raw Close *and* Adj Close; actions=True yields the
        # Dividends / Stock Splits columns. end is exclusive in yfinance, so +1 day.
        from datetime import timedelta

        df = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            actions=True,
        )
        prices: list[PriceBar] = []
        dividends: list[DividendRecord] = []
        actions: list[CorporateActionRecord] = []
        for idx, row in df.iterrows():
            d = idx.date()
            prices.append(
                PriceBar(
                    ts=d,
                    open=_num(row.get("Open")),
                    high=_num(row.get("High")),
                    low=_num(row.get("Low")),
                    close=_num(row.get("Close")),
                    adj_close=_num(row.get("Adj Close")),
                    volume=_num(row.get("Volume")),
                )
            )
            div = _num(row.get("Dividends"))
            if div:
                dividends.append(DividendRecord(ex_date=d, amount=div))
            split = _num(row.get("Stock Splits"))
            if split:  # yfinance reports the split multiplier directly (2.0 for 2:1)
                actions.append(
                    CorporateActionRecord(
                        action_type=CorporateActionType.SPLIT,
                        effective_date=d,
                        ratio=split,
                    )
                )
        return SecurityHistory(prices=prices, dividends=dividends, corporate_actions=actions)

    def fetch_sector(self, ticker: str) -> str | None:
        """Yahoo's sector classification (GICS-like) for ``ticker``, or None if unavailable.
        A licensed GICS feed can replace this later — the ingest just writes the column."""
        try:
            import yfinance as yf
        except ImportError:  # pragma: no cover - optional install
            return None
        try:
            return yf.Ticker(ticker).info.get("sector") or None
        except Exception:  # noqa: BLE001 - sector is best-effort enrichment, never fatal
            return None


def _num(value) -> float | None:
    """Coerce a pandas/NumPy scalar to a plain float, mapping NaN/None to None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN check without importing math
