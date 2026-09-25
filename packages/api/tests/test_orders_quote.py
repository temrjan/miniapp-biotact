"""Quote agreement tested across real request transactions and committed state."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from src.loyalty.models import PointLedger
from src.orders.models import Order, OrderStatus
from src.products.models import Product
from tests import test_orders_redeem as redeem_fixtures
from tests.test_orders_redeem import (
    _headers,
    _payload,
    _seed_partial_order,
    _seed_points,
    _seed_user,
)

# Reuse the committed-transaction fixtures without replacing domain behavior.
_pin_bot_token = redeem_fixtures._pin_bot_token
client = redeem_fixtures.client
maker = redeem_fixtures.maker
sent_messages = redeem_fixtures.sent_messages


async def seed(maker, balance=500_000):
    uid = await _seed_user(maker, 80902)
    await _seed_points(maker, user_id=uid, amount=balance)
    async with maker() as db, db.begin():
        product = Product(
            name="Sale",
            slug="sale",
            price=Decimal("200000"),
            is_sale=True,
            sale_price=Decimal("150000"),
        )
        db.add(product)
        await db.flush()
        return uid, product.id


async def snapshot(maker):
    async with maker() as db:
        return (
            await db.scalar(select(func.count()).select_from(Order)),
            await db.scalar(select(func.count()).select_from(PointLedger)),
            [
                (o.id, o.payment_status, o.points_applied, o.payment_uuid)
                for o in (await db.scalars(select(Order).order_by(Order.id))).all()
            ],
        )


async def test_quote_is_read_only_and_legacy_agreement(client, maker, sent_messages):
    uid, pid = await seed(maker)
    await _seed_partial_order(maker, user_id=uid, points_applied=1000, age=timedelta(hours=1))
    before = await snapshot(maker)
    payload = _payload(pid, points_to_apply=0)
    response = await client.post("/api/v1/orders/quote", headers=_headers(80902), json=payload)
    assert response.status_code == 200
    quote = response.json()
    assert quote["total"] == "150000.00"
    assert quote["delivery_fee"] == "0.00"
    assert quote["max_redeemable"] == 150000
    assert quote["lines"] == [
        {
            "product_id": pid,
            "product_name": "Sale",
            "quantity": 1,
            "unit_price": "150000.00",
            "line_total": "150000.00",
        }
    ]
    refused = await client.post("/api/v1/orders", headers=_headers(80902), json=payload)
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"code": "quote_required", "quote": quote}
    assert await snapshot(maker) == before
    assert sent_messages == []
    accepted = await client.post(
        "/api/v1/orders",
        headers=_headers(80902),
        json=payload | {"quote_fingerprint": quote["quote_fingerprint"]},
    )
    assert accepted.status_code == 201
    assert accepted.json()["total"] == quote["total"]
    assert (await snapshot(maker))[0] == before[0] + 1


@pytest.mark.parametrize("points", [0, 100000, 150000])
@pytest.mark.parametrize("new_price", ["120000", "180000"])
async def test_changed_price_refuses_before_settlement(
    client, maker, sent_messages, points, new_price
):
    _, pid = await seed(maker)
    payload = _payload(pid, points_to_apply=points)
    quote = await client.post("/api/v1/orders/quote", headers=_headers(80902), json=payload)
    assert quote.status_code == 200
    async with maker() as db, db.begin():
        product = await db.get(Product, pid)
        product.sale_price = Decimal(new_price)
    before = await snapshot(maker)
    response = await client.post(
        "/api/v1/orders",
        headers=_headers(80902),
        json=payload | {"quote_fingerprint": quote.json()["quote_fingerprint"]},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "quote_mismatch"
    assert detail["quote"]["total"] == f"{Decimal(new_price):.2f}"
    assert detail["quote"]["max_redeemable"] == int(new_price)
    assert await snapshot(maker) == before
    assert sent_messages == []


@pytest.mark.parametrize("change", ["quantity", "points", "ceiling", "same_ceiling", "sale_off"])
async def test_quote_tracks_terms_not_raw_balance(client, maker, change):
    uid, pid = await seed(maker)
    payload = _payload(pid, points_to_apply=0)
    response = await client.post("/api/v1/orders/quote", headers=_headers(80902), json=payload)
    assert response.status_code == 200
    fingerprint = response.json()["quote_fingerprint"]
    if change == "quantity":
        payload = _payload(pid, points_to_apply=0, quantity=2)
    elif change == "points":
        payload["points_to_apply"] = 1000
    else:
        async with maker() as db, db.begin():
            if change == "sale_off":
                (await db.get(Product, pid)).is_sale = False
            else:
                ledger = (
                    await db.scalars(select(PointLedger).where(PointLedger.user_id == uid))
                ).one()
                ledger.amount = 100000 if change == "ceiling" else 400000
    response = await client.post(
        "/api/v1/orders", headers=_headers(80902), json=payload | {"quote_fingerprint": fingerprint}
    )
    assert response.status_code == (201 if change == "same_ceiling" else 409)


async def test_quote_auth_and_duplicate_line_canonicalization(client, maker):
    _, pid = await seed(maker)
    payload = {"items": [{"product_id": pid, "quantity": 2}, {"product_id": pid, "quantity": 1}]}
    assert (await client.post("/api/v1/orders/quote", json=payload)).status_code == 401
    first = await client.post("/api/v1/orders/quote", headers=_headers(80902), json=payload)
    assert first.status_code == 200
    payload["items"].reverse()
    second = await client.post("/api/v1/orders/quote", headers=_headers(80902), json=payload)
    assert second.json()["quote_fingerprint"] == first.json()["quote_fingerprint"]
    assert len(second.json()["lines"]) == 2
    async with maker() as db, db.begin():
        (await db.get(Product, pid)).is_sale = False
    assert (
        await client.post("/api/v1/orders", headers=_headers(80902), json=payload)
    ).status_code == 201


@pytest.mark.parametrize("points", [100000, 150000])
async def test_quote_preserves_stale_reserve_and_survives_order_cleanup(
    client, maker, sent_messages, points
):
    uid, pid = await seed(maker, balance=150000)
    stale_id = await _seed_partial_order(
        maker, user_id=uid, points_applied=100000, age=timedelta(hours=1)
    )
    before = await snapshot(maker)
    payload = _payload(pid, points_to_apply=points)
    response = await client.post("/api/v1/orders/quote", headers=_headers(80902), json=payload)
    assert response.status_code == 200
    quote = response.json()
    # Reserve-aware funds are only 50,000: quote must still use the raw ledger balance.
    assert quote["max_redeemable"] == 150000
    assert await snapshot(maker) == before
    assert sent_messages == []
    response = await client.post(
        "/api/v1/orders",
        headers=_headers(80902),
        json=payload | {"quote_fingerprint": quote["quote_fingerprint"]},
    )
    assert response.status_code == 201
    assert response.json()["total"] == quote["total"]
    async with maker() as db:
        stale = await db.get(Order, stale_id)
        assert (stale.payment_status, stale.status) == ("failed", OrderStatus.CANCELLED)


@pytest.mark.parametrize("invalid", ["unknown", "out_of_stock", "points"])
async def test_quote_validation_refuses_without_writes(client, maker, invalid):
    _, pid = await seed(maker)
    if invalid == "out_of_stock":
        async with maker() as db, db.begin():
            (await db.get(Product, pid)).is_in_stock = False
    payload = _payload(
        999999 if invalid == "unknown" else pid, points_to_apply=1 if invalid == "points" else 0
    )
    before = await snapshot(maker)
    response = await client.post("/api/v1/orders/quote", headers=_headers(80902), json=payload)
    assert response.status_code == 422
    assert await snapshot(maker) == before
