"""
Status and history API routes.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_auth_csrf
from app.models import AlertLog, CheckType, Provider, ProviderStatus, StatusLog, get_db
from app.services.maintenance_service import get_maintenance_source, is_maintenance_active
from app.time_utils import serialize_datetime, utc_now

router = APIRouter(prefix="/api/status", tags=["status"])


class StatusSummary(BaseModel):
    total: int
    online: int
    offline: int
    offline_providers: List[dict]


class StatusLogResponse(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    provider_ip_address: str
    group_name: Optional[str]
    status: str
    response_time: Optional[int]
    timestamp: str


class AlertLogResponse(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    provider_ip_address: str
    group_name: Optional[str]
    status_change: str
    message: Optional[str]
    sent_at: str


class HistoryResponse(BaseModel):
    logs: List[StatusLogResponse]
    total: int
    page: int
    per_page: int


def _serialize_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 2)


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    """Strip incoming text values and convert empty strings to None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def build_provider_filters(
    provider_id: Optional[int],
    search: Optional[str],
    group: Optional[str]
) -> List:
    """Build reusable provider-based SQLAlchemy filters."""
    filters = []
    if provider_id:
        filters.append(Provider.id == provider_id)

    normalized_search = normalize_optional_text(search)
    if normalized_search:
        search_filter = f"%{normalized_search}%"
        filters.append(
            or_(
                Provider.name.ilike(search_filter),
                Provider.ip_address.ilike(search_filter),
            )
        )

    normalized_group = normalize_optional_text(group)
    if normalized_group:
        filters.append(func.lower(Provider.group_name) == normalized_group.lower())

    return filters


def build_uptime_buckets(now: datetime) -> tuple[list[datetime], list[str], list[datetime], list[str]]:
    """Build hourly and daily bucket boundaries for uptime charts."""
    hour_anchor = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    hourly_starts = [hour_anchor + timedelta(hours=index) for index in range(24)]
    hourly_labels = [bucket.strftime("%H:%M") for bucket in hourly_starts]

    day_anchor = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
    daily_starts = [day_anchor + timedelta(days=index) for index in range(7)]
    daily_labels = [bucket.strftime("%d.%m") for bucket in daily_starts]

    return hourly_starts, hourly_labels, daily_starts, daily_labels


def calculate_ratio(statuses: list[str]) -> Optional[float]:
    """Calculate uptime ratio from a list of statuses."""
    if not statuses:
        return None
    online_count = sum(1 for item in statuses if item == ProviderStatus.ONLINE.value)
    return online_count / len(statuses)


def serialize_provider_snapshot(provider: Provider) -> dict:
    """Serialize a provider for current status and overview payloads."""
    maintenance_active = is_maintenance_active(provider)
    return {
        "id": provider.id,
        "name": provider.name,
        "ip_address": provider.ip_address,
        "group_name": provider.group_name,
        "status": provider.current_status.value,
        "check_type": getattr(provider.check_type, "value", provider.check_type or CheckType.AUTO.value),
        "check_port": provider.check_port,
        "check_path": provider.check_path,
        "dns_expected_value": provider.dns_expected_value,
        "maintenance_mode": bool(provider.maintenance_mode),
        "maintenance_active": maintenance_active,
        "maintenance_source": get_maintenance_source(provider),
        "maintenance_note": provider.maintenance_note,
        "maintenance_window_start": serialize_datetime(provider.maintenance_window_start),
        "maintenance_window_end": serialize_datetime(provider.maintenance_window_end),
        "offline_since": serialize_datetime(provider.offline_since),
        "fail_count": provider.fail_count,
        "response_time": provider.response_time,
        "last_checked": serialize_datetime(provider.last_checked),
        "last_check_method": provider.last_check_method,
        "last_error": provider.last_error,
    }


def build_summary_payload(providers: list[Provider]) -> dict:
    """Build summary counters from a provider snapshot."""
    total = len(providers)
    offline_providers = [provider for provider in providers if provider.current_status == ProviderStatus.OFFLINE]
    online = total - len(offline_providers)
    return {
        "total": total,
        "online": online,
        "offline": len(offline_providers),
        "offline_providers": [
            {
                "id": provider.id,
                "name": provider.name,
                "ip_address": provider.ip_address,
                "group_name": provider.group_name,
                "fail_count": provider.fail_count,
                "maintenance_mode": bool(provider.maintenance_mode),
                "maintenance_active": is_maintenance_active(provider),
                "maintenance_source": get_maintenance_source(provider),
                "maintenance_note": provider.maintenance_note,
                "maintenance_window_start": serialize_datetime(provider.maintenance_window_start),
                "maintenance_window_end": serialize_datetime(provider.maintenance_window_end),
            }
            for provider in offline_providers
        ],
    }


def build_uptime_payload(providers: list[Provider], logs: list, now: datetime) -> dict:
    """Build uptime percentages and mini timelines for each provider."""
    hourly_starts, hourly_labels, daily_starts, daily_labels = build_uptime_buckets(now)
    start_24h = now - timedelta(hours=24)

    provider_logs_24h: dict[int, list[str]] = defaultdict(list)
    provider_logs_7d: dict[int, list[str]] = defaultdict(list)
    hourly_buckets: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    daily_buckets: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    hour_start = hourly_starts[0]
    day_start = daily_starts[0]

    for provider_id, status_value, timestamp in logs:
        serialized_status = status_value.value if hasattr(status_value, "value") else str(status_value)
        provider_logs_7d[provider_id].append(serialized_status)
        if timestamp >= start_24h:
            provider_logs_24h[provider_id].append(serialized_status)

        hourly_index = int((timestamp - hour_start).total_seconds() // 3600)
        if 0 <= hourly_index < 24:
            hourly_buckets[provider_id][hourly_index].append(serialized_status)

        daily_index = (timestamp.date() - day_start.date()).days
        if 0 <= daily_index < 7:
            daily_buckets[provider_id][daily_index].append(serialized_status)

    serialized_providers = []
    for provider in providers:
        hourly_ratios = [calculate_ratio(hourly_buckets[provider.id].get(index, [])) for index in range(24)]
        daily_ratios = [calculate_ratio(daily_buckets[provider.id].get(index, [])) for index in range(7)]
        serialized_providers.append(
            {
                "id": provider.id,
                "name": provider.name,
                "ip_address": provider.ip_address,
                "group_name": provider.group_name,
                "status": provider.current_status.value,
                "maintenance_mode": bool(provider.maintenance_mode),
                "maintenance_active": is_maintenance_active(provider),
                "maintenance_source": get_maintenance_source(provider),
                "uptime_24h": _serialize_percent(
                    calculate_ratio(provider_logs_24h.get(provider.id, [])) * 100
                    if provider_logs_24h.get(provider.id)
                    else None
                ),
                "uptime_7d": _serialize_percent(
                    calculate_ratio(provider_logs_7d.get(provider.id, [])) * 100
                    if provider_logs_7d.get(provider.id)
                    else None
                ),
                "timeline_24h": [None if ratio is None else round(ratio * 100, 2) for ratio in hourly_ratios],
                "timeline_7d": [None if ratio is None else round(ratio * 100, 2) for ratio in daily_ratios],
            }
        )

    return {
        "generated_at": serialize_datetime(now),
        "hourly_labels": hourly_labels,
        "daily_labels": daily_labels,
        "providers": serialized_providers,
    }


@router.get("/summary", response_model=StatusSummary)
async def get_status_summary(
    session: AsyncSession = Depends(get_db)
):
    """Get overall status summary."""
    result = await session.execute(select(Provider).order_by(Provider.name.asc()))
    providers = list(result.scalars().all())
    return build_summary_payload(providers)


@router.get("/current")
async def get_current_status(
    session: AsyncSession = Depends(get_db)
):
    """Get current status of all providers."""
    result = await session.execute(select(Provider).order_by(Provider.name.asc()))
    providers = result.scalars().all()

    return {
        "providers": [serialize_provider_snapshot(provider) for provider in providers]
    }


@router.get("/uptime")
async def get_uptime_overview(
    session: AsyncSession = Depends(get_db)
):
    """Return uptime percentages and mini timelines for each provider."""
    now = utc_now()
    start_7d = now - timedelta(days=7)

    provider_result = await session.execute(select(Provider).order_by(Provider.name.asc()))
    providers = list(provider_result.scalars().all())
    if not providers:
        return {
            "generated_at": serialize_datetime(now),
            "hourly_labels": build_uptime_buckets(now)[1],
            "daily_labels": build_uptime_buckets(now)[3],
            "providers": [],
        }

    logs_result = await session.execute(
        select(StatusLog.provider_id, StatusLog.status, StatusLog.timestamp)
        .where(StatusLog.timestamp >= start_7d)
        .order_by(StatusLog.timestamp.asc())
    )
    logs = logs_result.all()
    return build_uptime_payload(providers, logs, now)


@router.get("/overview")
async def get_dashboard_overview(
    session: AsyncSession = Depends(get_db)
):
    """Return dashboard data in a single response for faster auto-refresh."""
    now = utc_now()
    start_7d = now - timedelta(days=7)

    provider_result = await session.execute(select(Provider).order_by(Provider.name.asc()))
    providers = list(provider_result.scalars().all())

    logs_result = await session.execute(
        select(StatusLog.provider_id, StatusLog.status, StatusLog.timestamp)
        .where(StatusLog.timestamp >= start_7d)
        .order_by(StatusLog.timestamp.asc())
    )
    logs = logs_result.all()

    return {
        "generated_at": serialize_datetime(now),
        "summary": build_summary_payload(providers),
        "current": {
            "providers": [serialize_provider_snapshot(provider) for provider in providers]
        },
        "uptime": build_uptime_payload(providers, logs, now),
    }


@router.get("/history", response_model=HistoryResponse)
async def get_status_history(
    provider_id: Optional[int] = Query(None, description="Filter by provider ID"),
    search: Optional[str] = Query(None, description="Search by provider name or IP"),
    group: Optional[str] = Query(None, description="Filter by group"),
    start_date: Optional[datetime] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="End date (ISO format)"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=1000, description="Items per page"),
    session: AsyncSession = Depends(get_db)
):
    """Get status history logs with pagination and provider filters."""
    filters = build_provider_filters(provider_id, search, group)
    if start_date:
        filters.append(StatusLog.timestamp >= start_date)
    if end_date:
        filters.append(StatusLog.timestamp <= end_date)

    base_query = (
        select(
            StatusLog,
            Provider.name.label("provider_name"),
            Provider.ip_address.label("provider_ip_address"),
            Provider.group_name.label("group_name"),
        )
        .join(Provider)
        .where(*filters)
    )

    count_query = (
        select(func.count(StatusLog.id))
        .select_from(StatusLog)
        .join(Provider)
        .where(*filters)
    )

    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    result = await session.execute(
        base_query
        .order_by(desc(StatusLog.timestamp))
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = result.all()

    logs = [
        {
            "id": row.StatusLog.id,
            "provider_id": row.StatusLog.provider_id,
            "provider_name": row.provider_name,
            "provider_ip_address": row.provider_ip_address,
            "group_name": row.group_name,
            "status": row.StatusLog.status.value,
            "response_time": row.StatusLog.response_time,
            "timestamp": serialize_datetime(row.StatusLog.timestamp),
        }
        for row in rows
    ]

    return {
        "logs": logs,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/alerts")
async def get_alert_history(
    provider_id: Optional[int] = Query(None, description="Filter by provider ID"),
    search: Optional[str] = Query(None, description="Search by provider name or IP"),
    group: Optional[str] = Query(None, description="Filter by group"),
    limit: int = Query(100, ge=1, le=1000, description="Number of alerts to return"),
    session: AsyncSession = Depends(get_db)
):
    """Get alert history with the same provider filters as the logs."""
    filters = build_provider_filters(provider_id, search, group)
    query = (
        select(
            AlertLog,
            Provider.name.label("provider_name"),
            Provider.ip_address.label("provider_ip_address"),
            Provider.group_name.label("group_name"),
        )
        .join(Provider)
        .where(*filters)
        .order_by(desc(AlertLog.sent_at))
        .limit(limit)
    )

    result = await session.execute(query)
    rows = result.all()

    return {
        "alerts": [
            {
                "id": row.AlertLog.id,
                "provider_id": row.AlertLog.provider_id,
                "provider_name": row.provider_name,
                "provider_ip_address": row.provider_ip_address,
                "group_name": row.group_name,
                "status_change": row.AlertLog.status_change,
                "message": row.AlertLog.message,
                "sent_at": serialize_datetime(row.AlertLog.sent_at),
            }
            for row in rows
        ]
    }


@router.delete("/history")
async def clear_history(
    provider_id: Optional[int] = Query(None, description="Filter by provider ID"),
    search: Optional[str] = Query(None, description="Search by provider name or IP"),
    group: Optional[str] = Query(None, description="Filter by group"),
    include_logs: bool = Query(True, description="Delete status logs"),
    include_alerts: bool = Query(True, description="Delete alert history"),
    session: AsyncSession = Depends(get_db),
    current_user=Depends(require_auth_csrf),
):
    """Delete history rows using the same filters as the history page."""
    if not include_logs and not include_alerts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose at least one history type to delete",
        )

    filters = build_provider_filters(provider_id, search, group)
    deleted_logs = 0
    deleted_alerts = 0

    if filters:
        provider_ids_subquery = select(Provider.id).where(*filters)
        if include_logs:
            logs_result = await session.execute(
                delete(StatusLog).where(StatusLog.provider_id.in_(provider_ids_subquery))
            )
            deleted_logs = logs_result.rowcount or 0
        if include_alerts:
            alerts_result = await session.execute(
                delete(AlertLog).where(AlertLog.provider_id.in_(provider_ids_subquery))
            )
            deleted_alerts = alerts_result.rowcount or 0
    else:
        if include_logs:
            logs_result = await session.execute(delete(StatusLog))
            deleted_logs = logs_result.rowcount or 0
        if include_alerts:
            alerts_result = await session.execute(delete(AlertLog))
            deleted_alerts = alerts_result.rowcount or 0

    await session.commit()
    return {
        "deleted_logs": deleted_logs,
        "deleted_alerts": deleted_alerts,
        "filtered": bool(filters),
    }


@router.get("/provider/{provider_id}/stats")
async def get_provider_stats(
    provider_id: int,
    days: int = Query(7, ge=1, le=90, description="Number of days for statistics"),
    session: AsyncSession = Depends(get_db)
):
    """Get statistics for a specific provider."""
    result = await session.execute(select(Provider).where(Provider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found"
        )

    end_date = utc_now()
    start_date = end_date - timedelta(days=days)

    result = await session.execute(
        select(
            func.count(StatusLog.id).label("total_checks"),
            func.sum(case((StatusLog.status == ProviderStatus.ONLINE, 1), else_=0)).label("online_count"),
            func.avg(StatusLog.response_time).label("avg_response_time"),
            func.min(StatusLog.response_time).label("min_response_time"),
            func.max(StatusLog.response_time).label("max_response_time"),
        )
        .where(StatusLog.provider_id == provider_id)
        .where(StatusLog.timestamp >= start_date)
    )
    stats = result.one()

    total_checks = stats.total_checks or 0
    online_count = stats.online_count or 0
    uptime_percentage = (online_count / total_checks * 100) if total_checks > 0 else 0

    result = await session.execute(
        select(AlertLog)
        .where(AlertLog.provider_id == provider_id)
        .order_by(desc(AlertLog.sent_at))
        .limit(10)
    )
    recent_alerts = result.scalars().all()

    return {
        "provider": {
            "id": provider.id,
            "name": provider.name,
            "ip_address": provider.ip_address,
        },
        "period_days": days,
        "statistics": {
            "total_checks": total_checks,
            "online_count": online_count,
            "offline_count": total_checks - online_count,
            "uptime_percentage": round(uptime_percentage, 2),
            "avg_response_time": round(stats.avg_response_time, 2) if stats.avg_response_time else None,
            "min_response_time": stats.min_response_time,
            "max_response_time": stats.max_response_time,
        },
        "recent_alerts": [
            {
                "id": alert.id,
                "status_change": alert.status_change,
                "sent_at": serialize_datetime(alert.sent_at),
            }
            for alert in recent_alerts
        ],
    }
