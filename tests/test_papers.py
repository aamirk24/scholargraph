from __future__ import annotations

import uuid

import pytest

from models.paper import Paper

async def _register_and_login(async_client, suffix: str) -> dict[str, str]:
    email = f"user_{suffix}@example.com"
    username = f"user_{suffix}"
    password = "StrongPass123!"

    register_response = await async_client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
        },
    )
    assert register_response.status_code == 201, register_response.text

    login_response = await async_client.post(
        "/auth/login",
        data={
            "username": email,
            "password": password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_response.status_code == 200, login_response.text

    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_papers_returns_list(async_client, test_paper):
    response = await async_client.get("/papers")

    assert response.status_code == 200
    body = response.json()

    assert "items" in body
    assert "total" in body
    assert "page" in body
    assert "size" in body
    assert isinstance(body["items"], list)
    assert body["total"] >= 1

    ids = [item["id"] for item in body["items"]]
    assert str(test_paper.id) in ids


@pytest.mark.asyncio
async def test_get_papers_filtered_by_category(async_client, test_paper):
    response = await async_client.get("/papers", params={"category": "cs.AI"})

    assert response.status_code == 200
    body = response.json()

    assert isinstance(body["items"], list)
    assert body["total"] >= 1
    assert any(item["id"] == str(test_paper.id) for item in body["items"])

    for item in body["items"]:
        primary = item.get("primary_category")
        all_categories = item.get("all_categories") or []
        assert primary == "cs.AI" or "cs.AI" in all_categories


@pytest.mark.asyncio
async def test_get_paper_by_valid_id_has_links(async_client, test_paper):
    response = await async_client.get(f"/papers/{test_paper.id}")

    assert response.status_code == 200
    body = response.json()

    assert body["id"] == str(test_paper.id)
    assert body["title"] == test_paper.title
    assert "_links" in body

    links = body["_links"]
    assert "self" in links
    assert "citations" in links
    assert "authors" in links
    assert "similar" in links

    assert links["self"]["href"] == f"/papers/{test_paper.id}"
    assert links["citations"]["href"] == f"/papers/{test_paper.id}/citations"
    assert links["authors"]["href"] == f"/papers/{test_paper.id}/authors"
    assert links["similar"]["href"] == f"/papers/{test_paper.id}/similar"


@pytest.mark.asyncio
async def test_get_paper_by_invalid_id_returns_json_404(async_client):
    missing_id = uuid.uuid4()

    response = await async_client.get(f"/papers/{missing_id}")

    assert response.status_code == 404
    body = response.json()

    assert body["error"] == "not_found"
    assert body["resource"] == "papers"
    assert "was not found" in body["detail"]


@pytest.mark.asyncio
async def test_post_annotation(async_client, test_user, test_paper):
    payload = {
        "title": "Interesting note",
        "body": "This paper has a strong transformer-attention framing.",
        "tags": ["transformers", "attention"],
    }

    response = await async_client.post(
        f"/papers/{test_paper.id}/annotations",
        json=payload,
        headers=test_user["headers"],
    )

    assert response.status_code == 201
    body = response.json()

    assert body["paper_id"] == str(test_paper.id)
    assert body["user_id"] == str(test_user["user"].id)
    assert body["title"] == payload["title"]
    assert body["body"] == payload["body"]
    assert body["tags"] == payload["tags"]
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_get_annotations(async_client, test_user, test_paper):
    create_response = await async_client.post(
        f"/papers/{test_paper.id}/annotations",
        json={
            "title": "Public annotation",
            "body": "Visible to everyone.",
            "tags": ["public"],
        },
        headers=test_user["headers"],
    )
    assert create_response.status_code == 201

    response = await async_client.get(f"/papers/{test_paper.id}/annotations")

    assert response.status_code == 200
    body = response.json()

    assert isinstance(body, list)
    assert len(body) >= 1
    assert body[0]["paper_id"] == str(test_paper.id)


@pytest.mark.asyncio
async def test_put_annotation_by_owner(async_client, test_user, test_paper):
    create_response = await async_client.post(
        f"/papers/{test_paper.id}/annotations",
        json={
            "title": "Original title",
            "body": "Original body",
            "tags": ["draft"],
        },
        headers=test_user["headers"],
    )
    assert create_response.status_code == 201
    annotation_id = create_response.json()["id"]

    update_response = await async_client.put(
        f"/annotations/{annotation_id}",
        json={
            "title": "Updated title",
            "body": "Updated body",
            "tags": ["updated", "owner"],
        },
        headers=test_user["headers"],
    )

    assert update_response.status_code == 200
    body = update_response.json()

    assert body["id"] == annotation_id
    assert body["title"] == "Updated title"
    assert body["body"] == "Updated body"
    assert body["tags"] == ["updated", "owner"]


@pytest.mark.asyncio
async def test_put_annotation_by_non_owner(async_client, test_user, test_paper):
    create_response = await async_client.post(
        f"/papers/{test_paper.id}/annotations",
        json={
            "title": "Owner annotation",
            "body": "Only owner can edit this.",
            "tags": ["private"],
        },
        headers=test_user["headers"],
    )
    assert create_response.status_code == 201
    annotation_id = create_response.json()["id"]

    other_user_headers = await _register_and_login(async_client, suffix=uuid.uuid4().hex[:8])

    update_response = await async_client.put(
        f"/annotations/{annotation_id}",
        json={
            "title": "Malicious update",
            "body": "Should not work",
            "tags": ["forbidden"],
        },
        headers=other_user_headers,
    )

    assert update_response.status_code == 403
    body = update_response.json()
    assert body["error"] == "forbidden"
    assert "own annotations" in body["detail"]


@pytest.mark.asyncio
async def test_delete_annotation(async_client, test_user, test_paper):
    create_response = await async_client.post(
        f"/papers/{test_paper.id}/annotations",
        json={
            "title": "To be deleted",
            "body": "Delete me",
            "tags": ["temp"],
        },
        headers=test_user["headers"],
    )
    assert create_response.status_code == 201
    annotation_id = create_response.json()["id"]

    delete_response = await async_client.delete(
        f"/annotations/{annotation_id}",
        headers=test_user["headers"],
    )

    assert delete_response.status_code == 204
    assert delete_response.text == ""

    list_response = await async_client.get(f"/papers/{test_paper.id}/annotations")
    assert list_response.status_code == 200
    ids = [item["id"] for item in list_response.json()]
    assert annotation_id not in ids


@pytest.mark.asyncio
async def test_delete_non_existent_annotation(async_client, test_user):
    missing_id = uuid.uuid4()

    response = await async_client.delete(
        f"/annotations/{missing_id}",
        headers=test_user["headers"],
    )

    assert response.status_code == 404
    body = response.json()

    assert body["error"] == "not_found"
    assert body["resource"] == "annotations"
    assert "was not found" in body["detail"]



async def _seed_papers_for_pagination(test_db, count: int = 12) -> list[Paper]:
    papers: list[Paper] = []

    for i in range(count):
        paper = Paper(
            arxiv_id=f"pagetest-{uuid.uuid4().hex[:10]}",
            title=f"Pagination Test Paper {i:02d}",
            abstract=f"Abstract for pagination test paper {i:02d}.",
            primary_category="cs.AI",
            all_categories=["cs.AI"],
            pagerank_score=float(i) / 100.0,
            abstract_embedding=[0.001] * 384,
        )
        test_db.add(paper)
        papers.append(paper)

    await test_db.commit()

    for paper in papers:
        await test_db.refresh(paper)

    return papers


@pytest.mark.asyncio
async def test_papers_pagination_first_page_size_5(async_client, test_db):
    await _seed_papers_for_pagination(test_db, count=12)

    response = await async_client.get("/papers", params={"page": 1, "size": 5})

    assert response.status_code == 200
    body = response.json()

    assert body["page"] == 1
    assert body["size"] == 5
    assert body["total"] >= 12
    assert len(body["items"]) == 5


@pytest.mark.asyncio
async def test_papers_pagination_second_page_returns_next_5(async_client, test_db):
    await _seed_papers_for_pagination(test_db, count=12)

    page_1 = await async_client.get("/papers", params={"page": 1, "size": 5})
    page_2 = await async_client.get("/papers", params={"page": 2, "size": 5})

    assert page_1.status_code == 200
    assert page_2.status_code == 200

    body_1 = page_1.json()
    body_2 = page_2.json()

    assert body_1["page"] == 1
    assert body_2["page"] == 2
    assert body_1["size"] == 5
    assert body_2["size"] == 5

    assert len(body_1["items"]) == 5
    assert len(body_2["items"]) == 5

    ids_page_1 = [item["id"] for item in body_1["items"]]
    ids_page_2 = [item["id"] for item in body_2["items"]]

    assert ids_page_1 != ids_page_2
    assert set(ids_page_1).isdisjoint(set(ids_page_2))


@pytest.mark.asyncio
async def test_papers_pagination_total_count_correct(async_client, test_db):
    await _seed_papers_for_pagination(test_db, count=12)

    response = await async_client.get("/papers", params={"page": 1, "size": 5})

    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 12


@pytest.mark.asyncio
async def test_papers_pagination_beyond_last_page_returns_empty_items(async_client, test_db):
    await _seed_papers_for_pagination(test_db, count=12)

    response = await async_client.get("/papers", params={"page": 4, "size": 5})

    assert response.status_code == 200
    body = response.json()

    assert body["page"] == 4
    assert body["size"] == 5
    assert body["total"] == 12
    assert body["items"] == []
