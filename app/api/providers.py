"""
Provider CRUD and import API routes.
"""
import csv
import io
import ipaddress
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_auth, require_auth_csrf
from app.models import CheckType, Provider, ProviderStatus, get_db
from app.services.maintenance_service import (
    get_maintenance_source,
    is_maintenance_active,
    normalize_utc_naive,
)
from app.time_utils import serialize_datetime, utc_now

router = APIRouter(prefix="/api/providers", tags=["providers"])
MAX_IMPORT_PAYLOAD_BYTES = 2 * 1024 * 1024

SORT_COLUMNS = {
    "name": Provider.name,
    "ip_address": Provider.ip_address,
    "group_name": Provider.group_name,
    "current_status": Provider.current_status,
    "fail_count": Provider.fail_count,
    "last_checked": Provider.last_checked,
    "response_time": Provider.response_time,
    "created_at": Provider.created_at,
}

IMPORT_HEADER_MAP = {
    "name": "name",
    "ip": "ip_address",
    "ip_address": "ip_address",
    "host": "ip_address",
    "hostname": "ip_address",
    "group": "group_name",
    "group_name": "group_name",
    "description": "description",
    "check_type": "check_type",
    "type": "check_type",
    "check_port": "check_port",
    "port": "check_port",
    "check_path": "check_path",
    "path": "check_path",
    "dns_expected_value": "dns_expected_value",
    "dns_value": "dns_expected_value",
    "maintenance_mode": "maintenance_mode",
    "maintenance_note": "maintenance_note",
    "maintenance_window_start": "maintenance_window_start",
    "maintenance_start": "maintenance_window_start",
    "maintenance_window_end": "maintenance_window_end",
    "maintenance_end": "maintenance_window_end",
}


class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    ip_address: str = Field(..., min_length=1, max_length=45)
    description: Optional[str] = Field(None, max_length=500)
    group_name: Optional[str] = Field(None, max_length=100)
    check_type: str = Field(default="auto", min_length=1, max_length=20)
    check_port: Optional[int] = Field(None, ge=1, le=65535)
    check_path: Optional[str] = Field(None, max_length=255)
    dns_expected_value: Optional[str] = Field(None, max_length=255)
    maintenance_mode: bool = False
    maintenance_note: Optional[str] = Field(None, max_length=255)
    maintenance_window_start: Optional[datetime] = None
    maintenance_window_end: Optional[datetime] = None


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    ip_address: Optional[str] = Field(None, min_length=1, max_length=45)
    description: Optional[str] = Field(None, max_length=500)
    group_name: Optional[str] = Field(None, max_length=100)
    check_type: Optional[str] = Field(None, min_length=1, max_length=20)
    check_port: Optional[int] = Field(None, ge=1, le=65535)
    check_path: Optional[str] = Field(None, max_length=255)
    dns_expected_value: Optional[str] = Field(None, max_length=255)
    maintenance_mode: Optional[bool] = None
    maintenance_note: Optional[str] = Field(None, max_length=255)
    maintenance_window_start: Optional[datetime] = None
    maintenance_window_end: Optional[datetime] = None


class ProviderResponse(BaseModel):
    id: int
    name: str
    ip_address: str
    description: Optional[str]
    group_name: Optional[str]
    current_status: str
    check_type: str
    check_port: Optional[int]
    check_path: Optional[str]
    dns_expected_value: Optional[str]
    maintenance_mode: bool
    maintenance_active: bool
    maintenance_source: Optional[str]
    maintenance_note: Optional[str]
    maintenance_started_at: Optional[str]
    maintenance_window_start: Optional[str]
    maintenance_window_end: Optional[str]
    offline_since: Optional[str]
    fail_count: int
    last_checked: Optional[str]
    response_time: Optional[int]
    last_check_method: Optional[str]
    last_error: Optional[str]

    class Config:
        from_attributes = True


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    """Strip incoming text values and convert empty strings to None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def normalize_check_type(value: Optional[str]) -> Optional[str]:
    """Validate a check type value."""
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None

    normalized = normalized.lower()
    allowed_values = {check_type.value for check_type in CheckType}
    if normalized not in allowed_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Недопустимый тип проверки. Используйте auto, ping, tcp, http или dns",
        )
    return normalized


def parse_optional_port(value: Optional[str], line_number: int, errors: List[str]) -> Optional[int]:
    """Parse an optional TCP/HTTP port from import data."""
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None

    try:
        port = int(normalized)
    except ValueError:
        errors.append(f"Строка {line_number}: порт '{normalized}' должен быть числом")
        return None

    if not 1 <= port <= 65535:
        errors.append(f"Строка {line_number}: порт '{normalized}' вне диапазона 1-65535")
        return None

    return port


def parse_optional_bool(value: Optional[str]) -> Optional[bool]:
    """Parse common boolean representations used in CSV imports."""
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None

    lowered = normalized.lower()
    if lowered in {"1", "true", "yes", "on", "y", "да"}:
        return True
    if lowered in {"0", "false", "no", "off", "n", "нет"}:
        return False
    return None


def parse_optional_datetime(
    value: Optional[str],
    line_number: int,
    field_name: str,
    errors: List[str],
) -> Optional[datetime]:
    """Parse optional ISO-like datetime values from CSV imports."""
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None

    candidate = normalized.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        errors.append(
            f"Строка {line_number}: {field_name} должен быть в ISO-формате, "
            "например 2026-04-05T18:00:00+05:00"
        )
        return None
    return normalize_utc_naive(parsed)


def normalize_provider_payload(payload: BaseModel) -> dict:
    """Normalize request payload fields."""
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    if "ip_address" in data and data["ip_address"] is not None:
        data["ip_address"] = data["ip_address"].strip()
    if "description" in data:
        data["description"] = normalize_optional_text(data.get("description"))
    if "group_name" in data:
        data["group_name"] = normalize_optional_text(data.get("group_name"))
    if "check_type" in data:
        data["check_type"] = normalize_check_type(data.get("check_type"))
    if "check_path" in data:
        data["check_path"] = normalize_optional_text(data.get("check_path"))
    if "dns_expected_value" in data:
        data["dns_expected_value"] = normalize_optional_text(data.get("dns_expected_value"))
    if "maintenance_note" in data:
        data["maintenance_note"] = normalize_optional_text(data.get("maintenance_note"))
    if "maintenance_window_start" in data:
        data["maintenance_window_start"] = normalize_utc_naive(data.get("maintenance_window_start"))
    if "maintenance_window_end" in data:
        data["maintenance_window_end"] = normalize_utc_naive(data.get("maintenance_window_end"))

    start = data.get("maintenance_window_start")
    end = data.get("maintenance_window_end")
    if start and end and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Начало maintenance-окна не может быть позже конца",
        )
    return data


def apply_check_defaults(data: dict) -> dict:
    """Clear irrelevant fields for the selected check type."""
    check_type = data.get("check_type")
    if not check_type:
        return data

    if check_type not in {"tcp", "http"}:
        data["check_port"] = None
    if check_type != "http":
        data["check_path"] = None
    if check_type != "dns":
        data["dns_expected_value"] = None
    return data


def serialize_provider(provider: Provider) -> dict:
    """Convert provider model into API response payload."""
    maintenance_active = is_maintenance_active(provider)
    return {
        "id": provider.id,
        "name": provider.name,
        "ip_address": provider.ip_address,
        "description": provider.description,
        "group_name": provider.group_name,
        "current_status": provider.current_status.value,
        "check_type": getattr(provider.check_type, "value", provider.check_type or CheckType.AUTO.value),
        "check_port": provider.check_port,
        "check_path": provider.check_path,
        "dns_expected_value": provider.dns_expected_value,
        "maintenance_mode": bool(provider.maintenance_mode),
        "maintenance_active": maintenance_active,
        "maintenance_source": get_maintenance_source(provider),
        "maintenance_note": provider.maintenance_note,
        "maintenance_started_at": serialize_datetime(provider.maintenance_started_at),
        "maintenance_window_start": serialize_datetime(provider.maintenance_window_start),
        "maintenance_window_end": serialize_datetime(provider.maintenance_window_end),
        "offline_since": serialize_datetime(provider.offline_since),
        "fail_count": provider.fail_count,
        "last_checked": serialize_datetime(provider.last_checked),
        "response_time": provider.response_time,
        "last_check_method": provider.last_check_method,
        "last_error": provider.last_error,
    }


async def find_provider_by_ip(
    session: AsyncSession,
    ip_address: str,
    exclude_id: Optional[int] = None
) -> Optional[Provider]:
    """Find a provider by IP/host name in a case-insensitive way."""
    query = select(Provider).where(func.lower(Provider.ip_address) == ip_address.lower())
    if exclude_id is not None:
        query = query.where(Provider.id != exclude_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


def looks_like_ip_address(value: str) -> bool:
    """Return True if the value is an IPv4/IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def decode_import_bytes(raw_bytes: bytes) -> str:
    """Decode uploaded import files using common Windows encodings."""
    for encoding in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("latin-1")


def build_import_entry(
    line_number: int,
    name: Optional[str],
    ip_address: Optional[str],
    group_name: Optional[str],
    description: Optional[str],
    check_type: Optional[str],
    check_port: Optional[str],
    check_path: Optional[str],
    dns_expected_value: Optional[str],
    maintenance_mode: Optional[str],
    maintenance_note: Optional[str],
    maintenance_window_start: Optional[str],
    maintenance_window_end: Optional[str],
    errors: List[str]
) -> Optional[dict]:
    """Validate and normalize a single import row."""
    ip_address = normalize_optional_text(ip_address)
    name = normalize_optional_text(name) or ip_address
    group_name = normalize_optional_text(group_name)
    description = normalize_optional_text(description)
    normalized_check_type = CheckType.AUTO.value

    try:
        normalized_check_type = normalize_check_type(check_type) or CheckType.AUTO.value
    except HTTPException as error:
        errors.append(f"Строка {line_number}: {error.detail}")
        return None

    parsed_port = parse_optional_port(check_port, line_number, errors)
    parsed_maintenance_mode = parse_optional_bool(maintenance_mode)
    if maintenance_mode is not None and parsed_maintenance_mode is None:
        errors.append(
            f"Строка {line_number}: maintenance_mode должен быть yes/no, true/false, 1/0 или да/нет"
        )
        return None

    parsed_window_start = parse_optional_datetime(
        maintenance_window_start,
        line_number,
        "maintenance_window_start",
        errors,
    )
    parsed_window_end = parse_optional_datetime(
        maintenance_window_end,
        line_number,
        "maintenance_window_end",
        errors,
    )
    if parsed_window_start and parsed_window_end and parsed_window_start > parsed_window_end:
        errors.append(
            f"Строка {line_number}: maintenance_window_start не может быть позже maintenance_window_end"
        )
        return None

    probe_settings = apply_check_defaults({
        "check_type": normalized_check_type,
        "check_port": parsed_port,
        "check_path": normalize_optional_text(check_path),
        "dns_expected_value": normalize_optional_text(dns_expected_value),
    })

    if not ip_address:
        errors.append(f"Строка {line_number}: не указан IP-адрес или хост")
        return None

    if not name:
        errors.append(f"Строка {line_number}: не указано имя узла")
        return None

    return {
        "line_number": line_number,
        "name": name,
        "ip_address": ip_address,
        "group_name": group_name,
        "description": description,
        "check_type": probe_settings["check_type"],
        "check_port": probe_settings["check_port"],
        "check_path": probe_settings["check_path"],
        "dns_expected_value": probe_settings["dns_expected_value"],
        "maintenance_mode": bool(parsed_maintenance_mode),
        "maintenance_note": normalize_optional_text(maintenance_note),
        "maintenance_window_start": parsed_window_start,
        "maintenance_window_end": parsed_window_end,
    }


def parse_import_rows(raw_text: str, default_group: Optional[str]) -> tuple[List[dict], List[str]]:
    """Parse provider import text or CSV content."""
    errors: List[str] = []
    rows_to_import: List[dict] = []
    default_group = normalize_optional_text(default_group)

    lines = [
        line for line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return rows_to_import, errors

    delimiter = ";" if lines[0].count(";") > lines[0].count(",") else ","
    csv_rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    if not csv_rows:
        return rows_to_import, errors

    first_row = [cell.lower() for cell in csv_rows[0]]
    has_header = any(cell in IMPORT_HEADER_MAP for cell in first_row)

    if has_header:
        mapped_headers = [IMPORT_HEADER_MAP.get(cell) for cell in first_row]
        for line_number, row in enumerate(csv_rows[1:], start=2):
            row_data = {}
            for index, value in enumerate(row):
                if index >= len(mapped_headers):
                    continue
                key = mapped_headers[index]
                if key:
                    row_data[key] = value

            entry = build_import_entry(
                line_number=line_number,
                name=row_data.get("name"),
                ip_address=row_data.get("ip_address"),
                group_name=row_data.get("group_name") or default_group,
                description=row_data.get("description"),
                check_type=row_data.get("check_type"),
                check_port=row_data.get("check_port"),
                check_path=row_data.get("check_path"),
                dns_expected_value=row_data.get("dns_expected_value"),
                maintenance_mode=row_data.get("maintenance_mode"),
                maintenance_note=row_data.get("maintenance_note"),
                maintenance_window_start=row_data.get("maintenance_window_start"),
                maintenance_window_end=row_data.get("maintenance_window_end"),
                errors=errors,
            )
            if entry:
                rows_to_import.append(entry)
        return rows_to_import, errors

    for line_number, row in enumerate(csv_rows, start=1):
        if len(row) == 1:
            name = row[0]
            ip_address = row[0]
            group_name = default_group
            description = None
        else:
            first = row[0]
            second = row[1]
            if looks_like_ip_address(first) and not looks_like_ip_address(second):
                ip_address = first
                name = second
            else:
                name = first
                ip_address = second
            group_name = row[2] if len(row) > 2 else default_group
            description = row[3] if len(row) > 3 else None

        entry = build_import_entry(
            line_number=line_number,
            name=name,
            ip_address=ip_address,
            group_name=group_name,
            description=description,
            check_type=None,
            check_port=None,
            check_path=None,
            dns_expected_value=None,
            maintenance_mode=None,
            maintenance_note=None,
            maintenance_window_start=None,
            maintenance_window_end=None,
            errors=errors,
        )
        if entry:
            rows_to_import.append(entry)

    return rows_to_import, errors


@router.post("/import")
async def import_providers(
    import_text: str = Form(""),
    default_group: Optional[str] = Form(None),
    import_file: Optional[UploadFile] = File(None),
    session: AsyncSession = Depends(get_db),
    current_user=Depends(require_auth_csrf)
):
    """Bulk import providers from pasted text or a CSV/TXT file."""
    payload_parts: List[str] = []
    payload_size = 0

    normalized_text = normalize_optional_text(import_text)
    if normalized_text:
        payload_size += len(normalized_text.encode("utf-8"))
        if payload_size > MAX_IMPORT_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Import text is too large",
            )
        payload_parts.append(normalized_text)

    if import_file and import_file.filename:
        file_content = await import_file.read()
        if file_content:
            payload_size += len(file_content)
            if payload_size > MAX_IMPORT_PAYLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Import payload exceeds 2 MB",
                )
            payload_parts.append(decode_import_bytes(file_content))

    if not payload_parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Передайте текст импорта или загрузите CSV/TXT файл"
        )

    import_rows, errors = parse_import_rows("\n".join(payload_parts), default_group)
    if not import_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Не найдено ни одной корректной строки для импорта",
                "errors": errors,
            }
        )

    existing_result = await session.execute(select(Provider.ip_address))
    known_ips = {row[0].lower() for row in existing_result.all() if row[0]}

    created = 0
    skipped = 0
    imported_ips = set()

    for row in import_rows:
        ip_key = row["ip_address"].lower()
        if ip_key in known_ips or ip_key in imported_ips:
            skipped += 1
            errors.append(
                f"Строка {row['line_number']}: адрес '{row['ip_address']}' уже существует и был пропущен"
            )
            continue

        provider = Provider(
            name=row["name"],
            ip_address=row["ip_address"],
            description=row["description"],
            group_name=row["group_name"],
            check_type=CheckType(row["check_type"] or CheckType.AUTO.value),
            check_port=row["check_port"],
            check_path=row["check_path"],
            dns_expected_value=row["dns_expected_value"],
            maintenance_mode=1 if row["maintenance_mode"] else 0,
            maintenance_note=row["maintenance_note"],
            maintenance_started_at=utc_now() if row["maintenance_mode"] else None,
            maintenance_window_start=row["maintenance_window_start"],
            maintenance_window_end=row["maintenance_window_end"],
            current_status=ProviderStatus.ONLINE,
            fail_count=0,
        )
        session.add(provider)
        imported_ips.add(ip_key)
        created += 1

    if created:
        await session.commit()

    return {
        "created": created,
        "skipped": skipped,
        "total_received": len(import_rows),
        "errors": errors,
    }


@router.get("", response_model=List[ProviderResponse])
async def list_providers(
    search: Optional[str] = Query(None, description="Search by name or IP"),
    group: Optional[str] = Query(None, description="Filter by group"),
    provider_status: Optional[str] = Query(None, alias="status", description="Filter by status (online/offline)"),
    sort_by: str = Query("name", description="Sort field"),
    sort_order: str = Query("asc", description="Sort order (asc/desc)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_db)
):
    """List all providers with optional filtering and sorting."""
    query = select(Provider)

    normalized_search = normalize_optional_text(search)
    normalized_group = normalize_optional_text(group)
    if normalized_search:
        search_filter = f"%{normalized_search}%"
        query = query.where(
            or_(
                Provider.name.ilike(search_filter),
                Provider.ip_address.ilike(search_filter),
            )
        )

    if normalized_group:
        query = query.where(func.lower(Provider.group_name) == normalized_group.lower())

    if provider_status:
        normalized_status = provider_status.strip().lower()
        if normalized_status not in {"online", "offline"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Недопустимое значение статуса"
            )
        status_enum = ProviderStatus.ONLINE if normalized_status == "online" else ProviderStatus.OFFLINE
        query = query.where(Provider.current_status == status_enum)

    sort_column = SORT_COLUMNS.get(sort_by, Provider.name)
    if sort_order.lower() == "desc":
        query = query.order_by(desc(sort_column), asc(Provider.name))
    else:
        query = query.order_by(asc(sort_column), asc(Provider.name))

    query = query.offset(skip).limit(limit)

    result = await session.execute(query)
    providers = result.scalars().all()
    return [serialize_provider(provider) for provider in providers]


@router.get("/groups/all")
async def list_groups(
    session: AsyncSession = Depends(get_db)
):
    """List all unique group names."""
    result = await session.execute(
        select(Provider.group_name)
        .where(Provider.group_name.is_not(None))
        .where(Provider.group_name != "")
        .distinct()
        .order_by(asc(Provider.group_name))
    )
    groups = [row[0] for row in result.all() if row[0]]
    return {"groups": groups}


@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_db),
    current_user=Depends(require_auth)
):
    """Get a single provider by ID."""
    result = await session.execute(select(Provider).where(Provider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found"
        )

    return serialize_provider(provider)


@router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(
    provider_data: ProviderCreate,
    session: AsyncSession = Depends(get_db),
    current_user=Depends(require_auth_csrf)
):
    """Create a new provider."""
    data = normalize_provider_payload(provider_data)
    data = apply_check_defaults(data)
    if not data.get("name") or not data.get("ip_address"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Название и IP-адрес обязательны"
        )

    if await find_provider_by_ip(session, data["ip_address"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider with this IP address already exists"
        )

    provider = Provider(
        name=data["name"],
        ip_address=data["ip_address"],
        description=data.get("description"),
        group_name=data.get("group_name"),
        check_type=CheckType(data.get("check_type") or CheckType.AUTO.value),
        check_port=data.get("check_port"),
        check_path=data.get("check_path"),
        dns_expected_value=data.get("dns_expected_value"),
        maintenance_mode=1 if data.get("maintenance_mode") else 0,
        maintenance_note=data.get("maintenance_note"),
        maintenance_started_at=utc_now() if data.get("maintenance_mode") else None,
        maintenance_window_start=data.get("maintenance_window_start"),
        maintenance_window_end=data.get("maintenance_window_end"),
        current_status=ProviderStatus.ONLINE,
        fail_count=0,
    )

    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return serialize_provider(provider)


@router.put("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: int,
    provider_data: ProviderUpdate,
    session: AsyncSession = Depends(get_db),
    current_user=Depends(require_auth_csrf)
):
    """Update an existing provider."""
    result = await session.execute(select(Provider).where(Provider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found"
        )

    data = normalize_provider_payload(provider_data)
    data = apply_check_defaults(data)
    if "ip_address" in data and data["ip_address"]:
        duplicate = await find_provider_by_ip(session, data["ip_address"], exclude_id=provider_id)
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider with this IP address already exists"
            )

    if "name" in data and data["name"] is not None:
        provider.name = data["name"]
    if "ip_address" in data and data["ip_address"] is not None:
        provider.ip_address = data["ip_address"]
    if "description" in data:
        provider.description = data["description"]
    if "group_name" in data:
        provider.group_name = data["group_name"]
    if "check_type" in data and data["check_type"] is not None:
        provider.check_type = CheckType(data["check_type"])
    if "check_port" in data:
        provider.check_port = data["check_port"]
    if "check_path" in data:
        provider.check_path = data["check_path"]
    if "dns_expected_value" in data:
        provider.dns_expected_value = data["dns_expected_value"]
    if "maintenance_note" in data:
        provider.maintenance_note = data["maintenance_note"]
    if "maintenance_window_start" in data:
        provider.maintenance_window_start = data["maintenance_window_start"]
    if "maintenance_window_end" in data:
        provider.maintenance_window_end = data["maintenance_window_end"]
    if "maintenance_mode" in data and data["maintenance_mode"] is not None:
        maintenance_enabled = bool(data["maintenance_mode"])
        if maintenance_enabled and not bool(provider.maintenance_mode):
            provider.maintenance_started_at = utc_now()
        if not maintenance_enabled:
            provider.maintenance_started_at = None
        provider.maintenance_mode = 1 if maintenance_enabled else 0

    await session.commit()
    await session.refresh(provider)
    return serialize_provider(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_db),
    current_user=Depends(require_auth_csrf)
):
    """Delete a provider."""
    result = await session.execute(select(Provider).where(Provider.id == provider_id))
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found"
        )

    await session.delete(provider)
    await session.commit()
    return None
