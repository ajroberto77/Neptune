"""Ticker symbology translation.

Neptune stores ONE canonical ticker per instrument in the ``securities`` projection — the
``cato_securities`` form (e.g. ``BRK.B``, ``BF.B``). Each external data provider spells the
same instrument differently, and class/preferred shares are where they diverge:

    canonical (cato)   Yahoo (yfinance)   Bloomberg     FactSet
    BRK.B              BRK-B              BRK/B US      BRK.B-US
    BF.B               BF-B               BF/B US       BF.B-US

This module maps the canonical ticker to a provider's symbol on the way OUT to that feed.
Prices are stored against ``instrument_id`` (not the symbol), so translation only affects the
outbound request — internal lookups keep using the canonical ticker. New providers register a
translator here; unknown providers pass the ticker through unchanged.
"""
from __future__ import annotations

from typing import Callable


def to_yahoo(ticker: str) -> str:
    """Yahoo Finance: class shares use a dash, not a dot (``BRK.B`` -> ``BRK-B``)."""
    return ticker.replace(".", "-")


def to_bloomberg(ticker: str) -> str:
    """Bloomberg: class shares use a slash, plus an exchange suffix (``BRK.B`` -> ``BRK/B US``)."""
    return f"{ticker.replace('.', '/')} US"


def to_factset(ticker: str) -> str:
    """FactSet: dotted form with a region suffix (``BRK.B`` -> ``BRK.B-US``)."""
    return f"{ticker}-US"


# Keyed by a provider's ``source`` tag (matches PriceProvider.source).
_TRANSLATORS: dict[str, Callable[[str], str]] = {
    "yfinance": to_yahoo,
    "bloomberg": to_bloomberg,
    "factset": to_factset,
}


def to_provider_symbol(ticker: str, source: str) -> str:
    """Canonical ticker -> ``source``'s symbol. Unknown sources pass through unchanged."""
    translator = _TRANSLATORS.get(source)
    return translator(ticker) if translator else ticker
