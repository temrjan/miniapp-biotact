"""Retained source counts and actual mounted HMAC/refusal/replay boundaries."""

import copy
import json
import logging
import time
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select

from src.config import settings
from src.employee_referrals import count_store
from src.employee_referrals.count_wire import PATH, canonical, signature
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.delivery_store import replay_k2
from src.employee_referrals.models import CampaignReplica, ReferralOrderItemSnapshot
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.transport import LaneConfig
from src.orders.models import Order
from tests.test_employee_referral_order_capture import checkout as checkout
from tests.test_employee_referral_returns import admin, intent, paid_order, path
from tests.test_employee_referral_returns import secret as secret

KEY = "12" * 32
K2 = LaneConfig("true", "https://portal.example.test", "00" * 32)


@pytest.fixture(autouse=True)
def counts_on(monkeypatch):
    monkeypatch.setattr(settings, "employee_referral_count_readback_enabled", "true")
    monkeypatch.setattr(settings, "employee_referral_count_readback_key", SecretStr(KEY))


def signed(raw, offset=0, key=KEY):
    stamp = str(int(time.time()) + offset)
    return {
        "Content-Type": "application/json",
        "X-Biotact-Count-Timestamp": stamp,
        "X-Biotact-Count-Signature": signature(key, raw, stamp),
    }


async def read(h, day=None):
    day = day or h.now.astimezone(ZoneInfo("Asia/Tashkent")).date().isoformat()
    raw = canonical({"schema_version": 1, "campaign_id": 1, "day": day})
    response = await h.client.post(PATH, content=raw, headers=signed(raw))
    assert response.headers["cache-control"] == "no-store"
    return response


def qty(p, r):
    return {"paid_quantity": str(p), "returned_quantity": str(r), "net_quantity": str(p - r)}


async def test_real_retained_sources_partial_full_and_deletion(checkout, monkeypatch):
    h = checkout
    order = await paid_order(h)
    headers = await admin(h)
    paid = (await read(h)).json()
    assert paid["source_state"] == "retained_valid"
    assert paid["totals"] == paid["daily"] == qty(3, 0)
    assert len(paid["events"]) == 1
    assert paid["delivery"]["pending"] == 1
    assert (
        await h.client.post(path(order["id"]), json=intent(order, 1), headers=headers)
    ).status_code == 200
    partial = (await read(h)).json()
    assert partial["totals"] == qty(3, 1)
    assert partial["events"][1]["return_delta"] == [
        {"order_item_id": order["items"][0]["id"], "quantity": 1}
    ]
    async with h.sessions.begin() as session:
        c = await session.get(CampaignReplica, 1)
        c.state = "ended"
        c.ended_at = h.now
        await session.execute(delete(Order).where(Order.id == order["id"]))
    monkeypatch.setattr(settings, "employee_referral_capture_enabled", False)
    assert (
        await h.client.post(path(order["id"]), json=intent(order, 1, 1), headers=headers)
    ).status_code == 200
    full = (await read(h)).json()
    assert full["totals"] == qty(3, 3)
    assert full["daily"] == qty(3, 3)
    assert len(full["events"]) == 3
    assert full["events"][0] == paid["events"][0]
    assert (await read(h, "2001-01-01")).json()["daily"] == qty(0, 0)


async def test_actual_paid_writer_omission_is_gap_but_nonapplied_empty(checkout, monkeypatch):
    h = checkout
    order = (await h.create()).json()
    assert (await read(h)).json()["totals"] == qty(0, 0)
    from src.payments import lifecycle
    from tests.test_employee_referral_paid import callback

    async def omitted(_session, _order):
        return None

    monkeypatch.setattr(lifecycle, "record_paid", omitted)
    assert (await callback(h, order, uuid="omitted-producer")).status_code == 200
    value = (await read(h)).json()
    assert value["source_state"] == "source_gap"
    assert value["totals"] is value["daily"] is None
    assert value["events"] == []
    async with h.sessions() as session:
        stored = await session.get(Order, order["id"])
        assert stored.paid_at is not None
        assert not (await session.scalars(select(PaidOutbox))).all()


@pytest.mark.parametrize(
    "corruption",
    [
        "marker",
        "quantity",
        "attribution",
        "delta",
        "identity",
        "version",
        "hash",
        "missing-job",
        "ack-unready",
        "status-only",
    ],
)
async def test_independent_source_scans_refuse_actual_contradictions(checkout, corruption):
    h = checkout
    if corruption == "status-only":
        order = (await h.create()).json()
    else:
        order = await paid_order(h)
    if corruption in {"delta", "identity", "version", "hash"}:
        assert (
            await h.client.post(path(order["id"]), json=intent(order, 1), headers=await admin(h))
        ).status_code == 200
    async with h.sessions.begin() as session:
        if corruption in {"status-only", "marker"}:
            o = await session.get(Order, order["id"])
            if corruption == "status-only":
                o.payment_status = "paid"
            else:
                o.paid_at = None
        elif corruption in {"quantity", "attribution"}:
            if corruption == "quantity":
                line = (await session.scalars(select(ReferralOrderItemSnapshot))).first()
                line.quantity = 9
            else:
                from src.employee_referrals.models import ReferralOrderSnapshot

                (await session.get(ReferralOrderSnapshot, order["id"])).content_revision = 9
        elif corruption in {"delta", "identity", "version", "hash"}:
            row = (await session.scalars(select(ReturnOutbox))).one()
            if corruption == "delta":
                row.request_lines = [
                    {"order_item_id": str(order["items"][0]["id"]), "quantity": "2"}
                ]
            elif corruption == "hash":
                row.body_sha256 = "f" * 64
            elif corruption == "version":
                row.order_version = 3
            else:
                import hashlib

                value = json.loads(row.body)
                value["attribution"]["content_revision"] = 8
                row.body = canonical(value)
                row.body_sha256 = hashlib.sha256(row.body).hexdigest()
        elif corruption == "missing-job":
            await session.execute(delete(K2Delivery))
        else:
            job = (await session.scalars(select(K2Delivery))).one()
            job.state = "acknowledged"
            job.completed_at = h.now
            job.ack = {"projection": {"has_conflict": True, "accounting_state": "confirmed"}}
    response = await read(h)
    if corruption in {"hash", "version"}:
        assert response.status_code == 503
    else:
        assert response.status_code == 200, response.text
        value = response.json()
        if corruption == "missing-job":
            assert value["totals"] == qty(3, 0)
            assert value["delivery"]["missing_jobs"] == 1
        elif corruption == "ack-unready":
            assert value["totals"] == qty(3, 0)
            assert value["delivery"]["ack_unready"] == 1
        else:
            assert value["source_state"] == "inconsistent"
            assert value["totals"] is None


@pytest.mark.parametrize(
    "case",
    [
        "wrong-key",
        "wrong-direction",
        "wrong-path",
        "expired",
        "future",
        "duplicate",
        "media",
        "encoding",
        "oversize",
        "schema",
        "duplicate-json",
        "day",
        "scope",
        "query",
        "trailing",
        "off",
        "config",
        "echo",
        "unknown",
        "method",
    ],
)
async def test_mounted_auth_strict_bounds_and_fixed_errors(checkout, monkeypatch, case):
    h = checkout
    raw = canonical({"schema_version": 1, "campaign_id": 1, "day": "2026-10-05"})
    target = PATH
    headers = signed(raw)
    expected = 422
    if case == "wrong-key":
        headers = signed(raw, key="ff" * 32)
        expected = 401
    elif case in {"wrong-direction", "wrong-path"}:
        import hashlib
        import hmac

        stamp = headers["X-Biotact-Count-Timestamp"]
        prefix = f"biotact-count-v1\n{'shop-to-portal' if case == 'wrong-direction' else 'portal-to-shop'}\nPOST\n{PATH if case == 'wrong-direction' else '/other'}\n{stamp}\n".encode()
        headers["X-Biotact-Count-Signature"] = hmac.new(
            bytes.fromhex(KEY), prefix + raw, hashlib.sha256
        ).hexdigest()
        expected = 401
    elif case in {"expired", "future"}:
        headers = signed(raw, offset=-31 if case == "expired" else 31)
        expected = 401
    elif case == "duplicate":
        headers = [
            *list(headers.items()),
            ("X-Biotact-Count-Timestamp", headers["X-Biotact-Count-Timestamp"]),
        ]
        expected = 401
    elif case == "media":
        headers["Content-Type"] = "text/plain"
        expected = 415
    elif case == "encoding":
        headers["Content-Encoding"] = "gzip"
        expected = 415
    elif case == "oversize":
        raw = b"x" * 1025
        headers = signed(raw)
        expected = 413
    elif case in {"schema", "duplicate-json", "day", "scope"}:
        raw = {
            "schema": b'{"schema_version":true,"campaign_id":1,"day":"2026-10-05"}',
            "duplicate-json": b'{"schema_version":1,"campaign_id":1,"campaign_id":1,"day":"2026-10-05"}',
            "day": b'{"schema_version":1,"campaign_id":1,"day":"2026-02-30"}',
            "scope": b'{"schema_version":1,"campaign_id":"1","day":"2026-10-05"}',
        }[case]
        headers = signed(raw)
    elif case == "query":
        target += "?canary=private"
    elif case == "trailing":
        target += "/"
    elif case == "off":
        monkeypatch.setattr(settings, "employee_referral_count_readback_enabled", "false")
        expected = 404
    elif case == "config":
        monkeypatch.setattr(settings, "employee_referral_count_readback_key", SecretStr("bad"))
        expected = 503
    elif case == "echo":
        monkeypatch.setattr(settings, "database_echo", True)
        expected = 503
    elif case == "unknown":
        raw = canonical({"schema_version": 1, "campaign_id": 999, "day": "2026-10-05"})
        headers = signed(raw)
        expected = 404
    elif case == "method":
        expected = 405
    response = await h.client.request(
        "GET" if case == "method" else "POST", target, content=raw, headers=headers
    )
    assert response.status_code == expected, response.text
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"detail"}
    assert "private" not in response.text


@pytest.mark.parametrize(
    "state",
    ["acknowledged", "pending", "leased", "blocked", "missing", "quarantine", "invalid-config"],
)
async def test_selected_generation_safe_replay_keeps_notice_and_fact(checkout, state):
    h = checkout
    await paid_order(h)
    async with h.sessions.begin() as session:
        root = (await session.scalars(select(PaidOutbox))).one()
        job = (await session.scalars(select(K2Delivery))).one()
        notice = (await session.scalars(select(NoticeDelivery))).one()
        notice.state = "sent"
        notice.completed_at = h.now
        notice.message_id = 1
        if state == "missing":
            await session.delete(job)
        elif state in {"acknowledged", "leased", "blocked", "quarantine"}:
            job.state = "blocked" if state == "quarantine" else state
            if state == "acknowledged":
                job.ack = {}
                job.completed_at = h.now
            if state == "leased":
                job.leased_until = h.now + timedelta(seconds=30)
            if state == "quarantine":
                job.error_category = "storage"
        event = root.event_id
        body = root.body
    _result, _count, code = await replay_k2(
        h.sessions, event, K2 if state != "invalid-config" else LaneConfig("false")
    )
    assert code == (
        2 if state == "invalid-config" else 1 if state in {"missing", "leased", "quarantine"} else 0
    )
    async with h.sessions() as session:
        assert (await session.get(PaidOutbox, root.id)).body == body
        assert (await session.get(NoticeDelivery, notice.id)).state == "sent"
        if code == 0:
            fresh = await session.get(K2Delivery, job.id)
            assert (
                fresh.state,
                fresh.generation,
                fresh.attempt_count,
                fresh.ack,
                fresh.completed_at,
            ) == ("pending", 1, 0, None, None)


async def test_scope_sql_log_and_late_sdk_positive_controls(checkout, caplog, monkeypatch):
    h = checkout
    original = count_store.observe

    async def probe(*args, **kwargs):
        logging.getLogger("sqlalchemy.engine").warning("private_canary")
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %s',
            "private_canary",
            "POST",
            PATH + "?private_canary",
            "1.1",
            200,
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr("src.employee_referrals.count_api.observe", probe)
    caplog.set_level(logging.INFO)
    assert (await read(h)).status_code == 200
    logging.getLogger("unrelated").warning("public_control")
    assert "private_canary" not in caplog.text
    assert "public_control" in caplog.text


async def test_strict_source_response_values_and_delta_bounds(checkout):
    from pydantic import ValidationError

    from src.employee_referrals.count_wire import Readback, RequestBody, Totals

    h = checkout
    order = await paid_order(h)
    paid = (await read(h)).json()
    await h.client.post(path(order["id"]), json=intent(order, 1), headers=await admin(h))
    returned = (await read(h)).json()
    for totals in [
        qty(1, 0) | {"paid_quantity": "01"},
        qty(1, 0) | {"net_quantity": "-0"},
        qty(1, 0) | {"net_quantity": "2"},
    ]:
        with pytest.raises(ValidationError):
            Totals.model_validate(totals)
    with pytest.raises(ValidationError):
        RequestBody.model_validate({"schema_version": 1, "campaign_id": 1, "day": "2026-1-01"})
    failures = [
        paid | {"campaign_id": "0"},
        paid | {"source_observed_at": "2026-10-05T00:00:00Z"},
        paid | {"source_state": "source_gap"},
        paid | {"totals": None},
        paid | {"events": [paid["events"][0], paid["events"][0]]},
        paid | {"totals": qty(0, 1)},
    ]
    for value in failures:
        with pytest.raises(ValidationError):
            Readback.model_validate(value)
    for delta in [
        None,
        [],
        [{"order_item_id": 2, "quantity": 1}, {"order_item_id": 1, "quantity": 1}],
        [{"order_item_id": 1, "quantity": 1}, {"order_item_id": 1, "quantity": 1}],
    ]:
        value = copy.deepcopy(returned)
        value["events"][1]["return_delta"] = delta
        with pytest.raises(ValidationError):
            Readback.model_validate(value)
    value = copy.deepcopy(paid)
    value["events"][0]["return_delta"] = [{"order_item_id": 1, "quantity": 1}]
    with pytest.raises(ValidationError):
        Readback.model_validate(value)


async def test_surviving_item_deletion_and_permanent_owner_validation(checkout):
    from src.employee_referrals.models import ReferralIdentity
    from src.orders.models import OrderItem

    h = checkout
    order = await paid_order(h)
    before = (await read(h)).json()
    async with h.sessions.begin() as session:
        await session.execute(delete(OrderItem).where(OrderItem.id == order["items"][0]["id"]))
    after = (await read(h)).json()
    assert after["totals"] == qty(3, 0)
    assert after["events"] == before["events"]
    async with h.sessions.begin() as session:
        identity = (await session.scalars(select(ReferralIdentity))).one()
        identity.employee_code = uuid4()
    value = (await read(h)).json()
    assert value["source_state"] == "inconsistent"
    assert value["totals"] is None


async def test_response_size_and_global_position_refuse(checkout, monkeypatch):
    import hashlib

    h = checkout
    order = await paid_order(h)
    await h.client.post(path(order["id"]), json=intent(order, 1), headers=await admin(h))
    monkeypatch.setattr("src.employee_referrals.count_api.MAX_RESPONSE", 10)
    assert (await read(h)).status_code == 413
    monkeypatch.setattr("src.employee_referrals.count_api.MAX_RESPONSE", 16777216)
    async with h.sessions.begin() as session:
        root = (await session.scalars(select(PaidOutbox))).one()
        returned = (await session.scalars(select(ReturnOutbox))).one()
        value = json.loads(returned.body)
        await session.execute(delete(K2Delivery).where(K2Delivery.return_id == returned.id))
        await session.execute(delete(NoticeDelivery).where(NoticeDelivery.return_id == returned.id))
        value["source_position"] = root.id
        returned.id = root.id
        returned.body = canonical(value)
        returned.body_sha256 = hashlib.sha256(returned.body).hexdigest()
    assert (await read(h)).status_code == 503


@pytest.mark.parametrize("offset", [-30, 30])
async def test_inclusive_auth_clock_boundary(checkout, monkeypatch, offset):
    monkeypatch.setattr("src.employee_referrals.count_api.time.time", lambda: 1770000000)
    raw = canonical({"schema_version": 1, "campaign_id": 1, "day": "2026-10-05"})
    assert (
        await checkout.client.post(PATH, content=raw, headers=signed(raw, offset=offset))
    ).status_code == 200


@pytest.mark.parametrize("late", [False, True])
async def test_count_boundary_late_errors_sdk_cors_and_positive_controls(
    checkout, monkeypatch, caplog, late
):
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration

    from src.employee_referrals.order_privacy import filter_referral_telemetry as filter_sdk

    captured = []
    sentry_sdk.init(
        dsn="https://public@example.test/1",
        transport=lambda e: captured.append(e),
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_sdk,
        before_send_transaction=filter_sdk,
        default_integrations=False,
    )
    marker = "COUNT_PRIVATE_CANARY"
    caplog.set_level(logging.INFO)
    try:
        from src.employee_referrals.count_privacy import CountPrivacy

        async def inner(scope, receive, send):
            logging.getLogger("analytics").warning(marker)
            sentry_sdk.capture_message(marker)
            if late:
                await send({"type": "http.response.start", "status": 200, "headers": []})
            raise RuntimeError(marker)

        sent = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        await CountPrivacy(inner)(
            {
                "type": "http",
                "path": PATH,
                "method": "POST",
                "headers": [],
                "query_string": marker.encode(),
            },
            receive,
            send,
        )
        assert sent[0]["status"] == (200 if late else 503)
        assert (b"cache-control", b"no-store") in sent[0]["headers"]
        assert marker not in caplog.text
        # A separate isolation scope represents the next unrelated ASGI request.
        with sentry_sdk.isolation_scope() as scope:
            scope.remove_tag("biotact_referral_count")
            sentry_sdk.capture_message("COUNT_PUBLIC_CONTROL")
        logging.getLogger("unrelated").warning("COUNT_PUBLIC_CONTROL")
        sentry_sdk.flush()
        assert marker not in repr(captured)
        assert "COUNT_PUBLIC_CONTROL" in repr(captured)
        assert "COUNT_PUBLIC_CONTROL" in caplog.text
        response = await checkout.client.options(
            PATH + "?private=" + marker,
            headers={
                "Origin": "https://frontend.example.test",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers["cache-control"] == "no-store"
        assert marker not in caplog.text
    finally:
        sentry_sdk.get_client().close()


async def test_two_permanent_pairs_other_campaign_and_unsigned_healthy_empty(checkout):
    from tests.test_employee_referral_paid import callback
    from tests.test_payments_lifecycle import _webhook_body

    h = checkout
    unpaid = (await h.create()).json()
    unsigned = _webhook_body(
        uuid="unsigned-count-control",
        invoice_id=unpaid["order_number"],
        amount=1000000,
        status="success",
    )
    response = await h.client.post("/api/v1/payments/webhook", json=unsigned)
    assert response.status_code == 200
    assert (await read(h)).json()["totals"] == qty(0, 0)
    from src.payments.models import PaymentEvent

    async with h.sessions() as session:
        assert (await session.scalars(select(PaymentEvent))).all()
        assert (await session.get(Order, unpaid["id"])).paid_at is None
    await paid_order(h)
    data = copy.deepcopy(h.data)
    data["event_id"] = str(uuid4())
    data["campaign"]["content_revision"] = 2
    participant = copy.deepcopy(data["participants"][0])
    participant.update(employee_code=str(uuid4()), token="AQEBAQEBAQEBAQEBAQEBAQ")
    data["participants"].append(participant)
    await h.publish(data)
    other = (await h.create(h.body(referral_token=participant["token"]))).json()
    assert (await callback(h, other, uuid="other-pair-count")).status_code == 200
    value = (await read(h)).json()
    assert value["totals"] == qty(4, 0)
    assert len({e["snapshot"]["attribution"]["employee_code"] for e in value["events"]}) == 2
    data["event_id"] = str(uuid4())
    data["campaign"]["id"] = 2
    data["campaign"]["content_revision"] = 1
    data["participants"] = [dict(participant, token="AgICAgICAgICAgICAgICAg")]
    await h.publish(data)
    second = (await h.create(h.body(referral_token=data["participants"][0]["token"]))).json()
    assert (await callback(h, second, uuid="other-campaign-count")).status_code == 200
    assert (await read(h)).json()["totals"] == qty(4, 0)
    raw = canonical(
        {
            "schema_version": 1,
            "campaign_id": 2,
            "day": h.now.astimezone(ZoneInfo("Asia/Tashkent")).date().isoformat(),
        }
    )
    assert (await h.client.post(PATH, content=raw, headers=signed(raw))).json()["totals"] == qty(
        1, 0
    )


async def foreign_history(h):
    from tests.test_employee_referral_paid import callback

    data = copy.deepcopy(h.data)
    data["event_id"] = str(uuid4())
    data["campaign"]["id"] = 2
    data["participants"][0]["token"] = "AQEBAQEBAQEBAQEBAQEBAQ"
    await h.publish(data)
    orders = []
    for index in range(3):
        order = (await h.create(h.body(referral_token=data["participants"][0]["token"]))).json()
        assert (await callback(h, order, uuid=f"foreign-count-{index}")).status_code == 200
        orders.append(order)
    return orders


@pytest.mark.parametrize("empty", [False, True])
async def test_campaign_event_bound_ignores_valid_foreign_history(checkout, monkeypatch, empty):
    h = checkout
    if not empty:
        await paid_order(h)
    await foreign_history(h)
    monkeypatch.setattr(count_store, "MAX_EVENTS", 2)
    response = await read(h)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["source_state"] == "retained_valid"
    assert value["totals"] == qty(0 if empty else 3, 0)
    assert len(value["events"]) == (0 if empty else 1)
    for _ in range(3 if empty else 2):
        await paid_order(h)
    refused = await read(h)
    assert refused.status_code == 413
    assert refused.json() == {"detail": {"code": "count_payload_too_large"}}


@pytest.mark.parametrize("corruption", ["hash", "attribution", "uuid", "position"])
async def test_foreign_damage_and_global_collisions_are_not_filtered(
    checkout, monkeypatch, corruption
):
    import hashlib

    from tests.test_employee_referral_paid import facts

    h = checkout
    await paid_order(h)
    orders = await foreign_history(h)
    assert (
        await h.client.post(
            path(orders[0]["id"]), json=intent(orders[0], 1), headers=await admin(h)
        )
    ).status_code == 200
    monkeypatch.setattr(count_store, "MAX_EVENTS", 2)
    own = (await facts(h))[0]
    async with h.sessions.begin() as session:
        returned = (await session.scalars(select(ReturnOutbox))).one()
        value = json.loads(returned.body)
        if corruption == "hash":
            returned.body_sha256 = "f" * 64
        elif corruption == "attribution":
            value["attribution"]["campaign_id"] = 0
        elif corruption == "uuid":
            value["event_id"] = str(own.event_id)
            returned.return_id = own.event_id
        else:
            await session.execute(delete(K2Delivery).where(K2Delivery.return_id == returned.id))
            await session.execute(
                delete(NoticeDelivery).where(NoticeDelivery.return_id == returned.id)
            )
            returned.id = own.id
            value["source_position"] = own.id
        if corruption != "hash":
            returned.body = canonical(value)
            returned.body_sha256 = hashlib.sha256(returned.body).hexdigest()
    response = await read(h)
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": {"code": "count_unavailable"}}


async def test_owned_root_checks_capture_even_when_campaign_changed(checkout):
    from src.employee_referrals.models import ReferralOrderSnapshot
    from tests.test_employee_referral_paid import facts

    h = checkout
    order = await paid_order(h)
    root = (await facts(h))[0]
    body = root.body
    async with h.sessions.begin() as session:
        header = await session.get(ReferralOrderSnapshot, order["id"])
        header.campaign_id = 2
    response = await read(h)
    assert response.status_code == 200
    value = response.json()
    assert value["source_state"] == "inconsistent"
    assert value["totals"] is value["daily"] is None
    async with h.sessions.begin() as session:
        assert (await session.get(PaidOutbox, root.id)).body == body
        await session.execute(delete(Order).where(Order.id == order["id"]))
    deleted = (await read(h)).json()
    assert deleted["source_state"] == "retained_valid"
    assert deleted["totals"] == qty(3, 0)
    async with h.sessions() as session:
        assert (await session.get(PaidOutbox, root.id)).body == body
