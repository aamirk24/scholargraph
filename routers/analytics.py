from __future__ import annotations

import logging

from datetime import datetime
from typing import Literal
from sqlalchemy import asc

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.dependencies import get_current_admin_user
from app.limiter import limiter
from models.paper import Paper
from models.user import User
from services.pagerank import run_pagerank
from services.embeddings import embed_all_papers


logger = logging.getLogger(__name__)

router = APIRouter()

class PageRankAcceptedResponse(BaseModel):
    message: str


class TopicAnalyticsItem(BaseModel):
    category: str
    paper_count: int
    avg_pagerank_score: float | None


class TopicAnalyticsResponse(BaseModel):
    items: list[TopicAnalyticsItem]
    total: int


class TrendPoint(BaseModel):
    period: datetime
    paper_count: int


class TrendResponse(BaseModel):
    topic: str | None
    granularity: Literal["month", "year"]
    items: list[TrendPoint]
    total_points: int


class EmbedPapersAcceptedResponse(BaseModel):
    message: str


async def run_pagerank_job() -> None:
    """
    Background PageRank task.

    Opens its own AsyncSession because request-scoped DB sessions are gone by the
    time FastAPI runs background tasks.
    """
    async with AsyncSessionLocal() as db:
        try:
            result = await run_pagerank(db=db)
            logger.info("Background PageRank finished | result=%r", result)
        except Exception:
            logger.exception("Background PageRank failed")


async def run_embed_papers_job() -> None:
    """
    Background embedding task.

    Opens its own AsyncSession because request-scoped DB sessions are gone by the
    time FastAPI runs background tasks.
    """
    async with AsyncSessionLocal() as db:
        try:
            result = await embed_all_papers(db=db)
            logger.info("Background paper embedding finished | result=%r", result)
        except Exception:
            logger.exception("Background paper embedding failed")


@router.post(
    "/pagerank",
    response_model=PageRankAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "PageRank job accepted and started in the background"},
        401: {"description": "Not authenticated"},
        403: {"description": "Admin privileges required"},
        422: {"description": "Invalid request"},
    },
)
@limiter.limit("60/minute")
async def start_pagerank(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin_user),
) -> PageRankAcceptedResponse:
    """
    Trigger a background PageRank computation.

    Admin-only, JWT-protected. Returns immediately with 202 Accepted while the
    computation continues in the background. Progress is logged to the console.
    """
    background_tasks.add_task(run_pagerank_job)

    return PageRankAcceptedResponse(
        message="PageRank job accepted and started in the background",
    )


@router.get(
    "/topics",
    response_model=TopicAnalyticsResponse,
    responses={
        200: {"description": "Topic analytics returned successfully"},
        422: {"description": "Invalid limit parameter"},
    },
)
async def get_topic_analytics(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> TopicAnalyticsResponse:
    """
    Return topic/category analytics:
    - paper count per primary_category
    - average pagerank_score per primary_category
    """
    stmt = (
        select(
            Paper.primary_category.label("category"),
            func.count(Paper.id).label("paper_count"),
            func.avg(Paper.pagerank_score).label("avg_pagerank_score"),
        )
        .where(Paper.primary_category.is_not(None))
        .group_by(Paper.primary_category)
        .order_by(
            desc(func.count(Paper.id)),
            desc(func.avg(Paper.pagerank_score)),
            Paper.primary_category.asc(),
        )
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    items = [
        TopicAnalyticsItem(
            category=category,
            paper_count=int(paper_count),
            avg_pagerank_score=float(avg_pagerank_score) if avg_pagerank_score is not None else None,
        )
        for category, paper_count, avg_pagerank_score in rows
    ]

    return TopicAnalyticsResponse(
        items=items,
        total=len(items),
    )


@router.get(
    "/trend",
    response_model=TrendResponse,
    responses={
        200: {"description": "Publication trend returned successfully"},
        422: {"description": "Invalid topic or granularity parameter"},
    },
)
async def get_publication_trend(
    topic: str | None = Query(
        default=None,
        description="Filter by primary_category, e.g. cs.AI",
    ),
    granularity: Literal["month", "year"] = Query(
        default="month",
        description="Time bucket size",
    ),
    db: AsyncSession = Depends(get_db),
) -> TrendResponse:
    """
    Return publication count over time, optionally filtered by topic.

    Uses SQL DATE_TRUNC to bucket papers by month or year based on published_date.
    """
    period_expr = func.date_trunc(granularity, Paper.published_date).label("period")

    stmt = (
        select(
            period_expr,
            func.count(Paper.id).label("paper_count"),
        )
        .where(Paper.published_date.is_not(None))
    )

    if topic:
        stmt = stmt.where(Paper.primary_category == topic)

    stmt = (
        stmt.group_by(period_expr)
        .order_by(asc(period_expr))
    )

    result = await db.execute(stmt)
    rows = result.all()

    items = [
        TrendPoint(
            period=period,
            paper_count=int(paper_count),
        )
        for period, paper_count in rows
        if period is not None
    ]

    return TrendResponse(
        topic=topic,
        granularity=granularity,
        items=items,
        total_points=len(items),
    )


@router.post(
    "/embed-papers",
    response_model=EmbedPapersAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Paper embedding job accepted and started in the background"},
        401: {"description": "Not authenticated"},
        403: {"description": "Admin privileges required"},
        422: {"description": "Invalid request"},
    },
)
@limiter.limit("30/minute")
async def start_embed_papers(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin_user),
) -> EmbedPapersAcceptedResponse:
    """
    Trigger background embedding for all papers whose abstract_embedding is NULL.
    """
    background_tasks.add_task(run_embed_papers_job)
    return EmbedPapersAcceptedResponse(
        message="Paper embedding job accepted and started in the background",
    )
