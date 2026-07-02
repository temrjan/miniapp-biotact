"""Loyalty domain services (Stage 2): point balance, reconciliation, QR activation."""

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.schemas import TelegramUser
from src.auth.service import get_or_create_user, get_user_by_telegram_id
from src.common.exceptions import ConflictException, NotFoundException
from src.loyalty.models import (
    Consent,
    ConsentPurpose,
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
    LedgerReason,
    LoyaltyStatus,
    PointLedger,
    QrCode,
    QrCodeStatus,
)
from src.loyalty.pricing import compute_cashback, get_unit_price
from src.loyalty.schemas import ActivationStatus, RegistrationSource

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


@dataclass(frozen=True)
class LoyaltySummary:
    """Read-only loyalty snapshot for the mini-app profile card (Stage 3)."""

    tier: LoyaltyStatus
    balance: int  # SUM(point_ledger.amount); 1 балл = 1 сум
    lifetime_activations: int
    activations_to_friend: int  # remaining activations until "Друг"; 0 once a friend


async def get_loyalty_summary(session: AsyncSession, telegram_id: int) -> LoyaltySummary:
    """Read-only loyalty summary for a Telegram user — never writes (safe on a GET).

    An unknown user, or a known user whose profile is not created yet, returns the newbie
    default without inserting any row (so merely opening the profile card costs no writes).
    """
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return LoyaltySummary(
            tier=LoyaltyStatus.NEWBIE,
            balance=0,
            lifetime_activations=0,
            activations_to_friend=FRIEND_THRESHOLD,
        )

    profile = (
        await session.execute(
            select(CustomerProfile.status, CustomerProfile.lifetime_activations).where(
                CustomerProfile.user_id == user.id
            )
        )
    ).one_or_none()
    balance = await get_balance(session, user.id)
    if profile is None:
        return LoyaltySummary(
            tier=LoyaltyStatus.NEWBIE,
            balance=balance,
            lifetime_activations=0,
            activations_to_friend=FRIEND_THRESHOLD,
        )

    tier: LoyaltyStatus = profile[0]
    lifetime = int(profile[1])
    remaining = 0 if tier == LoyaltyStatus.BIOTACT_FRIEND else max(0, FRIEND_THRESHOLD - lifetime)
    return LoyaltySummary(
        tier=tier,
        balance=balance,
        lifetime_activations=lifetime,
        activations_to_friend=remaining,
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

        # Lock the profile row — the per-user serialization point for the guarded writes below.
        # This is what protects the consent/phone_shared dedup when the profile ALREADY exists (an
        # activate-first user): then the ON CONFLICT insert above is a no-op for both racers and does
        # NOT serialize them, so without this lock two concurrent consents could double-write.
        await db.execute(
            select(CustomerProfile.id).where(CustomerProfile.user_id == user_id).with_for_update()
        )

        # registration_source is set once (first writer wins): an activate-first user whose profile
        # was created without a source gets it backfilled here; a repeat call is a COALESCE no-op.
        await db.execute(
            update(CustomerProfile)
            .where(CustomerProfile.user_id == user_id)
            .values(
                registration_source=func.coalesce(CustomerProfile.registration_source, source.value)
            )
            .execution_options(synchronize_session=False)
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

        await db.flush()
        return ConsentOutcome(
            consent_recorded=consent_recorded, joined=joined, phone_shared=phone_shared
        )


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
