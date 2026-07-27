"""Monthly draw (Stage 5): ticket formula and the canonical snapshot serialization.

Fairness is commit-reveal, and the load-bearing half of it lives here: at commit the eligible
participants are frozen into ``lottery_tickets`` rows and a SHA-256 of the canonical list is
published. Nothing downstream recomputes eligibility — reads look at the frozen rows, because a
recount depends on profiles that keep changing while the published hash may not.

The winner is never chosen by this code: the draw is pulled live via random.org (strategy §10.3).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.exceptions import ConflictException
from src.loyalty.models import (
    CustomerProfile,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
    LoyaltyStatus,
)

# The audience is Uzbekistan and the draw airs on the 1st at 19:00 local time, so every calendar
# boundary and every date shown to a human is computed in this zone. Stored values stay UTC.
TASHKENT = ZoneInfo("Asia/Tashkent")

# "Activated recently" window. Counted from the freeze moment (`as_of`), NOT from the calendar
# boundary of the period: the snapshot is what the draw runs on, so recency must be measured
# against the same instant that froze it.
ACTIVATION_WINDOW = timedelta(days=30)

# The approved formula has exactly two sources — a filled survey (1) and "Друг" (+1) — so the
# ceiling of 2 holds BY CONSTRUCTION. No `min(...)` guard here on purpose: it would be protection
# against an unreachable state, and a mutation run proved it dead (raising the cap to 3 changed
# nothing). When a third source appears (referrals, channel subscription — out of Stage 5), the
# ceiling stops being implied and must be enforced explicitly, with a test that can fail.
FRIEND_BONUS_TICKETS = 1


def is_adult(profile: CustomerProfile) -> bool:
    """18+ decision: a stored age decides; the checkbox speaks only when no age is stored.

    Ratified 2026-07-27. The two inputs can contradict each other — a member may tick "I am 18+"
    while their survey says 15 — and in that case the stored number wins: age is data the member
    gave about themselves, the checkbox is a claim, and admitting a minor on a claim is the one
    outcome that must not happen. Where no age exists the checkbox is all there is, and that is
    what gives it teeth (see the epic spec §Assumptions).
    """
    if profile.age is not None:
        return profile.age >= 18
    return bool(profile.is_adult)


def has_survey(profile: CustomerProfile) -> bool:
    """Survey completeness for the base ticket: gender AND city. Age is deliberately NOT required.

    Requiring age here would exclude an age-less profile before the 18+ decision is ever reached,
    which would leave the checkbox unable to change any outcome at all (blocker caught at Gate-1).
    """
    return profile.gender is not None and profile.city is not None


def activated_within_window(profile: CustomerProfile, *, as_of: datetime) -> bool:
    """Whether the member activated a QR code within :data:`ACTIVATION_WINDOW` before ``as_of``.

    The bound is inclusive: exactly 30 days still counts. ``last_activation_at`` is ``timestamptz``,
    so the comparison is between aware datetimes — a naive ``as_of`` would raise rather than
    silently compare wrong, which is the failure mode we want.
    """
    if profile.last_activation_at is None:
        return False
    return as_of - profile.last_activation_at <= ACTIVATION_WINDOW


def compute_ticket_count(profile: CustomerProfile | None, *, as_of: datetime) -> int:
    """Tickets this profile holds in a draw frozen at ``as_of`` — 0, 1 or 2.

    Pure: no DB, no clock. ``None`` means "not a club member" (no ``customer_profile`` row) and
    yields 0, mirroring the rest of the loyalty surface where membership is the entry condition.
    """
    if profile is None:
        return 0
    if not (
        has_survey(profile) and is_adult(profile) and activated_within_window(profile, as_of=as_of)
    ):
        return 0
    tickets = 1
    if profile.status == LoyaltyStatus.BIOTACT_FRIEND:
        tickets += FRIEND_BONUS_TICKETS
    return tickets


def period_of(moment: datetime) -> str:
    """Calendar month (``YYYY-MM``) that ``moment`` belongs to, in Tashkent local time.

    Taken in the local zone on purpose: the last hours of a month in Tashkent are still the
    previous month in UTC, and a draw labelled "July" must mean July to the people in it.
    """
    return moment.astimezone(TASHKENT).strftime("%Y-%m")


def canonical_snapshot(rows: list[tuple[int, int, str]]) -> str:
    """Serialize ``(ticket_number, user_id, source)`` rows into the string the hash is taken over.

    The contract is byte-exact because an outsider has to reproduce the published SHA-256 from the
    published list: one line per ticket, ordered by ticket number ascending, fields joined by
    ``;``, lines joined by ``\\n``, NO trailing newline, UTF-8, plain decimal integers.

    ``user_id`` is inside the line, not just the number: hashing numbers alone would let the
    numbers be reshuffled between people after publication without changing the hash, and the
    proof would stop proving anything.
    """
    return "\n".join(
        f"{number};{user_id};{source}"
        for number, user_id, source in sorted(rows, key=lambda r: r[0])
    )


def snapshot_hash(rows: list[tuple[int, int, str]]) -> str:
    """SHA-256 (lowercase hex) of :func:`canonical_snapshot` — the value published for verification."""
    return sha256(canonical_snapshot(rows).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    """Aware UTC now. Never ``utcnow()``: a naive value is read back as the PROCESS's local time
    (measured in PR-6.5-B), which silently shifts stored timestamps on a non-UTC server."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class CommitOutcome:
    """Result of a freeze — primitives only, safe to read after the transaction closes."""

    draw_id: int
    period: str
    total_tickets: int
    participant_hash: str
    committed_at: datetime


# Two different races, two different serializers — see the spec §2.
_ALREADY_COMMITTED = "Розыгрыш за период {period} уже заморожен"
_NO_PARTICIPANTS = "За период {period} нет подходящих участников — замораживать нечего"


async def commit_draw(
    db: AsyncSession, *, period: str, prize_label: str | None = None, as_of: datetime | None = None
) -> CommitOutcome:
    """Freeze the participant snapshot of ``period`` and publish its SHA-256. Irreversible.

    One transaction: the draw row, every ticket, the total and the hash land together or not at
    all — a half-written snapshot would be a published lie. The transaction is the session's
    ambient one (autobegun), NOT an explicit ``async with db.begin()``: :func:`require_admin` reads
    the user from the same session before this call, which already opened it — an explicit begin
    would raise ``A transaction is already begun``. On any exception the caller rolls back
    (``get_db``), so a refusal leaves nothing behind.

    **One serializer: ``UNIQUE(lottery_draws.period)``.** The first commit of a period cannot be
    serialized by a row lock — there is no row yet to lock — so the loser's flush raises
    ``IntegrityError`` and is answered 409. A repeat commit needs no serializer at all: the row is
    created already ``committed`` and never returns to ``open``, so "row exists → refuse" is a
    terminal, race-free state. A ``SELECT … FOR UPDATE`` was written here first (by analogy with
    ``submit_survey``) and a mutation run proved it dead — removing it reddened nothing. It is
    gone rather than kept "just in case": protection against an unreachable state is what the
    rules forbid.

    ⚠️ When PR-5.7 introduces a real ``open`` state (a draw opened ahead of time and committed
    later), the row stops being terminal and the lock becomes load-bearing — it must come back
    together with a race test that fails without it.

    Refuses an empty snapshot: freezing zero participants would burn the period irreversibly (the
    next attempt is rejected as "already committed"), and an administrator facing an empty month
    needs to know why it is empty, not to record the emptiness.
    """
    as_of = as_of or utc_now()
    # Fast path, NOT the serializer: it spares a full profile scan and gives the administrator a
    # precise message. Correctness does not rest on it — without it the insert below would hit the
    # unique index and answer the same 409.
    existing = (
        await db.execute(select(LotteryDraw.id).where(LotteryDraw.period == period))
    ).one_or_none()
    if existing is not None:
        await db.rollback()  # leave no open transaction behind a refusal
        raise ConflictException(_ALREADY_COMMITTED.format(period=period))

    profiles = (
        (
            await db.execute(
                select(CustomerProfile).order_by(CustomerProfile.user_id)  # stable numbering
            )
        )
        .scalars()
        .all()
    )

    rows: list[tuple[int, int, str]] = []
    number = 0
    for profile in profiles:
        count = compute_ticket_count(profile, as_of=as_of)
        for index in range(count):
            number += 1
            source = (
                LotteryTicketSource.SURVEY.value if index == 0 else LotteryTicketSource.FRIEND.value
            )
            rows.append((number, profile.user_id, source))

    if not rows:
        await db.rollback()
        raise ConflictException(_NO_PARTICIPANTS.format(period=period))

    draw = LotteryDraw(
        period=period,
        prize_label=prize_label,
        status=LotteryDrawStatus.COMMITTED.value,
        total_tickets=len(rows),
        participant_hash=snapshot_hash(rows),
        committed_at=as_of,
        hash_published_at=as_of,
    )
    db.add(draw)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # Lost the first-commit race: the winner inserted the same period microseconds earlier.
        # Without this catch the global handler (main.py) still answers 409, but with a generic
        # body — the administrator would not learn WHICH conflict happened.
        raise ConflictException(_ALREADY_COMMITTED.format(period=period)) from exc

    db.add_all(
        LotteryTicket(draw_id=draw.id, user_id=user_id, ticket_number=ticket_number, source=source)
        for ticket_number, user_id, source in rows
    )
    await db.flush()

    await db.commit()
    return CommitOutcome(
        draw_id=draw.id,
        period=period,
        total_tickets=draw.total_tickets,
        participant_hash=draw.participant_hash or "",
        committed_at=as_of,
    )
