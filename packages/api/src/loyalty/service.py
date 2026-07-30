"""Loyalty domain services (Stage 2): point balance, reconciliation, QR activation."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.auth.schemas import TelegramUser
from src.auth.service import get_or_create_user, get_user_by_telegram_id
from src.common.exceptions import ConflictException, NotFoundException
from src.loyalty.models import (
    Consent,
    ConsentPurpose,
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
    Gender,
    LedgerReason,
    LotteryTicketSource,
    LoyaltyStatus,
    PointLedger,
    QrCode,
    QrCodeStatus,
)
from src.loyalty.pricing import compute_cashback, get_unit_price
from src.loyalty.schemas import (
    DEMOGRAPHIC_SURVEY_FIELDS,
    ActivationStatus,
    RegistrationSource,
    SurveyRequest,
)
from src.loyalty.tickets import ACTIVATION_SOURCES, sync_tickets
from src.orders.models import Order

logger = logging.getLogger(__name__)


async def _issue_tickets_safely(
    db: AsyncSession, *, user_id: int, sources: frozenset[LotteryTicketSource] | None = None
) -> None:
    """Issue owed tickets without letting the lottery subsystem undo what already happened.

    **The SAVEPOINT is the mechanism, and a bare ``try/except`` would not be one.** In PostgreSQL a
    failed statement aborts the whole transaction: afterwards only ``ROLLBACK`` is accepted, so
    catching the exception in Python would still lose the cashback at ``COMMIT``. ``begin_nested``
    rolls back the issuance alone and leaves the enclosing transaction healthy.

    Ordered LAST at both call sites for the same reason: money and the survey answer are already
    written when this runs, so the worst case is a member missing a ticket — never a member missing
    their cashback because a lottery row could not be written.

    Failure is loud in two places: ``CRITICAL`` here, and the ``eligible_without_survey_ticket``
    counter on the loyalty dashboard, which turns a silent skip into a visible number.
    ``SQLAlchemyError`` and not ``Exception``: a bug in our own predicates must surface, not be
    swallowed on the way to a green log.
    """
    try:
        async with db.begin_nested():
            await sync_tickets(db, user_id=user_id, sources=sources)
    except SQLAlchemyError:
        logger.critical(
            "ticket issuance failed for user_id=%s — the accrual is kept", user_id, exc_info=True
        )


# Cashback rate by tier (basis points): newbie 5%, friend 8%.
NEWBIE_RATE_BPS = 500
FRIEND_RATE_BPS = 800
# Welcome bonus on the user's first-ever activation = half the base rate (→ 7.5% on row one).
WELCOME_RATE_BPS = 250
# "Друг" status (Stage 3): granted once the lifetime activation count reaches the threshold.
FRIEND_THRESHOLD = 10
FRIEND_BONUS_POINTS = 5000


async def get_balance(session: AsyncSession, user_id: int) -> int:
    """Current point balance for a user = ``SUM(point_ledger.amount)`` (0 if none).

    The single source of truth — never a stored column. 1 балл = 1 сум.
    """
    result = await session.execute(
        select(func.coalesce(func.sum(PointLedger.amount), 0)).where(PointLedger.user_id == user_id)
    )
    return int(result.scalar_one())


def earn_row_reconciles(entry: PointLedger) -> bool:
    """Whether an earn row's ``amount`` matches ``compute_cashback(unit_price, rate_bps)``.

    Detects tampering, price/rate drift, or a rounding bug on accrual rows. Rows
    without a provenance snapshot (redeem/expiry/adjust — ``unit_price``/``rate_bps``
    NULL) are not reconcilable this way and return ``True`` (not applicable).
    """
    if entry.unit_price is None or entry.rate_bps is None:
        return True
    return entry.amount == compute_cashback(entry.unit_price, entry.rate_bps)


class InsufficientPointsError(Exception):
    """A redemption is refused because the available balance cannot cover it — nothing was written.

    Since partial redemption (Stage 6) points do NOT have to cover the whole order: a partial spend
    reserves points and the card collects the remainder. What is refused here is a spend that exceeds
    what is *available* — the balance minus what this user's other still-unpaid partial orders have
    already reserved (Model-1). ``balance`` carries that available figure; the mini-app uses it to
    say «не хватает N».
    """

    def __init__(self, *, balance: int, required: int) -> None:
        self.balance = balance
        self.required = required
        self.deficit = required - balance
        super().__init__(f"insufficient points: available={balance}, required={required}")


def _reserve_filters(
    *, user_id: int, exclude_order_id: int | None, created_after: datetime | None
) -> list[ColumnElement[bool]]:
    """Rows that pin points RIGHT NOW — the single definition of "a reservation", shared by callers.

    Both the money path (:func:`reserve_points`) and the read-only profile snapshot
    (:func:`get_loyalty_summary`) filter reservations from here, so the two can never drift apart.

    ``exclude_order_id`` drops the order currently being admitted so its own reservation is not
    counted against itself; it is compared explicitly rather than left as a bare ``!=`` against a
    possible ``None`` — the query must not depend on the ORM's null-aware rewriting to stay correct.

    ``created_after`` keeps only reservations younger than it. The money path passes ``None`` (a
    reservation pins points at ANY age — a stale one is often stale precisely because its invoice
    was paid, and ignoring it would spend points that are already committed). The profile passes the
    expiry cutoff, because a reservation past it is reclaimed by
    :func:`~src.orders.service.expire_stale_partials` on the customer's very next attempt.
    """
    filters = [
        Order.user_id == user_id,
        Order.payment_status.in_(("unpaid", "pending")),
        Order.points_applied > 0,
    ]
    if exclude_order_id is not None:
        filters.append(Order.id != exclude_order_id)
    if created_after is not None:
        filters.append(Order.created_at >= created_after)
    return filters


async def _reserved_points(
    db: AsyncSession,
    *,
    user_id: int,
    exclude_order_id: int | None = None,
    created_after: datetime | None = None,
) -> int:
    """Points this user has RESERVED but not yet spent — the sum pinned by still-open partial orders.

    A partial redemption (Model-1) does not write a ledger row at creation; the unpaid order row
    itself, carrying ``points_applied > 0``, IS the reservation. So the truly spendable balance is
    ``SUM(ledger) - Sum(points_applied of unpaid/pending redeeming orders)``. Which rows count is
    defined once in :func:`_reserve_filters`.
    """
    result = await db.execute(
        select(func.coalesce(func.sum(Order.points_applied), 0)).where(
            *_reserve_filters(
                user_id=user_id,
                exclude_order_id=exclude_order_id,
                created_after=created_after,
            )
        )
    )
    return int(result.scalar_one())


async def _live_reserve(
    db: AsyncSession, *, user_id: int, created_after: datetime
) -> tuple[int, datetime | None]:
    """This user's LIVE reservations in ONE read: points pinned, and when the oldest was created.

    Both numbers come from a single statement on purpose. Two separate reads could straddle a
    concurrent settlement and report a sum and an expiry that never existed together — harmless to
    money (this path writes nothing) but enough to show a customer a time that contradicts the
    amount beside it. ``None`` for the timestamp means nothing is pinned.

    The oldest reservation is the one to report: it is reclaimed first, so it is the earliest moment
    the customer can spend again.
    """
    result = await db.execute(
        select(
            func.coalesce(func.sum(Order.points_applied), 0),
            func.min(Order.created_at),
        ).where(
            *_reserve_filters(user_id=user_id, exclude_order_id=None, created_after=created_after)
        )
    )
    reserved, earliest = result.one()
    return int(reserved), earliest


async def reserve_points(
    db: AsyncSession, *, user_id: int, order_id: int, amount: int, settle_now: bool
) -> None:
    """Admit a redemption of ``amount`` against ``order_id`` under the per-user lock — reserve-aware.

    Runs inside the caller's ambient transaction and only flushes; the caller owns commit/rollback,
    so a refusal (or any later failure in the same request) leaves NO trace.

    Reserve model (Model-1, the fix for the burn incident): a PARTIAL order must not debit points at
    creation — if the customer abandons the card leg (Multicard sends no webhook on abandon) an
    eager debit would burn points that never bought anything. Instead the partial order RESERVES its
    points: no ledger row now, the pending order row is the reservation, and the real debit is
    written by :func:`commit_redeem` only once the card settles. ``settle_now`` is True only for a
    FULL-coverage order (points == total): it has no card leg, so the debit is written NOW, atomically
    — an all-points order can never be abandoned mid-payment.

    Admission is reserve-aware for BOTH shapes:

    * Per-user serializer: ``SELECT customer_profile … FOR UPDATE`` — the same row every accrual and
      redeem locks (:func:`activate_code`), so two orders racing one balance serialize here and the
      loser is refused, never overspends. Directly mutation-tested via the reserve race test.
    * ``available = SUM(ledger) - Sum(other unpaid/pending reservations)`` is computed under that lock
      and must cover ``amount``; the current order is excluded from the reservation sum (it is already
      flushed as pending) so it is not counted against itself.
    * Partial ``UNIQUE(order_id, reason)`` backstops the same order being debited twice when settled.

    A user without a ``customer_profile`` row has never earned a point (every earn path lazy-creates
    the profile before its first ledger row), so there is nothing to lock and available is 0 —
    deliberately NOT computed from a ledger that cannot have rows, so an out-of-band orphan row never
    becomes spendable un-serialized money.
    """
    if amount <= 0:
        # A zero redeem would mark an order "paid" for free; a negative one would CREDIT
        # points through the redeem path. Both are internal-misuse, never client-reachable.
        raise ValueError(f"redeem amount must be positive, got {amount}")
    profile_locked = (
        await db.execute(
            select(CustomerProfile.id).where(CustomerProfile.user_id == user_id).with_for_update()
        )
    ).one_or_none()
    balance = await get_balance(db, user_id) if profile_locked is not None else 0
    available = balance - await _reserved_points(db, user_id=user_id, exclude_order_id=order_id)
    if available < amount:
        raise InsufficientPointsError(balance=available, required=amount)
    if settle_now:
        # Full coverage — no card leg to wait for, so debit atomically at creation.
        db.add(
            PointLedger(
                user_id=user_id,
                amount=-amount,
                reason=LedgerReason.REDEEM,
                order_id=order_id,
            )
        )
        await db.flush()


async def commit_redeem(db: AsyncSession, *, user_id: int, order_id: int, amount: int) -> bool:
    """Write the real redeem debit for a PARTIAL order once its card leg has settled (Model-1).

    Called from :func:`~src.payments.lifecycle.process_payment_event` INSIDE the state-guarded
    success branch, so a late success on an already-cancelled/settled order never reaches here and
    cannot double-debit. Runs under the per-user ``customer_profile`` ``FOR UPDATE`` lock so it
    serialises against concurrent reserve admissions — a reserve check sees either the pre- or the
    post-commit state, never a torn one.

    Unlike :func:`reserve_points` this NEVER raises on a shortfall. The callback has already accepted
    real card money; refusing here would roll the settlement back and leave Multicard retrying a paid
    order forever. The reservation guaranteed sufficiency, so a shortfall is structurally impossible;
    if one is ever observed it is logged CRITICAL and the debit is SKIPPED rather than driving the
    balance negative. Returns whether the debit row was written.
    """
    (
        await db.execute(
            select(CustomerProfile.id).where(CustomerProfile.user_id == user_id).with_for_update()
        )
    ).one_or_none()
    balance = await get_balance(db, user_id)
    if balance < amount:
        logger.critical(
            "commit_redeem shortfall: order=%s user=%s balance=%s < amount=%s — debit SKIPPED, "
            "order left settled (reservation invariant violated)",
            order_id,
            user_id,
            balance,
            amount,
        )
        return False
    db.add(
        PointLedger(
            user_id=user_id,
            amount=-amount,
            reason=LedgerReason.REDEEM,
            order_id=order_id,
        )
    )
    await db.flush()
    return True


@dataclass(frozen=True)
class LoyaltySummary:
    """Read-only loyalty snapshot for the mini-app profile card (Stage 3)."""

    tier: LoyaltyStatus
    balance: int  # SUM(point_ledger.amount); 1 балл = 1 сум
    # What the customer can actually spend right now = balance minus their own LIVE reservations.
    # Kept beside ``balance`` rather than replacing it: reserved points are still the customer's —
    # they are pinned by an unfinished order, not gone — so the profile keeps showing the full
    # balance while every "can I spend it?" question is answered by this field.
    available: int
    # When the earliest live reservation expires; None when nothing is pinned. The wording it feeds
    # must be "try again after HH:MM", not "points come back at HH:MM": nothing happens on a timer,
    # the reservation is reclaimed by the customer's next attempt (orders.expire_stale_partials).
    reserved_until: datetime | None
    lifetime_activations: int
    activations_to_friend: int  # remaining activations until "Друг"; 0 once a friend
    is_member: bool  # True once a customer_profile row exists (the canonical "joined" signal)
    # Survey echo — lets the mini-app stop re-asking once the member saved the survey.
    gender: Gender | None
    age: int | None
    city: str | None
    # 18+ self-confirmation (Stage 5 lottery). Deliberately NOT part of the survey echo above: it
    # is a bool with a server_default, so "never asked" and "declined" are indistinguishable here —
    # eligibility grandfathers age>=18 rather than reading a missing answer into this flag.
    is_adult: bool


def _non_member_summary(balance: int) -> LoyaltySummary:
    """Newbie default for both non-member states (unknown user / user without a profile).

    ``available`` is 0, mirroring :func:`reserve_points`: without a ``customer_profile`` row there
    is nothing to lock on, so it refuses any redemption regardless of what the ledger says. Showing
    a spendable number here would promise what the money path would then deny.
    """
    return LoyaltySummary(
        tier=LoyaltyStatus.NEWBIE,
        balance=balance,
        available=0,
        reserved_until=None,
        lifetime_activations=0,
        activations_to_friend=FRIEND_THRESHOLD,
        is_member=False,
        gender=None,
        age=None,
        city=None,
        is_adult=False,
    )


async def get_loyalty_summary(session: AsyncSession, telegram_id: int) -> LoyaltySummary:
    """Read-only loyalty summary for a Telegram user — never writes (safe on a GET).

    An unknown user, or a known user whose profile is not created yet, returns the newbie
    default without inserting any row (so merely opening the profile card costs no writes).
    Survey fields (gender/age/city) are echoed as stored so the mini-app can show the
    "survey filled" state instead of re-asking; ``None`` means "not answered yet".

    Reports BOTH ``balance`` (everything the customer owns) and ``available`` (what a redemption
    would actually be admitted for today) so the mini-app can never offer points the money path is
    about to refuse. Deliberately does NOT reclaim expired reservations — a GET must not mutate —
    it merely stops counting them, which is what the customer's next attempt will make true.
    """
    # Local import: orders.service imports this module at module scope, so importing it back at the
    # top would be a cycle. The timeout is owned by the orders domain (it defines the reservation
    # lifecycle); re-declaring a second copy here is what would actually be dangerous, because it
    # could drift away from the value expire_stale_partials enforces.
    from src.orders.service import REDEEM_RESERVE_TIMEOUT

    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return _non_member_summary(balance=0)

    profile = (
        await session.execute(
            select(
                CustomerProfile.status,
                CustomerProfile.lifetime_activations,
                CustomerProfile.gender,
                CustomerProfile.age,
                CustomerProfile.city,
                CustomerProfile.is_adult,
            ).where(CustomerProfile.user_id == user.id)
        )
    ).one_or_none()
    balance = await get_balance(session, user.id)
    if profile is None:
        return _non_member_summary(balance)

    tier: LoyaltyStatus = profile.status
    lifetime = int(profile.lifetime_activations)
    remaining = 0 if tier == LoyaltyStatus.BIOTACT_FRIEND else max(0, FRIEND_THRESHOLD - lifetime)

    live_since = datetime.now(UTC) - REDEEM_RESERVE_TIMEOUT
    reserved, earliest = await _live_reserve(session, user_id=user.id, created_after=live_since)
    # Clamped, never a bare subtraction: a negative would be a data anomaly, and this response is
    # the whole profile card — letting one leak into a constrained field would 500 the card itself
    # instead of degrading one number (the failure mode LoyaltySummaryResponse already warns about).
    available = max(0, balance - reserved)
    reserved_until = None if earliest is None else earliest + REDEEM_RESERVE_TIMEOUT

    return LoyaltySummary(
        tier=tier,
        balance=balance,
        available=available,
        reserved_until=reserved_until,
        lifetime_activations=lifetime,
        activations_to_friend=remaining,
        is_member=True,
        gender=profile.gender,
        age=profile.age,
        city=profile.city,
        is_adult=bool(profile.is_adult),
    )


# Consent policy version recorded for `data_processing`; bump when the consent wording changes.
DATA_PROCESSING_CONSENT_VERSION = "1.0"


@dataclass(frozen=True)
class ConsentOutcome:
    """Result of a consent call — primitives only; each flag is True only if THIS call wrote it."""

    consent_recorded: bool  # a new data_processing consent row was inserted
    joined: bool  # this call created the customer_profile row (→ the `joined` event was written)
    phone_shared: bool  # this call wrote the `phone_shared` timeline event


async def record_consent(
    db: AsyncSession,
    *,
    telegram_user: TelegramUser,
    source: RegistrationSource,
) -> ConsentOutcome:
    """Record data-processing consent + entry lifecycle events, idempotently and race-safely.

    Runs in ONE explicit transaction (mirroring :func:`activate_code`): the user is resolved/created
    inside it so a brand-new user's row is FK-visible to the profile/consent/event writes. The
    guarded writes (consent dedup, ``phone_shared``) run under the per-user ``customer_profile``
    ``FOR UPDATE`` lock, so two concurrent consent calls serialize; ``joined`` is race-safe via the
    profile ``ON CONFLICT … RETURNING`` (the inserter is the winner). Every write shares this one
    transaction — a rollback leaves no orphan event.

    Idempotent on repeat: a second call by the same user writes nothing and returns all-False.
    """
    async with db.begin():
        user = await get_or_create_user(db, telegram_user)
        user_id = user.id

        # Lazy-create the profile race-safely; the inserter owns the one-time `joined` event.
        inserted = (
            await db.execute(
                pg_insert(CustomerProfile)
                .values(user_id=user_id)
                .on_conflict_do_nothing(index_elements=["user_id"])
                .returning(CustomerProfile.id)
            )
        ).one_or_none()
        joined = inserted is not None
        if joined:
            db.add(
                CustomerEvent(
                    user_id=user_id,
                    event_type=CustomerEventType.JOINED,
                    payload={"registration_source": source.value},
                )
            )

        # Primary per-user serializer: lock the profile row so the two guarded dedup writes below
        # (consent, phone_shared) serialize across concurrent calls. This is the SOLE serializer on
        # this path — the mandatory `UPDATE customer_profile SET registration_source` was moved to
        # AFTER the guards so this lock alone is directly mutation-tested (removing it reddens the
        # consent race test). The load-bearing money lock is in activate_code.
        await db.execute(
            select(CustomerProfile.id).where(CustomerProfile.user_id == user_id).with_for_update()
        )

        # data_processing consent — idempotent: write a row only when there is no active grant.
        consent_recorded = False
        active_consent = (
            await db.execute(
                select(Consent.id)
                .where(
                    Consent.user_id == user_id,
                    Consent.purpose == ConsentPurpose.DATA_PROCESSING,
                    Consent.granted.is_(True),
                    Consent.revoked_at.is_(None),
                )
                .limit(1)
            )
        ).first()
        if active_consent is None:
            db.add(
                Consent(
                    user_id=user_id,
                    purpose=ConsentPurpose.DATA_PROCESSING,
                    granted=True,
                    version=DATA_PROCESSING_CONSENT_VERSION,
                    source=source.value,
                )
            )
            consent_recorded = True

        # phone_shared — idempotent, only once the bot has stored a verified phone. Guard-then-insert
        # under the lock is race-safe (registration is one-shot per user). Payload is empty by design.
        phone_shared = False
        if user.phone_verified_at is not None:
            prior_phone_event = (
                await db.execute(
                    select(CustomerEvent.id)
                    .where(
                        CustomerEvent.user_id == user_id,
                        CustomerEvent.event_type == CustomerEventType.PHONE_SHARED,
                    )
                    .limit(1)
                )
            ).first()
            if prior_phone_event is None:
                db.add(
                    CustomerEvent(
                        user_id=user_id,
                        event_type=CustomerEventType.PHONE_SHARED,
                        payload={},
                    )
                )
                phone_shared = True

        # registration_source is set once (first writer wins): an activate-first user whose profile
        # was created without a source gets it backfilled here; a repeat call is a COALESCE no-op.
        # Runs AFTER the guards so the FOR UPDATE above is the sole (and testable) serializer.
        await db.execute(
            update(CustomerProfile)
            .where(CustomerProfile.user_id == user_id)
            .values(
                registration_source=func.coalesce(CustomerProfile.registration_source, source.value)
            )
            .execution_options(synchronize_session=False)
        )

        await db.flush()
        return ConsentOutcome(
            consent_recorded=consent_recorded, joined=joined, phone_shared=phone_shared
        )


@dataclass(frozen=True)
class SurveyOutcome:
    """Result of a survey call — primitives only; each field reflects only THIS call's write."""

    updated_fields: list[str]  # payload keys written by this call (sorted, for a stable response)
    survey_done: bool  # True only if this call wrote the one-time survey_done event


async def submit_survey(
    db: AsyncSession,
    *,
    telegram_user: TelegramUser,
    payload: SurveyRequest,
) -> SurveyOutcome:
    """Write progressive survey fields + the one-time ``survey_done`` event, race-safely.

    Requires an EXISTING member (a ``customer_profile`` row) — never creates one. A lazy profile
    create here would be a silent "join" that bypasses the mandatory consent checkbox, the same
    hole :func:`record_consent` closes for the entry flow. Present fields always overwrite the
    stored value (no ``COALESCE``): the survey is progressive, so a member may correct/extend it
    on a later call. ``survey_done`` reuses the guard-check-then-insert-under-lock primitive
    already proven by ``phone_shared`` above, and fires only for a call that carries at least one
    demographic field (:data:`DEMOGRAPHIC_SURVEY_FIELDS`) — the 18+ checkbox writes on its own but
    never spends the event.

    User resolution runs INSIDE the explicit transaction (mirroring :func:`activate_code` /
    :func:`record_consent`): a read before ``db.begin()`` would autobegin the session's
    transaction, and the later explicit ``begin()`` would then raise.
    """
    updates = payload.model_dump(exclude_none=True)
    updated_fields = sorted(updates)
    async with db.begin():
        user = await get_user_by_telegram_id(db, telegram_user.id)
        if user is None:
            raise NotFoundException("Not a club member")

        # SELECT resolves membership (None → not a member) AND is the primary per-user serializer:
        # the FOR UPDATE row lock makes concurrent survey calls serialize on the survey_done dedup
        # below. This is the SOLE serializer — the mandatory `UPDATE customer_profile SET **updates`
        # was moved to AFTER the guard so this lock alone is directly mutation-tested (removing it
        # reddens the survey race test). The load-bearing money lock is in activate_code.
        locked = (
            await db.execute(
                select(CustomerProfile.id)
                .where(CustomerProfile.user_id == user.id)
                .with_for_update()
            )
        ).one_or_none()
        if locked is None:
            raise NotFoundException("Not a club member")

        survey_done = False
        prior_survey_event = (
            await db.execute(
                select(CustomerEvent.id)
                .where(
                    CustomerEvent.user_id == user.id,
                    CustomerEvent.event_type == CustomerEventType.SURVEY_DONE,
                )
                .limit(1)
            )
        ).first()
        # A call carrying only `is_adult` writes the flag but leaves the event unspent: the member
        # has confirmed 18+, not answered the survey. Their later demographic call is the one that
        # marks it done (and the one the funnel counts).
        answers_something = not DEMOGRAPHIC_SURVEY_FIELDS.isdisjoint(updates)
        if answers_something and prior_survey_event is None:
            db.add(
                CustomerEvent(
                    user_id=user.id,
                    event_type=CustomerEventType.SURVEY_DONE,
                    payload={"fields": updated_fields},
                )
            )
            survey_done = True

        # Present fields overwrite the stored value (no COALESCE): the survey is progressive, so a
        # member may correct/extend it on a later call. Runs AFTER the survey_done guard so the
        # FOR UPDATE above is the sole (and testable) serializer.
        await db.execute(
            update(CustomerProfile)
            .where(CustomerProfile.user_id == user.id)
            .values(**updates)
            .execution_options(synchronize_session=False)
        )

        await db.flush()
        # Last in the transaction, after the answers are stored: the predicates must read the
        # values this call just wrote, or a member who completes their survey right now would be
        # judged by the profile they had a moment ago and leave without the ticket they earned.
        await _issue_tickets_safely(db, user_id=user.id)

        return SurveyOutcome(updated_fields=updated_fields, survey_done=survey_done)


@dataclass(frozen=True)
class ActivationOutcome:
    """Result of an activation — primitives only (safe to read after the txn commits)."""

    status: ActivationStatus
    tier: LoyaltyStatus  # loyalty tier AFTER this activation (may have just flipped to friend)
    points_awarded: int  # cashback + welcome + friend_bonus accrued by this code
    welcome_bonus: int  # welcome half-rate bonus; 0 when not the first activation / on replay
    friend_bonus: int  # one-time +5000 on the activation that flips to friend; 0 otherwise / replay
    balance: int  # current total balance after accrual
    lifetime_activations: int  # lifetime activations after this one


async def activate_code(
    db: AsyncSession,
    *,
    telegram_user: TelegramUser,
    code: str,
) -> ActivationOutcome:
    """Claim a QR code for a user and accrue cashback — idempotently and race-safely.

    The whole activation runs in ONE explicit transaction (``async with db.begin()``) so the
    per-user serialization lock (``SELECT customer_profile … FOR UPDATE``) is held across the
    claim, accrual and counter bump. Lock order is profile→code (consistent → deadlock-free).
    The user is resolved/created inside this transaction so a brand-new user's row is FK-visible
    to the ledger/profile writes.

    Outcomes:
      * code claimed        → cashback (+ welcome on the user's first-ever activation).
      * own code re-scanned  → idempotent replay rebuilt from the ledger (no new accrual).
      * code owned by other  → :class:`ConflictException` (409, no identity leak).
      * unknown code         → :class:`NotFoundException` (404).

    Raises :class:`~src.loyalty.pricing.UnknownProductError` (rolling the claim back via the
    transaction) if a claimed code's product cannot be priced — the caller logs + surfaces it.
    """
    async with db.begin():
        # NOTE: get_or_create_user is not ON CONFLICT-safe for a brand-new user (SELECT→INSERT);
        # two concurrent first-ever activations by an unseen user race UNIQUE(telegram_id) → one tx
        # aborts (claim rolls back — no double credit, retryable). Unreachable in the bot-first flow
        # (the user row exists before the mini-app opens). Pre-existing shared helper, out of scope.
        user = await get_or_create_user(db, telegram_user)
        user_id = user.id

        # Lazy-create the profile race-safely, then lock it (per-user serialization point).
        # The inserter owns the one-time `joined` event, so an activate-first user (no consent yet)
        # still gets `joined`. activate carries no registration source — the consent endpoint sets
        # `registration_source` and would find the profile already present (so it re-emits nothing).
        profile_inserted = (
            await db.execute(
                pg_insert(CustomerProfile)
                .values(user_id=user_id)
                .on_conflict_do_nothing(index_elements=["user_id"])
                .returning(CustomerProfile.id)
            )
        ).one_or_none()
        if profile_inserted is not None:
            db.add(
                CustomerEvent(
                    user_id=user_id,
                    event_type=CustomerEventType.JOINED,
                    payload={"registration_source": None},
                )
            )
        # Load-bearing per-user serializer (audit PR-A): this lock is what prevents a double welcome
        # / double +5000 when one user races TWO DIFFERENT codes across the first-activation or the
        # 9→10 boundary — the atomic claim below dedups a single code, not two different ones, and
        # the flip/welcome decision is made in Python from the values read here. Directly
        # mutation-tested by the deterministic race tests in test_loyalty_activation.py (removing
        # this `.with_for_update()` turns them red).
        locked = (
            await db.execute(
                select(CustomerProfile.lifetime_activations, CustomerProfile.status)
                .where(CustomerProfile.user_id == user_id)
                .with_for_update()
            )
        ).one()
        lifetime_before = int(locked[0])
        status_before: LoyaltyStatus = locked[1]

        # Atomic claim: only a row still 'generated' flips to 'used'. A concurrent claimer
        # sees 0 rows and falls through to the replay / conflict / not-found branch.
        claimed = (
            await db.execute(
                update(QrCode)
                .where(QrCode.code == code, QrCode.status == QrCodeStatus.GENERATED.value)
                .values(
                    status=QrCodeStatus.USED.value,
                    used_at=func.now(),
                    activated_by=user_id,
                )
                .returning(QrCode.id, QrCode.product_id)
                .execution_options(synchronize_session=False)
            )
        ).one_or_none()

        if claimed is None:
            return await _resolve_unclaimed(
                db, code=code, user_id=user_id, lifetime=lifetime_before, tier=status_before
            )

        qr_code_id, product_id = claimed
        return await _accrue(
            db,
            user_id=user_id,
            qr_code_id=qr_code_id,
            product_id=product_id,
            lifetime_before=lifetime_before,
            status_before=status_before,
        )


async def _accrue(
    db: AsyncSession,
    *,
    user_id: int,
    qr_code_id: int,
    product_id: int,
    lifetime_before: int,
    status_before: LoyaltyStatus,
) -> ActivationOutcome:
    """Write the cashback (+ welcome, + "Друг" bonus on the flip) rows and bump the counters.

    The status flip is an idempotent threshold check — a ``newbie`` whose lifetime count reaches
    :data:`FRIEND_THRESHOLD` (this activation included) becomes ``biotact_friend`` BEFORE the
    cashback is priced, so the 10th activation already earns at the friend rate. It is robust to a
    counter already past the threshold (backfill/re-ingest): such a user flips on the next
    activation. The flip is once per user (guarded by ``status == newbie``), so the +5000 bonus is
    granted exactly once; the partial ``UNIQUE(qr_code_id, reason)`` backstops a replay of the
    flipping code. All of this runs under the per-user ``customer_profile`` row lock held by the
    caller.
    """
    lifetime_after = lifetime_before + 1
    flips_to_friend = status_before == LoyaltyStatus.NEWBIE and lifetime_after >= FRIEND_THRESHOLD
    tier = LoyaltyStatus.BIOTACT_FRIEND if flips_to_friend else status_before
    rate_bps = FRIEND_RATE_BPS if tier == LoyaltyStatus.BIOTACT_FRIEND else NEWBIE_RATE_BPS

    unit_price = await get_unit_price(db, product_id)  # raises → claim rolls back with the txn
    cashback = compute_cashback(unit_price, rate_bps)
    db.add(
        _earn_row(
            user_id, qr_code_id, product_id, unit_price, rate_bps, cashback, LedgerReason.CASHBACK
        )
    )

    welcome = 0
    if lifetime_before == 0:  # first-ever activation → welcome (once per user)
        welcome = compute_cashback(unit_price, WELCOME_RATE_BPS)
        db.add(
            _earn_row(
                user_id,
                qr_code_id,
                product_id,
                unit_price,
                WELCOME_RATE_BPS,
                welcome,
                LedgerReason.WELCOME,
            )
        )

    friend_bonus = 0
    if flips_to_friend:  # crossing into "Друг" — one-time bonus + a milestone timeline event
        friend_bonus = FRIEND_BONUS_POINTS
        db.add(_friend_bonus_row(user_id, qr_code_id, friend_bonus))
        db.add(
            CustomerEvent(
                user_id=user_id,
                event_type=CustomerEventType.STATUS_FRIEND,
                payload={"lifetime_activations": lifetime_after, "bonus": friend_bonus},
            )
        )

    # Timeline event for the claim — written only here (real claim → ACTIVATED), never on a replay
    # (`_resolve_unclaimed` grants nothing) nor on conflict/not-found (the whole txn rolls back).
    db.add(
        CustomerEvent(
            user_id=user_id,
            event_type=CustomerEventType.QR_ACTIVATED,
            payload={
                "qr_code_id": qr_code_id,
                "product_id": product_id,
                "points_awarded": cashback + welcome + friend_bonus,
            },
        )
    )

    # DB-time counter bump on the locked row (Core UPDATE → no ORM/Core staleness, no float clock).
    # status is written as the (possibly flipped) tier — a no-op write of the same value when no
    # flip; the row is already locked, so it costs nothing. first_activated_product_id is set once
    # (COALESCE) to the first activated product's id.
    await db.execute(
        update(CustomerProfile)
        .where(CustomerProfile.user_id == user_id)
        .values(
            lifetime_activations=CustomerProfile.lifetime_activations + 1,
            status=tier,
            last_activation_at=func.now(),
            last_accrual_at=func.now(),
            first_activated_product_id=func.coalesce(
                CustomerProfile.first_activated_product_id, product_id
            ),
        )
        .execution_options(synchronize_session=False)
    )

    await db.flush()  # autoflush is off — write the rows; the partial UNIQUE backstops a dup here

    # One call covers BOTH triggers this activation can fire — the purchase itself and, on the 10th,
    # the crossing into "Друг" — because the flip is decided a few lines above, in this same
    # transaction under this same lock. There is no separate moment to hook the friend ticket onto.
    # A replay of one's own code never reaches here (`_resolve_unclaimed` grants nothing), which is
    # correct: re-scanning is not a purchase.
    await _issue_tickets_safely(db, user_id=user_id, sources=ACTIVATION_SOURCES)

    return ActivationOutcome(
        status=ActivationStatus.ACTIVATED,
        tier=tier,
        points_awarded=cashback + welcome + friend_bonus,
        welcome_bonus=welcome,
        friend_bonus=friend_bonus,
        balance=await get_balance(db, user_id),
        lifetime_activations=lifetime_after,
    )


def _earn_row(
    user_id: int,
    qr_code_id: int,
    product_id: int,
    unit_price: int,
    rate_bps: int,
    amount: int,
    reason: LedgerReason,
) -> PointLedger:
    """A self-contained earn row with its computation snapshot (Q4)."""
    return PointLedger(
        user_id=user_id,
        amount=amount,
        reason=reason,
        qr_code_id=qr_code_id,
        product_id=product_id,
        unit_price=unit_price,
        rate_bps=rate_bps,
    )


def _friend_bonus_row(user_id: int, qr_code_id: int, amount: int) -> PointLedger:
    """The one-time "Друг" bonus row (Stage 3).

    Tied to the triggering activation's ``qr_code_id`` so the partial ``UNIQUE(qr_code_id, reason)``
    backstops a double bonus on a replay of the flipping code. Carries no price snapshot
    (``unit_price``/``rate_bps`` NULL) — it is a flat milestone bonus, not cashback, so
    :func:`earn_row_reconciles` treats it as not-applicable.
    """
    return PointLedger(
        user_id=user_id,
        amount=amount,
        reason=LedgerReason.FRIEND_BONUS,
        qr_code_id=qr_code_id,
    )


async def _resolve_unclaimed(
    db: AsyncSession,
    *,
    code: str,
    user_id: int,
    lifetime: int,
    tier: LoyaltyStatus,
) -> ActivationOutcome:
    """The claim affected 0 rows — distinguish replay / conflict / unknown code."""
    existing = (await db.execute(select(QrCode).where(QrCode.code == code))).scalar_one_or_none()
    if existing is None:
        raise NotFoundException("Code not found")
    if existing.activated_by != user_id:
        raise ConflictException("Code already activated")  # generic — never reveal who

    # Same-user replay: rebuild the response from the ledger; grant nothing new.
    earned = (
        await db.execute(
            select(func.coalesce(func.sum(PointLedger.amount), 0)).where(
                PointLedger.qr_code_id == existing.id
            )
        )
    ).scalar_one()
    return ActivationOutcome(
        status=ActivationStatus.ALREADY_ACTIVATED,
        tier=tier,
        points_awarded=int(earned),
        welcome_bonus=0,
        friend_bonus=0,
        balance=await get_balance(db, user_id),
        lifetime_activations=lifetime,
    )
