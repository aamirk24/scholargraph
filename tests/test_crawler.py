from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from models.paper import Paper
from services.crawler import ArxivClient, save_paper_to_db


@pytest.mark.asyncio
async def test_arxiv_retry_honours_numeric_retry_after(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "12"},
                request=request,
            )
        return httpx.Response(200, text="<feed />", request=request)

    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    client = ArxivClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._respect_rate_limit = AsyncMock()

    try:
        result = await client._get_with_retry({"id_list": "2401.12345"})
    finally:
        await client._client.aclose()

    assert result == "<feed />"
    assert attempts == 2
    sleep.assert_awaited_once_with(12.0)


@pytest.mark.asyncio
async def test_concurrent_upserts_for_same_paper_do_not_create_duplicates(
    session_factory,
):
    paper_data = {
        "arxiv_id": "2401.12345",
        "title": "Concurrent ScholarGraph Test",
        "abstract": "A paper used to exercise overlapping crawl writes.",
        "authors": [],
        "published": date(2024, 1, 1),
        "updated": datetime(2024, 1, 2, tzinfo=UTC),
        "primary_category": "cs.AI",
        "all_categories": ["cs.AI"],
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }

    async def save_and_commit() -> None:
        async with session_factory() as session:
            await save_paper_to_db(session, paper_data)
            await session.commit()

    await asyncio.gather(save_and_commit(), save_and_commit())

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.arxiv_id == paper_data["arxiv_id"])
        )

    assert count == 1
