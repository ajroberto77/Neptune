"""Provider factory functions — single construction point for all external-data providers.

Both the FastAPI Depends layer and the background scheduler import these factories, so the
concrete implementation choice lives in one place. When a provider is swapped (e.g. yfinance
→ Bloomberg B-PIPE), only the relevant factory changes — no handler bodies are touched.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from neptune.macro.providers import FredProvider, MacroProvider
from neptune.securities.factor_providers import FactorProvider, KenFrenchProvider
from neptune.securities.providers import PriceProvider, YFinanceProvider
from neptune.settings_store.credentials import CredentialsService


def build_price_provider(session: Session) -> PriceProvider:
    # Phase 2+: read CredentialsService(session) to select Bloomberg vs yfinance.
    return YFinanceProvider()


def build_factor_provider() -> FactorProvider:
    return KenFrenchProvider()


def build_macro_provider(session: Session) -> MacroProvider:
    key = CredentialsService(session).resolve_key("FRED")
    if not key:
        raise RuntimeError(
            "no FRED API key configured — set one in Settings → Data provider API keys"
        )
    return FredProvider(key)
