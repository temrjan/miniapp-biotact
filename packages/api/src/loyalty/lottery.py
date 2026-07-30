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

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.service import get_user_by_telegram_id
from src.common.exceptions import BadRequestException, ConflictException, NotFoundException
from src.loyalty.models import (
    CustomerProfile,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
    LoyaltyStatus,
)
from src.users.models import User

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


def committable_periods(as_of: datetime) -> tuple[str, str]:
    """Periods a freeze may legally target at ``as_of``: the previous and the current month.

    Tashkent months, not UTC ones — see :func:`period_of`. The window is two wide because the
    snapshot is taken 24h before the live draw on the 1st: that moment can fall on the last day of
    the month being drawn or already on the 1st of the next one.

    Anything else is refused, and the refusal is not pedantry: freezing is irreversible, so a typo
    in the year would burn a period that has not happened yet — filled with today's participants —
    and freezing a long-past month would fill it with today's eligibility instead of that month's.
    """
    local = as_of.astimezone(TASHKENT)
    previous_month_day = local.replace(day=1) - timedelta(days=1)
    return previous_month_day.strftime("%Y-%m"), local.strftime("%Y-%m")


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


@dataclass(frozen=True)
class ExportOutcome:
    """A frozen draw ready to publish: the file's exact bytes, plus the label its filename carries.

    Deliberately two fields. The hash and the ticket count are NOT carried here — nothing consumes
    them (the body is the response, the period is the filename), and a field no caller reads is the
    kind of dead weight a mutation run flags. Tests that need the count read the
    ``lottery_draws.total_tickets`` column directly.
    """

    period: str
    body: str


# Two different races, two different serializers — see the spec §2.
_ALREADY_COMMITTED = "Розыгрыш за период {period} уже заморожен"
_NO_PARTICIPANTS = "За период {period} нет подходящих участников — замораживать нечего"
_PERIOD_OUT_OF_WINDOW = (
    "Период {period} нельзя заморозить сейчас: заморозка идёт по текущему или прошлому месяцу "
    "(допустимо: {allowed}). Заморозка необратима, поэтому чужой период не принимается."
)


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
    allowed = committable_periods(as_of)
    if period not in allowed:
        raise BadRequestException(
            _PERIOD_OUT_OF_WINDOW.format(period=period, allowed=" | ".join(allowed))
        )

    # Not the serializer (that is the unique index) — but load-bearing for the DIAGNOSIS, which on
    # an irreversible operation matters as much. Without this check a repeat freeze of a period
    # whose participants have since gone stale would fall through to the "nobody qualifies" branch
    # and tell the administrator the wrong thing about an already-frozen month.
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


_DRAW_NOT_FOUND = "Розыгрыш {draw_id} не найден"
_SNAPSHOT_TAMPERED = (
    "Снимок розыгрыша {period} не сходится с опубликованным хешем: строки билетов изменились "
    "после заморозки. Выгрузка не отдана — публиковать список, который не сходится с нашим же "
    "хешем, снаружи неотличимо от подлога."
)


async def export_draw(db: AsyncSession, *, draw_id: int) -> ExportOutcome:
    """Serialize a frozen draw into exactly the bytes its published SHA-256 was taken over.

    Read-only. The rows come from the frozen ``lottery_tickets``, never from a recount of
    eligibility: profiles keep changing after the freeze while the published hash does not, so a
    recount would drift away from the very value it is supposed to prove (see the module docstring).

    Three columns are selected rather than whole entities — they are all the canonical line needs,
    and selecting columns also sidesteps the session identity map, which could otherwise hand back a
    stale ``LotteryTicket`` read earlier in the same session.

    No ``ORDER BY`` here on purpose: :func:`canonical_snapshot` sorts by ticket number as part of
    the published byte contract, so a SQL sort would be decoration — removing it would redden
    nothing, which is how a dead construction is recognised.

    Refuses (409) when the recomputed hash disagrees with the published one. The comparison is made
    over the bytes about to be served, not over a parallel recomputation from the rows: the claim
    this endpoint makes is about the FILE, so that is what must be verified. The only way into this
    state is a snapshot row edited after the freeze; serving it would publish a list that fails our
    own published hash, which from outside looks exactly like a faked draw.
    """
    draw = (
        await db.execute(
            select(LotteryDraw.period, LotteryDraw.participant_hash).where(
                LotteryDraw.id == draw_id
            )
        )
    ).one_or_none()
    if draw is None:
        raise NotFoundException(_DRAW_NOT_FOUND.format(draw_id=draw_id))

    result = await db.execute(
        select(LotteryTicket.ticket_number, LotteryTicket.user_id, LotteryTicket.source).where(
            LotteryTicket.draw_id == draw_id
        )
    )
    rows: list[tuple[int, int, str]] = []
    for r in result:
        if r.ticket_number is None:
            # Stamped into this draw yet unnumbered. Unreachable while only ``commit_draw`` writes
            # tickets, and load-bearing from the step where clean (unnumbered) tickets start to
            # exist: it means the snapshot lost the very identifier the published hash is taken
            # over. Same alarm as the hash mismatch below, and the same refusal — a list we cannot
            # number is, from outside, indistinguishable from a faked one.
            raise ConflictException(_SNAPSHOT_TAMPERED.format(period=draw.period))
        rows.append((r.ticket_number, r.user_id, r.source))

    body = canonical_snapshot(rows)
    if sha256(body.encode("utf-8")).hexdigest() != draw.participant_hash:
        raise ConflictException(_SNAPSHOT_TAMPERED.format(period=draw.period))

    return ExportOutcome(period=draw.period, body=body)


@dataclass(frozen=True)
class DrawResult:
    """How a finished draw ended for one member."""

    period: str
    tickets: int  # how many tickets this member held in THAT draw
    won: bool
    # The best rank the member took (1 = main prize). Singular even though a member can hold
    # several winning tickets: whether multiple prizes go to one person is Open Q5, still
    # unanswered, and this field neither decides it nor blocks either answer.
    prize_rank: int | None
    prize_label: str | None


@dataclass(frozen=True)
class MyTicketsOutcome:
    """What the member sees about their own participation.

    The top block is "what I have now", ``result`` is "how the previous draw ended" — they describe
    different draws and are deliberately not merged. Invariants held by construction here and
    asserted in the tests: ``period is not None`` exactly when ``is_frozen``; ``prize_label`` is
    non-null only when frozen; after a finished draw the top block is always prospective.
    """

    tickets: int
    is_frozen: bool
    period: str | None
    prize_label: str | None
    result: DrawResult | None


_NO_TICKETS = MyTicketsOutcome(
    tickets=0, is_frozen=False, period=None, prize_label=None, result=None
)


async def get_my_tickets(
    db: AsyncSession, *, telegram_id: int, as_of: datetime | None = None
) -> MyTicketsOutcome:
    """One member's participation: frozen tickets if a draw is committed, prospective otherwise.

    Read-only. Never creates a ``users`` or ``customer_profile`` row — merely opening the card must
    cost nothing, the same rule :func:`~src.loyalty.service.get_loyalty_summary` follows. A user who
    never joined is not an error: they get zero tickets and the card explains how to earn one.

    **Prospective and frozen counts are never conflated.** Before the freeze no ``lottery_tickets``
    rows exist, so the number is recomputed from the profile and can still change on its own (an
    activation ages out of the 30-day window). After the freeze it is fixed. The caller is told
    which one it holds via ``is_frozen``, because "2 tickets" quietly becoming "1" without that
    distinction reads to a member as tickets being taken away.

    After a draw is drawn the member gets BOTH its outcome and a prospective count for the next
    period: showing a bare zero to someone who is already accumulating again would say they are out.

    ``as_of`` is injectable exactly as in :func:`commit_draw`, so the count never depends on the
    machine clock in tests.
    """
    as_of = as_of or utc_now()

    user = await get_user_by_telegram_id(db, telegram_id)
    if user is None:
        return _NO_TICKETS

    latest = (
        await db.execute(
            select(LotteryDraw.id, LotteryDraw.period, LotteryDraw.status, LotteryDraw.prize_label)
            .order_by(LotteryDraw.period.desc())
            .limit(1)
        )
    ).one_or_none()

    if latest is not None and latest.status == LotteryDrawStatus.COMMITTED.value:
        frozen = (
            await db.execute(
                select(func.count())
                .select_from(LotteryTicket)
                .where(LotteryTicket.draw_id == latest.id, LotteryTicket.user_id == user.id)
            )
        ).scalar_one()
        return MyTicketsOutcome(
            tickets=int(frozen),
            is_frozen=True,
            period=latest.period,
            prize_label=latest.prize_label,
            result=None,
        )

    result: DrawResult | None = None
    if latest is not None and latest.status == LotteryDrawStatus.DRAWN.value:
        rows = (
            await db.execute(
                select(LotteryTicket.is_winner, LotteryTicket.prize_rank).where(
                    LotteryTicket.draw_id == latest.id, LotteryTicket.user_id == user.id
                )
            )
        ).all()
        # No rows means this member held no tickets in that draw — they joined after the freeze.
        # Reporting `won=False` here would turn "did not take part" into "lost", which is a
        # different statement about a person, and the wrong one. Absence of an outcome is the
        # honest answer. (Caught by the Reviewer at Gate-2.)
        if rows:
            ranks = [r.prize_rank for r in rows if r.is_winner and r.prize_rank is not None]
            result = DrawResult(
                period=latest.period,
                tickets=len(rows),
                won=any(r.is_winner for r in rows),
                prize_rank=min(ranks, default=None),
                prize_label=latest.prize_label,
            )

    # NOTE: `record_winners` lives at the end of this module; see it for the draw's terminal
    # transition. Everything that is not a committed draw falls through to the prospective count —
    # including
    # the `open` status, which nothing writes today (`commit_draw` creates rows already committed)
    # and which PR-5.7 will make real. Falling through is the right answer for it: a draw that has
    # not run yet has no outcome to report, so the member simply sees what they currently hold.
    profile = (
        await db.execute(select(CustomerProfile).where(CustomerProfile.user_id == user.id))
    ).scalar_one_or_none()
    return MyTicketsOutcome(
        tickets=compute_ticket_count(profile, as_of=as_of),
        is_frozen=False,
        period=None,
        prize_label=None,
        result=result,
    )


@dataclass(frozen=True)
class WinnerRow:
    """One recorded winner. No phone: the administrator looks the contact up by name in "Клиенты"
    (decision 2026-07-28) — this endpoint hands out no contact details."""

    ticket_number: int
    prize_rank: int
    user_id: int
    first_name: str | None


@dataclass(frozen=True)
class DrawOutcome:
    """Result of closing a draw — primitives only, safe to read after the transaction closes."""

    draw_id: int
    period: str
    status: str
    drawn_at: datetime
    winners: tuple[WinnerRow, ...]


_NOT_COMMITTED = (
    "Розыгрыш {period} нельзя разыграть: он в состоянии «{status}». Разыгрывается только "
    "замороженный тираж, и только один раз."
)
_NUMBER_OUT_OF_RANGE = "Номер {number} вне диапазона 1..{total} — проверьте, что назвали."
_DUPLICATE_NUMBER = "Номер {number} назван дважды."
_ALREADY_WON = (
    "Номер {number} принадлежит участнику, который уже выиграл в этом розыгрыше. По правилам один "
    "человек забирает один приз — крутите барабан ещё раз. Ничего не записано."
)
_SNAPSHOT_INCOMPLETE = (
    "В снимке розыгрыша {period} нет билетов с номерами {numbers}, хотя они в диапазоне. "
    "Снимок повреждён — записывать победителей нельзя."
)
_LOST_RACE = "Розыгрыш {period} только что закрыл другой администратор. Ничего не записано."


async def record_winners(
    db: AsyncSession,
    *,
    draw_id: int,
    winning_numbers: list[int],
    as_of: datetime | None = None,
) -> DrawOutcome:
    """Record the numbers drawn live on air and close the draw. Irreversible.

    This code does not pick anyone. It accepts what an external source produced and refuses what
    would contradict the ratified rule: **one person takes at most one prize**, and a winner is
    excluded from later rounds as a PERSON, not merely as a ticket — excluding only the ticket
    would let a two-ticket holder take two prizes.

    A number belonging to someone who already won is REFUSED rather than silently skipped. The
    audience heard that number announced; had we dropped it quietly, its absence from the published
    result could only be explained by "trust us". The refusal moves the discrepancy back into the
    room, where it can be resolved in the open.

    **All or nothing.** Any rejected position cancels the whole call: nothing is written, the draw
    stays ``committed``, the administrator fixes the list and sends it again. "409 means nothing
    changed" is literal here — facing an error on an irreversible operation, nobody should have to
    wonder whether half of it went through.

    **The serializer is the conditional UPDATE** (``WHERE status='committed'``), not a row lock:
    zero rows affected means another administrator closed the draw first. Unlike the freeze — where
    the row was terminal from birth and a lock proved dead under mutation — the transition here is
    real, so the guard is load-bearing and reddens a race test when removed.
    """
    as_of = as_of or utc_now()

    draw = (
        await db.execute(
            select(
                LotteryDraw.id, LotteryDraw.period, LotteryDraw.status, LotteryDraw.total_tickets
            ).where(LotteryDraw.id == draw_id)
        )
    ).one_or_none()
    if draw is None:
        await db.rollback()
        raise NotFoundException(_DRAW_NOT_FOUND.format(draw_id=draw_id))
    if draw.status != LotteryDrawStatus.COMMITTED.value:
        await db.rollback()
        raise ConflictException(_NOT_COMMITTED.format(period=draw.period, status=draw.status))

    seen: set[int] = set()
    for number in winning_numbers:
        if not 1 <= number <= draw.total_tickets:
            await db.rollback()
            raise BadRequestException(
                _NUMBER_OUT_OF_RANGE.format(number=number, total=draw.total_tickets)
            )
        if number in seen:
            await db.rollback()
            raise BadRequestException(_DUPLICATE_NUMBER.format(number=number))
        seen.add(number)

    rows = (
        await db.execute(
            select(LotteryTicket.ticket_number, LotteryTicket.user_id, User.first_name)
            .join(User, User.id == LotteryTicket.user_id)
            .where(
                LotteryTicket.draw_id == draw_id,
                LotteryTicket.ticket_number.in_(winning_numbers),
            )
        )
    ).all()
    by_number = {row.ticket_number: row for row in rows}
    if len(by_number) != len(seen):
        # In range yet absent: numbering is contiguous 1..total by construction, so this means the
        # snapshot lost rows after the freeze. Same class of alarm as the export guard.
        missing = sorted(seen - by_number.keys())
        await db.rollback()
        raise ConflictException(_SNAPSHOT_INCOMPLETE.format(period=draw.period, numbers=missing))

    winners: list[WinnerRow] = []
    owners: set[int] = set()
    for rank, number in enumerate(winning_numbers, start=1):
        row = by_number[number]
        if row.user_id in owners:
            await db.rollback()
            raise ConflictException(_ALREADY_WON.format(number=number))
        owners.add(row.user_id)
        winners.append(
            WinnerRow(
                ticket_number=number,
                prize_rank=rank,
                user_id=row.user_id,
                first_name=row.first_name,
            )
        )

    for winner in winners:
        await db.execute(
            update(LotteryTicket)
            .where(
                LotteryTicket.draw_id == draw_id,
                LotteryTicket.ticket_number == winner.ticket_number,
            )
            .values(is_winner=True, prize_rank=winner.prize_rank)
        )

    # `RETURNING` rather than `rowcount`: it says "did this row match" in a form the type checker
    # can see, and it keeps the check in the statement instead of in driver metadata.
    transition = (
        await db.execute(
            update(LotteryDraw)
            .where(
                LotteryDraw.id == draw_id,
                LotteryDraw.status == LotteryDrawStatus.COMMITTED.value,
            )
            .values(status=LotteryDrawStatus.DRAWN.value, drawn_at=as_of)
            .returning(LotteryDraw.id)
        )
    ).one_or_none()
    if transition is None:
        await db.rollback()
        raise ConflictException(_LOST_RACE.format(period=draw.period))

    await db.commit()
    return DrawOutcome(
        draw_id=draw_id,
        period=draw.period,
        status=LotteryDrawStatus.DRAWN.value,
        drawn_at=as_of,
        winners=tuple(winners),
    )
