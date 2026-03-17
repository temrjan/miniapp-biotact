"""CRM Admin API router."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from jose import JWTError, jwt
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.crm.models import Lead, LeadStatus
from src.crm.schemas import (
    CustomerDetail,
    CustomerSummary,
    FamilyMemberResponse,
    FunnelStats,
    LeadResponse,
    LeadsListResponse,
    LeadUpdate,
    LeadWithCustomer,
)
from src.database import get_db
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


# ==================== Leads ====================


@router.get("/leads", response_model=LeadsListResponse, dependencies=[Depends(require_admin)])
async def get_leads(
    db: DbSession,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status_filter: LeadStatus | None = Query(None, alias="status"),
    search: str | None = Query(None),
) -> LeadsListResponse:
    """Get all leads with pagination and stats."""

    # Base query with user join
    query = select(Lead).options(selectinload(Lead.user))
    count_query = select(func.count(Lead.id))

    # Apply filters
    if status_filter:
        query = query.where(Lead.status == status_filter)
        count_query = count_query.where(Lead.status == status_filter)

    if search:
        # Join with users for search
        query = query.join(Lead.user, isouter=True)
        count_query = count_query.join(Lead.user, isouter=True)

        search_filter = or_(
            User.first_name.ilike(f"%{search}%"),
            User.last_name.ilike(f"%{search}%"),
            User.phone.ilike(f"%{search}%"),
            Lead.request_text.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Get total count
    total = await db.scalar(count_query) or 0

    # Get funnel stats
    stats_query = select(Lead.status, func.count(Lead.id).label("count")).group_by(Lead.status)
    stats_result = await db.execute(stats_query)
    stats_rows = stats_result.all()
    stats_data: dict[str, int] = {}
    for row in stats_rows:
        stats_data[row.status] = int(row.count)  # type: ignore[call-overload]

    total_leads = sum(stats_data.values())
    converted = stats_data.get(LeadStatus.CONVERTED, 0)
    conversion_rate = (converted / total_leads * 100) if total_leads > 0 else 0

    stats = FunnelStats(
        new=stats_data.get(LeadStatus.NEW, 0),
        in_progress=stats_data.get(LeadStatus.IN_PROGRESS, 0),
        converted=converted,
        closed=stats_data.get(LeadStatus.CLOSED, 0),
        total=total_leads,
        conversion_rate=round(conversion_rate, 1),
    )

    # Apply pagination and ordering
    query = query.order_by(Lead.created_at.desc())
    query = query.offset((page - 1) * size).limit(size)

    # Execute query
    result = await db.execute(query)
    leads = result.scalars().all()

    # Build response
    items = []
    for lead in leads:
        customer = None
        if lead.user:
            customer = CustomerSummary(
                id=lead.user.id,
                telegram_id=lead.user.telegram_id,
                first_name=lead.user.first_name,
                last_name=lead.user.last_name,
                phone=lead.user.phone,
                symptoms=lead.user.symptoms or [],
            )

        items.append(
            LeadWithCustomer(
                id=lead.id,
                user_id=lead.user_id,
                telegram_id=lead.telegram_id,
                status=lead.status,
                request_text=lead.request_text,
                ai_summary=lead.ai_summary,
                recommended_products=lead.recommended_products or [],
                manager_notes=lead.manager_notes,
                created_at=lead.created_at,
                updated_at=lead.updated_at,
                customer=customer,
            )
        )

    pages = (total + size - 1) // size if total else 0

    return LeadsListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
        stats=stats,
    )


@router.get(
    "/leads/{lead_id}", response_model=LeadWithCustomer, dependencies=[Depends(require_admin)]
)
async def get_lead(
    lead_id: int,
    db: DbSession,
) -> LeadWithCustomer:
    """Get lead details."""

    result = await db.execute(
        select(Lead).options(selectinload(Lead.user)).where(Lead.id == lead_id)
    )
    lead = result.scalar_one_or_none()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    customer = None
    if lead.user:
        customer = CustomerSummary(
            id=lead.user.id,
            telegram_id=lead.user.telegram_id,
            first_name=lead.user.first_name,
            last_name=lead.user.last_name,
            phone=lead.user.phone,
            symptoms=lead.user.symptoms or [],
        )

    return LeadWithCustomer(
        id=lead.id,
        user_id=lead.user_id,
        telegram_id=lead.telegram_id,
        status=lead.status,
        request_text=lead.request_text,
        ai_summary=lead.ai_summary,
        recommended_products=lead.recommended_products or [],
        manager_notes=lead.manager_notes,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        customer=customer,
    )


@router.patch(
    "/leads/{lead_id}", response_model=LeadResponse, dependencies=[Depends(require_admin)]
)
async def update_lead(
    lead_id: int,
    data: LeadUpdate,
    db: DbSession,
) -> LeadResponse:
    """Update lead status or notes."""

    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    if data.status is not None:
        lead.status = data.status
    if data.manager_notes is not None:
        lead.manager_notes = data.manager_notes

    await db.commit()
    await db.refresh(lead)

    return LeadResponse(
        id=lead.id,
        user_id=lead.user_id,
        telegram_id=lead.telegram_id,
        status=lead.status,
        request_text=lead.request_text,
        ai_summary=lead.ai_summary,
        recommended_products=lead.recommended_products or [],
        manager_notes=lead.manager_notes,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


# ==================== Customers ====================


@router.get(
    "/customers/{user_id}", response_model=CustomerDetail, dependencies=[Depends(require_admin)]
)
async def get_customer(
    user_id: int,
    db: DbSession,
) -> CustomerDetail:
    """Get customer details with family and leads."""

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.family_members),
            selectinload(User.leads),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    family_members = [
        FamilyMemberResponse(
            id=fm.id,
            user_id=fm.user_id,
            name=fm.name,
            relation=fm.relation,
            age=fm.age,
            symptoms=fm.symptoms or [],
            created_at=fm.created_at,
        )
        for fm in user.family_members
    ]

    leads = [
        LeadResponse(
            id=lead.id,
            user_id=lead.user_id,
            telegram_id=lead.telegram_id,
            status=lead.status,
            request_text=lead.request_text,
            ai_summary=lead.ai_summary,
            recommended_products=lead.recommended_products or [],
            manager_notes=lead.manager_notes,
            created_at=lead.created_at,
            updated_at=lead.updated_at,
        )
        for lead in sorted(user.leads, key=lambda x: x.created_at, reverse=True)
    ]

    return CustomerDetail(
        id=user.id,
        telegram_id=user.telegram_id,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        symptoms=user.symptoms or [],
        ai_notes=user.ai_notes,
        created_at=user.created_at,
        family_members=family_members,
        leads=leads,
    )


# ==================== Stats ====================


@router.get("/stats", response_model=FunnelStats, dependencies=[Depends(require_admin)])
async def get_crm_stats(db: DbSession) -> FunnelStats:
    """Get CRM funnel statistics."""

    stats_query = select(Lead.status, func.count(Lead.id).label("count")).group_by(Lead.status)

    result = await db.execute(stats_query)
    result_rows = result.all()
    stats_data: dict[str, int] = {}
    for row in result_rows:
        stats_data[row.status] = int(row.count)  # type: ignore[call-overload]

    total = sum(stats_data.values())
    converted = stats_data.get(LeadStatus.CONVERTED, 0)
    conversion_rate = (converted / total * 100) if total > 0 else 0

    return FunnelStats(
        new=stats_data.get(LeadStatus.NEW, 0),
        in_progress=stats_data.get(LeadStatus.IN_PROGRESS, 0),
        converted=converted,
        closed=stats_data.get(LeadStatus.CLOSED, 0),
        total=total,
        conversion_rate=round(conversion_rate, 1),
    )
