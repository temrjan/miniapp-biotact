"""Tests for paying an order with Biotact Club points (Stage 6, PR-6.3).

The model under test is all-or-nothing (Captain, 2026-07-13): either the club balance covers
the WHOLE total — the order is created ``paid``/``confirmed`` and the ledger gets one
``redeem`` row — or nothing at all is written (409 with the exact deficit). Multicard never
participates; a points order leaves no trace outside this database, so what these tests pin
IS the accounting record.

Unlike the sibling suites, requests here run through a ``get_db`` override that mirrors the
production dependency exactly (commit on success, rollback on raise) — the conftest override
neither commits nor rolls back, which would make "nothing was persisted" untestable. Committed
truth is asserted through a second sessionmaker on the same test DB (READ COMMITTED), the same
approach the loyalty suites use for seeding.
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
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
from src.products.models import Product
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
BOT_TOKEN = "test-bot-token-123"

ORDERS_URL = "/api/v1/orders"
PAYMENTS_URL = "/api/v1/payments/create"

CATALOGUE_PRICE = Decimal("69000.00")
POINTS_FOR_ONE = 69_000  # the same order total as an integer point amount (1 балл = 1 сум)


def _headers(telegram_id: int, first_name: str = "Member") -> dict[str, str]:
    """Valid Telegram initData headers for ``telegram_id`` (the mini-app's only auth)."""
    return {
        "X-Init-Data": _make_init_data(BOT_TOKEN, {"id": telegram_id, "first_name": first_name})
    }


def _points_payload(product_id: int, quantity: int = 1) -> dict[str, object]:
    """A points-checkout request body for one product line."""
    return {
        "items": [{"product_id": product_id, "quantity": quantity}],
        "payment_method": "points",
        "delivery_phone": "+998900000001",
        "delivery_name": "Points Buyer",
        "delivery_address": "Tashkent",
    }


@pytest.fixture(autouse=True)
def _pin_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """initData is HMAC-signed with the bot token — pin it so ``_make_init_data`` validates."""
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)


@pytest.fixture(autouse=True)
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture every Telegram send instead of hitting the network; return the captured texts.

    Autouse: a points order fires the admin notification inside ``POST /orders``, and no test
    here may depend on api.telegram.org being reachable (or on dev .env credentials).
    """
    captured: list[str] = []

    async def _capture(text: str, parse_mode: str = "HTML") -> bool:
        captured.append(text)
        return True

    monkeypatch.setattr(telegram_notifier, "send_message", _capture)
    return captured


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """A sessionmaker on the same test DB — committed seeding + committed-truth asserts.

    Depends on ``test_db`` so the schema exists before the first seed and is dropped after.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client whose DB dependency behaves EXACTLY like production ``get_db``.

    Commit on success / rollback on raise is the property under test on the money path: a 409
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
    price: Decimal = CATALOGUE_PRICE,
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
# refusals: all-or-nothing means REFUSED writes exactly nothing
# --------------------------------------------------------------------------- #


async def test_insufficient_balance_returns_409_with_deficit_and_writes_nothing(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """Balance 100, order 69 000 → 409 carrying the exact shortfall; DB untouched."""
    user_id = await _seed_user(maker, telegram_id=80001)
    product_id = await _seed_product(maker, slug="redeem-short")
    await _seed_points(maker, user_id=user_id, amount=100)

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80001))

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "insufficient_points"
    assert detail["balance"] == 100
    assert detail["required"] == POINTS_FOR_ONE
    assert detail["deficit"] == POINTS_FOR_ONE - 100
    # Nothing persisted: no order, no redeem row, balance untouched.
    assert await _committed_order_count(maker) == 0
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, user_id) == 100


async def test_non_member_cannot_pay_with_points(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """A user who never joined the club (no profile row) is refused on balance 0."""
    product_id = await _seed_product(maker, slug="redeem-nonmember")

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80002))

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["balance"] == 0
    assert detail["deficit"] == POINTS_FOR_ONE
    assert await _committed_order_count(maker) == 0


async def test_fractional_total_is_refused_not_rounded(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """A non-whole total (price seeded past the admin ``gt=0``-whole validation) → 422, DB clean.

    This DB is the only accounting record of a points order — silently rounding here would
    fabricate money. The refusal mirrors ``get_unit_price``'s FractionalPriceError on earn.
    """
    user_id = await _seed_user(maker, telegram_id=80003)
    product_id = await _seed_product(maker, slug="redeem-fractional", price=Decimal("1000.50"))
    await _seed_points(maker, user_id=user_id, amount=1_000_000)

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80003))

    assert resp.status_code == 422, resp.text
    assert await _committed_order_count(maker) == 0
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, user_id) == 1_000_000


async def test_zero_total_is_refused_for_points(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """A zero-priced product (out-of-band data) cannot become a free order stamped "paid".

    The card path is caught by the Multicard invoice guard (`total <= 0` → 400); the points
    path has no invoice, so the same class of bad catalogue data must be refused here.
    """
    user_id = await _seed_user(maker, telegram_id=80004)
    product_id = await _seed_product(maker, slug="redeem-zero", price=Decimal("0.00"))
    await _seed_points(maker, user_id=user_id, amount=1_000)

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80004))

    assert resp.status_code == 422, resp.text
    assert await _committed_order_count(maker) == 0


# --------------------------------------------------------------------------- #
# success: paid + confirmed + exactly one redeem row carrying the order id
# --------------------------------------------------------------------------- #


async def test_points_payment_marks_order_paid_and_writes_redeem_row(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """The happy path end to end: order fields, ledger row, remaining balance."""
    user_id = await _seed_user(maker, telegram_id=80010)
    product_id = await _seed_product(maker, slug="redeem-happy")
    await _seed_points(maker, user_id=user_id, amount=100_000)

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80010))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["points_applied"] == POINTS_FOR_ONE
    assert body["payment_status"] == "paid"
    assert body["status"] == "confirmed"
    assert body["paid_at"] is not None
    assert body["payment_uuid"] is None  # Multicard never touched this order
    assert Decimal(body["total"]) == CATALOGUE_PRICE

    redeem_rows = await _committed_redeem_rows(maker)
    assert len(redeem_rows) == 1
    row = redeem_rows[0]
    assert row.user_id == user_id
    assert row.order_id == body["id"]
    assert row.amount == -POINTS_FOR_ONE
    assert await _committed_balance(maker, user_id) == 100_000 - POINTS_FOR_ONE


async def test_exact_balance_pays_order_and_zeroes_balance(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """``balance == total`` is sufficient (the check is ``<``, not ``<=``) → balance lands on 0."""
    user_id = await _seed_user(maker, telegram_id=80011)
    product_id = await _seed_product(maker, slug="redeem-exact")
    await _seed_points(maker, user_id=user_id, amount=POINTS_FOR_ONE)

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80011))

    assert resp.status_code == 201, resp.text
    assert resp.json()["points_applied"] == POINTS_FOR_ONE
    assert await _committed_balance(maker, user_id) == 0


# --------------------------------------------------------------------------- #
# the card path is unchanged — byte for byte
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method_field",
    [{}, {"payment_method": "card"}],
    ids=["absent (old clients)", "explicit card"],
)
async def test_card_path_is_unchanged(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    sent_messages: list[str],
    method_field: dict[str, str],
) -> None:
    """Default and explicit ``card`` behave exactly as before — even for a user WITH points."""
    user_id = await _seed_user(maker, telegram_id=80020)
    product_id = await _seed_product(maker, slug="card-regress")
    await _seed_points(maker, user_id=user_id, amount=1_000_000)

    payload: dict[str, object] = {"items": [{"product_id": product_id, "quantity": 1}]}
    payload.update(method_field)
    resp = await client.post(ORDERS_URL, json=payload, headers=_headers(80020))

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


async def test_unknown_payment_method_is_rejected(client: AsyncClient) -> None:
    """Anything but ``card``/``points`` fails schema validation → 422."""
    resp = await client.post(
        ORDERS_URL,
        json={"items": [{"product_id": 1, "quantity": 1}], "payment_method": "cash"},
        headers=_headers(80021),
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Multicard and the points order never meet
# --------------------------------------------------------------------------- #


async def test_points_paid_order_cannot_be_invoiced_via_multicard(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    """`POST /payments/create` on a points-paid order → 400 (already paid), before any gateway IO."""
    user_id = await _seed_user(maker, telegram_id=80030)
    product_id = await _seed_product(maker, slug="redeem-noinvoice")
    await _seed_points(maker, user_id=user_id, amount=POINTS_FOR_ONE)

    created = await client.post(
        ORDERS_URL, json=_points_payload(product_id), headers=_headers(80030)
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    resp = await client.post(
        PAYMENTS_URL,
        json={"order_id": order_id, "return_url": "https://x.uz"},
        headers=_headers(80030),
    )

    assert resp.status_code == 400
    assert "already paid" in resp.text.lower()


# --------------------------------------------------------------------------- #
# the admin request goes out, marked «оплачено баллами»
# --------------------------------------------------------------------------- #


async def test_notification_is_marked_paid_with_points(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    """The fulfilment group gets the usual paid-order request with the points mark + debit."""
    user_id = await _seed_user(maker, telegram_id=80040)
    product_id = await _seed_product(maker, slug="redeem-notify")
    await _seed_points(maker, user_id=user_id, amount=POINTS_FOR_ONE)

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80040))

    assert resp.status_code == 201, resp.text
    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert "ОПЛАЧЕНО БАЛЛАМИ" in message
    assert "Списано баллов" in message
    assert resp.json()["order_number"] in message
    assert "Points Buyer" in message  # the fulfilment data is the same one card orders carry


async def test_notification_failure_does_not_fail_the_order(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telegram being down must not lose a paid order — the debit already happened."""
    user_id = await _seed_user(maker, telegram_id=80041)
    product_id = await _seed_product(maker, slug="redeem-notify-down")
    await _seed_points(maker, user_id=user_id, amount=POINTS_FOR_ONE)

    async def _boom(text: str, parse_mode: str = "HTML") -> bool:
        raise RuntimeError("telegram down")

    monkeypatch.setattr(telegram_notifier, "send_message", _boom)

    resp = await client.post(ORDERS_URL, json=_points_payload(product_id), headers=_headers(80041))

    assert resp.status_code == 201, resp.text
    assert len(await _committed_redeem_rows(maker)) == 1
    assert await _committed_balance(maker, user_id) == 0


# --------------------------------------------------------------------------- #
# race + double-debit backstop (the two ways a balance could go negative)
# --------------------------------------------------------------------------- #


async def test_race_one_balance_two_orders_exactly_one_wins(
    test_db: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent points orders, balance for one → one succeeds, one 409, balance ends 0.

    Both transactions are FORCED to overlap: a barrier inside ``_price_lines`` (the first DB
    work of ``create_order``, before the profile lock) releases them only once both are inside
    open transactions — ``asyncio.gather`` alone does not reliably overlap on localhost (the
    same technique as the activation race tests). Mutation check: removing
    ``.with_for_update()`` from ``redeem_points`` turns this red with a negative balance.
    """
    user_id = await _seed_user(maker, telegram_id=80050)
    product_id = await _seed_product(maker, slug="redeem-race")
    await _seed_points(maker, user_id=user_id, amount=POINTS_FOR_ONE)  # enough for exactly one

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
        payment_method="points",
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
    assert await _committed_balance(maker, user_id) == 0  # never negative


async def test_same_order_cannot_be_debited_twice_unique_backstop(
    test_db: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    """The partial ``UNIQUE(order_id, reason)`` makes a second redeem row physically impossible.

    ``POST /orders`` creates a fresh order per call, so this cannot be reached over HTTP —
    it is the schema-level backstop should any future caller replay a debit for the same order.
    """
    user_id = await _seed_user(maker, telegram_id=80060)
    product_id = await _seed_product(maker, slug="redeem-unique")
    await _seed_points(maker, user_id=user_id, amount=200_000)

    async with maker() as session, session.begin():
        order = await orders_service.create_order(
            session,
            OrderCreate(
                items=[OrderItemCreate(product_id=product_id, quantity=1)],
                payment_method="points",
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
    assert await _committed_balance(maker, user_id) == 200_000 - POINTS_FOR_ONE
