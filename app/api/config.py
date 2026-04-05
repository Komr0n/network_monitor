"""
Configuration API routes.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_auth, require_auth_csrf
from app.models import User, get_db
from app.time_utils import serialize_datetime
from app.services import (
    create_backup,
    get_backup_directory,
    get_database_file_path,
    get_password_hash,
    get_telegram_settings,
    list_backups,
    resolve_backup_file,
    save_telegram_settings,
    telegram_service,
)

router = APIRouter(prefix="/api/config", tags=["config"])


class UserResponse(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: Optional[str]
    last_login: Optional[str]


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=4, max_length=255)
    is_active: bool = True


class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=1, max_length=100)
    password: Optional[str] = Field(None, min_length=4, max_length=255)
    is_active: Optional[bool] = None


class TelegramConfigResponse(BaseModel):
    bot_token: str
    chat_id: str
    bot_configured: bool
    chat_configured: bool


class TelegramConfigUpdate(BaseModel):
    bot_token: Optional[str] = ""
    chat_id: Optional[str] = ""


class TelegramTestRequest(BaseModel):
    bot_token: Optional[str] = ""
    chat_id: Optional[str] = ""


class BackupResponse(BaseModel):
    filename: str
    size_bytes: int
    created_at: str


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "is_active": bool(user.is_active),
        "created_at": serialize_datetime(user.created_at),
        "last_login": serialize_datetime(user.last_login),
    }


async def ensure_active_user_will_remain(session: AsyncSession) -> None:
    """Prevent removal of the last active user."""
    result = await session.execute(select(func.count(User.id)).where(User.is_active == 1))
    active_count = result.scalar() or 0
    if active_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="В системе должен остаться хотя бы один активный пользователь"
        )


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """List all users."""
    result = await session.execute(select(User).order_by(User.username.asc()))
    users = result.scalars().all()
    return {"users": [serialize_user(user) for user in users]}


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth_csrf)
):
    """Create a user."""
    username = user_data.username.strip()
    result = await session.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким логином уже существует"
        )

    user = User(
        username=username,
        password_hash=get_password_hash(user_data.password),
        is_active=1 if user_data.is_active else 0
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return serialize_user(user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth_csrf)
):
    """Update a user."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    if user_data.username is not None:
        username = user_data.username.strip()
        duplicate = await session.execute(select(User).where(User.username == username, User.id != user_id))
        if duplicate.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пользователь с таким логином уже существует"
            )
        user.username = username

    if user_data.password:
        user.password_hash = get_password_hash(user_data.password)

    if user_data.is_active is not None:
        if not user_data.is_active and bool(user.is_active):
            await ensure_active_user_will_remain(session)
        user.is_active = 1 if user_data.is_active else 0

    await session.commit()
    await session.refresh(user)
    return serialize_user(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth_csrf)
):
    """Delete a user."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить текущего авторизованного пользователя"
        )

    if bool(user.is_active):
        await ensure_active_user_will_remain(session)

    await session.delete(user)
    await session.commit()
    return None


@router.get("/telegram", response_model=TelegramConfigResponse)
async def get_telegram_config(current_user: User = Depends(require_auth)):
    """Get Telegram configuration."""
    bot_token, chat_id = await get_telegram_settings()
    return {
        "bot_token": bot_token,
        "chat_id": chat_id,
        "bot_configured": bool(bot_token),
        "chat_configured": bool(chat_id),
    }


@router.put("/telegram", response_model=TelegramConfigResponse)
async def update_telegram_config(
    config_data: TelegramConfigUpdate,
    current_user: User = Depends(require_auth_csrf)
):
    """Update Telegram configuration."""
    await save_telegram_settings(config_data.bot_token, config_data.chat_id)
    bot_token, chat_id = await get_telegram_settings()
    return {
        "bot_token": bot_token,
        "chat_id": chat_id,
        "bot_configured": bool(bot_token),
        "chat_configured": bool(chat_id),
    }


@router.post("/telegram/test")
async def test_telegram_config(
    payload: TelegramTestRequest,
    current_user: User = Depends(require_auth_csrf)
):
    """Send a Telegram test message with provided or saved settings."""
    bot_token = (payload.bot_token or "").strip() or None
    chat_id = (payload.chat_id or "").strip() or None
    success, message = await telegram_service.send_test_message(bot_token=bot_token, chat_id=chat_id)
    return {"success": success, "message": message}


@router.get("/backups")
async def get_backups(current_user: User = Depends(require_auth)):
    """List available SQLite backup files."""
    backups = list_backups(limit=20)
    return {
        "database_path": str(get_database_file_path()),
        "backup_directory": str(get_backup_directory()),
        "backups": [
            {
                "filename": backup.filename,
                "size_bytes": backup.size_bytes,
                "created_at": serialize_datetime(backup.created_at),
            }
            for backup in backups
        ],
    }


@router.post("/backups", response_model=BackupResponse, status_code=status.HTTP_201_CREATED)
async def create_database_backup(current_user: User = Depends(require_auth_csrf)):
    """Create a new SQLite backup file."""
    try:
        backup = create_backup()
    except FileNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)) from error

    return {
        "filename": backup.filename,
        "size_bytes": backup.size_bytes,
        "created_at": serialize_datetime(backup.created_at),
    }


@router.get("/backups/download/{filename}")
async def download_database_backup(
    filename: str,
    current_user: User = Depends(require_auth)
):
    """Download a previously created SQLite backup."""
    try:
        file_path = resolve_backup_file(filename)
    except FileNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup file not found") from error

    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=file_path.name,
    )
