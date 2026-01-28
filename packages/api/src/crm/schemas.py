"""CRM Pydantic schemas."""

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict

from src.crm.models import LeadStatus


# ==================== Family Members ====================

class FamilyMemberBase(BaseModel):
    """Base family member schema."""
    name: str
    relation: Optional[str] = None
    age: Optional[int] = None
    symptoms: Optional[List[str]] = []


class FamilyMemberCreate(FamilyMemberBase):
    """Create family member schema."""
    pass


class FamilyMemberUpdate(BaseModel):
    """Update family member schema."""
    name: Optional[str] = None
    relation: Optional[str] = None
    age: Optional[int] = None
    symptoms: Optional[List[str]] = None


class FamilyMemberResponse(FamilyMemberBase):
    """Family member response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime


# ==================== Leads ====================

class LeadBase(BaseModel):
    """Base lead schema."""
    request_text: Optional[str] = None
    ai_summary: Optional[str] = None
    recommended_products: Optional[List[str]] = []


class LeadCreate(LeadBase):
    """Create lead schema (for bot)."""
    telegram_id: int


class LeadUpdate(BaseModel):
    """Update lead schema (for admin)."""
    status: Optional[LeadStatus] = None
    manager_notes: Optional[str] = None


class LeadResponse(LeadBase):
    """Lead response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    telegram_id: int
    status: LeadStatus
    manager_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ==================== Customer (for admin) ====================

class CustomerSummary(BaseModel):
    """Customer summary for lead list."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    symptoms: Optional[List[str]] = []


class LeadWithCustomer(LeadResponse):
    """Lead with customer info."""
    customer: Optional[CustomerSummary] = None


# ==================== Funnel Stats ====================

class FunnelStats(BaseModel):
    """CRM funnel statistics."""
    new: int = 0
    in_progress: int = 0
    converted: int = 0
    closed: int = 0
    total: int = 0
    conversion_rate: float = 0.0


class LeadsListResponse(BaseModel):
    """Paginated leads list with stats."""
    items: List[LeadWithCustomer]
    total: int
    page: int
    size: int
    pages: int
    stats: FunnelStats


# ==================== Customer Detail ====================

class CustomerDetail(CustomerSummary):
    """Full customer detail for CRM."""
    ai_notes: Optional[str] = None
    created_at: datetime
    family_members: List[FamilyMemberResponse] = []
    leads: List[LeadResponse] = []


# ==================== Bot API Schemas ====================

class BotCustomerData(BaseModel):
    """Customer data for bot API."""
    telegram_id: int
    name: Optional[str] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    symptoms: Optional[List[str]] = None
    ai_notes: Optional[str] = None


class BotCustomerResponse(BaseModel):
    """Customer info response for bot."""
    customer: Optional[CustomerSummary] = None
    family: List[FamilyMemberResponse] = []
    recent_leads: List[LeadResponse] = []
