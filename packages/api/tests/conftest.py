"""Pytest configuration and fixtures."""

import asyncio
import time
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database import Base, get_db
from src.main import app

# Test database URL
TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_db() -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    engine_test = create_async_engine(TEST_DATABASE_URL, echo=False)
    async_session_test = async_sessionmaker(
        engine_test, class_=AsyncSession, expire_on_commit=False
    )

    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_test() as session:
        yield session

    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine_test.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(test_db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test client with overridden database."""

    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Auth headers with admin JWT for protected endpoints."""
    payload = {
        "sub": "1",
        "type": "admin",
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(
        payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
    return {"Authorization": f"Bearer {token}"}
