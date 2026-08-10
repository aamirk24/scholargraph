from __future__ import annotations

from datetime import timedelta

import pytest

from services.auth import create_access_token


@pytest.mark.asyncio
async def test_register_success(async_client):
    payload = {
        "username": "newuser123",
        "email": "newuser123@example.com",
        "password": "StrongPass123!",
    }

    response = await async_client.post("/auth/register", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == payload["username"]
    assert body["email"] == payload["email"]
    assert "id" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_register_duplicate_email(async_client):
    payload = {
        "username": "dupuser1",
        "email": "duplicate@example.com",
        "password": "StrongPass123!",
    }

    first = await async_client.post("/auth/register", json=payload)
    assert first.status_code == 201

    second = await async_client.post(
        "/auth/register",
        json={
            "username": "dupuser2",
            "email": "duplicate@example.com",
            "password": "AnotherPass123!",
        },
    )

    # Current app behavior is 400, not 409
    assert second.status_code == 400
    assert second.json()["detail"] == "Email is already registered"


@pytest.mark.asyncio
async def test_login_valid(async_client, test_user):
    response = await async_client.post(
        "/auth/login",
        data={
            "username": test_user["email"],
            "password": test_user["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(async_client, test_user):
    response = await async_client.post(
        "/auth/login",
        data={
            "username": test_user["email"],
            "password": "WrongPassword999!",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_get_me_with_valid_token(async_client, test_user):
    response = await async_client.get(
        "/auth/me",
        headers=test_user["headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(test_user["user"].id)
    assert body["email"] == test_user["email"]
    assert body["username"] == test_user["user"].username


@pytest.mark.asyncio
async def test_get_me_with_no_token(async_client):
    response = await async_client.get("/auth/me")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me_with_expired_token(async_client, test_user):
    expired_token = create_access_token(
        {"sub": str(test_user["user"].id)},
        expires_delta=timedelta(minutes=-1),
    )

    response = await async_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(async_client, test_user):
    login_response = await async_client.post(
        "/auth/login",
        data={
            "username": test_user["email"],
            "password": test_user["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refresh_token"]

    response = await async_client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["refresh_token"] == refresh_token
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_generate_api_key(async_client, test_user):
    response = await async_client.post(
        "/auth/api-keys",
        json={
            "name": "pytest-generated-key",
            "scopes": ["papers:read", "analytics:read"],
        },
        headers=test_user["headers"],
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "pytest-generated-key"
    assert "key" in body
    assert isinstance(body["key"], str)
    assert len(body["key"]) > 10


@pytest.mark.asyncio
async def test_list_api_keys(async_client, test_user):
    create_response = await async_client.post(
        "/auth/api-keys",
        json={
            "name": "listable-key",
            "scopes": ["papers:read"],
        },
        headers=test_user["headers"],
    )
    assert create_response.status_code == 201

    response = await async_client.get(
        "/auth/api-keys",
        headers=test_user["headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1

    names = [item["name"] for item in body]
    assert "listable-key" in names

    first = body[0]
    assert "id" in first
    assert "name" in first
    assert "scopes" in first
    assert "created_at" in first
    assert "is_active" in first


@pytest.mark.asyncio
async def test_revoke_api_key(async_client, test_user):
    create_response = await async_client.post(
        "/auth/api-keys",
        json={
            "name": "revokable-key",
            "scopes": ["papers:read"],
        },
        headers=test_user["headers"],
    )
    assert create_response.status_code == 201

    list_response = await async_client.get(
        "/auth/api-keys",
        headers=test_user["headers"],
    )
    assert list_response.status_code == 200

    api_keys = list_response.json()
    api_key_id = next(item["id"] for item in api_keys if item["name"] == "revokable-key")

    revoke_response = await async_client.delete(
        f"/auth/api-keys/{api_key_id}",
        headers=test_user["headers"],
    )

    assert revoke_response.status_code == 204
    assert revoke_response.text == ""

    list_after = await async_client.get(
        "/auth/api-keys",
        headers=test_user["headers"],
    )
    assert list_after.status_code == 200

    updated_keys = list_after.json()
    revoked = next(item for item in updated_keys if item["id"] == api_key_id)
    assert revoked["is_active"] is False
