"""Tests for marketing consent (legal-docs PR-2): POST /loyalty/consent/marketing.

Grant/revoke are append-style writes on the ``consent`` table (a new row per change; the
ACTIVE state is the latest row per user), guarded by the same ``customer_profile``
``SELECT … FOR UPDATE`` serializer as ``record_consent`` — there is no unique constraint,
so the row lock is the sole protection against two active rows from concurrent grants.
Seeding/asserts go through a second sessionmaker on the same test DB, mirroring
test_loyalty_consent.py.
"""

import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.schemas import TelegramUser
from src.common.exceptions import NotFoundException
from src.config import settings
from src.loyalty.models import (
    Consent,
    ConsentPurpose,
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
)
from src.loyalty.service import set_marketing_consent
from src.users.models import User
from tests.conftest import _make_init_data
from tests.test_loyalty_consent import _sleep_after_read_from

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

VALID_COMPOSITE = "offer:draft-2026-08-20+policy:draft-2026-08-20"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` created — for seeding + own-txn calls."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_member(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a user WITH a customer_profile (marketing consent is member-only); return user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Seed")
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id))
        return user.id


async def _marketing_rows(s: AsyncSession) -> list[Consent]:
    return list(
        (
            await s.execute(
                select(Consent)
                .where(Consent.purpose == ConsentPurpose.MARKETING)
                .order_by(Consent.id)
            )
        )
        .scalars()
        .all()
    )


async def _marketing_events(s: AsyncSession) -> list[CustomerEvent]:
    return list(
        (
            await s.execute(
                select(CustomerEvent)
                .where(CustomerEvent.event_type == CustomerEventType.CONSENT_RECORDED)
                .order_by(CustomerEvent.id)
            )
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------- #
# service: grant / revoke semantics (append-style, idempotent)
# --------------------------------------------------------------------------- #


async def test_grant_inserts_active_row_and_event(test_db: AsyncSession, maker) -> None:
    """First grant → a granted=True row, marketing_active True, one consent_recorded event."""
    await _seed_member(maker, 71001)
    tg = TelegramUser(id=71001, first_name="Granter")
    async with maker() as s:
        active = await set_marketing_consent(
            s,
            telegram_user=tg,
            granted=True,
            doc_version=VALID_COMPOSITE,
            user_agent="MiniApp/2.0",
        )
    assert active is True
    async with maker() as s:
        rows = await _marketing_rows(s)
        assert len(rows) == 1
        assert rows[0].granted is True
        assert rows[0].version == VALID_COMPOSITE
        events = await _marketing_events(s)
        assert len(events) == 1
        assert events[0].payload == {
            "purpose": "marketing",
            "granted": True,
            "doc_version": VALID_COMPOSITE,
            "source": "profile",
            "user_agent": "MiniApp/2.0",
        }


async def test_grant_without_doc_version_defaults_to_1_0(test_db: AsyncSession, maker) -> None:
    """A grant without doc_version records the legacy default, same rule as data_processing."""
    await _seed_member(maker, 71002)
    tg = TelegramUser(id=71002, first_name="Default")
    async with maker() as s:
        await set_marketing_consent(s, telegram_user=tg, granted=True)
    async with maker() as s:
        rows = await _marketing_rows(s)
        assert len(rows) == 1
        assert rows[0].version == "1.0"


async def test_repeat_grant_is_a_noop(test_db: AsyncSession, maker) -> None:
    """A second grant while one is active writes nothing — no row, no event; still active."""
    await _seed_member(maker, 71003)
    tg = TelegramUser(id=71003, first_name="Twice")
    async with maker() as s:
        first = await set_marketing_consent(s, telegram_user=tg, granted=True)
    async with maker() as s:
        second = await set_marketing_consent(s, telegram_user=tg, granted=True)
    assert (first, second) == (True, True)
    async with maker() as s:
        assert len(await _marketing_rows(s)) == 1
        assert len(await _marketing_events(s)) == 1


async def test_revoke_appends_granted_false_row(test_db: AsyncSession, maker) -> None:
    """Revoke after grant → an appended granted=False row; active becomes False; event written."""
    await _seed_member(maker, 71004)
    tg = TelegramUser(id=71004, first_name="Revoker")
    async with maker() as s:
        await set_marketing_consent(s, telegram_user=tg, granted=True)
    async with maker() as s:
        active = await set_marketing_consent(s, telegram_user=tg, granted=False)
    assert active is False
    async with maker() as s:
        rows = await _marketing_rows(s)
        assert [r.granted for r in rows] == [True, False]
        events = await _marketing_events(s)
        assert len(events) == 2
        assert events[1].payload is not None
        assert events[1].payload["granted"] is False
        assert events[1].payload["purpose"] == "marketing"


async def test_repeat_revoke_is_a_noop(test_db: AsyncSession, maker) -> None:
    """Revoke while nothing is active writes nothing (no row, no event); still inactive."""
    await _seed_member(maker, 71005)
    tg = TelegramUser(id=71005, first_name="Calm")
    async with maker() as s:
        active = await set_marketing_consent(s, telegram_user=tg, granted=False)
    assert active is False
    async with maker() as s:
        assert len(await _marketing_rows(s)) == 0
        assert len(await _marketing_events(s)) == 0


async def test_re_grant_after_revoke_works(test_db: AsyncSession, maker) -> None:
    """grant → revoke → grant: three appended rows, final state active."""
    await _seed_member(maker, 71006)
    tg = TelegramUser(id=71006, first_name="Back")
    async with maker() as s:
        await set_marketing_consent(s, telegram_user=tg, granted=True)
    async with maker() as s:
        await set_marketing_consent(s, telegram_user=tg, granted=False)
    async with maker() as s:
        active = await set_marketing_consent(s, telegram_user=tg, granted=True)
    assert active is True
    async with maker() as s:
        rows = await _marketing_rows(s)
        assert [r.granted for r in rows] == [True, False, True]
        assert len(await _marketing_events(s)) == 3


async def test_marketing_requires_an_existing_member(test_db: AsyncSession, maker) -> None:
    """No customer_profile (non-member or unknown user) → 404, mirroring the survey endpoint.

    The toggle lives behind the member-only profile block, and the FOR UPDATE serializer needs
    a profile row to lock — creating one here would join the club through a side door.
    """
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await set_marketing_consent(
                s, telegram_user=TelegramUser(id=71999, first_name="Ghost"), granted=True
            )
    async with maker() as s:
        assert len(await _marketing_rows(s)) == 0


# --------------------------------------------------------------------------- #
# race — two concurrent grants under the profile FOR UPDATE (Finding 3)
# --------------------------------------------------------------------------- #


async def _seed_matched_member(sm: async_sessionmaker[AsyncSession], tg: TelegramUser) -> int:
    """Commit a member whose identity EXACTLY matches ``tg`` (no UPDATE users on the path) with a
    COMMITTED profile and no marketing consent — so the profile FOR UPDATE is the only serializer,
    mirroring ``_seed_matched_verified_user_with_profile`` in test_loyalty_consent.py.
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


async def _marketing_two_forced_concurrent(maker, monkeypatch, tg: TelegramUser) -> list[object]:
    """Force two ``set_marketing_consent`` grants to overlap (barrier on the user read + a sleep
    after the consent guard read). With the profile FOR UPDATE the second racer blocks at the lock
    and reads "already granted" after the first commits (one row); without it both read "absent"
    and each insert (two): deterministic red↔green.
    """
    from src.loyalty import service as svc

    real_get_user = svc.get_user_by_telegram_id
    barrier = asyncio.Barrier(2)

    async def _barriered(db: AsyncSession, telegram_id: int) -> User | None:
        user = await real_get_user(db, telegram_id)
        await asyncio.wait_for(barrier.wait(), timeout=10)  # both enter the lock region together
        return user

    monkeypatch.setattr(svc, "get_user_by_telegram_id", _barriered)
    async with maker() as s1, maker() as s2:
        _sleep_after_read_from(s1, Consent.__table__)
        _sleep_after_read_from(s2, Consent.__table__)
        return await asyncio.gather(
            set_marketing_consent(s1, telegram_user=tg, granted=True),
            set_marketing_consent(s2, telegram_user=tg, granted=True),
            return_exceptions=True,
        )


async def test_race_two_grants_leave_exactly_one_active_row(
    test_db: AsyncSession, maker, monkeypatch
) -> None:
    """Two barrier-forced concurrent grants → exactly one consent row and one event.

    Mutation-honest: removing the ``.with_for_update()`` on the profile read lets both racers
    past the guard → two rows → RED. Deterministic via the barrier + matched-identity seed.
    """
    tg = TelegramUser(id=71050, first_name="Racer", language_code="ru")
    await _seed_matched_member(maker, tg)
    results = await _marketing_two_forced_concurrent(maker, monkeypatch, tg)
    assert all(r is True for r in results), results
    async with maker() as s:
        rows = await _marketing_rows(s)
        assert len(rows) == 1
        assert rows[0].granted is True
        assert len(await _marketing_events(s)) == 1


# --------------------------------------------------------------------------- #
# HTTP endpoint — dark gate, auth, member-only, validation, e2e
# --------------------------------------------------------------------------- #


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled is off by default → 404 fires before auth (dark, not probeable)."""
    resp = await client.post("/api/v1/loyalty/consent/marketing", json={"granted": True})
    assert resp.status_code == 404


async def test_endpoint_requires_init_data(client, monkeypatch) -> None:
    """Flag on but no X-Init-Data → 401."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.post("/api/v1/loyalty/consent/marketing", json={"granted": True})
    assert resp.status_code == 401


async def test_endpoint_rejects_invalid_doc_version(client, maker, monkeypatch) -> None:
    """The marketing body shares the doc_version validation with /consent → garbage is a 422."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_member(maker, 71020)
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 71020, "first_name": "Bad"})
    }
    resp = await client.post(
        "/api/v1/loyalty/consent/marketing",
        json={"granted": True, "doc_version": "garbage"},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_endpoint_non_member_gets_404(client, monkeypatch) -> None:
    """A signed call from an unknown user (no profile) → 404, never a lazily created membership."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 71021, "first_name": "New"})
    }
    resp = await client.post(
        "/api/v1/loyalty/consent/marketing", json={"granted": True}, headers=headers
    )
    assert resp.status_code == 404


async def test_endpoint_grant_revoke_e2e(client, maker, monkeypatch) -> None:
    """Full path: member grants (200, active) then revokes (200, inactive); rows accumulate."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_member(maker, 71022)
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 71022, "first_name": "E2E"})
    }
    grant = await client.post(
        "/api/v1/loyalty/consent/marketing",
        json={"granted": True, "doc_version": VALID_COMPOSITE},
        headers=headers,
    )
    assert grant.status_code == 200, grant.text
    assert grant.json() == {"marketing_active": True}
    revoke = await client.post(
        "/api/v1/loyalty/consent/marketing", json={"granted": False}, headers=headers
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json() == {"marketing_active": False}
    async with maker() as s:
        rows = await _marketing_rows(s)
        assert [r.granted for r in rows] == [True, False]
