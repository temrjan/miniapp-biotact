"""Admin Users API router."""

from datetime import datetime
from typing import Annotated, Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status, Query, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from jose import jwt, JWTError

from src.config import settings
from src.database import get_db
from src.users.models import User
from src.orders.models import Order


router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Dependency to require admin authentication."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    token = authorization.replace("Bearer ", "")

    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if payload.get("type") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not an admin token",
        )

    user_id = int(payload.get("sub", 0))
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


# Schemas
class UserAdminResponse(BaseModel):
    """User response for admin."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    language_code: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    updated_at: datetime
    orders_count: int = 0
    total_spent: Decimal = Decimal("0")


class UserListResponse(BaseModel):
    """Paginated user list response."""
    items: list[UserAdminResponse]
    total: int
    page: int
    size: int
    pages: int


class UserOrderItem(BaseModel):
    """User's order item."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    status: str
    total: Decimal
    currency: str
    payment_status: Optional[str] = None
    created_at: datetime


class UserDetailResponse(UserAdminResponse):
    """User detail with orders."""
    address_street: Optional[str] = None
    address_house: Optional[str] = None
    address_apartment: Optional[str] = None
    recent_orders: list[UserOrderItem] = []


class UserUpdateData(BaseModel):
    """Update user data."""
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


@router.get("", response_model=UserListResponse, dependencies=[Depends(require_admin)])
async def get_all_users(
    db: DbSession,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    active_only: bool = Query(False),
) -> UserListResponse:
    """Get all users with pagination (admin only)."""

    # Base query
    query = select(User)
    count_query = select(func.count(User.id))

    # Apply filters
    if search:
        search_filter = or_(
            User.username.ilike(f"%{search}%"),
            User.first_name.ilike(f"%{search}%"),
            User.last_name.ilike(f"%{search}%"),
            User.phone.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if active_only:
        query = query.where(User.is_active == True)
        count_query = count_query.where(User.is_active == True)

    # Get total count
    total = await db.scalar(count_query)

    # Apply pagination and ordering
    query = query.order_by(User.created_at.desc())
    query = query.offset((page - 1) * size).limit(size)

    # Execute query
    result = await db.execute(query)
    users = result.scalars().all()

    # Get orders stats for each user
    user_ids = [u.id for u in users]

    if user_ids:
        orders_stats_query = (
            select(
                Order.user_id,
                func.count(Order.id).label("orders_count"),
                func.coalesce(func.sum(Order.total), 0).label("total_spent"),
            )
            .where(Order.user_id.in_(user_ids))
            .group_by(Order.user_id)
        )
        orders_result = await db.execute(orders_stats_query)
        orders_stats = {row.user_id: row for row in orders_result.all()}
    else:
        orders_stats = {}

    # Build response
    items = []
    for user in users:
        stats = orders_stats.get(user.id)
        items.append(UserAdminResponse(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            phone=user.phone,
            language_code=user.language_code,
            is_active=user.is_active,
            is_admin=user.is_admin,
            created_at=user.created_at,
            updated_at=user.updated_at,
            orders_count=stats.orders_count if stats else 0,
            total_spent=stats.total_spent if stats else Decimal("0"),
        ))

    # Calculate pages
    pages = (total + size - 1) // size if total else 0

    return UserListResponse(
        items=items,
        total=total or 0,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/{user_id}", response_model=UserDetailResponse, dependencies=[Depends(require_admin)])
async def get_user_details(
    user_id: int,
    db: DbSession,
) -> UserDetailResponse:
    """Get user details with orders (admin only)."""

    # Get user
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get orders stats
    stats_query = (
        select(
            func.count(Order.id).label("orders_count"),
            func.coalesce(func.sum(Order.total), 0).label("total_spent"),
        )
        .where(Order.user_id == user_id)
    )
    stats_result = await db.execute(stats_query)
    stats = stats_result.one()

    # Get recent orders
    orders_query = (
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    orders_result = await db.execute(orders_query)
    orders = orders_result.scalars().all()

    recent_orders = [
        UserOrderItem(
            id=order.id,
            order_number=order.order_number,
            status=order.status.value,
            total=order.total,
            currency=order.currency,
            payment_status=order.payment_status,
            created_at=order.created_at,
        )
        for order in orders
    ]

    return UserDetailResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        language_code=user.language_code,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        updated_at=user.updated_at,
        orders_count=stats.orders_count,
        total_spent=stats.total_spent,
        address_street=user.address_street,
        address_house=user.address_house,
        address_apartment=user.address_apartment,
        recent_orders=recent_orders,
    )


@router.patch("/{user_id}", response_model=UserAdminResponse, dependencies=[Depends(require_admin)])
async def update_user(
    user_id: int,
    data: UserUpdateData,
    db: DbSession,
) -> UserAdminResponse:
    """Update user (admin only)."""

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update fields
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.is_admin is not None:
        user.is_admin = data.is_admin

    await db.commit()
    await db.refresh(user)

    # Get orders stats
    stats_query = (
        select(
            func.count(Order.id).label("orders_count"),
            func.coalesce(func.sum(Order.total), 0).label("total_spent"),
        )
        .where(Order.user_id == user_id)
    )
    stats_result = await db.execute(stats_query)
    stats = stats_result.one()

    return UserAdminResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        language_code=user.language_code,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        updated_at=user.updated_at,
        orders_count=stats.orders_count,
        total_spent=stats.total_spent,
    )
