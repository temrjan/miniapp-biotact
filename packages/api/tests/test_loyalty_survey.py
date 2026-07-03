"""Tests for the progressive survey (Stage 4, PR-4.4b): POST /loyalty/survey.

``submit_survey`` owns one explicit transaction, so seeding and the call cannot share the single
``test_db`` session (its transaction would already be open). We seed/assert through a second
sessionmaker bound to the same test database, mirroring ``test_loyalty_consent.py``.
"""

import asyncio

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.schemas import TelegramUser
from src.common.exceptions import NotFoundException
from src.config import settings
from src.loyalty.models import CustomerEvent, CustomerEventType, CustomerProfile, Gender
from src.loyalty.schemas import SurveyRequest
from src.loyalty.service import SurveyOutcome, submit_survey
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` created — for seeding + own-txn survey calls."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_member(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a user WITH an empty customer_profile (an already-joined member); return user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id))
        return user.id


async def _seed_user_without_profile(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a bare user with no customer_profile (never joined); return user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="NotMember")
        s.add(user)
        await s.flush()
        return user.id


async def _count_survey_done_events(s: AsyncSession) -> int:
    return int(
        (
            await s.execute(
                select(func.count())
                .select_from(CustomerEvent)
                .where(CustomerEvent.event_type == CustomerEventType.SURVEY_DONE)
            )
        ).scalar_one()
    )


# --------------------------------------------------------------------------- #
# service: happy path, overwrite (not COALESCE), survey_done idempotency, 404s
# --------------------------------------------------------------------------- #


async def test_survey_writes_partial_field_and_marks_survey_done(
    test_db: AsyncSession, maker
) -> None:
    """A single field (city) is written; survey_done is True on the first non-empty call."""
    await _seed_member(maker, 80001)
    tg = TelegramUser(id=80001, first_name="Partial")
    async with maker() as s:
        outcome = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(city="Tashkent"))
    assert isinstance(outcome, SurveyOutcome)
    assert outcome.updated_fields == ["city"]
    assert outcome.survey_done is True

    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.city == "Tashkent"
        assert profile.gender is None
        assert profile.age is None
        assert await _count_survey_done_events(s) == 1


async def test_survey_writes_full_fields(test_db: AsyncSession, maker) -> None:
    """All three fields in one call are all persisted."""
    await _seed_member(maker, 80002)
    tg = TelegramUser(id=80002, first_name="Full")
    async with maker() as s:
        outcome = await submit_survey(
            s,
            telegram_user=tg,
            payload=SurveyRequest(gender=Gender.FEMALE, age=29, city="Samarkand"),
        )
    assert outcome.updated_fields == ["age", "city", "gender"]  # pins service's sorted(updates)

    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.gender is Gender.FEMALE
        assert profile.age == 29
        assert profile.city == "Samarkand"


async def test_survey_second_call_overwrites_not_coalesce(test_db: AsyncSession, maker) -> None:
    """A later call REPLACES a previously-set field — a COALESCE guard would block this (Q2)."""
    await _seed_member(maker, 80003)
    tg = TelegramUser(id=80003, first_name="Overwrite")
    async with maker() as s:
        await submit_survey(s, telegram_user=tg, payload=SurveyRequest(city="Tashkent"))
    async with maker() as s:
        second = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(city="Samarkand"))
    assert second.survey_done is False  # event already exists — not a second write
    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.city == "Samarkand"  # overwritten, NOT stuck at the first value
        assert await _count_survey_done_events(s) == 1


async def test_survey_done_not_duplicated_across_repeat_calls_with_new_fields(
    test_db: AsyncSession, maker
) -> None:
    """A repeat call with a DIFFERENT field still writes the field but not a second event."""
    await _seed_member(maker, 80004)
    tg = TelegramUser(id=80004, first_name="Repeat")
    async with maker() as s:
        first = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(age=40))
    async with maker() as s:
        second = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(city="Bukhara"))
    assert (first.survey_done, second.survey_done) == (True, False)
    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.age == 40
        assert profile.city == "Bukhara"
        assert await _count_survey_done_events(s) == 1


async def test_survey_unknown_user_raises_not_found(test_db: AsyncSession, maker) -> None:
    """A telegram id with no `users` row at all → 404, nothing written."""
    tg = TelegramUser(id=80005, first_name="Ghost")
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await submit_survey(s, telegram_user=tg, payload=SurveyRequest(age=25))


async def test_survey_user_without_profile_raises_not_found(test_db: AsyncSession, maker) -> None:
    """A real user who never joined (no customer_profile) → 404, nothing written."""
    await _seed_user_without_profile(maker, 80006)
    tg = TelegramUser(id=80006, first_name="NeverJoined")
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await submit_survey(s, telegram_user=tg, payload=SurveyRequest(age=25))


async def test_survey_writes_boundary_age_one(test_db: AsyncSession, maker) -> None:
    """age=1 (the lower bound) reaches the profile — a schema pass through to persistence."""
    await _seed_member(maker, 80007)
    tg = TelegramUser(id=80007, first_name="AgeOne")
    async with maker() as s:
        outcome = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(age=1))
    assert outcome.updated_fields == ["age"]
    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.age == 1


async def test_survey_done_event_payload_lists_written_fields(test_db: AsyncSession, maker) -> None:
    """The survey_done event records which fields this call wrote: payload == {"fields": [...]}."""
    await _seed_member(maker, 80008)
    tg = TelegramUser(id=80008, first_name="Payload")
    async with maker() as s:
        await submit_survey(s, telegram_user=tg, payload=SurveyRequest(city="Tashkent"))
    async with maker() as s:
        event = (
            await s.execute(
                select(CustomerEvent).where(
                    CustomerEvent.event_type == CustomerEventType.SURVEY_DONE
                )
            )
        ).scalar_one()
        assert event.payload == {"fields": ["city"]}


# --------------------------------------------------------------------------- #
# schema: empty body rejected
# --------------------------------------------------------------------------- #


def test_survey_request_rejects_all_empty_fields() -> None:
    """An all-None body is meaningless — the model validator rejects it (→ 422 at the endpoint)."""
    with pytest.raises(ValueError, match="At least one"):
        SurveyRequest()


@pytest.mark.parametrize("bad_age", [0, 121])
def test_survey_request_rejects_out_of_range_age(bad_age: int) -> None:
    """age is bounded 1..120 — below (0) and above (121) are rejected (→ 422)."""
    with pytest.raises(ValidationError):
        SurveyRequest(age=bad_age)


@pytest.mark.parametrize("ok_age", [1, 120])
def test_survey_request_accepts_boundary_age(ok_age: int) -> None:
    """The inclusive bounds 1 and 120 are valid ages (guards against an over-tight range)."""
    assert SurveyRequest(age=ok_age).age == ok_age


def test_survey_request_rejects_whitespace_only_city() -> None:
    """A blank city collapses to '' after strip and fails min_length=1 (→ 422)."""
    with pytest.raises(ValidationError):
        SurveyRequest(city="   ")


def test_survey_request_rejects_overlong_city() -> None:
    """A city over 100 chars is rejected (max_length=100)."""
    with pytest.raises(ValidationError):
        SurveyRequest(city="x" * 101)


def test_survey_request_accepts_max_length_city() -> None:
    """Exactly 100 chars is the inclusive upper bound — accepted."""
    city = "x" * 100
    assert SurveyRequest(city=city).city == city


def test_survey_request_strips_city_whitespace() -> None:
    """Surrounding whitespace is trimmed before storage (strip_whitespace=True)."""
    assert SurveyRequest(city="  Tashkent  ").city == "Tashkent"


def test_survey_request_rejects_unknown_gender() -> None:
    """gender is a Gender enum (male|female) — an unknown value is rejected (→ 422)."""
    with pytest.raises(ValidationError):
        SurveyRequest(gender="other")


# --------------------------------------------------------------------------- #
# races — real concurrent sessions on Postgres (customer_profile FOR UPDATE lock)
# --------------------------------------------------------------------------- #


async def test_race_two_survey_calls_write_survey_done_once(test_db: AsyncSession, maker) -> None:
    """Two concurrent survey calls (same member) → exactly one survey_done event.

    This is what the ``FOR UPDATE`` lock exists for: removing it lets both racers see "no prior
    event" and both insert, duplicating ``survey_done``.
    """
    await _seed_member(maker, 80010)
    tg = TelegramUser(id=80010, first_name="Race")
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            submit_survey(s1, telegram_user=tg, payload=SurveyRequest(age=22)),
            submit_survey(s2, telegram_user=tg, payload=SurveyRequest(city="Andijan")),
            return_exceptions=True,
        )
    assert all(isinstance(r, SurveyOutcome) for r in results), results
    assert sum(r.survey_done for r in results) == 1
    async with maker() as s:
        assert await _count_survey_done_events(s) == 1


# --------------------------------------------------------------------------- #
# HTTP endpoint — dark gate, auth, end-to-end
# --------------------------------------------------------------------------- #


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled is off by default → 404 fires before auth (dark, not probeable)."""
    resp = await client.post("/api/v1/loyalty/survey", json={"age": 30})
    assert resp.status_code == 404


async def test_endpoint_requires_init_data(client, monkeypatch) -> None:
    """Flag on but no X-Init-Data → 401."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.post("/api/v1/loyalty/survey", json={"age": 30})
    assert resp.status_code == 401


async def test_endpoint_rejects_empty_body(client, monkeypatch) -> None:
    """An all-empty payload is a 422 — the model validator, not a DB write."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 80021, "first_name": "Empty"})
    }
    resp = await client.post("/api/v1/loyalty/survey", json={}, headers=headers)
    assert resp.status_code == 422


async def test_endpoint_non_member_returns_404(client, monkeypatch) -> None:
    """Flag on, valid initData, but the user never joined → 404."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    headers = {
        "X-Init-Data": _make_init_data(
            "test-bot-token-123", {"id": 80022, "first_name": "NoProfile"}
        )
    }
    resp = await client.post("/api/v1/loyalty/survey", json={"age": 30}, headers=headers)
    assert resp.status_code == 404


async def test_endpoint_happy_e2e_member(client, maker, monkeypatch) -> None:
    """Signed initData + flag on, existing member → 200, field written, survey_done True."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_member(maker, 80023)
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 80023, "first_name": "E2E"})
    }
    resp = await client.post("/api/v1/loyalty/survey", json={"gender": "male"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated_fields": ["gender"], "survey_done": True}
