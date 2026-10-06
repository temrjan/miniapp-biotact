"""ACR T3: current PG admin → fixed bounded result proxy and private capture."""

import copy
import json
import logging

import httpx
import pytest
from sqlalchemy import update
from src.campaigns import client as portal_client
from src.campaigns.router import ERRORS
from src.employee_referrals.order_privacy import filter_referral_telemetry
from src.users.models import User
from tests.test_campaign_proxy import Harness, VERSION, harness, headers  # noqa: F401

URL = "/api/v1/admin/campaigns/7/results"
PRIVATE = "PRIVATE_RESULTS_SENTINEL"
CACHE = {"cache-control": "no-store"}


def report() -> dict:
    q = dict(paid_quantity="3", returned_quantity="1", net_quantity="2")
    return dict(
        schema_version=1,
        campaign_id="7",
        campaign_state="published",
        state="observed",
        coverage_scope="retained-source-v1",
        observed_at=VERSION,
        items=[
            dict(
                employee_code="11111111-1111-4111-8111-111111111111",
                employee_display_name="Synthetic Employee",
                state="observed",
                totals=q,
            )
        ],
        totals=q,
    )


@pytest.mark.parametrize(
    "case", ["missing", "invalid", "employee", "expired", "deleted", "demoted", "inactive"]
)
async def test_current_admin_auth(harness: Harness, case: str) -> None:
    auth = headers()
    if case == "missing":
        auth = {}
    elif case == "invalid":
        auth = {"Authorization": "Bearer " + PRIVATE}
    elif case == "employee":
        auth = headers(kind="access")
    elif case == "expired":
        auth = headers(expired=True)
    elif case == "deleted":
        auth = headers(sub="999")
    else:
        async with harness.sessions.begin() as session:
            await session.execute(
                update(User)
                .where(User.id == 1)
                .values(**{"is_admin" if case == "demoted" else "is_active": False})
            )
    response = await harness.browser.get(URL, headers=auth)
    assert response.status_code == (401 if case in {"missing", "invalid", "expired"} else 403)
    assert response.headers["cache-control"] == "no-store" and harness.calls == []
    assert PRIVATE not in response.text


async def test_exact_read_fixed_path_and_recheck_current_flags(harness: Harness) -> None:
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(
            200, json=report(), headers=CACHE | {"ETag": PRIVATE, "Last-Modified": PRIVATE}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as upstream:
        harness.app.state.campaign_http_client = upstream
        response = await harness.browser.get(URL, headers=headers())
        assert response.status_code == 200 and response.json() == report()
        assert (
            response.headers["cache-control"] == "no-store"
            and "etag" not in response.headers
            and "last-modified" not in response.headers
        )
        assert (
            len(calls) == 1
            and str(calls[0].url) == "https://portal.test/api/v1/internal/campaigns/7/results"
        )
        assert (
            calls[0].content == b""
            and calls[0].headers["authorization"] == "Bearer service-v3-test-only"
        )
        async with harness.sessions.begin() as session:
            await session.execute(update(User).where(User.id == 1).values(is_admin=False))
        assert (await harness.browser.get(URL, headers=headers())).status_code == 403
        assert len(calls) == 1


@pytest.mark.parametrize(
    "case",
    [
        "extra",
        "missing",
        "wrong-id",
        "duplicate-row",
        "order",
        "quantity",
        "arithmetic",
        "aggregate",
        "timestamp",
        "name",
        "state",
        "preparing",
        "failure-total",
        "duplicate-key",
        "duplicate-nested",
        "no-store",
        "media",
        "oversize",
        "network",
        "timeout",
    ],
)
async def test_bad_upstream_never_becomes_zero(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    value = copy.deepcopy(report())
    row = value["items"][0]
    cache = CACHE
    media = "application/json"
    if case == "extra":
        value["token"] = PRIVATE
    elif case == "missing":
        del value["totals"]
    elif case == "wrong-id":
        value["campaign_id"] = "8"
    elif case == "duplicate-row":
        value["items"].append(copy.deepcopy(row))
    elif case == "order":
        second = copy.deepcopy(row)
        second["employee_code"] = "00000000-0000-4000-8000-000000000001"
        value["items"].append(second)
    elif case == "quantity":
        row["totals"]["paid_quantity"] = 3
    elif case == "arithmetic":
        row["totals"]["net_quantity"] = "3"
    elif case == "aggregate":
        value["totals"] = dict(paid_quantity="4", returned_quantity="1", net_quantity="3")
    elif case == "timestamp":
        value["observed_at"] = "2026-02-30T00:00:00.000000Z"
    elif case == "name":
        row["employee_display_name"] = "\x00" + PRIVATE
    elif case == "state":
        value["state"] = "incomplete"
        value["totals"] = None
    elif case == "preparing":
        value["state"] = "preparing"
    elif case == "failure-total":
        row["state"] = value["state"] = "unavailable"
    elif case == "no-store":
        cache = {}
    elif case == "media":
        media = "text/html"
    elif case == "oversize":
        monkeypatch.setattr(portal_client, "MAX_BODY", 10)
    elif case == "timeout":
        monkeypatch.setattr(portal_client, "OPERATION_TIMEOUT", 0)
    raw = json.dumps(value).encode()
    if case == "duplicate-key":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    if case == "duplicate-nested":
        raw = raw.replace(b'"paid_quantity": "3"', b'"paid_quantity": "3", "paid_quantity": "3"')

    def reply(request):
        if case == "network":
            raise httpx.ConnectError(PRIVATE, request=request)
        return httpx.Response(200, content=raw, headers=cache | {"content-type": media})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as upstream:
        harness.app.state.campaign_http_client = upstream
        response = await harness.browser.get(URL, headers=headers())
    assert (
        response.status_code == 503
        and response.json()["detail"]["code"] == "campaign_service_unavailable"
    )
    assert PRIVATE not in response.text and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (401, "invalid_service_auth", 503),
        (404, "campaign_not_found", 404),
        (422, "invalid_request", 422),
        (503, "service_unavailable", 503),
        (409, "campaign_changed", 503),
        (200, "unknown", 503),
    ],
)
async def test_nested_refusals(harness: Harness, status: int, code: str, expected: int) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"detail": {"code": code}}, headers=CACHE)
        )
    ) as upstream:
        harness.app.state.campaign_http_client = upstream
        response = await harness.browser.get(URL, headers=headers())
    assert response.status_code == expected
    fixed = response.json()["detail"]["code"]
    assert response.json()["detail"]["message"] == ERRORS[fixed][1]


async def test_get_input_privacy_cors_and_safe_status(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for path, body in (
        (URL + "?sentinel=" + PRIVATE, b""),
        (URL, PRIVATE.encode()),
        (URL.replace("/7/", "/9223372036854775808/"), b""),
    ):
        response = await harness.browser.request("GET", path, content=body, headers=headers())
        assert (
            response.status_code == 422 and response.json()["detail"]["code"] == "invalid_request"
        )
        assert response.headers["cache-control"] == "no-store"
    assert harness.calls == [] and PRIVATE not in caplog.text
    # Actual logger factory receives Uvicorn's arguments and retains only status.
    logger = logging.getLogger("uvicorn.access")
    logger.info('%s - "%s %s HTTP/%s" %s', "host", "GET", URL + "?" + PRIVATE, "1.1", 422)
    # Access records are outside the request context, so path classification must protect them.
    assert PRIVATE not in caplog.text
    assert (
        filter_referral_telemetry(
            {"tags": {"biotact_campaign_results": "yes"}, "message": PRIVATE}, {}
        )
        is None
    )
    assert filter_referral_telemetry({"message": "POSITIVE_CONTROL"}, {}) == {
        "message": "POSITIVE_CONTROL"
    }
