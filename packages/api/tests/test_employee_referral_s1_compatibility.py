"""Retained S1 schema must remain readable by the prepared predecessor worker."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.employee_referrals import worker
from tests.test_employee_referral_maintenance_health import configs
from tests.test_employee_referral_maintenance_health import probe_database as probe_database
from tests.test_employee_referral_maintenance_health import probe_sessions as probe_sessions


@pytest_asyncio.fixture
async def retained_schema(
    probe_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with probe_sessions.begin() as session:
        await session.execute(
            text(
                "ALTER TABLE public.categories ALTER COLUMN is_purchasable SET DEFAULT true, "
                "ALTER COLUMN is_purchasable SET NOT NULL"
            )
        )
        await session.execute(text("UPDATE alembic_version SET version_num='b7e31c904a62'"))
    yield probe_sessions


async def test_prepared_predecessor_reads_retained_s1_schema(
    retained_schema: async_sessionmaker[AsyncSession],
) -> None:
    # This same call returns schema_ready=False before the correction.
    assert worker.migration_head() == "a8f6d2c93017"
    result, code = await worker.maintenance_health(retained_schema, configs())
    assert code == 0
    assert result["schema_ready"] is True
    assert result["ready"] is True
    assert result["schema"] == "b7e31c904a62"
    assert result["schema_compatibility"] == "s1-category-boolean-v1"


@pytest.mark.parametrize(
    "corruption",
    [
        "ALTER TABLE categories ALTER COLUMN is_purchasable DROP NOT NULL",
        "ALTER TABLE categories ALTER COLUMN is_purchasable SET DEFAULT false",
        "ALTER TABLE categories ALTER COLUMN is_purchasable DROP DEFAULT",
        "ALTER TABLE categories ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE categories RENAME COLUMN is_purchasable TO wrong_flag",
    ],
)
async def test_retained_marker_does_not_hide_native_drift(
    retained_schema: async_sessionmaker[AsyncSession], corruption: str
) -> None:
    async with retained_schema.begin() as session:
        await session.execute(text(corruption))
    try:
        result, code = await worker.maintenance_health(retained_schema, configs())
        assert code == 1
        assert result["schema_ready"] is False
        assert result["ready"] is False
        assert "schema_compatibility" not in result
    finally:
        async with retained_schema.begin() as session:
            if "RENAME" in corruption:
                await session.execute(
                    text("ALTER TABLE categories RENAME COLUMN wrong_flag TO is_purchasable")
                )
            await session.execute(text("ALTER TABLE categories DISABLE ROW LEVEL SECURITY"))
            await session.execute(
                text(
                    "ALTER TABLE categories ALTER COLUMN is_purchasable SET DEFAULT true, "
                    "ALTER COLUMN is_purchasable SET NOT NULL"
                )
            )


async def test_same_head_b7_requires_native_invariant(
    retained_schema: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "migration_head", lambda: "b7e31c904a62")
    result, code = await worker.maintenance_health(retained_schema, configs())
    assert code == 0
    assert result["schema_ready"] is True
    async with retained_schema.begin() as session:
        await session.execute(
            text("ALTER TABLE categories ALTER COLUMN is_purchasable DROP NOT NULL")
        )
    result, code = await worker.maintenance_health(retained_schema, configs())
    assert code == 1
    assert result["schema_ready"] is False


@pytest.mark.parametrize(
    ("expected", "live"), [("a8f6d2c93017", "unknown"), ("unknown", "b7e31c904a62")]
)
async def test_other_head_pairs_still_refuse(
    retained_schema: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    expected: str,
    live: str,
) -> None:
    monkeypatch.setattr(worker, "migration_head", lambda: expected)
    async with retained_schema.begin() as session:
        await session.execute(
            text("UPDATE alembic_version SET version_num=:value"), {"value": live}
        )
    result, code = await worker.maintenance_health(retained_schema, configs())
    assert code == 1
    assert result["schema_ready"] is False


async def test_multiple_heads_refuse(retained_schema: async_sessionmaker[AsyncSession]) -> None:
    async with retained_schema.begin() as session:
        await session.execute(text("INSERT INTO alembic_version VALUES ('unknown')"))
    try:
        result, code = await worker.maintenance_health(retained_schema, configs())
        assert code == 1
        assert result["schema_ready"] is False
    finally:
        async with retained_schema.begin() as session:
            await session.execute(text("DELETE FROM alembic_version WHERE version_num='unknown'"))


@pytest.mark.parametrize("layout", ["missing", "wrong_type", "inherited"])
async def test_unsupported_layout_refuses(
    retained_schema: async_sessionmaker[AsyncSession], layout: str
) -> None:
    async with retained_schema.begin() as session:
        if layout == "missing":
            await session.execute(text("ALTER TABLE categories RENAME TO recovery_categories"))
        elif layout == "inherited":
            await session.execute(
                text("CREATE TABLE recovery_category_child () INHERITS (categories)")
            )
        else:
            await session.execute(
                text("ALTER TABLE categories ALTER COLUMN is_purchasable DROP DEFAULT")
            )
            await session.execute(
                text(
                    "ALTER TABLE categories ALTER COLUMN is_purchasable TYPE text USING is_purchasable::text"
                )
            )
    try:
        result, code = await worker.maintenance_health(retained_schema, configs())
        assert code == 1
        assert result["schema_ready"] is False
    finally:
        async with retained_schema.begin() as session:
            if layout == "missing":
                await session.execute(text("ALTER TABLE recovery_categories RENAME TO categories"))
            elif layout == "inherited":
                await session.execute(text("DROP TABLE recovery_category_child"))
            else:
                await session.execute(
                    text(
                        "ALTER TABLE categories ALTER COLUMN is_purchasable TYPE boolean USING is_purchasable::boolean"
                    )
                )
                await session.execute(
                    text("ALTER TABLE categories ALTER COLUMN is_purchasable SET DEFAULT true")
                )
