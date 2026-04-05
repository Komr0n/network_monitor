"""
APScheduler jobs for the monitoring system.
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import CHECK_INTERVAL
from app.services import monitoring_service, telegram_service
from app.time_utils import serialize_datetime

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler()
TELEGRAM_COMMAND_POLL_INTERVAL = 5


async def monitoring_job():
    """
    Main monitoring job that checks all providers.
    
    This job runs every CHECK_INTERVAL seconds and:
    1. Fetches all providers from the database
    2. Checks each provider concurrently
    3. Updates status and sends alerts on changes
    
    The job runs as a non-blocking async task to prevent
    blocking the FastAPI event loop.
    """
    try:
        # Run monitoring as a non-blocking task
        task = asyncio.create_task(monitoring_service.check_all_providers())
        await task
    except Exception as e:
        logger.error(f"Error in monitoring job: {e}")


async def telegram_commands_job():
    """Poll Telegram for bot commands."""
    try:
        await telegram_service.poll_commands()
    except Exception as e:
        logger.error("Error in Telegram commands job: %s", e)


def start_scheduler():
    """Start the APScheduler with monitoring job."""
    for job_id in ("monitoring_job", "telegram_commands_job"):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    scheduler.add_job(
        monitoring_job,
        trigger=IntervalTrigger(seconds=CHECK_INTERVAL),
        id="monitoring_job",
        name="Network Monitoring Job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        telegram_commands_job,
        trigger=IntervalTrigger(seconds=TELEGRAM_COMMAND_POLL_INTERVAL),
        id="telegram_commands_job",
        name="Telegram Commands Job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started with monitoring interval=%ss and telegram poll interval=%ss",
        CHECK_INTERVAL,
        TELEGRAM_COMMAND_POLL_INTERVAL,
    )


def stop_scheduler():
    """Stop the scheduler gracefully."""
    scheduler.shutdown(wait=True)
    logger.info("Scheduler stopped")


def get_scheduler_status():
    """Get current scheduler status."""
    job = scheduler.get_job("monitoring_job")
    if job:
        return {
            "running": scheduler.running,
            "next_run": serialize_datetime(job.next_run_time),
            "interval": CHECK_INTERVAL
        }
    return {"running": scheduler.running, "interval": CHECK_INTERVAL}
