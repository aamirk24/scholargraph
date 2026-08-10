from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request, Depends, status
from pydantic import BaseModel, Field

from app.database import AsyncSessionLocal
from app.dependencies import get_current_admin_user
from app.limiter import limiter
from models.user import User
from services.crawler import build_graph_for_all, build_graph_for_topic, crawl_topic, seed_foundations

logger = logging.getLogger(__name__)

router = APIRouter()

class CrawlRequest(BaseModel):
    topic: str = Field(..., examples=["cs.AI"])
    max_papers: int = Field(default=200, ge=1, le=1000)


class CrawlAcceptedResponse(BaseModel):
    message: str
    topic: str
    query: str
    max_papers: int


class SeedFoundationsAcceptedResponse(BaseModel):
    message: str
    top_n: int


class BuildGraphRequest(BaseModel):
    topic: str = Field(..., examples=["cs.AI"])


class BuildGraphAcceptedResponse(BaseModel):
    message: str
    topic: str


class BuildGraphAllAcceptedResponse(BaseModel):
    message: str
    force_refresh: bool


def topic_to_query(topic: str) -> str:
    topic = topic.strip()
    if ":" in topic:
        return topic
    return f"cat:{topic}"


async def run_crawl_job(topic_query: str, max_papers: int) -> None:
    async with AsyncSessionLocal() as db:
        try:
            result = await crawl_topic(
                db=db,
                topic=topic_query,
                max_papers=max_papers,
            )
            logger.info(
                "Background crawl finished | query=%r max_papers=%d result=%r",
                topic_query, max_papers, result,
            )
        except Exception:
            logger.exception(
                "Background crawl failed | query=%r max_papers=%d",
                topic_query, max_papers,
            )


async def run_seed_foundations_job(top_n: int) -> None:
    async with AsyncSessionLocal() as db:
        try:
            result = await seed_foundations(db=db, top_n=top_n)
            logger.info("seed_foundations finished | result=%r", result)
        except Exception:
            logger.exception("seed_foundations failed")


async def run_build_graph_job(topic: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            result = await build_graph_for_topic(db=db, topic=topic)
            logger.info(
                "Background graph build finished | topic=%r result=%r",
                topic, result,
            )
        except Exception:
            logger.exception("Background graph build failed | topic=%r", topic)


async def run_build_graph_all_job(force_refresh: bool) -> None:
    async with AsyncSessionLocal() as db:
        try:
            result = await build_graph_for_all(db=db, force_refresh=force_refresh)
            logger.info(
                "Background full-corpus graph build finished | result=%r", result
            )
        except Exception:
            logger.exception("Background full-corpus graph build failed")


@router.post(
    "",
    response_model=CrawlAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Crawl job accepted and started in the background"},
        401: {"description": "Not authenticated"},
        403: {"description": "Admin privileges required"},
        422: {"description": "Invalid crawl request payload"},
    },
)
@limiter.limit("60/minute")
async def start_crawl(
    request: Request,
    crawl_request: CrawlRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin_user),
) -> CrawlAcceptedResponse:
    """
    Start a background crawl job for an arXiv topic.

    JWT-protected, admin only. Returns 202 immediately while the crawl
    runs in the background.
    """
    topic_query = topic_to_query(crawl_request.topic)
    background_tasks.add_task(run_crawl_job, topic_query, crawl_request.max_papers)
    return CrawlAcceptedResponse(
        message="Crawl job accepted and started in the background",
        topic=crawl_request.topic,
        query=topic_query,
        max_papers=crawl_request.max_papers,
    )


@router.post(
    "/seed-foundations",
    response_model=SeedFoundationsAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Foundation seeding job accepted and started in the background"},
        401: {"description": "Not authenticated"},
        422: {"description": "Invalid top_n parameter"},
    },
)
@limiter.limit("60/minute")
async def start_seed_foundations(
    request: Request,
    background_tasks: BackgroundTasks,
    top_n: int = 150,
    current_user: User = Depends(get_current_admin_user),
) -> SeedFoundationsAcceptedResponse:
    """
    Identify the top-N most-cited papers missing from the corpus and crawl them.

    Run this after crawling all topics and before build-graph-all.
    It finds the foundational papers (BERT, ResNet, Transformers, etc.) that
    your corpus papers cite heavily but which don't appear in a topic crawl,
    then fetches and inserts them from arXiv.

    Recommended sequence:
        POST /crawl           (repeat for each topic)
        POST /crawl/seed-foundations
        POST /crawl/build-graph-all
    """
    background_tasks.add_task(run_seed_foundations_job, top_n)
    return SeedFoundationsAcceptedResponse(
        message="Foundation seeding job accepted and started in the background",
        top_n=top_n,
    )


@router.post(
    "/build-graph",
    response_model=BuildGraphAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Graph build job accepted and started in the background"},
        401: {"description": "Not authenticated"},
        422: {"description": "Invalid graph build request payload"},
    },
)
@limiter.limit("60/minute")
async def start_build_graph(
    request: Request,
    graph_request: BuildGraphRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin_user),
) -> BuildGraphAcceptedResponse:
    """
    Build citation edges for papers in a single topic already in the DB.

    Use this for incremental updates after crawling one new topic.
    For a full corpus build after crawling all topics, use /build-graph-all.
    """
    background_tasks.add_task(run_build_graph_job, graph_request.topic)
    return BuildGraphAcceptedResponse(
        message="Graph build job accepted and started in the background",
        topic=graph_request.topic,
    )


@router.post(
    "/build-graph-all",
    response_model=BuildGraphAllAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Full corpus graph build accepted and started in the background"},
        401: {"description": "Not authenticated"},
        422: {"description": "Invalid force_refresh parameter"},
    },
)
@limiter.limit("60/minute")
async def start_build_graph_all(
    request: Request,
    background_tasks: BackgroundTasks,
    force_refresh: bool = False,
    current_user: User = Depends(get_current_admin_user),
) -> BuildGraphAllAcceptedResponse:
    """
    Build citation edges across the entire paper corpus in one pass.

    Run this once after crawling all topics. Cross-topic citations
    (e.g. cs.AI paper citing a cs.LG paper) are captured correctly
    because the full corpus is searched when resolving each reference.

    Set force_refresh=true to delete and recreate all existing edges.
    """
    background_tasks.add_task(run_build_graph_all_job, force_refresh)
    return BuildGraphAllAcceptedResponse(
        message="Full corpus graph build accepted and started in the background",
        force_refresh=force_refresh,
    )
