"""Persisted runtime app settings (key→value rows in the portfolio DB).

Currently the price-refresh interval (minutes) for the always-on scheduler. The stored value
overrides ``config.settings.price_refresh_minutes`` (the env/default); absent → the default.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from neptune.config import settings
from neptune.db.models import AppSettingORM

_PRICE_REFRESH_KEY = "price_refresh_minutes"


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
