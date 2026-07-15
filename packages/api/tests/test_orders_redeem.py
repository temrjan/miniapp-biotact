"""Tests for spending Biotact Club points on an order (Stage 6, PR-6.3-v2 partial redemption).

``points_to_apply`` is the amount of points to spend: ``0`` is the card path; ``= subtotal`` covers
the goods in full (settled outright, ``paid`` + ``confirmed``, Multicard never involved); anything
between debits that many points NOW and leaves the order ``unpaid``/``pending`` so Multicard can
collect the card remainder. Points cover goods only, never delivery.

A malformed amount (below the minimum, off the 100-step, over the ceiling, or leaving the card in
the forbidden 1..4999 band) is a 422 carrying the computed ``max_redeemable`` — the caller could
have formed it correctly. A 409 is reserved for a genuine live-balance shortfall under concurrency:
the unlocked form check passes, then ``redeem_points`` re-checks under ``FOR UPDATE`` and refuses so
the balance never goes negative.

Requests run through a ``get_db`` override that mirrors production (commit on success, rollback on
raise) — the conftest override does neither, which would make "a refusal persisted nothing"
untestable. Committed truth is read through a second sessionmaker (READ COMMITTED).
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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
from src.orders.models import Order
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


# --------------------------------------------------------------------------- #
# partial redemption: points debited now, order UNPAID, card owes the remainder
# --------------------------------------------------------------------------- #


async def test_partial_redeem_debits_points_and_leaves_order_unpaid(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    """Points spent now, order ``unpaid``/``pending``, exactly one debit — no notice yet (unpaid)."""
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

    redeem_rows = await _committed_redeem_rows(maker)
    assert len(redeem_rows) == 1
    assert redeem_rows[0].amount == -60_000
    assert await _committed_balance(maker, user_id) == 100_000 - 60_000
    # An unpaid order is NOT announced as paid at creation — that would lie to the fulfilment group.
    assert sent_messages == []


async def test_remainder_exactly_the_floor_is_accepted(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """Leaving the card exactly MIN_CARD_REMAINDER (5 000) is allowed — the tight edge of the band."""
    user_id = await _seed_user(maker, telegram_id=80021)
    product_id = await _seed_product(maker, slug="redeem-floor-edge")
    await _seed_points(maker, user_id=user_id, amount=100_000)

    resp = await client.post(
        ORDERS_URL, json=_payload(product_id, points_to_apply=64_000), headers=_headers(80021)
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["payment_status"] == "unpaid"
    assert await _committed_balance(maker, user_id) == 100_000 - 64_000


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


async def test_race_two_partial_orders_never_go_negative(
    test_db: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent partial orders whose debits together exceed the balance → one wins, one 409.

    Both pass the unlocked form check (each sees balance 60 000, each asks to spend 60 000), then
    serialize on the ``customer_profile`` FOR UPDATE lock inside ``redeem_points``: the loser
    re-reads the committed post-debit balance (0) and is refused, balance never negative. A barrier
    inside ``_price_lines`` forces the real overlap (``asyncio.gather`` alone does not interleave on
    localhost). Mutation: remove ``.with_for_update()`` from ``redeem_points`` → both debit → red
    with a negative balance.
    """
    user_id = await _seed_user(maker, telegram_id=80040)
    product_id = await _seed_product(maker, slug="redeem-race")
    await _seed_points(maker, user_id=user_id, amount=60_000)  # enough for exactly one debit

    real_price_lines = orders_service._price_lines
    barrier = asyncio.Barrier(2)

    async def _barriered(
        db: AsyncSession, lines: list[OrderItemCreate]
    ) -> tuple[list[object], Decimal]:
        result = await real_price_lines(db, lines)
        await asyncio.wait_for(barrier.wait(), timeout=10)  # fail loudly instead of hanging CI
        return result

    monkeypatch.setattr(orders_service, "_price_lines", _barriered)

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
    assert len(winners) == 1, f"exactly one order must win, got {results!r}"
    assert len(refusals) == 1, f"the loser must be refused, got {results!r}"
    assert refusals[0].balance == 0  # the loser saw the post-debit committed balance

    assert await _committed_order_count(maker) == 1
    redeem_rows = await _committed_redeem_rows(maker)
    assert len(redeem_rows) == 1
    assert redeem_rows[0].order_id == winners[0]
    assert redeem_rows[0].amount == -60_000
    assert await _committed_balance(maker, user_id) == 0  # never negative


async def test_same_order_cannot_be_debited_twice_unique_backstop(
    test_db: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    """The partial ``UNIQUE(order_id, reason)`` makes a second redeem row physically impossible.

    ``POST /orders`` creates a fresh order per call, so this cannot be reached over HTTP — it is the
    schema-level backstop should any future caller replay a debit for the same order.
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

    async with maker() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                await loyalty_service.redeem_points(
                    session, user_id=user_id, order_id=order_id, amount=1_000
                )

    # The backstop held: still exactly one debit, balance reflects one order only.
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
    still-open request transaction it would hold ``redeem_points``' ``FOR UPDATE`` lock on this
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
