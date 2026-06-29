"""Loyalty domain services (Stage 2): point balance, reconciliation, QR activation."""

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.schemas import TelegramUser
from src.auth.service import get_or_create_user
from src.common.exceptions import ConflictException, NotFoundException
from src.loyalty.models import (
    CustomerProfile,
    LedgerReason,
    PointLedger,
    QrCode,
    QrCodeStatus,
)
from src.loyalty.pricing import compute_cashback, get_unit_price
from src.loyalty.schemas import ActivationStatus

# Stage 2 always accrues at the newbie rate; the friend tier (800 bps) arrives in Stage 3.
NEWBIE_RATE_BPS = 500
# Welcome bonus on the user's first-ever activation = half the base rate (→ 7.5% on row one).
WELCOME_RATE_BPS = 250


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
class ActivationOutcome:
    """Result of an activation — primitives only (safe to read after the txn commits)."""

    status: ActivationStatus
    points_awarded: int  # cashback (+ welcome) accrued by this code
    welcome_bonus: int  # welcome half-rate bonus; 0 when not the first activation / on replay
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
        await db.execute(
            pg_insert(CustomerProfile)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        locked = (
            await db.execute(
                select(CustomerProfile.lifetime_activations)
                .where(CustomerProfile.user_id == user_id)
                .with_for_update()
            )
        ).one()
        lifetime_before = int(locked[0])

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
                db, code=code, user_id=user_id, lifetime=lifetime_before
            )

        qr_code_id, product_id = claimed
        return await _accrue(
            db,
            user_id=user_id,
            qr_code_id=qr_code_id,
            product_id=product_id,
            lifetime_before=lifetime_before,
        )


async def _accrue(
    db: AsyncSession,
    *,
    user_id: int,
    qr_code_id: int,
    product_id: int,
    lifetime_before: int,
) -> ActivationOutcome:
    """Write the cashback (+ first-activation welcome) ledger rows and bump the counters."""
    unit_price = await get_unit_price(db, product_id)  # raises → claim rolls back with the txn
    cashback = compute_cashback(unit_price, NEWBIE_RATE_BPS)
    db.add(
        _earn_row(
            user_id,
            qr_code_id,
            product_id,
            unit_price,
            NEWBIE_RATE_BPS,
            cashback,
            LedgerReason.CASHBACK,
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

    # DB-time counter bump on the locked row (Core UPDATE → no ORM/Core staleness, no float clock).
    # first_activated_sku is a legacy Stage-1 column; COALESCE sets it once to the first product.
    await db.execute(
        update(CustomerProfile)
        .where(CustomerProfile.user_id == user_id)
        .values(
            lifetime_activations=CustomerProfile.lifetime_activations + 1,
            last_activation_at=func.now(),
            last_accrual_at=func.now(),
            first_activated_sku=func.coalesce(CustomerProfile.first_activated_sku, str(product_id)),
        )
        .execution_options(synchronize_session=False)
    )

    await db.flush()  # autoflush is off — write the rows; the partial UNIQUE backstops a dup here
    return ActivationOutcome(
        status=ActivationStatus.ACTIVATED,
        points_awarded=cashback + welcome,
        welcome_bonus=welcome,
        balance=await get_balance(db, user_id),
        lifetime_activations=lifetime_before + 1,
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


async def _resolve_unclaimed(
    db: AsyncSession,
    *,
    code: str,
    user_id: int,
    lifetime: int,
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
        points_awarded=int(earned),
        welcome_bonus=0,
        balance=await get_balance(db, user_id),
        lifetime_activations=lifetime,
    )
