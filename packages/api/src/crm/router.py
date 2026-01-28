"""CRM API router for admin panel frontend."""

from datetime import datetime, timedelta
from typing import Annotated, Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Query, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from jose import jwt, JWTError

from src.config import settings
from src.database import get_db
from src.users.models import User
from src.orders.models import Order
from src.crm.models import Lead, FamilyMember, LeadStatus


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


# ==================== Schemas ====================

class FamilyMemberSchema(BaseModel):
    """Family member for frontend."""
    name: str
    relation: Optional[str] = None
    age: Optional[int] = None
    problems: List[str] = []


class CustomerSchema(BaseModel):
    """Customer schema for frontend."""
    model_config = ConfigDict(from_attributes=True)

    telegram_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: str = "ru"
    phone: Optional[str] = None
    problems: List[str] = []
    family: List[FamilyMemberSchema] = []
    purchased_products: List[str] = []
    ai_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PaginatedCustomers(BaseModel):
    """Paginated customers response."""
    items: List[CustomerSchema]
    total: int
    page: int
    size: int
    pages: int


class CustomersStats(BaseModel):
    """CRM statistics for frontend."""
    total_customers: int = 0
    new_today: int = 0
    with_phone: int = 0
    with_purchases: int = 0
    by_problem: dict = {}


# ==================== Endpoints ====================

@router.get("/customers", response_model=PaginatedCustomers, dependencies=[Depends(require_admin)])
async def get_customers(
    db: DbSession,
    page: int = Query(1, ge=1),
    size: int = Query(12, ge=1, le=100),
    search: Optional[str] = Query(None),
    problem: Optional[str] = Query(None),
) -> PaginatedCustomers:
    """Get all customers (leads) - everyone who pressed /start."""

    # Base query - ALL users (everyone is a potential lead)
    query = (
        select(User)
        .options(selectinload(User.family_members))
    )
    count_query = select(func.count(User.id))

    # Search filter
    if search:
        search_filter = or_(
            User.first_name.ilike(f"%{search}%"),
            User.last_name.ilike(f"%{search}%"),
            User.username.ilike(f"%{search}%"),
            User.phone.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Problem filter
    if problem:
        query = query.where(User.symptoms.contains([problem]))
        count_query = count_query.where(User.symptoms.contains([problem]))

    # Get total count
    total = await db.scalar(count_query) or 0

    # Apply pagination and ordering
    query = query.order_by(User.updated_at.desc())
    query = query.offset((page - 1) * size).limit(size)

    # Execute
    result = await db.execute(query)
    users = result.unique().scalars().all()

    # Get purchased products for users
    user_ids = [u.id for u in users]
    purchased_products = {}

    if user_ids:
        # Get products from completed orders
        orders_query = (
            select(Order.user_id, Order.items)
            .where(
                Order.user_id.in_(user_ids),
                Order.status.in_(["delivered", "shipped", "processing"])
            )
        )
        orders_result = await db.execute(orders_query)
        for row in orders_result.all():
            if row.user_id not in purchased_products:
                purchased_products[row.user_id] = set()
            if row.items:
                for item in row.items:
                    if isinstance(item, dict) and "name" in item:
                        purchased_products[row.user_id].add(item["name"])

    # Build response
    items = []
    for user in users:
        family = [
            FamilyMemberSchema(
                name=fm.name,
                relation=fm.relation,
                age=fm.age,
                problems=fm.symptoms or [],
            )
            for fm in user.family_members
        ]

        items.append(CustomerSchema(
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            language_code=user.language_code,
            phone=user.phone,
            problems=user.symptoms or [],
            family=family,
            purchased_products=list(purchased_products.get(user.id, [])),
            ai_notes=user.ai_notes,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ))

    pages = (total + size - 1) // size if total else 0

    return PaginatedCustomers(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/customers/stats", response_model=CustomersStats, dependencies=[Depends(require_admin)])
async def get_customers_stats(db: DbSession) -> CustomersStats:
    """Get CRM statistics."""

    # Total customers (all users = all leads)
    total = await db.scalar(select(func.count(User.id))) or 0

    # New today
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    new_today = await db.scalar(
        select(func.count(User.id)).where(User.created_at >= today)
    ) or 0

    # With phone
    with_phone = await db.scalar(
        select(func.count(User.id)).where(User.phone.isnot(None))
    ) or 0

    # With purchases (users who have orders)
    with_purchases = await db.scalar(
        select(func.count(User.id.distinct()))
        .join(Order, Order.user_id == User.id)
    ) or 0

    # By problem (count users per symptom)
    # This is a simplified version - counts users who have each symptom
    by_problem = {
        "immunity": 0,
        "gut": 0,
        "stress": 0,
        "skin": 0,
    }

    for problem_key in by_problem.keys():
        count_query = (
            select(func.count(User.id))
            .where(User.symptoms.contains([problem_key]))
        )
        by_problem[problem_key] = await db.scalar(count_query) or 0

    return CustomersStats(
        total_customers=total,
        new_today=new_today,
        with_phone=with_phone,
        with_purchases=with_purchases,
        by_problem=by_problem,
    )


@router.get("/customer/{telegram_id}", response_model=CustomerSchema, dependencies=[Depends(require_admin)])
async def get_customer(
    telegram_id: int,
    db: DbSession,
) -> CustomerSchema:
    """Get single customer by telegram_id."""

    result = await db.execute(
        select(User)
        .options(selectinload(User.family_members))
        .where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    # Get purchased products
    orders_result = await db.execute(
        select(Order.items)
        .where(
            Order.user_id == user.id,
            Order.status.in_(["delivered", "shipped", "processing"])
        )
    )
    purchased_products = set()
    for row in orders_result.all():
        if row.items:
            for item in row.items:
                if isinstance(item, dict) and "name" in item:
                    purchased_products.add(item["name"])

    family = [
        FamilyMemberSchema(
            name=fm.name,
            relation=fm.relation,
            age=fm.age,
            problems=fm.symptoms or [],
        )
        for fm in user.family_members
    ]

    return CustomerSchema(
        telegram_id=user.telegram_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        language_code=user.language_code,
        phone=user.phone,
        problems=user.symptoms or [],
        family=family,
        purchased_products=list(purchased_products),
        ai_notes=user.ai_notes,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
