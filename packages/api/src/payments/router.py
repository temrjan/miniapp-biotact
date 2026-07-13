"""Payment API router."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.common.exceptions import ForbiddenException
from src.config import settings
from src.database import get_db
from src.notifications.service import telegram_notifier
from src.orders import service as orders_service
from src.payments.schemas import (
    CreatePaymentRequest,
    PaymentCreateData,
    PaymentResponse,
    SuccessCallbackData,
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

    if order.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order already paid",
        )

    # Multicard does not document a zero invoice; the server prices every order, so a
    # non-positive total means bad catalogue data — fail loudly instead of charging 0.
    if order.total <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order total must be positive",
        )

    # Build callback URL
    base_url = str(req.base_url).rstrip("/")
    callback_url = f"{base_url}{settings.api_v1_prefix}/payments/callback"

    # Build OFD items
    ofd_items = build_ofd_items(order.items)

    # Amount in tiyins
    amount_tiyins = int(order.total * 100)

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

    try:
        result = await multicard_client.create_invoice(payment_data)
        if not result:
            raise HTTPException(status_code=502, detail="Payment service returned no result")

        # Save payment UUID to order
        await orders_service.update_order_payment(
            db,
            order.id,
            payment_uuid=result.uuid,
            payment_status="pending",
        )

        # Send Telegram notification about new order
        try:
            items_data = [
                {
                    "product_name": item.product_name,
                    "quantity": item.quantity,
                    "product_price": item.product_price,
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
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

        return PaymentResponse(
            payment_url=result.pay_url,
            payment_uuid=result.uuid,
            order_id=order.id,
        )

    except Exception as e:
        logger.error(f"Failed to create payment: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment service temporarily unavailable",
        ) from e


@router.post("/callback")
async def payment_callback(
    callback: SuccessCallbackData,
    db: DbSession,
) -> dict[str, bool]:
    """Handle success callback from Multicard."""
    logger.info(f"Payment callback: uuid={callback.uuid}, invoice_id={callback.invoice_id}")

    # Verify signature (MD5: store_id + invoice_id + amount + secret)
    if not multicard_client.verify_callback_sign(
        callback.store_id,
        callback.invoice_id,
        callback.amount,
        callback.sign,
    ):
        logger.warning(f"Invalid callback signature for {callback.uuid}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )

    # Get order by invoice_id (order_number)
    order = await orders_service.get_order_by_number(db, callback.invoice_id)
    if not order:
        logger.error(f"Order not found: {callback.invoice_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    # Update order as paid
    await orders_service.update_order_payment(
        db,
        order.id,
        payment_uuid=callback.uuid,
        payment_status="paid",
    )
    # Update order status to confirmed
    await orders_service.update_order_status(db, order.id, "confirmed")

    logger.info(f"Order {order.order_number} paid successfully via {callback.ps}")

    # Send Telegram notification about successful payment
    try:
        # Get order with items for notification
        order_with_items = await orders_service.get_order_with_items(db, order.id)
        if order_with_items:
            items_data = [
                {
                    "product_name": item.product_name,
                    "quantity": item.quantity,
                    "product_price": item.product_price,
                }
                for item in order_with_items.items
            ]
            await telegram_notifier.notify_payment_success(
                order_number=order_with_items.order_number,
                total=float(order_with_items.total),
                items=items_data,
                customer_name=order_with_items.delivery_name,
                customer_phone=order_with_items.delivery_phone,
                delivery_address=order_with_items.delivery_address,
                invoice_uuid=callback.uuid,
            )
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")

    return {"success": True}


@router.get("/status/{payment_uuid}")
async def get_payment_status(payment_uuid: str) -> dict[str, Any]:
    """Get payment status from Multicard."""
    result = await multicard_client.get_invoice_status(payment_uuid)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )
    return result
