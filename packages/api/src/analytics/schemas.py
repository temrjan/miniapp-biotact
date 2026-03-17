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
