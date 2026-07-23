"""APScheduler wiring for the ingestion + scoring pipeline."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.logging import get_logger
from app.worker import pipeline

log = get_logger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    """Create the scheduler with all pipeline jobs registered (not yet started)."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        pipeline.job_sync_markets,
        "interval",
        minutes=settings.sync_markets_minutes,
        id="sync_markets",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        pipeline.job_sync_trades,
        "interval",
        minutes=settings.sync_trades_minutes,
        id="sync_trades",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        pipeline.job_sync_traders,
        "interval",
        minutes=settings.sync_traders_minutes,
        id="sync_traders",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        pipeline.job_scoring,
        "interval",
        minutes=settings.scoring_minutes,
        id="run_scoring",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
