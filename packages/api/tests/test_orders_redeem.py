"""Tests for spending Biotact Club points on an order (Stage 6 → Model-1 reserve-then-commit).

``points_to_apply`` is the amount of points to spend: ``0`` is the card path; ``= subtotal`` covers
the goods in full (settled outright, ``paid`` + ``confirmed``, Multicard never involved, debit
written atomically at creation); anything between RESERVES that many points and leaves the order
``unpaid``/``pending`` so Multicard can collect the card remainder. Points cover goods only, never
delivery.

Model-1 (the burn-incident fix): a PARTIAL redemption does NOT debit at creation — an abandoned card
leg (Multicard sends no webhook on abandon) would otherwise burn points that never bought anything.
The pending order row (``points_applied > 0``) IS the reservation; the real ``redeem`` debit lands
only when the card settles (``commit_redeem``, exercised in the payment-lifecycle tests). Available =
``SUM(ledger) - Σ(other pending reservations)``.

A malformed amount (below the minimum, off the 100-step, over the ceiling, or leaving the card in
the forbidden 1..4999 band) is a 422 carrying the computed ``max_redeemable`` — the caller could
have formed it correctly. A 409 is reserved for a genuine available-balance shortfall: the unlocked
form check passes, then ``reserve_points`` re-checks the reserve-aware available under ``FOR UPDATE``
and refuses, so reservations can never overspend the balance.

Requests run through a ``get_db`` override that mirrors production (commit on success, rollback on
raise) — the conftest override does neither, which would make "a refusal persisted nothing"
untestable. Committed truth is read through a second sessionmaker (READ COMMITTED).
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database import get_db
from src.loyalty import service as loyalty_service
from src.loyalty.models import CustomerProfile, LedgerReason, PointLedger
from src.loyalty.service import InsufficientPointsError
from src.main import app
from src.notifications.service import telegram_notifier
from src.orders import service as orders_service
from src.orders.models import Order, OrderStatus
from src.orders.schemas import OrderCreate, OrderItemCreate
from src.payments.schemas import MultiCardPaymentResponse, PaymentCreateData
from src.payments.service import multicard_client
from src.products.models import Product
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
BOT_TOKEN = "test-bot-token-123"

ORDERS_URL = "/api/v1/orders"
PAYMENTS_URL = "/api/v1/payments/create"

# A whole, step-aligned price that leaves room for both a partial (remainder ≥ 5000) and a full
# redemption — the workhorse for the money-path tests.
GOODS_PRICE = Decimal("69000.00")


def _headers(telegram_id: int, first_name: str = "Member") -> dict[str, str]:
    """Valid Telegram initData headers for ``telegram_id`` (the mini-app's only auth)."""
    return {
        "X-Init-Data": _make_init_data(BOT_TOKEN, {"id": telegram_id, "first_name": first_name})
    }


def _payload(
    product_id: int, *, points_to_apply: int | None = None, quantity: int = 1
) -> dict[str, object]:
    """A checkout request body. ``points_to_apply=None`` omits the field entirely (old clients)."""
    body: dict[str, object] = {
        "items": [{"product_id": product_id, "quantity": quantity}],
        "delivery_phone": "+998900000001",
        "delivery_name": "Points Buyer",
        "delivery_address": "Tashkent",
    }
    if points_to_apply is not None:
        body["points_to_apply"] = points_to_apply
    return body


@pytest.fixture(autouse=True)
def _pin_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """initData is HMAC-signed with the bot token — pin it so ``_make_init_data`` validates."""
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)


@pytest.fixture(autouse=True)
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture every Telegram send instead of hitting the network; return the captured texts."""
    captured: list[str] = []

    async def _capture(text: str, parse_mode: str = "HTML") -> bool:
        captured.append(text)
        return True

    monkeypatch.setattr(telegram_notifier, "send_message", _capture)
    return captured


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """A sessionmaker on the same test DB — committed seeding + committed-truth asserts."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client whose DB dependency behaves EXACTLY like production ``get_db``.

    Commit on success / rollback on raise is the property under test on the money path: a refusal
    must leave no order and no ledger row behind. The conftest ``client`` does neither, so a
    flushed-then-refused order would stay visible in its session and lie to the asserts.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# committed seeding / committed-truth helpers
# --------------------------------------------------------------------------- #


async def _seed_product(
    sm: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    price: Decimal = GOODS_PRICE,
) -> int:
    """Commit one catalogue product; return its id."""
    async with sm() as session, session.begin():
        product = Product(
            name=f"Product {slug}", slug=slug, price=price, is_in_stock=True, is_active=True
        )
        session.add(product)
        await session.flush()
        return product.id


async def _seed_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a user the way ``get_or_create_user`` would; return its id."""
    async with sm() as session, session.begin():
        user = User(telegram_id=telegram_id, first_name="Seeded")
        session.add(user)
        await session.flush()
        return user.id


async def _seed_points(sm: async_sessionmaker[AsyncSession], *, user_id: int, amount: int) -> None:
    """Commit a club membership (profile row) + one earn row worth ``amount`` points.

    The profile row matters: it is the FOR UPDATE serializer every redeem locks. Created the
    same race-safe way the earn path does (INSERT … ON CONFLICT DO NOTHING).
    """
    async with sm() as session, session.begin():
        await session.execute(
            pg_insert(CustomerProfile)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        session.add(PointLedger(user_id=user_id, amount=amount, reason=LedgerReason.CASHBACK))


async def _committed_balance(sm: async_sessionmaker[AsyncSession], user_id: int) -> int:
    """COMMITTED point balance — what any other transaction (accounting included) sees."""
    async with sm() as session:
        return await loyalty_service.get_balance(session, user_id)


async def _committed_order_count(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as session:
        return int((await session.execute(select(func.count()).select_from(Order))).scalar_one())


async def _committed_redeem_rows(
    sm: async_sessionmaker[AsyncSession],
) -> list[PointLedger]:
    async with sm() as session:
        result = await session.execute(
            select(PointLedger).where(PointLedger.reason == LedgerReason.REDEEM.value)
        )
        return list(result.scalars().all())


async def _committed_order(sm: async_sessionmaker[AsyncSession], order_id: int) -> Order:
    async with sm() as session:
        return (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()


async def _seed_partial_order(
    sm: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    points_applied: int,
    age: timedelta,
    payment_uuid: str | None = None,
    payment_status: str = "unpaid",
) -> int:
    """Commit an unpaid partial-redeem order aged ``age`` — a standing reservation of ``points_applied``.

    ``created_at`` is set explicitly (overriding the server default) so a test can make the order
    older than :data:`~src.orders.service.REDEEM_RESERVE_TIMEOUT` and have the lazy cleanup pick it up.
    """
    async with sm() as session, session.begin():
        order = Order(
            order_number=f"ORD-STALE-{user_id}-{int(age.total_seconds())}",
            user_id=user_id,
            subtotal=Decimal("69000.00"),
            delivery_fee=Decimal("0"),
            total=Decimal("69000.00"),
            points_applied=points_applied,
            payment_uuid=payment_uuid,
            payment_status=payment_status,
            status=OrderStatus.PENDING,
            created_at=datetime.now(UTC) - age,
        )
        session.add(order)
        await session.flush()
        return order.id


# --------------------------------------------------------------------------- #
# refusals (422): a malformed amount writes exactly nothing, and returns the ceiling
# --------------------------------------------------------------------------- #


async def test_over_affordable_amount_is_422_with_max(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """Requesting more points than the balance affords → 422 carrying ``max_redeemable`` (not 409).

    Balance 100, goods 69 000: nothing legal can be redeemed, so the ceiling is 0 and the malformed
    amount is rejected at the form gate before any write. The old all-or-nothing model answered 409
    here; the 409 is now reserved for the concurrency race below.
    """
    user_id = await _seed_user(maker, telegram_id=80001)
    product_id = await _seed_product(maker, slug="redeem-over")
    await _seed_points(maker, user_id=user_id, amount=100)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80001)
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "invalid_redeem_amount"
    assert detail["max_redeemable"] == 0
    assert await _committed_order_count(maker) == 0
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, user_id) == 100


async def test_non_member_requesting_points_is_422(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """A user who never joined the club (no profile row, balance 0) cannot redeem → 422, max 0."""
    product_id = await _seed_product(maker, slug="redeem-nonmember")

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=500), headers=_headers(80002)
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["max_redeemable"] == 0
    assert await _committed_order_count(maker) == 0


async def test_below_minimum_is_422(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """``points_to_apply`` under MIN_REDEEM (500) is refused even when affordable."""
    user_id = await _seed_user(maker, telegram_id=80003)
    product_id = await _seed_product(maker, slug="redeem-min")
    await _seed_points(maker, user_id=user_id, amount=100_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=400), headers=_headers(80003)
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "invalid_redeem_amount"
    assert await _committed_order_count(maker) == 0


async def test_not_a_multiple_of_100_is_422(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """An amount off the 100-step is refused (the card remainder must stay a whole sum too)."""
    user_id = await _seed_user(maker, telegram_id=80004)
    product_id = await _seed_product(maker, slug="redeem-step")
    await _seed_points(maker, user_id=user_id, amount=100_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=60_050), headers=_headers(80004)
    )

    assert resp.status_code == 422, resp.text
    assert await _committed_order_count(maker) == 0


async def test_dead_zone_remainder_is_422(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """A card remainder inside the forbidden 1..4999 band is refused, even under the ceiling.

    Goods 69 000, balance rich (max = 69 000). ``points_to_apply=65 000`` is ≤ max, ≥ 500 and
    step-aligned, yet leaves the card 4 000 — the dead zone — so it must be rejected. This is the
    branch NOT implied by ``points <= max_redeemable``.
    """
    user_id = await _seed_user(maker, telegram_id=80005)
    product_id = await _seed_product(maker, slug="redeem-deadzone")
    await _seed_points(maker, user_id=user_id, amount=1_000_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=65_000), headers=_headers(80005)
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["max_redeemable"] == 69_000
    assert await _committed_order_count(maker) == 0
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, user_id) == 1_000_000


async def test_fractional_subtotal_refuses_points(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """A non-step subtotal (price past the admin whole-sum norm) admits no legal amount → 422.

    Points move whole sums and this DB is the only record of the debit; a fractional subtotal has
    no step-aligned full cover and is too small for a partial, so the ceiling is 0 and any spend is
    refused — never silently rounded.
    """
    user_id = await _seed_user(maker, telegram_id=80006)
    product_id = await _seed_product(maker, slug="redeem-fractional", price=Decimal("1000.50"))
    await _seed_points(maker, user_id=user_id, amount=1_000_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=1_000), headers=_headers(80006)
    )

    assert resp.status_code == 422, resp.text
    assert await _committed_order_count(maker) == 0
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, user_id) == 1_000_000


# --------------------------------------------------------------------------- #
# full coverage: settled outright, paid + confirmed, marked «оплачено баллами»
# --------------------------------------------------------------------------- #


async def test_full_coverage_pays_confirms_and_notifies(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    """``points == total`` settles the order outright and fires the «ОПЛАЧЕНО БАЛЛАМИ» notice."""
    user_id = await _seed_user(maker, telegram_id=80010)
    product_id = await _seed_product(maker, slug="redeem-full")
    await _seed_points(maker, user_id=user_id, amount=69_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80010)
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["points_applied"] == 69_000
    assert body["payment_status"] == "paid"
    assert body["status"] == "confirmed"
    assert body["paid_at"] is not None
    assert body["payment_uuid"] is None  # Multicard never touched this order

    redeem_rows = await _committed_redeem_rows(maker)
    assert len(redeem_rows) == 1
    assert redeem_rows[0].order_id == body["id"]
    assert redeem_rows[0].amount == -69_000
    assert await _committed_balance(maker, user_id) == 0

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert "ОПЛАЧЕНО БАЛЛАМИ" in message
    assert "Списано баллов" in message


async def test_new_order_notice_survives_a_product_deleted_before_payment(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    sent_messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A product deleted between order creation and ``/payments/create`` must not mute the notice.

    The two are separate HTTP requests and ``create_payment`` re-reads the order from the DB, so
    "the same moment" is a property of the frontend calling them back to back, not a guarantee of
    the API: ``order_items.product_id`` can already be NULL by then (ON DELETE SET NULL). Reading
    ``item.product.slug`` unguarded would raise inside the notify block, which swallows exceptions
    — the fulfilment group would never hear about the new order.
    Mutation: drop ``if item.product else None`` in the create-payment notice → red.
    """
    await _seed_user(maker, telegram_id=80012)
    product_id = await _seed_product(maker, slug="probe_active_caps")

    created = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=0), headers=_headers(80012)
    )
    assert created.status_code == 201, created.text

    async with maker() as session, session.begin():
        await session.execute(delete(Product).where(Product.id == product_id))

    sent_messages.clear()  # интересует только уведомление о выставленном счёте

    async def _fake_invoice(payment_data: PaymentCreateData) -> MultiCardPaymentResponse:
        return MultiCardPaymentResponse(uuid="stub-orphan", pay_url="https://pay.uz/stub")

    monkeypatch.setattr(multicard_client, "create_invoice", _fake_invoice)

    resp = await client.post(
        PAYMENTS_URL,
        json={"order_id": created.json()["id"], "return_url": "https://x.uz"},
        headers=_headers(80012),
    )

    assert resp.status_code == 200, resp.text
    assert len(sent_messages) == 1  # уведомление ушло, несмотря на удалённый товар
    assert "НОВЫЙ ЗАКАЗ" in sent_messages[0]
    assert "(капсулы)" not in sent_messages[0]


async def test_notifications_release_form_points_path(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    """A fully-points order carries the release form too — it ships like any other.

    This path never reaches the payments router, so it is the only send for such an order. It also
    guards a dependency that is invisible from here: the product is reachable only because
    ``create_order`` ends with ``db.refresh(order)``, which reloads the items and (via
    ``lazy="joined"``) their products. Drop that refresh and the notification silently stops going
    out — the block that sends it swallows exceptions. Then this test fails.
    """
    user_id = await _seed_user(maker, telegram_id=80011)
    product_id = await _seed_product(maker, slug="probe_active_caps")
    await _seed_points(maker, user_id=user_id, amount=69_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80011)
    )

    assert resp.status_code == 201, resp.text
    assert len(sent_messages) == 1
    assert "(капсулы)" in sent_messages[0]


async def test_full_points_order_cannot_be_invoiced_via_multicard(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """`POST /payments/create` on a fully points-paid order → 400 (already paid), before any IO."""
    user_id = await _seed_user(maker, telegram_id=80011)
    product_id = await _seed_product(maker, slug="redeem-noinvoice")
    await _seed_points(maker, user_id=user_id, amount=69_000)

    created = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80011)
    )
    assert created.status_code == 201, created.text

    resp = await client.post(
        PAYMENTS_URL,
        json={"order_id": created.json()["id"], "return_url": "https://x.uz"},
        headers=_headers(80011),
    )

    assert resp.status_code == 400
    assert "already paid" in resp.text.lower()


async def test_full_coverage_refused_when_reservation_holds_the_balance(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """Full coverage is reserve-aware too: a standing partial reservation reduces ``available``, so a
    full-points order that would overspend it is refused (409).

    The Model-1 reserve invariant (``SUM(ledger) >= Σ reservations``) must hold for EVERY redemption
    shape, not only partials — otherwise a full-coverage debit could leave a pending partial's
    reservation unbacked, and its later settlement would find the balance short. Balance 69 000 with
    one fresh (not yet stale) partial reserving 60 000: available is 9 000, so a full-coverage 69 000
    order is refused. Mutation: check the raw balance instead of ``available`` in ``reserve_points``
    -> the full order is admitted and debits 69 000, leaving the 60 000 reservation unbacked -> red.
    """
    user_id = await _seed_user(maker, telegram_id=80090)
    product_id = await _seed_product(maker, slug="redeem-full-reserved")
    await _seed_points(maker, user_id=user_id, amount=69_000)
    # a FRESH partial reservation (age well under the timeout) — lazy cleanup will not touch it
    await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=60_000,
        age=timedelta(minutes=1),
        payment_status="unpaid",
    )

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80090)
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["balance"] == 9_000  # 69 000 balance - 60 000 reserved
    assert await _committed_redeem_rows(maker) == []  # nothing debited — the full order was refused


# --------------------------------------------------------------------------- #
# partial redemption: points debited now, order UNPAID, card owes the remainder
# --------------------------------------------------------------------------- #


async def test_partial_redeem_reserves_points_writes_no_ledger_row(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    """Model-1: a partial redemption RESERVES points — order ``unpaid``, NO ledger row, balance intact.

    This is the burn-incident fix. The points are NOT debited at creation, so an abandoned card leg
    (Multicard sends no webhook on abandon) leaves nothing behind to burn. The pending order row
    (``points_applied > 0``) IS the reservation; the real debit lands only when the card settles (see
    the payment-lifecycle tests). Mutation: pass ``settle_now=True`` for the partial in
    ``create_order`` -> a -60 000 row appears and the committed balance drops -> this test goes red.
    """
    user_id = await _seed_user(maker, telegram_id=80020)
    product_id = await _seed_product(maker, slug="redeem-partial")
    await _seed_points(maker, user_id=user_id, amount=100_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=60_000), headers=_headers(80020)
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["points_applied"] == 60_000
    assert body["payment_status"] == "unpaid"
    assert body["status"] == "pending"
    assert body["paid_at"] is None

    # The reservation writes NO ledger row and does NOT touch the balance — nothing to burn.
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, user_id) == 100_000
    # An unpaid order is NOT announced as paid at creation — that would lie to the fulfilment group.
    assert sent_messages == []


async def test_remainder_exactly_the_floor_is_accepted(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """Leaving the card exactly MIN_CARD_REMAINDER (5 000) is allowed — the tight edge of the band.

    A partial redemption, so Model-1 reserves rather than debits: the order is ``unpaid`` and the
    balance is untouched (no ledger row) — the reservation is the pending order itself.
    """
    user_id = await _seed_user(maker, telegram_id=80021)
    product_id = await _seed_product(maker, slug="redeem-floor-edge")
    await _seed_points(maker, user_id=user_id, amount=100_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=64_000), headers=_headers(80021)
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["payment_status"] == "unpaid"
    assert await _committed_redeem_rows(maker) == []  # reserved, not debited
    assert await _committed_balance(maker, user_id) == 100_000  # balance untouched until settlement


async def test_partial_order_invoices_only_the_remainder(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    sent_messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`POST /payments/create` on a partial order invoices ``total - points_applied``, not ``total``.

    Goods 69 000, points 60 000 → the card is invoiced for exactly 9 000 (900 000 tiyins). The
    new-order notice shows the card amount and the points debited.
    Mutation: revert the invoice amount to ``int(order.total * 100)`` → the captured amount becomes
    6 900 000 → red.
    """
    user_id = await _seed_user(maker, telegram_id=80022)
    product_id = await _seed_product(maker, slug="redeem-invoice")
    await _seed_points(maker, user_id=user_id, amount=100_000)

    created = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=60_000), headers=_headers(80022)
    )
    assert created.status_code == 201, created.text
    sent_messages.clear()  # drop anything from creation; assert only on the invoice notice

    captured: dict[str, int] = {}

    async def _fake_invoice(payment_data: PaymentCreateData) -> MultiCardPaymentResponse:
        captured["amount"] = payment_data.amount
        return MultiCardPaymentResponse(uuid="stub-uuid", pay_url="https://pay.uz/stub")

    monkeypatch.setattr(multicard_client, "create_invoice", _fake_invoice)

    resp = await client.post(
        PAYMENTS_URL,
        json={"order_id": created.json()["id"], "return_url": "https://x.uz"},
        headers=_headers(80022),
    )

    assert resp.status_code == 200, resp.text
    assert captured["amount"] == (69_000 - 60_000) * 100  # the card remainder, in tiyins
    assert len(sent_messages) == 1
    notice = sent_messages[0]
    assert "НОВЫЙ ЗАКАЗ" in notice
    assert "Списано баллов" in notice
    assert "9 000" in notice  # card remainder shown, not the 69 000 catalogue sum


# --------------------------------------------------------------------------- #
# the card path is unchanged — byte for byte, even for a user WITH points
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "points_field",
    [None, 0],
    ids=["field absent (old clients)", "points_to_apply=0"],
)
async def test_card_path_is_unchanged(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    sent_messages: list[str],
    points_field: int | None,
) -> None:
    """Absent field and explicit ``0`` behave exactly as before — even for a user with points."""
    user_id = await _seed_user(maker, telegram_id=80030)
    product_id = await _seed_product(maker, slug="card-regress")
    await _seed_points(maker, user_id=user_id, amount=1_000_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=points_field), headers=_headers(80030)
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["points_applied"] == 0
    assert body["payment_status"] == "unpaid"
    assert body["status"] == "pending"
    assert body["paid_at"] is None
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, user_id) == 1_000_000
    # No admin notification from POST /orders on the card path — it fires at invoice time.
    assert sent_messages == []


# --------------------------------------------------------------------------- #
# race + double-debit backstop (the two ways a balance could go negative)
# --------------------------------------------------------------------------- #


async def test_race_two_partial_reservations_never_overspend(
    test_db: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent partial reservations that together exceed the balance -> one reserves, one 409.

    Model-1: a partial does not debit — it reserves by keeping the order open. Both racers pass the
    unlocked form check (each sees balance 60 000), then serialize on the ``customer_profile`` FOR
    UPDATE lock inside ``reserve_points``: the winner's pending order becomes the reservation, the
    loser re-reads it as ``reserved`` and is refused, so two reservations can never exceed the
    balance. NEITHER writes a ledger row (nothing has settled) and the balance is untouched. The
    barrier wraps ``reserve_points`` so BOTH orders are already flushed (each a reservation, still
    uncommitted) before either acquires the lock — otherwise each reads the other's reservation as
    invisible and both admit. Mutation: remove ``.with_for_update()`` from ``reserve_points`` -> both
    read available 60 000 and both reserve -> red (two open orders reserving 120 000 against 60 000).
    (A barrier in ``_price_lines`` is too early: asyncio then serialises the two commits and the loser
    sees the winner's reservation even with the lock removed, so it would not catch the mutation.)
    """
    user_id = await _seed_user(maker, telegram_id=80040)
    product_id = await _seed_product(maker, slug="redeem-race")
    await _seed_points(maker, user_id=user_id, amount=60_000)  # room for exactly one reservation

    real_reserve = loyalty_service.reserve_points
    barrier = asyncio.Barrier(2)

    async def _barriered_reserve(db: AsyncSession, **kwargs: object) -> None:
        # Both racers have flushed their order (the reservation) by the time they reach here; release
        # them together so each computes ``available`` while the other's order is still uncommitted.
        await asyncio.wait_for(barrier.wait(), timeout=10)  # fail loudly instead of hanging CI
        await real_reserve(db, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(loyalty_service, "reserve_points", _barriered_reserve)

    order_data = OrderCreate(
        items=[OrderItemCreate(product_id=product_id, quantity=1)],
        points_to_apply=60_000,
    )

    async def _attempt() -> int:
        async with maker() as session, session.begin():
            order = await orders_service.create_order(session, order_data, user_id=user_id)
            return order.id

    results = await asyncio.gather(_attempt(), _attempt(), return_exceptions=True)

    winners = [r for r in results if isinstance(r, int)]
    refusals = [r for r in results if isinstance(r, InsufficientPointsError)]
    assert len(winners) == 1, f"exactly one reservation must win, got {results!r}"
    assert len(refusals) == 1, f"the loser must be refused, got {results!r}"
    assert refusals[0].balance == 0  # the loser saw the winner's reservation eat the balance

    assert await _committed_order_count(maker) == 1  # only the winner's order persisted
    assert await _committed_redeem_rows(maker) == []  # a reservation writes no ledger row
    assert await _committed_balance(maker, user_id) == 60_000  # untouched — nothing settled yet


async def test_insufficient_points_maps_to_structured_409(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 409 contract THROUGH the router: `InsufficientPointsError` → 409 with error/balance/deficit.

    The service-level race test proves the debit never goes negative, but bypasses the HTTP layer —
    so a typo in the router's 409 status or detail shape would pass unnoticed. A single request
    cannot reach a shortfall (the unlocked form check and the locked debit see the same balance), and
    a genuine HTTP race for one user serialises on the auth user-row write. So the shortfall — which
    in production only arises from concurrency inside ``reserve_points`` — is injected at that exact
    boundary, and this asserts the router's translation of it.
    """
    user_id = await _seed_user(maker, telegram_id=80070)
    product_id = await _seed_product(maker, slug="redeem-409-map")
    await _seed_points(maker, user_id=user_id, amount=60_000)

    async def _refuse(
        db: object, *, user_id: int, order_id: int, amount: int, settle_now: bool
    ) -> None:
        raise InsufficientPointsError(balance=5, required=amount)

    monkeypatch.setattr(loyalty_service, "reserve_points", _refuse)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=60_000), headers=_headers(80070)
    )

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "insufficient_points"
    assert detail["balance"] == 5
    assert detail["required"] == 60_000
    assert detail["deficit"] == 60_000 - 5
    # The injected refusal aborts the request → nothing persisted (rollback on the raise).
    assert await _committed_order_count(maker) == 0


async def test_same_order_cannot_be_committed_twice_unique_backstop(
    test_db: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    """The partial ``UNIQUE(order_id, reason)`` makes a second redeem row for one order impossible.

    Model-1 settles a partial's debit via ``commit_redeem`` in the success callback. The lifecycle is
    idempotent (``UNIQUE(payment_uuid, status)``) so a real callback commits once; this proves the
    schema-level backstop should any caller ever replay the debit for the same order.
    """
    user_id = await _seed_user(maker, telegram_id=80050)
    product_id = await _seed_product(maker, slug="redeem-unique")
    await _seed_points(maker, user_id=user_id, amount=200_000)

    async with maker() as session, session.begin():
        order = await orders_service.create_order(
            session,
            OrderCreate(
                items=[OrderItemCreate(product_id=product_id, quantity=1)],
                points_to_apply=60_000,
            ),
            user_id=user_id,
        )
        order_id = order.id

    # A partial only RESERVES at creation — no ledger row yet.
    assert await _committed_redeem_rows(maker) == []

    async with maker() as session, session.begin():
        wrote = await loyalty_service.commit_redeem(
            session, user_id=user_id, order_id=order_id, amount=60_000
        )
    assert wrote is True

    async with maker() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                await loyalty_service.commit_redeem(
                    session, user_id=user_id, order_id=order_id, amount=1_000
                )

    # The backstop held: still exactly one debit, balance reflects one settlement only.
    assert len(await _committed_redeem_rows(maker)) == 1
    assert await _committed_balance(maker, user_id) == 200_000 - 60_000


# --------------------------------------------------------------------------- #
# notification robustness on the full-points path (no invoice to fall back on)
# --------------------------------------------------------------------------- #


async def test_notification_failure_does_not_fail_the_order(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telegram being down must not lose a settled points order — the debit already happened."""
    user_id = await _seed_user(maker, telegram_id=80060)
    product_id = await _seed_product(maker, slug="redeem-notify-down")
    await _seed_points(maker, user_id=user_id, amount=69_000)

    async def _boom(text: str, parse_mode: str = "HTML") -> bool:
        raise RuntimeError("telegram down")

    monkeypatch.setattr(telegram_notifier, "send_message", _boom)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80060)
    )

    assert resp.status_code == 201, resp.text
    assert len(await _committed_redeem_rows(maker)) == 1
    assert await _committed_balance(maker, user_id) == 0


async def test_notification_runs_after_commit_and_holds_no_lock(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the notification is in flight, the redeem is COMMITTED and the profile is UNLOCKED.

    [Gate-2 MINOR, PR-6.3] The send is a network call with a 10s httpx timeout. If it ran inside the
    still-open request transaction it would hold ``reserve_points``' ``FOR UPDATE`` lock on this
    customer's ``customer_profile`` row for its whole duration — stalling every other loyalty write
    for the same user behind a Telegram outage. The probe runs INSIDE the notifier: an independent
    connection tries ``SELECT … FOR UPDATE NOWAIT`` on the locked row; Postgres raises 55P03
    immediately if the lock is still held, so the test states the property without sleeping.
    Mutation: drop ``await db.commit()`` from the router → red.
    """
    user_id = await _seed_user(maker, telegram_id=80061)
    product_id = await _seed_product(maker, slug="redeem-notify-lock")
    await _seed_points(maker, user_id=user_id, amount=69_000)

    observed: dict[str, object] = {}

    async def _probe_while_sending(text: str, parse_mode: str = "HTML") -> bool:
        async with maker() as probe, probe.begin():
            try:
                await probe.execute(
                    select(CustomerProfile.id)
                    .where(CustomerProfile.user_id == user_id)
                    .with_for_update(nowait=True)
                )
                observed["profile_lock_free"] = True
            except DBAPIError as exc:  # 55P03 — the handler is still holding the row lock
                observed["profile_lock_free"] = False
                observed["lock_error"] = str(exc.orig)
        observed["committed_redeem_rows"] = len(await _committed_redeem_rows(maker))
        return True

    monkeypatch.setattr(telegram_notifier, "send_message", _probe_while_sending)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80061)
    )

    assert resp.status_code == 201, resp.text
    assert observed["profile_lock_free"] is True, (
        f"the profile row was still locked during the Telegram call: {observed.get('lock_error')}"
    )
    assert observed["committed_redeem_rows"] == 1, "the debit was not committed before the send"


# --------------------------------------------------------------------------- #
# Model-1 lazy cleanup: an abandoned partial reservation is reclaimed on the next attempt
# --------------------------------------------------------------------------- #


async def test_stale_partial_reservation_is_expired_and_frees_reserve(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale partial reservation is reclaimed on the next attempt: invoice deleted, order cancelled,
    the reserve it held freed so the new redemption fits (Model-1 lazy cleanup, Gate-1 #3 + M2).

    The user has 100 000 points and one stale partial reserving 60 000 (older than the timeout), armed
    with a Multicard invoice. Without cleanup only 40 000 is available and a fresh 60 000 redemption
    would 409. Cleanup deletes the invoice (mocked success), cancels the stale order, and the freed
    reserve admits the new order. Mutation: skip the atomic cancel in ``_expire_partial`` -> the stale
    reserve stands -> the new redemption 409s -> red.
    """
    user_id = await _seed_user(maker, telegram_id=80080)
    product_id = await _seed_product(maker, slug="redeem-cleanup")
    await _seed_points(maker, user_id=user_id, amount=100_000)
    stale_id = await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=60_000,
        age=timedelta(hours=1),
        payment_uuid="stale-uuid",
        payment_status="pending",
    )

    deleted: list[str] = []

    async def _fake_delete(uuid: str) -> bool:
        deleted.append(uuid)
        return True

    monkeypatch.setattr(multicard_client, "delete_invoice", _fake_delete)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=60_000), headers=_headers(80080)
    )

    assert resp.status_code == 201, resp.text  # the freed reserve admitted the new redemption
    assert deleted == [
        "stale-uuid"
    ]  # the stale invoice was annulled first (M3 order of operations)

    stale = await _committed_order(maker, stale_id)
    assert stale.payment_status == "failed"
    assert stale.status == OrderStatus.CANCELLED

    body = resp.json()
    assert body["payment_status"] == "unpaid"  # the new order reserves (partial), not settled
    assert body["points_applied"] == 60_000
    assert await _committed_redeem_rows(maker) == []  # reservation writes no ledger row
    assert await _committed_balance(maker, user_id) == 100_000  # balance untouched


async def test_stale_partial_with_unremovable_invoice_is_not_cancelled(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a stale invoice cannot be deleted (Multicard reports it already paid), the order is NOT
    cancelled and its reserve stands — the third burn path stays closed (Model-1, Gate-1 #2 + M3).

    ``delete_invoice`` returns False (already paid / transport error). Cancelling then would abandon
    an order Multicard may still settle, so the stale order is left ``pending``, its 60 000 reserve
    still held, and the fresh 60 000 redemption is refused (409) because only 40 000 is available.
    Mutation: cancel regardless of the delete result in ``_expire_partial`` -> the stale order flips to
    cancelled and the new redemption is admitted (201) -> red.
    """
    user_id = await _seed_user(maker, telegram_id=80081)
    product_id = await _seed_product(maker, slug="redeem-cleanup-paid")
    await _seed_points(maker, user_id=user_id, amount=100_000)
    stale_id = await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=60_000,
        age=timedelta(hours=1),
        payment_uuid="paid-uuid",
        payment_status="pending",
    )

    async def _refuse_delete(uuid: str) -> bool:
        return (
            False  # Multicard: "Транзакция по инвойсу завершена" -> the invoice may still be paid
        )

    monkeypatch.setattr(multicard_client, "delete_invoice", _refuse_delete)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=60_000), headers=_headers(80081)
    )

    assert resp.status_code == 409, resp.text  # reserve still held -> not enough available
    assert resp.json()["detail"]["balance"] == 40_000  # 100 000 balance - 60 000 still reserved

    stale = await _committed_order(maker, stale_id)
    assert stale.payment_status == "pending"  # NOT cancelled — the invoice may still be charged
    assert stale.status == OrderStatus.PENDING


# --------------------------------------------------------------------------- #
# PR-6.6-B: the customer releases their own reservations on demand
#
# Same machinery as the lazy cleanup, minus the age bar: the customer has just said they are not
# paying, so there is nothing to wait for. Safety still lives in delete-invoice-BEFORE-cancel.
# --------------------------------------------------------------------------- #

RELEASE_URL = f"{ORDERS_URL}/reservations/release"


async def _always_delete(uuid: str) -> bool:
    return True


async def _never_delete(uuid: str) -> bool:
    return False


async def test_release_frees_a_fresh_reservation_without_waiting(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reservation minutes old — far inside the timeout — is released the moment the customer asks.

    This is the whole point of the endpoint: the lazy cleanup would refuse this order for another
    ~29 minutes. Mutation: keep the age bar (max_age default instead of None) -> nothing released.
    """
    user_id = await _seed_user(maker, telegram_id=80200)
    await _seed_points(maker, user_id=user_id, amount=100_000)
    order_id = await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=60_000,
        age=timedelta(minutes=1),
        payment_uuid="fresh-uuid",
        payment_status="pending",
    )
    monkeypatch.setattr(multicard_client, "delete_invoice", _always_delete)

    resp = await client.post(RELEASE_URL, headers=_headers(80200))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"released_orders": 1, "released_points": 60_000, "blocked_orders": 0}
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "failed"
    assert order.status == OrderStatus.CANCELLED
    assert order.points_applied == 60_000  # historical fact of the order, never zeroed out
    assert await _committed_redeem_rows(maker) == []  # releasing a reserve is not a money operation
    assert await _committed_balance(maker, user_id) == 100_000


async def test_release_frees_every_reservation_the_customer_holds(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two live reservations → both released, points summed (decision R-1: the button is not per-order)."""
    user_id = await _seed_user(maker, telegram_id=80201)
    await _seed_points(maker, user_id=user_id, amount=100_000)
    await _seed_partial_order(
        maker, user_id=user_id, points_applied=20_000, age=timedelta(minutes=2), payment_uuid="a"
    )
    await _seed_partial_order(
        maker, user_id=user_id, points_applied=30_000, age=timedelta(minutes=3), payment_uuid="b"
    )
    monkeypatch.setattr(multicard_client, "delete_invoice", _always_delete)

    resp = await client.post(RELEASE_URL, headers=_headers(80201))

    assert resp.status_code == 200, resp.text
    assert resp.json()["released_orders"] == 2
    assert resp.json()["released_points"] == 50_000


async def test_release_leaves_an_unremovable_invoice_alone_and_409s(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multicard refuses the delete → the order is NOT cancelled and the caller gets a 409.

    The refusal is exactly the case where cancelling would be dangerous: the invoice may already be
    paid, and a cancelled-but-payable order means a late charge for goods nobody will ship.
    Mutation: cancel before/regardless of the delete result -> the order flips -> red.
    """
    user_id = await _seed_user(maker, telegram_id=80202)
    await _seed_points(maker, user_id=user_id, amount=100_000)
    order_id = await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=60_000,
        age=timedelta(minutes=1),
        payment_uuid="paid-uuid",
        payment_status="pending",
    )
    monkeypatch.setattr(multicard_client, "delete_invoice", _never_delete)

    resp = await client.post(RELEASE_URL, headers=_headers(80202))

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "reservations_not_released"
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"  # untouched — its reserve still stands
    assert order.status != OrderStatus.CANCELLED


async def test_release_reports_partial_success_as_200_not_409(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One released, one blocked → 200 with both counts (invariant I-3).

    Collapsing this into a 409 would tell a customer whose points partly came back that nothing
    happened — they would press again, and the second press would report 0 released.
    """
    user_id = await _seed_user(maker, telegram_id=80203)
    await _seed_points(maker, user_id=user_id, amount=100_000)
    await _seed_partial_order(
        maker, user_id=user_id, points_applied=20_000, age=timedelta(minutes=2), payment_uuid="ok"
    )
    await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=30_000,
        age=timedelta(minutes=3),
        payment_uuid="stuck",
    )

    async def _selective(uuid: str) -> bool:
        return uuid == "ok"

    monkeypatch.setattr(multicard_client, "delete_invoice", _selective)

    resp = await client.post(RELEASE_URL, headers=_headers(80203))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"released_orders": 1, "released_points": 20_000, "blocked_orders": 1}


async def test_release_cancels_an_order_that_never_reached_the_gateway(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``payment_uuid`` (the customer never got to /payments/create) → cancelled with no Multicard call at all."""
    user_id = await _seed_user(maker, telegram_id=80204)
    await _seed_points(maker, user_id=user_id, amount=100_000)
    order_id = await _seed_partial_order(
        maker, user_id=user_id, points_applied=15_000, age=timedelta(minutes=1)
    )
    called: list[str] = []

    async def _tracking(uuid: str) -> bool:
        called.append(uuid)
        return True

    monkeypatch.setattr(multicard_client, "delete_invoice", _tracking)

    resp = await client.post(RELEASE_URL, headers=_headers(80204))

    assert resp.status_code == 200, resp.text
    assert resp.json()["released_points"] == 15_000
    assert called == []  # there is no invoice to annul, so the gateway is not called at all
    assert (await _committed_order(maker, order_id)).status == OrderStatus.CANCELLED


async def test_release_with_nothing_held_is_a_quiet_200(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """Nothing reserved (or a second click right after a successful release) → 200, zero released.

    An error here would punish a customer for pressing a button twice (invariant I-4).
    """
    user_id = await _seed_user(maker, telegram_id=80205)
    await _seed_points(maker, user_id=user_id, amount=100_000)

    resp = await client.post(RELEASE_URL, headers=_headers(80205))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"released_orders": 0, "released_points": 0, "blocked_orders": 0}


async def test_release_is_idempotent_on_a_second_press(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Press, then press again: the first frees the points, the second is a harmless zero (invariant I-4)."""
    user_id = await _seed_user(maker, telegram_id=80206)
    await _seed_points(maker, user_id=user_id, amount=100_000)
    await _seed_partial_order(
        maker, user_id=user_id, points_applied=25_000, age=timedelta(minutes=1), payment_uuid="one"
    )
    monkeypatch.setattr(multicard_client, "delete_invoice", _always_delete)

    first = await client.post(RELEASE_URL, headers=_headers(80206))
    second = await client.post(RELEASE_URL, headers=_headers(80206))

    assert first.json()["released_points"] == 25_000
    assert second.status_code == 200, second.text
    assert second.json() == {"released_orders": 0, "released_points": 0, "blocked_orders": 0}


async def test_release_never_touches_another_customers_reservation(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B pressing the button must not cancel A's order. Mutation: drop the user_id filter -> red."""
    victim_id = await _seed_user(maker, telegram_id=80207)
    await _seed_points(maker, user_id=victim_id, amount=100_000)
    victim_order = await _seed_partial_order(
        maker,
        user_id=victim_id,
        points_applied=40_000,
        age=timedelta(minutes=1),
        payment_uuid="v",
        payment_status="pending",
    )
    await _seed_user(maker, telegram_id=80208)
    monkeypatch.setattr(multicard_client, "delete_invoice", _always_delete)

    resp = await client.post(RELEASE_URL, headers=_headers(80208))

    assert resp.status_code == 200, resp.text
    assert resp.json()["released_orders"] == 0
    order = await _committed_order(maker, victim_order)
    assert order.payment_status == "pending"
    assert order.status != OrderStatus.CANCELLED


async def test_release_never_cancels_a_paid_order(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled order is not a reservation — its points were already debited by ``commit_redeem``.

    Mutation: drop the payment_status filter -> a paid order gets cancelled -> red.
    """
    user_id = await _seed_user(maker, telegram_id=80209)
    await _seed_points(maker, user_id=user_id, amount=100_000)
    paid_id = await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=30_000,
        age=timedelta(minutes=1),
        payment_uuid="paid",
        payment_status="paid",
    )
    monkeypatch.setattr(multicard_client, "delete_invoice", _always_delete)

    resp = await client.post(RELEASE_URL, headers=_headers(80209))

    assert resp.status_code == 200, resp.text
    assert resp.json()["released_orders"] == 0
    order = await _committed_order(maker, paid_id)
    assert order.payment_status == "paid"
    assert order.status != OrderStatus.CANCELLED


async def test_lazy_cleanup_still_spares_a_fresh_reservation(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DEFAULT age bar is intact: creating a new order does not reap a minutes-old reservation.

    Guards the compatibility claim behind ``max_age``'s default. Mutation: make ``None`` the default
    -> creating any redeeming order would instantly reap the customer's live reservation -> red,
    because the fresh reserve would stop being counted and the 409 below would not happen.
    """
    user_id = await _seed_user(maker, telegram_id=80210)
    product_id = await _seed_product(maker, slug="redeem-fresh-spared")
    await _seed_points(maker, user_id=user_id, amount=100_000)
    fresh_id = await _seed_partial_order(
        maker,
        user_id=user_id,
        points_applied=60_000,
        age=timedelta(minutes=1),
        payment_uuid="fresh",
        payment_status="pending",
    )
    monkeypatch.setattr(multicard_client, "delete_invoice", _always_delete)

    # 60 000 is still reserved, so only 40 000 is available and a second 60 000 spend must be refused.
    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=60_000), headers=_headers(80210)
    )

    assert resp.status_code == 409, resp.text
    order = await _committed_order(maker, fresh_id)
    assert order.payment_status == "pending"  # the lazy path left the fresh reservation alone
    assert order.status != OrderStatus.CANCELLED


# --------------------------------------------------------------------------- #
# PR-6.5-B: отметки времени пишутся с часовым поясом
#
# `datetime.utcnow()` отдаёт наивное время, а колонка — `timestamptz`. Драйвер трактует наивное
# значение как локальное время ПРОЦЕССА приложения, поэтому отметка уезжает ровно на смещение
# пояса сервера: на проде (Europe/Berlin) это -2ч летом и -1ч зимой.
#
# Пояс самого PostgreSQL на результат НЕ влияет — замерено (PR-6.5-B). Значит воспроизвести дефект
# можно только задав пояс процессу: это делает фикстура `prod_timezone`. Под `TZ=UTC`, как гоняет
# CI, наивная и осознанная записи совпадают, и тест был бы зелёным на сломанном коде.
# --------------------------------------------------------------------------- #


async def test_full_coverage_writes_timezone_aware_paid_at(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], prod_timezone: None
) -> None:
    """Полное покрытие баллами закрывает заказ на сервере — отметка оплаты не должна уехать назад."""
    user_id = await _seed_user(maker, telegram_id=80300)
    product_id = await _seed_product(maker, slug="redeem-paidat-tz")
    await _seed_points(maker, user_id=user_id, amount=69_000)
    before = datetime.now(UTC)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=69_000), headers=_headers(80300)
    )

    assert resp.status_code == 201, resp.text
    order = await _committed_order(maker, resp.json()["id"])
    assert order.paid_at is not None
    # Наивная запись под берлинским поясом уедет на час-два НАЗАД и провалит нижнюю границу.
    assert timedelta(0) <= order.paid_at - before < timedelta(minutes=5), (
        f"отметка уехала: paid_at={order.paid_at}, момент операции={before}"
    )


@pytest.mark.parametrize('points', [0, 100_000, 330_000])
async def test_sale_effective_price_settlement(client, maker, monkeypatch, points, admin_headers):
    uid = await _seed_user(maker, 80901)
    await _seed_points(maker, user_id=uid, amount=500_000)
    async with maker() as session, session.begin():
        sale = Product(name='Sale', slug='sale', price=Decimal('200000'))
        regular = Product(name='Regular', slug='regular', price=Decimal('30000'))
        session.add_all([sale, regular])
        await session.flush()
        ids = (sale.id, regular.id)
    sale_url = f'/api/v1/products/{ids[0]}'
    enabled = await client.patch(sale_url, headers=admin_headers, json={
        'is_sale': True, 'sale_price': '150000.00',
    })
    assert enabled.status_code == 200
    public = await client.get('/api/v1/products?on_sale=true')
    assert public.status_code == 200
    assert [(p['id'], p['price'], p['sale_price']) for p in public.json()['items']] == [
        (ids[0], '200000.00', '150000.00'),
    ]
    captured = []
    async def invoice(data):
        captured.append(data)
        return MultiCardPaymentResponse(uuid='sale-invoice', pay_url='https://pay.uz/stub')
    monkeypatch.setattr(multicard_client, 'create_invoice', invoice)
    payload = {'items': [{'product_id': ids[0], 'quantity': 2}, {'product_id': ids[1], 'quantity': 1}], 'points_to_apply': points}
    quote = await client.post('/api/v1/orders/quote', headers=_headers(80901), json=payload)
    assert quote.status_code == 200
    payload['quote_fingerprint'] = quote.json()['quote_fingerprint']
    response = await client.post(ORDERS_URL, headers=_headers(80901), json=payload)
    assert response.status_code == 201, response.text
    order = response.json()
    assert Decimal(order['subtotal']) == Decimal('330000')
    assert Decimal(order['total']) == sum(Decimal(i['total']) for i in order['items'])
    assert Decimal(order['items'][0]['product_price']) == Decimal('150000')
    if points == 330_000:
        assert order['payment_status'] == 'paid'
        assert order['status'] == 'confirmed'
        assert captured == []
        assert await _committed_balance(maker, uid) == 170_000
    else:
        payment = await client.post(PAYMENTS_URL, headers=_headers(80901), json={'order_id': order['id'], 'return_url': 'https://x.uz'})
        assert payment.status_code == 200, payment.text
        assert len(captured) == 1
        assert captured[0].amount == (330_000 - points) * 100
        assert sum(i.price * i.qty for i in captured[0].ofd) == captured[0].amount
    disabled = await client.patch(sale_url, headers=admin_headers, json={'is_sale': False})
    assert disabled.status_code == 200
    current = (await client.get(sale_url)).json()
    assert current['is_sale'] is False
    assert current['sale_price'] == '150000.00'
    assert (await client.get('/api/v1/products?on_sale=true')).json()['items'] == []
    fresh = await client.post('/api/v1/orders/quote', headers=_headers(80901), json={
        'items': payload['items'], 'points_to_apply': 0,
    })
    assert fresh.status_code == 200
    assert fresh.json()['total'] == '430000.00'
    persisted = await _committed_order(maker, order['id'])
    assert persisted.total == Decimal('330000')
