"""Payment API router."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.common.exceptions import ForbiddenException
from src.config import settings
from src.database import get_db
from src.employee_referrals.notices import invoice_referral_label, owns_paid
from src.employee_referrals.paid import PaidUnavailableError, commit_paid
from src.notifications.service import source_label, telegram_notifier
from src.orders import service as orders_service
from src.payments import size_stock
from src.payments.lifecycle import PaymentAmountMismatchError, process_payment_event
from src.payments.schemas import (
    CreatePaymentRequest,
    MultiCardPaymentResponse,
    PaymentCreateData,
    PaymentResponse,
    SuccessCallbackData,
    WebhookData,
)
from src.payments.service import build_ofd_items, multicard_client
from src.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/create", response_model=PaymentResponse)
async def create_payment(
    request: CreatePaymentRequest,
    req: Request,
    db: DbSession,
    user: CurrentUser,
) -> PaymentResponse:
    """Create a Multicard invoice for the caller's own order.

    Identity is required: this endpoint used to accept any ``order_id`` from anyone, which both
    invoiced other people's orders and fired the admin Telegram notification below on demand.
    """
    # Get order with items
    order = await orders_service.get_order_with_items(db, request.order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    # Ownership is checked before payment state, so a stranger cannot even learn whether
    # somebody else's order is already paid.
    if order.user_id != user.id:
        raise ForbiddenException("Not your order")

    if order.size_stock_protocol == size_stock.PROTOCOL:
        order = await size_stock.lock_order(db, order.id)
        if order.size_owner_state != "not_started":
            return size_stock.payment_response(order)
    elif order.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order already paid",
        )

    # Points may already cover part of the goods; Multicard collects only the card remainder.
    # Multicard does not document a zero invoice, and a fully-covered order is already ``paid``
    # (rejected above), so a non-positive remainder here means bad catalogue data — fail loudly
    # instead of asking the gateway to charge 0.
    amount_due = order.total - order.points_applied
    if amount_due <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order remainder must be positive",
        )

    # Build callback URL
    base_url = str(req.base_url).rstrip("/")
    callback_url = f"{base_url}{settings.api_v1_prefix}/payments/callback"

    # Amount charged to the card, in tiyins — the remainder after points.
    amount_tiyins = int(amount_due * 100)

    # OFD line items scaled so their totals sum EXACTLY to the charged amount (discount model).
    ofd_items = build_ofd_items(order.items, amount_tiyins=amount_tiyins)

    # Create payment data
    payment_data = PaymentCreateData(
        store_id=settings.multicard_store_id,
        amount=amount_tiyins,
        invoice_id=order.order_number,
        callback_url=callback_url,
        return_url=request.return_url,
        lang="ru",
        ofd=ofd_items,
    )

    owned_response = None
    legacy_token = None
    if order.size_stock_protocol == size_stock.PROTOCOL:
        owned_response, notify_created = await size_stock.create_owned_invoice(
            db, order.id, payment_data
        )
        if not notify_created:
            return owned_response
    elif any(item.product_id == 39 for item in order.items):
        legacy_token = await size_stock.claim_legacy_vest(db, order.id)

    try:
        result: MultiCardPaymentResponse | None
        if owned_response is not None:
            result = MultiCardPaymentResponse(
                uuid=owned_response.payment_uuid, pay_url=owned_response.payment_url
            )
        else:
            result = await multicard_client.create_invoice(payment_data)
        if not result:
            raise HTTPException(status_code=502, detail="Payment service returned no result")

        # Arm this attempt on the order — atomically, only if it is not already paid. Guards the
        # TOCTOU between the paid-check above and here across the (up to 30s) Multicard call: a
        # concurrent success callback may have paid the order meanwhile, and a blind write would
        # stamp it back to pending over a real payment.
        armed = owned_response is not None or await orders_service.try_arm_payment_attempt(
            db, order.id, payment_uuid=result.uuid
        )
        if not armed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order already paid",
            )
        if legacy_token is not None:
            await size_stock.finish_legacy_vest(db, order.id, legacy_token, result.uuid)

        # Send Telegram notification about new order
        try:
            items_data = [
                {
                    "product_name": item.product_name,
                    "size": item.size,
                    "quantity": item.quantity,
                    "product_price": item.product_price,
                    "is_sale_at_order": item.is_sale_at_order,
                    "regular_price_at_order": item.regular_price_at_order,
                    # Форма выпуска для отдела продаж — выводится из slug'а в уведомителе.
                    # Товар загружен вместе с заказом (`get_order_with_items` → `selectinload`),
                    # лишнего запроса здесь нет.
                    "slug": item.product.slug if item.product else None,
                }
                for item in order.items
            ]
            await telegram_notifier.notify_order_created(
                order_number=order.order_number,
                total=float(order.total),
                items=items_data,
                customer_name=order.delivery_name,
                customer_phone=order.delivery_phone,
                delivery_address=order.delivery_address,
                invoice_uuid=result.uuid,
                points_applied=order.points_applied,
                referral_label=await invoice_referral_label(db, order.id),
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

        return PaymentResponse(
            payment_url=result.pay_url,
            payment_uuid=result.uuid,
            order_id=order.id,
        )

    except HTTPException:
        # Deliberate HTTP responses (e.g. "already paid" from the arm guard) must pass through —
        # not be swallowed and relabelled as a 502 by the gateway-error handler below.
        raise
    except Exception as e:
        logger.error(f"Failed to create payment: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment service temporarily unavailable",
        ) from e


async def _notify_paid(db: AsyncSession, order_id: int, *, invoice_uuid: str) -> None:
    """Send the admin "paid" notification for ``order_id`` — best-effort, called AFTER commit.

    Loads the order fresh with items (the lifecycle handler locks the bare row, not the lines).
    Must run after the transaction is committed so the ``FOR UPDATE`` lock is not held across the
    Telegram network call (up to 10s) — same lesson as the points path.
    """
    # Keep optional reads and their cleanup outside the completed financial session.
    async with AsyncSession(bind=db.bind, expire_on_commit=False) as notify_db:
        if await owns_paid(notify_db, order_id):
            return
        order = await orders_service.get_order_with_items(notify_db, order_id)
        if not order:
            return
        items_data = [
            {
                "product_name": item.product_name,
                "size": item.size,
                "quantity": item.quantity,
                "product_price": item.product_price,
                "is_sale_at_order": item.is_sale_at_order,
                "regular_price_at_order": item.regular_price_at_order,
                # Как и в create-пути: товар приехал вместе с заказом, запрос не добавляется.
                "slug": item.product.slug if item.product else None,
            }
            for item in order.items
        ]
        await telegram_notifier.notify_payment_success(
            order_number=order.order_number,
            total=float(order.total),
            items=items_data,
            customer_name=order.delivery_name,
            customer_phone=order.delivery_phone,
            delivery_address=order.delivery_address,
            invoice_uuid=invoice_uuid,
            points_applied=order.points_applied,
            source_label=source_label(order),
        )


@router.post("/callback")
async def payment_callback(
    callback: SuccessCallbackData,
    db: DbSession,
) -> dict[str, bool | str]:
    """Handle the success callback from Multicard (MD5-signed).

    Idempotent: a duplicate callback (Multicard repeats it on any 2xx timeout) is a no-op — the
    order is not re-written and admins are not re-notified. The amount is reconciled against what
    the order owes before anything is applied.
    """
    logger.info("Payment callback: uuid=%s, invoice_id=%s", callback.uuid, callback.invoice_id)

    # Verify signature (MD5: store_id + invoice_id + amount + secret)
    if not multicard_client.verify_callback_sign(
        callback.store_id,
        callback.invoice_id,
        callback.amount,
        callback.sign,
    ):
        logger.warning("Invalid callback signature for %s", callback.uuid)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )

    order = await orders_service.get_order_by_number(db, callback.invoice_id)
    if order is not None and order.size_stock_protocol == size_stock.PROTOCOL:
        order_id = order.id
        try:
            accepted, newly = await size_stock.success_callback(db, order, callback)
        except PaidUnavailableError:
            raise HTTPException(503, detail={"code": "referral_paid_unavailable"}) from None
        if newly:
            try:
                await _notify_paid(db, order_id, invoice_uuid=callback.uuid)
            except Exception:
                logger.error("Paid notification failed for order=%s", order_id)
        return (
            {"success": True}
            if accepted
            else {"success": False, "message": "Order payment is closed"}
        )

    try:
        outcome = await process_payment_event(
            db,
            payment_uuid=callback.uuid,
            invoice_id=callback.invoice_id,
            amount=callback.amount,
            status="success",
            payload=callback.model_dump(),
            trust_success=True,  # MD5 callback is the authoritative success channel
        )
    except PaidUnavailableError:
        raise HTTPException(503, detail={"code": "referral_paid_unavailable"}) from None
    except PaymentAmountMismatchError as exc:
        # Reject the wrong sum without settling. Ordinary refusal and timeout/server failure
        # have distinct provider treatment; an error alone does not prove a completed refund.
        logger.error("Callback amount mismatch for %s: %s", callback.uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount mismatch",
        ) from exc

    if outcome.order is None:
        logger.error("Order not found for callback invoice_id=%s", callback.invoice_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    if outcome.applied_success:
        logger.info("Order %s paid successfully via %s", callback.invoice_id, callback.ps)
        # Commit BEFORE notifying so the order's FOR UPDATE lock is released before the Telegram
        # call. Optional notification reads use their own transaction and cleanup.
        try:
            await commit_paid(db)
        except PaidUnavailableError:
            raise HTTPException(503, detail={"code": "referral_paid_unavailable"}) from None
        try:
            await _notify_paid(db, outcome.order.id, invoice_uuid=callback.uuid)
        except Exception as exc:  # notification is best-effort, never fails the callback
            logger.error("Failed to send Telegram notification: %s", exc)

    return {"success": True}


@router.post("/webhook")
async def payment_webhook(
    webhook: WebhookData,
    db: DbSession,
) -> dict[str, bool]:
    """Handle a payment lifecycle webhook from Multicard (SHA1-signed).

    Covers the full status vocabulary the success callback does not: ``error`` (payment failed)
    and ``revert`` (money refunded) cancel the order; ``draft``/``progress``/``hold`` are
    recorded but move nothing. Multicard retries up to 5 times unless we answer 2xx, so every
    outcome except a bad signature returns success — including an unknown invoice, so a stray
    webhook stops retrying instead of hammering us.
    """
    logger.info(
        "Payment webhook: uuid=%s, invoice_id=%s, status=%s",
        webhook.uuid,
        webhook.invoice_id,
        webhook.status,
    )

    # Verify signature (SHA1: uuid + invoice_id + amount + secret)
    if not multicard_client.verify_webhook_sign(
        webhook.uuid,
        webhook.invoice_id,
        webhook.amount,
        webhook.sign,
    ):
        logger.warning("Invalid webhook signature for %s", webhook.uuid)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )

    order = await orders_service.get_order_by_number(db, webhook.invoice_id)
    if order is not None and order.size_stock_protocol == size_stock.PROTOCOL:
        await size_stock.lifecycle_hint(db, order.id, webhook)
        return {"success": True}

    try:
        outcome = await process_payment_event(
            db,
            payment_uuid=webhook.uuid,
            invoice_id=webhook.invoice_id,
            amount=webhook.amount,
            status=webhook.status,
            payload=webhook.model_dump(),
            trust_success=False,  # webhook status is unsigned → not authoritative for success
        )
    except PaymentAmountMismatchError as exc:
        logger.error("Webhook amount mismatch for %s: %s", webhook.uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount mismatch",
        ) from exc

    if outcome.order is None:
        # Unknown invoice — still 2xx so Multicard stops retrying a webhook we cannot match.
        logger.warning("Webhook for unknown invoice_id=%s (acknowledged)", webhook.invoice_id)
        return {"success": True}

    if outcome.applied_success:
        await db.commit()
        try:
            await _notify_paid(db, outcome.order.id, invoice_uuid=webhook.uuid)
        except Exception as exc:  # notification is best-effort, never fails the webhook
            logger.error("Failed to send Telegram notification: %s", exc)

    return {"success": True}
