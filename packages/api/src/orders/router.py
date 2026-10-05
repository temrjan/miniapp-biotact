"""Order API router."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user, verify_admin_jwt
from src.common.exceptions import ForbiddenException
from src.database import get_db
from src.employee_referrals.capture import CaptureUnavailableError, capture_requested
from src.employee_referrals.notices import owns_paid
from src.employee_referrals.paid import PaidUnavailableError, commit_paid
from src.loyalty.service import InsufficientPointsError
from src.notifications.service import telegram_notifier
from src.orders import service
from src.orders.models import Order
from src.orders.schemas import (
    OrderCreate,
    OrderListResponse,
    OrderQuote,
    OrderResponse,
    OrderStatusUpdate,
    ReleaseReservationsResponse,
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

    ``points_to_apply > 0`` spends that many club points on this order (Stage 6 partial redemption):
    full coverage settles it outright, a partial spend leaves the card the remainder. A malformed
    amount is a 422 carrying ``max_redeemable``; a live-balance shortfall under concurrency is a 409
    whose detail carries the exact deficit (nothing debited, no order created).
    """
    # Model-1 lazy cleanup: reclaim THIS user's abandoned partial-redemption reservations before we
    # price a new redemption against their balance, committed as its OWN unit — the external invoice
    # DELETE must never be undone by a later rollback of the create below. A pure card order holds no
    # reservation, so it skips this. Best-effort: a cleanup failure must not block a new order (the
    # stale reserve is simply retried on the next attempt).
    try:
        capture_requested(order_data.referral_token)
    except CaptureUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "referral_capture_unavailable",
                "message": "Referral capture unavailable",
            },
        ) from exc
    if order_data.points_to_apply > 0:
        try:
            await service.expire_stale_partials(db, user_id=user.id)
            await db.commit()
        except Exception:  # cleanup is best-effort, never blocks a new order
            logger.error("stale-partial cleanup failed for user %s", user.id)
            await db.rollback()

    try:
        order = await service.create_order(db, order_data, user_id=user.id)
    except CaptureUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "referral_capture_unavailable",
                "message": "Referral capture unavailable",
            },
        ) from exc
    except PaidUnavailableError:
        raise HTTPException(503, detail={"code": "referral_paid_unavailable"}) from None
    except service.QuoteAgreementError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "quote": exc.quote.model_dump(mode="json")}
        ) from exc
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
        #   * ``reserve_points`` holds a ``FOR UPDATE`` lock on this customer's profile row —
        #     the same row every accrual locks. ``get_db`` would otherwise commit only after
        #     this handler returns, so a slow/unreachable Telegram (httpx timeout: 10s) would
        #     keep that lock — and every other loyalty write for THIS user (QR activation,
        #     survey, another redeem) — waiting behind a notification. [Gate-2 MINOR]
        #   * Order of truth: the money is settled first, the message is sent about a fact.
        #     Sending first would let a failing commit announce a phantom order to the group.
        #
        # The later ``get_db`` commit is a harmless no-op on the already-committed session.
        try:
            await commit_paid(db)
        except PaidUnavailableError:
            raise HTTPException(503, detail={"code": "referral_paid_unavailable"}) from None

        # The same admin request card payments produce, marked «оплачено баллами» (Captain,
        # 2026-07-13). A fully-covered points order never reaches the payments router, so this is
        # its only send. Failure must not fail the order — the debit is already committed; mirror
        # of the payments-router pattern.
        try:
            if not await owns_paid(db, order.id):
                items_data = [
                    {
                        "product_name": item.product_name,
                        "quantity": item.quantity,
                        "product_price": item.product_price,
                        "is_sale_at_order": item.is_sale_at_order,
                        "regular_price_at_order": item.regular_price_at_order,
                        # Форма выпуска для отдела продаж. Заказ, покрытый баллами целиком, до
                        # платёжного роутера не доходит вовсе — если не отдать slug здесь, отгрузка
                        # такого заказа готовится вслепую, в отличие от карточных.
                        #
                        # ⚠️ ЗАВИСИМОСТЬ: товар доступен здесь только потому, что `create_order`
                        # заканчивается `db.refresh(order)` (`orders/service.py:302`) — refresh
                        # каскадом перезагружает `order.items`, а связь `OrderItem.product` объявлена
                        # `lazy="joined"` (`orders/models.py:132`) и приезжает тем же JOIN'ом.
                        # Уберут refresh — обращение к `item.product` упадёт, уведомление молча не
                        # уйдёт (блок ниже глотает исключения). Это стережёт тест
                        # `test_notifications_release_form_points_path`.
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
                    points_applied=order.points_applied,
                )
        except Exception:  # notification is best-effort, never blocks money
            logger.error("Failed to send Telegram notification")

    return order


@router.post("/quote", response_model=OrderQuote)
async def quote_order(order_data: OrderCreate, db: DbSession, user: CurrentUser) -> OrderQuote:
    """Preview authoritative terms without creating an order or changing reservations."""
    try:
        return await service.build_quote(db, order_data, user_id=user.id)
    except service.InvalidRedeemAmountError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_redeem_amount",
                "points_to_apply": exc.points,
                "max_redeemable": exc.max_redeemable,
            },
        ) from exc
    except service.OrderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reservations/release", response_model=ReleaseReservationsResponse)
async def release_reservations(
    db: DbSession,
    user: CurrentUser,
) -> ReleaseReservationsResponse:
    """Release the points the caller's own unfinished orders are holding — no waiting.

    Points spent partially are RESERVED, not debited (Model-1): the unpaid order IS the reservation.
    Until now the only way to get them back was the lazy cleanup, which waits
    :data:`~src.orders.service.REDEEM_RESERVE_TIMEOUT` because it cannot know whether the customer is
    still paying. Here the customer has said so themselves, so the age bar is dropped — safety still
    comes from deleting the Multicard invoice BEFORE cancelling, never from waiting.

    Committed immediately, before the response is shaped: the invoice DELETE already happened
    externally, and letting a later failure roll the cancellation back would leave a dead invoice
    beside a live reservation.

    A 409 is reserved for "nothing could be freed although something is held" — every other shape,
    including releasing nothing because nothing was held (a second click), is a 200.
    """
    outcome = await service.expire_stale_partials(db, user_id=user.id, max_age=None)
    await db.commit()

    if outcome.released_orders == 0 and outcome.blocked_orders > 0:
        # Deletion was refused for every reservation. The cause cannot be told apart here —
        # ``delete_invoice`` returns False both for an already-paid invoice and for a transport
        # failure — so the client must not be told a cause as if it were a fact.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "reservations_not_released",
                "blocked_orders": outcome.blocked_orders,
            },
        )

    return ReleaseReservationsResponse(
        released_orders=outcome.released_orders,
        released_points=outcome.released_points,
        blocked_orders=outcome.blocked_orders,
    )


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
