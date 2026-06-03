"""API keys for external data providers (write-only secrets, masked on read).

Mirrors the DB-connection password discipline: the key is persisted in the portfolio DB
(``provider_credentials``) but NEVER returned by the API — callers see only whether a key is
set and where it resolves from (``stored`` UI value > ``env`` fallback > ``none``).

``resolve_key`` returns the actual key for the ingest layer to use; it must never be
serialized into a response.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from neptune.config import settings
from neptune.db.models import ProviderCredentialORM

# Known providers → the ``settings`` attribute holding the env/.env fallback key.
# ALFRED (vintages) uses the SAME key as FRED, so there is one provider here.
PROVIDERS: dict[str, str] = {
    "FRED": "fred_api_key",
}


class CredentialsService:
    def __init__(self, session: Session):
        self.session = session

    def _env_key(self, provider: str) -> str | None:
        attr = PROVIDERS.get(provider)
        return getattr(settings, attr, None) if attr else None

    def resolve_key(self, provider: str) -> str | None:
        """The effective key for the ingest layer: the UI-stored key wins, else the env/.env
        fallback. NEVER serialize this into an API response."""
        row = self.session.get(ProviderCredentialORM, provider)
        if row is not None and row.api_key:
            return row.api_key
        return self._env_key(provider)

    def set_key(self, provider: str, api_key: str | None) -> dict:
        """Store (or, with an empty string, clear) the provider key. Returns masked status."""
        if provider not in PROVIDERS:
            raise KeyError(provider)
        value = api_key or None  # "" / None both clear the stored secret
        row = self.session.get(ProviderCredentialORM, provider)
        if row is None:
            row = ProviderCredentialORM(provider=provider, api_key=value)
            self.session.add(row)
        else:
            row.api_key = value
        self.session.commit()
        return self.status(provider)

    def status(self, provider: str) -> dict:
        """Masked status — never the key. ``source`` ∈ {stored, env, none}."""
        row = self.session.get(ProviderCredentialORM, provider)
        if row is not None and row.api_key:
            source = "stored"
        elif self._env_key(provider):
            source = "env"
        else:
            source = "none"
        return {"provider": provider, "has_key": source != "none", "source": source}

    def list_status(self) -> list[dict]:
        return [self.status(p) for p in PROVIDERS]
