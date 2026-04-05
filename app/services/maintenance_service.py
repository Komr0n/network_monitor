"""
Helpers for manual and scheduled maintenance windows.
"""
from __future__ import annotations

from datetime import datetime

from app.models import Provider
from app.time_utils import ensure_utc, utc_now


def normalize_utc_naive(value: datetime | None) -> datetime | None:
    """Convert an incoming datetime into naive UTC for database storage."""
    if value is None:
        return None
    return ensure_utc(value).replace(tzinfo=None)


def is_scheduled_maintenance_active(provider: Provider, moment: datetime | None = None) -> bool:
    """Return True when the current time falls within the configured maintenance window."""
    now = moment or utc_now()
    start = getattr(provider, "maintenance_window_start", None)
    end = getattr(provider, "maintenance_window_end", None)

    if start and end:
        return start <= now <= end
    if start and not end:
        return now >= start
    if end and not start:
        return now <= end
    return False


def is_maintenance_active(provider: Provider, moment: datetime | None = None) -> bool:
    """Return True when maintenance is enabled manually or by schedule."""
    return bool(getattr(provider, "maintenance_mode", False)) or is_scheduled_maintenance_active(provider, moment)


def get_maintenance_source(provider: Provider, moment: datetime | None = None) -> str | None:
    """Return the active maintenance source."""
    if bool(getattr(provider, "maintenance_mode", False)):
        return "manual"
    if is_scheduled_maintenance_active(provider, moment):
        return "scheduled"
    return None


def has_maintenance_window(provider: Provider) -> bool:
    """Return True when a maintenance schedule is configured."""
    return bool(getattr(provider, "maintenance_window_start", None) or getattr(provider, "maintenance_window_end", None))
