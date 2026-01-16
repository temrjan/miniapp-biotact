"""Pytest configuration and fixtures."""

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.main import app
from src.database import Base, get_db
from src.config import settings


# Test database URL
TEST_DATABASE_URL = settings.database_url.replace("mistore_db", "mistore_test_db")


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
