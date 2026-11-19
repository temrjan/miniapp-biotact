"""Payment status reads: no anonymous route, and the invoice UUID is checked before Multicard.

``GET /api/v1/payments/status/{uuid}`` used to forward any path value to Multicard with the
merchant token and return the provider's ``data`` object without asking who the caller was.
The route is gone (R-01); the owner reads ``payment_status`` from ``GET /orders/{id}`` (R-05).
``MultiCardClient.get_invoice_status`` still serves the internal callers, so it now refuses a
value that is not a canonical UUID before it asks for a token or builds a URL (R-02), and keeps
its result contract for an accepted value (R-03).
"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.orders.models import Order
from src.payments.service import MultiCardClient, multicard_client
from src.users.models import User
from tests.conftest import TEST_DATABASE_URL, _make_init_data

BOT_TOKEN = "payment-status-bot"
OWNER_TELEGRAM_ID = 52001
STRANGER_TELEGRAM_ID = 52002
INVOICE_UUID = "527b7ead-b587-11f0-af7c-005056b4367d"
STATUS_URL = f"/api/v1/payments/status/{INVOICE_UUID}"
# What the provider's invoice object carries: the old route returned it to anyone.
PROVIDER_DATA = {"status": "success", "payment": {"card_pan": "860000******0000"}}


def _headers(telegram_id: int) -> dict[str, str]:
    return {"X-Init-Data": _make_init_data(BOT_TOKEN, {"id": telegram_id, "first_name": "Buyer"})}


@pytest.fixture(autouse=True)
def _pin_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def _seed_pending_order(sm: async_sessionmaker[AsyncSession]) -> int:
    """Commit the owner and one order that waits for its Multicard invoice; return the order id."""
    async with sm() as db, db.begin():
        user = User(telegram_id=OWNER_TELEGRAM_ID, first_name="Owner")
        db.add(user)
        await db.flush()
        order = Order(
            order_number="ORD-S5A-1",
            user_id=user.id,
            subtotal=Decimal("69000"),
            delivery_fee=Decimal("0"),
            total=Decimal("69000"),
            payment_uuid=INVOICE_UUID,
            payment_status="pending",
        )
        db.add(order)
        await db.flush()
        return order.id


@pytest.mark.parametrize(
    "telegram_id",
    [None, STRANGER_TELEGRAM_ID, OWNER_TELEGRAM_ID],
    ids=["anonymous", "stranger", "owner"],
)
async def test_r01_payment_status_route_is_not_served(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    telegram_id: int | None,
) -> None:
    """R-01: the path gets the unmatched-route 404 for every caller and reaches no provider call.

    The stub answers with card data, so the old route would have returned 200 with that body.
    """
    await _seed_pending_order(maker)
    get_token = AsyncMock(return_value="synthetic")
    get_status = AsyncMock(return_value=PROVIDER_DATA)
    monkeypatch.setattr(multicard_client, "_get_token", get_token)
    monkeypatch.setattr(multicard_client, "get_invoice_status", get_status)
    headers = {} if telegram_id is None else _headers(telegram_id)

    resp = await client.get(STATUS_URL, headers=headers)

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    get_status.assert_not_awaited()
    get_token.assert_not_awaited()


async def test_r05_owner_reads_payment_status_from_own_order(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """R-05: the owner's own order read carries the payment state the removed route used to give."""
    order_id = await _seed_pending_order(maker)

    resp = await client.get(f"/api/v1/orders/{order_id}", headers=_headers(OWNER_TELEGRAM_ID))

    assert resp.status_code == 200, resp.text
    assert resp.json()["payment_status"] == "pending"


def _client_with_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: httpx.Response | None = None,
    error: Exception | None = None,
) -> tuple[MultiCardClient, AsyncMock, AsyncMock, Mock]:
    """A real client whose token and HTTP transport are replaced at the port."""
    client = MultiCardClient()
    get_token = AsyncMock(return_value="synthetic")
    transport = AsyncMock()
    transport.__aenter__.return_value = transport
    if error is not None:
        transport.get.side_effect = error
    else:
        transport.get.return_value = response
    factory = Mock(return_value=transport)
    monkeypatch.setattr(client, "_get_token", get_token)
    monkeypatch.setattr("src.payments.service.httpx.AsyncClient", factory)
    return client, get_token, transport, factory


def _response(client: MultiCardClient, status: int, content: bytes, uuid: str) -> httpx.Response:
    url = f"{client.base_url}/payment/invoice/{uuid}"
    return httpx.Response(status, content=content, request=httpx.Request("GET", url))


@pytest.mark.parametrize(
    "value",
    [
        "?store_id=6",
        "x#frag",
        "..",
        "",
        f"{INVOICE_UUID}\n",
        INVOICE_UUID.replace("-", ""),
        f"{{{INVOICE_UUID}}}",
        f"urn:uuid:{INVOICE_UUID}",
        INVOICE_UUID.replace("7", "\u0667", 1),  # ARABIC-INDIC DIGIT SEVEN
    ],
    ids=[
        "query",
        "fragment",
        "dot-dot",
        "empty",
        "trailing-newline",
        "no-hyphens",
        "braced",
        "urn",
        "non-ascii-digit",
    ],
)
async def test_r02_non_canonical_uuid_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """R-02: a value that is not a canonical UUID returns None before the token or any request."""
    client, get_token, transport, factory = _client_with_transport(monkeypatch)

    assert await client.get_invoice_status(value) is None

    get_token.assert_not_awaited()
    factory.assert_not_called()
    transport.get.assert_not_awaited()


@pytest.mark.parametrize("uuid", [INVOICE_UUID, INVOICE_UUID.upper()], ids=["lower", "upper"])
async def test_r02_canonical_uuid_reaches_the_url_unchanged(
    monkeypatch: pytest.MonkeyPatch, uuid: str
) -> None:
    """R-02 positive control: either case of a canonical UUID is requested exactly as given."""
    probe = MultiCardClient()
    body = b'{"success":true,"data":{"status":"success"}}'
    client, get_token, transport, _ = _client_with_transport(
        monkeypatch, response=_response(probe, 200, body, uuid)
    )

    assert await client.get_invoice_status(uuid) == {"status": "success"}

    get_token.assert_awaited_once_with()
    transport.get.assert_awaited_once_with(
        f"{client.base_url}/payment/invoice/{uuid}",
        headers={"Authorization": "Bearer synthetic", "Content-Type": "application/json"},
    )


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            200,
            b'{"success":true,"data":{"status":"success","amount":6900000}}',
            {"status": "success", "amount": 6900000},
        ),
        (200, b'{"success":false}', None),
        (500, b'{"success":true,"data":{"status":"success"}}', None),
        (200, b"not-json", None),
    ],
    ids=["success-returns-data", "success-false", "http-500", "not-json"],
)
async def test_r03_accepted_uuid_keeps_the_result_contract(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, expected: dict[str, Any] | None
) -> None:
    """R-03 (a), (b): ``data`` on success, otherwise None, never an exception."""
    probe = MultiCardClient()
    client, _, transport, _ = _client_with_transport(
        monkeypatch, response=_response(probe, status, body, INVOICE_UUID)
    )

    result = await client.get_invoice_status(INVOICE_UUID)

    assert result == expected
    if result is not None:
        assert "success" not in result
    transport.get.assert_awaited_once()


async def test_r03_transport_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-03 (b): a transport failure is reported as None, not raised."""
    client, _, transport, _ = _client_with_transport(
        monkeypatch, error=httpx.ConnectError("synthetic outage")
    )

    assert await client.get_invoice_status(INVOICE_UUID) is None
    transport.get.assert_awaited_once()


async def test_r03_token_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-03 (c): a failure to get the token reaches the caller and no request is made."""
    client, get_token, transport, _ = _client_with_transport(monkeypatch)
    failure = RuntimeError("synthetic token failure")
    get_token.side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        await client.get_invoice_status(INVOICE_UUID)

    assert caught.value is failure
    transport.get.assert_not_awaited()
