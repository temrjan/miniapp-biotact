"""Tests for consent + entry lifecycle events (Stage 4, PR-4.2): POST /loyalty/consent.

``record_consent`` owns one explicit transaction, so seeding and the call cannot share the single
``test_db`` session (its transaction would already be open). We seed/assert through a second
sessionmaker bound to the same test database, and run real concurrent consent calls on separate
connections via ``asyncio.gather`` (genuine Postgres row-lock contention on the ``customer_profile``
``FOR UPDATE`` lock).
"""

import asyncio
import secrets
from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import Select, Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.schemas import TelegramUser
from src.config import settings
from src.loyalty.codes import generate_batch
from src.loyalty.models import (
    Consent,
    ConsentPurpose,
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
)
from src.loyalty.schemas import RegistrationSource
from src.loyalty.service import ConsentOutcome, activate_code, record_consent
from src.products.models import Product
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` created — for seeding + own-txn consent calls."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a bare user (so concurrent consent calls don't race the lazy user INSERT); return id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Seed")
        s.add(user)
        await s.flush()
        return user.id


async def _seed_user_with_verified_phone(
    sm: async_sessionmaker[AsyncSession], telegram_id: int
) -> int:
    """Commit a user whose phone is verified (as the bot would leave it); return id.

    The value of ``phone_verified_at`` is never asserted — only ``IS NOT NULL`` matters to the
    ``phone_shared`` guard, so a fixed timestamp is not a flaky-time dependency.
    """
    async with sm() as s, s.begin():
        user = User(
            telegram_id=telegram_id,
            first_name="Seed",
            phone="+998901112233",
            phone_verified_at=datetime.now(UTC),
        )
        s.add(user)
        await s.flush()
        return user.id


async def _seed_product_and_code(sm: async_sessionmaker[AsyncSession]) -> str:
    """Commit a priced product + one generated QR code; return its code string."""
    async with sm() as s, s.begin():
        product = Product(
            name="Test BAD", slug=f"bad-{secrets.token_hex(4)}", price=Decimal("10000")
        )
        s.add(product)
        await s.flush()
        _, qrs = await generate_batch(s, product.id, 1)
        return qrs[0].code


async def _seed_verified_user_with_profile(
    sm: async_sessionmaker[AsyncSession], telegram_id: int
) -> int:
    """Commit a verified-phone user WITH an empty profile (no consent yet) — the activate-first state.

    Here two concurrent consent calls both find the profile already present, so the profile
    ``ON CONFLICT`` insert is a no-op for both and does NOT serialize them: only the ``FOR UPDATE``
    lock keeps the consent/phone_shared dedup race-safe.
    """
    async with sm() as s, s.begin():
        user = User(
            telegram_id=telegram_id,
            first_name="Seed",
            phone="+998901112233",
            phone_verified_at=datetime.now(UTC),
        )
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id))
        return user.id


async def _joined_events(s: AsyncSession) -> list[CustomerEvent]:
    return list(
        (
            await s.execute(
                select(CustomerEvent).where(CustomerEvent.event_type == CustomerEventType.JOINED)
            )
        )
        .scalars()
        .all()
    )


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


# --------------------------------------------------------------------------- #
# service: happy path, idempotency, phone_shared guard, source COALESCE
# --------------------------------------------------------------------------- #


async def test_consent_creates_profile_records_data_processing_and_joined(
    test_db: AsyncSession, maker
) -> None:
    """First consent (direct): writes a data_processing row, creates the profile, emits `joined`."""
    async with maker() as s:
        outcome = await record_consent(
            s,
            telegram_user=TelegramUser(id=70001, first_name="Newcomer"),
            source=RegistrationSource.DIRECT,
        )
    assert isinstance(outcome, ConsentOutcome)
    assert outcome.consent_recorded is True
    assert outcome.joined is True
    assert outcome.phone_shared is False  # no verified phone

    async with maker() as s:
        consent = (await s.execute(select(Consent))).scalar_one()
        assert consent.purpose is ConsentPurpose.DATA_PROCESSING
        assert consent.granted is True
        assert consent.version == "1.0"
        assert consent.source == "direct"
        assert consent.revoked_at is None
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.registration_source == "direct"
        joined = await _joined_events(s)
        assert len(joined) == 1
        assert joined[0].payload == {"registration_source": "direct"}


async def test_consent_is_idempotent_on_repeat(test_db: AsyncSession, maker) -> None:
    """A second consent by the same user writes nothing and returns all-False."""
    tg = TelegramUser(id=70002, first_name="Twice")
    async with maker() as s:
        first = await record_consent(s, telegram_user=tg, source=RegistrationSource.QR)
    async with maker() as s:
        second = await record_consent(s, telegram_user=tg, source=RegistrationSource.QR)
    assert (first.consent_recorded, first.joined) == (True, True)
    assert (second.consent_recorded, second.joined, second.phone_shared) == (False, False, False)
    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(Consent))).scalar_one() == 1
        assert (
            await s.execute(select(func.count()).select_from(CustomerProfile))
        ).scalar_one() == 1
        assert await _count_events(s, CustomerEventType.JOINED) == 1


async def test_consent_writes_phone_shared_once_when_phone_verified(
    test_db: AsyncSession, maker
) -> None:
    """A verified phone → `phone_shared` written once; a repeat call does not duplicate it."""
    await _seed_user_with_verified_phone(maker, 70003)
    tg = TelegramUser(id=70003, first_name="Verified")
    async with maker() as s:
        first = await record_consent(s, telegram_user=tg, source=RegistrationSource.QR)
    async with maker() as s:
        second = await record_consent(s, telegram_user=tg, source=RegistrationSource.QR)
    assert first.phone_shared is True
    assert second.phone_shared is False
    async with maker() as s:
        assert await _count_events(s, CustomerEventType.PHONE_SHARED) == 1
        event = (
            await s.execute(
                select(CustomerEvent).where(
                    CustomerEvent.event_type == CustomerEventType.PHONE_SHARED
                )
            )
        ).scalar_one()
        assert event.payload == {}


async def test_consent_no_phone_shared_when_phone_unverified(test_db: AsyncSession, maker) -> None:
    """Without a verified phone, no `phone_shared` event is written."""
    async with maker() as s:
        outcome = await record_consent(
            s,
            telegram_user=TelegramUser(id=70004, first_name="NoPhone"),
            source=RegistrationSource.DIRECT,
        )
    assert outcome.phone_shared is False
    async with maker() as s:
        assert await _count_events(s, CustomerEventType.PHONE_SHARED) == 0


async def test_registration_source_is_set_once(test_db: AsyncSession, maker) -> None:
    """`registration_source` is COALESCE-set once — the first source wins on a later call."""
    tg = TelegramUser(id=70005, first_name="Source")
    async with maker() as s:
        await record_consent(s, telegram_user=tg, source=RegistrationSource.QR)
    async with maker() as s:
        await record_consent(s, telegram_user=tg, source=RegistrationSource.DIRECT)
    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.registration_source == "qr"  # first source wins, not overwritten to direct


# --------------------------------------------------------------------------- #
# interaction with activate_code (the `joined` invariant holds on both paths)
# --------------------------------------------------------------------------- #


async def test_consent_after_activate_first_backfills_source_without_second_joined(
    test_db: AsyncSession, maker
) -> None:
    """activate-first emits `joined` (null source); a later consent backfills source, no 2nd `joined`."""
    code = await _seed_product_and_code(maker)
    tg = TelegramUser(id=70006, first_name="ActivateFirst")
    async with maker() as s:
        await activate_code(s, telegram_user=tg, code=code)  # creates profile + joined(null source)
    async with maker() as s:
        outcome = await record_consent(s, telegram_user=tg, source=RegistrationSource.QR)
    assert outcome.joined is False  # profile already exists → consent re-emits no joined
    assert outcome.consent_recorded is True
    async with maker() as s:
        joined = await _joined_events(s)
        assert len(joined) == 1  # exactly one joined, ever
        assert joined[0].payload == {"registration_source": None}  # from the activate path
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.registration_source == "qr"  # backfilled by consent


# --------------------------------------------------------------------------- #
# races — real concurrent sessions on Postgres (customer_profile FOR UPDATE lock)
# --------------------------------------------------------------------------- #


async def test_race_two_consent_calls_write_each_thing_once(test_db: AsyncSession, maker) -> None:
    """Two concurrent consents (verified phone) → exactly one profile/joined/consent/phone_shared."""
    await _seed_user_with_verified_phone(maker, 70010)  # user exists; profile does NOT
    tg = TelegramUser(id=70010, first_name="RaceConsent")
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            record_consent(s1, telegram_user=tg, source=RegistrationSource.QR),
            record_consent(s2, telegram_user=tg, source=RegistrationSource.QR),
            return_exceptions=True,
        )
    assert all(isinstance(r, ConsentOutcome) for r in results), results
    # each write is performed by exactly one of the two racers
    assert sum(r.joined for r in results) == 1
    assert sum(r.consent_recorded for r in results) == 1
    assert sum(r.phone_shared for r in results) == 1
    async with maker() as s:
        assert (
            await s.execute(select(func.count()).select_from(CustomerProfile))
        ).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(Consent))).scalar_one() == 1
        assert await _count_events(s, CustomerEventType.JOINED) == 1
        assert await _count_events(s, CustomerEventType.PHONE_SHARED) == 1


async def test_race_two_consent_on_existing_profile_dedup(test_db: AsyncSession, maker) -> None:
    """Two concurrent consents on a PRE-EXISTING profile → exactly one consent + one phone_shared.

    Happy-path dedup-contract check over plain ``asyncio.gather``: it does NOT deterministically
    force the two transactions to overlap (localhost Postgres may let one commit before the other
    reads), so on its own it is not a mutation-honest lock test. The deterministic, barrier-forced
    proof that the profile ``SELECT … FOR UPDATE`` is the SOLE serializer (reddening when the lock
    is removed) is ``test_race_consent_barriered_committed_profile_dedup`` below.
    """
    await _seed_verified_user_with_profile(maker, 70011)  # profile ALREADY exists, no consent yet
    tg = TelegramUser(id=70011, first_name="RaceExisting")
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            record_consent(s1, telegram_user=tg, source=RegistrationSource.QR),
            record_consent(s2, telegram_user=tg, source=RegistrationSource.QR),
            return_exceptions=True,
        )
    assert all(isinstance(r, ConsentOutcome) for r in results), results
    assert sum(r.joined for r in results) == 0  # profile pre-existed → neither call emits joined
    assert sum(r.consent_recorded for r in results) == 1
    assert sum(r.phone_shared for r in results) == 1
    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(Consent))).scalar_one() == 1
        assert await _count_events(s, CustomerEventType.PHONE_SHARED) == 1


async def _seed_matched_verified_user_with_profile(
    sm: async_sessionmaker[AsyncSession], tg: TelegramUser
) -> int:
    """Commit a verified-phone user whose identity EXACTLY matches ``tg`` + a COMMITTED empty
    profile (no consent yet). Both details make the ``customer_profile`` ``FOR UPDATE`` the ONLY
    serializer of a concurrent consent race (see :func:`_consent_two_forced_concurrent`): the
    identity match means ``get_or_create_user`` writes no ``UPDATE users`` (no masking users-row
    lock), and the committed profile means the ``ON CONFLICT`` insert is a no-op (not a second
    serializer). Mirrors ``_seed_matched_user_with_profile`` in test_loyalty_activation.py.
    """
    async with sm() as s, s.begin():
        user = User(
            telegram_id=tg.id,
            username=tg.username,
            first_name=tg.first_name,
            last_name=tg.last_name,
            language_code=tg.language_code or "ru",
            phone="+998901112233",
            phone_verified_at=datetime.now(UTC),
        )
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id))
        return user.id


def _sleep_after_read_from(session: AsyncSession, target_table: Table, delay: float = 0.25) -> None:
    """Patch ``session.execute`` to sleep right AFTER any SELECT that reads ``target_table``.

    ``record_consent``/``submit_survey`` have no mid-critical-section service call to wrap (unlike
    ``activate_code`` → ``get_unit_price``), so we widen the window at the guard SELECT itself: both
    racers pause just after reading the dedup table but before committing. With the profile
    ``FOR UPDATE``, the second racer is still blocked at the lock and never reaches this read until
    the first commits (→ one write); without the lock (mutation), both reach the read, both see
    "absent", both write (→ two): deterministic red↔green.

    The target is matched by statement introspection (``get_final_froms``), not SQL text, so a later
    rewrite of the guard cannot silently stop triggering the sleep and quietly return to false-green.
    """
    real_execute = session.execute

    async def _execute(statement, *args, **kwargs):
        result = await real_execute(statement, *args, **kwargs)
        if isinstance(statement, Select) and target_table in statement.get_final_froms():
            await asyncio.sleep(delay)
        return result

    session.execute = _execute  # type: ignore[method-assign]


async def _consent_two_forced_concurrent(maker, monkeypatch, tg: TelegramUser) -> list[object]:
    """Force two ``record_consent`` calls to overlap deterministically (barrier + guard-read sleep).

    A barrier around ``get_or_create_user`` releases both racers into the lock region together; a
    sleep after each racer's ``SELECT … FROM consent`` guard read holds the window open past the
    other's read. With the profile ``FOR UPDATE`` the second racer blocks at the lock and only reads
    consent after the first commits (one consent + one phone_shared); without it both read "absent"
    and each insert (two): deterministic. Preconditions met by
    :func:`_seed_matched_verified_user_with_profile` (matched identity → no ``UPDATE users`` that
    would deadlock the barrier; committed profile → ON CONFLICT is a no-op).
    """
    from src.loyalty import service as svc

    real_get_or_create = svc.get_or_create_user
    barrier = asyncio.Barrier(2)

    async def _barriered(db: AsyncSession, telegram_user: TelegramUser) -> User:
        user = await real_get_or_create(db, telegram_user)
        await asyncio.wait_for(barrier.wait(), timeout=10)  # both enter the lock region together
        return user

    monkeypatch.setattr(svc, "get_or_create_user", _barriered)
    async with maker() as s1, maker() as s2:
        _sleep_after_read_from(s1, Consent.__table__)
        _sleep_after_read_from(s2, Consent.__table__)
        return await asyncio.gather(
            record_consent(s1, telegram_user=tg, source=RegistrationSource.QR),
            record_consent(s2, telegram_user=tg, source=RegistrationSource.QR),
            return_exceptions=True,
        )


async def test_race_consent_barriered_committed_profile_dedup(
    test_db: AsyncSession, maker, monkeypatch
) -> None:
    """Barrier-forced concurrent consents (matched identity, committed profile) → exactly one
    consent + one phone_shared.

    The profile ``SELECT … FOR UPDATE`` is the SOLE serializer (the mandatory UPDATE now runs AFTER
    the guards). Removing that ``.with_for_update()`` → both racers read "no consent" and each
    insert → two rows: mutation RED. Deterministic via the barrier + matched-identity seed.
    """
    tg = TelegramUser(id=70050, first_name="Matched", language_code="ru")
    await _seed_matched_verified_user_with_profile(maker, tg)
    results = await _consent_two_forced_concurrent(maker, monkeypatch, tg)
    assert all(isinstance(r, ConsentOutcome) for r in results), results
    assert sum(r.consent_recorded for r in results) == 1
    assert sum(r.phone_shared for r in results) == 1
    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(Consent))).scalar_one() == 1
        assert await _count_events(s, CustomerEventType.PHONE_SHARED) == 1


# --------------------------------------------------------------------------- #
# HTTP endpoint — dark gate, auth, end-to-end
# --------------------------------------------------------------------------- #


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled is off by default → 404 fires before auth (dark, not probeable)."""
    resp = await client.post("/api/v1/loyalty/consent", json={"source": "qr"})
    assert resp.status_code == 404


async def test_endpoint_requires_init_data(client, monkeypatch) -> None:
    """Flag on but no X-Init-Data → 401."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.post("/api/v1/loyalty/consent", json={"source": "qr"})
    assert resp.status_code == 401


async def test_endpoint_rejects_unknown_source(client, monkeypatch) -> None:
    """`source` is constrained to the RegistrationSource enum → garbage is a 422."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 70021, "first_name": "Bad"})
    }
    resp = await client.post("/api/v1/loyalty/consent", json={"source": "hacker"}, headers=headers)
    assert resp.status_code == 422


async def test_endpoint_happy_e2e_new_user(client, monkeypatch) -> None:
    """Signed initData + flag on, brand-new user → 200, profile created, joined, no phone_shared."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 70020, "first_name": "E2E"})
    }
    resp = await client.post("/api/v1/loyalty/consent", json={"source": "qr"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"consent_recorded": True, "joined": True, "phone_shared": False}


async def test_endpoint_writes_phone_shared_for_verified_user(client, maker, monkeypatch) -> None:
    """A user the bot already verified → the endpoint reports phone_shared True."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_user_with_verified_phone(maker, 70022)
    headers = {
        "X-Init-Data": _make_init_data(
            "test-bot-token-123", {"id": 70022, "first_name": "Verified"}
        )
    }
    resp = await client.post("/api/v1/loyalty/consent", json={"source": "qr"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"consent_recorded": True, "joined": True, "phone_shared": True}
