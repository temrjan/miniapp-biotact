"""Tests for the progressive survey (Stage 4, PR-4.4b): POST /loyalty/survey.

``submit_survey`` owns one explicit transaction, so seeding and the call cannot share the single
``test_db`` session (its transaction would already be open). We seed/assert through a second
sessionmaker bound to the same test database, mirroring ``test_loyalty_consent.py``.
"""

import asyncio

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import Select, Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.schemas import TelegramUser
from src.common.exceptions import NotFoundException
from src.config import settings
from src.loyalty.models import CustomerEvent, CustomerEventType, CustomerProfile, Gender
from src.loyalty.schemas import (
    DEMOGRAPHIC_SURVEY_FIELDS,
    NON_DEMOGRAPHIC_SURVEY_FIELDS,
    SurveyRequest,
)
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
# 18+ self-confirmation (Stage 5, PR-5.2): written like any survey field, but it
# is NOT an answer about the member — so it must not spend the survey_done event
# --------------------------------------------------------------------------- #


async def test_survey_only_is_adult_writes_flag_without_survey_done(
    test_db: AsyncSession, maker
) -> None:
    """A call carrying ONLY the 18+ checkbox writes the flag and does NOT mark the survey done.

    ``survey_done`` is the 4th step of the registration funnel (``analytics.get_loyalty_stats``)
    and reads as "the member told us about themselves". 18+ is a legal condition of lottery entry,
    not demographics: counting it would report a completed survey for someone who answered none.
    """
    await _seed_member(maker, 80030)
    tg = TelegramUser(id=80030, first_name="AdultOnly")
    async with maker() as s:
        outcome = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(is_adult=True))
    assert outcome.updated_fields == ["is_adult"]
    assert outcome.survey_done is False

    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.is_adult is True
        assert (profile.gender, profile.age, profile.city) == (None, None, None)
        assert await _count_survey_done_events(s) == 0


async def test_survey_is_adult_with_demographics_marks_survey_done(
    test_db: AsyncSession, maker
) -> None:
    """18+ alongside a demographic field: both are written and the one-time event fires."""
    await _seed_member(maker, 80031)
    tg = TelegramUser(id=80031, first_name="AdultAndCity")
    async with maker() as s:
        outcome = await submit_survey(
            s, telegram_user=tg, payload=SurveyRequest(city="Tashkent", is_adult=True)
        )
    assert outcome.updated_fields == ["city", "is_adult"]
    assert outcome.survey_done is True

    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert (profile.city, profile.is_adult) == ("Tashkent", True)
        assert await _count_survey_done_events(s) == 1


async def test_survey_done_fires_on_the_later_demographic_call_after_is_adult_only(
    test_db: AsyncSession, maker
) -> None:
    """18+ first, demography later → the event is spent by the SECOND call, not the first.

    The funnel must count this member at the moment they actually answered the survey; the event
    payload therefore lists the demographic field, not the checkbox that preceded it.
    """
    await _seed_member(maker, 80032)
    tg = TelegramUser(id=80032, first_name="AdultThenSurvey")
    async with maker() as s:
        first = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(is_adult=True))
    async with maker() as s:
        second = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(age=33))
    assert (first.survey_done, second.survey_done) == (False, True)

    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert (profile.is_adult, profile.age) == (True, 33)
        assert await _count_survey_done_events(s) == 1
        event = (
            await s.execute(
                select(CustomerEvent).where(
                    CustomerEvent.event_type == CustomerEventType.SURVEY_DONE
                )
            )
        ).scalar_one()
        assert event.payload == {"fields": ["age"]}


async def test_survey_is_adult_false_overwrites_stored_true(test_db: AsyncSession, maker) -> None:
    """Un-ticking is a real write: ``False`` is a value, not an absence.

    Guards the ``exclude_none=True`` contract in the service — a falsy filter there would silently
    drop this call and leave a member confirmed as 18+ against their correction.
    """
    await _seed_member(maker, 80033)
    tg = TelegramUser(id=80033, first_name="Untick")
    async with maker() as s:
        await submit_survey(s, telegram_user=tg, payload=SurveyRequest(is_adult=True))
    async with maker() as s:
        outcome = await submit_survey(s, telegram_user=tg, payload=SurveyRequest(is_adult=False))
    assert outcome.updated_fields == ["is_adult"]

    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.is_adult is False


# --------------------------------------------------------------------------- #
# schema: empty body rejected
# --------------------------------------------------------------------------- #


def test_survey_request_rejects_all_empty_fields() -> None:
    """An all-None body is meaningless — the model validator rejects it (→ 422 at the endpoint)."""
    with pytest.raises(ValueError, match="At least one"):
        SurveyRequest()


def test_survey_field_classification_is_exhaustive() -> None:
    """Every field of ``SurveyRequest`` is classified as demographic or not — no field falls through.

    The split drives ``survey_done`` (and with it the registration funnel), so it must never be
    derived by subtraction: the planned «Правила акции» checkbox would then default into
    "demographics" and a lone consent tick would spend the event again — the exact bug PR-5.2
    closed. This test fails on the NEXT unclassified field, forcing a deliberate choice.
    """
    assert set(SurveyRequest.model_fields) == (
        DEMOGRAPHIC_SURVEY_FIELDS | NON_DEMOGRAPHIC_SURVEY_FIELDS
    )
    assert DEMOGRAPHIC_SURVEY_FIELDS.isdisjoint(NON_DEMOGRAPHIC_SURVEY_FIELDS)


def test_survey_request_accepts_is_adult_alone() -> None:
    """The 18+ checkbox on its own is a valid body — it is a field, not a modifier of the others."""
    assert SurveyRequest(is_adult=True).is_adult is True


def test_survey_request_accepts_is_adult_false_alone() -> None:
    """``False`` alone passes the "at least one field" guard — un-ticking must reach the server."""
    assert SurveyRequest(is_adult=False).is_adult is False


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

    Happy-path dedup-contract check over plain ``asyncio.gather``: it does NOT deterministically
    force the two transactions to overlap, so on its own it is not a mutation-honest lock test. The
    deterministic, barrier-forced proof that the profile ``SELECT … FOR UPDATE`` is the SOLE
    serializer (reddening when the lock is removed) is
    ``test_race_survey_barriered_committed_profile_dedup`` below.
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


def _sleep_after_read_from(session: AsyncSession, target_table: Table, delay: float = 0.25) -> None:
    """Patch ``session.execute`` to sleep right AFTER any SELECT that reads ``target_table``.

    ``submit_survey`` has no mid-critical-section service call to wrap (unlike ``activate_code`` →
    ``get_unit_price``), so we widen the window at the ``survey_done`` guard SELECT itself: both
    racers pause just after reading ``customer_event`` but before committing. With the profile
    ``FOR UPDATE`` the second racer is still blocked at the lock and never reaches this read until
    the first commits (→ one event); without the lock (mutation), both reach the read, both see
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


async def _seed_matched_member(sm: async_sessionmaker[AsyncSession], tg: TelegramUser) -> int:
    """Commit a member whose identity matches ``tg`` + a COMMITTED empty profile.

    The committed profile makes the ``customer_profile`` ``FOR UPDATE`` the sole serializer of a
    concurrent survey race (see :func:`_survey_two_forced_concurrent`). Identity match keeps the
    seed honest even though ``submit_survey`` resolves via ``get_user_by_telegram_id`` (a plain
    SELECT that takes no users-row lock).
    """
    async with sm() as s, s.begin():
        user = User(
            telegram_id=tg.id,
            username=tg.username,
            first_name=tg.first_name,
            last_name=tg.last_name,
            language_code=tg.language_code or "ru",
        )
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id))
        return user.id


async def _survey_two_forced_concurrent(
    maker, monkeypatch, tg: TelegramUser, p1: SurveyRequest, p2: SurveyRequest
) -> list[object]:
    """Force two ``submit_survey`` calls to overlap deterministically (barrier + guard-read sleep).

    A barrier around ``get_user_by_telegram_id`` releases both racers into the lock region together;
    a sleep after each racer's ``SELECT … FROM customer_event`` (the ``survey_done`` guard) holds
    the window open past the other's read. With the profile ``FOR UPDATE`` the second racer blocks
    at the lock and only reads after the first commits (one ``survey_done``); without it both read
    "absent" and each insert (two): deterministic. Committed matched member seeded by
    :func:`_seed_matched_member`.
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
        _sleep_after_read_from(s1, CustomerEvent.__table__)
        _sleep_after_read_from(s2, CustomerEvent.__table__)
        return await asyncio.gather(
            submit_survey(s1, telegram_user=tg, payload=p1),
            submit_survey(s2, telegram_user=tg, payload=p2),
            return_exceptions=True,
        )


async def test_race_survey_barriered_committed_profile_dedup(
    test_db: AsyncSession, maker, monkeypatch
) -> None:
    """Barrier-forced concurrent survey calls (matched member, committed profile) → exactly one
    survey_done event.

    The profile ``SELECT … FOR UPDATE`` is the SOLE serializer (the mandatory UPDATE now runs AFTER
    the survey_done guard). Removing that ``.with_for_update()`` → both racers read "no survey_done"
    and each insert → two events: mutation RED. Deterministic via barrier + guard-read sleep.
    """
    tg = TelegramUser(id=80050, first_name="Matched", language_code="ru")
    await _seed_matched_member(maker, tg)
    results = await _survey_two_forced_concurrent(
        maker, monkeypatch, tg, SurveyRequest(age=22), SurveyRequest(city="Andijan")
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


async def test_endpoint_accepts_is_adult_only_body(client, maker, monkeypatch) -> None:
    """HTTP: ``{"is_adult": true}`` is a valid body (200), not the 422 an unknown field would give.

    End-to-end proof of the two halves together: the flag is persisted and the funnel event is not.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_member(maker, 80024)
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 80024, "first_name": "Adult"})
    }
    resp = await client.post("/api/v1/loyalty/survey", json={"is_adult": True}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated_fields": ["is_adult"], "survey_done": False}

    async with maker() as s:
        profile = (await s.execute(select(CustomerProfile))).scalar_one()
        assert profile.is_adult is True
        assert await _count_survey_done_events(s) == 0
