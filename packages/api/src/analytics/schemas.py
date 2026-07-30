"""Analytics schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class RevenueDataPoint(BaseModel):
    """Revenue data point for chart."""

    model_config = ConfigDict(from_attributes=True)

    date: str  # YYYY-MM-DD format
    revenue: Decimal
    orders_count: int


class RecentOrderItem(BaseModel):
    """Recent order item for dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    status: str
    total: Decimal
    currency: str
    customer_name: str | None
    created_at: datetime


class TopProductItem(BaseModel):
    """Top selling product."""

    model_config = ConfigDict(from_attributes=True)

    product_id: int
    product_name: str
    total_quantity: int
    total_revenue: Decimal


class OrderStatusCount(BaseModel):
    """Order status count for distribution chart."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    count: int
    percentage: float


class DashboardStats(BaseModel):
    """Dashboard statistics."""

    model_config = ConfigDict(from_attributes=True)

    total_revenue: Decimal
    total_orders: int
    pending_orders: int
    completed_orders: int
    revenue_data: list[RevenueDataPoint]
    recent_orders: list[RecentOrderItem]
    top_products: list[TopProductItem]
    order_status_distribution: list[OrderStatusCount]

    # User statistics
    total_users: int
    buyers_7d: int
    buyers_30d: int
    new_users_today: int
    new_users_7d: int


# --------------------------------------------------------------------------- #
# Biotact Club loyalty monitoring (read-only) — GET /analytics/loyalty
# --------------------------------------------------------------------------- #


class ActivationDataPoint(BaseModel):
    """One day of the QR-activation trend (gaps filled with zero)."""

    model_config = ConfigDict(from_attributes=True)

    date: str  # YYYY-MM-DD format
    count: int


class FunnelStep(BaseModel):
    """One registration-funnel step: distinct users who reached it."""

    model_config = ConfigDict(from_attributes=True)

    step: str  # joined | phone_shared | qr_activated | survey_done
    users: int


class PointsByReason(BaseModel):
    """Signed points total grouped by ledger reason."""

    model_config = ConfigDict(from_attributes=True)

    reason: str
    total: int


class GenderCount(BaseModel):
    """Profile count per declared gender (``unknown`` = not declared)."""

    model_config = ConfigDict(from_attributes=True)

    gender: str
    count: int


class AgeBucketCount(BaseModel):
    """Profile count per age bucket (``unknown`` = age not declared)."""

    model_config = ConfigDict(from_attributes=True)

    bucket: str
    count: int


class CityCount(BaseModel):
    """Profile count per city (top declared cities)."""

    model_config = ConfigDict(from_attributes=True)

    city: str
    count: int


class Demographics(BaseModel):
    """Survey-derived demographics with honest coverage."""

    model_config = ConfigDict(from_attributes=True)

    gender: list[GenderCount]
    age_buckets: list[AgeBucketCount]
    top_cities: list[CityCount]
    survey_coverage: float  # % of profiles with >=1 survey field filled


class LoyaltyStats(BaseModel):
    """Biotact Club monitoring snapshot (mirror of ``DashboardStats``, loyalty domain)."""

    model_config = ConfigDict(from_attributes=True)

    # Block 1 — QR activations (hero)
    qr_generated: int
    qr_activated: int
    qr_activated_7d: int
    qr_activated_30d: int
    activation_rate: float  # qr_activated / qr_generated * 100, 0.0 when generated == 0
    batches_count: int
    activations_by_day: list[ActivationDataPoint]

    # Block 2 — Members
    profiles_total: int
    phone_verified: int
    friends: int

    # Block 3 — Registration funnel
    funnel: list[FunnelStep]
    consents_ever_granted: int

    # Block 3b — Lottery tickets (Stage 5). The third field is a DETECTOR, not a statistic:
    # a missed issuance does not raise anything and does not stop `tickets_total` from growing, so
    # the only way to see it is to count the members who are owed a survey ticket and do not have
    # one. It is expected to read exactly 0; any other value means issuance silently skipped
    # someone, and it says so without anybody reading logs.
    tickets_total: int
    tickets_clean: int
    eligible_without_survey_ticket: int

    # Block 4 — Points economy
    points_earned: int
    points_spent: int  # shown positive
    points_outstanding: int  # SUM(amount) = balance still held by everyone
    points_by_reason: list[PointsByReason]

    # Block 5 — Demographics
    demographics: Demographics
