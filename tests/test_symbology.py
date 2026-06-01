"""Ticker symbology: canonical (cato) ticker -> each provider's symbol."""
from __future__ import annotations

from neptune.securities.providers import RecordedProvider
from neptune.securities.symbology import to_provider_symbol


def test_yahoo_class_shares_use_a_dash():
    assert to_provider_symbol("BRK.B", "yfinance") == "BRK-B"
    assert to_provider_symbol("BF.B", "yfinance") == "BF-B"
    assert to_provider_symbol("AAPL", "yfinance") == "AAPL"  # plain tickers untouched


def test_other_providers_have_their_own_forms():
    assert to_provider_symbol("BRK.B", "bloomberg") == "BRK/B US"
    assert to_provider_symbol("BRK.B", "factset") == "BRK.B-US"


def test_unknown_source_passes_through():
    assert to_provider_symbol("BRK.B", "recorded") == "BRK.B"
    # A RecordedProvider's source isn't a known translator, so nothing is rewritten.
    assert to_provider_symbol("BF.B", RecordedProvider({}).source) == "BF.B"
