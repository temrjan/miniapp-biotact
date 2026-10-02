"""G2b mounted status GET with committed authorization and strict upstream data."""

import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from src.campaigns.client import _decode
from src.campaigns.schemas import CampaignSync
from src.users.models import User
from tests.test_campaign_proxy import Harness, VERSION, harness, headers  # noqa: F401

URL = "/api/v1/admin/campaigns/7/k1-sync"


def sync_data(**changes: object) -> dict[str, object]:
    return dict(
        campaign_id="7", campaign_state="published", sender_enabled=True, status="confirmed",
        desired_revision="9223372036854775807", confirmed_revision="9223372036854775807",
        participant_count=1, confirmed_at=VERSION, valid_until=VERSION, stop_confirmed=False,
        stop_deadline=None, pending_count=0, blocked_count=0, oldest_pending_at=None,
        last_error_code=None, observed_at=VERSION,
    ) | changes


@pytest.mark.parametrize("case", ["missing", "tma", "spoof", "employee", "expired", "deleted", "demoted", "inactive"])
async def test_sync_auth(harness: Harness, case: str) -> None:
    auth = headers()
    if case == "missing":
        auth = {}
    elif case == "tma":
        auth = {"Authorization": "tma synthetic"}
    elif case == "spoof":
        auth = {"Authorization": "Bearer synthetic"}
    elif case == "employee":
        auth = headers(kind="access")
    elif case == "expired":
        auth = headers(expired=True)
    elif case == "deleted":
        auth = headers(sub="999")
    else:
        async with harness.sessions.begin() as session:
            await session.execute(update(User).where(User.id == 1).values(**{
                "is_admin" if case == "demoted" else "is_active": False,
            }))
    response = await harness.browser.get(URL, headers=auth)
    assert response.status_code == (403 if case in {"demoted", "inactive", "deleted", "employee"} else 401)
    assert response.headers["cache-control"] == "no-store"
    assert harness.calls == []


@pytest.mark.parametrize("status", ["not_published", "disabled", "pending", "confirmed", "expired", "blocked"])
async def test_fixed_sync_read(harness: Harness, status: str) -> None:
    calls: list[httpx.Request] = []
    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=sync_data(status=status))
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as upstream:
        harness.app.state.campaign_http_client = upstream
        response = await harness.browser.get(URL, headers=headers())
    assert response.status_code == 200
    assert response.json() == sync_data(status=status)
    assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert str(calls[0].url) == "https://portal.test/api/v1/internal/campaigns/7/k1-sync"
    assert calls[0].headers["authorization"] == "Bearer service-v3-test-only"
    assert calls[0].content == b""


@pytest.mark.parametrize("id", ["0", "-1", "x"])
async def test_invalid_id(harness: Harness, id: str) -> None:
    response = await harness.browser.get(URL.replace("/7/", f"/{id}/"), headers=headers())
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert harness.calls == []


@pytest.mark.parametrize("status,body,expected", [
    (404, {"detail": {"code": "campaign_not_found"}}, 404),
    (503, {"detail": {"code": "service_unavailable"}}, 503),
    (401, {"detail": {"code": "invalid_service_auth"}}, 503),
    (200, {}, 503), (302, {}, 503),
    (200, sync_data(campaign_id="8"), 503),
    (200, sync_data(token="PRIVATE_SYNC_TOKEN"), 503),
    (200, sync_data(pending_count=True), 503),
    (200, sync_data(valid_until="2026-02-30T07:00:00.123456Z"), 503),
])
async def test_failed_read_is_safe(harness: Harness, status: int, body: object, expected: int) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body))) as upstream:
        harness.app.state.campaign_http_client = upstream
        response = await harness.browser.get(URL, headers=headers())
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE_SYNC_TOKEN" not in response.text
    assert "status" not in response.json()


async def test_network_and_auth_db_failures(harness: Harness) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("PRIVATE_SYNC_TOKEN")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as upstream:
        harness.app.state.campaign_http_client = upstream
        response = await harness.browser.get(URL, headers=headers())
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE_SYNC_TOKEN" not in response.text
    def broken_session():
        raise OperationalError("PRIVATE_SYNC_TOKEN", {}, Exception("PRIVATE_SYNC_TOKEN"))
    harness.app.state.campaign_sessions = broken_session
    response = await harness.browser.get(URL, headers=headers())
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE_SYNC_TOKEN" not in response.text


@pytest.mark.parametrize("changes", [
    {"campaign_id": "0"}, {"campaign_id": "01"}, {"campaign_id": "9223372036854775808"},
    {"campaign_id": "1" * 20}, {"confirmed_revision": 1}, {"sender_enabled": 1},
    {"participant_count": -1}, {"participant_count": 9007199254740992},
    {"pending_count": 2}, {"blocked_count": -1}, {"oldest_pending_at": "2026-01-01T00:00:00Z"},
    {"observed_at": "2026-01-01T00:00:00.123456+00:00"},
    {"last_error_code": "PRIVATE_SYNC_TOKEN"}, {"employee": {"token": "PRIVATE_SYNC_TOKEN"}},
])
def test_decoder_rejects(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CampaignSync.model_validate(sync_data(**changes))


def test_duplicate_json() -> None:
    raw = json.dumps(sync_data())[:-1] + ', "campaign_id": "7"}'
    with pytest.raises(ValueError, match="Duplicate"):
        _decode(httpx.Response(200, content=raw), CampaignSync, 200, 7, False)


@pytest.mark.parametrize("error", ["configuration", "clock", "transport", "protocol", "authentication", "conflict", "payload_limit", "receiver_disabled", "invalid_request", "attempt_limit", "database", "storage"])
def test_decoder_accepts_categories(error: str) -> None:
    assert CampaignSync.model_validate(sync_data(last_error_code=error)).last_error_code == error
