"""
Export API routes.
"""
import csv
import io
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_auth
from app.models import Provider, StatusLog, get_db
from app.time_utils import serialize_datetime, utc_now

router = APIRouter(prefix="/api/export", tags=["export"])


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    """Strip incoming text values and convert empty strings to None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def build_history_query(
    provider_id: Optional[int],
    search: Optional[str],
    group: Optional[str],
    start_date: datetime,
    end_date: datetime,
):
    """Build a reusable history export query."""
    query = (
        select(StatusLog, Provider)
        .join(Provider)
        .where(StatusLog.timestamp >= start_date)
        .where(StatusLog.timestamp <= end_date)
    )

    if provider_id:
        query = query.where(StatusLog.provider_id == provider_id)

    normalized_search = normalize_optional_text(search)
    if normalized_search:
        search_filter = f"%{normalized_search}%"
        query = query.where(
            or_(
                Provider.name.ilike(search_filter),
                Provider.ip_address.ilike(search_filter),
            )
        )

    normalized_group = normalize_optional_text(group)
    if normalized_group:
        query = query.where(func.lower(Provider.group_name) == normalized_group.lower())

    return query.order_by(asc(StatusLog.timestamp))


@router.get("/csv")
async def export_csv(
    provider_id: Optional[int] = Query(None, description="Filter by provider ID"),
    search: Optional[str] = Query(None, description="Search by provider name or IP"),
    group: Optional[str] = Query(None, description="Filter by group"),
    start_date: Optional[datetime] = Query(None, description="Start date"),
    end_date: Optional[datetime] = Query(None, description="End date"),
    session: AsyncSession = Depends(get_db)
):
    """
    Export status logs to CSV.

    Returns a CSV file with columns:
    - timestamp
    - provider_name
    - ip_address
    - group_name
    - status
    - response_time_ms
    """
    if not end_date:
        end_date = utc_now()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    result = await session.execute(
        build_history_query(provider_id, search, group, start_date, end_date)
    )
    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No data to export")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp",
        "provider_name",
        "ip_address",
        "group_name",
        "status",
        "response_time_ms",
    ])

    for status_log, provider in rows:
        writer.writerow([
            serialize_datetime(status_log.timestamp) or "",
            provider.name,
            provider.ip_address,
            provider.group_name or "",
            status_log.status.value,
            status_log.response_time or "",
        ])

    output.seek(0)
    filename = f"network_monitor_export_{utc_now().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/providers/csv")
async def export_providers_csv(
    session: AsyncSession = Depends(get_db),
    current_user=Depends(require_auth)
):
    """
    Export providers list to CSV.

    Returns a CSV file with all provider information.
    """
    result = await session.execute(select(Provider).order_by(asc(Provider.name)))
    providers = result.scalars().all()

    if not providers:
        raise HTTPException(status_code=404, detail="No providers to export")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id",
        "name",
        "ip_address",
        "description",
        "group_name",
        "current_status",
        "check_type",
        "check_port",
        "check_path",
        "dns_expected_value",
        "maintenance_mode",
        "maintenance_note",
        "maintenance_window_start",
        "maintenance_window_end",
        "offline_since",
        "fail_count",
        "last_checked",
        "response_time_ms",
        "last_check_method",
        "last_error",
        "created_at",
    ])

    for provider in providers:
        writer.writerow([
            provider.id,
            provider.name,
            provider.ip_address,
            provider.description or "",
            provider.group_name or "",
            provider.current_status.value,
            getattr(provider.check_type, "value", provider.check_type or ""),
            provider.check_port or "",
            provider.check_path or "",
            provider.dns_expected_value or "",
            bool(provider.maintenance_mode),
            provider.maintenance_note or "",
            serialize_datetime(provider.maintenance_window_start) or "",
            serialize_datetime(provider.maintenance_window_end) or "",
            serialize_datetime(provider.offline_since) or "",
            provider.fail_count,
            serialize_datetime(provider.last_checked) or "",
            provider.response_time or "",
            provider.last_check_method or "",
            provider.last_error or "",
            serialize_datetime(provider.created_at) or "",
        ])

    output.seek(0)
    filename = f"network_monitor_providers_{utc_now().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
