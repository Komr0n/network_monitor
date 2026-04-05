"""
Telegram alerting and command polling service.
"""
from __future__ import annotations

import html
import logging
import ssl
from datetime import datetime
from typing import Any, Optional

import certifi
import httpx
from sqlalchemy import select

from app.config import TELEGRAM_CA_BUNDLE
from app.models import AsyncSessionLocal, Provider, ProviderStatus
from app.services.maintenance_service import is_maintenance_active
from app.time_utils import format_display_datetime, format_duration_human, utc_now
from app.services.settings_service import (
    get_setting,
    get_telegram_settings,
    set_setting,
)

logger = logging.getLogger(__name__)

TELEGRAM_UPDATE_OFFSET_KEY = "telegram_update_offset"


class TelegramService:
    """Service for sending Telegram alerts and handling bot commands."""

    async def _resolve_credentials(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> tuple[str, str]:
        saved_bot_token, saved_chat_id = await get_telegram_settings()
        return (bot_token or saved_bot_token or "").strip(), (chat_id or saved_chat_id or "").strip()

    @staticmethod
    def _build_base_url(bot_token: str) -> str:
        return f"https://api.telegram.org/bot{bot_token}"

    @staticmethod
    def _build_ssl_context() -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=certifi.where())
        if TELEGRAM_CA_BUNDLE:
            context.load_verify_locations(cafile=TELEGRAM_CA_BUNDLE)
        return context

    @staticmethod
    def _format_http_error(error: Exception) -> str:
        message = str(error)
        if "CERTIFICATE_VERIFY_FAILED" in message:
            return (
                "Ошибка TLS/SSL при подключении к Telegram: система не может проверить сертификат. "
                "Обычно это проблема корневых сертификатов Windows или корпоративного прокси. "
                "Обновите корневые сертификаты сервера или укажите путь к CA bundle в TELEGRAM_CA_BUNDLE."
            )
        return message

    async def _telegram_request(
        self,
        bot_token: str,
        method: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> tuple[bool, dict[str, Any] | str]:
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=self._build_ssl_context()) as client:
                response = await client.post(
                    f"{self._build_base_url(bot_token)}/{method}",
                    json=payload or {},
                )

            body = response.json()
            if response.is_success and body.get("ok"):
                return True, body

            description = body.get("description") or response.text or f"HTTP {response.status_code}"
            return False, description
        except httpx.HTTPError as error:
            logger.error("Telegram HTTP error calling %s: %s", method, error)
            return False, self._format_http_error(error)
        except Exception as error:
            logger.error("Unexpected Telegram error calling %s: %s", method, error)
            return False, str(error)

    async def _post_message(
        self,
        message: str,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        resolved_token, resolved_chat_id = await self._resolve_credentials(bot_token, chat_id)

        if not resolved_token or not resolved_chat_id:
            return False, "Не заполнены токен бота или chat_id"

        ok, payload = await self._telegram_request(
            resolved_token,
            "sendMessage",
            payload={
                "chat_id": resolved_chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if ok:
            return True, "Сообщение успешно отправлено"
        return False, str(payload)

    async def _send_message_to_chat(self, bot_token: str, chat_id: str, message: str) -> tuple[bool, str]:
        ok, payload = await self._telegram_request(
            bot_token,
            "sendMessage",
            payload={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if ok:
            return True, "OK"
        return False, str(payload)

    async def send_alert(
        self,
        provider_name: str,
        ip_address: str,
        status: str,
        timestamp: Optional[datetime] = None,
        check_type: str = "auto",
        check_method: Optional[str] = None,
        group_name: Optional[str] = None,
        offline_duration_seconds: Optional[int] = None,
        maintenance_mode: bool = False,
    ) -> bool:
        """Send a provider status change alert."""
        timestamp = timestamp or utc_now()
        status_emoji = "🔴" if status == "down" else "🟢"
        status_text = "Недоступен" if status == "down" else "Доступен"
        escaped_name = html.escape(provider_name)
        escaped_ip = html.escape(ip_address)
        escaped_check_type = html.escape(check_type)
        escaped_method = html.escape(check_method or "n/a")
        escaped_group_name = html.escape(group_name or "")

        maintenance_hint = "\n<b>Maintenance:</b> enabled" if maintenance_mode else ""
        group_hint = f"\n<b>Группа:</b> {escaped_group_name}" if escaped_group_name else ""
        downtime_hint = ""
        if status == "up" and offline_duration_seconds is not None:
            downtime_hint = f"\n<b>Простой:</b> {html.escape(format_duration_human(offline_duration_seconds))}"
        message = (
            f"{status_emoji} <b>Оповещение мониторинга</b>\n\n"
            f"<b>Узел:</b> {escaped_name}\n"
            f"<b>IP/Host:</b> {escaped_ip}"
            f"{group_hint}\n"
            f"<b>Статус:</b> {status_text}\n"
            f"<b>Проверка:</b> {escaped_check_type}\n"
            f"<b>Последний метод:</b> {escaped_method}"
            f"{downtime_hint}\n"
            f"<b>Время:</b> {html.escape(format_display_datetime(timestamp))}"
            f"{maintenance_hint}"
        )

        success, details = await self._post_message(message)
        if success:
            logger.info("Telegram alert sent for %s (%s): %s", provider_name, ip_address, status)
        else:
            logger.error("Telegram alert failed for %s (%s): %s", provider_name, ip_address, details)
        return success

    async def test_connection(self, bot_token: Optional[str] = None) -> bool:
        """Test Telegram bot connection with getMe."""
        resolved_token, _ = await self._resolve_credentials(bot_token, None)
        if not resolved_token:
            return False

        ok, payload = await self._telegram_request(resolved_token, "getMe", timeout=10.0)
        return bool(ok and isinstance(payload, dict))

    async def send_test_message(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Send a test message to verify both bot token and chat id."""
        message = (
            "🧪 <b>Тестовое сообщение</b>\n\n"
            "Если вы видите это сообщение, настройки Telegram указаны корректно."
        )
        return await self._post_message(message, bot_token=bot_token, chat_id=chat_id)

    async def _load_command_state(self) -> tuple[str, str, int]:
        bot_token, configured_chat_id = await self._resolve_credentials()
        offset_raw = await get_setting(TELEGRAM_UPDATE_OFFSET_KEY, "0")
        try:
            offset = int(offset_raw or "0")
        except ValueError:
            offset = 0
        return bot_token, configured_chat_id, offset

    async def _save_command_offset(self, offset: int) -> None:
        await set_setting(TELEGRAM_UPDATE_OFFSET_KEY, str(offset))

    async def _fetch_updates(self, bot_token: str, offset: int) -> list[dict[str, Any]]:
        ok, payload = await self._telegram_request(
            bot_token,
            "getUpdates",
            payload={
                "offset": offset,
                "timeout": 0,
                "allowed_updates": ["message"],
            },
            timeout=15.0,
        )
        if not ok:
            logger.warning("Telegram getUpdates failed: %s", payload)
            return []
        return list(payload.get("result") or [])

    async def _load_provider_snapshot(self) -> list[Provider]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Provider).order_by(Provider.name.asc()))
            return list(result.scalars().all())

    @staticmethod
    def _render_provider_line(provider: Provider) -> str:
        status_icon = "🔴" if provider.current_status == ProviderStatus.OFFLINE else "🟢"
        maintenance_suffix = " [maintenance]" if is_maintenance_active(provider) else ""
        group_suffix = f" · {html.escape(provider.group_name)}" if provider.group_name else ""
        return (
            f"{status_icon} <b>{html.escape(provider.name)}</b> "
            f"({html.escape(provider.ip_address)}){group_suffix}{maintenance_suffix}"
        )

    async def _build_status_message(self) -> str:
        providers = await self._load_provider_snapshot()
        if not providers:
            return "ℹ️ Узлы мониторинга пока не добавлены."

        lines = ["📡 <b>Статус узлов</b>", ""]
        for provider in providers[:40]:
            lines.append(self._render_provider_line(provider))

        if len(providers) > 40:
            lines.append("")
            lines.append(f"Показаны первые 40 из {len(providers)} узлов.")

        return "\n".join(lines)

    async def _build_down_message(self) -> str:
        providers = await self._load_provider_snapshot()
        down_providers = [provider for provider in providers if provider.current_status == ProviderStatus.OFFLINE]
        if not down_providers:
            return "✅ Сейчас нет недоступных узлов."

        lines = ["🚨 <b>Недоступные узлы</b>", ""]
        lines.extend(self._render_provider_line(provider) for provider in down_providers[:40])
        if len(down_providers) > 40:
            lines.append("")
            lines.append(f"Показаны первые 40 из {len(down_providers)} проблемных узлов.")
        return "\n".join(lines)

    async def _build_summary_message(self) -> str:
        providers = await self._load_provider_snapshot()
        total = len(providers)
        down_providers = [provider for provider in providers if provider.current_status == ProviderStatus.OFFLINE]
        maintenance_providers = [provider for provider in providers if is_maintenance_active(provider)]
        online = total - len(down_providers)

        lines = [
            "📊 <b>Сводка мониторинга</b>",
            "",
            f"<b>Всего узлов:</b> {total}",
            f"<b>В сети:</b> {online}",
            f"<b>Недоступно:</b> {len(down_providers)}",
            f"<b>Maintenance:</b> {len(maintenance_providers)}",
        ]

        if down_providers:
            lines.append("")
            lines.append("<b>Проблемные узлы:</b>")
            lines.extend(self._render_provider_line(provider) for provider in down_providers[:10])

        return "\n".join(lines)

    async def _build_command_response(self, command: str) -> Optional[str]:
        normalized = command.split("@", 1)[0].lower()
        if normalized == "/status":
            return await self._build_status_message()
        if normalized == "/down":
            return await self._build_down_message()
        if normalized == "/summary":
            return await self._build_summary_message()
        return None

    async def poll_commands(self) -> None:
        """Poll Telegram for bot commands without using webhooks."""
        bot_token, configured_chat_id, offset = await self._load_command_state()
        if not bot_token:
            return

        updates = await self._fetch_updates(bot_token, offset)
        if not updates:
            return

        max_offset = offset
        for update in updates:
            update_id = int(update.get("update_id") or 0)
            if update_id >= max_offset:
                max_offset = update_id + 1

            message = update.get("message") or {}
            text = str(message.get("text") or "").strip()
            if not text.startswith("/"):
                continue

            chat = message.get("chat") or {}
            chat_id = str(chat.get("id") or "").strip()
            if not chat_id:
                continue

            if configured_chat_id and chat_id != configured_chat_id:
                logger.info("Ignoring Telegram command from unauthorized chat %s", chat_id)
                continue

            response_text = await self._build_command_response(text.split()[0])
            if not response_text:
                continue

            success, details = await self._send_message_to_chat(bot_token, chat_id, response_text)
            if success:
                logger.info("Handled Telegram command %s for chat %s", text, chat_id)
            else:
                logger.warning("Failed to answer Telegram command %s: %s", text, details)

        await self._save_command_offset(max_offset)


telegram_service = TelegramService()
