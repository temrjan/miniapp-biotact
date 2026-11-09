"""Which event discount a Mini App buyer holds right now (spec event-qr-discount S3, R-10, R-21)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.event_promotions.models import EventPromotion, EventPromotionGrant

_WHOLE_SUM = Decimal("1")


@dataclass(frozen=True)
class ActivePromotion:
    """The one grant selected for pricing, the quote and the "my discount" read."""

    grant_id: int
    promotion_id: int
    title: str
    percent: int
    ends_at: datetime


def promotion_unit_price(price: Decimal, percent: int) -> Decimal:
    """``price * (100 - percent) / 100``, rounded half up to a whole sum (spec §2)."""
    return (price * (100 - percent) / 100).quantize(_WHOLE_SUM, rounding=ROUND_HALF_UP)


async def active_promotion(
    db: AsyncSession, user_id: int, *, now: datetime | None = None
) -> ActivePromotion | None:
    """The user's active grant, or ``None`` — always ``None`` while the API switch is OFF (R-01).

    Active means ``starts_at <= now < ends_at`` of the grant's promotion. With several active
    grants the highest percent wins, then the latest ``ends_at``, then the highest grant id (R-10).
    """
    if not settings.event_promotions_enabled:
        return None
    at = now or datetime.now(UTC)
    row = (
        await db.execute(
            select(
                EventPromotionGrant.id,
                EventPromotion.id,
                EventPromotion.title,
                EventPromotion.percent,
                EventPromotion.ends_at,
            )
            .join(EventPromotion, EventPromotion.id == EventPromotionGrant.promotion_id)
            .where(
                EventPromotionGrant.user_id == user_id,
                EventPromotion.starts_at <= at,
                EventPromotion.ends_at > at,
            )
            .order_by(
                EventPromotion.percent.desc(),
                EventPromotion.ends_at.desc(),
                EventPromotionGrant.id.desc(),
            )
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return None
    grant_id, promotion_id, title, percent, ends_at = row
    return ActivePromotion(grant_id, promotion_id, title, percent, ends_at)


async def mark_opened(db: AsyncSession, grant_id: int) -> None:
    """Record the first shop opening once; a later call keeps the first value (R-21)."""
    await db.execute(
        update(EventPromotionGrant)
        .where(EventPromotionGrant.id == grant_id, EventPromotionGrant.first_opened_at.is_(None))
        .values(first_opened_at=datetime.now(UTC))
    )
