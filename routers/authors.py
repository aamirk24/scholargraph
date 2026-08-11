from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from crud.authors import (
    get_author_papers,
    get_author_with_stats,
    get_authors_with_stats,
    get_author_top_papers_by_pagerank,
    get_author_total_citations_received
)
from schemas.author import (
    AuthorDetailResponse,
    AuthorListItem,
    AuthorListResponse,
    AuthorImpactResponse
)
from schemas.paper import PaperResponse
from schemas.utils import build_links

router = APIRouter()


@router.get(
    "",
    response_model=AuthorListResponse,
    responses={
        200: {"description": "Authors returned successfully"},
    },
)
async def list_authors(
    db: AsyncSession = Depends(get_db),
) -> AuthorListResponse:
    rows = await get_authors_with_stats(db)

    items = [
        AuthorListItem(
            id=author.id,
            name=author.name,
            paper_count=int(paper_count),
            avg_pagerank_score=float(avg_pagerank_score) if avg_pagerank_score is not None else None,
        )
        for author, paper_count, avg_pagerank_score in rows
    ]

    return AuthorListResponse(
        items=items,
        total=len(items),
    )


@router.get(
    "/{author_id}",
    response_model=AuthorDetailResponse,
    responses={
        200: {"description": "Author returned successfully"},
        404: {"description": "Author not found"},
        422: {"description": "Invalid author ID"},
    },
)
async def get_author_by_id(
    author_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuthorDetailResponse:
    row = await get_author_with_stats(db, author_id)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Author with id '{author_id}' was not found.",
        )

    author, paper_count, avg_pagerank_score = row
    papers = await get_author_papers(db, author_id)

    paper_items: list[PaperResponse] = []
    for paper in papers:
        item = PaperResponse.model_validate(paper)
        item.links = build_links(paper.id)
        paper_items.append(item)

    return AuthorDetailResponse(
        id=author.id,
        name=author.name,
        paper_count=int(paper_count),
        avg_pagerank_score=float(avg_pagerank_score) if avg_pagerank_score is not None else None,
        papers=paper_items,
    )


@router.get(
    "/{author_id}/impact",
    response_model=AuthorImpactResponse,
    responses={
        200: {"description": "Author impact analytics returned successfully"},
        404: {"description": "Author not found"},
        422: {"description": "Invalid author ID"},
    },
)
async def get_author_impact(
    author_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuthorImpactResponse:
    """
    Return author citation analytics:
    - total papers
    - total citations received
    - top 5 papers by pagerank_score
    - average pagerank across all papers
    """
    row = await get_author_with_stats(db, author_id)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Author with id '{author_id}' was not found.",
        )

    author, paper_count, avg_pagerank_score = row

    total_citations_received = await get_author_total_citations_received(db, author_id)
    top_papers = await get_author_top_papers_by_pagerank(db, author_id, limit=5)

    paper_items: list[PaperResponse] = []
    for paper in top_papers:
        item = PaperResponse.model_validate(paper)
        item.links = build_links(paper.id)
        paper_items.append(item)

    return AuthorImpactResponse(
        id=author.id,
        name=author.name,
        total_papers=int(paper_count),
        total_citations_received=total_citations_received,
        avg_pagerank_score=float(avg_pagerank_score) if avg_pagerank_score is not None else None,
        top_papers=paper_items,
    )
