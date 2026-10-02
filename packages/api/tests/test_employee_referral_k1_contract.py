"""The mounted K1 boundary and frozen wire contract."""

import copy
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from src.config import settings
from src.employee_referrals.schemas import K1Error, parse_snapshot
from src.main import create_app


@pytest.mark.asyncio
async def test_disabled_receiver_is_explicit_and_does_not_need_a_key():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/internal/employee-referrals/k1", content=b"invalid")
    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": "k1_disabled", "message": "K1 receiver is disabled."}
    }
    assert response.headers["cache-control"] == "no-store"


PATH = "/api/v1/internal/employee-referrals/k1"
KEY = bytes(32)  # Public frozen synthetic vector, never a deployed key.
WIRE = (
    '{"schema_version":1,"event_id":"11111111-1111-4111-8111-111111111111",'
    '"campaign":{"id":1,"content_revision":1,"name":"Синтетическая кампания",'
    '"product_id":39,"state":"published","published_at":"2026-10-01T12:00:00Z",'
    '"start_at":"2026-10-01T12:00:00Z","end_at":"2026-10-31T12:00:00Z","ended_at":null},'
    '"participants":[{"employee_code":"22222222-2222-4222-8222-222222222222",'
    '"token":"AAAAAAAAAAAAAAAAAAAAAA","employee_display_name":"Тестовый сотрудник"}],'
    '"issued_at":"2026-10-01T12:00:00Z"}'
).encode()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def signed(body, *, stamp=None, path=PATH, key=KEY, method="POST", direction="portal-to-shop"):
    stamp = str(int(time.time())) if stamp is None else stamp
    prefix = f"biotact-k1-v1\n{direction}\n{method}\n{path}\n{stamp}\n".encode()
    return {
        "content-type": "application/json",
        "x-biotact-k1-timestamp": stamp,
        "x-biotact-k1-signature": hmac.new(key, prefix + body, hashlib.sha256).hexdigest(),
    }


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", True)
    monkeypatch.setattr(settings, "employee_referral_k1_key", SecretStr(KEY.hex()))


def test_frozen_vector_and_canonical_content():
    assert len(WIRE) == 524
    assert (
        hashlib.sha256(WIRE).hexdigest()
        == "b63ef17e24e4fc592a53cbe0bc84f4a33b34ed17ac64a28a0321f068814ef7de"
    )
    assert (
        signed(WIRE, stamp="1790856000")["x-biotact-k1-signature"]
        == "0901e5b830e982bf1084e6eb104c8a580e33e0642f531c9607fa0e362c2bd38c"
    )
    parsed = parse_snapshot(WIRE)
    assert (
        parsed.content_sha256()
        == "791fb752d003cfc6507190c4efd1038779e5c4e9ff42bb3f6acbfe4a6bdf9121"
    )
    value = json.loads(WIRE)
    value["event_id"] = str(uuid4())
    value["issued_at"] = "2026-10-01T12:00:01Z"
    value["campaign"]["start_at"] = "2026-10-01T12:00:00.000000Z"
    assert parse_snapshot(encoded(value)).content_sha256() == parsed.content_sha256()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), True),
        (("schema_version",), 2),
        (("schema_version",), "1"),
        (("campaign", "id"), True),
        (("campaign", "id"), 0),
        (("campaign", "id"), 2**63),
        (("campaign", "id"), 1.0),
        (("campaign", "id"), "1"),
        (("campaign", "content_revision"), 0),
        (("campaign", "product_id"), -1),
        (("campaign", "name"), " "),
        (("campaign", "name"), "x" * 201),
        (("campaign", "name"), "a\x00b"),
        (("campaign", "name"), "a\ud800"),
        (("campaign", "state"), "draft"),
        (("campaign", "ended_at"), "2026-10-01T12:00:00Z"),
        (("campaign", "end_at"), "2026-10-01T12:00:00Z"),
        (("campaign", "published_at"), "2026-10-01T12:00:01Z"),
        (("issued_at",), "2026-10-01T12:00:00"),
        (("issued_at",), "2026-10-01T17:00:00+05:00"),
        (("issued_at",), "2026-02-30T12:00:00Z"),
        (("issued_at",), "2026-10-01T12:00:60Z"),
        (("issued_at",), "2026-10-01T12:00:00.1234567Z"),
        (("issued_at",), 1790856000),
        (("event_id",), "11111111-1111-1111-8111-111111111111"),
        (("event_id",), "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        (("participants", 0, "token"), "AAAAAAAAAAAAAAAAAAAAAB"),
        (("participants", 0, "token"), "AAAAAAAAAAAAAAAAAAAAAA=="),
        (("participants", 0, "token"), "!" * 22),
        (("participants", 0, "employee_code"), "bad"),
        (("participants", 0, "employee_display_name"), "x" * 162),
        (("participants", 0, "employee_display_name"), "\t"),
        (("participants", 0, "employee_display_name"), "a\x00"),
        (("campaign", "extra"), 1),
        (("extra",), 1),
        (("participants", 0, "extra"), 1),
    ],
)
def test_schema_rejects_invalid_with_positive_counterpart(path, value):
    data = json.loads(WIRE)
    assert parse_snapshot(WIRE).schema_version == 1
    node = data
    for segment in path[:-1]:
        node = node[segment]
    node[path[-1]] = value
    # ascii escaping also exercises escaped NUL/surrogates at the JSON boundary.
    with pytest.raises(K1Error, match="k1_invalid_request"):
        parse_snapshot(json.dumps(data).encode())


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"[]",
        b"null",
        b"\xff",
        b"\xef\xbb\xbf" + WIRE,
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1,"x":2}',
        b"{",
    ],
)
def test_invalid_json(raw):
    with pytest.raises(K1Error, match="k1_invalid_request"):
        parse_snapshot(raw)


def test_roster_unique_empty_limits_and_ended_dates():
    data = json.loads(WIRE)
    data["participants"].append(copy.deepcopy(data["participants"][0]))
    with pytest.raises(K1Error):
        parse_snapshot(encoded(data))
    data["participants"] = []
    data["campaign"]["id"] = 2**63 - 1
    data["campaign"]["name"] = "ю" * 200
    assert parse_snapshot(encoded(data)).participants == []
    data["campaign"]["state"] = "ended"
    with pytest.raises(K1Error):
        parse_snapshot(encoded(data))
    data["campaign"]["ended_at"] = "2026-10-01T12:00:00Z"
    assert parse_snapshot(encoded(data)).campaign.state == "ended"
    data["campaign"]["ended_at"] = "2026-10-01T12:00:01Z"
    with pytest.raises(K1Error):
        parse_snapshot(encoded(data))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "key",
        "path",
        "method",
        "direction",
        "body",
        "old",
        "future",
        "missing",
        "duplicate",
        "leading-zero",
        "uppercase",
        "big-stamp",
    ],
)
async def test_auth_rejects_before_database(enabled, mutation):
    body = WIRE
    options = {
        "key": {"key": b"x" * 32},
        "path": {"path": "/wrong"},
        "method": {"method": "GET"},
        "direction": {"direction": "shop-to-portal"},
        "old": {"stamp": str(int(time.time()) - 31)},
        "future": {"stamp": str(int(time.time()) + 40)},
    }
    headers = signed(body, **options.get(mutation, {}))
    if mutation == "body":
        body += b" "
    if mutation == "missing":
        headers.pop("x-biotact-k1-signature")
    if mutation == "leading-zero":
        headers["x-biotact-k1-timestamp"] = "01"
    if mutation == "uppercase":
        headers["x-biotact-k1-signature"] = "A" * 64
    if mutation == "big-stamp":
        headers["x-biotact-k1-timestamp"] = "1" * 13
    headers = list(headers.items())
    if mutation == "duplicate":
        headers.append(("x-biotact-k1-signature", "0" * 64))
    app = create_app()
    # No usable factory: reaching storage would turn an auth refusal into 503.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(PATH, content=body, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Biotact-K1-HMAC"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({"content-type": "text/plain"}, WIRE, 415),
        ({"content-encoding": "gzip"}, WIRE, 415),
        ({}, b"x" * 1_048_577, 413),
        ({}, b" " * (1_048_576 - 2) + b"{}", 422),
        ({}, b"not json", 422),
    ],
    ids=["media", "encoding", "over-limit", "at-limit", "invalid-json"],
)
async def test_bounded_media_and_schema(enabled, headers, body, expected):
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(PATH, content=body, headers=signed(body) | headers)
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["key", "echo"])
async def test_misconfiguration_is_local(enabled, monkeypatch, bad):
    if bad == "key":
        monkeypatch.setattr(settings, "employee_referral_k1_key", SecretStr("bad"))
    else:
        monkeypatch.setattr(settings, "database_echo", True)
    app = create_app()

    @app.get("/ordinary-control")
    async def control():
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(PATH, content=WIRE, headers=signed(WIRE))).status_code == 503
        assert (await client.get("/ordinary-control")).json() == {"ok": True}


@pytest.mark.asyncio
async def test_sdk_cannot_read_disabled_or_unauthenticated_or_oversized_body(monkeypatch):
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration

    from src.employee_referrals.privacy import filter_k1
    from tests.test_employee_referral_k1_receiver import Captured

    transport = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=transport,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_k1,
        before_send_transaction=filter_k1,
    )
    app = create_app()
    counts = []

    async def measured(scope, receive, send):
        count = 0

        async def counted():
            nonlocal count
            count += 1
            return await receive()

        await app(scope, counted, send)
        counts.append(count)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=measured), base_url="http://test"
        ) as client:
            monkeypatch.setattr(settings, "employee_referral_k1_enabled", False)
            assert (await client.post(PATH, content=b"private-body")).status_code == 404
            assert counts[-1] == 0
            monkeypatch.setattr(settings, "employee_referral_k1_enabled", True)
            monkeypatch.setattr(settings, "employee_referral_k1_key", SecretStr(KEY.hex()))
            assert (await client.post(PATH, content=b"private-body")).status_code == 401
            assert counts[-1] == 0
            oversized = b"x" * 1_048_577

            async def chunks():
                yield oversized[:1_048_576]
                yield oversized[1:2]
                raise AssertionError("Must stop receiving after size limit")

            response = await client.post(PATH, content=chunks(), headers=signed(oversized))
            assert response.status_code == 413
            assert counts[-1] == 2
    finally:
        sentry_sdk.get_client().close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("delta", "expected"), [(-31, 401), (-30, 422), (30, 422), (31, 401)])
async def test_header_window_exact_edges(enabled, monkeypatch, delta, expected):
    from src.employee_referrals import auth

    monkeypatch.setattr(auth.time, "time", lambda: 1790856000.99)
    body = b"{}"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            PATH, content=body, headers=signed(body, stamp=str(1790856000 + delta))
        )
    assert response.status_code == expected


def test_participant_order_fraction_and_new_identity_dont_corrupt_digest():
    data = json.loads(WIRE)
    data["participants"].append(
        {
            "employee_code": "33333333-3333-4333-8333-333333333333",
            "token": "AQEBAQEBAQEBAQEBAQEBAQ",
            "employee_display_name": "x" * 161,
        }
    )
    digest = parse_snapshot(encoded(data)).content_sha256()
    data["participants"].reverse()
    assert parse_snapshot(encoded(data)).content_sha256() == digest
    data["campaign"]["published_at"] = "2026-10-01T12:00:00.0Z"
    assert parse_snapshot(encoded(data)).content_sha256() == digest
    data["participants"][0]["employee_display_name"] = "changed"
    assert parse_snapshot(encoded(data)).content_sha256() != digest


def test_sentry_filter_only_removes_the_named_route_or_explicit_tag(monkeypatch):
    from src.employee_referrals.privacy import filter_k1

    ordinary = {"request": {"url": "https://example.invalid/ordinary"}, "message": "control"}
    assert filter_k1(ordinary, {}) is ordinary
    assert filter_k1({"request": {"url": "https://example.invalid" + PATH}}, {}) is None
    assert filter_k1({"tags": {"biotact_k1": "yes"}}, {}) is None
    assert filter_k1({}, {}) == {}
    monkeypatch.setattr(settings, "api_v1_prefix", "/custom")
    assert (
        filter_k1(
            {"request": {"url": "https://example.invalid/custom/internal/employee-referrals/k1"}},
            {},
        )
        is None
    )


@pytest.mark.asyncio
async def test_signature_binds_actual_raw_path_without_normalization(enabled, monkeypatch):
    body = b"{}"
    alternate = PATH.replace("/k1", "/%6b1")
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        assert (await client.post(alternate, content=body, headers=signed(body))).status_code == 401
        assert (
            await client.post(alternate, content=body, headers=signed(body, path=alternate))
        ).status_code == 422
    monkeypatch.setattr(settings, "api_v1_prefix", "/custom")
    custom = "/custom/internal/employee-referrals/k1"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        assert (
            await client.post(custom, content=body, headers=signed(body, path=custom))
        ).status_code == 422
        assert (await client.post(custom, content=body, headers=signed(body))).status_code == 401


@pytest.mark.asyncio
async def test_content_length_does_not_bypass_stream_limit(enabled):
    body = b"x" * 1_048_577
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            PATH, content=body, headers=signed(body) | {"content-length": "1"}
        )
    assert response.status_code == 413
