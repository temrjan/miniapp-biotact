"""S2: real migrated storage, future-shaped customers, and unchanged Telegram entry.

Only the explicit local disposable server from the S1 scratch helper is used.
Future-shape fixtures relax one column after migration, never product metadata.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src import database
from src.broadcasts import service as broadcasts
from src.broadcasts.models import Broadcast, BroadcastStatus
from src.config import settings
from src.crm.models import FamilyMember, Lead
from src.employee_referrals import count_store, delivery_store
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.notices import render
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.worker import readiness
from src.loyalty import backfill, tickets
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LedgerReason,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
    PointLedger,
)
from src.main import create_app
from src.notifications.service import telegram_notifier
from src.orders import service as orders
from src.orders.schemas import OrderCreate
from src.payments.service import multicard_client
from src.products.models import Product
from src.users.models import User
from src.users.schemas import UserProfileResponse
from tests import test_category_schema_migration as migration
from tests.test_employee_referral_k1_contract import KEY, PATH, WIRE, encoded, signed
from tests.test_payments_lifecycle import SECRET, _callback_body

scratch = migration.scratch


def admin_token(user_id=90, *, expired=False, kind="admin"):
    return jwt.encode(
        {"sub": str(user_id), "type": kind, "exp": int(time.time()) + (-60 if expired else 600)},
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )


@dataclass
class FutureCustomers:
    scratch: migration.ScratchDatabase
    sessions: async_sessionmaker
    client: AsyncClient
    headers: dict[str, str]


@pytest_asyncio.fixture
async def customers(scratch, monkeypatch):
    # Current code needs the current schema (G1 reads new order columns); S3 at head already
    # relaxes ``telegram_id``, so the statement below is kept only as a harmless no-op.
    await asyncio.to_thread(scratch.migrate, target="head")
    scratch.assert_native()
    scratch.execute("ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL")
    scratch.execute("""
        INSERT INTO users(id,telegram_id,first_name,language_code,is_active,is_admin,
                          created_at,updated_at)
        VALUES (10,990042,'Historic','ru',true,false,now(),now()),
               (11,NULL,'NullCustomer','uz',true,false,now()+interval '1 second',now()),
               (12,NULL,NULL,'ru',true,false,now()+interval '2 seconds',now()),
               (13,990013,'Inactive','ru',false,false,now()+interval '3 seconds',now()),
               (90,990090,'Admin','ru',true,true,now()+interval '4 seconds',now());
    """)
    scratch.execute("UPDATE users SET updated_at=now()+id*interval '1 second'")
    engine = create_async_engine(scratch.url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_maker", sessions)
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "bot_api_token", SecretStr("s2-synthetic-bot-api"))
    # Every tested background factory is pinned to this separate synthetic database.
    monkeypatch.setattr(broadcasts, "async_session_maker", sessions)
    app = create_app()
    app.state.employee_referral_sessions = sessions
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://s2.test") as c:
            yield FutureCustomers(
                scratch, sessions, c, {"Authorization": "Bearer " + admin_token()}
            )
    finally:
        await engine.dispose()


async def test_user_list_detail_patch_and_current_admin_guard(customers):
    h = customers
    result = await h.client.get("/api/v1/admin/users", headers=h.headers)
    assert result.status_code == 200, result.text
    assert result.json()["total"] == 5
    rows = {u["id"]: u for u in result.json()["items"]}
    assert rows[11]["telegram_id"] is None
    assert rows[12]["telegram_id"] is None
    assert rows[10]["telegram_id"] == 990042
    assert rows[11]["language_code"] == "uz"
    for user_id in (10, 11, 12):
        response = await h.client.get(f"/api/v1/admin/users/{user_id}", headers=h.headers)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == user_id
        assert response.json()["recent_orders"] == []
    response = await h.client.patch(
        "/api/v1/admin/users/11", json={"is_active": False}, headers=h.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["telegram_id"] is None
    assert response.json()["is_active"] is False
    assert response.json()["is_admin"] is False
    async with h.sessions() as db:
        user = await db.get(User, 12)
        profile = UserProfileResponse.from_user(user)
        assert profile.model_dump()["telegram_id"] is None
    for token in (None, admin_token(11, kind="buyer"), admin_token(expired=True)):
        headers = {"Authorization": "Bearer " + token} if token else {}
        response = await h.client.patch(
            "/api/v1/admin/users/12", json={"is_admin": True}, headers=headers
        )
        assert response.status_code in (401, 403)
    h.scratch.execute("UPDATE users SET is_admin=false WHERE id=90")
    response = await h.client.patch(
        "/api/v1/admin/users/12", json={"is_admin": True}, headers=h.headers
    )
    assert response.status_code == 403
    assert h.scratch.execute("SELECT is_admin,is_active FROM users WHERE id=12") == [(False, True)]


async def test_crm_preserves_ids_nested_leads_pages_filters_and_history(customers):
    h = customers
    async with h.sessions.begin() as db:
        db.add(FamilyMember(user_id=11, name="Family", symptoms=["gut"]))
        db.add(Lead(id=21, user_id=11, telegram_id=8811, request_text="Synthetic lead"))
        db.add(CustomerProfile(user_id=10, gender=Gender.MALE, city="City", age=30))
        db.add(PointLedger(user_id=10, amount=5500, reason=LedgerReason.CASHBACK))
    response = await h.client.get("/api/v1/crm/customers", headers=h.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5
    rows = {u["id"]: u for u in body["items"]}
    assert rows[11]["telegram_id"] is None
    assert rows[11]["family"][0]["name"] == "Family"
    assert rows[11]["loyalty"] is None
    assert rows[10]["loyalty"]["balance"] == 5500
    all_ids = []
    for page in range(1, 6):
        r = await h.client.get(f"/api/v1/crm/customers?size=1&page={page}", headers=h.headers)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 5
        assert r.json()["pages"] == 5
        all_ids.extend(u["id"] for u in r.json()["items"])
    assert sorted(all_ids) == [10, 11, 12, 13, 90]
    r = await h.client.get("/api/v1/crm/customers?search=NullCustomer", headers=h.headers)
    assert r.status_code == 200
    assert [u["id"] for u in r.json()["items"]] == [11]
    r = await h.client.get("/api/v1/crm/customers?club=member", headers=h.headers)
    assert r.status_code == 200
    assert [u["id"] for u in r.json()["items"]] == [10]
    r = await h.client.get("/api/v1/admin/crm/customers/11", headers=h.headers)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == 11
    assert r.json()["telegram_id"] is None
    assert r.json()["family_members"][0]["user_id"] == 11
    assert r.json()["leads"][0]["telegram_id"] == 8811
    r = await h.client.get("/api/v1/admin/crm/leads", headers=h.headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["customer"]["telegram_id"] is None
    r = await h.client.patch(
        "/api/v1/admin/crm/leads/21", json={"manager_notes": "Updated"}, headers=h.headers
    )
    assert r.status_code == 200
    assert r.json()["telegram_id"] == 8811
    for path, code in (
        ("/api/v1/crm/customer/990042", 200),
        ("/api/v1/crm/customer/11", 404),
        ("/api/v1/admin/crm/customers/9999", 404),
    ):
        r = await h.client.get(path, headers=h.headers)
        assert r.status_code == code, r.text
        if code == 200:
            assert r.json()["id"] == 10
    stats = await h.client.get("/api/v1/crm/customers/stats", headers=h.headers)
    assert stats.status_code == 200
    assert stats.json()["total_customers"] == 5
    assert (await h.client.get("/api/v1/analytics/dashboard", headers=h.headers)).status_code == 200


async def test_telegram_input_and_auth_contracts_do_not_accept_null(customers):
    h = customers
    for path in ("/api/v1/users/telegram", "/api/v1/bot/customer", "/api/v1/bot/lead"):
        response = await h.client.post(
            path,
            json={"telegram_id": None},
            headers={
                "X-Bot-Token": (
                    settings.telegram_bot_token
                    if path.startswith("/api/v1/users/")
                    else settings.bot_api_token.get_secret_value()
                )
            },
        )
        assert response.status_code == 422, response.text
    for path in ("/api/v1/users/me", "/api/v1/loyalty/me", "/api/v1/loyalty/my-tickets"):
        response = await h.client.get(path)
        assert response.status_code == 401, response.text
    response = await h.client.get(
        "/api/v1/users/telegram/990042", headers={"X-Bot-Token": settings.telegram_bot_token}
    )
    assert response.status_code == 200
    assert response.json()["id"] == 10
    assert h.scratch.execute("SELECT count(*) FROM users") == [(5,)]
    assert h.scratch.execute("SELECT count(*) FROM customer_profile") == [(0,)]


async def test_broadcast_recipient_filter_and_staff_notice_destination(customers, monkeypatch):
    h = customers
    async with h.sessions.begin() as db:
        db.add(Broadcast(id=1, title="Synthetic", text="Hello", status=BroadcastStatus.SENDING))
    send = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(broadcasts, "_send_to_user", send)
    monkeypatch.setattr(broadcasts, "RATE_LIMIT_DELAY", 0)
    await broadcasts.execute_broadcast(1)
    assert sorted(call.args[1] for call in send.call_args_list) == [990042, 990090]
    assert h.scratch.execute("SELECT total_users,sent_count FROM broadcasts WHERE id=1") == [(2, 2)]
    staff_messages = []

    def staff_send(request):
        staff_messages.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(telegram_notifier, "chat_id", "synthetic-staff")
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: AsyncClient(transport=httpx.MockTransport(staff_send), **kwargs),
    )
    await telegram_notifier.notify_order_created("S2-order", 100, [], customer_name=None)
    assert len(staff_messages) == 1
    assert staff_messages[0]["chat_id"] == "synthetic-staff"
    assert "Не указано" in staff_messages[0]["text"]
    assert "None" not in staff_messages[0]["text"]


async def test_no_profile_purchase_backfill_and_anomalous_print(customers):
    h = customers
    async with h.sessions.begin() as db:
        db.add(Product(id=31, name="Synthetic", slug="s2-product", price=Decimal("10000")))
    async with h.sessions.begin() as db:
        order = await orders.create_order(
            db, OrderCreate(items=[{"product_id": 31, "quantity": 1}]), 11
        )
        assert order.user_id == 11
        assert order.total == Decimal("10000")
        assert (await tickets.sync_tickets(db, user_id=11)).issued == ()
        report = await backfill.run_backfill(db)
        assert report.members_scanned == 0
    assert h.scratch.execute("SELECT count(*) FROM customer_profile") == [(0,)]
    assert h.scratch.execute("SELECT count(*) FROM point_ledger") == [(0,)]
    assert h.scratch.execute("SELECT count(*) FROM lottery_tickets") == [(0,)]
    async with h.sessions.begin() as db:
        db.add_all([CustomerProfile(user_id=10), CustomerProfile(user_id=11)])
        draw = LotteryDraw(
            id=1,
            draw_number=1,
            status=LotteryDrawStatus.COMMITTED.value,
            total_tickets=1,
            participant_hash="a" * 64,
        )
        db.add(draw)
        await db.flush()
        db.add(
            LotteryTicket(
                user_id=10, draw_id=1, ticket_number=1, source=LotteryTicketSource.SURVEY.value
            )
        )
    path = "/api/v1/admin/lottery/1/print"
    response = await h.client.get(path, headers=h.headers)
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["telegram_last4"] == "0042"
    async with h.sessions.begin() as db:
        db.add(
            LotteryTicket(
                user_id=11, draw_id=1, ticket_number=2, source=LotteryTicketSource.SURVEY.value
            )
        )
        draw = await db.get(LotteryDraw, 1)
        draw.total_tickets = 2
    before = h.scratch.execute(
        "SELECT user_id,draw_id,ticket_number FROM lottery_tickets ORDER BY id"
    )
    response = await h.client.get(path, headers=h.headers)
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": "Print list requires Telegram identity for every participant"
    }
    assert (
        h.scratch.execute("SELECT user_id,draw_id,ticket_number FROM lottery_tickets ORDER BY id")
        == before
    )
    response = await h.client.get("/api/v1/admin/crm/customers/11", headers=h.headers)
    assert response.status_code == 200
    assert response.json()["telegram_id"] is None
    h.scratch.execute("UPDATE lottery_draws SET status='cancelled' WHERE id=1")
    response = await h.client.get(path, headers=h.headers)
    assert response.status_code == 409
    assert "отменён" in response.json()["detail"]
    assert (
        await h.client.get("/api/v1/admin/lottery/999/print", headers=h.headers)
    ).status_code == 404


def test_pre_s3_schema_not_null_and_current_chain_in_parity(scratch):
    scratch.migrate()
    scratch.assert_native()
    assert scratch.execute(
        "SELECT attnotnull FROM pg_attribute WHERE attrelid='users'::regclass AND attname='telegram_id'"
    ) == [(True,)]
    with pytest.raises(psycopg2.errors.NotNullViolation):
        scratch.execute(
            "INSERT INTO users(telegram_id,language_code,is_active,is_admin) VALUES(NULL,'ru',true,false)"
        )
    # S2's NOT NULL promise ends at its exact revision; S3 intentionally relaxes it.
    scratch.migrate(target="head")
    assert scratch.execute(
        "SELECT attnotnull FROM pg_attribute WHERE attrelid='users'::regclass AND attname='telegram_id'"
    ) == [(False,)]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=migration.API_ROOT,
        env=dict(os.environ, DATABASE_URL=scratch.url),
        capture_output=True,
        text=True,
        check=False,
        timeout=100,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("user_id", [10, 11], ids=["historic", "absent-Telegram"])
@pytest.mark.skipif(
    not os.environ.get("S2_PORTAL_SOURCE"),
    reason="Explicit maintained portal source is needed for the cross-owner contract",
)
async def test_order_paid_return_worker_and_maintained_portal_contract(
    customers, monkeypatch, user_id
):
    h = customers
    from src.employee_referrals.schemas import utc_text

    monkeypatch.setattr(settings, "employee_referral_capture_enabled", True)
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", True)
    monkeypatch.setattr(settings, "employee_referral_k1_key", SecretStr(KEY.hex()))
    monkeypatch.setattr(multicard_client, "secret", SECRET)
    monkeypatch.setattr(telegram_notifier, "notify_payment_success", AsyncMock(return_value=True))
    async with h.sessions.begin() as db:
        db.add(
            Product(id=39, name="Synthetic campaign", slug="s2-campaign", price=Decimal("10000"))
        )
        now = await db.scalar(select(func.clock_timestamp()))
    data = json.loads(WIRE)
    data["issued_at"] = utc_text(now - timedelta(seconds=2))
    data["campaign"].update(
        published_at=utc_text(now - timedelta(days=1)),
        start_at=utc_text(now - timedelta(hours=1)),
        end_at=utc_text(now + timedelta(days=1)),
    )
    raw = encoded(data)
    response = await h.client.post(PATH, content=raw, headers=signed(raw))
    assert response.status_code == 200, response.text
    async with h.sessions.begin() as db:
        order = await orders.create_order(
            db,
            OrderCreate(
                items=[{"product_id": 39, "quantity": 2}],
                referral_token="AAAAAAAAAAAAAAAAAAAAAA",
                delivery_name="Synthetic buyer",
            ),
            user_id,
        )
        order_id, order_number = order.id, order.order_number
    callback = _callback_body(uuid=f"s2-{user_id}", invoice_id=order_number, amount=2000000)
    for _ in range(2):
        response = await h.client.post("/api/v1/payments/callback", json=callback)
        assert response.status_code == 200, response.text
    async with h.sessions() as db:
        root = (await db.scalars(select(PaidOutbox))).one()
        value = json.loads(root.body)
        assert value["order_id"] == order_id
        assert value["lines"][0]["quantity"] == 2
        item_id = value["lines"][0]["order_item_id"]
        assert await db.scalar(select(func.count()).select_from(CustomerProfile)) == 0
    intent = {
        "return_id": str(uuid4()),
        "lines": [{"order_item_id": str(item_id), "quantity": "1"}],
    }
    for _ in range(2):
        response = await h.client.post(
            f"/api/v1/admin/orders/{order_id}/referral-returns", json=intent, headers=h.headers
        )
        assert response.status_code == 200, response.text
    await readiness(h.sessions)
    claim = await delivery_store.claim_job(h.sessions, "notice")
    assert claim.notice.customer_name == "Synthetic buyer"
    assert "Synthetic buyer" in render(claim.notice)
    count = await count_store.observe(
        h.sessions, 1, now.astimezone(ZoneInfo("Asia/Tashkent")).date().isoformat()
    )
    assert count["source_state"] == "retained_valid"
    assert count["totals"] == {"paid_quantity": "2", "returned_quantity": "1", "net_quantity": "1"}
    async with h.sessions() as db:
        returned = (await db.scalars(select(ReturnOutbox))).one()
        payloads = [root.body.decode(), returned.body.decode()]
        assert await db.scalar(select(func.count()).select_from(K2Delivery)) == 2
        assert await db.scalar(select(func.count()).select_from(NoticeDelivery)) == 2
        assert await db.scalar(select(func.count()).select_from(PointLedger)) == 0
        assert await db.scalar(select(func.count()).select_from(LotteryTicket)) == 0
    portal = Path(os.environ["S2_PORTAL_SOURCE"])
    # Execute the actual maintained parser in its own declared environment, without service I/O.
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(portal / "backend/src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = await asyncio.to_thread(
        subprocess.run,
        [
            str(portal / "backend/.venv/bin/python"),
            "-c",
            "import json,sys; from team_app.referrals.k2_wire import parse_snapshot; "
            "from team_app.referrals.count_wire import Readback; "
            "v=json.load(sys.stdin); [parse_snapshot(p.encode()) for p in v['snapshots']]; "
            "Readback.model_validate(v['count']); print('actual portal paid/return/count accepted')",
        ],
        input=json.dumps({"snapshots": payloads, "count": count}),
        capture_output=True,
        text=True,
        env=env,
        cwd=migration.API_ROOT,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "actual portal paid/return/count accepted" in result.stdout
