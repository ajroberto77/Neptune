"""The always-on factor-panel refresh scheduler.

A second, independent in-process APScheduler ``BackgroundScheduler`` (kept decoupled from
the price-refresh one in ``scheduler.py``) runs the Ken French factor-panel refresh job on an
interval (minutes, persisted in app settings; 0 = off). APScheduler is imported lazily and
optionally: if it isn't installed the scheduler quietly disables itself (the dashboard's
manual "Backfill factors" and client polling still work). The interval is reschedulable at
runtime via ``reschedule`` (called by the PUT /settings/factor-refresh endpoint).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from neptune.config import settings
from neptune.db.base import SessionLocal
from neptune.db.runtime import securities_session
from neptune.providers import build_factor_provider
from neptune.scheduling.factors import refresh_factor_panel
from neptune.settings_store.app_settings import AppSettingsService

log = logging.getLogger(__name__)

_JOB_ID = "factor_refresh"
_scheduler = None  # the singleton BackgroundScheduler (None until started / if unavailable)

# Ken French always downloads the whole panel CSV and filters client-side, so a wider window
# doesn't cost more network — this just bounds how many rows get idempotently re-upserted per
# run. 30 days safely covers the publication lag without redoing a full multi-year backfill
# on every scheduled tick.
_REFRESH_WINDOW_DAYS = 30


def _run_factor_refresh() -> None:
    """Job body: refresh the factor panel + rebuild style loadings. Swallows errors (logged)
    so one bad run never kills the scheduler thread."""
    try:
        end = date.today()
        start = end - timedelta(days=_REFRESH_WINDOW_DAYS)
        with SessionLocal() as session, securities_session(session) as sec_session:
            result = refresh_factor_panel(
                sec_session,
                build_factor_provider(),
                start,
                end,
                benchmark=settings.benchmark,
            )
        log.info("scheduled factor refresh: %s", result)
    except Exception:  # noqa: BLE001 — keep the scheduler alive across failures
        log.exception("scheduled factor refresh failed")


def current_interval() -> int:
    with SessionLocal() as session:
        return AppSettingsService(session).get_factor_refresh_minutes()


def start_scheduler() -> object | None:
    """Start the background scheduler and apply the persisted interval. No-op-returns None if
    APScheduler isn't installed."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.warning("apscheduler not installed; server-side factor refresh disabled")
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
            _run_factor_refresh, "interval", minutes=minutes, id=_JOB_ID, replace_existing=True
        )
        log.info("factor refresh scheduled every %d min", minutes)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
