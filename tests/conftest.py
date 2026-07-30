# tests/conftest.py
from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

# app.database constructs its engine during import. The test environment must
# therefore be configured before importing app.database, app.main, models, or
# services.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    (
        "postgresql+asyncpg://"
        "sguser:password@localhost:5432/scholargraph_test"
    ),
)

# The test suite drops schemas and truncates tables. Prevent it from ever
# pointing at the development or production database.
if "scholargraph_test" not in TEST_DATABASE_URL:
    raise RuntimeError(
        "Refusing to run destructive tests unless TEST_DATABASE_URL points "
        "to the dedicated scholargraph_test database."
    )

# Force application imports to use the isolated test database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

os.environ.setdefault(
    "SECRET_KEY",
    "pytest-only-secret-key-that-must-never-be-used-in-production",
)
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
os.environ.setdefault("ENVIRONMENT", "test")

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.database import Base, get_db
from app.main import app
from models.annotation import Annotation  # noqa: F401
from models.author import Author, PaperAuthor  # noqa: F401
from models.citation import Citation  # noqa: F401
from models.paper import Paper
from models.user import APIKey, User  # noqa: F401
from services.auth import hash_password


@pytest.fixture(scope="session")
def test_engine():
    """
    Create one SQLAlchemy engine for the dedicated test database.

    NullPool prevents asyncpg connections from being reused across incompatible
    event loops or teardown boundaries.
    """
    return create_async_engine(
        TEST_DATABASE_URL,
        future=True,
        poolclass=NullPool,
    )


@pytest_asyncio.fixture(
    scope="session",
    loop_scope="session",
    autouse=True,
)
async def prepare_test_database(test_engine):
    """
    Enable pgvector and create all ORM tables at the beginning of the suite.

    Tables are removed after the suite finishes. The database itself remains
    available for future test runs.
    """
    async with test_engine.begin() as conn:
        await conn.execute(
            text("CREATE EXTENSION IF NOT EXISTS vector")
        )
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        await test_engine.dispose()


async def _truncate_all_tables(engine) -> None:
    """
    Remove all rows between tests while preserving the test schema.
    """
    table_names = [
        table.name
        for table in reversed(Base.metadata.sorted_tables)
    ]

    if not table_names:
        return

    joined = ", ".join(
        f'"{name}"'
        for name in table_names
    )

    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"TRUNCATE TABLE {joined} "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest_asyncio.fixture(autouse=True)
async def clean_db(
    test_engine,
    prepare_test_database,
):
    """
    Reset all test data before and after each test.
    """
    await _truncate_all_tables(test_engine)

    yield

    await _truncate_all_tables(test_engine)


@pytest.fixture
def session_factory(test_engine):
    """
    Return an async-session factory bound to the test database.
    """
    return async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def test_db(
    session_factory,
) -> AsyncSession:
    """
    Provide a direct database session for test data and assertions.
    """
    async with session_factory() as session:
        yield session
        await session.close()


@pytest_asyncio.fixture
async def async_client(
    session_factory,
) -> httpx.AsyncClient:
    """
    Provide an HTTP client connected directly to the FastAPI application.

    Each request receives a new AsyncSession, matching the behaviour of the
    production get_db dependency.
    """

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(
    async_client: httpx.AsyncClient,
    test_db: AsyncSession,
) -> dict:
    """
    Create a test user and authenticate through the real login endpoint.
    """
    unique = uuid.uuid4().hex[:8]

    email = f"testuser_{unique}@example.com"
    username = f"testuser_{unique}"
    password = "TestPassword123!"

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        is_active=True,
    )

    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    response = await async_client.post(
        "/auth/login",
        data={
            "username": email,
            "password": password,
        },
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 200, response.text

    token = response.json()["access_token"]

    return {
        "user": user,
        "email": email,
        "password": password,
        "token": token,
        "headers": {
            "Authorization": f"Bearer {token}",
        },
    }


@pytest_asyncio.fixture
async def test_paper(
    test_db: AsyncSession,
) -> Paper:
    """
    Create one paper with deterministic test data.
    """
    paper = Paper(
        arxiv_id=f"9999.{uuid.uuid4().hex[:5]}",
        title="Test Paper on Transformer Attention",
        abstract=(
            "This is a known test abstract about transformers, "
            "attention mechanisms, semantic retrieval, and "
            "citation analytics."
        ),
        published_date=date(2024, 1, 15),
        updated_date=datetime(
            2024,
            1,
            16,
            12,
            0,
            0,
            tzinfo=UTC,
        ),
        primary_category="cs.AI",
        all_categories=[
            "cs.AI",
            "cs.LG",
        ],
        pdf_url="https://arxiv.org/pdf/9999.99999.pdf",
        pagerank_score=0.123456,
        abstract_embedding=[0.001] * 384,
    )

    test_db.add(paper)
    await test_db.commit()
    await test_db.refresh(paper)

    return paper


@pytest_asyncio.fixture
async def test_api_key(
    async_client: httpx.AsyncClient,
    test_user: dict,
) -> dict:
    """
    Create an API key using the real API-key endpoint.
    """
    response = await async_client.post(
        "/auth/api-keys",
        json={
            "name": f"pytest-key-{uuid.uuid4().hex[:8]}",
            "scopes": [
                "papers:read",
                "analytics:read",
            ],
        },
        headers=test_user["headers"],
    )

    assert response.status_code == 201, response.text

    body = response.json()
    raw_key = body["key"]

    return {
        "raw_key": raw_key,
        "response": body,
        "headers": {
            "X-API-Key": raw_key,
        },
    }