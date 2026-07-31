"""Legacy calendar helpers, kept alive only until the contract phase removes the column.

``period_of`` and ``committable_periods`` have had no callers since step 3: a draw is frozen by an
administrator pressing a button, so there is no month to label and no window to be inside. They stay
because ``lottery_draws.period`` stays until step 6 removes both — and they stay TESTED, because a
function whose proof is deleted first is a function nobody notices coming back to life.

Everything about freezing a draw is in ``test_lottery_freeze.py``.
"""

from datetime import UTC, datetime

from src.loyalty.lottery import committable_periods, period_of

AS_OF = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def test_period_of_uses_tashkent_not_utc() -> None:
    """22:00 UTC on 31 July is already 1 August in Tashkent — the label follows the audience."""
    assert period_of(datetime(2026, 7, 31, 22, 0, tzinfo=UTC)) == "2026-08"
    assert period_of(datetime(2026, 7, 31, 18, 0, tzinfo=UTC)) == "2026-07"


def test_committable_window_is_previous_and_current_tashkent_month() -> None:
    """The window is two months wide: the freeze ran 24h before the draw, either side of midnight."""
    assert committable_periods(AS_OF) == ("2026-07", "2026-08")


def test_committable_window_uses_tashkent_not_utc() -> None:
    """22:00 UTC on 31 July is already 1 August in Tashkent — the window moves with the audience."""
    assert committable_periods(datetime(2026, 7, 31, 22, 0, tzinfo=UTC)) == ("2026-07", "2026-08")
    assert committable_periods(datetime(2026, 7, 31, 18, 0, tzinfo=UTC)) == ("2026-06", "2026-07")


def test_committable_window_crosses_the_year_boundary() -> None:
    """January must look back to DECEMBER of the previous year, not to month zero.

    A naive ``month - 1`` yields "2026-00" on 1 January. The bug it pins is dead code today, and the
    test costs nothing to keep until the function goes.
    """
    assert committable_periods(datetime(2026, 1, 15, 12, 0, tzinfo=UTC)) == ("2025-12", "2026-01")
