"""Ticket formula (Stage 5, PR-5.3): ``compute_ticket_count`` — the unit matrix.

Pure function, no DB: a profile is built in memory and the answer is 0, 1 or 2. Every row of the
matrix in ``.claude/specs/2026-07-27-pr5.3-lottery-commit.md`` is one ``pytest.param`` here, and
the ids carry the row's meaning so a failure names the rule it broke.

The matrix is dictated by the specification, not by production data: on 2026-07-27 prod held 7
profiles of which 6 pass on age alone, so the age-NULL rows below are unreachable there today.
Their absence from the data is not unreachability in code — that is exactly why they are pinned.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.loyalty.lottery import ACTIVATION_WINDOW, compute_ticket_count
from src.loyalty.models import CustomerProfile, Gender, LoyaltyStatus

AS_OF = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)  # freeze moment; the 30-day window counts from here


def _profile(
    *,
    gender: Gender | None = Gender.MALE,
    age: int | None = 25,
    city: str | None = "Ташкент",
    is_adult: bool = False,
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
    activated_ago: timedelta | None = timedelta(days=3),
) -> CustomerProfile:
    """An in-memory profile; defaults describe an eligible newbie with one ticket."""
    return CustomerProfile(
        gender=gender,
        age=age,
        city=city,
        is_adult=is_adult,
        status=status,
        last_activation_at=None if activated_ago is None else AS_OF - activated_ago,
    )


MATRIX = [
    pytest.param(_profile(), 1, id="01_survey_age25_newbie"),
    pytest.param(_profile(status=LoyaltyStatus.BIOTACT_FRIEND), 2, id="02_friend_gets_second"),
    # Age wins over the checkbox: a stored 17 excludes even with 18+ ticked.
    pytest.param(
        _profile(age=17, is_adult=True, status=LoyaltyStatus.BIOTACT_FRIEND),
        0,
        id="03_age17_with_checkbox_excluded",
    ),
    # No age stored → the checkbox decides. This is the row that gives the checkbox its teeth.
    pytest.param(_profile(age=None, is_adult=True), 1, id="04_age_null_checkbox_admits"),
    pytest.param(
        _profile(age=None, is_adult=False, status=LoyaltyStatus.BIOTACT_FRIEND),
        0,
        id="05_age_null_no_checkbox_excluded",
    ),
    pytest.param(
        _profile(city=None, status=LoyaltyStatus.BIOTACT_FRIEND), 0, id="06_no_city_incomplete"
    ),
    pytest.param(_profile(gender=None), 0, id="07_no_gender_incomplete"),
    # The checkbox does not substitute for the survey.
    pytest.param(
        _profile(gender=None, city=None, age=None, is_adult=True),
        0,
        id="08_checkbox_without_survey_excluded",
    ),
    pytest.param(
        _profile(activated_ago=timedelta(days=31), status=LoyaltyStatus.BIOTACT_FRIEND),
        0,
        id="09_activation_31_days_stale",
    ),
    pytest.param(_profile(activated_ago=ACTIVATION_WINDOW), 1, id="10_activation_exactly_30_days"),
    pytest.param(
        _profile(activated_ago=None, status=LoyaltyStatus.BIOTACT_FRIEND),
        0,
        id="11_never_activated",
    ),
    pytest.param(_profile(age=18), 1, id="12_age_18_boundary"),
    pytest.param(None, 0, id="13_not_a_member"),
]


@pytest.mark.parametrize(("profile", "expected"), MATRIX)
def test_ticket_matrix(profile: CustomerProfile | None, expected: int) -> None:
    assert compute_ticket_count(profile, as_of=AS_OF) == expected


def test_ticket_count_never_exceeds_the_cap() -> None:
    """The cap is 2 by the approved formula — a friend with a full survey is the maximum."""
    best = _profile(status=LoyaltyStatus.BIOTACT_FRIEND, is_adult=True, age=30)
    assert compute_ticket_count(best, as_of=AS_OF) == 2


def test_activation_window_is_thirty_days() -> None:
    """Pins the window itself: the matrix rows 09/10 are meaningless if this drifts."""
    assert timedelta(days=30) == ACTIVATION_WINDOW
