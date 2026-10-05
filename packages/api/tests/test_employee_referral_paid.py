"""Paid count facts through real mounted checkout/callback and PostgreSQL sessions."""

import asyncio
import hashlib
import json
import logging
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sqlalchemy import delete, event, func, select
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.employee_referrals import paid
from src.employee_referrals.models import (
    CampaignReplica,
    ReferralIdentity,
    ReferralOrderItemSnapshot,
    ReferralOrderSnapshot,
)
from src.employee_referrals.order_privacy import filter_referral_telemetry
from src.employee_referrals.paid_models import PaidOutbox
from src.loyalty import service as loyalty
from src.loyalty.models import CustomerProfile, PointLedger
from src.notifications.service import telegram_notifier
from src.orders.models import Order
from src.payments.models import PaymentEvent
from src.payments.service import multicard_client
from src.products.models import Product
from tests import test_employee_referral_order_capture as capture_tests
from tests.test_employee_referral_k1_receiver import Captured
from tests.test_payments_lifecycle import SECRET, _callback_body, _webhook_body

checkout = capture_tests.checkout

CALLBACK = "/api/v1/payments/callback"
WEBHOOK = "/api/v1/payments/webhook"


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(multicard_client, "secret", SECRET)


async def facts(h):
    async with h.sessions() as session:
        return list(await session.scalars(select(PaidOutbox).order_by(PaidOutbox.id)))


async def callback(h, order, uuid="count-success", **changes):
    amount = int((Decimal(str(order["total"])) - order["points_applied"]) * 100)
    body = _callback_body(uuid=uuid, invoice_id=order["order_number"], amount=amount, **changes)
    return await h.client.post(CALLBACK, json=body)


@pytest.mark.parametrize("points", [0, 10000, 50000])
async def test_all_methods_count_retained_campaign_units_once(checkout, monkeypatch, points):
    h = checkout
    response = await h.create(
        h.body(
            items=[
                {"product_id": 39, "quantity": 2},
                {"product_id": 40, "quantity": 1},
                {"product_id": 39, "quantity": 1},
            ],
            points_to_apply=points,
        )
    )
    assert response.status_code == 201, response.text
    order = response.json()
    if points != 50000:
        assert await facts(h) == []
        async with h.sessions.begin() as session:
            campaign = await session.get(CampaignReplica, 1)
            campaign.state = "ended"
            campaign.ended_at = h.now
            identity = await session.get(ReferralIdentity, "AAAAAAAAAAAAAAAAAAAAAA")
            identity.in_current_registry = False
            await session.execute(delete(Product).where(Product.id == 39))
        monkeypatch.setattr(settings, "employee_referral_capture_enabled", False)
        assert (await callback(h, order)).status_code == 200
        assert (await callback(h, order)).status_code == 200
        assert (await callback(h, order, uuid="another-success")).status_code == 200
    rows = await facts(h)
    assert len(rows) == 1
    row = rows[0]
    body = json.loads(row.body)
    assert row.body_sha256 == hashlib.sha256(row.body).hexdigest()
    assert body["order_id"] == order["id"]
    assert body["source_position"] == row.id
    assert body["attribution"]["campaign_id"] == 1
    assert body["attribution"]["employee_code"] == h.data["participants"][0]["employee_code"]
    assert [line["quantity"] for line in body["lines"]] == [2, 1]
    assert sum(line["quantity"] for line in body["lines"]) == 3
    assert set(body) == {
        "schema_version",
        "event_id",
        "event_type",
        "source",
        "order_id",
        "order_version",
        "source_position",
        "source_recorded_at",
        "occurred_at",
        "attribution",
        "lines",
    }
    assert all(set(line) == {"order_item_id", "product_id", "quantity"} for line in body["lines"])
    async with h.sessions.begin() as session:
        retained = await session.get(Order, order["id"], with_for_update=True)
        same = await paid.record_paid(session, retained)
        assert same.body == row.body
        assert same.event_id == row.event_id
        if points == 0:
            await session.execute(delete(Order).where(Order.id == order["id"]))
    assert (await facts(h))[0].body == row.body


@pytest.mark.parametrize("status", ["progress", "hold", "draft", "success", "error"])
async def test_non_applied_events_never_produce_count(checkout, status):
    h = checkout
    order = (await h.create()).json()
    async with h.sessions.begin() as session:
        row = await session.get(Order, order["id"])
        row.payment_uuid = "ordinary-event"
    body = _webhook_body(
        uuid="ordinary-event", invoice_id=order["order_number"], amount=1000000, status=status
    )
    assert (await h.client.post(WEBHOOK, json=body)).status_code == 200
    assert await facts(h) == []
    if status == "error":
        assert (await callback(h, order)).status_code == 200
        assert await facts(h) == []


async def test_bad_signature_amount_admin_edit_and_ordinary_legacy(checkout):
    h = checkout
    order = (await h.create()).json()
    assert (await callback(h, order, sign="bad")).status_code == 400
    body = _callback_body(uuid="wrong-amount", invoice_id=order["order_number"], amount=1)
    assert (await h.client.post(CALLBACK, json=body)).status_code == 400
    async with h.sessions.begin() as session:
        row = await session.get(Order, order["id"])
        row.payment_status = "paid"
    assert (await callback(h, order)).status_code == 200
    assert await facts(h) == []
    ordinary = (await h.create(h.body(referral_token=None))).json()
    assert (await callback(h, ordinary, uuid="ordinary")).status_code == 200
    assert await facts(h) == []


async def test_paid_counts_even_existing_missing_club_debit(checkout):
    h = checkout
    order = (await h.create(h.body(points_to_apply=5000))).json()
    async with h.sessions.begin() as session:
        await session.execute(delete(PointLedger))
        await session.execute(delete(CustomerProfile))
    assert (await callback(h, order)).status_code == 200
    assert len(await facts(h)) == 1
    async with h.sessions() as session:
        assert await loyalty.get_balance(session, 1) == 0
        assert (await session.get(Order, order["id"])).payment_status == "paid"


async def test_same_order_concurrent_callbacks_and_telegram_failure(checkout, monkeypatch):
    h = checkout
    order = (await h.create()).json()
    monkeypatch.setattr(
        telegram_notifier,
        "notify_payment_success",
        AsyncMock(side_effect=RuntimeError("external send")),
    )
    replies = await asyncio.gather(callback(h, order), callback(h, order))
    assert [r.status_code for r in replies] == [200, 200]
    assert len(await facts(h)) == 1
    async with h.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PaymentEvent)) == 1
        assert (await session.get(Order, order["id"])).payment_status == "paid"


@pytest.mark.parametrize("stage", ["insert", "commit", "cancel"])
@pytest.mark.parametrize("points", [0, 10000])
async def test_paid_write_commit_cancel_atomicity(checkout, stage, points):
    h = checkout
    order = (await h.create()).json() if points == 0 else None

    def before(connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO employee_referral_paid_outbox"):
            connection.info["paid_insert"] = True
            if stage == "cancel":
                raise asyncio.CancelledError()
            if stage == "insert":
                raise SQLAlchemyError("PRIVATE_PAID_SQL_CANARY")

    def commit(connection):
        if connection.info.pop("paid_insert", False) and stage == "commit":
            raise SQLAlchemyError("PRIVATE_PAID_COMMIT_CANARY")

    event.listen(h.engine.sync_engine, "before_cursor_execute", before)
    event.listen(h.engine.sync_engine, "commit", commit)
    try:
        if stage == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await callback(h, order) if points == 0 else await h.create(
                    h.body(points_to_apply=points)
                )
        else:
            response = (
                await callback(h, order)
                if points == 0
                else await h.create(h.body(points_to_apply=points))
            )
            assert response.status_code == 503
            assert response.json() == {"detail": {"code": "referral_paid_unavailable"}}
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", before)
        event.remove(h.engine.sync_engine, "commit", commit)
    assert await facts(h) == []
    async with h.sessions() as session:
        assert await loyalty.get_balance(session, 1) == 100000
        assert await session.scalar(select(func.count()).select_from(PaymentEvent)) == 0
        if points == 0:
            assert (await session.get(Order, order["id"])).payment_status == "unpaid"
        else:
            assert await session.scalar(select(func.count()).select_from(Order)) == 0


async def test_paid_private_failure_and_echo_guard(checkout, monkeypatch, caplog):
    h = checkout
    order = (await h.create()).json()
    caplog.set_level(logging.DEBUG)
    marker = "PRIVATE_PAID_BODY_SQL_CANARY"
    original = paid.freeze_body

    def fail(*args, **kwargs):
        logging.getLogger("sqlalchemy.engine.Engine").error(marker, exc_info=True)
        raise ValueError(marker)

    monkeypatch.setattr(paid, "freeze_body", fail)
    response = await callback(h, order)
    assert response.status_code == 503
    assert marker not in response.text + caplog.text
    logging.getLogger("unrelated").warning("PAID_POSITIVE_CONTROL")
    assert "PAID_POSITIVE_CONTROL" in caplog.text
    assert filter_referral_telemetry({"tags": {"biotact_referral_paid": "yes"}}, {}) is None
    assert filter_referral_telemetry({"message": "PAID_POSITIVE_CONTROL"}, {}) is not None
    monkeypatch.setattr(paid, "freeze_body", original)
    monkeypatch.setattr(settings, "database_echo", True)
    assert (await callback(h, order)).status_code == 503
    assert await facts(h) == []


async def test_paid_sdk_privacy_preserves_ordinary_payment(checkout, monkeypatch):
    h = checkout
    retained = (await h.create()).json()
    ordinary = (await h.create(h.body(referral_token=None))).json()
    captured = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=captured,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )

    async def notified(_db, order_id, **_kwargs):
        # Capture after the paid helper and commit have both returned.
        marker = (
            "PRIVATE_PAID_LATE_CANARY" if order_id == retained["id"] else "ORDINARY_PAID_CONTROL"
        )
        sentry_sdk.capture_message(marker)

    monkeypatch.setattr("src.payments.router._notify_paid", notified)
    try:
        assert (await callback(h, retained)).status_code == 200
        sentry_sdk.flush()
        assert not captured.events
        assert (await callback(h, ordinary, uuid="ordinary-sdk")).status_code == 200
        sentry_sdk.flush()
        assert any(e.get("message") == "ORDINARY_PAID_CONTROL" for e in captured.events)
        assert any(e.get("type") == "transaction" for e in captured.events)
        assert "PRIVATE_PAID_LATE_CANARY" not in json.dumps(captured.events)
        assert len(await facts(h)) == 1
    finally:
        sentry_sdk.get_client().close()


async def test_missing_retained_items_rolls_back_paid_unit(checkout):
    h = checkout
    order = (await h.create()).json()
    # Real lost capture data must fail closed; never commit a partial count body.
    async with h.sessions.begin() as session:
        await session.execute(
            delete(ReferralOrderItemSnapshot).where(
                ReferralOrderItemSnapshot.order_id == order["id"]
            )
        )
    response = await callback(h, order)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "referral_paid_unavailable"}}
    assert await facts(h) == []
    async with h.sessions() as session:
        assert (await session.get(Order, order["id"])).payment_status == "unpaid"
        assert await session.scalar(select(func.count()).select_from(PaymentEvent)) == 0


@pytest.mark.parametrize("echo", ["setting", "engine"])
@pytest.mark.parametrize("hook", ["callback", "fully-points"])
async def test_ordinary_paid_echo_preserves_commit(checkout, monkeypatch, echo, hook):
    h = checkout
    monkeypatch.setattr(settings, "employee_referral_capture_enabled", False)
    order = None
    if hook == "callback":
        order = (await h.create(h.body(points_to_apply=5000))).json()
    if echo == "setting":
        monkeypatch.setattr(settings, "database_echo", True)
    else:
        h.engine.echo = "debug"
    try:
        response = (
            await callback(h, order)
            if hook == "callback"
            else await h.create(h.body(points_to_apply=10000))
        )
        assert response.status_code == (200 if hook == "callback" else 201), response.text
        if hook == "fully-points":
            order = response.json()
    finally:
        h.engine.echo = False
    assert await facts(h) == []
    async with h.sessions() as reader:
        assert (await reader.get(Order, order["id"])).payment_status == "paid"
        assert await reader.get(ReferralOrderSnapshot, order["id"]) is None
        assert await loyalty.get_balance(reader, 1) == (95000 if hook == "callback" else 90000)
        assert await reader.scalar(select(func.count()).select_from(PaymentEvent)) == (
            1 if hook == "callback" else 0
        )


async def test_retained_late_paid_echo_keeps_eligibility_and_rollback_private(
    checkout, monkeypatch, caplog, capsys
):
    h = checkout
    order = (await h.create(h.body(points_to_apply=5000))).json()
    marker = "PRIVATE_PAID_ELIGIBILITY_CANARY"
    async with h.sessions.begin() as writer:
        header = await writer.get(ReferralOrderSnapshot, order["id"])
        header.campaign_name = header.employee_display_name = marker
        code = str(header.employee_code)
        campaign = await writer.get(CampaignReplica, 1)
        campaign.state = "ended"
        campaign.ended_at = h.now
    monkeypatch.setattr(settings, "employee_referral_capture_enabled", False)
    captured = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=captured,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )
    caplog.set_level(logging.DEBUG)
    h.engine.echo = "debug"
    try:
        response = await callback(h, order)
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "referral_paid_unavailable"}}
        sentry_sdk.flush()
        assert captured.events == []
    finally:
        h.engine.echo = False
        sentry_sdk.get_client().close()
    output = capsys.readouterr()
    observed = response.text + caplog.text + output.out + output.err + json.dumps(captured.events)
    assert marker not in observed
    assert code not in observed
    logging.getLogger("sqlalchemy.engine.Engine").warning("ORDINARY_ECHO_CONTROL")
    assert "ORDINARY_ECHO_CONTROL" in caplog.text
    assert await facts(h) == []
    async with h.sessions() as reader:
        assert (await reader.get(Order, order["id"])).payment_status == "unpaid"
        assert await loyalty.get_balance(reader, 1) == 100000
        assert await reader.scalar(select(func.count()).select_from(PaymentEvent)) == 0
