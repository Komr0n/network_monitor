"""
Helpers for consistent UTC storage and display-time serialization.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo
from typing import Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import APP_TIMEZONE

logger = logging.getLogger(__name__)

UTC = timezone.utc


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime for DB compatibility."""
    return datetime.now(UTC).replace(tzinfo=None)


def ensure_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and return an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def serialize_datetime(value: Optional[datetime]) -> Optional[str]:
    """Serialize datetimes as explicit UTC ISO-8601 strings."""
    if value is None:
        return None
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def _resolve_display_timezone() -> tzinfo:
    configured_timezone = (APP_TIMEZONE or "").strip()
    if configured_timezone:
        try:
            return ZoneInfo(configured_timezone)
        except ZoneInfoNotFoundError:
            logger.warning("APP_TIMEZONE=%s is invalid, falling back to the system timezone", configured_timezone)

    return datetime.now().astimezone().tzinfo or UTC


DISPLAY_TIMEZONE = _resolve_display_timezone()


def to_display_timezone(value: datetime) -> datetime:
    """Convert a UTC-stored datetime into the configured display timezone."""
    return ensure_utc(value).astimezone(DISPLAY_TIMEZONE)


def format_display_datetime(value: Optional[datetime], include_zone: bool = True) -> str:
    """Format a datetime for human-readable reports."""
    if value is None:
        return "-"

    localized = to_display_timezone(value)
    formatted = localized.strftime("%Y-%m-%d %H:%M:%S")
    if not include_zone:
        return formatted

    zone_label = (APP_TIMEZONE or localized.tzname() or "UTC").strip()
    return f"{formatted} {zone_label}"


def format_duration_human(total_seconds: Optional[int]) -> str:
    """Format a duration in a short human-readable Russian form."""
    if total_seconds is None or total_seconds <= 0:
        return "меньше секунды"

    remaining = int(total_seconds)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if seconds and len(parts) < 2:
        parts.append(f"{seconds} сек")

    return " ".join(parts[:3]) or "меньше секунды"
