"""Analytics service."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics.schemas import (
    ActivationDataPoint,
    AgeBucketCount,
    CityCount,
    DashboardStats,
    Demographics,
    FunnelStep,
    GenderCount,
    LoyaltyStats,
    OrderStatusCount,
    PointsByReason,
    RecentOrderItem,
    RevenueDataPoint,
    TopProductItem,
)
from src.loyalty.lottery import ADULT_AGE
from src.loyalty.models import (
    CodeBatch,
    Consent,
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
    LotteryTicket,
    LotteryTicketSource,
    LoyaltyStatus,
    PointLedger,
    QrCode,
    QrCodeStatus,
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


async def get_loyalty_stats(db: AsyncSession, days: int = 30) -> LoyaltyStats:
    """Get Biotact Club monitoring statistics (admin only, read-only, no writes).

    Live aggregation over the loyalty tables. Every count/sum is guarded so an empty
    program (the current prod state — 0 activations) returns zeros and 200, never 500.
    Enum columns (``customer_profile.status``, stored in PG as the StrEnum NAME) are
    filtered by the enum MEMBER for clarity and to match the codebase canon; SQLAlchemy's
    Enum binder maps the member to the stored NAME. String columns
    (``customer_event.event_type``, ``point_ledger.reason``, ``qr_codes.status``) store the
    enum VALUE and are filtered by ``.value``.
    """
    now = datetime.now(UTC)
    start_7d = now - timedelta(days=7)
    start_30d = now - timedelta(days=30)
    trend_start = now - timedelta(days=days)

    # --- Block 1: QR activations (hero) ---
    qr_generated = await db.scalar(select(func.count(QrCode.id))) or 0
    qr_activated = (
        await db.scalar(
            select(func.count(QrCode.id)).where(QrCode.status == QrCodeStatus.USED.value)
        )
        or 0
    )
    batches_count = await db.scalar(select(func.count(CodeBatch.id))) or 0

    qr_activated_7d = (
        await db.scalar(
            select(func.count(CustomerEvent.id)).where(
                CustomerEvent.event_type == CustomerEventType.QR_ACTIVATED.value,
                CustomerEvent.occurred_at >= start_7d,
            )
        )
        or 0
    )
    qr_activated_30d = (
        await db.scalar(
            select(func.count(CustomerEvent.id)).where(
                CustomerEvent.event_type == CustomerEventType.QR_ACTIVATED.value,
                CustomerEvent.occurred_at >= start_30d,
            )
        )
        or 0
    )
    activation_rate = round(qr_activated / qr_generated * 100, 1) if qr_generated else 0.0

    # Activation trend over the `days` window; fill missing days with zero (like revenue_data).
    trend_query = (
        select(
            func.date(CustomerEvent.occurred_at).label("date"),
            func.count(CustomerEvent.id).label("cnt"),
        )
        .where(
            CustomerEvent.event_type == CustomerEventType.QR_ACTIVATED.value,
            CustomerEvent.occurred_at >= trend_start,
        )
        .group_by(func.date(CustomerEvent.occurred_at))
        .order_by(func.date(CustomerEvent.occurred_at))
    )
    trend_rows = (await db.execute(trend_query)).all()
    trend_dict = {row.date: int(row.cnt) for row in trend_rows}

    activations_by_day: list[ActivationDataPoint] = []
    current_date = trend_start.date()
    end_date = now.date()
    while current_date <= end_date:
        activations_by_day.append(
            ActivationDataPoint(
                date=current_date.isoformat(),
                count=trend_dict.get(current_date, 0),
            )
        )
        current_date += timedelta(days=1)

    # --- Block 2: Members ---
    profiles_total = await db.scalar(select(func.count(CustomerProfile.id))) or 0
    phone_verified = (
        await db.scalar(select(func.count(User.id)).where(User.phone_verified_at.isnot(None))) or 0
    )
    friends = (
        await db.scalar(
            select(func.count(CustomerProfile.id)).where(
                CustomerProfile.status == LoyaltyStatus.BIOTACT_FRIEND
            )
        )
        or 0
    )

    # --- Block 3: Registration funnel (distinct users per step, fixed order) ---
    funnel: list[FunnelStep] = []
    for event_type in (
        CustomerEventType.JOINED,
        CustomerEventType.PHONE_SHARED,
        CustomerEventType.QR_ACTIVATED,
        CustomerEventType.SURVEY_DONE,
    ):
        step_users = (
            await db.scalar(
                select(func.count(func.distinct(CustomerEvent.user_id))).where(
                    CustomerEvent.event_type == event_type.value
                )
            )
            or 0
        )
        funnel.append(FunnelStep(step=event_type.value, users=step_users))

    consents_ever_granted = (
        await db.scalar(
            select(func.count(func.distinct(Consent.user_id))).where(Consent.granted.is_(True))
        )
        or 0
    )

    # --- Block 4: Points economy (balance = SUM, never a stored column) ---
    points_earned = int(
        await db.scalar(
            select(func.coalesce(func.sum(PointLedger.amount), 0)).where(PointLedger.amount > 0)
        )
        or 0
    )
    points_spent = -int(
        await db.scalar(
            select(func.coalesce(func.sum(PointLedger.amount), 0)).where(PointLedger.amount < 0)
        )
        or 0
    )
    points_outstanding = int(
        await db.scalar(select(func.coalesce(func.sum(PointLedger.amount), 0))) or 0
    )
    reason_rows = (
        await db.execute(
            select(
                PointLedger.reason,
                func.coalesce(func.sum(PointLedger.amount), 0).label("total"),
            )
            .group_by(PointLedger.reason)
            .order_by(func.coalesce(func.sum(PointLedger.amount), 0).desc())
        )
    ).all()
    points_by_reason = [
        PointsByReason(reason=row.reason, total=int(row.total)) for row in reason_rows
    ]

    # --- Block 5: Demographics (survey-derived, with honest coverage) ---
    gender_rows = (
        await db.execute(
            select(CustomerProfile.gender, func.count(CustomerProfile.id).label("cnt"))
            .group_by(CustomerProfile.gender)
            .order_by(func.count(CustomerProfile.id).desc())
        )
    ).all()
    gender = [
        GenderCount(
            gender=row.gender.value if row.gender is not None else "unknown",
            count=int(row.cnt),
        )
        for row in gender_rows
    ]

    age_bucket = case(
        (CustomerProfile.age.is_(None), "unknown"),
        (CustomerProfile.age < 25, "18-24"),
        (CustomerProfile.age < 35, "25-34"),
        (CustomerProfile.age < 45, "35-44"),
        (CustomerProfile.age < 55, "45-54"),
        else_="55+",
    )
    age_rows = (
        await db.execute(
            select(age_bucket.label("bucket"), func.count(CustomerProfile.id).label("cnt"))
            .group_by(age_bucket)
            .order_by(func.count(CustomerProfile.id).desc())
        )
    ).all()
    age_buckets = [AgeBucketCount(bucket=row.bucket, count=int(row.cnt)) for row in age_rows]

    city_rows = (
        await db.execute(
            select(CustomerProfile.city, func.count(CustomerProfile.id).label("cnt"))
            .where(CustomerProfile.city.isnot(None))
            .group_by(CustomerProfile.city)
            .order_by(func.count(CustomerProfile.id).desc())
            .limit(5)
        )
    ).all()
    top_cities = [CityCount(city=row.city, count=int(row.cnt)) for row in city_rows]

    profiles_with_survey = (
        await db.scalar(
            select(func.count(CustomerProfile.id)).where(
                or_(
                    CustomerProfile.gender.isnot(None),
                    CustomerProfile.age.isnot(None),
                    CustomerProfile.city.isnot(None),
                    CustomerProfile.purchase_goal.isnot(None),
                    CustomerProfile.purchase_for_whom.isnot(None),
                )
            )
        )
        or 0
    )
    survey_coverage = (
        round(profiles_with_survey / profiles_total * 100, 1) if profiles_total else 0.0
    )

    # --- Block 3b: lottery tickets, with a detector for silently skipped issuance ---
    tickets_total = await db.scalar(select(func.count(LotteryTicket.id))) or 0
    tickets_clean = (
        await db.scalar(
            select(func.count(LotteryTicket.id)).where(LotteryTicket.draw_id.is_(None))
        )
        or 0
    )
    # The eligibility predicates of `loyalty.lottery` expressed in SQL, because an aggregate cannot
    # call them row by row. The duplication is real and is bound by a test that seeds the full
    # matrix and asserts this count agrees with the Python predicates — without it the two would
    # drift the first time the rules change, and the detector would start lying.
    adult_sql = case(
        (CustomerProfile.age.is_not(None), CustomerProfile.age >= ADULT_AGE),
        else_=CustomerProfile.is_adult,
    )
    eligible_without_survey_ticket = (
        await db.scalar(
            select(func.count(CustomerProfile.id)).where(
                CustomerProfile.gender.is_not(None),
                CustomerProfile.city.is_not(None),
                adult_sql,
                ~select(LotteryTicket.id)
                .where(
                    LotteryTicket.user_id == CustomerProfile.user_id,
                    LotteryTicket.source == LotteryTicketSource.SURVEY.value,
                )
                .exists(),
            )
        )
        or 0
    )

    return LoyaltyStats(
        qr_generated=qr_generated,
        qr_activated=qr_activated,
        qr_activated_7d=qr_activated_7d,
        qr_activated_30d=qr_activated_30d,
        activation_rate=activation_rate,
        batches_count=batches_count,
        activations_by_day=activations_by_day,
        profiles_total=profiles_total,
        phone_verified=phone_verified,
        friends=friends,
        funnel=funnel,
        consents_ever_granted=consents_ever_granted,
        tickets_total=tickets_total,
        tickets_clean=tickets_clean,
        eligible_without_survey_ticket=eligible_without_survey_ticket,
        points_earned=points_earned,
        points_spent=points_spent,
        points_outstanding=points_outstanding,
        points_by_reason=points_by_reason,
        demographics=Demographics(
            gender=gender,
            age_buckets=age_buckets,
            top_cities=top_cities,
            survey_coverage=survey_coverage,
        ),
    )
