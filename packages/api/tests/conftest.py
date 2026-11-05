"""Pytest configuration and fixtures."""

import asyncio
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncGenerator
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database import Base, get_db
from src.main import app
from src.users.models import User

# Test database URL
TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


def _make_init_data(bot_token: str, user: dict[str, object]) -> str:
    """Build a validly-signed Telegram initData string (mirrors validate_init_data's HMAC).

    Shared across the loyalty test modules (activation/summary/consent) — imported, not copied.
    """
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHtest",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_schema() -> AsyncGenerator[None, None]:
    """Build the test schema once per session and drop it when the session ends.

    The initial ``drop_all`` removes tables that an interrupted earlier run left behind, so the
    session always starts from the current models. The engine lives in the session loop only;
    tests use their own engine from ``test_db``.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _truncate_all_tables(connection: Connection) -> None:
    """Empty every model table and restart the sequences that its columns own."""
    preparer = connection.dialect.identifier_preparer
    tables = ", ".join(preparer.format_table(table) for table in Base.metadata.sorted_tables)
    connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")


@pytest_asyncio.fixture(scope="function")
async def test_db(db_schema: None) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session on the session schema; empty all tables afterwards."""
    engine_test = create_async_engine(TEST_DATABASE_URL, echo=False)
    async_session_test = async_sessionmaker(
        engine_test, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_test() as session:
        yield session

    async with engine_test.begin() as conn:
        await conn.run_sync(_truncate_all_tables)

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
def prod_timezone():
    """Reproduce the APPLICATION SERVER's timezone on prod (Europe/Berlin).

    Without this the naive-datetime defect is not reproducible at all. Mechanism, measured
    empirically (PR-6.5-B): writing a NAIVE ``datetime`` into a ``timestamptz`` column makes the
    driver treat it as local time of the PROCESS, not as UTC. Observed drift: TZ=UTC -> 0h,
    TZ=Europe/Berlin -> +2h, TZ=Asia/Tashkent -> +5h. The PostgreSQL session timezone does NOT
    affect it — which is why the original diagnosis ("PG reads it as Berlin time") was wrong.

    Consequence: under ``TZ=UTC`` — exactly how the CI runner runs — a naive write and an aware one
    land identically, so a test for this bug stays green against broken code. The fixture sets the
    zone for the duration of one test and restores whatever was there before.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest_asyncio.fixture
async def admin_headers(test_db: AsyncSession) -> dict[str, str]:
    """A real committed administrator, separate from customer fixtures."""
    test_db.add(User(id=900001, telegram_id=990900001, is_admin=True, is_active=True))
    await test_db.commit()
    payload = {
        "sub": "900001",
        "type": "admin",
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(
        payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
    return {"Authorization": f"Bearer {token}"}
