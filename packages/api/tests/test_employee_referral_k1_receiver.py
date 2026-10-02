"""Mounted K1 receiver with independent PostgreSQL readers and real transactions."""

import asyncio
import copy
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.transport import Transport
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.employee_referrals.models import CampaignReplica, K1Receipt, ReferralIdentity
from src.employee_referrals.privacy import filter_k1
from src.employee_referrals.schemas import utc_text
from src.main import create_app
from tests.test_employee_referral_k1_contract import PATH, WIRE, encoded, signed
from tests.test_employee_referral_k1_contract import enabled as enabled


@dataclass
class Harness:
    app: object
    client: AsyncClient
    sessions: async_sessionmaker
    data: dict
    engine: object

    async def post(self, data=None, *, raw=None):
        raw = encoded(self.data if data is None else data) if raw is None else raw
        return await self.client.post(PATH, content=raw, headers=signed(raw))

    async def rows(self):
        async with self.sessions() as reader:
            replica = await reader.get(CampaignReplica, self.data["campaign"]["id"])
            tokens = list(
                await reader.scalars(
                    select(ReferralIdentity).order_by(ReferralIdentity.employee_code)
                )
            )
            receipts = list(await reader.scalars(select(K1Receipt)))
            return replica, tokens, receipts


@pytest_asyncio.fixture
async def k1(test_db, enabled):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()
    app.state.employee_referral_sessions = maker
    async with maker() as reader:
        now = (await reader.execute(select(func.clock_timestamp()))).scalar_one()
    data = json.loads(WIRE)
    data["issued_at"] = utc_text(now - timedelta(seconds=2))
    data["campaign"]["published_at"] = utc_text(now - timedelta(days=1))
    data["campaign"]["start_at"] = utc_text(now - timedelta(hours=1))
    data["campaign"]["end_at"] = utc_text(now + timedelta(days=1))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield Harness(app, client, maker, data, engine)
    await engine.dispose()


def next_event(data, *, revision=None, issued_at=None):
    result = copy.deepcopy(data)
    result["event_id"] = str(uuid4())
    if revision is not None:
        result["campaign"]["content_revision"] = revision
    if issued_at is not None:
        result["issued_at"] = issued_at
    return result


async def test_commit_replay_and_conflicting_raw_bytes(k1):
    first = await k1.post()
    assert first.status_code == 200, first.text
    assert first.json()["receipt"]["outcome"] == "applied"
    assert first.json()["registry"]["participant_count"] == 1
    replica, tokens, receipts = await k1.rows()
    assert len(tokens) == len(receipts) == 1
    assert replica.valid_until - replica.issued_at == timedelta(seconds=60)
    replay = await k1.post()
    assert replay.json()["replayed"] is True
    assert replay.json()["receipt"] == first.json()["receipt"]
    assert replay.json()["registry"]["valid_until"] == first.json()["registry"]["valid_until"]
    conflict = await k1.post(raw=encoded(k1.data) + b" ")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "k1_event_conflict"
    assert len((await k1.rows())[2]) == 1


async def test_full_roster_omission_reappearance_and_stable_pair(k1):
    assert (await k1.post()).status_code == 200
    second = next_event(k1.data, revision=2)
    second["participants"] = []
    assert (await k1.post(second)).json()["registry"]["participant_count"] == 0
    assert (await k1.rows())[1][0].in_current_registry is False
    third = next_event(k1.data, revision=3)
    third["participants"][0]["employee_display_name"] = "Updated name"
    third["participants"].append(
        {
            "employee_code": str(uuid4()),
            "token": "AQEBAQEBAQEBAQEBAQEBAQ",
            "employee_display_name": "Late participant",
        }
    )
    assert (await k1.post(third)).json()["registry"]["participant_count"] == 2
    _, identities, receipts = await k1.rows()
    assert len(identities) == 2
    assert len(receipts) == 3
    assert all(p.in_current_registry for p in identities)
    bad = next_event(third, revision=4)
    bad["participants"][0]["token"] = "AgICAgICAgICAgICAgICAg"
    response = await k1.post(bad)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "k1_identity_conflict"
    other = next_event(k1.data)
    other["campaign"]["id"] = 2
    assert (await k1.post(other)).status_code == 409
    async with k1.sessions() as reader:
        assert await reader.get(CampaignReplica, 2) is None
    other["participants"][0]["token"] = "AwMDAwMDAwMDAwMDAwMDAw"
    assert (await k1.post(other)).status_code == 200


async def test_renewal_revision_order_and_immutable_content(k1):
    from datetime import datetime

    current = datetime.fromisoformat(k1.data["issued_at"])
    k1.data["issued_at"] = utc_text(current - timedelta(seconds=20))
    k1.data["campaign"]["content_revision"] = 5
    assert (await k1.post()).status_code == 200
    renewed = next_event(k1.data, issued_at=utc_text(current))
    response = await k1.post(renewed)
    assert response.json()["receipt"]["outcome"] == "renewed"
    deadline = response.json()["registry"]["valid_until"]
    unchanged = await k1.post(next_event(k1.data))
    assert unchanged.json()["receipt"]["outcome"] == "unchanged"
    assert unchanged.json()["registry"]["valid_until"] == deadline
    lower = next_event(renewed, revision=3, issued_at=utc_text(current + timedelta(seconds=1)))
    lower["participants"][0]["employee_display_name"] = "Old name"
    lower["participants"].append(
        {
            "employee_code": str(uuid4()),
            "token": "AgICAgICAgICAgICAgICAg",
            "employee_display_name": "Not current",
        }
    )
    response = await k1.post(lower)
    assert response.json()["receipt"]["outcome"] == "superseded"
    assert response.json()["registry"]["valid_until"] == deadline
    _, identities, _ = await k1.rows()
    assert len(identities) == 1
    assert identities[0].employee_display_name != "Old name"
    conflict = next_event(lower)
    conflict["participants"] = []
    assert (await k1.post(conflict)).json()["detail"]["code"] == "k1_revision_conflict"
    higher = next_event(k1.data, revision=6)
    response = await k1.post(higher)
    assert response.json()["receipt"]["outcome"] == "applied"
    assert response.json()["registry"]["valid_until"] < deadline
    same = next_event(higher)
    same["participants"] = []
    assert (await k1.post(same)).json()["detail"]["code"] == "k1_revision_conflict"


async def test_end_is_terminal_but_old_replay_and_superseded_work(k1):
    original = await k1.post()
    ended = next_event(k1.data, revision=2)
    ended["campaign"]["state"] = "ended"
    ended["campaign"]["ended_at"] = k1.data["issued_at"]
    assert (await k1.post(ended)).status_code == 200
    replay = (await k1.post()).json()
    assert replay["replayed"]
    assert replay["registry"]["state"] == "ended"
    assert replay["receipt"] == original.json()["receipt"]
    assert (await k1.post(next_event(k1.data))).json()["receipt"]["outcome"] == "superseded"
    revived = await k1.post(next_event(k1.data, revision=3))
    assert revived.status_code == 409
    assert revived.json()["detail"]["code"] == "k1_campaign_conflict"
    changed_end = next_event(ended, revision=3)
    changed_end["campaign"]["ended_at"] = ended["campaign"]["published_at"]
    assert (await k1.post(changed_end)).status_code == 409


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "changed"),
        ("product_id", 40),
        ("published_at", "2020-01-01T00:00:00Z"),
        ("start_at", "2020-01-01T00:00:00Z"),
        ("end_at", "2030-01-01T00:00:00Z"),
    ],
)
async def test_campaign_metadata_is_immutable(k1, field, value):
    assert (await k1.post()).status_code == 200
    modified = next_event(k1.data, revision=2)
    modified["campaign"][field] = value
    assert (await k1.post(modified)).json()["detail"]["code"] == "k1_campaign_conflict"
    assert len((await k1.rows())[2]) == 1


async def test_future_rolls_back_first_insert_and_expired_ended_bootstraps(k1):
    future = next_event(k1.data, issued_at="9999-12-31T23:59:59Z")
    assert (await k1.post(future)).status_code == 422
    assert (await k1.rows()) == (None, [], [])
    data = k1.data
    data["issued_at"] = data["campaign"]["published_at"]
    data["campaign"]["state"] = "ended"
    data["campaign"]["ended_at"] = data["issued_at"]
    response = await k1.post()
    assert response.status_code == 200
    assert response.json()["registry"]["valid_until"] < response.json()["registry"]["observed_at"]


async def test_same_event_concurrency_and_global_collisions(k1):
    responses = await asyncio.gather(*(k1.post() for _ in range(6)))
    assert [r.status_code for r in responses] == [200] * 6
    assert sum(not r.json()["replayed"] for r in responses) == 1
    assert len((await k1.rows())[2]) == 1
    one = next_event(k1.data)
    one["campaign"]["id"] = 2
    one["participants"][0]["token"] = "AQEBAQEBAQEBAQEBAQEBAQ"
    two = copy.deepcopy(one)
    two["campaign"]["id"] = 3
    responses = await asyncio.gather(k1.post(one), k1.post(two))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len((await k1.rows())[2]) == 2
    three = next_event(one)
    three["campaign"]["id"] = 4
    three["participants"][0]["token"] = "AgICAgICAgICAgICAgICAg"
    four = next_event(three)
    four["campaign"]["id"] = 5
    responses = await asyncio.gather(k1.post(three), k1.post(four))
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with k1.sessions() as reader:
        assert await reader.scalar(select(func.count()).select_from(CampaignReplica)) == 3


async def test_competing_versions_have_a_single_current_roster(k1):
    higher = next_event(k1.data, revision=2)
    higher["participants"] = []
    responses = await asyncio.gather(k1.post(), k1.post(higher))
    assert [r.status_code for r in responses] == [200, 200]
    replica, identities, receipts = await k1.rows()
    assert replica.content_revision == 2
    assert not any(i.in_current_registry for i in identities)
    assert len(receipts) == 2


@pytest.mark.parametrize("point", ["receipt", "commit", "cancel"])
async def test_failures_rollback_every_part_and_allow_retry(k1, point):
    def fail_sql(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO employee_referral_k1_receipts"):
            if point == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("SYNTHETIC_PRIVATE_DB_PARAMETERS")

    def fail_commit(conn):
        raise RuntimeError("SYNTHETIC_PRIVATE_COMMIT_PARAMETERS")

    target = k1.engine.sync_engine
    if point == "commit":
        event.listen(target, "commit", fail_commit)
    else:
        event.listen(target, "before_cursor_execute", fail_sql)
    try:
        if point == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await k1.post()
        else:
            response = await k1.post()
            assert response.status_code == 503
            assert "PRIVATE" not in response.text
    finally:
        if point == "commit":
            event.remove(target, "commit", fail_commit)
        else:
            event.remove(target, "before_cursor_execute", fail_sql)
    assert (await k1.rows()) == (None, [], [])
    assert (await k1.post()).json()["receipt"]["outcome"] == "applied"


async def test_lost_ack_after_commit_is_recovered_by_replay(k1):
    async def lose_response(scope, receive, send):
        async def fail_send(message):
            if message["type"] == "http.response.start":
                raise ConnectionError("synthetic lost ACK")
            await send(message)

        await k1.app(scope, receive, fail_send)

    async with AsyncClient(
        transport=ASGITransport(app=lose_response), base_url="http://test"
    ) as client:
        with pytest.raises(ConnectionError):
            await client.post(PATH, content=encoded(k1.data), headers=signed(encoded(k1.data)))
    assert len((await k1.rows())[2]) == 1
    assert (await k1.post()).json()["replayed"] is True


class Captured(Transport):
    def __init__(self):
        super().__init__()
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.headers.get("type") in ("event", "transaction"):
                self.events.append(item.payload.json)


async def test_privacy_with_actual_sdk_sql_failures_and_positive_controls(k1, caplog):
    capture = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=capture,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_k1,
        before_send_transaction=filter_k1,
    )
    marker = "K1_SYNTHETIC_PRIVATE_NAME"
    k1.data["participants"][0]["employee_display_name"] = marker

    # Positive private-channel control at the SDK boundary: real SQL still runs.
    # Removing the filter must let this private event through the actual transport.
    def sdk_probe(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO employee_referral_k1_receipts"):
            sentry_sdk.capture_message(marker)

    event.listen(k1.engine.sync_engine, "before_cursor_execute", sdk_probe)

    @k1.app.get("/privacy-positive-control")
    async def control():
        logging.getLogger("privacy-test").warning("ORDINARY_POSITIVE_CONTROL")
        sentry_sdk.capture_message("ORDINARY_POSITIVE_CONTROL")
        return {"ok": True}

    try:
        with caplog.at_level(logging.INFO):
            assert (await k1.post()).status_code == 200
            invalid = next_event(k1.data)
            invalid["private_extra"] = marker
            assert (await k1.post(invalid)).status_code == 422
            collision = next_event(k1.data, revision=2)
            collision["participants"][0]["token"] = "AQEBAQEBAQEBAQEBAQEBAQ"
            assert (await k1.post(collision)).status_code == 409
            # Real SQL exception containing a bound private marker, after partial update.
            async with k1.sessions.begin() as session:
                await session.execute(
                    text(
                        "CREATE FUNCTION k1_private_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'K1_SYNTHETIC_PRIVATE_NAME' USING ERRCODE = '23514'; END $$"
                    )
                )
                await session.execute(
                    text(
                        "CREATE TRIGGER k1_private_failure BEFORE INSERT ON employee_referral_k1_receipts FOR EACH ROW EXECUTE FUNCTION k1_private_failure()"
                    )
                )
            try:
                response = await k1.post(next_event(k1.data, revision=2))
                assert response.status_code == 503
                assert marker not in response.text
            finally:
                async with k1.sessions.begin() as session:
                    await session.execute(
                        text("DROP TRIGGER k1_private_failure ON employee_referral_k1_receipts")
                    )
                    await session.execute(text("DROP FUNCTION k1_private_failure()"))

            # Unexpected driver failure must also remain behind the redacted boundary.
            def fail_driver(conn, cursor, statement, parameters, context, executemany):
                if statement.startswith("INSERT INTO employee_referral_k1_receipts"):
                    raise RuntimeError(marker)

            event.listen(k1.engine.sync_engine, "before_cursor_execute", fail_driver)
            try:
                response = await k1.post(next_event(k1.data, revision=2))
                assert response.status_code == 503
                assert marker not in response.text
            finally:
                event.remove(k1.engine.sync_engine, "before_cursor_execute", fail_driver)
            assert (await k1.client.get("/privacy-positive-control")).status_code == 200
        sentry_sdk.flush()
        private_log_marker = marker in caplog.text
        assert private_log_marker is False
        serialized = json.dumps(capture.events)
        private_events = sum(marker in json.dumps(e) for e in capture.events)
        assert private_events == 0
        from urllib.parse import urlsplit

        assert all(
            urlsplit(e.get("request", {}).get("url", "")).path != PATH for e in capture.events
        )
        assert k1.data["participants"][0]["token"] not in serialized
        assert "ORDINARY_POSITIVE_CONTROL" in serialized
        assert "ORDINARY_POSITIVE_CONTROL" in caplog.text
        assert any(e.get("type") == "transaction" for e in capture.events)
    finally:
        event.remove(k1.engine.sync_engine, "before_cursor_execute", sdk_probe)
        sentry_sdk.get_client().close()


@pytest.mark.parametrize("zone", ["UTC", "Europe/Berlin", "Asia/Tashkent"])
async def test_process_timezone_does_not_change_persisted_timestamps(k1, monkeypatch, zone):
    import os
    import time
    from datetime import datetime

    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        response = await k1.post()
        assert response.status_code == 200
        replica, _, _ = await k1.rows()
        assert replica.issued_at == datetime.fromisoformat(k1.data["issued_at"])
        assert response.json()["registry"]["issued_at"] == k1.data["issued_at"]
    finally:
        if original is None:
            monkeypatch.delenv("TZ")
        else:
            monkeypatch.setenv("TZ", original)
        time.tzset()


async def test_replay_ignores_body_future_check_after_clock_change(k1, monkeypatch):

    assert (await k1.post()).status_code == 200

    # Change the database observation, not the HTTP authentication clock.
    def changed_clock(conn, cursor, statement, parameters, context, executemany):
        if "clock_timestamp()" in statement:
            return statement.replace(
                "clock_timestamp()", "TIMESTAMPTZ '2000-01-01 00:00:00+00'"
            ), parameters
        return statement, parameters

    event.listen(k1.engine.sync_engine, "before_cursor_execute", changed_clock, retval=True)
    try:
        response = await k1.post()
        assert response.status_code == 200
        assert response.json()["replayed"] is True
        assert response.json()["registry"]["observed_at"].startswith("2000-01-01")
        assert (await k1.post(next_event(k1.data))).status_code == 422
    finally:
        event.remove(k1.engine.sync_engine, "before_cursor_execute", changed_clock)


async def test_clock_observation_is_after_waiting_for_the_campaign_lock(k1):
    assert (await k1.post()).status_code == 200
    waiting = asyncio.Event()

    def blocked_query(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO employee_referral_campaigns"):
            waiting.set()

    event.listen(k1.engine.sync_engine, "before_cursor_execute", blocked_query)
    try:
        async with k1.sessions.begin() as blocker:
            await blocker.execute(select(CampaignReplica).with_for_update())
            task = asyncio.create_task(k1.post(next_event(k1.data)))
            await asyncio.wait_for(waiting.wait(), 5)
            # Barrier time recorded on a different connection while the request is blocked.
            async with k1.sessions() as reader:
                released_after = await reader.scalar(select(func.clock_timestamp()))
            assert not task.done()
        response = await asyncio.wait_for(task, 5)
        assert response.status_code == 200
        from datetime import datetime

        assert datetime.fromisoformat(response.json()["registry"]["observed_at"]) >= released_after
    finally:
        event.remove(k1.engine.sync_engine, "before_cursor_execute", blocked_query)


async def test_concurrent_renewals_keep_the_latest_issuance(k1):
    from datetime import datetime

    current = datetime.fromisoformat(k1.data["issued_at"])
    older = next_event(k1.data, issued_at=utc_text(current - timedelta(seconds=10)))
    newest = next_event(k1.data)
    responses = await asyncio.gather(k1.post(older), k1.post(newest))
    assert [r.status_code for r in responses] == [200, 200]
    replica, _, receipts = await k1.rows()
    assert replica.issued_at == current
    assert len(receipts) == 2


async def test_actual_session_engine_echo_blocks_only_k1(k1):
    k1.engine.echo = True
    try:
        assert (await k1.post()).status_code == 503
    finally:
        k1.engine.echo = False
    assert (await k1.rows()) == (None, [], [])


async def test_invalid_auth_never_reaches_sql_with_positive_control(k1):
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(k1.engine.sync_engine, "before_cursor_execute", record_sql)
    try:
        raw = encoded(k1.data)
        response = await k1.client.post(PATH, content=raw, headers=signed(raw, key=b"wrong-key"))
        assert response.status_code == 401
        assert statements == []
        assert (await k1.post()).status_code == 200
        assert any(
            "INSERT INTO employee_referral_k1_receipts" in statement for statement in statements
        )
    finally:
        event.remove(k1.engine.sync_engine, "before_cursor_execute", record_sql)
