"""Capture local K1 identity inside the caller's existing order transaction."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.employee_referrals.models import (
    CampaignReplica,
    ReferralIdentity,
    ReferralOrderItemSnapshot,
    ReferralOrderSnapshot,
)
from src.employee_referrals.schemas import canonical_token
from src.orders.models import Order


class CaptureUnavailableError(Exception):
    """Fixed boundary error; it carries no request or database values."""


async def capture_clock(session: AsyncSession) -> datetime:
    now: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    return now


def capture_requested(token: str | None) -> bool:
    if not settings.employee_referral_capture_enabled or token is None or len(token) != 22:
        return False
    try:
        canonical_token(token)
    except ValueError:
        return False
    if settings.database_echo:
        raise CaptureUnavailableError("Referral capture unavailable")
    return True


async def capture_order(session: AsyncSession, order: Order, token: str | None) -> None:
    if not capture_requested(token):
        return
    campaign_id = await session.scalar(
        select(ReferralIdentity.campaign_id).where(ReferralIdentity.token == token)
    )
    if campaign_id is None:
        return
    # Receiver holds FOR UPDATE. Re-read both objects after this conflicting lock,
    # even if a caller has an older object already present in the identity map.
    replica = await session.scalar(
        select(CampaignReplica)
        .where(CampaignReplica.campaign_id == campaign_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    identity = await session.scalar(
        select(ReferralIdentity)
        .where(ReferralIdentity.token == token)
        .execution_options(populate_existing=True)
    )
    if replica is None or identity is None:
        return
    now = await capture_clock(session)
    if (
        not identity.in_current_registry
        or replica.state != "published"
        or replica.ended_at is not None
        or replica.published_at > now
        or not replica.start_at <= now < replica.end_at
        or not replica.issued_at <= now < replica.valid_until
        or not any(item.product_id == replica.product_id for item in order.items)
    ):
        return
    await store_snapshot(session, order, replica, identity, now)


async def store_snapshot(
    session: AsyncSession,
    order: Order,
    replica: CampaignReplica,
    identity: ReferralIdentity,
    captured_at: datetime,
) -> None:
    """Copy only flushed, server-priced lines belonging to this exact order."""
    if order.id is None or identity.campaign_id != replica.campaign_id:
        raise ValueError("Invalid referral snapshot ownership")
    for item in order.items:
        if item.id is None or item.order_id != order.id:
            raise ValueError("Invalid referral snapshot ownership")
    header = ReferralOrderSnapshot(
        order_id=order.id,
        campaign_id=replica.campaign_id,
        product_id=replica.product_id,
        content_revision=replica.content_revision,
        campaign_name=replica.name,
        employee_code=identity.employee_code,
        employee_display_name=identity.employee_display_name,
        captured_at=captured_at,
        issued_at=replica.issued_at,
        valid_until=replica.valid_until,
        content_sha256=replica.content_sha256,
        currency=order.currency,
    )
    header.items = [
        ReferralOrderItemSnapshot(
            order_item_id=item.id,
            product_id=replica.product_id,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.product_price,
            line_total=item.total,
            is_sale_at_order=item.is_sale_at_order,
            regular_price_at_order=item.regular_price_at_order,
        )
        for item in order.items
        if item.product_id == replica.product_id
    ]
    session.add(header)
    await session.flush()
