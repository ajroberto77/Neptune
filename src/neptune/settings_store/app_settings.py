"""Persisted runtime app settings (key→value rows in the portfolio DB).

Currently: the price-refresh interval (minutes) for the always-on scheduler, and which
classification scheme drives the sector-concentration cap. Each stored value overrides its
``config.settings`` default; absent → the default.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from neptune.config import settings
from neptune.db.models import AppSettingORM

_PRICE_REFRESH_KEY = "price_refresh_minutes"
_SECTOR_SOURCE_KEY = "sector_source"

# Which classification schemes can drive the hedge optimizer's sector-concentration cap.
# YAHOO is Neptune's own long-standing source and stays the default — now primarily
# projected from cato_securities' own Yahoo-tier classification (securities/models.py's
# Security.sector), with securities/ingest.py's own yfinance fetch as a fallback for names
# CATO hasn't covered. SIC/KENFRENCH_12 are read-only imports from cato_securities'
# entity_classifications (see neptune.universe), selectable but never auto-switched to.
SECTOR_SOURCES = ("YAHOO", "SIC", "KENFRENCH_12")
DEFAULT_SECTOR_SOURCE = "YAHOO"


class AppSettingsService:
    def __init__(self, session: Session):
        self.session = session

    def get_price_refresh_minutes(self) -> int:
        """Persisted interval if set, else the configured default. 0 = scheduler off."""
        row = self.session.get(AppSettingORM, _PRICE_REFRESH_KEY)
        if row is None:
            return settings.price_refresh_minutes
        try:
            return int(row.value)
        except ValueError:
            return settings.price_refresh_minutes

    def set_price_refresh_minutes(self, minutes: int) -> int:
        """Persist the interval (clamped to ≥ 0). Returns the stored value."""
        minutes = max(0, int(minutes))
        row = self.session.get(AppSettingORM, _PRICE_REFRESH_KEY)
        if row is None:
            self.session.add(AppSettingORM(key=_PRICE_REFRESH_KEY, value=str(minutes)))
        else:
            row.value = str(minutes)
        self.session.commit()
        return minutes

    def get_sector_source(self) -> str:
        """Which classification scheme resolves Security.sector for the hedge optimizer's
        cap and the sector panels. Persisted if set and still valid, else the default."""
        row = self.session.get(AppSettingORM, _SECTOR_SOURCE_KEY)
        if row is None or row.value not in SECTOR_SOURCES:
            return DEFAULT_SECTOR_SOURCE
        return row.value

    def set_sector_source(self, scheme: str) -> str:
        """Persist which scheme drives the sector cap. Returns the stored value."""
        if scheme not in SECTOR_SOURCES:
            raise ValueError(f"unknown sector source {scheme!r} — must be one of {SECTOR_SOURCES}")
        row = self.session.get(AppSettingORM, _SECTOR_SOURCE_KEY)
        if row is None:
            self.session.add(AppSettingORM(key=_SECTOR_SOURCE_KEY, value=scheme))
        else:
            row.value = scheme
        self.session.commit()
        return scheme
