"""The draw (Stage 5): freezing tickets into it, cancelling it, and the canonical snapshot.

Fairness is commit-reveal, and the load-bearing half of it lives here. A ticket belongs to its owner
from the moment it is earned; the freeze STAMPS the clean ones into a draw — sequential numbers, one
published SHA-256 over the canonical list — and nothing downstream ever recomputes who took part.
Reads look at the stamped rows, because a recount depends on profiles that keep changing while the
published hash does not.

Eligibility is decided by :func:`has_survey` and :func:`is_adult` — the SAME two functions the
issuance path calls (:mod:`src.loyalty.tickets`). The freeze filters its candidates with them in
Python instead of re-expressing them as SQL inside one clever UPDATE: a second formulation of the
rules would sit exactly where drift is most expensive — deciding who takes part in a draw whose 18+
condition is a legal one. The price is one extra statement per freeze, paid once a month by an
administrator.

The winner is never chosen by this code: the numbers are drawn outside the system and recorded
afterwards (:func:`record_winners`). A freeze that turns out to be wrong is undone by
:func:`cancel_draw`, which releases every stamp and journals the exact snapshot bytes.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import cast

from sqlalchemy import Table, bindparam, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.service import get_user_by_telegram_id
from src.common.exceptions import BadRequestException, ConflictException, NotFoundException
from src.loyalty.models import (
    CustomerProfile,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
)
from src.users.models import User

# The legal floor for taking part, named because it is now asserted in two languages: here, and as
# SQL in the dashboard detector that counts members owed a ticket. A bare 18 in both places is two
# numbers that only look like one.
ADULT_AGE = 18


def is_adult(profile: CustomerProfile) -> bool:
    """18+ decision: a stored age decides; the checkbox speaks only when no age is stored.

    Ratified 2026-07-27. The two inputs can contradict each other — a member may tick "I am 18+"
    while their survey says 15 — and in that case the stored number wins: age is data the member
    gave about themselves, the checkbox is a claim, and admitting a minor on a claim is the one
    outcome that must not happen. Where no age exists the checkbox is all there is, and that is
    what gives it teeth (see the epic spec §Assumptions).
    """
    if profile.age is not None:
        return profile.age >= ADULT_AGE
    return bool(profile.is_adult)


def has_survey(profile: CustomerProfile) -> bool:
    """Survey completeness for the base ticket: gender AND city. Age is deliberately NOT required.

    Requiring age here would exclude an age-less profile before the 18+ decision is ever reached,
    which would leave the checkbox unable to change any outcome at all (blocker caught at Gate-1).
    """
    return profile.gender is not None and profile.city is not None


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
    draw_number: int
    total_tickets: int
    participant_hash: str
    committed_at: datetime
    conducted_on: date | None


@dataclass(frozen=True)
class CancelOutcome:
    """Result of cancelling a draw. ``released_tickets`` is what went back to the members."""

    draw_id: int
    draw_number: int
    cancelled_at: datetime
    released_tickets: int


@dataclass(frozen=True)
class ExportOutcome:
    """A draw ready to publish: the file's exact bytes, plus the label its filename carries.

    Deliberately three fields. The hash and the ticket count are NOT carried here — nothing consumes
    them (the body is the response, the number is the filename), and a field no caller reads is the
    kind of dead weight a mutation run flags. ``cancelled`` is read: the filename has to say so, and
    a published list nobody can tell apart from a live one is how a cancelled draw turns into an
    accusation.
    """

    draw_number: int
    body: str
    cancelled: bool


_DRAW_ALREADY_LIVE = (
    "Розыгрыш №{draw_number} ещё не закрыт: сначала запишите победителей или отмените его. "
    "Двух замороженных тиражей одновременно не бывает."
)
_NOTHING_TO_FREEZE = (
    "Замораживать нечего: ни одного чистого билета у пригодных участников. Билет есть только у "
    "того, кто заполнил анкету и подтвердил 18+."
)
_COUNT_MISMATCH = (
    "Подтверждение не совпало: вы указали {expected}, а к заморозке готово {actual} билетов. "
    "Ничего не заморожено — проверьте число и повторите."
)


async def eligible_clean_tickets(
    db: AsyncSession,
) -> list[tuple[int, int, str]]:
    """Clean tickets whose owner qualifies right now, as ``(ticket_id, user_id, source)``.

    Ordered by ticket id — the order they were issued in. Not by owner: sorting by member would put
    a person's two tickets on adjacent lines of the printed list, next to the last four digits of
    their Telegram id. Fairness is untouched either way, because the numbers are drawn outside this
    system; only the reading of the published list changes.

    The INNER JOIN drops tickets whose owner has no profile row — not a member, not eligible —
    which is the same answer the predicates would give, arrived at one round-trip earlier.

    Public because the admin page (step 4) has to show the count that the freeze will actually
    take. ``tickets_clean`` on the dashboard counts every clean ticket including the ones whose
    owner no longer qualifies; confirming THAT number would fail the freeze and teach administrators
    to press twice.
    """
    rows = (
        await db.execute(
            select(
                LotteryTicket.id,
                LotteryTicket.user_id,
                LotteryTicket.source,
                CustomerProfile,
            )
            .join(CustomerProfile, CustomerProfile.user_id == LotteryTicket.user_id)
            .where(LotteryTicket.draw_id.is_(None))
            .order_by(LotteryTicket.id)
        )
    ).all()
    return [
        (row.id, row.user_id, row.source)
        for row in rows
        if has_survey(row.CustomerProfile) and is_adult(row.CustomerProfile)
    ]


async def commit_draw(
    db: AsyncSession,
    *,
    expected_tickets: int,
    prize_label: str | None = None,
    conducted_on: date | None = None,
    as_of: datetime | None = None,
) -> CommitOutcome:
    """Stamp every eligible clean ticket into a new draw and publish its SHA-256.

    One transaction: the draw row, every stamp, the total and the hash land together or not at all —
    a half-written snapshot would be a published lie. The transaction is the session's ambient one
    (autobegun), NOT an explicit ``async with db.begin()``: :func:`require_admin` reads the user from
    the same session before this call, which already opened it. On any exception the caller rolls
    back (``get_db``), so a refusal leaves nothing behind.

    **Order is chosen so that no refusal burns a draw number.** Both refusals — nothing to freeze,
    and a confirmation that does not match — happen BEFORE the draw row is inserted, because the
    number comes from a database identity and a rolled-back insert consumes it anyway. An
    administrator retrying a mistyped confirmation should not watch the draws jump from №1 to №3.

    **``expected_tickets`` is the mechanical protection ratified for the button.** It is verified
    here rather than only in the dialog: otherwise one client is protected and the endpoint is not,
    while the operation stays irreversible. It doubles as optimistic concurrency — a ticket issued
    while the administrator was reading the page changes the number and the freeze refuses instead
    of silently capturing a different set.

    **What makes two live draws impossible is the partial unique index**
    ``uq_lottery_draws_one_committed``, not the check above it. The check gives the diagnosis; two
    presses microseconds apart both pass it, and the loser is stopped by the database — its
    ``IntegrityError`` is translated here into the same message, because two paths into one state
    that answer differently are a state nobody can debug from the outside.

    Eligibility is re-checked at the stamp, by the same functions issuance uses (ratified
    2026-07-30): a member who stopped qualifying keeps their tickets — they are theirs — but does not
    play with them, and the ticket stays clean, waiting.
    """
    as_of = as_of or utc_now()

    live = (
        await db.execute(
            select(LotteryDraw.draw_number).where(
                LotteryDraw.status == LotteryDrawStatus.COMMITTED.value
            )
        )
    ).one_or_none()
    if live is not None:
        await db.rollback()  # leave no open transaction behind a refusal
        raise ConflictException(_DRAW_ALREADY_LIVE.format(draw_number=live.draw_number))

    candidates = await eligible_clean_tickets(db)
    if not candidates:
        await db.rollback()
        raise ConflictException(_NOTHING_TO_FREEZE)
    if len(candidates) != expected_tickets:
        await db.rollback()
        raise ConflictException(
            _COUNT_MISMATCH.format(expected=expected_tickets, actual=len(candidates))
        )

    draw = LotteryDraw(
        prize_label=prize_label,
        conducted_on=conducted_on,
        status=LotteryDrawStatus.COMMITTED.value,
        total_tickets=len(candidates),
        committed_at=as_of,
        hash_published_at=as_of,
    )
    db.add(draw)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Lost the race by microseconds: the winner's committed draw already holds the index entry.
        # Without this catch the global handler (main.py) still answers 409, but with a generic body,
        # and the administrator would not learn WHICH conflict happened. The winner's number is only
        # readable after the rollback — before it, this session is still inside the failed
        # transaction — and it is read rather than guessed, so both paths into "a draw is already
        # live" answer with the same sentence and the same number.
        await db.rollback()
        winner = (
            await db.execute(
                select(LotteryDraw.draw_number).where(
                    LotteryDraw.status == LotteryDrawStatus.COMMITTED.value
                )
            )
        ).one_or_none()
        if winner is None:
            # Not the race we know about. Re-raising beats inventing a diagnosis for a conflict we
            # have not identified.
            raise
        raise ConflictException(_DRAW_ALREADY_LIVE.format(draw_number=winner.draw_number)) from exc

    rows = [
        (number, user_id, source) for number, (_, user_id, source) in enumerate(candidates, start=1)
    ]
    # One executemany against the TABLE, not the mapped class: handed an entity plus a list of
    # parameter dicts, the ORM reads it as "bulk update by primary key" and demands the PK in every
    # dict, which is a different statement from the one this needs. The candidate query never loaded
    # ticket entities either, so there is no in-memory state to keep in step.
    # ``__table__`` is a ``Table`` at runtime; the declarative stubs only promise ``FromClause``,
    # which ``update()`` does not accept. The cast states what the mapper guarantees.
    tickets = cast(Table, LotteryTicket.__table__)
    await db.execute(
        update(tickets)
        .where(tickets.c.id == bindparam("ticket_id"), tickets.c.draw_id.is_(None))
        .values(draw_id=draw.id, ticket_number=bindparam("number")),
        [
            {"ticket_id": ticket_id, "number": number}
            for number, (ticket_id, _, _) in enumerate(candidates, start=1)
        ],
    )

    draw.participant_hash = snapshot_hash(rows)
    await db.flush()
    await db.commit()
    return CommitOutcome(
        draw_id=draw.id,
        draw_number=draw.draw_number,
        total_tickets=draw.total_tickets,
        participant_hash=draw.participant_hash or "",
        committed_at=as_of,
        conducted_on=conducted_on,
    )


_NOT_CANCELLABLE = (
    "Розыгрыш №{draw_number} нельзя отменить: он в состоянии «{status}». Отменяется только "
    "замороженный тираж, до записи победителей."
)


async def cancel_draw(
    db: AsyncSession, *, draw_id: int, as_of: datetime | None = None
) -> CancelOutcome:
    """Release every stamp of a committed draw and journal what it contained.

    **Locks are taken in the same order as** :func:`record_winners` — tickets first, the draw row
    last. The opposite order is not a style question: cancelling and recording winners would then
    hold each other's locks (one has the tickets and wants the draw, the other has the draw and
    wants the tickets), PostgreSQL would break the deadlock by killing one, and an administrator
    would get a 500 where the honest answer is a 409.

    **The serializer is the conditional UPDATE at the end** (``WHERE status='committed'``), copied
    from :func:`record_winners`: zero rows means the draw was drawn or cancelled while we worked, and
    the whole transaction — including the released stamps — rolls back. "409 means nothing changed"
    is literal, and it is the rollback that makes it so rather than our care.

    **The journal keeps the canonical TEXT**, the exact bytes the published SHA-256 was taken over,
    so anyone can re-hash it and check it against ``participant_hash`` in the same row. A trail that
    cannot be verified only asserts that we kept something. The bytes are produced by the same
    function the export serves, so there is no second serialization to drift.

    It does NOT re-verify that the bytes still match the published hash. A draw whose rows were
    edited after the freeze is exactly the one that must remain cancellable — refusing would trap
    the system in a corrupted state — and the mismatch, preserved verbatim, is itself the evidence.

    ``is_winner``/``prize_rank`` are not reset: winners are written in the same transaction that
    moves a draw to ``drawn``, and that state is refused above. Resetting them would be protection
    against a state that cannot exist.
    """
    as_of = as_of or utc_now()

    draw = (
        await db.execute(
            select(LotteryDraw.draw_number, LotteryDraw.status).where(LotteryDraw.id == draw_id)
        )
    ).one_or_none()
    if draw is None:
        await db.rollback()
        raise NotFoundException(_DRAW_NOT_FOUND.format(draw_id=draw_id))
    if draw.status != LotteryDrawStatus.COMMITTED.value:
        await db.rollback()
        raise ConflictException(
            _NOT_CANCELLABLE.format(draw_number=draw.draw_number, status=draw.status)
        )

    stamped = (
        await db.execute(
            select(LotteryTicket.ticket_number, LotteryTicket.user_id, LotteryTicket.source).where(
                LotteryTicket.draw_id == draw_id
            )
        )
    ).all()
    rows: list[tuple[int, int, str]] = [
        (r.ticket_number, r.user_id, r.source) for r in stamped if r.ticket_number is not None
    ]
    journal = canonical_snapshot(rows)

    await db.execute(
        update(LotteryTicket)
        .where(LotteryTicket.draw_id == draw_id)
        .values(draw_id=None, ticket_number=None)
    )

    transition = (
        await db.execute(
            update(LotteryDraw)
            .where(
                LotteryDraw.id == draw_id,
                LotteryDraw.status == LotteryDrawStatus.COMMITTED.value,
            )
            .values(
                status=LotteryDrawStatus.CANCELLED.value,
                cancelled_at=as_of,
                cancelled_snapshot=journal,
            )
            .returning(LotteryDraw.id)
        )
    ).one_or_none()
    if transition is None:
        await db.rollback()
        raise ConflictException(_LOST_RACE.format(draw_number=draw.draw_number))

    await db.commit()
    return CancelOutcome(
        draw_id=draw_id,
        draw_number=draw.draw_number,
        cancelled_at=as_of,
        # The rows we just read are the rows we just released: the draw is committed, and the only
        # two operations that touch these stamps both serialize on the draw row below.
        released_tickets=len(stamped),
    )


_DATE_ON_CANCELLED = "Розыгрыш №{draw_number} отменён — менять его дату проведения нечего."


async def set_conducted_on(
    db: AsyncSession, *, draw_id: int, conducted_on: date | None
) -> tuple[int, date | None]:
    """Set or clear the announced date of a draw; returns ``(draw_number, conducted_on)``.

    The date is informational and lives outside the published hash on purpose, which is precisely
    what makes it editable at all: postponing a draw cannot break a fingerprint already published.
    Allowed while a draw is committed or drawn — a date is announced, then sometimes moved — and
    refused on a cancelled one, where there is no event left to date.
    """
    draw = (
        await db.execute(
            select(LotteryDraw.draw_number, LotteryDraw.status).where(LotteryDraw.id == draw_id)
        )
    ).one_or_none()
    if draw is None:
        await db.rollback()
        raise NotFoundException(_DRAW_NOT_FOUND.format(draw_id=draw_id))
    if draw.status == LotteryDrawStatus.CANCELLED.value:
        await db.rollback()
        raise ConflictException(_DATE_ON_CANCELLED.format(draw_number=draw.draw_number))

    await db.execute(
        update(LotteryDraw).where(LotteryDraw.id == draw_id).values(conducted_on=conducted_on)
    )
    await db.commit()
    return draw.draw_number, conducted_on


_DRAW_NOT_FOUND = "Розыгрыш {draw_id} не найден"
_SNAPSHOT_TAMPERED = (
    "Снимок розыгрыша №{draw_number} не сходится с опубликованным хешем: строки билетов изменились "
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

    **A cancelled draw is served from its journal.** Cancelling releases the stamps, so recomputing
    from ``lottery_tickets`` would return an empty body for a draw that certainly had participants —
    the export of a cancelled draw would look like tampering the moment it is most needed. The
    journal holds the exact bytes the hash was taken over, so the same verification still applies to
    the same claim.
    """
    draw = (
        await db.execute(
            select(
                LotteryDraw.draw_number,
                LotteryDraw.participant_hash,
                LotteryDraw.status,
                LotteryDraw.cancelled_snapshot,
            ).where(LotteryDraw.id == draw_id)
        )
    ).one_or_none()
    if draw is None:
        raise NotFoundException(_DRAW_NOT_FOUND.format(draw_id=draw_id))

    cancelled = draw.status == LotteryDrawStatus.CANCELLED.value
    if cancelled:
        body = draw.cancelled_snapshot or ""
    else:
        result = await db.execute(
            select(LotteryTicket.ticket_number, LotteryTicket.user_id, LotteryTicket.source).where(
                LotteryTicket.draw_id == draw_id
            )
        )
        rows: list[tuple[int, int, str]] = []
        for r in result:
            if r.ticket_number is None:
                # Stamped into this draw yet unnumbered: the snapshot lost the very identifier the
                # published hash is taken over. Same alarm as the hash mismatch below, and the same
                # refusal — a list we cannot number is, from outside, indistinguishable from a faked
                # one.
                raise ConflictException(_SNAPSHOT_TAMPERED.format(draw_number=draw.draw_number))
            rows.append((r.ticket_number, r.user_id, r.source))
        body = canonical_snapshot(rows)

    if sha256(body.encode("utf-8")).hexdigest() != draw.participant_hash:
        raise ConflictException(_SNAPSHOT_TAMPERED.format(draw_number=draw.draw_number))

    return ExportOutcome(draw_number=draw.draw_number, body=body, cancelled=cancelled)


@dataclass(frozen=True)
class CurrentDrawInfo:
    """The latest draw as the admin page shows it — primitives only, safe after the session ends."""

    draw_id: int
    draw_number: int
    status: str
    total_tickets: int
    participant_hash: str | None
    conducted_on: date | None
    prize_label: str | None
    committed_at: datetime | None
    drawn_at: datetime | None
    cancelled_at: datetime | None
    winners: tuple["WinnerRow", ...]  # WinnerRow is defined further down this module


@dataclass(frozen=True)
class CurrentOutcome:
    """What the draw page stands on: the number to confirm, and the draw to act on."""

    eligible_tickets: int
    clean_tickets: int
    draw: CurrentDrawInfo | None
    # Filled whenever the latest draw is not itself drawn: the winners of the previous draw must
    # stay reachable after the next freeze (Gate-2 БЛОКЕР-1).
    last_drawn: CurrentDrawInfo | None


async def _current_draw_info(db: AsyncSession, draw: LotteryDraw) -> CurrentDrawInfo:
    """One draw as the admin page shows it; winners are read from the ticket rows, not remembered
    from a mutation response — the page must survive a reload."""
    winners: tuple[WinnerRow, ...] = ()
    if draw.status == LotteryDrawStatus.DRAWN.value:
        rows = (
            await db.execute(
                select(
                    LotteryTicket.ticket_number,
                    LotteryTicket.prize_rank,
                    User.id,
                    User.first_name,
                )
                .join(User, User.id == LotteryTicket.user_id)
                .where(LotteryTicket.draw_id == draw.id, LotteryTicket.is_winner.is_(True))
                .order_by(LotteryTicket.prize_rank)
            )
        ).all()
        winners = tuple(
            WinnerRow(
                ticket_number=r.ticket_number,
                prize_rank=r.prize_rank,
                user_id=r.id,
                first_name=r.first_name,
            )
            for r in rows
        )
    return CurrentDrawInfo(
        draw_id=draw.id,
        draw_number=draw.draw_number,
        status=draw.status,
        total_tickets=draw.total_tickets,
        participant_hash=draw.participant_hash,
        conducted_on=draw.conducted_on,
        prize_label=draw.prize_label,
        committed_at=draw.committed_at,
        drawn_at=draw.drawn_at,
        cancelled_at=draw.cancelled_at,
        winners=winners,
    )


async def get_current_draw(db: AsyncSession) -> CurrentOutcome:
    """The draw page's single read: the post-filter count plus the LATEST draw of any status.

    ``eligible_tickets`` comes from :func:`eligible_clean_tickets` itself — the very function the
    freeze stamps — so the number the administrator types and the number the freeze takes cannot
    disagree by construction. ``clean_tickets`` is every clean ticket including the ones whose owner
    no longer qualifies; shown next to the eligible count so the difference is visible rather than
    mysterious (P-3).

    The draw is the latest BY NUMBER, of ANY status — including cancelled (P-2). The member card
    skips cancelled draws because it has nothing to say about them; the administrator, on the
    contrary, must publish the cancellation the moment it happens, and needs the cancelled draw's
    number and hash for exactly that post. Winners are read from the ticket rows, not remembered
    from a mutation response: the page must survive a reload.

    ⚠️ Known cost: ``eligible_tickets`` materializes the full candidate list to count it (the same
    price the freeze pays — measured at 576 ms on 20 000 tickets), and React Query refetches on
    window focus. Irrelevant at the current base (7 tickets); when it stops being irrelevant, the
    answer is a cheaper way to ask the SAME predicates — never an SQL copy of them (P-3).
    """
    eligible = await eligible_clean_tickets(db)
    clean = int(
        (
            await db.execute(
                select(func.count())
                .select_from(LotteryTicket)
                .where(LotteryTicket.draw_id.is_(None))
            )
        ).scalar_one()
    )

    latest = (
        await db.execute(select(LotteryDraw).order_by(LotteryDraw.draw_number.desc()).limit(1))
    ).scalar_one_or_none()

    info = await _current_draw_info(db, latest) if latest is not None else None

    # The last DRAWN draw travels alongside whenever it is not the latest one: freezing draw №2
    # must not make the winners of draw №1 unreachable anywhere in the admin — prizes are handed
    # out for days after the next freeze (Gate-2 БЛОКЕР-1).
    last_drawn: CurrentDrawInfo | None = None
    if latest is None or latest.status != LotteryDrawStatus.DRAWN.value:
        drawn = (
            await db.execute(
                select(LotteryDraw)
                .where(LotteryDraw.status == LotteryDrawStatus.DRAWN.value)
                .order_by(LotteryDraw.draw_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if drawn is not None:
            last_drawn = await _current_draw_info(db, drawn)

    return CurrentOutcome(
        eligible_tickets=len(eligible), clean_tickets=clean, draw=info, last_drawn=last_drawn
    )


@dataclass(frozen=True)
class PrintRow:
    """One line of the printed list: the number a member reads, the four digits they find themselves by."""

    ticket_number: int
    telegram_last4: str


@dataclass(frozen=True)
class PrintListOutcome:
    """The print page's data — primitives only, safe after the session ends."""

    draw_number: int
    conducted_on: date | None
    total_tickets: int
    rows: tuple[PrintRow, ...]


_PRINT_OF_CANCELLED = (
    "Розыгрыш №{draw_number} отменён — печатный список недоступен. Состав этого тиража — "
    "в журнале выгрузки (draw-{draw_number}-cancelled.txt)."
)


async def get_print_list(db: AsyncSession, *, draw_id: int) -> PrintListOutcome:
    """The printed list of a draw: ticket numbers ascending, each with the owner's last four digits.

    The four digits are computed HERE, zero-padded — ``42`` and ``0042`` are the same column a member
    scans with their eyes, and without the padding they print as two different numbers. The full
    ``telegram_id`` never leaves the server: the digits are a projection, and they are all the
    public column needs.

    A cancelled draw REFUSES (409). Cancelling releases the stamps, so the rows would come back
    empty — and an empty printed list reads from outside as a corrupted one, not as a cancelled
    draw. The journal served by the export endpoint is the list of a cancelled draw; the canonical
    text there carries no last-4, by the ratified hash contract.
    """
    draw = (
        await db.execute(
            select(
                LotteryDraw.draw_number,
                LotteryDraw.status,
                LotteryDraw.conducted_on,
                LotteryDraw.total_tickets,
            ).where(LotteryDraw.id == draw_id)
        )
    ).one_or_none()
    if draw is None:
        raise NotFoundException(_DRAW_NOT_FOUND.format(draw_id=draw_id))
    if draw.status == LotteryDrawStatus.CANCELLED.value:
        raise ConflictException(_PRINT_OF_CANCELLED.format(draw_number=draw.draw_number))

    result = await db.execute(
        select(LotteryTicket.ticket_number, User.telegram_id)
        .join(User, User.id == LotteryTicket.user_id)
        .where(LotteryTicket.draw_id == draw_id)
        .order_by(LotteryTicket.ticket_number)
    )
    rows = tuple(
        PrintRow(ticket_number=r.ticket_number, telegram_last4=f"{r.telegram_id % 10000:04d}")
        for r in result
    )
    return PrintListOutcome(
        draw_number=draw.draw_number,
        conducted_on=draw.conducted_on,
        total_tickets=draw.total_tickets,
        rows=rows,
    )


@dataclass(frozen=True)
class DrawResult:
    """How a finished draw ended for one member."""

    draw_number: int
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
    asserted in the tests: ``draw_number is not None`` exactly when ``is_frozen``; ``prize_label``
    is non-null only when frozen; after a finished draw the top block is always prospective.
    """

    tickets: int
    is_frozen: bool
    draw_number: int | None  # the draw this card describes; the "№" belongs to the client's i18n
    prize_label: str | None
    result: DrawResult | None
    # The member's own ticket numbers in the draw the card is showing — ascending, and present for a
    # DRAWN draw too. The published list arrives after the draw, and finding yourself in it is the
    # entire reason these numbers are shown at all.
    numbers: tuple[int, ...]
    # Tickets held right now and in no draw: what was earned after a freeze lands here too.
    clean_tickets: int
    # Why those held tickets will not play: "survey" | "age" | None. A code, not a sentence — the
    # mini-app is bilingual and the language lives on the client.
    not_playing_reason: str | None


# Checked in this order: the survey is needed either way, so pointing at the 18+ box first would
# send a member to fix the second thing while the first still blocks them.
REASON_SURVEY = "survey"
REASON_AGE = "age"


_NO_TICKETS = MyTicketsOutcome(
    tickets=0,
    is_frozen=False,
    draw_number=None,
    prize_label=None,
    result=None,
    numbers=(),
    clean_tickets=0,
    not_playing_reason=None,
)


async def _own_numbers(db: AsyncSession, *, draw_id: int, user_id: int) -> tuple[int, ...]:
    """This member's ticket numbers inside one draw, ascending.

    Ascending because the member reads them against the published list, which is sorted that way;
    any other order turns a check into a search. Scoped by ``user_id`` — the caller derives it from
    the signed initData, so another member's numbers are unreachable rather than merely forbidden.
    """
    rows = (
        await db.execute(
            select(LotteryTicket.ticket_number)
            .where(LotteryTicket.draw_id == draw_id, LotteryTicket.user_id == user_id)
            .order_by(LotteryTicket.ticket_number)
        )
    ).scalars()
    # ``ticket_number`` is nullable in the schema (a clean ticket has none), and a stamped row always
    # carries one — the same UPDATE writes both. The guard is here for the type checker, not for a
    # reachable state: unlike the export, a member's card has no artifact to defend, so a row that
    # somehow lost its number is skipped rather than turned into an error on their screen.
    return tuple(number for number in rows if number is not None)


async def _not_playing_reason(db: AsyncSession, *, user_id: int) -> str | None:
    """Which predicate keeps this member's held tickets out of the next draw, if any.

    The same two functions issuance and the stamp call — a third formulation of the rules is what
    this stage refuses to grow (step 3, P-3). Asked only when the member actually holds tickets:
    "you have none" is a different conversation, and the card already has a line for it.
    """
    profile = (
        await db.execute(select(CustomerProfile).where(CustomerProfile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None or not has_survey(profile):
        return REASON_SURVEY
    if not is_adult(profile):
        return REASON_AGE
    return None


async def get_my_tickets(db: AsyncSession, *, telegram_id: int) -> MyTicketsOutcome:
    """One member's participation: their stake in the live draw, or the tickets they hold now.

    Read-only. Never creates a ``users`` or ``customer_profile`` row — merely opening the card must
    cost nothing, the same rule :func:`~src.loyalty.service.get_loyalty_summary` follows. A user who
    never joined is not an error: they get zero tickets and the card explains how to earn one.

    **Nothing is recomputed from the profile any more.** A ticket is a row the member owns, so both
    numbers below are counts of rows: stamped into the live draw, or still clean. The distinction is
    reported through ``is_frozen`` because "2 tickets" quietly becoming "1" without it reads to a
    member as tickets being taken away.

    **Cancelled draws are skipped entirely.** Taking simply the latest draw would let a cancelled one
    hide the result of the draw before it — the member would open the card after a cancellation and
    see nothing where their prize used to be.

    ⚠️ Known gap until step 5 rewrites the card: while a draw is live the top block shows the stake
    in it and NOT the tickets earned since the freeze, which do exist and are clean. Adding a field
    for them here would ship a field nothing renders.

    No ``as_of`` any more: it existed to date the 30-day activation window, and with the window gone
    nothing in this read depends on the clock.
    """
    user = await get_user_by_telegram_id(db, telegram_id)
    if user is None:
        return _NO_TICKETS

    latest = (
        await db.execute(
            select(
                LotteryDraw.id,
                LotteryDraw.draw_number,
                LotteryDraw.status,
                LotteryDraw.prize_label,
            )
            .where(LotteryDraw.status != LotteryDrawStatus.CANCELLED.value)
            .order_by(LotteryDraw.draw_number.desc())
            .limit(1)
        )
    ).one_or_none()

    # Held right now and in no draw. Needed in every branch below, including the frozen one: a ticket
    # earned a second after the freeze is real, clean, and until this step was invisible.
    clean = int(
        (
            await db.execute(
                select(func.count())
                .select_from(LotteryTicket)
                .where(LotteryTicket.user_id == user.id, LotteryTicket.draw_id.is_(None))
            )
        ).scalar_one()
    )
    reason = await _not_playing_reason(db, user_id=user.id) if clean else None

    if latest is not None and latest.status == LotteryDrawStatus.COMMITTED.value:
        numbers = await _own_numbers(db, draw_id=latest.id, user_id=user.id)
        return MyTicketsOutcome(
            tickets=len(numbers),
            is_frozen=True,
            draw_number=latest.draw_number,
            prize_label=latest.prize_label,
            result=None,
            numbers=numbers,
            clean_tickets=clean,
            not_playing_reason=reason,
        )

    result: DrawResult | None = None
    numbers = ()
    if latest is not None and latest.status == LotteryDrawStatus.DRAWN.value:
        numbers = await _own_numbers(db, draw_id=latest.id, user_id=user.id)
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
                draw_number=latest.draw_number,
                tickets=len(rows),
                won=any(r.is_winner for r in rows),
                prize_rank=min(ranks, default=None),
                prize_label=latest.prize_label,
            )

    return MyTicketsOutcome(
        tickets=clean,
        is_frozen=False,
        draw_number=None,
        prize_label=None,
        result=result,
        numbers=numbers,
        clean_tickets=clean,
        not_playing_reason=reason,
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
    draw_number: int
    status: str
    drawn_at: datetime
    winners: tuple[WinnerRow, ...]


_NOT_COMMITTED = (
    "Розыгрыш №{draw_number} нельзя разыграть: он в состоянии «{status}». Разыгрывается только "
    "замороженный тираж, и только один раз."
)
_NUMBER_OUT_OF_RANGE = "Номер {number} вне диапазона 1..{total} — проверьте, что назвали."
_DUPLICATE_NUMBER = "Номер {number} назван дважды."
_ALREADY_WON = (
    "Номер {number} принадлежит участнику, который уже выиграл в этом розыгрыше. По правилам один "
    "человек забирает один приз — крутите барабан ещё раз. Ничего не записано."
)
_SNAPSHOT_INCOMPLETE = (
    "В снимке розыгрыша №{draw_number} нет билетов с номерами {numbers}, хотя они в диапазоне. "
    "Снимок повреждён — записывать победителей нельзя."
)
# One message for two outcomes on purpose: by the time we read the row again it is either drawn or
# cancelled, and both mean the same thing to the administrator holding a list of numbers — this
# call wrote nothing. Naming only one of them would state a fact we did not check.
_LOST_RACE = (
    "Розыгрыш №{draw_number} только что закрыл или отменил другой администратор. "
    "Ничего не записано."
)


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
                LotteryDraw.id,
                LotteryDraw.draw_number,
                LotteryDraw.status,
                LotteryDraw.total_tickets,
            ).where(LotteryDraw.id == draw_id)
        )
    ).one_or_none()
    if draw is None:
        await db.rollback()
        raise NotFoundException(_DRAW_NOT_FOUND.format(draw_id=draw_id))
    if draw.status != LotteryDrawStatus.COMMITTED.value:
        await db.rollback()
        raise ConflictException(
            _NOT_COMMITTED.format(draw_number=draw.draw_number, status=draw.status)
        )

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
        raise ConflictException(
            _SNAPSHOT_INCOMPLETE.format(draw_number=draw.draw_number, numbers=missing)
        )

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
        raise ConflictException(_LOST_RACE.format(draw_number=draw.draw_number))

    await db.commit()
    return DrawOutcome(
        draw_id=draw_id,
        draw_number=draw.draw_number,
        status=LotteryDrawStatus.DRAWN.value,
        drawn_at=as_of,
        winners=tuple(winners),
    )
