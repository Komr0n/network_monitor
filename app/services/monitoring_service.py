"""
Monitoring service - core logic for checking providers and managing status.
"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import FAIL_THRESHOLD
from app.models import AlertLog, AsyncSessionLocal, Provider, ProviderStatus, StatusLog
from app.services.maintenance_service import is_maintenance_active
from app.time_utils import format_duration_human, utc_now
from app.services.probe_service import ProbeResult, check_target
from app.services.telegram_service import telegram_service

logger = logging.getLogger(__name__)


class MonitoringService:
    """Service for monitoring provider status."""

    def __init__(self):
        self._lock = asyncio.Lock()

    @staticmethod
    def _status_log_details(result: ProbeResult) -> str | None:
        if result.details:
            return result.details[:500]
        if result.error:
            return result.error[:500]
        return None

    async def check_provider(self, provider: Provider, session: AsyncSession) -> bool:
        """
        Check a single provider and update its status.
        """
        probe_result = await check_target(
            provider.ip_address,
            check_type=getattr(provider.check_type, "value", provider.check_type) or "auto",
            port=provider.check_port,
            path=provider.check_path,
            dns_expected_value=provider.dns_expected_value,
        )

        current_time = utc_now()
        provider.last_checked = current_time
        provider.last_check_method = probe_result.method or None
        provider.last_error = (probe_result.error or "")[:500] or None

        if probe_result.is_online:
            if provider.current_status == ProviderStatus.OFFLINE:
                offline_duration_seconds = None
                if provider.offline_since:
                    offline_duration_seconds = max(0, int((current_time - provider.offline_since).total_seconds()))
                provider.current_status = ProviderStatus.ONLINE
                provider.fail_count = 0
                provider.response_time = int(probe_result.response_time_ms) if probe_result.response_time_ms else None
                provider.offline_since = None

                await self._log_status_change(session, provider, ProviderStatus.ONLINE, probe_result)
                await self._send_alert(session, provider, "up", offline_duration_seconds=offline_duration_seconds)
                logger.info(
                    "Provider %s (%s) is now ONLINE via %s",
                    provider.name,
                    provider.ip_address,
                    probe_result.method or "unknown method",
                )
            else:
                provider.fail_count = 0
                provider.response_time = int(probe_result.response_time_ms) if probe_result.response_time_ms else None
                provider.offline_since = None
                await self._log_status(session, provider, ProviderStatus.ONLINE, probe_result)
        else:
            provider.fail_count += 1
            provider.response_time = None

            if provider.fail_count >= FAIL_THRESHOLD:
                if provider.current_status == ProviderStatus.ONLINE:
                    provider.current_status = ProviderStatus.OFFLINE
                    provider.offline_since = current_time

                    await self._log_status_change(session, provider, ProviderStatus.OFFLINE, probe_result)
                    await self._send_alert(session, provider, "down")
                    logger.warning(
                        "Provider %s (%s) is now OFFLINE after %s consecutive failures (%s: %s)",
                        provider.name,
                        provider.ip_address,
                        provider.fail_count,
                        probe_result.method or "probe",
                        probe_result.error or probe_result.details or "no details",
                    )
                else:
                    if provider.offline_since is None:
                        provider.offline_since = current_time
                    await self._log_status(session, provider, ProviderStatus.OFFLINE, probe_result)
                    logger.debug(
                        "Provider %s (%s) still OFFLINE (fail_count: %s)",
                        provider.name,
                        provider.ip_address,
                        provider.fail_count,
                    )
            else:
                await self._log_status(session, provider, ProviderStatus.OFFLINE, probe_result)
                logger.debug(
                    "Provider %s (%s) probe failure (%s/%s): %s",
                    provider.name,
                    provider.ip_address,
                    provider.fail_count,
                    FAIL_THRESHOLD,
                    probe_result.error or probe_result.details or "no details",
                )

        await session.commit()
        return probe_result.is_online

    async def _log_status(
        self,
        session: AsyncSession,
        provider: Provider,
        status: ProviderStatus,
        result: ProbeResult,
    ):
        """Log a status check to StatusLog."""
        status_log = StatusLog(
            provider_id=provider.id,
            status=status,
            response_time=int(result.response_time_ms) if result.response_time_ms else None,
            check_method=(result.method or "")[:50] or None,
            details=self._status_log_details(result),
            timestamp=utc_now(),
        )
        session.add(status_log)

    async def _log_status_change(
        self,
        session: AsyncSession,
        provider: Provider,
        new_status: ProviderStatus,
        result: ProbeResult,
    ):
        """Log a status change to StatusLog."""
        await self._log_status(session, provider, new_status, result)

    async def _send_alert(
        self,
        session: AsyncSession,
        provider: Provider,
        status_change: str,
        offline_duration_seconds: int | None = None,
    ):
        """
        Send Telegram alert and log to AlertLog.
        """
        if is_maintenance_active(provider):
            logger.info(
                "Skipping Telegram alert for %s (%s) because maintenance is active",
                provider.name,
                provider.ip_address,
            )
            return

        alert_sent = await telegram_service.send_alert(
            provider_name=provider.name,
            ip_address=provider.ip_address,
            status=status_change,
            timestamp=utc_now(),
            check_type=getattr(provider.check_type, "value", provider.check_type) or "auto",
            check_method=provider.last_check_method,
            group_name=provider.group_name,
            offline_duration_seconds=offline_duration_seconds,
            maintenance_mode=is_maintenance_active(provider),
        )

        if status_change == "up" and offline_duration_seconds is not None:
            message = (
                f"Узел {provider.name} ({provider.ip_address})"
                f"{f' [{provider.group_name}]' if provider.group_name else ''} "
                f"восстановлен после простоя {format_duration_human(offline_duration_seconds)}"
            )
        else:
            message = (
                f"Узел {provider.name} ({provider.ip_address})"
                f"{f' [{provider.group_name}]' if provider.group_name else ''} "
                f"{'недоступен' if status_change == 'down' else 'доступен'}"
            )

        alert_log = AlertLog(
            provider_id=provider.id,
            status_change=status_change,
            message=message,
            sent_at=utc_now(),
        )
        session.add(alert_log)

        if alert_sent:
            logger.info("Alert sent for %s: %s", provider.name, status_change)
        else:
            logger.warning("Failed to send alert for %s: %s", provider.name, status_change)

    async def check_all_providers(self):
        """
        Check all providers concurrently.
        This is the main entry point for the monitoring job.
        """
        async with self._lock:
            async with AsyncSessionLocal() as session:
                try:
                    result = await session.execute(select(Provider))
                    providers = result.scalars().all()

                    if not providers:
                        logger.debug("No providers to check")
                        return

                    logger.debug("Checking %s providers", len(providers))

                    tasks = [self._check_provider_safe(provider) for provider in providers]

                    await asyncio.gather(*tasks, return_exceptions=True)

                except Exception as e:
                    logger.error("Error in check_all_providers: %s", e)
                    await session.rollback()

    async def _check_provider_safe(self, provider: Provider):
        """Safely check a provider with error handling."""
        try:
            async with AsyncSessionLocal() as provider_session:
                result = await provider_session.execute(
                    select(Provider).where(Provider.id == provider.id)
                )
                fresh_provider = result.scalar_one()

                await self.check_provider(fresh_provider, provider_session)
        except Exception as e:
            logger.error("Error checking provider %s (%s): %s", provider.name, provider.ip_address, e)


# Global instance
monitoring_service = MonitoringService()
