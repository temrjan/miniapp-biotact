"""Size ownership retains current-main campaign facts and durable notices."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.employee_referrals import paid
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.notices import render, verify_notice
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.returns import validated
from src.loyalty.service import get_balance
from src.notifications.service import telegram_notifier
from src.orders.models import Order
from src.payments.schemas import MultiCardPaymentResponse
from src.payments.service import multicard_client
from src.products.models import ProductSize
from tests import test_employee_referral_order_capture as capture_tests
from tests.test_employee_referral_order_capture import Checkout
from tests.test_payments_lifecycle import SECRET, _callback_body

checkout = capture_tests.checkout


async def seed_sizes(h: Checkout) -> None:
    async with h.sessions.begin() as session:
        session.add_all(
            ProductSize(product_id=39, size=size, available_quantity=50)
            for size in ("L", "XL", "2XL")
        )


async def create_sized(h: Checkout, points: int = 0) -> dict:
    body = h.body(
        items=[
            {"product_id": 39, "quantity": 2, "size": "L"},
            {"product_id": 39, "quantity": 1, "size": "XL"},
            {"product_id": 40, "quantity": 1},
        ],
        points_to_apply=points,
    )
    quote = await h.client.post("/api/v1/orders/quote", json=body, headers=h.headers)
    assert quote.status_code == 200, quote.text
    response = await h.create({**body, "quote_fingerprint": quote.json()["quote_fingerprint"]})
    assert response.status_code == 201, response.text
    return response.json()


async def arm(h: Checkout, order: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(settings, "multicard_store_id", "6")
    monkeypatch.setattr(multicard_client, "secret", SECRET)
    monkeypatch.setattr(
        multicard_client,
        "create_invoice",
        AsyncMock(
            return_value=MultiCardPaymentResponse(
                uuid="sized-campaign-invoice", pay_url="https://test.invalid/pay"
            )
        ),
    )
    monkeypatch.setattr(telegram_notifier, "notify_order_created", AsyncMock())
    response = await h.client.post(
        "/api/v1/payments/create",
        json={"order_id": order["id"], "return_url": "https://test.invalid/return"},
        headers=h.headers,
    )
    assert response.status_code == 200, response.text
    return _callback_body(
        uuid="sized-campaign-invoice",
        invoice_id=order["order_number"],
        amount=int((Decimal(str(order["total"])) - order["points_applied"]) * 100),
    )


@pytest.mark.parametrize("points", [0, 1000, 50000])
async def test_size_payment_preserves_one_campaign_fact_and_sized_notice(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch, points: int
) -> None:
    h = checkout
    await seed_sizes(h)
    order = await create_sized(h, points)
    assert Decimal(str(order["total"])) == Decimal("50000")
    if points < 50000:
        callback = await arm(h, order, monkeypatch)
        for _ in range(2):
            response = await h.client.post("/api/v1/payments/callback", json=callback)
            assert response.status_code == 200
            assert response.json()["success"] is True
    async with h.sessions() as reader:
        facts = list(await reader.scalars(select(PaidOutbox)))
        notices = list(await reader.scalars(select(NoticeDelivery)))
        assert len(facts) == len(notices) == 1
        assert len(list(await reader.scalars(select(K2Delivery)))) == 1
        count = validated(facts[0])
        assert sum(line.quantity for line in count.lines) == 3
        assert all(line.product_id == 39 for line in count.lines)
        value = verify_notice(notices[0], count)
        assert [line.size for line in value.lines] == ["L", "XL", None]
        text = render(value)
        assert "(L)" in text
        assert "(XL)" in text
        assert "Ordinary product" in text
        assert await get_balance(reader, 1) == 100000 - points
        assert (await reader.get(Order, order["id"])).accepted_settlement is True
        sizes = list(await reader.scalars(select(ProductSize).order_by(ProductSize.id)))
        assert [size.available_quantity for size in sizes] == [48, 49, 50]
        # Adding a nullable display-only size does not change the remote count protocol.
        assert all("size" not in line for line in json.loads(facts[0].body)["lines"])
    assert telegram_notifier.notify_payment_success.await_count == 0


async def test_campaign_job_failure_rolls_back_sized_settlement_then_retry_succeeds(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = checkout
    await seed_sizes(h)
    order = await create_sized(h, 1000)
    callback = await arm(h, order, monkeypatch)
    original = paid.append_jobs

    async def fail(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("Synthetic campaign job insert failure")

    monkeypatch.setattr(paid, "append_jobs", fail)
    response = await h.client.post("/api/v1/payments/callback", json=callback)
    assert response.status_code == 503
    async with h.sessions() as reader:
        stored = await reader.get(Order, order["id"])
        assert stored.accepted_settlement is False
        assert stored.size_owner_state == "live"
        assert stored.payment_status == "pending"
        assert await get_balance(reader, 1) == 100000
        assert list(await reader.scalars(select(PaidOutbox))) == []
        assert list(await reader.scalars(select(NoticeDelivery))) == []
    monkeypatch.setattr(paid, "append_jobs", original)
    response = await h.client.post("/api/v1/payments/callback", json=callback)
    assert response.status_code == 200
    assert response.json()["success"] is True
    async with h.sessions() as reader:
        assert len(list(await reader.scalars(select(PaidOutbox)))) == 1
        assert await get_balance(reader, 1) == 99000
