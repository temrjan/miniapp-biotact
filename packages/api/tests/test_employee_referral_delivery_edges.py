"""Retained legacy, atomic returns, bounded schedules and actual privacy channels."""

import asyncio
import hashlib
import json
import logging
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
import sentry_sdk
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.employee_referrals import delivery_store as store
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.delivery_privacy import private_scope
from src.employee_referrals.notices import invoice_referral_label, render
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.returns import validated
from src.employee_referrals.schemas import MAX_BIGINT
from src.employee_referrals.transport import Result, parse_ack, send_k2, send_notice
from src.employee_referrals.worker import command, health
from src.notifications.service import TelegramNotifier, telegram_notifier
from src.orders.models import Order
from tests.test_employee_referral_delivery import K2, NOTICES, ack_value, jobs, streamed_response
from tests.test_employee_referral_delivery import secret as secret
from tests.test_employee_referral_k1_receiver import Captured
from tests.test_employee_referral_order_capture import checkout as checkout
from tests.test_employee_referral_paid import callback, facts
from tests.test_employee_referral_returns import admin, intent, paid_order, path


@pytest.mark.parametrize("retained", [True, False])
async def test_legacy_paid_return_snapshot_labels_and_no_legacy_resend(checkout, retained):
    h = checkout
    order = await paid_order(h)
    async with h.sessions.begin() as session:
        notice = await session.scalar(select(NoticeDelivery))
        # Simulate the documented migration result on this owned metadata fixture.
        notice.state, notice.payload, notice.payload_sha256 = "legacy_unknown", None, None
        if not retained:
            await session.execute(delete(Order).where(Order.id == order["id"]))
    assert await store.claim_job(h.sessions, "notice") is None
    root = (await facts(h))[0]
    assert await store.retry(h.sessions, "notice", root.event_id, NOTICES) == (
        "no_relevant_work",
        0,
        0,
    )
    response = await h.client.post(path(order["id"]), headers=await admin(h), json=intent(order, 1))
    assert response.status_code == 200, response.text
    claim = await store.claim_job(h.sessions, "notice")
    value = claim.notice
    message = render(value)
    assert "Накопленно возвращено этой версии: 1" in message
    if retained:
        assert value.campaign_name == h.data["campaign"]["name"]
        assert value.employee_name == h.data["participants"][0]["employee_display_name"]
    else:
        assert value.campaign_name == "Кампания №1"
        assert value.employee_name == f"код {validated(root).attribution.employee_code}"
        assert value.order_number == f"Заказ №{order['id']}"
        assert all(line.product_name == f"Товар №{line.order_item_id}" for line in value.lines)
    assert "Сумма:" not in message
    assert (await jobs(h, NoticeDelivery))[0].state == "legacy_unknown"


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_return_job_failure_rolls_back_whole_confirmation(checkout, lane):
    h = checkout
    order = await paid_order(h)
    headers = await admin(h)
    request = intent(order, 1)

    def fail(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith(f"INSERT INTO employee_referral_{lane}_delivery"):
            raise SQLAlchemyError("PRIVATE_RETURN_JOB_CANARY")

    event.listen(h.engine.sync_engine, "before_cursor_execute", fail)
    try:
        assert (
            await h.client.post(path(order["id"]), headers=headers, json=request)
        ).status_code == 503
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", fail)
    assert len(await jobs(h, K2Delivery)) == len(await jobs(h, NoticeDelivery)) == 1
    async with h.sessions() as session:
        assert await session.scalar(select(ReturnOutbox)) is None
    assert (
        await h.client.post(path(order["id"]), headers=headers, json=request)
    ).status_code == 200
    assert len(await jobs(h, K2Delivery)) == len(await jobs(h, NoticeDelivery)) == 2


async def test_ordinary_callback_and_distinct_invoice_labels(checkout, monkeypatch):
    h = checkout
    campaign = (await h.create()).json()
    ordinary = (await h.create(h.body(referral_token=None))).json()
    async with h.sessions() as session:
        assert await invoice_referral_label(session, campaign["id"]) == "Заказ кампании"
        assert await invoice_referral_label(session, ordinary["id"]) == "Без реферала"
    old = telegram_notifier.notify_payment_success
    assert (await callback(h, ordinary, uuid="ordinary-paid-control")).status_code == 200
    assert old.await_count == 1
    assert await jobs(h, K2Delivery) == await jobs(h, NoticeDelivery) == []
    sent = []

    async def boundary(self, message, parse_mode="HTML"):
        sent.append(message)
        return True

    monkeypatch.setattr(TelegramNotifier, "send_message", boundary)
    notifier = TelegramNotifier()
    await notifier.notify_order_created("ordinary", 100, [])
    await notifier.notify_order_created("campaign", 100, [], referral_label="Заказ кампании")
    assert "Без реферала" in sent[0]
    assert "Заказ кампании" in sent[1]
    assert "Без реферала" not in sent[1]


@pytest.mark.parametrize(
    ("lane", "delays"), [("k2", [1, 2, 4, 8]), ("notice", [60, 300, 1800, 7200])]
)
async def test_durable_retry_schedule_fifth_failure_and_operator_boundaries(checkout, lane, delays):
    h = checkout
    await paid_order(h)
    model = K2Delivery if lane == "k2" else NoticeDelivery
    config = K2 if lane == "k2" else NOTICES
    claim = None
    for expected in delays:
        claim = await store.claim_job(h.sessions, lane)
        async with h.sessions() as session:
            before = await store.now(session)
        await store.complete(h.sessions, claim, Result(category="transport"))
        saved = (await jobs(h, model))[0]
        assert expected <= (saved.next_attempt_at - before).total_seconds() < expected + 1
        async with h.sessions.begin() as session:
            job = await session.get(model, saved.id)
            job.next_attempt_at = await store.now(session) - timedelta(seconds=1)
    claim = await store.claim_job(h.sessions, lane)
    await store.complete(h.sessions, claim, Result(category="transport"))
    saved = (await jobs(h, model))[0]
    assert saved.state == "blocked"
    assert saved.attempt_count == 5
    assert await store.claim_job(h.sessions, lane) is None
    assert await store.retry(h.sessions, lane, UUID(claim.snapshot.event_id), config) == (
        "queued",
        1,
        0,
    )
    new = await store.claim_job(h.sessions, lane)
    await store.complete(h.sessions, claim, Result(category="authentication", permanent=True))
    assert (await jobs(h, model))[0].generation == new.generation
    async with h.sessions.begin() as session:
        job = await session.get(model, new.job_id)
        job.generation = MAX_BIGINT
        job.leased_until = await store.now(session) - timedelta(seconds=1)
    assert await store.claim_job(h.sessions, lane) is None
    assert (await jobs(h, model))[0].error_category == "storage"
    assert await store.retry(h.sessions, lane, UUID(new.snapshot.event_id), config) == (
        "quarantined",
        0,
        1,
    )


async def test_return_display_delta_corruption_is_quarantined(checkout):
    h = checkout
    order = await paid_order(h)
    assert (
        await h.client.post(path(order["id"]), headers=await admin(h), json=intent(order, 1))
    ).status_code == 200
    async with h.sessions.begin() as session:
        paid = await session.scalar(
            select(NoticeDelivery).where(NoticeDelivery.source_kind == "paid")
        )
        paid.state, paid.message_id, paid.completed_at = "sent", 1, await store.now(session)
        job = await session.scalar(
            select(NoticeDelivery).where(NoticeDelivery.source_kind == "return")
        )
        payload = json.loads(job.payload)
        payload["lines"][0]["delta"] = 0
        job.payload = json.dumps(payload).encode()
        job.payload_sha256 = hashlib.sha256(job.payload).hexdigest()
    assert await store.claim_job(h.sessions, "notice") is None
    assert (await jobs(h, NoticeDelivery))[1].error_category == "storage"


async def test_real_sql_httpx_sdk_private_channels_reset_after_failure(checkout, caplog):
    h = checkout
    await paid_order(h)
    claim = await store.claim_job(h.sessions, "notice")
    captured = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1", transport=captured, traces_sample_rate=1.0
    )
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine.Engine")
    canary = "PRIVATE_SQL_HTTP_SDK_CANARY"

    async def boundary(request):
        sentry_sdk.add_breadcrumb(message=canary)
        sentry_sdk.capture_exception(RuntimeError(canary))
        raise httpx.ReadError(canary, request=request)

    try:
        with private_scope():
            async with h.sessions() as session:
                assert await session.scalar(text("SELECT :secret"), {"secret": canary}) == canary
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(boundary), trust_env=False
        ) as client:
            result = await send_notice(client, NOTICES, claim.notice)
        assert result.category == "transport"
        assert result.message_id is None
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: streamed_response(200, json={"ok": True, "result": {"message_id": 1}})
            ),
            trust_env=False,
        ) as client:
            assert (await send_notice(client, NOTICES, claim.notice)).message_id == 1
        assert NOTICES.token not in caplog.text + json.dumps(captured.events)
        async with h.sessions() as session:
            assert (
                await session.scalar(text("SELECT :control"), {"control": "SQL_POSITIVE_CONTROL"})
                == "SQL_POSITIVE_CONTROL"
            )
        logging.getLogger("unrelated").warning("PRIVATE_RESET_POSITIVE_CONTROL")
        sentry_sdk.capture_message("PRIVATE_RESET_POSITIVE_CONTROL")
        sentry_sdk.flush()
        assert canary not in caplog.text + json.dumps(captured.events)
        assert "SQL_POSITIVE_CONTROL" in caplog.text
        assert "PRIVATE_RESET_POSITIVE_CONTROL" in json.dumps(captured.events)
    finally:
        sentry_sdk.get_client().close()


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_slow_stream_total_timeout_and_cancellation_cleanup(checkout, lane, caplog):
    h = checkout
    await paid_order(h)
    claim = await store.claim_job(h.sessions, lane)
    started, closed = asyncio.Event(), asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            yield b"{"
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def boundary(_request):
        return httpx.Response(
            200,
            stream=SlowStream(),
            headers={"content-type": "application/json", "cache-control": "no-store"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(boundary), trust_env=False
    ) as client:

        def send():
            return (
                send_k2(client, K2, claim.body, claim.snapshot, claim.digest)
                if lane == "k2"
                else send_notice(client, NOTICES, claim.notice)
            )

        async with asyncio.timeout(11):
            result = await send()
        assert result.category == "transport"
        assert closed.is_set()
        closed.clear()
        started.clear()
        task = asyncio.create_task(send())
        await started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    caplog.set_level(logging.WARNING)
    logging.warning("CANCEL_RESET_CONTROL")
    assert "CANCEL_RESET_CONTROL" in caplog.text
    assert (await jobs(h, K2Delivery if lane == "k2" else NoticeDelivery))[0].state == "leased"


async def test_aggregate_unready_aged_and_actual_operator_exit_codes(checkout, monkeypatch, capsys):
    h = checkout
    await paid_order(h)
    claim = await store.claim_job(h.sessions, "k2")
    value = ack_value(claim, conflict=True, version=claim.snapshot.order_version + 1)
    value["receipt"]["outcome"] = "superseded"
    await store.complete(
        h.sessions,
        claim,
        Result(ack=parse_ack(json.dumps(value).encode(), claim.snapshot, claim.digest)),
    )
    async with h.sessions.begin() as session:
        notice = await session.scalar(select(NoticeDelivery))
        notice.created_at = await store.now(session) - timedelta(minutes=16)
    data, code = await health(h.sessions, {"k2": K2, "notice": NOTICES})
    assert code == 1
    assert data["k2"]["counts"]["ack_unready"] == 1
    assert data["notice"]["aged"] == 1
    for name, setting in [
        ("employee_referral_k2_sender_enabled", "true"),
        ("employee_referral_k2_portal_origin", K2.origin),
        ("employee_referral_notices_enabled", "false"),
    ]:
        monkeypatch.setattr(settings, name, setting)
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "employee_referral_k2_key", SecretStr(K2.key))
    assert await command(["health"]) == 1
    assert await command(["retry", "--lane", "k2", "--event-id", claim.snapshot.event_id]) == 0
    assert await command(["retry", "--lane", "k2", "--event-id", "PRIVATE_BAD_UUID"]) == 2
    assert "PRIVATE_BAD_UUID" not in capsys.readouterr().out
    monkeypatch.setattr(settings, "database_echo", True)
    assert await command(["health"]) == 2


@pytest.mark.parametrize(
    "defect",
    [
        "nan_total",
        "zero_total",
        "bad_price",
        "bad_counts",
        "empty_lines",
        "wrong_event",
        "duplicate_lines",
    ],
)
async def test_corrupt_valid_hash_display_payload_never_sends(checkout, defect):
    h = checkout
    await paid_order(h)
    async with h.sessions.begin() as session:
        job = await session.scalar(select(NoticeDelivery))
        data = json.loads(job.payload)
        if defect == "nan_total":
            data["total"] = "NaN"
        elif defect == "zero_total":
            data["total"] = "0"
        elif defect == "bad_price":
            data["lines"][0]["product_price"] = "-1"
        elif defect == "bad_counts":
            data["lines"][0]["returned"] = data["lines"][0]["quantity"] + 1
        elif defect == "empty_lines":
            data["lines"] = []
        elif defect == "wrong_event":
            data["event_id"] = "44444444-4444-4444-8444-444444444444"
        else:
            data["lines"].append(data["lines"][0])
        job.payload = json.dumps(data).encode()
        job.payload_sha256 = hashlib.sha256(job.payload).hexdigest()
    assert await store.claim_job(h.sessions, "notice") is None
    saved = (await jobs(h, NoticeDelivery))[0]
    assert saved.state == "blocked"
    assert saved.error_category == "storage"
    assert saved.attempt_count == 0


async def test_actual_operator_invalid_config_missing_correspondence_and_safe_output(
    checkout, monkeypatch, capsys
):
    h = checkout
    await paid_order(h)
    for args in (
        ["retry"],
        ["health", "--lane", "k2"],
        ["retry", "--lane", "k2", "--event-id", "11111111-1111-1111-8111-111111111111"],
        ["PRIVATE_ARGUMENT_CANARY"],
    ):
        assert await command(args) == 2
    monkeypatch.setattr(settings, "employee_referral_k2_sender_enabled", "TRUE")
    assert await command(["health"]) == 2
    monkeypatch.setattr(settings, "employee_referral_k2_sender_enabled", "false")
    async with h.sessions.begin() as session:
        await session.execute(delete(K2Delivery))
    assert await command(["health"]) == 2
    monkeypatch.setattr(settings, "database_url", "PRIVATE_DATABASE_URL_CANARY")
    assert await command(["health"]) == 2
    output = capsys.readouterr().out
    assert "PRIVATE_ARGUMENT_CANARY" not in output
    assert "PRIVATE_DATABASE_URL_CANARY" not in output
    assert [json.loads(line) for line in output.splitlines()] == [
        *[{"result": "configuration", "count": 0}] * 4,
        {"result": "configuration"},
        {"result": "storage", "count": 0},
        {"result": "storage", "count": 0},
    ]


async def test_claim_commit_failure_sends_nothing_and_completion_failure_retains_lease(checkout):
    from src.employee_referrals.worker import deliver_one
    from tests.test_employee_referral_delivery import streamed_response

    h = checkout
    await paid_order(h)
    requests = []

    def refuse_write(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("UPDATE employee_referral_k2_delivery"):
            raise SQLAlchemyError("PRIVATE_CLAIM_COMPLETION_CANARY")

    def boundary(request):
        requests.append(request.content)
        return streamed_response(503, json={"detail": {"code": "k2_unavailable"}})

    event.listen(h.engine.sync_engine, "before_cursor_execute", refuse_write)
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(boundary), trust_env=False
        ) as client:
            with pytest.raises(SQLAlchemyError):
                await deliver_one(h.sessions, client, "k2", K2)
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", refuse_write)
    assert requests == []
    saved = (await jobs(h, K2Delivery))[0]
    assert saved.state == "pending"
    assert saved.attempt_count == 0
    claim = await store.claim_job(h.sessions, "k2")
    ack = parse_ack(json.dumps(ack_value(claim)).encode(), claim.snapshot, claim.digest)
    event.listen(h.engine.sync_engine, "before_cursor_execute", refuse_write)
    try:
        with pytest.raises(SQLAlchemyError):
            await store.complete(h.sessions, claim, Result(ack=ack))
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", refuse_write)
    saved = (await jobs(h, K2Delivery))[0]
    assert saved.state == "leased"
    assert saved.attempt_count == 1
    async with h.sessions.begin() as session:
        job = await session.get(K2Delivery, claim.job_id)
        job.leased_until = await store.now(session) - timedelta(seconds=1)
    next_claim = await store.claim_job(h.sessions, "k2")
    assert next_claim.body == claim.body
    assert next_claim.generation == claim.generation + 1
    await store.complete(h.sessions, next_claim, Result(ack=ack))
    assert (await jobs(h, K2Delivery))[0].state == "acknowledged"
