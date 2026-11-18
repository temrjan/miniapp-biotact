"""G5b: real mounted current-admin confirmations and independent PG durability."""

import asyncio
import copy
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sqlalchemy import delete, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import database
from src.auth.admin_router import create_admin_token
from src.config import settings
from src.employee_referrals import returns
from src.employee_referrals.models import CampaignReplica, ReferralIdentity
from src.employee_referrals.order_privacy import filter_referral_telemetry
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.schemas import utc_text
from src.orders.models import Order
from src.payments.service import multicard_client
from src.products.models import Product
from src.users.models import User
from tests.test_employee_referral_k1_receiver import Captured
from tests.test_employee_referral_order_capture import checkout as checkout
from tests.test_employee_referral_order_transactions import wait_for_lock
from tests.test_employee_referral_paid import callback, facts
from tests.test_payments_lifecycle import SECRET, _webhook_body


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(multicard_client, "secret", SECRET)


def path(order_id):
    return f"/api/v1/admin/orders/{order_id}/referral-returns"


async def admin(h, user_id=2):
    async with h.sessions.begin() as session:
        if await session.get(User, user_id) is None:
            session.add(
                User(id=user_id, telegram_id=990000 + user_id, is_admin=True, is_active=True)
            )
    return {"Authorization": "Bearer " + create_admin_token(user_id, 990000 + user_id)}


async def rows(h):
    async with h.sessions() as session:
        return list(
            await session.scalars(select(ReturnOutbox).order_by(ReturnOutbox.order_version))
        )


async def paid_order(h, points=0, quantity=2):
    order = (
        await h.create(
            h.body(
                items=[
                    {"product_id": 39, "quantity": quantity},
                    {"product_id": 40, "quantity": 1},
                    {"product_id": 39, "quantity": 1},
                ],
                points_to_apply=points,
            )
        )
    ).json()
    if points != 50000:
        assert (await callback(h, order, uuid=f"count-success-{order['id']}")).status_code == 200
    return order


def intent(order, *quantities, return_id=None):
    items = [item for item in order["items"] if item["product_id"] == 39]
    return {
        "return_id": str(uuid4()) if return_id is None else return_id,
        "lines": [
            {"order_item_id": str(item["id"]), "quantity": str(quantity)}
            for item, quantity in zip(items, quantities, strict=False)
            if quantity
        ],
    }


@pytest.mark.parametrize("points", [0, 10000, 50000])
async def test_paid_partial_full_replay_retention_and_actual_producer(
    checkout, monkeypatch, points
):
    h = checkout
    headers = await admin(h)
    order = await paid_order(h, points)
    root = (await facts(h))[0]
    summary = (await h.client.get(path(order["id"]), headers=headers)).json()
    assert summary == {
        "order_id": str(order["id"]),
        "order_version": "1",
        "lines": [
            {
                "order_item_id": str(item["id"]),
                "product_id": "39",
                "paid_quantity": str(item["quantity"]),
                "returned_quantity": "0",
            }
            for item in order["items"]
            if item["product_id"] == 39
        ],
    }
    assert (
        sum(
            int(line["paid_quantity"]) - int(line["returned_quantity"]) for line in summary["lines"]
        )
        == 3
    )
    a = intent(order, 1, return_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = await h.client.post(path(order["id"]), json=a, headers=headers)
    assert first.status_code == 200, first.text
    receipt = first.json()["receipt"]
    assert receipt["lines"] == a["lines"]
    assert receipt["order_version"] == "2"
    assert not first.json()["replayed"]
    assert (await h.client.post(path(order["id"]), json=a, headers=await admin(h, 3))).json() == {
        "receipt": receipt,
        "replayed": True,
    }
    summary = (await h.client.get(path(order["id"]), headers=headers)).json()
    assert [line["returned_quantity"] for line in summary["lines"]] == ["1", "0"]
    assert (
        sum(
            int(line["paid_quantity"]) - int(line["returned_quantity"]) for line in summary["lines"]
        )
        == 2
    )
    async with h.sessions.begin() as session:
        campaign = await session.get(CampaignReplica, 1)
        campaign.state = "ended"
        campaign.ended_at = h.now
        identity = await session.get(ReferralIdentity, "AAAAAAAAAAAAAAAAAAAAAA")
        identity.in_current_registry = False
        await session.execute(delete(Product).where(Product.id == 39))
        if points == 0:
            await session.execute(delete(Order).where(Order.id == order["id"]))
    monkeypatch.setattr(settings, "employee_referral_capture_enabled", False)
    b = intent(order, 1, 1, return_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    b["lines"].reverse()
    second = await h.client.post(path(order["id"]), json=b, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["receipt"]["order_version"] == "3"
    summary = (await h.client.get(path(order["id"]), headers=headers)).json()
    assert [line["returned_quantity"] for line in summary["lines"]] == ["2", "1"]
    assert (
        sum(
            int(line["paid_quantity"]) - int(line["returned_quantity"]) for line in summary["lines"]
        )
        == 0
    )
    assert (await h.client.post(path(order["id"]), json=a, headers=headers)).json()[
        "receipt"
    ] == receipt
    stored = await rows(h)
    assert len(stored) == 2
    assert stored[0].confirmed_by == 2
    assert (await facts(h))[0].body == root.body
    assert len({root.id, *[row.id for row in stored]}) == 3
    for row in stored:
        value = json.loads(row.body)
        assert hashlib.sha256(row.body).hexdigest() == row.body_sha256
        assert value["event_id"] == str(row.return_id)
        assert value["source_position"] == row.id
        assert value["occurred_at"] == value["source_recorded_at"] == utc_text(row.created_at)
        assert value["attribution"] == json.loads(root.body)["attribution"]
        assert all(
            set(line) == {"order_item_id", "product_id", "quantity", "returned_quantity"}
            for line in value["lines"]
        )
    output = os.environ.get("G5B_PRODUCER_DIR")
    if output and points == 0:
        target = Path(output)
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        for name, row in zip(
            ("paid-v1.json", "refund-v2.json", "refund-v3.json"), [root, *stored], strict=True
        ):
            await asyncio.to_thread((target / name).write_bytes, row.body)


async def test_non_sources_status_revert_and_bounds(checkout):
    h = checkout
    headers = await admin(h)
    unpaid = (await h.create()).json()
    ordinary = (await h.create(h.body(referral_token=None))).json()
    await callback(h, ordinary, uuid="ordinary-no-return")
    async with h.sessions.begin() as session:
        (await session.get(Order, unpaid["id"])).payment_status = "paid"
    for order in (unpaid, ordinary):
        for method in ("get", "post"):
            response = await h.client.request(
                method, path(order["id"]), json=intent(order, 1), headers=headers
            )
            assert response.status_code == 404
    order = await paid_order(h)
    body = _webhook_body(
        uuid="count-success", invoice_id=order["order_number"], amount=5000000, status="revert"
    )
    assert (await h.client.post("/api/v1/payments/webhook", json=body)).status_code == 200
    assert (
        await h.client.patch(
            f"/api/v1/admin/orders/{order['id']}/status",
            json={"status": "cancelled"},
            headers=headers,
        )
    ).status_code == 200
    assert await rows(h) == []
    bad = [intent(order, 3), intent(order, -1), intent(order, "01"), intent(order, 2**63)]
    zero = intent(order, 1)
    zero["lines"][0]["quantity"] = "0"
    bad.append(zero)
    noncampaign = next(item for item in order["items"] if item["product_id"] == 40)
    bad += [
        {
            "return_id": str(uuid4()),
            "lines": [{"order_item_id": str(noncampaign["id"]), "quantity": "1"}],
        }
    ]
    duplicate = intent(order, 1)
    duplicate["lines"] *= 2
    bad += [
        duplicate,
        {"return_id": str(uuid4()), "lines": []},
        dict(intent(order, 1), extra="private"),
    ]
    for index, value in enumerate(bad):
        response = await h.client.post(path(order["id"]), json=value, headers=headers)
        assert response.status_code == (409 if index == 0 else 422), response.text
    assert await rows(h) == []
    good = intent(order, 2, 1)
    assert (await h.client.post(path(order["id"]), json=good, headers=headers)).status_code == 200
    assert (
        await h.client.post(path(order["id"]), json=intent(order, 1), headers=headers)
    ).status_code == 409
    changed = copy.deepcopy(good)
    changed["lines"][0]["quantity"] = "1"
    assert (
        await h.client.post(path(order["id"]), json=changed, headers=headers)
    ).status_code == 409
    assert (await h.client.post(path(9999), json=good, headers=headers)).status_code == 409
    paid_id = str((await facts(h))[0].event_id)
    assert (
        await h.client.post(
            path(order["id"]), json=intent(order, 1, return_id=paid_id), headers=headers
        )
    ).status_code == 409
    assert len(await rows(h)) == 1


async def test_auth_refusal_before_body_and_private_history(checkout):
    h = checkout
    headers = await admin(h)
    order = await paid_order(h)
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(h.engine.sync_engine, "before_cursor_execute", observe)
    try:
        cases = [({}, 401), ({"Authorization": "Bearer bad"}, 401)]
        for changes, status in [
            ({"type": "access"}, 403),
            ({"exp": 0}, 401),
            ({"sub": "bad"}, 401),
            ({"sub": "99999"}, 403),
        ]:
            payload = dict(sub="2", type="admin", exp=int(time.time()) + 300, **{})
            payload.update(changes)
            token = jwt.encode(
                payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm
            )
            cases.append(({"Authorization": "Bearer " + token}, status))
        wrong_key = jwt.encode(
            {"sub": "2", "type": "admin", "exp": int(time.time()) + 300},
            "different-synthetic-key",
            algorithm=settings.algorithm,
        )
        cases.append(({"Authorization": "Bearer " + wrong_key}, 401))
        for auth, expected in cases:
            statements.clear()
            response = await h.client.post(path(order["id"]), content=b"bad" * 400000, headers=auth)
            assert response.status_code == expected, response.text
            assert not any("employee_referral_" in statement for statement in statements)
        for field in ("is_active", "is_admin"):
            async with h.sessions.begin() as session:
                setattr(await session.get(User, 2), field, False)
            assert (await h.client.get(path(order["id"]), headers=headers)).status_code == 403
            async with h.sessions.begin() as session:
                setattr(await session.get(User, 2), field, True)
        async with h.sessions.begin() as session:
            await session.execute(delete(User).where(User.id == 2))
        assert (await h.client.get(path(order["id"]), headers=headers)).status_code == 403
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", observe)
    assert await rows(h) == []


@pytest.mark.parametrize("same_id", [True, False])
async def test_observed_root_lock_races_last_unit(checkout, same_id):
    h = checkout
    headers = await admin(h)
    order = (await h.create()).json()
    await callback(h, order)
    a = intent(order, 1)
    b = a if same_id else intent(order, 1)
    async with h.sessions() as locker:
        await locker.scalar(
            select(PaidOutbox).where(PaidOutbox.order_id == order["id"]).with_for_update()
        )
        tasks = [
            asyncio.create_task(h.client.post(path(order["id"]), json=value, headers=headers))
            for value in (a, b)
        ]
        try:
            await wait_for_lock(h)
            assert all(not task.done() for task in tasks)
            await locker.commit()
            responses = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert sorted(response.status_code for response in responses) == (
        [200, 200] if same_id else [200, 409]
    )
    if same_id:
        assert sum(response.json()["replayed"] for response in responses) == 1
    assert len(await rows(h)) == 1


@pytest.mark.parametrize("failure", ["insert", "commit", "cancel", "serialization", "lost"])
async def test_atomic_failure_and_unknown_commit_retry(checkout, monkeypatch, failure):
    h = checkout
    headers = await admin(h)
    order = await paid_order(h)
    original = (await facts(h))[0].body
    value = intent(order, 1)

    class CommitBoundary(AsyncSession):
        async def commit(self):
            if failure == "commit":
                raise RuntimeError("PRIVATE_COMMIT")
            await super().commit()
            if failure == "lost":
                raise RuntimeError("PRIVATE_LOST_CONFIRMATION")

    if failure in ("commit", "lost"):
        monkeypatch.setattr(
            database,
            "async_session_maker",
            async_sessionmaker(h.engine, class_=CommitBoundary, expire_on_commit=False),
        )

    def insert(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO employee_referral_return_outbox"):
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "insert":
                raise SQLAlchemyError("PRIVATE_INSERT")

    def serialization(*args, **kwargs):
        raise ValueError("PRIVATE_SERIALIZATION")

    if failure == "serialization":
        monkeypatch.setattr(returns, "freeze_snapshot", serialization)
    event.listen(h.engine.sync_engine, "before_cursor_execute", insert)
    try:
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await h.client.post(path(order["id"]), json=value, headers=headers)
        else:
            response = await h.client.post(
                path(order["id"]),
                content=json.dumps(value).encode()
                if failure != "serialization"
                else (
                    '{"return_id":"'
                    + value["return_id"]
                    + '","lines":[{"order_item_id":"'
                    + value["lines"][0]["order_item_id"]
                    + '","quantity":"1"}]}'
                ).encode(),
                headers=headers | {"Content-Type": "application/json"},
            )
            assert response.status_code == 503
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", insert)
    assert len(await rows(h)) == (1 if failure == "lost" else 0)
    assert (await facts(h))[0].body == original
    if failure == "lost":
        monkeypatch.setattr(database, "async_session_maker", h.sessions)
        response = await h.client.post(path(order["id"]), json=value, headers=headers)
        assert response.status_code == 200
        assert response.json()["replayed"]
        assert len(await rows(h)) == 1


async def test_privacy_media_path_echo_and_positive_controls(checkout, monkeypatch, caplog, capsys):
    h = checkout
    headers = await admin(h)
    order = await paid_order(h)
    marker = "RETURN_PRIVATE_CANARY"
    caplog.set_level(logging.DEBUG)
    captured = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=captured,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )
    original = returns.read_history

    async def noisy(*args, **kwargs):
        logging.getLogger("sqlalchemy.engine.Engine").error(marker, exc_info=True)
        sentry_sdk.capture_message(marker)
        return await original(*args, **kwargs)

    monkeypatch.setattr(returns, "read_history", noisy)
    monkeypatch.setattr("src.employee_referrals.return_router.read_history", noisy)
    try:
        assert (await h.client.get(path(order["id"]), headers=headers)).status_code == 200
        for suffix in ("?secret=" + marker, "/", ""):
            response = await h.client.post(
                path(order["id"]) + suffix,
                content=marker,
                headers=headers | {"Content-Type": "application/json"},
            )
            assert response.status_code == 422
            assert response.headers["cache-control"] == "no-store"
        assert (await h.client.delete(path(order["id"]), headers=headers)).json() == {
            "detail": {"code": "referral_return_invalid_request"}
        }
        for content, content_headers, status in [
            (b"{}", {"Content-Type": "text/plain"}, 415),
            (b"x" * 1048577, {"Content-Type": "application/json"}, 413),
            (b'{"lines":[],"lines":[]}', {"Content-Type": "application/json"}, 422),
        ]:
            assert (
                await h.client.post(
                    path(order["id"]), content=content, headers=headers | content_headers
                )
            ).status_code == status
        await h.client.options(
            path(order["id"]) + "?secret=" + marker,
            headers={"Origin": "https://test", "Access-Control-Request-Method": "POST"},
        )
        statements = []

        def observe(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(h.engine.sync_engine, "before_cursor_execute", observe)
        h.engine.echo = "debug"
        try:
            response = await h.client.get(path(order["id"]), headers=headers)
            assert response.status_code == 503
            assert not statements
        finally:
            h.engine.echo = False
            event.remove(h.engine.sync_engine, "before_cursor_execute", observe)
        sentry_sdk.flush()
        assert captured.events == []
        output = capsys.readouterr()
        assert marker not in caplog.text + output.out + output.err + json.dumps(captured.events)
        logging.getLogger("uvicorn.access").warning("RETURN_UNRELATED_POSITIVE")
        sentry_sdk.capture_message("RETURN_UNRELATED_POSITIVE")
        sentry_sdk.flush()
        assert "RETURN_UNRELATED_POSITIVE" in caplog.text
        assert any(event.get("message") == "RETURN_UNRELATED_POSITIVE" for event in captured.events)
    finally:
        sentry_sdk.get_client().close()


async def test_cross_order_uuid_unique_race_and_shared_positions(checkout, monkeypatch):
    h = checkout
    auth = await admin(h)
    first, second = await paid_order(h), await paid_order(h)
    entered, release = asyncio.Event(), asyncio.Event()
    original = returns.lookup
    seen = {}
    shared = str(uuid4())

    async def simultaneous(session, value, order_id):
        found = await original(session, value, order_id)
        # Both orders have passed global ID check before either INSERT.
        if value.return_id == shared:
            seen[order_id] = seen.get(order_id, 0) + 1
            if seen[order_id] == 2:
                if sum(seen.values()) == 4:
                    entered.set()
                await release.wait()
        return found

    monkeypatch.setattr(returns, "lookup", simultaneous)
    reached_commit, release_commit = asyncio.Event(), asyncio.Event()

    class HeldCommit(AsyncSession):
        async def commit(self):
            reached_commit.set()
            await release_commit.wait()
            await super().commit()

    monkeypatch.setattr(
        database,
        "async_session_maker",
        async_sessionmaker(h.engine, class_=HeldCommit, expire_on_commit=False),
    )
    tasks = [
        asyncio.create_task(
            h.client.post(path(order["id"]), json=intent(order, 1, return_id=shared), headers=auth)
        )
        for order in (first, second)
    ]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        await asyncio.wait_for(reached_commit.wait(), 5)
        await wait_for_lock(h)
        release_commit.set()
        results = await asyncio.gather(*tasks)
    finally:
        release.set()
        release_commit.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert sorted(response.status_code for response in results) == [200, 409]
    assert len(await rows(h)) == 1
    responses = await asyncio.gather(
        *[
            h.client.post(path(order["id"]), json=intent(order, 1), headers=auth)
            for order in (first, second)
        ]
    )
    assert all(response.status_code == 200 for response in responses)
    all_rows = await rows(h)
    assert len(all_rows) == 3
    assert len({row.id for row in all_rows} | {row.id for row in await facts(h)}) == 5


@pytest.mark.parametrize(
    "corruption", ["delta", "hash", "version", "identity", "item", "bounds", "wire"]
)
async def test_corrupted_retained_history_fails_closed(checkout, corruption):
    h = checkout
    auth = await admin(h)
    order = await paid_order(h)
    value = intent(order, 1)
    assert (await h.client.post(path(order["id"]), json=value, headers=auth)).status_code == 200
    # Metadata-created test schema has no trigger; real migration trigger is tested separately.
    async with h.sessions.begin() as session:
        row = (await session.scalars(select(ReturnOutbox))).one()
        if corruption == "delta":
            row.request_lines = [
                {"order_item_id": row.request_lines[0]["order_item_id"], "quantity": "2"}
            ]
        elif corruption == "hash":
            row.body_sha256 = "0" * 64
        else:
            body = json.loads(row.body)
            if corruption == "version":
                row.order_version = body["order_version"] = 3
            elif corruption == "identity":
                body["attribution"]["content_revision"] += 1
            elif corruption == "item":
                row.request_lines = [{"order_item_id": "99999", "quantity": "1"}]
            elif corruption == "bounds":
                row.request_lines = [
                    {"order_item_id": row.request_lines[0]["order_item_id"], "quantity": "3"}
                ]
            else:
                body["lines"][0]["returned_quantity"] = 3
            row.body = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            row.body_sha256 = hashlib.sha256(row.body).hexdigest()
    for method in ("get", "post"):
        response = await h.client.request(
            method, path(order["id"]), json=intent(order, 1), headers=auth
        )
        assert response.status_code == 503
    assert len(await rows(h)) == 1

    response = await h.client.post(path(order["id"]), json=value, headers=auth)
    assert response.status_code == 503  # Replay also validates authoritative history.


async def test_real_return_get_late_send_privacy_and_decoded_invalid_paths(
    checkout, caplog, capsys
):
    h = checkout
    auth = await admin(h)
    order = await paid_order(h)
    marker = "RETURN_LATE_SEND_CANARY"
    caplog.set_level(logging.DEBUG)
    captured = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=captured,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )
    try:
        for suffix in ("?private=" + marker, "/"):
            response = await h.client.get(path(order["id"]) + suffix, headers=auth)
            assert response.status_code == 422
            assert response.headers["cache-control"] == "no-store"
        for identity in ("0", "01", "9223372036854775808", marker):
            response = await h.client.get(path(identity), headers=auth)
            assert response.status_code == 422
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path(order["id"]),
            "raw_path": path(order["id"]).encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
            "headers": [(b"authorization", auth["Authorization"].encode())],
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                assert (b"cache-control", b"no-store") in message["headers"]
                return
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %s', marker, "GET", marker, "1.1", 200
            )
            logging.getLogger("sqlalchemy.engine.Engine").error(marker)
            sentry_sdk.capture_exception(RuntimeError(marker))
            raise RuntimeError(marker)

        await h.app(scope, receive, send)
        assert len(messages) == 2
        sentry_sdk.flush()
        assert captured.events == []
        output = capsys.readouterr()
        assert marker not in caplog.text + output.out + output.err + json.dumps(captured.events)
        logging.getLogger("uvicorn.access").warning("RETURN_LATE_UNRELATED_POSITIVE")
        sentry_sdk.capture_message("RETURN_LATE_UNRELATED_POSITIVE")
        with sentry_sdk.start_transaction(name="RETURN_LATE_UNRELATED_TRANSACTION"):
            pass
        sentry_sdk.flush()
        assert "RETURN_LATE_UNRELATED_POSITIVE" in caplog.text
        assert any(
            event.get("message") == "RETURN_LATE_UNRELATED_POSITIVE" for event in captured.events
        )
        assert any(
            event.get("transaction") == "RETURN_LATE_UNRELATED_TRANSACTION"
            for event in captured.events
        )
    finally:
        sentry_sdk.get_client().close()
