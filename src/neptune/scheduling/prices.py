"""The price-refresh job: re-pull recent prices for every tracked ticker + the benchmark.

Pure-ish and testable — takes an open portfolio session and a provider, opens its own
securities session, and re-ingests a short recent window (so today's live bar is updated).
The scheduler wraps this; the per-portfolio API endpoint does the same for one book.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from neptune.db.models import PositionORM
from neptune.db.runtime import securities_session
from neptune.securities.ingest import ingest_ticker
from neptune.securities.providers import PriceProvider


def tracked_tickers(portfolio_session: Session, benchmark: str | None) -> list[str]:
    """Distinct tickers held across all portfolios, plus the benchmark."""
    tickers = set(portfolio_session.scalars(select(PositionORM.ticker).distinct()).all())
    if benchmark:
        tickers.add(benchmark)
    return sorted(tickers)


def refresh_tracked_prices(
    portfolio_session: Session,
    *,
    benchmark: str | None,
    provider: PriceProvider,
    days: int = 7,
) -> dict:
    """Re-ingest a recent window for every tracked ticker. Per-ticker feed errors are
    collected (non-fatal); a provider-unavailable ``RuntimeError`` propagates to the caller."""
    tickers = tracked_tickers(portfolio_session, benchmark)
    if not tickers:
        return {"tickers": 0, "updated_bars": 0, "errors": []}
    end = date.today()
    start = end - timedelta(days=days)
    updated, errors = 0, []
    with securities_session(portfolio_session) as sec_session:
        for ticker in tickers:
            try:
                res = ingest_ticker(
                    sec_session, provider, ticker, start, end, create_if_missing=True
                )
            except RuntimeError:  # provider not installed — global, surface to caller
                raise
            except Exception:  # noqa: BLE001 — per-ticker feed error, non-fatal
                errors.append(ticker)
            else:
                updated += res.prices
    return {"tickers": len(tickers), "updated_bars": updated, "errors": errors}
