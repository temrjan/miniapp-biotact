"""Audit S2 regression and behavioral tests for broadcasts."""

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_object_session,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src import database
from src.broadcasts import service as broadcasts_service
from src.broadcasts.models import Broadcast, BroadcastStatus
from src.config import settings
from src.loyalty.models import CustomerProfile
from src.main import app
from src.users.models import User
from tests.conftest import TEST_DATABASE_URL, _make_init_data


@dataclass
class BroadcastHarness:
    """Test harness with real per-request DB sessions against PostgreSQL."""

    client: AsyncClient
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    admin_headers: dict[str, str]


@pytest_asyncio.fixture
async def broadcast_env(
    test_db: AsyncSession,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[BroadcastHarness, None]:
    """Provide an HTTP client and session maker where each request commits in its own session."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, echo=False)
    sessions = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(database, "async_session_maker", sessions)
    monkeypatch.setattr(broadcasts_service, "async_session_maker", sessions)
    monkeypatch.setattr(broadcasts_service, "RATE_LIMIT_DELAY", 0.0)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield BroadcastHarness(
            client=ac,
            engine=engine,
            sessions=sessions,
            admin_headers=admin_headers,
        )

    await engine.dispose()


async def test_s2_invariant_1_cancel_during_sending_stops_and_preserves_cancelled(
    broadcast_env: BroadcastHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 1: cancel_broadcast during sending stops remaining sends, persists counters, and never overwrites CANCELLED with COMPLETED."""
    h = broadcast_env
    async with h.sessions.begin() as db:
        db.add_all(
            [
                User(id=101, telegram_id=990101, first_name="U1", is_active=True, is_admin=False),
                User(id=102, telegram_id=990102, first_name="U2", is_active=True, is_admin=False),
                Broadcast(
                    id=1, title="Mid-loop cancel", text="Msg 1", status=BroadcastStatus.SENDING
                ),
                Broadcast(
                    id=2, title="Last-user cancel", text="Msg 2", status=BroadcastStatus.SENDING
                ),
                Broadcast(
                    id=3,
                    title="Post-loop cancel race",
                    text="Msg 3",
                    status=BroadcastStatus.SENDING,
                ),
            ]
        )

    # Case A: cancel_broadcast commits from another session while sending to the 1st of 3 recipients.
    sent_mid: list[int] = []

    async def send_and_cancel_on_first(
        _client: httpx.AsyncClient,
        chat_id: int,
        broadcast: Broadcast,
    ) -> tuple[bool, str]:
        sent_mid.append(chat_id)
        if len(sent_mid) == 1:
            resp = await h.client.post(
                f"/api/v1/admin/broadcasts/{broadcast.id}/cancel",
                headers=h.admin_headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == BroadcastStatus.CANCELLED
        return True, ""

    monkeypatch.setattr(broadcasts_service, "_send_to_user", send_and_cancel_on_first)
    await broadcasts_service.execute_broadcast(1)

    assert len(sent_mid) == 1
    async with h.sessions() as db:
        b1 = await db.get(Broadcast, 1)
        assert b1 is not None
        assert b1.status == BroadcastStatus.CANCELLED
        assert b1.completed_at is None
        assert (b1.total_users, b1.sent_count, b1.blocked_count, b1.failed_count) == (3, 1, 0, 0)

    # Case B: cancel_broadcast commits from another session while the last recipient is being processed.
    sent_last: list[int] = []
    outcomes = [(True, ""), (False, "failed"), (False, "blocked")]

    async def send_and_cancel_on_last(
        _client: httpx.AsyncClient,
        chat_id: int,
        broadcast: Broadcast,
    ) -> tuple[bool, str]:
        idx = len(sent_last)
        sent_last.append(chat_id)
        if len(sent_last) == 3:
            resp = await h.client.post(
                f"/api/v1/admin/broadcasts/{broadcast.id}/cancel",
                headers=h.admin_headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == BroadcastStatus.CANCELLED
        return outcomes[idx]

    monkeypatch.setattr(broadcasts_service, "_send_to_user", send_and_cancel_on_last)
    await broadcasts_service.execute_broadcast(2)

    assert len(sent_last) == 3
    async with h.sessions() as db:
        b2 = await db.get(Broadcast, 2)
        assert b2 is not None
        assert b2.status == BroadcastStatus.CANCELLED
        assert b2.completed_at is None
        assert (b2.total_users, b2.sent_count, b2.blocked_count, b2.failed_count) == (3, 1, 1, 1)

    # Case C (F-01): cancel commits from another connection after the loop finishes and
    # immediately before the finalization UPDATE setting completed_at executes.
    sent_race: list[int] = []
    injected_cancel: list[str] = []
    sync_dsn = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    async def send_all_three(
        _client: httpx.AsyncClient,
        chat_id: int,
        _broadcast: Broadcast,
    ) -> tuple[bool, str]:
        idx = len(sent_race)
        sent_race.append(chat_id)
        return outcomes[idx]

    def inject_cancel_before_final_update(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "UPDATE broadcasts" in statement and "completed_at" in statement and not injected_cancel:
            with psycopg2.connect(sync_dsn) as conn, conn.cursor() as cur:
                cur.execute("UPDATE broadcasts SET status = 'cancelled' WHERE id = 3")
            with psycopg2.connect(sync_dsn) as conn, conn.cursor() as cur:
                cur.execute("SELECT status FROM broadcasts WHERE id = 3")
                row = cur.fetchone()
                assert row is not None
                injected_cancel.append(str(row[0]))

    monkeypatch.setattr(broadcasts_service, "_send_to_user", send_all_three)
    event.listen(h.engine.sync_engine, "before_cursor_execute", inject_cancel_before_final_update)
    try:
        await broadcasts_service.execute_broadcast(3)
    finally:
        event.remove(
            h.engine.sync_engine, "before_cursor_execute", inject_cancel_before_final_update
        )

    assert len(sent_race) == 3
    assert injected_cancel == ["cancelled"]
    async with h.sessions() as db:
        b3 = await db.get(Broadcast, 3)
        assert b3 is not None
        assert b3.status == BroadcastStatus.CANCELLED
        assert b3.completed_at is None
        assert (b3.total_users, b3.sent_count, b3.blocked_count, b3.failed_count) == (3, 1, 1, 1)


async def test_s2_invariant_2_cancelled_error_and_exception_mark_broadcast_cancelled(
    broadcast_env: BroadcastHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 2: asyncio.CancelledError and exceptions (including failed in-flight session tx) mark SENDING as CANCELLED and re-raise CancelledError."""
    h = broadcast_env
    async with h.sessions.begin() as db:
        db.add_all(
            [
                User(id=201, telegram_id=990201, first_name="U201", is_active=True, is_admin=False),
                Broadcast(id=1, title="Task cancel", text="Hello", status=BroadcastStatus.SENDING),
                Broadcast(
                    id=2, title="Broken tx error", text="Hello", status=BroadcastStatus.SENDING
                ),
            ]
        )

    # Case A: asyncio.CancelledError interrupts execute_broadcast while SENDING and is re-raised.
    entered = asyncio.Event()

    async def block_until_cancelled(
        _client: httpx.AsyncClient,
        _chat_id: int,
        _broadcast: Broadcast,
    ) -> tuple[bool, str]:
        entered.set()
        await asyncio.sleep(60)
        return True, ""

    monkeypatch.setattr(broadcasts_service, "_send_to_user", block_until_cancelled)
    task = asyncio.create_task(broadcasts_service.execute_broadcast(1))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with h.sessions() as db:
        b1 = await db.get(Broadcast, 1)
        assert b1 is not None
        assert b1.status == BroadcastStatus.CANCELLED
        assert b1.completed_at is None

    # Case B: Exception after an in-flight session operation aborts the current transaction.
    async def fail_with_aborted_session_tx(
        _client: httpx.AsyncClient,
        _chat_id: int,
        broadcast: Broadcast,
    ) -> tuple[bool, str]:
        active_session = async_object_session(broadcast)
        assert active_session is not None
        await active_session.execute(text("SELECT 1 / 0"))
        return True, ""

    monkeypatch.setattr(broadcasts_service, "_send_to_user", fail_with_aborted_session_tx)
    await broadcasts_service.execute_broadcast(2)

    async with h.sessions() as db:
        b2 = await db.get(Broadcast, 2)
        assert b2 is not None
        assert b2.status == BroadcastStatus.CANCELLED
        assert b2.completed_at is None


async def test_s2_invariant_3_stuck_sending_broadcast_recovers_while_active_send_blocks_concurrent(
    broadcast_env: BroadcastHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 3: orphaned SENDING rows recover to CANCELLED on read and send, while genuinely active sends refuse concurrent sends with 400."""
    h = broadcast_env
    started_ts = datetime(2026, 11, 6, 3, 0, 0, tzinfo=UTC)

    async with h.sessions.begin() as db:
        db.add_all(
            [
                User(id=301, telegram_id=990301, first_name="U301", is_active=True, is_admin=False),
                Broadcast(
                    id=1,
                    title="Orphaned get detail",
                    text="Body 1",
                    status=BroadcastStatus.SENDING,
                    total_users=10,
                    sent_count=4,
                    blocked_count=1,
                    failed_count=2,
                    started_at=started_ts,
                ),
                Broadcast(
                    id=2,
                    title="Orphaned list",
                    text="Body 2",
                    status=BroadcastStatus.SENDING,
                    total_users=8,
                    sent_count=3,
                    blocked_count=2,
                    failed_count=1,
                    started_at=started_ts,
                ),
            ]
        )

    # 1. GET /api/v1/admin/broadcasts/{id} recovers an orphaned SENDING row.
    detail_resp = await h.client.get("/api/v1/admin/broadcasts/1", headers=h.admin_headers)
    assert detail_resp.status_code == 200, detail_resp.text
    detail_data = detail_resp.json()
    assert detail_data["status"] == BroadcastStatus.CANCELLED
    assert detail_data["total_users"] == 10
    assert detail_data["sent_count"] == 4
    assert detail_data["blocked_count"] == 1
    assert detail_data["failed_count"] == 2
    assert datetime.fromisoformat(detail_data["started_at"]) == started_ts
    assert detail_data["completed_at"] is None

    # 2. Reset id=2 to orphaned SENDING and verify GET /api/v1/admin/broadcasts recovers it too.
    async with h.sessions.begin() as db:
        b2 = await db.get(Broadcast, 2)
        assert b2 is not None
        b2.status = BroadcastStatus.SENDING

    list_resp = await h.client.get("/api/v1/admin/broadcasts", headers=h.admin_headers)
    assert list_resp.status_code == 200, list_resp.text
    items_by_id = {item["id"]: item for item in list_resp.json()["items"]}
    assert items_by_id[2]["status"] == BroadcastStatus.CANCELLED
    assert (
        items_by_id[2]["total_users"],
        items_by_id[2]["sent_count"],
        items_by_id[2]["blocked_count"],
        items_by_id[2]["failed_count"],
    ) == (8, 3, 2, 1)
    assert datetime.fromisoformat(items_by_id[2]["started_at"]) == started_ts
    assert items_by_id[2]["completed_at"] is None

    # 3. Retrying a stuck SENDING broadcast itself via POST /api/v1/admin/broadcasts/{id}/send succeeds.
    async with h.sessions.begin() as db:
        db.add(
            Broadcast(
                id=3,
                title="Orphaned retry self",
                text="Body 3",
                status=BroadcastStatus.SENDING,
                total_users=5,
                sent_count=2,
                started_at=started_ts,
            )
        )

    async def fast_ok(
        _client: httpx.AsyncClient,
        _chat_id: int,
        _broadcast: Broadcast,
    ) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(broadcasts_service, "_send_to_user", fast_ok)
    retry_self_resp = await h.client.post(
        "/api/v1/admin/broadcasts/3/send",
        headers=h.admin_headers,
    )
    assert retry_self_resp.status_code == 200, retry_self_resp.text
    async with h.sessions() as db:
        b3 = await db.get(Broadcast, 3)
        assert b3 is not None
        assert b3.status == BroadcastStatus.COMPLETED
        assert (b3.total_users, b3.sent_count, b3.blocked_count, b3.failed_count) == (2, 2, 0, 0)

    # 4. Starting another broadcast via POST /api/v1/admin/broadcasts/{id}/send recovers an orphaned SENDING row and succeeds.
    async with h.sessions.begin() as db:
        db.add_all(
            [
                Broadcast(
                    id=4,
                    title="Orphaned blocker",
                    text="Body 4",
                    status=BroadcastStatus.SENDING,
                    total_users=9,
                    sent_count=5,
                    blocked_count=1,
                    failed_count=1,
                    started_at=started_ts,
                ),
                Broadcast(
                    id=5,
                    title="New broadcast",
                    text="Body 5",
                    status=BroadcastStatus.DRAFT,
                ),
            ]
        )

    send_other_resp = await h.client.post(
        "/api/v1/admin/broadcasts/5/send",
        headers=h.admin_headers,
    )
    assert send_other_resp.status_code == 200, send_other_resp.text
    async with h.sessions() as db:
        b4 = await db.get(Broadcast, 4)
        b5 = await db.get(Broadcast, 5)
        assert b4 is not None
        assert b5 is not None
        assert b4.status == BroadcastStatus.CANCELLED
        assert (b4.total_users, b4.sent_count, b4.blocked_count, b4.failed_count) == (9, 5, 1, 1)
        assert b4.started_at == started_ts
        assert b4.completed_at is None
        assert b5.status == BroadcastStatus.COMPLETED

    # 5. While a broadcast send IS active in the current process (both direct execute_broadcast and POST /send),
    # GET shows SENDING and concurrent POST /send is refused with 400.
    async with h.sessions.begin() as db:
        db.add_all(
            [
                Broadcast(
                    id=6, title="Active sending", text="Body 6", status=BroadcastStatus.SENDING
                ),
                Broadcast(
                    id=7, title="Concurrent draft", text="Body 7", status=BroadcastStatus.DRAFT
                ),
                Broadcast(id=8, title="Second draft", text="Body 8", status=BroadcastStatus.DRAFT),
            ]
        )

    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def hold_active_send(
        _client: httpx.AsyncClient,
        _chat_id: int,
        _broadcast: Broadcast,
    ) -> tuple[bool, str]:
        send_started.set()
        await release_send.wait()
        return True, ""

    monkeypatch.setattr(broadcasts_service, "_send_to_user", hold_active_send)
    active_task = asyncio.create_task(broadcasts_service.execute_broadcast(6))
    await send_started.wait()

    try:
        active_detail = await h.client.get("/api/v1/admin/broadcasts/6", headers=h.admin_headers)
        assert active_detail.status_code == 200, active_detail.text
        assert active_detail.json()["status"] == BroadcastStatus.SENDING

        concurrent_resp = await h.client.post(
            "/api/v1/admin/broadcasts/7/send",
            headers=h.admin_headers,
        )
        assert concurrent_resp.status_code == 400, concurrent_resp.text
        assert concurrent_resp.json()["detail"] == "Another broadcast is currently sending"
    finally:
        release_send.set()
        await active_task

    send_started.clear()
    release_send.clear()
    route_send_task = asyncio.create_task(
        h.client.post("/api/v1/admin/broadcasts/7/send", headers=h.admin_headers)
    )
    await send_started.wait()
    try:
        active_route_detail = await h.client.get(
            "/api/v1/admin/broadcasts/7", headers=h.admin_headers
        )
        assert active_route_detail.status_code == 200, active_route_detail.text
        assert active_route_detail.json()["status"] == BroadcastStatus.SENDING

        concurrent_route_resp = await h.client.post(
            "/api/v1/admin/broadcasts/8/send",
            headers=h.admin_headers,
        )
        assert concurrent_route_resp.status_code == 400, concurrent_route_resp.text
        assert concurrent_route_resp.json()["detail"] == "Another broadcast is currently sending"
    finally:
        release_send.set()
        route_resp = await route_send_task
        assert route_resp.status_code == 200, route_resp.text

    # 6. Handoff window (T-01): between send_broadcast committing SENDING and background
    # execute_broadcast starting, GET list/detail must NOT falsely recover the fresh SENDING
    # row to CANCELLED, and the broadcast must complete sending to all active recipients.
    async with h.sessions.begin() as db:
        db.add(Broadcast(id=9, title="Handoff window", text="Body 9", status=BroadcastStatus.DRAFT))

    handoff_sent: list[int] = []
    handoff_list_status: list[str] = []
    handoff_detail_status: list[str] = []

    async def record_send(
        _client: httpx.AsyncClient,
        chat_id: int,
        _broadcast: Broadcast,
    ) -> tuple[bool, str]:
        handoff_sent.append(chat_id)
        return True, ""

    real_execute_broadcast = broadcasts_service.execute_broadcast

    async def execute_after_handoff_polls(broadcast_id: int) -> None:
        list_poll = await h.client.get("/api/v1/admin/broadcasts", headers=h.admin_headers)
        assert list_poll.status_code == 200, list_poll.text
        items_map = {item["id"]: item for item in list_poll.json()["items"]}
        handoff_list_status.append(items_map[broadcast_id]["status"])

        detail_poll = await h.client.get(
            f"/api/v1/admin/broadcasts/{broadcast_id}",
            headers=h.admin_headers,
        )
        assert detail_poll.status_code == 200, detail_poll.text
        handoff_detail_status.append(detail_poll.json()["status"])

        await real_execute_broadcast(broadcast_id)

    monkeypatch.setattr(broadcasts_service, "_send_to_user", record_send)
    monkeypatch.setattr(broadcasts_service, "execute_broadcast", execute_after_handoff_polls)

    handoff_resp = await h.client.post("/api/v1/admin/broadcasts/9/send", headers=h.admin_headers)
    assert handoff_resp.status_code == 200, handoff_resp.text
    assert handoff_list_status == [BroadcastStatus.SENDING]
    assert handoff_detail_status == [BroadcastStatus.SENDING]
    assert len(handoff_sent) == 2

    async with h.sessions() as db:
        b9 = await db.get(Broadcast, 9)
        assert b9 is not None
        assert b9.status == BroadcastStatus.COMPLETED
        assert (b9.total_users, b9.sent_count, b9.blocked_count, b9.failed_count) == (2, 2, 0, 0)


async def test_s2_invariant_4_blocked_bot_403_increments_blocked_count_without_deactivating_user(
    broadcast_env: BroadcastHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 4: Telegram 403 increments blocked_count and leaves user.is_active=True so Mini App and loyalty access continue working."""
    h = broadcast_env
    monkeypatch.setattr(settings, "loyalty_enabled", True)

    async with h.sessions.begin() as db:
        buyer = User(
            id=401,
            telegram_id=990403,
            first_name="BlockedBotBuyer",
            is_active=True,
            is_admin=False,
        )
        db.add(buyer)
        await db.flush()
        db.add(CustomerProfile(user_id=401))
        db.add(
            Broadcast(id=1, title="Promo", text="Hello **world**", status=BroadcastStatus.SENDING)
        )

    def telegram_403_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["chat_id"] == 990403:
            return httpx.Response(
                403,
                json={
                    "ok": False,
                    "error_code": 403,
                    "description": "Forbidden: bot was blocked by the user",
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {}})

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(telegram_403_handler),
            **kwargs,
        ),
    )

    await broadcasts_service.execute_broadcast(1)

    async with h.sessions() as db:
        b1 = await db.get(Broadcast, 1)
        assert b1 is not None
        assert b1.status == BroadcastStatus.COMPLETED
        assert (b1.total_users, b1.sent_count, b1.blocked_count, b1.failed_count) == (2, 1, 1, 0)

        user = await db.scalar(select(User).where(User.id == 401))
        assert user is not None
        assert user.is_active is True

    init_data = _make_init_data(
        settings.telegram_bot_token,
        {"id": 990403, "first_name": "BlockedBotBuyer", "language_code": "ru"},
    )
    buyer_headers = {"X-Init-Data": init_data}

    me_resp = await h.client.get("/api/v1/users/me", headers=buyer_headers)
    assert me_resp.status_code == 200, me_resp.text
    assert me_resp.json()["id"] == 401
    assert me_resp.json()["is_active"] is True

    loyalty_resp = await h.client.get("/api/v1/loyalty/me", headers=buyer_headers)
    assert loyalty_resp.status_code == 200, loyalty_resp.text
