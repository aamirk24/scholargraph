from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from crud.papers import (
    count_papers,
    get_paper,
    get_paper_authors,
    get_paper_references,
    get_papers,
    get_papers_citing_paper,
    get_ranked_papers,
    get_similar_papers,
    semantic_search_papers,
)
from schemas.paper import (
    CitationPaperList,
    CitationPaperResponse,
    PaperAuthorList,
    PaperAuthorResponse,
    PaperList,
    PaperResponse,
    RankedPaperList,
    RankedPaperResponse,
    SemanticSearchPaperList,
    SemanticSearchPaperResponse,
    SemanticSearchQueryParams,
)
from services.embeddings import generate_embedding
from schemas.utils import build_links

router = APIRouter()


@router.get(
    "",
    response_model=PaperList,
    responses={
        200: {"description": "Paginated list of papers returned successfully"},
        422: {"description": "Invalid pagination or filter parameters"},
    },
)
async def list_papers(
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaperList:
    skip = (page - 1) * size

    papers = await get_papers(
        db=db,
        category=category,
        search=search,
        skip=skip,
        limit=size,
    )

    total = await count_papers(
        db=db,
        category=category,
        search=search,
    )

    items: list[PaperResponse] = []
    for paper in papers:
        item = PaperResponse.model_validate(paper)
        item.links = build_links(paper.id)
        items.append(item)

    return PaperList(
        items=items,
        total=total,
        page=page,
        size=size,
    )


@router.get(
    "/ranked",
    response_model=RankedPaperList,
    responses={
        200: {"description": "Top ranked papers returned successfully"},
        422: {"description": "Invalid category or limit parameter"},
    },
)
async def get_ranked_papers_endpoint(
    category: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> RankedPaperList:
    """
    Return top papers by PageRank score.

    Query params:
    - category: optional category/topic filter
    - limit: top N papers to return (default 20, max 100)
    """
    papers = await get_ranked_papers(
        db=db,
        category=category,
        limit=limit,
    )

    items: list[RankedPaperResponse] = []
    for idx, paper in enumerate(papers, start=1):
        base_item = PaperResponse.model_validate(paper)
        item = RankedPaperResponse(
            **base_item.model_dump(),
            rank=idx,
        )
        item.links = build_links(paper.id)
        items.append(item)

    return RankedPaperList(
        items=items,
        total=len(items),
        limit=limit,
        category=category,
    )


@router.get(
    "/search/semantic",
    response_model=SemanticSearchPaperList,
    responses={
        200: {"description": "Semantic search results returned successfully"},
        422: {"description": "Invalid semantic search query parameters"},
    },
)
async def semantic_search_endpoint(
    params: SemanticSearchQueryParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> SemanticSearchPaperList:
    """
    Main semantic search endpoint.

    Logic:
    - sanitize and validate the query through Pydantic
    - embed the query
    - search papers by vector similarity
    - optionally filter by category
    - return the top results with similarity_score
    """
    query_vector = generate_embedding(params.q)

    rows = await semantic_search_papers(
        db=db,
        query_vector=query_vector,
        limit=params.limit,
        category=params.category,
    )

    items: list[SemanticSearchPaperResponse] = []

    for paper, similarity_score in rows:
        base_item = PaperResponse.model_validate(paper)
        item = SemanticSearchPaperResponse(
            **base_item.model_dump(),
            similarity_score=similarity_score,
        )
        item.links = build_links(paper.id)
        items.append(item)

    return SemanticSearchPaperList(
        items=items,
        total=len(items),
        limit=params.limit,
        query=params.q,
        category=params.category,
    )


@router.get(
    "/{paper_id}/similar",
    response_model=SemanticSearchPaperList,
    responses={
        200: {"description": "Similar papers returned successfully"},
        400: {"description": "Paper exists but does not have an embedding yet"},
        404: {"description": "Paper not found"},
        422: {"description": "Invalid paper ID or limit parameter"},
    },
)
async def get_similar_papers_endpoint(
    paper_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SemanticSearchPaperList:
    """
    Find papers similar to a given paper using its stored embedding vector.

    Excludes the source paper itself.
    """
    paper = await get_paper(db=db, paper_id=paper_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paper with id '{paper_id}' was not found.",
        )

    if paper.abstract_embedding is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Paper with id '{paper_id}' does not have an embedding yet.",
        )

    rows = await get_similar_papers(
        db=db,
        source_paper_id=paper.id,
        source_vector=list(paper.abstract_embedding),
        limit=limit,
    )

    items: list[SemanticSearchPaperResponse] = []

    for similar_paper, similarity_score in rows:
        base_item = PaperResponse.model_validate(similar_paper)
        item = SemanticSearchPaperResponse(
            **base_item.model_dump(),
            similarity_score=similarity_score,
        )
        item.links = build_links(similar_paper.id)
        items.append(item)

    return SemanticSearchPaperList(
        items=items,
        total=len(items),
        limit=limit,
        query=f"similar_to:{paper_id}",
        category=None,
    )


@router.get(
    "/{paper_id}",
    response_model=PaperResponse,
    responses={
        200: {"description": "Paper returned successfully"},
        404: {"description": "Paper not found"},
        422: {"description": "Invalid paper ID"},
    },
)
async def get_paper_by_id(
    paper_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PaperResponse:
    paper = await get_paper(db=db, paper_id=paper_id)

    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paper with id '{paper_id}' was not found.",
        )

    response = PaperResponse.model_validate(paper)
    response.links = build_links(paper.id)
    return response


@router.get(
    "/{paper_id}/citations",
    response_model=CitationPaperList,
    responses={
        200: {"description": "Citation relationships returned successfully"},
        404: {"description": "Paper not found"},
        422: {"description": "Invalid paper ID or pagination parameters"},
    },
)
async def get_paper_citations_endpoint(
    paper_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> CitationPaperList:
    """
    Return both citation directions for a paper:

    - cited_by: papers that cite this paper
    - references: papers this paper cites
    """
    paper = await get_paper(db=db, paper_id=paper_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paper with id '{paper_id}' was not found.",
        )

    incoming = await get_papers_citing_paper(db=db, paper_id=paper_id)
    outgoing = await get_paper_references(db=db, paper_id=paper_id)

    combined: list[CitationPaperResponse] = []

    for cited_by_paper in incoming:
        base_item = PaperResponse.model_validate(cited_by_paper)
        item = CitationPaperResponse(
            **base_item.model_dump(),
            direction="cited_by",
        )
        item.links = build_links(cited_by_paper.id)
        combined.append(item)

    for referenced_paper in outgoing:
        base_item = PaperResponse.model_validate(referenced_paper)
        item = CitationPaperResponse(
            **base_item.model_dump(),
            direction="references",
        )
        item.links = build_links(referenced_paper.id)
        combined.append(item)

    combined.sort(key=lambda item: (item.direction, item.title.lower()))

    total = len(combined)
    start = (page - 1) * size
    end = start + size
    paginated_items = combined[start:end]

    return CitationPaperList(
        items=paginated_items,
        total=total,
        page=page,
        size=size,
    )


@router.get(
    "/{paper_id}/authors",
    response_model=PaperAuthorList,
    responses={
        200: {"description": "Paper authors returned successfully"},
        404: {"description": "Paper not found"},
        422: {"description": "Invalid paper ID or pagination parameters"},
    },
)
async def get_paper_authors_endpoint(
    paper_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaperAuthorList:
    """
    Return authors for a paper, including their position on the paper.
    """
    paper = await get_paper(db=db, paper_id=paper_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Paper with id '{paper_id}' was not found.",
        )

    author_links = await get_paper_authors(db=db, paper_id=paper_id)

    total = len(author_links)
    start = (page - 1) * size
    end = start + size
    paginated_links = author_links[start:end]

    items: list[PaperAuthorResponse] = []
    for link in paginated_links:
        if link.author is None:
            continue

        items.append(
            PaperAuthorResponse(
                id=link.author.id,
                name=link.author.name,
                position=link.position,
            )
        )

    return PaperAuthorList(
        items=items,
        total=total,
        page=page,
        size=size,
    )
