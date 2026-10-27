"""Payment lifecycle — idempotent, race-safe, attempt-aware handling of Multicard events (PR-6.1).

The success callback (MD5) and the status webhook (SHA1) both funnel through
:func:`process_payment_event` — one path, not two copies. It guarantees, on the money path:

* **Receipts and settlement.** ``INSERT ... ON CONFLICT DO NOTHING`` preserves the first receipt.
  A recognized webhook-success hint may precede the first authoritative callback; only a
  never-settled, eligible order can recover that conflict. Applied callbacks remain no-ops.
* **Attempt awareness (Gate-2 blocker fix).** One order lives through SEVERAL payment attempts —
  a customer abandons attempt A, ``/payments/create`` mints attempt B, each a different
  ``payment_uuid``. ``order.payment_uuid`` is the CURRENT (last-armed) attempt. A FAILURE event
  (``error``/``revert``) from a non-active attempt is an echo of an abandoned one: recorded for
  audit, never applied — so a late ``error`` from attempt A cannot cancel an order the customer
  is still paying via attempt B. ``success`` is NOT attempt-guarded: money that actually arrived
  on any attempt is a real payment; the state machine below still stops it double-settling.
* **State machine.** Even for the active attempt, only a legal transition applies: ``success``/
  ``error`` only out of a not-yet-settled order, ``revert`` only out of ``paid``. A paid order is
  never flipped to failed by a late error; a cancelled order is never resurrected.
* **Trusted success.** ``success`` is applied ONLY from the MD5 callback. The SHA1 webhook does
  not sign its ``status`` field, so a replayed valid webhook could carry ``status=success``;
  webhook success is therefore recorded but never marks an order paid.
* **Amount reconciliation.** A ``success`` whose reported ``amount`` ≠ what the order owes is
  refused — the signature proves authenticity, not that the sum is right.
* **Row lock.** The order is taken ``FOR UPDATE`` before its ``payment_uuid``/``payment_status``
  are read and acted on — the attempt/state checks are check-then-act and would race without it.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.employee_referrals.paid import record_paid
from src.loyalty import service as loyalty_service
from src.orders.models import Order, OrderStatus
from src.payments.models import PaymentEvent

logger = logging.getLogger(__name__)

# Multicard payment statuses (webhook vocabulary). ``success`` also arrives via the MD5 callback.
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_REVERT = "revert"

# Which order payment_status values a status may legally transition OUT of.
# unpaid/pending are "not yet settled"; success and error resolve them, revert reverses a paid order.
_ALLOWED_FROM: dict[str, frozenset[str]] = {
    STATUS_SUCCESS: frozenset({"unpaid", "pending"}),
    STATUS_ERROR: frozenset({"unpaid", "pending"}),
    STATUS_REVERT: frozenset({"paid"}),
}


class PaymentAmountMismatchError(Exception):
    """A ``success`` event's amount does not match what the order owes — refuse, do not pay.

    An ordinary amount refusal rejects an incompatible sum. Timeout and server-error outcomes
    have distinct provider treatment; a callback error alone does not prove a completed refund.
    Both figures are retained for the ops log.
    """

    def __init__(self, *, expected_tiyins: int, got_tiyins: int) -> None:
        self.expected_tiyins = expected_tiyins
        self.got_tiyins = got_tiyins
        super().__init__(
            f"amount mismatch: order owes {expected_tiyins} tiyins, callback reports {got_tiyins}"
        )


@dataclass(frozen=True)
class EventOutcome:
    """Result of handling one payment event — primitives the router turns into an HTTP reply."""

    order: Order | None  # None ⇔ no order matches invoice_id
    newly_processed: bool  # Receipt-new; independent of whether success was applied
    applied_success: bool  # True only if THIS call flipped the order to paid (⇒ notify admins)


def _amount_due_tiyins(order: Order) -> int:
    """What the order still owes Multicard, in tiyins.

    ``total - points_applied``: today ``points_applied`` is 0 for every order that reaches
    Multicard (a fully-points order never gets an invoice), so this equals ``total``. Written
    against the remainder from the start so partial redemption (Stage 6) — where the card is
    invoiced for the leftover — does not silently break this reconciliation the day it ships.
    """
    return int((order.total - order.points_applied) * 100)


def _is_success_hint(receipt: PaymentEvent, *, payment_uuid: str, invoice_id: str) -> bool:
    """Recognize the supported retained WebhookData representation, without coercion.

    This is a bounded writer-provenance check, not proof of payment or historical authentication.
    Only the incoming trusted callback can reconcile the amount and authorize settlement.
    """
    payload = receipt.payload
    return (
        receipt.payment_uuid == payment_uuid
        and receipt.status == STATUS_SUCCESS
        and receipt.invoice_id == invoice_id
        and isinstance(payload, dict)
        and payload.keys() == {"uuid", "invoice_id", "amount", "status", "sign"}
        and all(isinstance(payload[key], str) for key in ("uuid", "invoice_id", "status", "sign"))
        and type(payload["amount"]) is int
        and payload["uuid"] == receipt.payment_uuid
        and payload["invoice_id"] == receipt.invoice_id
        and payload["status"] == receipt.status
        and payload["amount"] == receipt.amount
        and re.fullmatch(r"[0-9a-f]{40}", payload["sign"]) is not None
    )


async def process_payment_event(
    db: AsyncSession,
    *,
    payment_uuid: str,
    invoice_id: str,
    amount: int,
    status: str,
    payload: dict[str, Any],
    trust_success: bool,
) -> EventOutcome:
    """Apply one Multicard event to its order — idempotent, race-safe, attempt-aware.

    Runs inside the caller's request transaction (``get_db`` commits on return, rolls back on
    raise). ``trust_success`` is True only for the MD5 callback: a ``success`` from an untrusted
    source (the webhook, whose status field is unsigned) is recorded but never applied.

    Order of operations is load-bearing:

    1. Lock the order row (``FOR UPDATE``) — serializes concurrent events for one order.
    2. Retain the first receipt. A duplicate is a no-op except a recognized success hint on an
       eligible, never-settled order receiving its first trusted success callback.
    3. Attempt guard (failure events only): a non-active-attempt ``error``/``revert`` ⇒ echo of an
       abandoned attempt ⇒ do not apply.
    4. ``success`` from an untrusted source ⇒ do not apply.
    5. ``success`` amount reconciliation ⇒ raise on mismatch (→ 400, order untouched).
    6. State-machine guard: illegal transition ⇒ do not apply.
    7. Apply.

    An unknown ``invoice_id`` returns ``EventOutcome(order=None, ...)`` — the router decides the
    HTTP code (a webhook must still answer 2xx so Multicard stops retrying).
    """
    order = (
        await db.execute(
            select(Order)
            .where(Order.order_number == invoice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if order is None:
        return EventOutcome(order=None, newly_processed=False, applied_success=False)

    inserted = (
        await db.execute(
            pg_insert(PaymentEvent)
            .values(
                payment_uuid=payment_uuid,
                status=status,
                amount=amount,
                invoice_id=invoice_id,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["payment_uuid", "status"])
            .returning(PaymentEvent.id)
        )
    ).one_or_none()
    receipt_new = inserted is not None
    if not receipt_new:
        if (
            status != STATUS_SUCCESS
            or not trust_success
            or order.status == OrderStatus.CANCELLED
            or order.payment_status not in _ALLOWED_FROM[STATUS_SUCCESS]
            or order.paid_at is not None
        ):
            return EventOutcome(order=order, newly_processed=False, applied_success=False)
        receipt = await db.scalar(
            select(PaymentEvent).where(
                PaymentEvent.payment_uuid == payment_uuid, PaymentEvent.status == STATUS_SUCCESS
            )
        )
        if receipt is None or not _is_success_hint(
            receipt, payment_uuid=payment_uuid, invoice_id=order.order_number
        ):
            logger.warning("Unrecognized success receipt conflict: order_id=%s", order.id)
            return EventOutcome(order=order, newly_processed=False, applied_success=False)
        logger.info("Recovering success from retained webhook hint: order_id=%s", order.id)

    # Attempt guard — for FAILURE events only (error/revert). A late error/revert from an
    # abandoned attempt must not touch an order the customer is still paying (or has paid) via
    # the current attempt. Success is deliberately NOT attempt-guarded: money that actually
    # arrived — even on an older attempt the customer paid in a stale tab — is a real payment and
    # should settle the order (the state guard below still prevents double-settling a paid one).
    if status in (STATUS_ERROR, STATUS_REVERT) and payment_uuid != order.payment_uuid:
        logger.warning(
            "Failure event for non-active attempt: order=%s active_uuid=%s event_uuid=%s status=%s "
            "(recorded, not applied)",
            invoice_id,
            order.payment_uuid,
            payment_uuid,
            status,
        )
        return EventOutcome(order=order, newly_processed=receipt_new, applied_success=False)

    if status == STATUS_SUCCESS:
        if not trust_success:
            # Webhook success is unsigned on the status field → spoofable. Success is authoritative
            # only from the MD5 callback; record for audit, do not mark the order paid.
            logger.info(
                "Untrusted success for order=%s (webhook) — recorded, not applied", invoice_id
            )
            return EventOutcome(order=order, newly_processed=receipt_new, applied_success=False)
        expected = _amount_due_tiyins(order)
        if amount != expected:
            raise PaymentAmountMismatchError(expected_tiyins=expected, got_tiyins=amount)

    # State-machine guard: a non-order-touching status (draft/progress/hold) or an illegal
    # transition (e.g. late error on a paid order, revert on an unpaid one) is recorded, not applied.
    if (
        status not in _ALLOWED_FROM
        or order.payment_status not in _ALLOWED_FROM[status]
        or (status == STATUS_SUCCESS and order.status == OrderStatus.CANCELLED)
    ):
        if status in _ALLOWED_FROM:
            logger.warning(
                "Illegal transition for order=%s: %s while payment_status=%s (recorded, not applied)",
                invoice_id,
                status,
                order.payment_status,
            )
        return EventOutcome(order=order, newly_processed=receipt_new, applied_success=False)

    applied_success = False
    if status == STATUS_SUCCESS:
        order.payment_uuid = payment_uuid
        order.payment_status = "paid"
        order.status = OrderStatus.CONFIRMED
        order.paid_at = datetime.now(UTC)
        applied_success = True
        if order.points_applied > 0:
            # Model-1 commit: the partial order only RESERVED its points at creation (no ledger
            # row). Write the real debit now that the card has actually settled the remainder. This
            # sits INSIDE the state-guarded success branch, so a late success on an already
            # cancelled/paid order never reaches here — it cannot double-debit or resurrect a burn.
            assert order.user_id is not None  # orders_owner_ck: points imply an owner.
            await loyalty_service.commit_redeem(
                db, user_id=order.user_id, order_id=order.id, amount=order.points_applied
            )
    elif status == STATUS_ERROR:
        order.payment_status = "failed"
        order.status = OrderStatus.CANCELLED
    elif status == STATUS_REVERT:
        order.payment_status = "refunded"
        order.status = OrderStatus.CANCELLED

    await db.flush()
    if applied_success:
        await record_paid(db, order)
    return EventOutcome(order=order, newly_processed=receipt_new, applied_success=applied_success)
