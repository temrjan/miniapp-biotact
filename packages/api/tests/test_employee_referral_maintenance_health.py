"""Actual PostgreSQL aggregate probe, isolated from ordinary test schemas."""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg2
import pytest
import pytest_asyncio
from psycopg2 import sql
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.employee_referrals import worker
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.models import CampaignReplica
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.transport import LaneConfig


@pytest.fixture(scope="module")
def probe_database() -> Iterator[str]:
    base = settings.database_url.rsplit("/", 1)[0]
    name = "maintenance_probe_" + uuid4().hex
    connection = psycopg2.connect(base.replace("+asyncpg", "") + "/postgres")
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = base + "/" + name
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    yield url
    connection.close()


@pytest_asyncio.fixture
async def probe_sessions(probe_database: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(probe_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await session.execute(
            text("""TRUNCATE employee_referral_campaigns,
            employee_referral_tokens, employee_referral_k1_receipts,
            employee_referral_paid_outbox, employee_referral_k2_delivery,
            employee_referral_notice_delivery CASCADE""")
        )
        await session.execute(
            text("UPDATE alembic_version SET version_num=:head"),
            {"head": await asyncio.to_thread(worker.migration_head)},
        )
    yield sessions
    await engine.dispose()


def configs(enabled: bool = False) -> dict[worker.Lane, LaneConfig]:
    return {
        "k2": LaneConfig("true", "https://portal.example", "0" * 64) if enabled else LaneConfig(),
        "notice": LaneConfig("true", token="12345:synthetic", chat="-12345")
        if enabled
        else LaneConfig(),
    }


async def seed_jobs(sessions: async_sessionmaker[AsyncSession], lane: str, state: str) -> None:
    clock = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            PaidOutbox(
                id=1,
                order_id=1,
                event_id=uuid4(),
                body=b"{}",
                body_sha256="0" * 64,
                created_at=clock,
            )
        )
        await session.flush()
        for name, model in (("k2", K2Delivery), ("notice", NoticeDelivery)):
            actual = state if name == lane else "pending"
            fields = {
                "id": 1,
                "source_kind": "paid",
                "paid_id": 1,
                "state": actual,
                "attempt_count": 1 if actual == "leased" else 0,
                "generation": 1,
                "next_attempt_at": clock,
                "created_at": clock,
                "leased_until": clock + timedelta(seconds=30) if actual == "leased" else None,
                "error_category": "transport" if actual == "blocked" else None,
            }
            if name == "notice":
                fields.update(payload=b"{}", payload_sha256="0" * 64)
            session.add(model(**fields))


async def test_empty_disabled_probe_is_ready_and_runs_all_reads_in_readonly_transactions(
    probe_database: str, probe_sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Server-enforced readonly applies to the nested legacy health session as well.
    engine = create_async_engine(
        probe_database, connect_args={"server_settings": {"default_transaction_read_only": "on"}}
    )
    statements: list[str] = []
    event.listen(
        engine.sync_engine,
        "before_cursor_execute",
        lambda _connection, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    try:
        result, code = await worker.maintenance_health(
            async_sessionmaker(engine, expire_on_commit=False), configs()
        )
    finally:
        await engine.dispose()
    assert code == 0
    expected_head = await asyncio.to_thread(worker.migration_head)
    expected: dict[str, object] = {
        "confirmation_seconds": 300,
        "stored_confirmation_seconds": 0,
        "healthy": True,
        "schema": expected_head,
        "schema_ready": True,
        "leased": 0,
        "enabled": False,
        "blocked": 0,
        "ready": True,
    }
    if expected_head == "b7e31c904a62":
        expected["schema_compatibility"] = "s1-category-boolean-v1"
    assert result == expected
    assert any("SET TRANSACTION READ ONLY" in statement for statement in statements)
    assert all(statement.lstrip().split()[0] in {"SELECT", "SET"} for statement in statements)


async def test_schema_mismatch_is_not_ready(
    probe_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with probe_sessions.begin() as session:
        await session.execute(text("UPDATE alembic_version SET version_num='old-revision'"))
    result, code = await worker.maintenance_health(probe_sessions, configs())
    assert code == 1
    assert result["schema"] == "old-revision"
    assert result["schema_ready"] is False
    assert result["ready"] is False


async def test_invalid_config_remains_visible(
    probe_sessions: async_sessionmaker[AsyncSession],
) -> None:
    config = configs()
    config["k2"] = LaneConfig("invalid")
    result, code = await worker.maintenance_health(probe_sessions, config)
    assert code == 1
    assert result["healthy"] is False
    assert result["ready"] is False


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_retained_lease_prevents_safe_drain(
    probe_sessions: async_sessionmaker[AsyncSession], lane: str
) -> None:
    await seed_jobs(probe_sessions, lane, "leased")
    result, code = await worker.maintenance_health(probe_sessions, configs(True))
    assert code == 1
    assert result["leased"] == 1
    assert result["ready"] is False


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_blocked_lane_is_healthy_failure_even_with_clean_drain(
    probe_sessions: async_sessionmaker[AsyncSession], lane: str
) -> None:
    await seed_jobs(probe_sessions, lane, "blocked")
    result, code = await worker.maintenance_health(probe_sessions, configs(True))
    assert code == 0  # Drain readiness and delivery health are separate output fields.
    assert result["ready"] is True
    assert result["blocked"] == 1
    assert result["healthy"] is False


async def test_historical_ended_lifetimes_are_reported_without_rewriting(
    probe_sessions: async_sessionmaker[AsyncSession],
) -> None:
    clock = datetime(2026, 1, 2, tzinfo=UTC)
    async with probe_sessions.begin() as session:
        for campaign_id, seconds in ((1, 60), (2, 300)):
            session.add(
                CampaignReplica(
                    campaign_id=campaign_id,
                    content_revision=1,
                    product_id=39,
                    name="Historical synthetic campaign",
                    state="ended",
                    published_at=clock - timedelta(days=1),
                    start_at=clock - timedelta(days=1),
                    end_at=clock + timedelta(days=1),
                    ended_at=clock,
                    content_sha256="0" * 64,
                    issued_at=clock,
                    valid_until=clock + timedelta(seconds=seconds),
                    observed_at=clock,
                )
            )
    result, code = await worker.maintenance_health(probe_sessions, configs())
    assert code == 0
    assert result["confirmation_seconds"] == 300
    assert result["stored_confirmation_seconds"] == 300
    async with probe_sessions() as session:
        rows = list(
            await session.scalars(select(CampaignReplica).order_by(CampaignReplica.campaign_id))
        )
        assert [row.valid_until - row.issued_at for row in rows] == [
            timedelta(seconds=60),
            timedelta(seconds=300),
        ]
