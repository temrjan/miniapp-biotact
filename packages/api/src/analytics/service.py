"""Analytics service."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics.schemas import (
    DashboardStats,
    OrderStatusCount,
    RecentOrderItem,
    RevenueDataPoint,
    TopProductItem,
)
from src.orders.models import Order, OrderItem, OrderStatus
from src.users.models import User


async def get_dashboard_stats(db: AsyncSession, days: int = 30) -> DashboardStats:
    """Get dashboard statistics."""

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    today_start = end_date.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total revenue (paid orders only)
    revenue_query = select(func.coalesce(func.sum(Order.total), 0)).where(
        Order.payment_status == "paid"
    )
    total_revenue = await db.scalar(revenue_query)

    # Total orders count
    total_orders_query = select(func.count(Order.id))
    total_orders = await db.scalar(total_orders_query)

    # Pending orders count
    pending_query = select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING)
    pending_orders = await db.scalar(pending_query)

    # Completed orders count
    completed_query = select(func.count(Order.id)).where(Order.status.in_([OrderStatus.DELIVERED]))
    completed_orders = await db.scalar(completed_query)

    # Revenue by day (last N days)
    revenue_by_day_query = (
        select(
            func.date(Order.created_at).label("date"),
            func.coalesce(func.sum(Order.total), 0).label("revenue"),
            func.count(Order.id).label("orders_count"),
        )
        .where(
            Order.created_at >= start_date,
            Order.payment_status == "paid",
        )
        .group_by(func.date(Order.created_at))
        .order_by(func.date(Order.created_at))
    )

    revenue_result = await db.execute(revenue_by_day_query)
    revenue_rows = revenue_result.all()

    # Fill in missing dates with zero revenue
    revenue_data = []
    current_date = start_date.date()
    revenue_dict = {row.date: row for row in revenue_rows}

    while current_date <= end_date.date():
        date_str = current_date.isoformat()
        if current_date in revenue_dict:
            row = revenue_dict[current_date]
            revenue_data.append(
                RevenueDataPoint(
                    date=date_str,
                    revenue=row.revenue,
                    orders_count=row.orders_count,
                )
            )
        else:
            revenue_data.append(
                RevenueDataPoint(
                    date=date_str,
                    revenue=Decimal("0"),
                    orders_count=0,
                )
            )
        current_date += timedelta(days=1)

    # Recent orders (last 10)
    recent_orders_query = select(Order).order_by(Order.created_at.desc()).limit(10)

    recent_result = await db.execute(recent_orders_query)
    recent_orders_rows = recent_result.scalars().all()

    recent_orders = [
        RecentOrderItem(
            id=order.id,
            order_number=order.order_number,
            status=order.status.value,
            total=order.total,
            currency=order.currency,
            customer_name=order.delivery_name,
            created_at=order.created_at,
        )
        for order in recent_orders_rows
    ]

    # Top Products (by quantity sold, last N days)
    top_products_query = (
        select(
            OrderItem.product_id,
            OrderItem.product_name,
            func.sum(OrderItem.quantity).label("total_quantity"),
            func.sum(OrderItem.total).label("total_revenue"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.created_at >= start_date,
            Order.payment_status == "paid",
            OrderItem.product_id.isnot(None),
        )
        .group_by(OrderItem.product_id, OrderItem.product_name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(5)
    )

    top_products_result = await db.execute(top_products_query)
    top_products_rows = top_products_result.all()

    top_products = [
        TopProductItem(
            product_id=row.product_id,
            product_name=row.product_name,
            total_quantity=row.total_quantity or 0,
            total_revenue=row.total_revenue or Decimal("0"),
        )
        for row in top_products_rows
    ]

    # Order Status Distribution
    status_query = select(
        Order.status,
        func.count(Order.id).label("count"),
    ).group_by(Order.status)

    status_result = await db.execute(status_query)
    status_rows = status_result.all()

    status_counts = [int(row.count) for row in status_rows]  # type: ignore[call-overload]
    total_for_percentage = sum(status_counts) or 1

    order_status_distribution = []
    for row in status_rows:
        count = int(row.count)  # type: ignore[call-overload]
        order_status_distribution.append(
            OrderStatusCount(
                status=row.status.value,
                count=count,
                percentage=round((count / total_for_percentage) * 100, 1),
            )
        )

    # User Statistics
    # Total users
    total_users = await db.scalar(select(func.count(User.id))) or 0

    # Buyers in last 7 days (users who made orders)
    buyers_7d_query = select(func.count(func.distinct(Order.user_id))).where(
        Order.created_at >= end_date - timedelta(days=7)
    )
    buyers_7d = await db.scalar(buyers_7d_query) or 0

    # Buyers in last 30 days
    buyers_30d_query = select(func.count(func.distinct(Order.user_id))).where(
        Order.created_at >= end_date - timedelta(days=30)
    )
    buyers_30d = await db.scalar(buyers_30d_query) or 0

    # New users today
    new_users_today = (
        await db.scalar(select(func.count(User.id)).where(User.created_at >= today_start)) or 0
    )

    # New users in last 7 days
    new_users_7d = (
        await db.scalar(
            select(func.count(User.id)).where(User.created_at >= end_date - timedelta(days=7))
        )
        or 0
    )

    return DashboardStats(
        total_revenue=total_revenue or Decimal("0"),
        total_orders=total_orders or 0,
        pending_orders=pending_orders or 0,
        completed_orders=completed_orders or 0,
        revenue_data=revenue_data,
        recent_orders=recent_orders,
        top_products=top_products,
        order_status_distribution=order_status_distribution,
        total_users=total_users,
        buyers_7d=buyers_7d,
        buyers_30d=buyers_30d,
        new_users_today=new_users_today,
        new_users_7d=new_users_7d,
    )
