"""
Application settings service.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.models import AppSetting, AsyncSessionLocal, init_db

TELEGRAM_BOT_TOKEN_KEY = "telegram_bot_token"
TELEGRAM_CHAT_ID_KEY = "telegram_chat_id"


async def get_setting(key: str, default: Optional[str] = None, session: Optional[AsyncSession] = None) -> Optional[str]:
    """Read a setting from the database with an optional fallback."""
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()

    try:
        try:
            result = await session.execute(select(AppSetting).where(AppSetting.key == key))
            setting = result.scalar_one_or_none()
            if setting is None or setting.value is None:
                return default
            return setting.value
        except OperationalError:
            await init_db()
            return default
    finally:
        if owns_session:
            await session.close()


async def set_setting(key: str, value: Optional[str], session: Optional[AsyncSession] = None) -> None:
    """Create or update a setting."""
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()

    try:
        try:
            result = await session.execute(select(AppSetting).where(AppSetting.key == key))
            setting = result.scalar_one_or_none()

            if setting is None:
                setting = AppSetting(key=key, value=value or "")
                session.add(setting)
            else:
                setting.value = value or ""

            if owns_session:
                await session.commit()
        except OperationalError:
            await init_db()
            if owns_session:
                await session.close()
                async with AsyncSessionLocal() as retry_session:
                    retry_result = await retry_session.execute(select(AppSetting).where(AppSetting.key == key))
                    retry_setting = retry_result.scalar_one_or_none()
                    if retry_setting is None:
                        retry_session.add(AppSetting(key=key, value=value or ""))
                    else:
                        retry_setting.value = value or ""
                    await retry_session.commit()
    finally:
        if owns_session:
            try:
                await session.close()
            except Exception:
                pass


async def get_telegram_settings(session: Optional[AsyncSession] = None) -> tuple[str, str]:
    """Get Telegram credentials from DB first, then .env fallback."""
    bot_token = await get_setting(TELEGRAM_BOT_TOKEN_KEY, TELEGRAM_BOT_TOKEN, session=session)
    chat_id = await get_setting(TELEGRAM_CHAT_ID_KEY, TELEGRAM_CHAT_ID, session=session)
    return (bot_token or "").strip(), (chat_id or "").strip()


async def save_telegram_settings(bot_token: Optional[str], chat_id: Optional[str], session: Optional[AsyncSession] = None) -> None:
    """Persist Telegram credentials."""
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()

    try:
        await set_setting(TELEGRAM_BOT_TOKEN_KEY, (bot_token or "").strip(), session=session)
        await set_setting(TELEGRAM_CHAT_ID_KEY, (chat_id or "").strip(), session=session)
        if owns_session:
            await session.commit()
    finally:
        if owns_session:
            await session.close()
