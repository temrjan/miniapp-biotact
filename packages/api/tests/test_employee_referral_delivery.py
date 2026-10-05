"""G6a actual producers, retained effects, generation races and external boundaries."""

import asyncio
import json
import logging
import os
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sentry_sdk
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.employee_referrals import delivery_store as store
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.delivery_privacy import private_scope
from src.employee_referrals.notices import Notice, render, units, verify_notice
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.returns import validated
from src.employee_referrals.transport import (
    LaneConfig,
    Result,
    headers,
    parse_ack,
    send_k2,
    send_notice,
)
from src.employee_referrals.worker import deliver_one, health, readiness
from src.notifications.service import telegram_notifier
from src.orders.models import Order
from src.payments.service import multicard_client
from tests.test_employee_referral_k1_receiver import Captured
from tests.test_employee_referral_order_capture import checkout as checkout
from tests.test_employee_referral_paid import callback, facts
from tests.test_employee_referral_returns import admin, intent, paid_order, path
from tests.test_payments_lifecycle import SECRET

K2 = LaneConfig("true", "https://portal.example.test", "00" * 32)
NOTICES = LaneConfig("true", token="12345:synthetic_g6a", chat="-12345")


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw = raw

    async def __aiter__(self):
        yield self.raw[:17]
        yield self.raw[17:]


def streamed_response(status, *, json=None, content=None, headers=None):
    from json import dumps

    response_headers = dict(headers or {})
    if json is not None:
        response_headers.setdefault("content-type", "application/json")
    raw = dumps(json).encode() if json is not None else content or b""
    return httpx.Response(status, stream=BytesStream(raw), headers=response_headers)


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(multicard_client, "secret", SECRET)


async def jobs(h, model):
    async with h.sessions() as session:
        return list(await session.scalars(select(model).order_by(model.id)))


def ack_value(claim, *, replayed=False, version=None, conflict=False):
    s = claim.snapshot
    return {
        "schema_version": 1,
        "receipt": {
            "event_id": s.event_id,
            "source": s.source,
            "order_id": s.order_id,
            "order_version": s.order_version,
            "source_position": s.source_position,
            "body_sha256": claim.digest,
            "outcome": "applied",
            "received_at": s.model_dump(mode="json")["source_recorded_at"],
        },
        "replayed": replayed,
        "projection": {
            "order_version": version or s.order_version,
            "accounting_state": "confirmed",
            "has_conflict": conflict,
        },
    }


@pytest.mark.parametrize("points", [0, 10000, 50000])
async def test_new_jobs_single_selected_path_retained_returns_and_ordinary(
    checkout, monkeypatch, points
):
    h = checkout
    old = telegram_notifier.notify_payment_success
    order = await paid_order(h, points)
    assert old.await_count == 0
    root = (await facts(h))[0]
    notice = (await jobs(h, NoticeDelivery))[0]
    parsed = verify_notice(notice, validated(root))
    assert len(parsed.lines) == 3  # includes ordinary goods in the previous paid card
    message = render(parsed)
    assert "ОПЛАЧЕНО БАЛЛАМИ" in message if points == 50000 else "ОПЛАЧЕНО" in message
    assert "Asia/Tashkent" in message
    assert "Сумма:" in message
    assert "Сотрудник:" in message
    if points == 10000:
        assert "Итого оплачено картой:" in message
    hdr = await admin(h)
    first = intent(order, 1)
    response = await h.client.post(path(order["id"]), headers=hdr, json=first)
    assert response.status_code == 200, response.text
    assert (await h.client.post(path(order["id"]), headers=hdr, json=first)).json()["replayed"]
    if points == 0:
        async with h.sessions.begin() as session:
            await session.execute(delete(Order).where(Order.id == order["id"]))
    monkeypatch.setattr(settings, "employee_referral_capture_enabled", False)
    assert (
        await h.client.post(path(order["id"]), headers=hdr, json=intent(order, 1, 1))
    ).status_code == 200
    saved = await jobs(h, NoticeDelivery)
    refs = await jobs(h, K2Delivery)
    assert len(saved) == len(refs) == 3
    async with h.sessions() as session:
        returns = list(
            await session.scalars(select(ReturnOutbox).order_by(ReturnOutbox.order_version))
        )
    texts = [
        render(verify_notice(j, validated(row)))
        for j, row in zip(saved, [root, *returns], strict=True)
    ]
    assert "Накопленно возвращено этой версии: 1\nОсталось этой версии: 2" in texts[1]
    assert "Накопленно возвращено этой версии: 3\nОсталось этой версии: 0" in texts[2]
    assert "Сумма:" not in texts[1]
    assert "Invoice:" not in texts[2]
    assert old.await_count == 0
    ordinary = (await h.create(h.body(referral_token=None, points_to_apply=10000))).json()
    assert ordinary["payment_status"] == "paid"
    assert old.await_count == 1
    assert len(await jobs(h, NoticeDelivery)) == 3
    target = os.environ.get("G6A_PRODUCER_DIR")
    if target and points == 0:
        directory = Path(target)
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        for name, row in zip(
            ("paid-v1.json", "refund-v2.json", "refund-v3.json"), [root, *returns], strict=True
        ):
            (directory / name).write_bytes(row.body)
        (directory / "notices.json").write_text(json.dumps([j.payload.decode() for j in saved]))


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_actual_job_insert_failure_rolls_back_paid_unit(checkout, lane):
    h = checkout
    order = (await h.create()).json()

    def fail(_connection, _cursor, statement, _params, _context, _many):
        if statement.startswith(f"INSERT INTO employee_referral_{lane}_delivery"):
            raise SQLAlchemyError("PRIVATE_NEW_JOB_CANARY")

    event.listen(h.engine.sync_engine, "before_cursor_execute", fail)
    try:
        response = await callback(h, order)
        assert response.status_code == 503
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", fail)
    assert await facts(h) == []
    assert await jobs(h, K2Delivery) == await jobs(h, NoticeDelivery) == []
    async with h.sessions() as session:
        assert (await session.get(Order, order["id"])).payment_status == "unpaid"


async def test_two_connections_skip_locked_live_lease_generation_fifth_crash(checkout):
    h = checkout
    await paid_order(h)
    async with h.sessions.begin() as holder:
        pid1 = await holder.scalar(select(func.pg_backend_pid()))
        job = await holder.scalar(select(K2Delivery).with_for_update())
        async with h.sessions() as other:
            pid2 = await other.scalar(select(func.pg_backend_pid()))
        assert pid1 != pid2
        assert await store.claim_job(h.sessions, "k2") is None
    first, second = await asyncio.gather(
        store.claim_job(h.sessions, "k2"), store.claim_job(h.sessions, "k2")
    )
    claim = first or second
    assert claim is not None
    assert (first is None) != (second is None)
    assert await store.claim_job(h.sessions, "k2") is None
    value = ack_value(claim)
    ack = parse_ack(json.dumps(value).encode(), claim.snapshot, claim.digest)
    async with h.sessions.begin() as session:
        job = await session.get(K2Delivery, claim.job_id)
        job.leased_until = await store.now(session) - timedelta(seconds=1)
    await store.complete(h.sessions, claim, Result(ack=ack))
    assert (await jobs(h, K2Delivery))[0].state == "leased"
    newer = await store.claim_job(h.sessions, "k2")
    assert newer.generation == claim.generation + 1
    await store.complete(h.sessions, claim, Result(category="transport"))
    assert (await jobs(h, K2Delivery))[0].generation == newer.generation
    for attempt in range(3, 6):
        async with h.sessions.begin() as session:
            job = await session.get(K2Delivery, claim.job_id)
            job.leased_until = await store.now(session) - timedelta(seconds=1)
        newer = await store.claim_job(h.sessions, "k2")
        assert (await jobs(h, K2Delivery))[0].attempt_count == attempt
    async with h.sessions.begin() as session:
        job = await session.get(K2Delivery, claim.job_id)
        job.leased_until = await store.now(session) - timedelta(seconds=1)
    assert await store.claim_job(h.sessions, "k2") is None
    blocked = (await jobs(h, K2Delivery))[0]
    assert blocked.state == "blocked"
    assert blocked.error_category == "lease_expired"
    assert blocked.attempt_count == 5
    assert await store.retry(h.sessions, "k2", UUID(claim.snapshot.event_id), K2) == (
        "queued",
        1,
        0,
    )
    assert await store.claim_job(h.sessions, "k2") is not None
    assert (await jobs(h, K2Delivery))[0].attempt_count == 1
    await readiness(h.sessions)


async def test_network_without_db_lock_lost_completion_replay_and_independent_notices(checkout):
    h = checkout
    await paid_order(h)
    seen = []

    async def handler(request):
        raw = request.content
        seen.append(raw)
        async with h.sessions.begin() as session:
            await session.execute(text("SET LOCAL lock_timeout='100ms'"))
            await session.scalar(select(K2Delivery).with_for_update(nowait=True))
        claim = await store.claim_job(h.sessions, "notice")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: streamed_response(200, json={"ok": True, "result": {"message_id": 77}})
            ),
            trust_env=False,
        ) as notice_client:
            result = await send_notice(notice_client, NOTICES, claim.notice)
        await store.complete(h.sessions, claim, result)
        source = (await facts(h))[0]
        snapshot = validated(source)
        fake = store.Claim("k2", 1, 1, raw, source.body_sha256, snapshot, None)
        return streamed_response(200, json=ack_value(fake), headers={"cache-control": "no-store"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        claim = await store.claim_job(h.sessions, "k2")
        result = await send_k2(client, K2, claim.body, claim.snapshot, claim.digest)
        assert result.ack is not None
        assert (await jobs(h, K2Delivery))[0].state == "leased"  # no durable completion yet
        assert (await jobs(h, NoticeDelivery))[0].state == "sent"
        # Lost completion, a restart/expired lease can send again.
        async with h.sessions.begin() as session:
            job = await session.get(K2Delivery, claim.job_id)
            job.leased_until = await store.now(session) - timedelta(seconds=1)
        newer = await store.claim_job(h.sessions, "k2")
        # Network stub for second boundary avoids a second notice claim.
        client._transport = httpx.MockTransport(
            lambda _: streamed_response(
                200, json=ack_value(newer, replayed=True), headers={"cache-control": "no-store"}
            )
        )
        result = await send_k2(client, K2, newer.body, newer.snapshot, newer.digest)
        await store.complete(h.sessions, newer, result)
    assert claim.body == newer.body == seen[0]
    assert (await jobs(h, K2Delivery))[0].state == "acknowledged"
    assert await store.retry(h.sessions, "k2", UUID(claim.snapshot.event_id), K2) == (
        "no_relevant_work",
        0,
        0,
    )
    data, code = await health(h.sessions, {"k2": K2, "notice": NOTICES})
    assert code == 0
    assert data["k2"]["counts"]["completed"] == 1
    assert data["notice"]["counts"]["completed"] == 1


@pytest.mark.parametrize(
    ("status", "code", "category", "permanent"),
    [
        (401, "k2_invalid_auth", "authentication", True),
        (409, "k2_version_conflict", "conflict", True),
        (413, "k2_payload_too_large", "payload_limit", True),
        (415, "k2_unsupported_media", "protocol", True),
        (422, "k2_invalid_request", "invalid_request", True),
        (404, "k2_disabled", "receiver_disabled", False),
        (429, "any", "transport", False),
        (503, "k2_unavailable", "transport", False),
        (302, "anything", "protocol", False),
        (204, "anything", "protocol", False),
        (401, "unknown", "protocol", False),
    ],
)
async def test_transport_refusals_and_exact_signature(checkout, status, code, category, permanent):
    h = checkout
    await paid_order(h)
    claim = await store.claim_job(h.sessions, "k2")
    requests = []

    def handler(request):
        requests.append(request)
        stamp = int(request.headers["X-Biotact-K2-Timestamp"])
        expected = headers(K2.key, claim.body, stamp)
        assert request.content == claim.body
        assert request.headers["X-Biotact-K2-Signature"] == expected["X-Biotact-K2-Signature"]
        assert request.url.path == "/api/v1/internal/employee-referrals/k2"
        return streamed_response(
            status,
            json={"detail": {"code": code}},
            headers={"Location": "https://elsewhere.invalid"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
    ) as client:
        result = await send_k2(client, K2, claim.body, claim.snapshot, claim.digest)
    assert result.ack is None
    assert result.category == category
    assert result.permanent is permanent
    assert len(requests) == 1
    await store.complete(h.sessions, claim, result)
    job = (await jobs(h, K2Delivery))[0]
    assert job.state == ("blocked" if permanent else "pending")
    if permanent and category in store.QUARANTINE:
        assert await store.retry(h.sessions, "k2", UUID(claim.snapshot.event_id), K2) == (
            "quarantined",
            0,
            1,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "bool",
        "unknown",
        "version",
        "position",
        "digest",
        "source",
        "event",
        "received",
        "duplicate",
        "nan",
        "huge",
        "encoding",
        "media",
        "cache",
        "superseded",
        "projection",
    ],
)
async def test_strict_ack_invalid_never_completes(checkout, mutation):
    h = checkout
    await paid_order(h)
    claim = await store.claim_job(h.sessions, "k2")
    value = ack_value(claim)
    hdr = {"cache-control": "no-store", "content-type": "application/json"}
    if mutation == "bool":
        value["receipt"]["order_id"] = True
    elif mutation == "unknown":
        value["unknown"] = 1
    elif mutation in {"version", "position"}:
        value["receipt"]["order_" + mutation if mutation == "version" else "source_position"] += 1
    elif mutation == "digest":
        value["receipt"]["body_sha256"] = "a" * 64
    elif mutation == "source":
        value["receipt"]["source"] = "foreign"
    elif mutation == "event":
        value["receipt"]["event_id"] = str(uuid4())
    elif mutation == "received":
        value["receipt"]["received_at"] = "2026-01-01T00:00:00+00:00"
    elif mutation == "superseded":
        value["receipt"]["outcome"] = "superseded"
    elif mutation == "projection":
        value["projection"]["order_version"] = 0
    raw = json.dumps(value).encode()
    if mutation == "duplicate":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": 1,"schema_version": 1')
    elif mutation == "nan":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": NaN')
    elif mutation == "huge":
        raw = b"x" * 16385
    elif mutation == "encoding":
        hdr["content-encoding"] = "gzip"
    elif mutation == "media":
        hdr["content-type"] = "text/html"
    elif mutation == "cache":
        hdr["cache-control"] = "public"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: streamed_response(200, content=raw, headers=hdr)),
        trust_env=False,
    ) as client:
        result = await send_k2(client, K2, claim.body, claim.snapshot, claim.digest)
    assert result.ack is None
    assert result.category == "protocol"


@pytest.mark.parametrize(
    ("status", "value", "category", "message"),
    [
        (200, {"ok": True, "result": {"message_id": 9}}, "protocol", 9),
        (200, {"ok": False, "result": {"message_id": 9}}, "protocol", None),
        (200, {"ok": True, "result": {"message_id": True}}, "protocol", None),
        (200, {"ok": True, "result": {"message_id": 0}}, "protocol", None),
        (200, {"ok": True, "result": None}, "protocol", None),
        (401, {}, "configuration", None),
        (403, {}, "configuration", None),
        (400, {}, "recipient_unreachable", None),
        (429, {"parameters": {"retry_after": 9000}}, "transport", None),
        (503, {}, "transport", None),
        (302, {}, "protocol", None),
    ],
)
async def test_notice_one_attempt_strict_message_id_and_independence(
    checkout, status, value, category, message
):
    h = checkout
    await paid_order(h)
    k2 = await store.claim_job(h.sessions, "k2")
    await store.complete(
        h.sessions,
        k2,
        Result(ack=parse_ack(json.dumps(ack_value(k2)).encode(), k2.snapshot, k2.digest)),
    )
    claim = await store.claim_job(h.sessions, "notice")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "api.telegram.org"
        assert request.url.path.endswith("/sendMessage")
        assert json.loads(request.content)["parse_mode"] == "HTML"
        return streamed_response(status, json=value)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        result = await send_notice(client, NOTICES, claim.notice)
    assert len(requests) == 1
    assert result.message_id == message
    assert result.category == category
    await store.complete(h.sessions, claim, result)
    assert (await jobs(h, K2Delivery))[0].state == "acknowledged"
    assert (await jobs(h, NoticeDelivery))[0].state == (
        "sent" if message else "blocked" if result.permanent else "pending"
    )
    if status == 429:
        assert result.retry_after == 7200


@pytest.mark.parametrize(
    ("status", "category", "permanent"),
    [
        (401, "configuration", True),
        (403, "configuration", True),
        (400, "recipient_unreachable", True),
        (503, "transport", False),
        (429, "transport", False),
    ],
)
async def test_notice_malformed_refusal_preserves_status_and_durable_result(
    checkout, status, category, permanent
):
    h = checkout
    await paid_order(h)
    claim = await store.claim_job(h.sessions, "notice")
    requests = []

    def boundary(request):
        requests.append(request)
        return streamed_response(status, content=b"<html>synthetic refusal</html>")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(boundary), trust_env=False
    ) as client:
        result = await send_notice(client, NOTICES, claim.notice)
    assert (result.category, result.permanent, result.retry_after) == (category, permanent, 0)
    assert result.message_id is None
    assert len(requests) == 1
    await store.complete(h.sessions, claim, result)
    notice = (await jobs(h, NoticeDelivery))[0]
    assert notice.state == ("blocked" if permanent else "pending")
    assert notice.error_category == category
    assert notice.attempt_count == 1
    assert (await jobs(h, K2Delivery))[0].attempt_count == 0
    assert len(await facts(h)) == 1


async def test_campaign_notice_cancel_keeps_paid_fact_and_durable_lease(checkout):
    h = checkout
    await paid_order(h, 50000)
    assert telegram_notifier.notify_payment_success.await_count == 0
    ready = asyncio.Event()

    async def boundary(_request):
        ready.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(boundary), trust_env=False
    ) as client:
        task = asyncio.create_task(deliver_one(h.sessions, client, "notice", NOTICES))
        try:
            await asyncio.wait_for(ready.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    retained = await facts(h)
    assert len(retained) == 1
    notice = (await jobs(h, NoticeDelivery))[0]
    assert notice.state == "leased"
    assert notice.attempt_count == 1
    assert notice.message_id is None
    assert (await jobs(h, K2Delivery))[0].attempt_count == 0
    async with h.sessions() as session:
        order = await session.get(Order, retained[0].order_id)
        assert order.payment_status == "paid"
        assert order.points_applied == 50000


async def test_render_bounds_integrity_privacy_and_positive_controls(checkout, caplog, monkeypatch):
    h = checkout
    await paid_order(h)
    claim = await store.claim_job(h.sessions, "notice")
    data = claim.notice.model_dump(mode="json")
    data.update(
        campaign_name="<b>&" + "😀" * 200,
        employee_name="<script>&" * 80,
        delivery_address="&<>" * 1000,
    )
    data["lines"] *= 100
    value = Notice.model_validate(data)
    message = render(value)
    assert units(message) <= 3500
    assert "Остальные строки — в заказе" in message
    assert "&lt;b&gt;&amp;" in message
    assert "<script>" not in message
    captured = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1", transport=captured, traces_sample_rate=1.0
    )
    caplog.set_level(logging.DEBUG)
    canary = "PRIVATE_DELIVERY_CANARY"
    try:
        with private_scope():
            logging.getLogger("sqlalchemy.engine.Engine").error(canary, exc_info=True)
            logging.getLogger("httpx").warning("https://api.telegram.org/bot%s/sendMessage", canary)
            sentry_sdk.capture_message(canary)
            with sentry_sdk.start_transaction(name=canary):
                sentry_sdk.capture_message(canary)
        logging.getLogger("unrelated").warning("DELIVERY_POSITIVE_CONTROL")
        sentry_sdk.capture_message("DELIVERY_POSITIVE_CONTROL")
        with sentry_sdk.start_transaction(name="DELIVERY_TX_CONTROL"):
            pass
        sentry_sdk.flush()
        assert canary not in caplog.text + json.dumps(captured.events)
        assert "DELIVERY_POSITIVE_CONTROL" in caplog.text
        assert any(e.get("message") == "DELIVERY_POSITIVE_CONTROL" for e in captured.events)
        assert any(e.get("transaction") == "DELIVERY_TX_CONTROL" for e in captured.events)
    finally:
        sentry_sdk.get_client().close()
    async with h.sessions.begin() as session:
        job = await session.get(NoticeDelivery, claim.job_id)
        job.payload = b"{}"
        job.leased_until = await store.now(session) - timedelta(seconds=1)
    assert await store.claim_job(h.sessions, "notice") is None
    assert (await jobs(h, NoticeDelivery))[0].error_category == "storage"


@pytest.mark.parametrize(
    "configuration",
    [
        LaneConfig(),
        LaneConfig("TRUE"),
        LaneConfig("true", "http://localhost", "00" * 32),
        LaneConfig("true", "https://x.invalid/path", "00" * 32),
        LaneConfig("true", "https://user@x.invalid", "00" * 32),
        LaneConfig("true", "https://x.invalid:0", "00" * 32),
        LaneConfig("true", "https://x.invalid", "bad"),
    ],
)
async def test_disabled_invalid_lane_no_claim_or_network(checkout, configuration):
    h = checkout
    await paid_order(h)

    def forbidden(_request):
        raise AssertionError("No network expected")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(forbidden), trust_env=False
    ) as client:
        assert not await deliver_one(h.sessions, client, "k2", configuration)
    assert (await jobs(h, K2Delivery))[0].attempt_count == 0
