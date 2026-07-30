"""Ticket issuance (Stage 5, step 2): a ticket is a record its owner keeps, not a recount.

One entry point, :func:`sync_tickets`, called from every place a member can become entitled to a
ticket. It is a "catch up what is owed" operation rather than three separate inserts: a member who
became a friend BEFORE filling the survey would otherwise lose the friend ticket forever, because
the moment that could have written it has passed and will not come again.

The eligibility predicates are NOT redefined here — :func:`~src.loyalty.lottery.has_survey` and
:func:`~src.loyalty.lottery.is_adult` are the same functions the freeze re-checks in step 3. Two
sets of conditions for one question are two things to drift apart.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.lottery import has_survey, is_adult
from src.loyalty.models import (
    CustomerProfile,
    LotteryTicket,
    LotteryTicketSource,
    LoyaltyStatus,
)

logger = logging.getLogger(__name__)

# No member holds more than this many tickets that have not played yet (ratified 2026-07-30).
CLEAN_TICKET_CEILING = 2

# Order matters: the one-time sources are handed out before the renewable one, so that hitting the
# ceiling costs a member the ticket they can earn again (a purchase) rather than one they cannot.
_ISSUE_PRIORITY = (
    LotteryTicketSource.SURVEY,
    LotteryTicketSource.FRIEND,
    LotteryTicketSource.ACTIVATION,
)

# Earned once in a lifetime for reaching a state, not for an event that repeats.
_ONE_TIME_SOURCES = frozenset({LotteryTicketSource.SURVEY, LotteryTicketSource.FRIEND})

# What may be issued from a call that merely observes the member's STATE — the safe default, and
# what every caller gets unless it says otherwise.
#
# ``activation`` is deliberately NOT here: it is earned by an EVENT, and state cannot tell you that
# the event happened. Defaulting to it handed a purchase ticket to a member who had only filled in
# their survey and never bought anything (caught by the matrix test). Only the path that witnessed
# the purchase may grant the ticket for it.
STATE_SOURCES = frozenset({LotteryTicketSource.SURVEY, LotteryTicketSource.FRIEND})
ACTIVATION_SOURCES = STATE_SOURCES | {LotteryTicketSource.ACTIVATION}


@dataclass(frozen=True)
class IssueOutcome:
    """What this call actually wrote — primitives only, safe to read after the transaction closes."""

    issued: tuple[str, ...]


_NOTHING = IssueOutcome(issued=())


async def sync_tickets(
    db: AsyncSession,
    *,
    user_id: int,
    sources: frozenset[LotteryTicketSource] | None = None,
    batch: str | None = None,
) -> IssueOutcome:
    """Issue whatever tickets ``user_id`` is owed and does not hold yet. Idempotent.

    **Takes the profile row lock itself.** Not "call me under a lock": this function has three
    callers, and the third — the one-off backfill — walks profiles outside any activation
    transaction. Issuance is a check-then-insert, so without a serializer a backfill racing a live
    activation of the same member would write a duplicate. Under a lock the caller already holds
    (both product call sites) re-taking it on the same row in the same transaction is a re-entrant
    no-op and costs nothing.

    ``sources`` restricts what may be issued and DEFAULTS to the state-derived ones. The renewable
    purchase ticket has to be asked for explicitly, by the one caller that witnessed the purchase —
    state alone cannot tell you a purchase happened, and defaulting to it gave a ticket to members
    who had only filled in a survey. The backfill narrows further still: a ticket for a purchase
    made a year ago motivates nothing (ratified 2026-07-30). ``batch`` marks the written rows so
    that a backfill, and only a backfill, can be undone.

    Eligibility is checked HERE and again at the stamp: a member who stops qualifying keeps the
    tickets they earned (they are theirs) but does not play with them.
    """
    allowed = sources if sources is not None else STATE_SOURCES

    profile = (
        await db.execute(
            select(CustomerProfile).where(CustomerProfile.user_id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if profile is None:
        return _NOTHING  # not a club member — the entry condition of the whole loyalty surface
    if not (has_survey(profile) and is_adult(profile)):
        # The survey is what buys entry into the draw (approved scope), so an incomplete profile
        # earns nothing at all — not even for a purchase.
        return _NOTHING

    # One round-trip, not two. Both questions — "which sources were EVER earned" and "which are
    # unplayed right now" — are answered by the same rows, and this runs inside the activation
    # transaction, holding the per-member lock the whole time. Measured: splitting it cost ~1.5 ms
    # of lock hold per activation for nothing.
    held = (
        await db.execute(
            select(LotteryTicket.source, LotteryTicket.draw_id).where(
                LotteryTicket.user_id == user_id
            )
        )
    ).all()
    owned_ever = {row.source for row in held}
    clean_sources = [row.source for row in held if row.draw_id is None]
    room = CLEAN_TICKET_CEILING - len(clean_sources)

    issued: list[str] = []
    for source in _ISSUE_PRIORITY:
        if room <= 0:
            break
        if source not in allowed:
            continue
        if not _is_due(source, profile=profile, owned_ever=owned_ever, clean=clean_sources):
            continue
        db.add(
            LotteryTicket(
                user_id=user_id,
                draw_id=None,
                ticket_number=None,
                source=source.value,
                backfill_batch=batch,
            )
        )
        issued.append(source.value)
        room -= 1

    if issued:
        await db.flush()
        logger.info("tickets issued user=%s sources=%s batch=%s", user_id, issued, batch)
    return IssueOutcome(issued=tuple(issued))


def _is_due(
    source: LotteryTicketSource,
    *,
    profile: CustomerProfile,
    owned_ever: set[str],
    clean: list[str],
) -> bool:
    """Whether this source owes the member a ticket right now.

    A one-time source is due while no ticket of it exists in ANY state — held, playing or spent.
    Keying this on the survey event instead would strand a member who filled gender and city before
    ticking 18+: the event is already written, a lone checkbox never writes another one
    (``service.py`` survey guard), and the ticket would never come.
    """
    if source in _ONE_TIME_SOURCES:
        if source is LotteryTicketSource.FRIEND and profile.status != LoyaltyStatus.BIOTACT_FRIEND:
            return False
        return source.value not in owned_ever
    # Renewable: one unplayed purchase ticket at a time, so buying twice before a draw does not
    # quietly outweigh the status that takes ten activations to earn.
    return source.value not in clean
