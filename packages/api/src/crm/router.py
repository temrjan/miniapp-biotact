"""CRM API router for admin panel frontend."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.database import get_db
from src.loyalty.models import CustomerProfile, PointLedger
from src.loyalty.predicates import survey_complete_sql
from src.loyalty.schemas import RegistrationSource
from src.orders.models import Order, OrderItem
from src.users.models import User

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
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    if payload.get("type") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not an admin token",
        )

    user_id = int(payload.get("sub", 0))
    result = await db.execute(select(User).where(User.id == user_id))
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
    relation: str | None = None
    age: int | None = None
    problems: list[str] = []


class CustomerLoyaltySchema(BaseModel):
    """Biotact Club slice of a customer — present only for club members.

    Survey fields are an UNCONSTRAINED echo of the DB (same decision as GET /loyalty/me):
    the response mirrors what is stored, it does not validate input.
    """

    source: str | None = None  # 'qr' | 'direct' | None (column is nullable, first writer wins)
    status: str  # LoyaltyStatus VALUE on the wire: 'newbie' | 'biotact_friend'
    lifetime_activations: int
    gender: str | None = None
    age: int | None = None
    city: str | None = None
    balance: int  # SUM(point_ledger.amount); may go negative once REDEEM exists


class CustomerSchema(BaseModel):
    """Customer schema for frontend."""

    model_config = ConfigDict(from_attributes=True)

    telegram_id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str = "ru"
    phone: str | None = None
    problems: list[str] = []
    family: list[FamilyMemberSchema] = []
    purchased_products: list[str] = []
    ai_notes: str | None = None
    created_at: datetime
    updated_at: datetime
    loyalty: CustomerLoyaltySchema | None = None  # None ⇔ not a Biotact Club member


class PaginatedCustomers(BaseModel):
    """Paginated customers response."""

    items: list[CustomerSchema]
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
    by_problem: dict[str, int] = {}


async def _loyalty_by_user_id(
    db: AsyncSession, user_ids: list[int]
) -> dict[int, CustomerLoyaltySchema]:
    """Biotact Club data for a set of users in exactly two queries (profiles + balances).

    Users without a ``customer_profile`` row are simply absent from the result — the
    caller renders them as non-members (``loyalty=None``).
    """
    if not user_ids:
        return {}

    profiles = (
        await db.execute(
            select(
                CustomerProfile.user_id,
                CustomerProfile.registration_source,
                CustomerProfile.status,
                CustomerProfile.lifetime_activations,
                CustomerProfile.gender,
                CustomerProfile.age,
                CustomerProfile.city,
            ).where(CustomerProfile.user_id.in_(user_ids))
        )
    ).all()
    if not profiles:
        return {}

    member_ids = [row.user_id for row in profiles]
    balance_rows = (
        await db.execute(
            select(PointLedger.user_id, func.sum(PointLedger.amount).label("total"))
            .where(PointLedger.user_id.in_(member_ids))
            .group_by(PointLedger.user_id)
        )
    ).all()
    balances = {row.user_id: int(row.total) for row in balance_rows}

    return {
        row.user_id: CustomerLoyaltySchema(
            source=row.registration_source,
            status=row.status.value,
            lifetime_activations=int(row.lifetime_activations),
            gender=row.gender.value if row.gender is not None else None,
            age=row.age,
            city=row.city,
            balance=balances.get(row.user_id, 0),  # no ledger rows yet → 0
        )
        for row in profiles
    }


# ==================== Endpoints ====================


@router.get("/customers", response_model=PaginatedCustomers, dependencies=[Depends(require_admin)])
async def get_customers(
    db: DbSession,
    page: int = Query(1, ge=1),
    size: int = Query(12, ge=1, le=100),
    search: str | None = Query(None),
    problem: str | None = Query(None),
    club: Literal["member", "qr", "no_survey"] | None = Query(None),
) -> PaginatedCustomers:
    """Get all customers (leads) - everyone who pressed /start."""

    # Base query - ALL users (everyone is a potential lead)
    query = select(User).options(selectinload(User.family_members))
    count_query = select(func.count(User.id))

    # Biotact Club filter — INNER JOIN on the (unique-per-user) loyalty profile row.
    # Applied to BOTH the data and the count query, or total/pages would lie.
    if club is not None:
        query = query.join(CustomerProfile, CustomerProfile.user_id == User.id)
        count_query = count_query.join(CustomerProfile, CustomerProfile.user_id == User.id)
        if club == "qr":
            # The column stores the enum VALUE ('qr'), so compare with .value explicitly.
            qr_source = RegistrationSource.QR.value
            query = query.where(CustomerProfile.registration_source == qr_source)
            count_query = count_query.where(CustomerProfile.registration_source == qr_source)
        elif club == "no_survey":
            # Members who were EVER credited points and never finished the survey — the list staff
            # work through to remind them, not an analytics figure.
            #
            # "Ever credited", not "still holds a balance" (Captain's ruling, 2026-08-10, against
            # the opposite reading proposed at Gate-1): spending the points again does not finish
            # the survey, so it does not settle the reminder. The ledger answers this directly
            # because it is append-only — an earn row stays forever and a redeem arrives as its own
            # negative row, so a member who spent everything still carries the credit that happened.
            # `lifetime_activations` would answer a NEARBY question (how many activations), is
            # maintained by a separate UPDATE, and says "yes" even where the cashback rounded to
            # zero and nothing was actually credited.
            #
            # EXISTS rather than SUM: the question is whether one such row exists, so the scan can
            # stop at the first — and a SUM would have to be compared against a threshold that is
            # exactly the reading being rejected here.
            #
            # Asked inside the query, not after the page is fetched (the way `_loyalty_by_user_id`
            # computes balances for display): filtering afterwards would hand back a short page
            # while `total` counted a different, larger population.
            #
            # The survey rule is NOT restated here. `survey_complete_sql` is the same expression
            # `eligible_sql` is built from, so this filter cannot drift away from the definition
            # the draw itself uses; `not_` is exact because `IS NOT NULL` never yields NULL. It is
            # imported from `predicates`, not `lottery`: the latter reaches `src.auth` and would
            # make importing THIS module hit a package cycle (measured, not assumed).
            ever_earned = (
                select(PointLedger.id)
                .where(
                    PointLedger.user_id == CustomerProfile.user_id,
                    PointLedger.amount > 0,
                )
                # Correlate the profile only. Left to itself SQLAlchemy would also correlate away
                # `point_ledger` if an enclosing query ever joined it, emitting a subquery with no
                # FROM — the trap already paid for in `owed_survey_ticket_sql`.
                .correlate(CustomerProfile)
                .exists()
            )
            no_survey = not_(survey_complete_sql())
            query = query.where(no_survey, ever_earned)
            count_query = count_query.where(no_survey, ever_earned)

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
    purchased_products: dict[int, set[str]] = {}

    if user_ids:
        # Get products from completed orders. ``OrderItem`` joins explicitly — selecting the
        # ``Order.items`` relationship here puts both tables in FROM with no join condition
        # (SAWarning: cartesian product), and the items column of each row comes back as a
        # boolean, not item dicts.
        orders_query = (
            select(Order.user_id, OrderItem.product_name)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                Order.user_id.in_(user_ids),
                Order.status.in_(["delivered", "shipped", "processing"]),
            )
        )
        orders_result = await db.execute(orders_query)
        for row in orders_result.all():
            purchased_products.setdefault(row.user_id, set()).add(row.product_name)

    # Biotact Club data for the page (2 queries total, not N+1)
    loyalty_by_user_id = await _loyalty_by_user_id(db, user_ids)

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

        items.append(
            CustomerSchema(
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
                loyalty=loyalty_by_user_id.get(user.id),
            )
        )

    pages = (total + size - 1) // size if total else 0

    return PaginatedCustomers(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get(
    "/customers/stats", response_model=CustomersStats, dependencies=[Depends(require_admin)]
)
async def get_customers_stats(db: DbSession) -> CustomersStats:
    """Get CRM statistics."""

    # Total customers (all users = all leads)
    total = await db.scalar(select(func.count(User.id))) or 0

    # New today
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    new_today = await db.scalar(select(func.count(User.id)).where(User.created_at >= today)) or 0

    # With phone
    with_phone = await db.scalar(select(func.count(User.id)).where(User.phone.isnot(None))) or 0

    # With purchases (users who have orders)
    with_purchases = (
        await db.scalar(
            select(func.count(User.id.distinct())).join(Order, Order.user_id == User.id)
        )
        or 0
    )

    # By problem (count users per symptom)
    # This is a simplified version - counts users who have each symptom
    by_problem = {
        "immunity": 0,
        "gut": 0,
        "stress": 0,
        "skin": 0,
    }

    for problem_key in by_problem:
        count_query = select(func.count(User.id)).where(User.symptoms.contains([problem_key]))
        by_problem[problem_key] = await db.scalar(count_query) or 0

    return CustomersStats(
        total_customers=total,
        new_today=new_today,
        with_phone=with_phone,
        with_purchases=with_purchases,
        by_problem=by_problem,
    )


@router.get(
    "/customer/{telegram_id}", response_model=CustomerSchema, dependencies=[Depends(require_admin)]
)
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

    # Get purchased products (explicit join — see the customers-list query above)
    orders_result = await db.execute(
        select(OrderItem.product_name)
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.user_id == user.id,
            Order.status.in_(["delivered", "shipped", "processing"]),
        )
    )
    purchased_products = {row.product_name for row in orders_result.all()}

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
        loyalty=(await _loyalty_by_user_id(db, [user.id])).get(user.id),
    )
