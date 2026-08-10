from __future__ import annotations

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.database import AsyncSessionLocal
from services.crawler import build_graph_for_topic

logger = logging.getLogger(__name__)

settings = get_settings()
GRAPH_REFRESH_TOPICS = settings.graph_refresh_topics

scheduler = AsyncIOScheduler(timezone="UTC")


async def nightly_graph_refresh() -> None:
    logger.info("Nightly graph refresh started | topics=%s", GRAPH_REFRESH_TOPICS)

    async with AsyncSessionLocal() as db:
        for topic in GRAPH_REFRESH_TOPICS:
            try:
                result = await build_graph_for_topic(
                    db=db,
                    topic=topic,
                    force_refresh=True,
                )
                logger.info(
                    "Nightly graph refresh complete for topic=%r result=%r",
                    topic,
                    result,
                )
            except Exception:
                await db.rollback()
                logger.exception(
                    "Nightly graph refresh failed for topic=%r",
                    topic,
                )

    logger.info("Nightly graph refresh finished")


def start_scheduler() -> None:
    if not settings.scheduler_enabled:
        logger.info("APScheduler disabled by configuration")
        return

    scheduler.add_job(
        nightly_graph_refresh,
        "cron",
        hour=2,
        minute=0,
        id="nightly_graph_refresh",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler started with nightly graph refresh job")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
