"""CRM Bot API router for AI assistant."""

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.crm.models import FamilyMember, Lead, LeadStatus
from src.crm.schemas import (
    BotCustomerData,
    BotCustomerResponse,
    CustomerSummary,
    FamilyMemberCreate,
    FamilyMemberResponse,
    LeadCreate,
    LeadResponse,
)
from src.database import get_db
from src.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def verify_bot_token(
    x_bot_token: Annotated[str | None, Header()] = None,
) -> None:
    """Verify bot API token in constant time (audit M4 a).

    Bytes, not str: ``compare_digest`` raises on a non-ASCII str, which would be a 500.
    """
    expected = settings.bot_api_token.get_secret_value().encode("utf-8")
    if not x_bot_token or not hmac.compare_digest(x_bot_token.encode("utf-8"), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bot token",
        )


# ==================== Customer ====================


@router.get(
    "/customer/{telegram_id}",
    response_model=BotCustomerResponse,
    dependencies=[Depends(verify_bot_token)],
)
async def get_customer(
    telegram_id: int,
    db: DbSession,
) -> BotCustomerResponse:
    """Get customer info by telegram_id for AI context."""

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.family_members),
            selectinload(User.leads),
        )
        .where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        return BotCustomerResponse(
            customer=None,
            family=[],
            recent_leads=[],
        )

    customer = CustomerSummary(
        id=user.id,
        telegram_id=user.telegram_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        phone=user.phone,
        symptoms=user.symptoms or [],
    )

    family = [
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

    # Get recent leads (last 5)
    recent_leads = [
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
        for lead in sorted(user.leads, key=lambda x: x.created_at, reverse=True)[:5]
    ]

    return BotCustomerResponse(
        customer=customer,
        family=family,
        recent_leads=recent_leads,
    )


@router.post("/customer", response_model=CustomerSummary, dependencies=[Depends(verify_bot_token)])
async def save_customer(
    data: BotCustomerData,
    db: DbSession,
) -> CustomerSummary:
    """Create or update customer from bot."""

    result = await db.execute(select(User).where(User.telegram_id == data.telegram_id))
    user = result.scalar_one_or_none()

    if user:
        # Update existing user
        if data.name:
            user.first_name = data.name
        if data.username:
            user.username = data.username
        if data.phone:
            user.phone = data.phone
        if data.symptoms is not None:
            user.symptoms = data.symptoms
        if data.ai_notes is not None:
            user.ai_notes = data.ai_notes
    else:
        # Create new user with race condition handling
        user = User(
            telegram_id=data.telegram_id,
            first_name=data.name,
            username=data.username,
            phone=data.phone,
            symptoms=data.symptoms or [],
            ai_notes=data.ai_notes,
        )
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as exc:
            logger.warning(
                "Race condition in save_customer for telegram_id=%s, fetching existing",
                data.telegram_id,
            )
            await db.rollback()
            result = await db.execute(select(User).where(User.telegram_id == data.telegram_id))
            user = result.scalar_one_or_none()
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create or find customer",
                ) from exc

    await db.commit()
    await db.refresh(user)

    return CustomerSummary(
        id=user.id,
        telegram_id=user.telegram_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        phone=user.phone,
        symptoms=user.symptoms or [],
    )


# ==================== Family Members ====================


@router.post(
    "/customer/{telegram_id}/family",
    response_model=FamilyMemberResponse,
    dependencies=[Depends(verify_bot_token)],
)
async def add_family_member(
    telegram_id: int,
    data: FamilyMemberCreate,
    db: DbSession,
) -> FamilyMemberResponse:
    """Add family member to customer."""

    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    family_member = FamilyMember(
        user_id=user.id,
        name=data.name,
        relation=data.relation,
        age=data.age,
        symptoms=data.symptoms or [],
    )
    db.add(family_member)

    await db.commit()
    await db.refresh(family_member)

    return FamilyMemberResponse(
        id=family_member.id,
        user_id=family_member.user_id,
        name=family_member.name,
        relation=family_member.relation,
        age=family_member.age,
        symptoms=family_member.symptoms or [],
        created_at=family_member.created_at,
    )


# ==================== Leads ====================


@router.post("/lead", response_model=LeadResponse, dependencies=[Depends(verify_bot_token)])
async def create_lead(
    data: LeadCreate,
    db: DbSession,
) -> LeadResponse:
    """Create lead from bot (when customer is ready to buy)."""

    # Find user by telegram_id
    result = await db.execute(select(User).where(User.telegram_id == data.telegram_id))
    user = result.scalar_one_or_none()

    lead = Lead(
        user_id=user.id if user else None,
        telegram_id=data.telegram_id,
        status=LeadStatus.NEW,
        request_text=data.request_text,
        ai_summary=data.ai_summary,
        recommended_products=data.recommended_products or [],
    )
    db.add(lead)

    await db.commit()
    await db.refresh(lead)

    # TODO: Send notification to Telegram sales group

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
