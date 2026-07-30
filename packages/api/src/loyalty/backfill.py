"""One-off backfill of tickets for members who earned them before the feature existed (step 2).

Only the two one-time sources are handed out. A ticket for a purchase made a year ago motivates
nothing — the purchase ticket exists to reward the NEXT one — and handing them out retroactively
would fill the first draw with people who have not bought anything in months (ratified 2026-07-30).

There is no separate issuance logic here: this module walks the members and calls the very same
:func:`~src.loyalty.tickets.sync_tickets` the product paths call. A second implementation would be a
second thing to be wrong.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.models import CustomerProfile, LotteryTicket, LotteryTicketSource
from src.loyalty.tickets import sync_tickets

logger = logging.getLogger(__name__)

# The backfill grants what was earned by reaching a state, never the renewable purchase ticket.
BACKFILL_SOURCES = frozenset({LotteryTicketSource.SURVEY, LotteryTicketSource.FRIEND})


@dataclass(frozen=True)
class BackfillReport:
    """What a run did (or would do) — primitives only, safe to print after the session closes."""

    members_scanned: int
    members_granted: int
    by_source: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(self.by_source.items())) or "—"
        return (
            f"members scanned: {self.members_scanned} · granted to: {self.members_granted} · "
            f"tickets: {breakdown}"
        )


@dataclass(frozen=True)
class RevertReport:
    """Outcome of undoing a batch. ``kept_stamped`` is the part that CANNOT be undone."""

    deleted: int
    kept_stamped: int

    def render(self) -> str:
        return f"deleted: {self.deleted} · kept because already in a draw: {self.kept_stamped}"


async def run_backfill(
    db: AsyncSession, *, batch: str | None = None, dry_run: bool = True
) -> BackfillReport:
    """Grant the one-time tickets every eligible member is owed. Idempotent.

    ``dry_run`` executes the REAL issuance inside a SAVEPOINT and rolls it back, so the preview
    cannot drift from what an ``--apply`` run would do. A dry run that simulates the decision with
    its own copy of the rules is a dry run that lies the day the rules change.

    Members are processed one at a time under the per-member lock ``sync_tickets`` takes itself, so
    a live activation racing this walk cannot produce a duplicate.
    """
    user_ids = (await db.execute(select(CustomerProfile.user_id))).scalars().all()
    counts: Counter[str] = Counter()
    granted = 0

    savepoint = await db.begin_nested() if dry_run else None
    for user_id in user_ids:
        outcome = await sync_tickets(
            db, user_id=user_id, sources=BACKFILL_SOURCES, batch=None if dry_run else batch
        )
        if outcome.issued:
            granted += 1
            counts.update(outcome.issued)
    if savepoint is not None:
        await savepoint.rollback()

    report = BackfillReport(
        members_scanned=len(user_ids), members_granted=granted, by_source=dict(counts)
    )
    logger.info("backfill %s: %s", "dry-run" if dry_run else f"batch={batch}", report.render())
    return report


async def revert_backfill(db: AsyncSession, *, batch: str) -> RevertReport:
    """Undo a batch — but only the part that is still undoable.

    A ticket already stamped into a draw is NOT deleted: the published snapshot is taken over those
    exact rows, and removing one would break a hash we asked the public to verify. Such rows are
    counted and reported rather than silently left behind, because "the backfill was reverted" and
    "the backfill was reverted except for 240 rows" are different statements about the database.
    """
    stamped = (
        await db.execute(
            select(LotteryTicket.id).where(
                LotteryTicket.backfill_batch == batch, LotteryTicket.draw_id.is_not(None)
            )
        )
    ).all()
    deleted = (
        await db.execute(
            delete(LotteryTicket)
            .where(LotteryTicket.backfill_batch == batch, LotteryTicket.draw_id.is_(None))
            .returning(LotteryTicket.id)
        )
    ).all()

    report = RevertReport(deleted=len(deleted), kept_stamped=len(stamped))
    logger.info("backfill revert batch=%s: %s", batch, report.render())
    return report
