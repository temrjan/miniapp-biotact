"""One durable payment owner for orders with finite size allocations.

Database locks never span provider requests. A committed claim fences creation permanently;
unknown external outcomes retain the allocation until privileged, evidenced recovery.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.employee_referrals.paid import commit_paid, record_paid
from src.loyalty.service import commit_redeem
from src.orders.models import Order, OrderStatus
from src.payments.models import PaymentEvent
from src.payments.schemas import (
    PaymentCreateData,
    PaymentResponse,
    SuccessCallbackData,
    WebhookData,
)
from src.payments.service import multicard_client
from src.products.models import SizeStockRecord
from src.products.stock import lock_products, restore_allocations

logger = logging.getLogger(__name__)
PROTOCOL = "size_stock_v1"
HELD_STATES = ("not_started", "creating", "live", "cancelling", "uncertain")


def conflict(code: str, status: int = 409, *, order_id: int | None = None) -> HTTPException:
    detail: dict[str, object] = {"code": code}
    if order_id is not None:
        detail["order_id"] = order_id
    return HTTPException(status_code=status, detail=detail)


async def lock_order(db: AsyncSession, order_id: int) -> Order:
    order = (
        await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update(of=Order)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def record(
    db: AsyncSession,
    order: Order,
    action: str,
    *,
    actor: str = "system",
    reason: str = "",
    evidence_ref: str | None = None,
    expected_revision: int | None = None,
    result: dict[str, object] | None = None,
) -> None:
    db.add(
        SizeStockRecord(
            order_id=order.id,
            action=action,
            actor=actor,
            reason=reason,
            evidence_ref=evidence_ref,
            expected_revision=expected_revision,
            result=result
            or {"revision": order.size_revision, "owner_state": order.size_owner_state},
        )
    )


def payment_response(order: Order) -> PaymentResponse:
    if order.size_owner_state in ("cancelled", "refunded") or order.status == OrderStatus.CANCELLED:
        raise conflict("order_payment_closed", order_id=order.id)
    if order.accepted_settlement or order.size_owner_state == "paid":
        raise HTTPException(status_code=400, detail="Order already paid")
    if order.size_owner_state == "cancelling":
        raise conflict("payment_cancellation_pending", order_id=order.id)
    if order.size_owner_state == "creating":
        raise conflict("payment_processing", order_id=order.id)
    if order.size_owner_state == "live" and order.owned_invoice_uuid and order.checkout_url:
        return PaymentResponse(
            payment_url=order.checkout_url, payment_uuid=order.owned_invoice_uuid, order_id=order.id
        )
    raise conflict("payment_outcome_unknown", order_id=order.id)


async def create_owned_invoice(
    db: AsyncSession, order_id: int, data: PaymentCreateData
) -> tuple[PaymentResponse, bool]:
    order = await lock_order(db, order_id)
    if order.size_stock_protocol != PROTOCOL:
        raise conflict("order_state_conflict")
    if order.size_owner_state != "not_started":
        return payment_response(order), False
    if any(item.restored_quantity for item in order.items) or order.accepted_settlement:
        raise conflict("order_payment_closed", order_id=order.id)
    token = str(uuid.uuid4())
    order.creation_token = token
    order.size_owner_state = "creating"
    order.size_revision += 1
    record(db, order, "creation_claim", result={"creation_token": token})
    # Failure/uncertain acknowledgment stops before POST. Never retry this transaction's POST.
    await db.commit()
    try:
        result = await multicard_client.create_invoice(data)
        if result is None or not result.uuid or not result.pay_url:
            raise ValueError("Incomplete invoice result")
    except Exception as exc:
        await db.rollback()
        order = await lock_order(db, order_id)
        if order.creation_token == token and order.size_owner_state == "creating":
            order.size_owner_state = "uncertain"
            order.size_revision += 1
            record(db, order, "creation_unknown")
            await db.commit()
        else:
            await db.rollback()
        logger.warning("Invoice creation outcome unknown for order=%s", order_id, exc_info=True)
        raise conflict("payment_outcome_unknown", 502, order_id=order_id) from exc

    order = await lock_order(db, order_id)
    if order.creation_token != token or order.size_owner_state not in (
        "creating",
        "uncertain",
        "paid",
    ):
        record(db, order, "creation_contradiction", result={"uuid": result.uuid})
        await db.commit()
        raise conflict("payment_outcome_unknown", order_id=order_id)
    if order.owned_invoice_uuid not in (None, result.uuid):
        record(db, order, "creation_contradiction", result={"uuid": result.uuid})
        await db.commit()
        raise conflict("payment_outcome_unknown", order_id=order_id)
    order.owned_invoice_uuid = result.uuid
    order.payment_uuid = result.uuid
    order.checkout_url = result.pay_url
    if not order.accepted_settlement:
        order.size_owner_state = "live"
        order.payment_status = "pending"
    order.size_revision += 1
    record(db, order, "invoice_bound", result={"uuid": result.uuid})
    await db.commit()
    return PaymentResponse(
        payment_url=result.pay_url, payment_uuid=result.uuid, order_id=order_id
    ), True


async def close_unpaid(db: AsyncSession, order: Order) -> None:
    await restore_allocations(db, order)
    order.size_owner_state = "cancelled"
    order.payment_status = "failed"
    order.status = OrderStatus.CANCELLED
    order.size_revision += 1
    record(db, order, "cancelled")


async def cancel_owned_order(db: AsyncSession, order_id: int) -> bool:
    """One cancellation attempt. Pending/unknown claims are never reset or automatically replayed."""
    order = await lock_order(db, order_id)
    if order.size_stock_protocol != PROTOCOL:
        raise conflict("order_state_conflict")
    if order.accepted_settlement or order.size_owner_state == "paid":
        await db.commit()
        return False
    if order.size_owner_state in ("cancelled", "refunded"):
        await db.commit()
        return True
    if order.size_owner_state == "not_started" and order.creation_token is None:
        await close_unpaid(db, order)
        await db.commit()
        return True
    if order.size_owner_state != "live" or not order.owned_invoice_uuid:
        await db.commit()
        return False
    invoice_uuid = order.owned_invoice_uuid
    token = str(uuid.uuid4())
    order.cancellation_token = token
    order.size_owner_state = "cancelling"
    order.size_revision += 1
    record(db, order, "cancellation_claim", result={"uuid": invoice_uuid, "token": token})
    await db.commit()
    if not await multicard_client.delete_owned_invoice(invoice_uuid):
        return False
    order = await lock_order(db, order_id)
    if (
        order.size_owner_state != "cancelling"
        or order.cancellation_token != token
        or order.owned_invoice_uuid != invoice_uuid
        or order.accepted_settlement
    ):
        await db.commit()
        return False
    await close_unpaid(db, order)
    await db.commit()
    return True


def invoice_identity(details: dict[str, object], order: Order, invoice_uuid: str) -> bool:
    """Documented invoice detail fields; missing identity is an unresolved result."""
    required = ("uuid", "store_id", "invoice_id", "amount")
    if any(key not in details or details[key] is None for key in required):
        raise conflict("payment_verification_pending", 500)
    return (
        details["uuid"] == invoice_uuid
        and details["invoice_id"] == order.order_number
        and str(details["store_id"]) == settings.multicard_store_id
        and type(details["amount"]) is int
        and details["amount"] == int((order.total - order.points_applied) * 100)
    )


async def settle_owned(db: AsyncSession, order: Order, invoice_uuid: str) -> bool:
    if order.accepted_settlement:
        return False
    if order.size_owner_state not in HELD_STATES or not order.creation_token:
        raise conflict("order_payment_closed")
    if any(item.restored_quantity for item in order.items):
        raise conflict("order_payment_closed")
    order.owned_invoice_uuid = invoice_uuid
    order.payment_uuid = invoice_uuid
    if order.points_applied:
        assert order.user_id is not None  # orders_owner_ck: points imply an owner.
        if not await commit_redeem(
            db, user_id=order.user_id, order_id=order.id, amount=order.points_applied
        ):
            # An accepted settlement is atomic with its points debit.
            raise conflict("payment_verification_pending", 500)
    order.accepted_settlement = True
    order.size_owner_state = "paid"
    order.payment_status = "paid"
    order.status = OrderStatus.CONFIRMED
    order.paid_at = datetime.now(UTC)
    order.size_revision += 1
    record(db, order, "settled", result={"uuid": invoice_uuid})
    await db.flush()
    await record_paid(db, order)
    return True


async def success_callback(
    db: AsyncSession, order: Order, callback: SuccessCallbackData
) -> tuple[bool, bool]:
    """Return (accepted, newly settled). Signature is checked by the shared HTTP boundary."""
    order_id = order.id
    order = await lock_order(db, order_id)
    if str(callback.store_id) != settings.multicard_store_id:
        record(db, order, "reconciliation_pending", reason="Callback store mismatch")
        await db.commit()
        raise HTTPException(status_code=400, detail="Store mismatch")
    if callback.amount != int((order.total - order.points_applied) * 100):
        record(db, order, "reconciliation_pending", reason="Callback amount mismatch")
        await db.commit()
        raise HTTPException(status_code=400, detail="Amount mismatch")
    if order.owned_invoice_uuid not in (None, callback.uuid):
        record(db, order, "reconciliation_pending", reason="Callback UUID mismatch")
        await db.commit()
        raise HTTPException(status_code=400, detail="Invoice UUID mismatch")
    if order.accepted_settlement:
        if order.owned_invoice_uuid is None:
            record(db, order, "reconciliation_pending", reason="Success without owned invoice")
            await db.commit()
            raise HTTPException(status_code=400, detail="No invoice creation claim")
        await db.commit()
        return True, False
    if order.size_owner_state in ("cancelled", "refunded"):
        record(db, order, "reconciliation_pending", reason="Success after payment closed")
        await db.commit()
        return False, False
    if order.creation_token is None:
        record(db, order, "reconciliation_pending", reason="Success without creation claim")
        await db.commit()
        raise HTTPException(status_code=400, detail="No invoice creation claim")
    verified_token = None
    if order.owned_invoice_uuid is None and order.creation_token:
        verified_token = order.creation_token
        # Release the read transaction BEFORE the authenticated provider read.
        await db.rollback()
        try:
            details = await multicard_client.get_invoice_status(callback.uuid)
        except Exception as exc:
            raise conflict("payment_verification_pending", 500) from exc
        if details is None:
            raise conflict("payment_verification_pending", 500)
        order = await lock_order(db, order_id)
        if not invoice_identity(details, order, callback.uuid):
            record(db, order, "reconciliation_pending", reason="Provider invoice identity mismatch")
            await db.commit()
            raise HTTPException(status_code=400, detail="Invoice identity mismatch")
    if order.owned_invoice_uuid not in (None, callback.uuid):
        record(db, order, "reconciliation_pending", reason="Callback UUID mismatch")
        await db.commit()
        raise HTTPException(status_code=400, detail="Invoice UUID mismatch")
    if order.accepted_settlement:
        await db.commit()
        return True, False
    if order.size_owner_state in ("cancelled", "refunded"):
        record(db, order, "reconciliation_pending", reason="Success after payment closed")
        await db.commit()
        return False, False
    if order.creation_token is None:
        raise HTTPException(status_code=400, detail="No invoice creation claim")
    if order.owned_invoice_uuid is None and order.creation_token != verified_token:
        raise conflict("payment_verification_pending", 500)
    newly = await settle_owned(db, order, callback.uuid)
    await commit_paid(db)
    return True, newly


async def lifecycle_hint(db: AsyncSession, order_id: int, webhook: WebhookData) -> None:
    order = await lock_order(db, order_id)
    inserted = (
        await db.execute(
            pg_insert(PaymentEvent)
            .values(
                payment_uuid=webhook.uuid,
                status=webhook.status,
                amount=webhook.amount,
                invoice_id=webhook.invoice_id,
                payload=webhook.model_dump(),
            )
            .on_conflict_do_nothing(index_elements=["payment_uuid", "status"])
            .returning(PaymentEvent.id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        record(
            db,
            order,
            "reconciliation_pending",
            result={"event_id": inserted, "uuid": webhook.uuid, "status_hint": webhook.status},
        )
    await db.commit()


async def claim_legacy_vest(db: AsyncSession, order_id: int) -> str:
    """Fence cutover against an in-flight size-less creator; retain every attempt for inspection."""
    order = await lock_order(db, order_id)
    product = (await lock_products(db, {39})).get(39)
    if product is None or product.size_cutover_blocked or product.requires_size:
        raise conflict("size_cutover_pending")
    token = str(uuid.uuid4())
    order.legacy_vest_creation_token = token
    record(db, order, "legacy_creation_claim", result={"creation_token": token})
    await db.commit()
    return token


async def finish_legacy_vest(
    db: AsyncSession, order_id: int, token: str, invoice_uuid: str
) -> None:
    order = await lock_order(db, order_id)
    if order.legacy_vest_creation_token == token:
        order.legacy_vest_creation_token = None
    record(
        db, order, "legacy_invoice_created", result={"creation_token": token, "uuid": invoice_uuid}
    )
    await db.commit()


async def change_status(db: AsyncSession, order_id: int, status: OrderStatus) -> Order:
    order = await lock_order(db, order_id)
    if status == OrderStatus.CANCELLED:
        if not order.accepted_settlement:
            if not await cancel_owned_order(db, order_id):
                raise conflict("payment_cancellation_pending", order_id=order_id)
            return await lock_order(db, order_id)
        if order.dispatched_at is None:
            await restore_allocations(db, order)
        order.status = status
    else:
        if (
            order.status == OrderStatus.CANCELLED
            or order.size_owner_state in ("cancelled", "refunded")
            or any(item.restored_quantity for item in order.items)
        ):
            raise conflict("order_state_conflict")
        if status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED):
            if not order.accepted_settlement:
                raise conflict("order_state_conflict")
            if order.dispatched_at is None:
                order.dispatched_at = datetime.now(UTC)
        order.status = status
    if status == OrderStatus.DELIVERED and order.completed_at is None:
        order.completed_at = datetime.now(UTC)
    order.size_revision += 1
    record(db, order, "status_changed", result={"status": status.value})
    await db.flush()
    await db.refresh(order)
    return order
