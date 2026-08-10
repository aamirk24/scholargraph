from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import dependencies


@pytest.mark.asyncio
async def test_admin_dependency_allows_configured_user(test_user, monkeypatch):
    monkeypatch.setattr(
        dependencies.settings,
        "admin_emails",
        [test_user["email"].lower()],
    )

    result = await dependencies.get_current_admin_user(test_user["user"])

    assert result.id == test_user["user"].id


@pytest.mark.asyncio
async def test_admin_dependency_rejects_non_admin(test_user, monkeypatch):
    monkeypatch.setattr(
        dependencies.settings,
        "admin_emails",
        ["another-admin@example.com"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await dependencies.get_current_admin_user(test_user["user"])

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin privileges required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/crawl", {"topic": "cs.AI", "max_papers": 1}),
        ("/crawl/seed-foundations?top_n=1", None),
        ("/crawl/build-graph", {"topic": "cs.AI"}),
        ("/crawl/build-graph-all", None),
        ("/analytics/embed-papers", None),
        ("/analytics/pagerank", None),
    ],
)
async def test_maintenance_endpoints_reject_non_admin(
    async_client,
    test_user,
    monkeypatch,
    path,
    payload,
):
    monkeypatch.setattr(
        dependencies.settings,
        "admin_emails",
        ["another-admin@example.com"],
    )

    response = await async_client.post(
        path,
        json=payload,
        headers=test_user["headers"],
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin privileges required"
