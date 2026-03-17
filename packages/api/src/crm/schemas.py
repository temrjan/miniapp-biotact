"""CRM Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.crm.models import LeadStatus

# ==================== Family Members ====================

class FamilyMemberBase(BaseModel):
    """Base family member schema."""
    name: str
    relation: str | None = None
    age: int | None = None
    symptoms: list[str] | None = []


class FamilyMemberCreate(FamilyMemberBase):
    """Create family member schema."""
    pass


class FamilyMemberUpdate(BaseModel):
    """Update family member schema."""
    name: str | None = None
    relation: str | None = None
    age: int | None = None
    symptoms: list[str] | None = None


class FamilyMemberResponse(FamilyMemberBase):
    """Family member response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime


# ==================== Leads ====================

class LeadBase(BaseModel):
    """Base lead schema."""
    request_text: str | None = None
    ai_summary: str | None = None
    recommended_products: list[str] | None = []


class LeadCreate(LeadBase):
    """Create lead schema (for bot)."""
    telegram_id: int


class LeadUpdate(BaseModel):
    """Update lead schema (for admin)."""
    status: LeadStatus | None = None
    manager_notes: str | None = None


class LeadResponse(LeadBase):
    """Lead response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None = None
    telegram_id: int
    status: LeadStatus
    manager_notes: str | None = None
    created_at: datetime
    updated_at: datetime


# ==================== Customer (for admin) ====================

class CustomerSummary(BaseModel):
    """Customer summary for lead list."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    symptoms: list[str] | None = []


class LeadWithCustomer(LeadResponse):
    """Lead with customer info."""
    customer: CustomerSummary | None = None


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
    items: list[LeadWithCustomer]
    total: int
    page: int
    size: int
    pages: int
    stats: FunnelStats


# ==================== Customer Detail ====================

class CustomerDetail(CustomerSummary):
    """Full customer detail for CRM."""
    ai_notes: str | None = None
    created_at: datetime
    family_members: list[FamilyMemberResponse] = []
    leads: list[LeadResponse] = []


# ==================== Bot API Schemas ====================

class BotCustomerData(BaseModel):
    """Customer data for bot API."""
    telegram_id: int
    name: str | None = None
    username: str | None = None
    phone: str | None = None
    symptoms: list[str] | None = None
    ai_notes: str | None = None


class BotCustomerResponse(BaseModel):
    """Customer info response for bot."""
    customer: CustomerSummary | None = None
    family: list[FamilyMemberResponse] = []
    recent_leads: list[LeadResponse] = []
