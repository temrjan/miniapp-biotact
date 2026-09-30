"""Portal transport contracts without substituting proxy policy."""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from src.campaigns.client import OPERATION_TIMEOUT, PortalClient, portal_http_client
from src.campaigns.schemas import (
    CampaignError,
    DraftInput,
    EditInput,
    TransitionInput,
    WriteOutcome,
)
from src.config import settings
from tests.test_campaign_proxy import PRIVATE, SERVICE_TOKEN, VERSION, page, record


@asynccontextmanager
async def portal(handler, *, origin="https://portal.test", secret=SERVICE_TOKEN):
    config = settings.model_copy(
        update={"campaign_portal_origin": origin, "campaign_service_token": SecretStr(secret)}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as http:
        yield PortalClient(http, config)


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://portal.test",
        "https://portal.test/",
        " https://portal.test",
        "https://user@portal.test",
        "https://portal.test?q=1",
        "https://portal.test#frag",
        "https://portal.test:bad",
        "https://portal.test:0",
        "https://portal.test:",
        "https://portal.test?",
        "https://portal.test\\path",
    ],
)
async def test_invalid_origin_is_a_definite_no_send_failure(origin: str) -> None:
    calls = []
    async with portal(lambda r: calls.append(r), origin=origin) as client:
        with pytest.raises(CampaignError, match=r"^campaign_service_unavailable$"):
            await client.create(DraftInput(name="Draft"), WriteOutcome())
    assert calls == []


@pytest.mark.parametrize("secret", ["", " ", "\t"])
async def test_blank_token_never_enters_transport(secret: str) -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=record())

    async with portal(respond, secret=secret) as client:
        with pytest.raises(CampaignError, match=r"^campaign_service_unavailable$"):
            await client.detail(7)
    assert calls == []


async def test_service_token_and_patch_null_precision_are_forwarded_exactly() -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=record())

    async with portal(respond, secret=" service-v3-test-only ") as client:
        response = await client.edit(
            7,
            EditInput.model_validate({"expected_updated_at": VERSION, "image_url": None}),
            WriteOutcome(),
        )
    assert response.model_dump() == record()
    assert len(calls) == 1
    assert calls[0].url == "https://portal.test/api/v1/internal/campaigns/7"
    assert calls[0].headers["authorization"] == "Bearer  service-v3-test-only "
    assert "cookie" not in calls[0].headers
    assert json.loads(calls[0].content) == {"expected_updated_at": VERSION, "image_url": None}


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"id": "7"},
        {"id": 8},
        {"product_id": False},
        {"state": "active"},
        {"name": 7},
        {"updated_at": "2026-09-30T07:00:00"},
        {"created_at": 1_000_000_000},
        {"private": PRIVATE},
    ],
)
@pytest.mark.parametrize("write", [False, True])
async def test_invalid_success_is_unconfirmed(changes: dict[str, object], write: bool) -> None:
    async with portal(lambda r: httpx.Response(200, json=record(**changes))) as client:
        operation = (
            client.edit(7, EditInput(expected_updated_at=VERSION, name="Draft"), WriteOutcome())
            if write
            else client.detail(7)
        )
        with pytest.raises(CampaignError) as caught:
            await operation
    assert caught.value.code == (
        "campaign_result_unknown" if write else "campaign_service_unavailable"
    )


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize(
    "response_kind",
    [
        "missing",
        "html",
        "wrong_status",
        "redirect",
        "bad_json",
        "wrong_pair",
        "unknown",
        "extra_error",
    ],
)
async def test_unrecognized_reply_never_becomes_success(response_kind: str, write: bool) -> None:
    payload = record()
    payload.pop("updated_at")
    responses = {
        "missing": httpx.Response(200, json=payload),
        "html": httpx.Response(200, text="<html>" + PRIVATE),
        "wrong_status": httpx.Response(202, json=record()),
        "redirect": httpx.Response(307, headers={"Location": "https://other.test/" + PRIVATE}),
        "bad_json": httpx.Response(500, content=b"{"),
        "wrong_pair": httpx.Response(409, json={"detail": {"code": "product_not_found"}}),
        "unknown": httpx.Response(503, json={"detail": {"code": PRIVATE}}),
        "extra_error": httpx.Response(
            409, json={"detail": {"code": "campaign_changed", "private": PRIVATE}}
        ),
    }
    calls = []

    def respond(request):
        calls.append(request)
        return responses[response_kind]

    async with portal(respond) as client:
        operation = (
            client.end(7, TransitionInput(expected_updated_at=VERSION), WriteOutcome())
            if write
            else client.detail(7)
        )
        with pytest.raises(CampaignError) as caught:
            await operation
    assert caught.value.code == (
        "campaign_result_unknown" if write else "campaign_service_unavailable"
    )
    assert len(calls) == 1
    assert calls[0].url.host == "portal.test"


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize(
    "error_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
async def test_transport_failures_are_not_retried(
    write: bool, error_type: type[httpx.HTTPError]
) -> None:
    calls = []

    def fail(request):
        calls.append(request)
        raise error_type(PRIVATE)

    async with portal(fail) as client:
        operation = (
            client.create(DraftInput(name="Draft"), WriteOutcome())
            if write
            else client.listing(None, 1, 20)
        )
        with pytest.raises(CampaignError) as caught:
            await operation
    assert caught.value.code == (
        "campaign_result_unknown" if write else "campaign_service_unavailable"
    )
    assert len(calls) == 1


async def test_real_whole_operation_deadline_cancels_stalled_stream_once() -> None:
    calls = []

    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"{}"

    def respond(request):
        calls.append(request)
        return httpx.Response(201, stream=Stalled())

    start = time.monotonic()
    try:
        async with portal(respond) as client, asyncio.timeout(OPERATION_TIMEOUT + 5):
            with pytest.raises(CampaignError, match=r"^campaign_result_unknown$"):
                await client.create(DraftInput(name="Draft"), WriteOutcome())
    except TimeoutError:
        pytest.fail("The client did not enforce its whole-operation deadline")
    assert OPERATION_TIMEOUT <= time.monotonic() - start < OPERATION_TIMEOUT + 5
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"total": True},
        {"pages": -1},
        {"page": "1"},
        {"size": 101},
        {"items": {}},
        {"private": PRIVATE},
    ],
)
async def test_page_counts_and_shape_are_strict(changes: dict[str, object]) -> None:
    async with portal(lambda r: httpx.Response(200, json=page() | changes)) as client:
        with pytest.raises(CampaignError, match=r"^campaign_service_unavailable$"):
            await client.listing(None, 1, 20)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product_id", True),
        ("product_id", "7"),
        ("name", 7),
        ("state", "draft"),
        ("start_at", 1_000_000_000),
        ("start_at", "2026-09-30"),
        ("end_at", "2026-09-30T07:00:00"),
        ("end_at", "2026-09-30T07:00:00+05:99"),
        ("image_url", "x" * 501),
    ],
)
def test_request_structure_rejects_coercion_and_extra_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DraftInput.model_validate({"name": "Draft", field: value})


async def test_campaign_http_logs_are_context_local_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    def respond(request):
        for name in (
            "httpx",
            "httpcore.http11",
            "httpcore.connection",
            "httpcore.http2",
            "httpcore.proxy",
            "httpcore.socks",
        ):
            logging.getLogger(name).debug("private %s", PRIVATE)
        return httpx.Response(200, json=record(), headers={"set-cookie": PRIVATE})

    logging.getLogger("httpx").info("capture_control")
    async with portal(respond) as client:
        await client.detail(7)
    assert "capture_control" in caplog.text
    assert PRIVATE not in caplog.text


async def test_dedicated_client_has_finite_timeouts_and_no_env_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    client = portal_http_client()
    assert client.trust_env is False
    assert client.follow_redirects is False
    assert all(value == OPERATION_TIMEOUT for value in client.timeout.as_dict().values())
    await client.aclose()
    assert client.is_closed
