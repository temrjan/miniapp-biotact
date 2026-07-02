"""Tests for QR activation (Stage 2, PR-2.3b): cashback, welcome, idempotency, races.

Activation owns one explicit transaction, so seeding and activation cannot share the single
``test_db`` session (its transaction would already be open). We seed/assert through a second
sessionmaker bound to the same test database, and run real concurrent activations on separate
connections via ``asyncio.gather`` (genuine Postgres row-lock contention).
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from decimal import Decimal
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.schemas import TelegramUser
from src.common.exceptions import ConflictException, NotFoundException
from src.config import settings
from src.loyalty.codes import generate_batch
from src.loyalty.models import (
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
    LedgerReason,
    LoyaltyStatus,
    PointLedger,
    QrCode,
    QrCodeStatus,
)
from src.loyalty.pricing import UnknownProductError
from src.loyalty.schemas import ActivationStatus
from src.loyalty.service import ActivationOutcome, activate_code, earn_row_reconciles
from src.products.models import Product
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` created — for seeding + own-txn activations."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_product_and_codes(
    sm: async_sessionmaker[AsyncSession], *, price: str = "10000", n: int = 1
) -> tuple[int, list[str]]:
    """Commit a priced product + ``n`` generated QR codes; return (product_id, [code, …])."""
    async with sm() as s, s.begin():
        product = Product(name="Test BAD", slug=f"bad-{secrets.token_hex(4)}", price=Decimal(price))
        s.add(product)
        await s.flush()
        _, qrs = await generate_batch(s, product.id, n)
        return product.id, [q.code for q in qrs]


async def _seed_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a user (so concurrent activations don't race on the lazy user INSERT); return id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Seed")
        s.add(user)
        await s.flush()
        return user.id


async def _set_price(sm: async_sessionmaker[AsyncSession], product_id: int, price: str) -> None:
    """Mutate a product's price after the fact (e.g. an admin edit post-QR-print)."""
    async with sm() as s, s.begin():
        await s.execute(
            update(Product).where(Product.id == product_id).values(price=Decimal(price))
        )


async def _seed_user_with_profile(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    lifetime: int,
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
) -> int:
    """Commit a user + a profile pre-set to ``lifetime``/``status`` (target the flip boundary)."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Seed")
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id, lifetime_activations=lifetime, status=status))
        return user.id


def _make_init_data(bot_token: str, user: dict[str, object]) -> str:
    """Build a validly-signed Telegram initData string (mirrors validate_init_data's HMAC)."""
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHtest",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# --------------------------------------------------------------------------- #
# service: happy path, welcome-once, replay, conflict, not-found, reconciliation
# --------------------------------------------------------------------------- #


async def test_first_activation_grants_cashback_and_welcome(test_db: AsyncSession, maker) -> None:
    """First activation = 5% cashback + 2.5% welcome (7.5% total), counter = 1."""
    pid, (code,) = await _seed_product_and_codes(maker)
    async with maker() as s:
        outcome = await activate_code(
            s, telegram_user=TelegramUser(id=60001, first_name="Buyer"), code=code
        )
    assert outcome.status is ActivationStatus.ACTIVATED
    assert outcome.points_awarded == 750
    assert outcome.welcome_bonus == 250
    assert outcome.balance == 750
    assert outcome.lifetime_activations == 1

    async with maker() as s:
        rows = (await s.execute(select(PointLedger))).scalars().all()
        assert {r.reason for r in rows} == {"cashback", "welcome"}
        assert all(earn_row_reconciles(r) for r in rows)  # amount == compute_cashback snapshot
        qr = (await s.execute(select(QrCode).where(QrCode.code == code))).scalar_one()
        assert qr.status == QrCodeStatus.USED.value
        assert qr.activated_by is not None
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.lifetime_activations == 1
        assert profile.first_activated_product_id == pid


async def test_second_activation_has_no_welcome(test_db: AsyncSession, maker) -> None:
    """Welcome is once-per-user: the second activation accrues cashback only."""
    _, codes = await _seed_product_and_codes(maker, n=2)
    tg = TelegramUser(id=60002, first_name="Repeat")
    async with maker() as s:
        first = await activate_code(s, telegram_user=tg, code=codes[0])
    async with maker() as s:
        second = await activate_code(s, telegram_user=tg, code=codes[1])
    assert first.welcome_bonus == 250
    assert second.status is ActivationStatus.ACTIVATED
    assert second.welcome_bonus == 0
    assert second.points_awarded == 500
    assert second.lifetime_activations == 2
    assert second.balance == 1250  # 750 + 500


async def test_same_user_replay_is_idempotent(test_db: AsyncSession, maker) -> None:
    """Re-scanning an own code returns the original earn, grants nothing new, adds no rows."""
    _, (code,) = await _seed_product_and_codes(maker)
    tg = TelegramUser(id=60003, first_name="Replay")
    async with maker() as s:
        await activate_code(s, telegram_user=tg, code=code)
    async with maker() as s:
        replay = await activate_code(s, telegram_user=tg, code=code)
    assert replay.status is ActivationStatus.ALREADY_ACTIVATED
    assert replay.points_awarded == 750
    assert replay.welcome_bonus == 0
    assert replay.balance == 750
    assert replay.lifetime_activations == 1
    async with maker() as s:
        count = (await s.execute(select(func.count()).select_from(PointLedger))).scalar_one()
        assert count == 2  # no new ledger rows on replay


async def test_other_user_code_conflicts(test_db: AsyncSession, maker) -> None:
    """A code already activated by someone else → 409, generic, no extra accrual."""
    _, (code,) = await _seed_product_and_codes(maker)
    async with maker() as s:
        await activate_code(s, telegram_user=TelegramUser(id=60004, first_name="Owner"), code=code)
    async with maker() as s:
        with pytest.raises(ConflictException):
            await activate_code(
                s, telegram_user=TelegramUser(id=60005, first_name="Thief"), code=code
            )
    async with maker() as s:
        count = (await s.execute(select(func.count()).select_from(PointLedger))).scalar_one()
        assert count == 2  # only the owner's rows


async def test_unknown_code_not_found(test_db: AsyncSession, maker) -> None:
    """An unknown code → 404."""
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await activate_code(
                s, telegram_user=TelegramUser(id=60006, first_name="Ghost"), code="NOPENOPE0000"
            )


async def test_pricing_failure_rolls_back_claim(test_db: AsyncSession, maker, monkeypatch) -> None:
    """MINOR-2b: if a claimed code's product can't be priced, the claim rolls back (0 accrual)."""
    _, (code,) = await _seed_product_and_codes(maker)
    from src.loyalty import service as svc

    async def _boom(session: AsyncSession, product_id: int) -> int:
        raise UnknownProductError(product_id)

    monkeypatch.setattr(svc, "get_unit_price", _boom)
    async with maker() as s:
        with pytest.raises(UnknownProductError):
            await activate_code(
                s, telegram_user=TelegramUser(id=60007, first_name="Unpriced"), code=code
            )
    async with maker() as s:
        qr = (await s.execute(select(QrCode).where(QrCode.code == code))).scalar_one()
        assert qr.status == QrCodeStatus.GENERATED.value  # claim rolled back
        assert qr.activated_by is None
        count = (await s.execute(select(func.count()).select_from(PointLedger))).scalar_one()
        assert count == 0  # nothing accrued


# --------------------------------------------------------------------------- #
# races — real concurrent sessions on Postgres
# --------------------------------------------------------------------------- #


async def test_race_same_code_one_set_of_accruals(test_db: AsyncSession, maker) -> None:
    """Two concurrent scans of one code by one user → exactly one accrual set; the other replays."""
    _, (code,) = await _seed_product_and_codes(maker)
    await _seed_user(maker, 60010)  # pre-exist so get_or_create doesn't race on user INSERT
    tg = TelegramUser(id=60010, first_name="Racer")
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            activate_code(s1, telegram_user=tg, code=code),
            activate_code(s2, telegram_user=tg, code=code),
            return_exceptions=True,
        )
    assert all(isinstance(r, ActivationOutcome) for r in results), results
    assert {r.status for r in results} == {
        ActivationStatus.ACTIVATED,
        ActivationStatus.ALREADY_ACTIVATED,
    }
    async with maker() as s:
        rows = (await s.execute(select(PointLedger))).scalars().all()
        assert {r.reason for r in rows} == {"cashback", "welcome"}
        assert len(rows) == 2
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.lifetime_activations == 1


async def test_race_first_activation_grants_exactly_one_welcome(
    test_db: AsyncSession, maker
) -> None:
    """Two concurrent first-activations (different codes, one new user) → exactly one welcome."""
    _, codes = await _seed_product_and_codes(maker, n=2)
    await _seed_user(maker, 60011)  # user exists; profile does NOT (lazy-created under the race)
    tg = TelegramUser(id=60011, first_name="DoubleScan")
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            activate_code(s1, telegram_user=tg, code=codes[0]),
            activate_code(s2, telegram_user=tg, code=codes[1]),
            return_exceptions=True,
        )
    assert all(isinstance(r, ActivationOutcome) for r in results), results
    async with maker() as s:
        rows = (await s.execute(select(PointLedger))).scalars().all()
        assert len([r for r in rows if r.reason == "welcome"]) == 1
        assert len([r for r in rows if r.reason == "cashback"]) == 2
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.lifetime_activations == 2


# --------------------------------------------------------------------------- #
# HTTP endpoint — dark gate, auth, end-to-end happy path
# --------------------------------------------------------------------------- #


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled is off by default → 404 fires before auth (dark, not probeable)."""
    resp = await client.post("/api/v1/qr/activate", json={"code": "ANYTHING0000"})
    assert resp.status_code == 404


async def test_endpoint_requires_init_data(client, monkeypatch) -> None:
    """Flag on but no X-Init-Data → 401."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.post("/api/v1/qr/activate", json={"code": "ANYTHING0000"})
    assert resp.status_code == 401


async def test_endpoint_happy_e2e(client, maker, monkeypatch) -> None:
    """Full path: signed initData + flag on → 200 with cashback + welcome."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    _, (code,) = await _seed_product_and_codes(maker)
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 60020, "first_name": "E2E"})
    }
    resp = await client.post("/api/v1/qr/activate", json={"code": code}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "activated"
    assert body["points_awarded"] == 750
    assert body["welcome_bonus"] == 250
    assert body["balance"] == 750
    assert body["lifetime_activations"] == 1


async def test_welcome_rounding_is_half_up_per_row(test_db: AsyncSession, maker) -> None:
    """Each earn row rounds independently ROUND_HALF_UP — NOT one ×1.5 multiplier.

    Price 20 → cashback = compute_cashback(20, 500) = 1.0 → 1; welcome = compute_cashback(20, 250)
    = 0.5 → 1 (half rounds up, in the customer's favour). Total 2 across two reconcilable rows.
    """
    _, (code,) = await _seed_product_and_codes(maker, price="20")
    async with maker() as s:
        outcome = await activate_code(
            s, telegram_user=TelegramUser(id=60031, first_name="Boundary"), code=code
        )
    assert outcome.welcome_bonus == 1
    assert outcome.points_awarded == 2
    async with maker() as s:
        rows = (await s.execute(select(PointLedger))).scalars().all()
        assert {r.amount for r in rows} == {1}  # two rows, each rounded once to 1
        assert all(earn_row_reconciles(r) for r in rows)


async def test_fractional_price_at_activation_returns_503_not_500(
    client, maker, monkeypatch
) -> None:
    """A price made fractional AFTER generation → 503 (never 500), claim rolled back, 0 accrual."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    pid, (code,) = await _seed_product_and_codes(maker)  # whole price; qr generated
    await _set_price(
        maker, pid, "100.50"
    )  # admin edits price to fractional after the box is printed
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 60040, "first_name": "Frac"})
    }
    resp = await client.post("/api/v1/qr/activate", json={"code": code}, headers=headers)
    assert resp.status_code == 503  # graceful, retryable — not a 500
    assert "internal server error" not in resp.text.lower()
    async with maker() as s:
        qr = (await s.execute(select(QrCode).where(QrCode.code == code))).scalar_one()
        assert qr.status == QrCodeStatus.GENERATED.value  # claim rolled back atomically
        count = (await s.execute(select(func.count()).select_from(PointLedger))).scalar_one()
        assert count == 0  # no partial credit


async def test_body_cannot_override_rate_or_amount(client, maker, monkeypatch) -> None:
    """The client controls only `code`; forged rate/price/amount body fields have no effect."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    _, (code,) = await _seed_product_and_codes(maker)  # price 10_000 → 750 server-computed
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 60041, "first_name": "Tamper"})
    }
    resp = await client.post(
        "/api/v1/qr/activate",
        json={"code": code, "rate_bps": 9999, "unit_price": 999_999, "amount": 999_999},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["points_awarded"] == 750  # forged money fields ignored


async def test_conflict_http_body_is_generic(client, maker, monkeypatch) -> None:
    """Other-user activation → 409 with a generic body; never leaks the owner's identity."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    _, (code,) = await _seed_product_and_codes(maker)
    first = await client.post(
        "/api/v1/qr/activate",
        json={"code": code},
        headers={
            "X-Init-Data": _make_init_data(
                "test-bot-token-123", {"id": 60042, "first_name": "Owner"}
            )
        },
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/qr/activate",
        json={"code": code},
        headers={
            "X-Init-Data": _make_init_data(
                "test-bot-token-123", {"id": 60043, "first_name": "Thief"}
            )
        },
    )
    assert second.status_code == 409
    assert "60042" not in second.text and "Owner" not in second.text  # no owner identity leak
    assert "already activated" in second.text.lower()


# --------------------------------------------------------------------------- #
# Stage 3 — "Друг" status flip: rate 5%→8%, +5000 bonus, milestone event
# --------------------------------------------------------------------------- #


async def test_tenth_activation_flips_to_friend(test_db: AsyncSession, maker) -> None:
    """The 10th activation flips newbie→friend, earns at 8% (flip-then-accrue), grants +5000.

    MINOR-1 invariant: points_awarded == cashback + friend_bonus and balance == points_awarded.
    """
    _, (code,) = await _seed_product_and_codes(maker, price="10000")
    await _seed_user_with_profile(maker, 60100, lifetime=9)  # next activation is the 10th
    async with maker() as s:
        outcome = await activate_code(
            s, telegram_user=TelegramUser(id=60100, first_name="Friend"), code=code
        )
    assert outcome.tier is LoyaltyStatus.BIOTACT_FRIEND
    assert outcome.friend_bonus == 5000
    assert outcome.welcome_bonus == 0
    assert outcome.points_awarded == 5800  # 800 cashback (8%) + 5000 bonus
    assert outcome.balance == 5800  # == points_awarded (started at 0)
    assert outcome.lifetime_activations == 10

    async with maker() as s:
        rows = (await s.execute(select(PointLedger))).scalars().all()
        assert {r.reason for r in rows} == {"cashback", "friend_bonus"}
        assert all(earn_row_reconciles(r) for r in rows)  # friend_bonus skipped (NULL snapshot)
        cashback = next(r for r in rows if r.reason == LedgerReason.CASHBACK)
        assert cashback.rate_bps == 800
        assert cashback.amount == 800
        bonus = next(r for r in rows if r.reason == LedgerReason.FRIEND_BONUS)
        assert bonus.amount == 5000
        assert bonus.unit_price is None  # flat bonus, not cashback — no price snapshot
        assert bonus.rate_bps is None
        assert bonus.qr_code_id is not None  # tied to the flipping code (partial-UNIQUE backstop)
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.status is LoyaltyStatus.BIOTACT_FRIEND
        assert profile.lifetime_activations == 10
        event = (
            await s.execute(
                select(CustomerEvent).where(
                    CustomerEvent.event_type == CustomerEventType.STATUS_FRIEND
                )
            )
        ).scalar_one()
        assert event.payload == {"lifetime_activations": 10, "bonus": 5000}


async def test_friend_activation_earns_8pct_without_repeat_bonus(
    test_db: AsyncSession, maker
) -> None:
    """An existing friend earns 8% on every activation, with no second bonus or event."""
    _, (code,) = await _seed_product_and_codes(maker, price="10000")
    await _seed_user_with_profile(maker, 60101, lifetime=15, status=LoyaltyStatus.BIOTACT_FRIEND)
    async with maker() as s:
        outcome = await activate_code(
            s, telegram_user=TelegramUser(id=60101, first_name="OldFriend"), code=code
        )
    assert outcome.tier is LoyaltyStatus.BIOTACT_FRIEND
    assert outcome.friend_bonus == 0
    assert outcome.points_awarded == 800  # 8% only
    assert outcome.lifetime_activations == 16
    async with maker() as s:
        rows = (await s.execute(select(PointLedger))).scalars().all()
        assert {r.reason for r in rows} == {"cashback"}
        assert rows[0].rate_bps == 800
        flip_events = (
            await s.execute(
                select(func.count())
                .select_from(CustomerEvent)
                .where(CustomerEvent.event_type == CustomerEventType.STATUS_FRIEND)
            )
        ).scalar_one()
        assert flip_events == 0  # already a friend → no flip event


async def test_below_threshold_stays_newbie_at_5pct(test_db: AsyncSession, maker) -> None:
    """The 9th activation stays newbie: 5% cashback, no flip, no bonus."""
    _, (code,) = await _seed_product_and_codes(maker, price="10000")
    await _seed_user_with_profile(maker, 60102, lifetime=8)  # next activation is the 9th
    async with maker() as s:
        outcome = await activate_code(
            s, telegram_user=TelegramUser(id=60102, first_name="Almost"), code=code
        )
    assert outcome.tier is LoyaltyStatus.NEWBIE
    assert outcome.friend_bonus == 0
    assert outcome.points_awarded == 500  # 5%
    assert outcome.lifetime_activations == 9
    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.status is LoyaltyStatus.NEWBIE


async def test_threshold_is_idempotent_flips_when_already_past_ten(
    test_db: AsyncSession, maker
) -> None:
    """A newbie whose counter is already ≥10 (backfill/re-ingest) flips on the next activation."""
    _, (code,) = await _seed_product_and_codes(maker, price="10000")
    await _seed_user_with_profile(maker, 60103, lifetime=12, status=LoyaltyStatus.NEWBIE)
    async with maker() as s:
        outcome = await activate_code(
            s, telegram_user=TelegramUser(id=60103, first_name="Backfill"), code=code
        )
    assert outcome.tier is LoyaltyStatus.BIOTACT_FRIEND
    assert outcome.friend_bonus == 5000
    assert outcome.lifetime_activations == 13


async def test_replay_of_flipping_code_grants_no_second_bonus(test_db: AsyncSession, maker) -> None:
    """Re-scanning the flipping code returns the original earn (incl. bonus), grants nothing new."""
    _, (code,) = await _seed_product_and_codes(maker, price="10000")
    await _seed_user_with_profile(maker, 60104, lifetime=9)
    tg = TelegramUser(id=60104, first_name="Replay10")
    async with maker() as s:
        first = await activate_code(s, telegram_user=tg, code=code)
    async with maker() as s:
        replay = await activate_code(s, telegram_user=tg, code=code)
    assert first.friend_bonus == 5000
    assert replay.status is ActivationStatus.ALREADY_ACTIVATED
    assert replay.friend_bonus == 0
    assert replay.tier is LoyaltyStatus.BIOTACT_FRIEND  # status persists across the replay
    assert replay.points_awarded == 5800  # echoes the code's original earn (cashback + bonus)
    assert replay.lifetime_activations == 10  # not re-incremented
    async with maker() as s:
        bonus_count = (
            await s.execute(
                select(func.count())
                .select_from(PointLedger)
                .where(PointLedger.reason == LedgerReason.FRIEND_BONUS)
            )
        ).scalar_one()
        assert bonus_count == 1  # exactly one friend bonus, ever


async def test_race_at_boundary_flips_exactly_once(test_db: AsyncSession, maker) -> None:
    """Two concurrent activations across the 10th boundary → exactly one flip + one +5000 bonus.

    The per-user ``customer_profile`` FOR UPDATE lock serializes them: whoever wins flips to friend;
    the other re-reads ``friend`` under the lock and only accrues 8% — no second flip/bonus/event.
    """
    _, codes = await _seed_product_and_codes(maker, price="10000", n=2)
    await _seed_user_with_profile(maker, 60105, lifetime=9)
    tg = TelegramUser(id=60105, first_name="BoundaryRace")
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            activate_code(s1, telegram_user=tg, code=codes[0]),
            activate_code(s2, telegram_user=tg, code=codes[1]),
            return_exceptions=True,
        )
    assert all(isinstance(r, ActivationOutcome) for r in results), results
    async with maker() as s:
        bonus = (
            await s.execute(
                select(func.count())
                .select_from(PointLedger)
                .where(PointLedger.reason == LedgerReason.FRIEND_BONUS)
            )
        ).scalar_one()
        assert bonus == 1  # exactly one bonus despite the race
        flip_events = (
            await s.execute(
                select(func.count())
                .select_from(CustomerEvent)
                .where(CustomerEvent.event_type == CustomerEventType.STATUS_FRIEND)
            )
        ).scalar_one()
        assert flip_events == 1  # exactly one milestone event
        cashbacks = (
            (
                await s.execute(
                    select(PointLedger).where(PointLedger.reason == LedgerReason.CASHBACK)
                )
            )
            .scalars()
            .all()
        )
        assert len(cashbacks) == 2
        assert all(r.rate_bps == 800 for r in cashbacks)  # both at 8% (friend rate)
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.status is LoyaltyStatus.BIOTACT_FRIEND
        assert profile.lifetime_activations == 11


async def test_endpoint_returns_tier_and_friend_bonus(client, maker, monkeypatch) -> None:
    """The HTTP response exposes the new tier + friend_bonus fields on the flip."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    _, (code,) = await _seed_product_and_codes(maker, price="10000")
    await _seed_user_with_profile(maker, 60050, lifetime=9)
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 60050, "first_name": "Flip"})
    }
    resp = await client.post("/api/v1/qr/activate", json={"code": code}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "biotact_friend"
    assert body["friend_bonus"] == 5000
    assert body["points_awarded"] == 5800


# --------------------------------------------------------------------------- #
# Stage 4 (PR-4.2) — lifecycle events written inside the claim transaction
# --------------------------------------------------------------------------- #


async def _count_events(s: AsyncSession, event_type: CustomerEventType) -> int:
    return int(
        (
            await s.execute(
                select(func.count())
                .select_from(CustomerEvent)
                .where(CustomerEvent.event_type == event_type)
            )
        ).scalar_one()
    )


async def test_activation_writes_qr_activated_and_joined_events(
    test_db: AsyncSession, maker
) -> None:
    """A real claim by a new user writes exactly one qr_activated + one joined (null source)."""
    pid, (code,) = await _seed_product_and_codes(maker, price="10000")
    async with maker() as s:
        await activate_code(
            s, telegram_user=TelegramUser(id=70101, first_name="Claimer"), code=code
        )
    async with maker() as s:
        qr = (await s.execute(select(QrCode).where(QrCode.code == code))).scalar_one()
        qr_events = (
            (
                await s.execute(
                    select(CustomerEvent).where(
                        CustomerEvent.event_type == CustomerEventType.QR_ACTIVATED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(qr_events) == 1
        assert qr_events[0].payload == {
            "qr_code_id": qr.id,
            "product_id": pid,
            "points_awarded": 750,  # 500 cashback + 250 welcome (first activation)
        }
        joined = (
            (
                await s.execute(
                    select(CustomerEvent).where(
                        CustomerEvent.event_type == CustomerEventType.JOINED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(joined) == 1
        assert joined[0].payload == {"registration_source": None}  # activate has no source


async def test_replay_writes_no_new_qr_activated_or_joined(test_db: AsyncSession, maker) -> None:
    """An idempotent replay grants nothing new — no second qr_activated / joined event."""
    _, (code,) = await _seed_product_and_codes(maker)
    tg = TelegramUser(id=70102, first_name="ReplayEvents")
    async with maker() as s:
        await activate_code(s, telegram_user=tg, code=code)
    async with maker() as s:
        await activate_code(s, telegram_user=tg, code=code)  # replay
    async with maker() as s:
        assert await _count_events(s, CustomerEventType.QR_ACTIVATED) == 1
        assert await _count_events(s, CustomerEventType.JOINED) == 1


async def test_conflict_writes_no_events_for_other_user(test_db: AsyncSession, maker) -> None:
    """A code owned by another user rolls the whole txn back — the thief gets no profile/events."""
    _, (code,) = await _seed_product_and_codes(maker)
    async with maker() as s:
        await activate_code(s, telegram_user=TelegramUser(id=70103, first_name="Owner"), code=code)
    async with maker() as s:
        with pytest.raises(ConflictException):
            await activate_code(
                s, telegram_user=TelegramUser(id=70104, first_name="Thief"), code=code
            )
    async with maker() as s:
        # only the owner persists — the thief's user/profile INSERT rolled back with the claim
        assert (await s.execute(select(func.count()).select_from(User))).scalar_one() == 1
        assert (
            await s.execute(select(func.count()).select_from(CustomerProfile))
        ).scalar_one() == 1
        assert await _count_events(s, CustomerEventType.QR_ACTIVATED) == 1  # owner only
        assert await _count_events(s, CustomerEventType.JOINED) == 1  # owner only


async def test_unknown_code_writes_no_events(test_db: AsyncSession, maker) -> None:
    """An unknown code rolls back the lazily-created user/profile — no orphan events."""
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await activate_code(
                s, telegram_user=TelegramUser(id=70105, first_name="Ghost"), code="NOPENOPE0000"
            )
    async with maker() as s:
        assert (
            await s.execute(select(func.count()).select_from(CustomerProfile))
        ).scalar_one() == 0
        assert await _count_events(s, CustomerEventType.QR_ACTIVATED) == 0
        assert await _count_events(s, CustomerEventType.JOINED) == 0


async def test_race_activate_writes_exactly_one_qr_activated_on_claim(
    test_db: AsyncSession, maker
) -> None:
    """Two concurrent scans of one code → exactly one qr_activated (the claim), one joined.

    The loser replays (``_resolve_unclaimed`` grants nothing → no event), so the timeline records
    the single real claim, not the replay — proven under genuine Postgres row-lock contention.
    """
    _, (code,) = await _seed_product_and_codes(maker)
    await _seed_user(maker, 70110)  # pre-exist so get_or_create doesn't race on user INSERT
    tg = TelegramUser(id=70110, first_name="RaceEvents")
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            activate_code(s1, telegram_user=tg, code=code),
            activate_code(s2, telegram_user=tg, code=code),
            return_exceptions=True,
        )
    assert all(isinstance(r, ActivationOutcome) for r in results), results
    assert {r.status for r in results} == {
        ActivationStatus.ACTIVATED,
        ActivationStatus.ALREADY_ACTIVATED,
    }
    async with maker() as s:
        assert await _count_events(s, CustomerEventType.QR_ACTIVATED) == 1
        assert await _count_events(s, CustomerEventType.JOINED) == 1
