"""Order API router."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user, verify_admin_jwt
from src.common.exceptions import ForbiddenException
from src.database import get_db
from src.loyalty.service import InsufficientPointsError
from src.notifications.service import telegram_notifier
from src.orders import service
from src.orders.models import Order
from src.orders.schemas import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
)
from src.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]
# Orders are per-person data on the money path: identity is required, never optional. The previous
# optional dependency silently fell back to ``user_id = 1``, which is what made every order
# readable and creatable without any auth at all.
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    db: DbSession,
    user: CurrentUser,
) -> Order:
    """Create a new order. Prices come from the catalogue — the request carries no money.

    ``payment_method='points'`` settles the whole order from the club balance in this same
    request; a shortfall is a 409 whose detail carries the exact deficit (all-or-nothing —
    nothing is debited, no order is created).
    """
    try:
        order = await service.create_order(db, order_data, user_id=user.id)
    except InsufficientPointsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "insufficient_points",
                "balance": exc.balance,
                "required": exc.required,
                "deficit": exc.deficit,
            },
        ) from exc
    except service.InvalidRedeemAmountError as exc:
        # Structured 422 so the mini-app can clamp its points stepper to the returned ceiling
        # without a second round-trip. Must precede the generic OrderValidationError below — it is
        # a subclass, and Python matches the first handler.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "invalid_redeem_amount",
                "points_to_apply": exc.points,
                "max_redeemable": exc.max_redeemable,
            },
        ) from exc
    except service.OrderValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    if order.payment_status == "paid":
        # Reached only by a FULLY points-covered order — the sole shape ``create_order`` stamps
        # ``paid`` (a card order is ``unpaid`` until Multicard; a PARTIAL points order stays
        # ``unpaid`` too and is notified later from the payments router). Commit BEFORE the network
        # call, for two reasons specific to that points settlement:
        #
        #   * ``redeem_points`` holds a ``FOR UPDATE`` lock on this customer's profile row —
        #     the same row every accrual locks. ``get_db`` would otherwise commit only after
        #     this handler returns, so a slow/unreachable Telegram (httpx timeout: 10s) would
        #     keep that lock — and every other loyalty write for THIS user (QR activation,
        #     survey, another redeem) — waiting behind a notification. [Gate-2 MINOR]
        #   * Order of truth: the money is settled first, the message is sent about a fact.
        #     Sending first would let a failing commit announce a phantom order to the group.
        #
        # The later ``get_db`` commit is a harmless no-op on the already-committed session.
        await db.commit()

        # The same admin request card payments produce, marked «оплачено баллами» (Captain,
        # 2026-07-13). A fully-covered points order never reaches the payments router, so this is
        # its only send. Failure must not fail the order — the debit is already committed; mirror
        # of the payments-router pattern.
        try:
            items_data = [
                {
                    "product_name": item.product_name,
                    "quantity": item.quantity,
                    "product_price": item.product_price,
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
                points_applied=order.points_applied,
            )
        except Exception as exc:  # notification is best-effort, never blocks money
            logger.error("Failed to send Telegram notification: %s", exc)

    return order


@router.get("", response_model=OrderListResponse)
async def get_orders(
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> OrderListResponse:
    """Get the caller's own orders."""
    return await service.get_user_orders(db, user_id=user.id, page=page, size=size)


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int,
    db: DbSession,
    user: CurrentUser,
) -> Order:
    """Get one of the caller's own orders.

    The ownership check is unconditional. It used to be guarded by ``if user and ...``, so an
    anonymous caller skipped it entirely and could read anyone's order — address, phone, name and
    contents included.
    """
    order = await service.get_order_with_items(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    if order.user_id != user.id:
        raise ForbiddenException("Not your order")
    return order


@router.patch(
    "/{order_id}/status", response_model=OrderResponse, dependencies=[Depends(verify_admin_jwt)]
)
async def update_order_status(
    order_id: int,
    status_data: OrderStatusUpdate,
    db: DbSession,
) -> Order:
    """Update order status (admin only)."""
    order = await service.update_order_status(db, order_id, status_data.status)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    return order
