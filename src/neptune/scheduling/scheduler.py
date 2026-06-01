"""The always-on price-refresh scheduler.

A single in-process APScheduler ``BackgroundScheduler`` runs the price-refresh job on an
interval (minutes, persisted in app settings; 0 = off). APScheduler is imported lazily and
optionally: if it isn't installed the scheduler quietly disables itself (the dashboard's
manual "Refresh now" and client polling still work). The interval is reschedulable at runtime
via ``reschedule`` (called by the PUT /settings/price-refresh endpoint).
"""
from __future__ import annotations

import logging

from neptune.config import settings
from neptune.db.base import SessionLocal
from neptune.scheduling.prices import refresh_tracked_prices
from neptune.securities.providers import YFinanceProvider
from neptune.settings_store.app_settings import AppSettingsService

log = logging.getLogger(__name__)

_JOB_ID = "price_refresh"
_scheduler = None  # the singleton BackgroundScheduler (None until started / if unavailable)


def _run_refresh() -> None:
    """Job body: refresh all tracked prices. Swallows errors (logged) so one bad run never
    kills the scheduler thread."""
    try:
        with SessionLocal() as session:
            result = refresh_tracked_prices(
                session, benchmark=settings.benchmark, provider=YFinanceProvider()
            )
        log.info("scheduled price refresh: %s", result)
    except Exception:  # noqa: BLE001 — keep the scheduler alive across failures
        log.exception("scheduled price refresh failed")


def current_interval() -> int:
    with SessionLocal() as session:
        return AppSettingsService(session).get_price_refresh_minutes()


def start_scheduler() -> object | None:
    """Start the background scheduler and apply the persisted interval. No-op-returns None if
    APScheduler isn't installed."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.warning("apscheduler not installed; server-side price refresh disabled")
        return None
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.start()
    reschedule(current_interval())
    return _scheduler


def reschedule(minutes: int) -> None:
    """(Re)install the refresh job at ``minutes`` (0 = remove it). No-op if not started."""
    if _scheduler is None:
        return
    if _scheduler.get_job(_JOB_ID):
        _scheduler.remove_job(_JOB_ID)
    if minutes and minutes > 0:
        _scheduler.add_job(
            _run_refresh, "interval", minutes=minutes, id=_JOB_ID, replace_existing=True
        )
        log.info("price refresh scheduled every %d min", minutes)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
