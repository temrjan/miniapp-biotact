"""Event QR scan, grant and send-time checks (spec event-qr-discount R-31, R-34).

A grant has no dates of its own: it is active exactly while its promotion's window
``starts_at <= now < ends_at`` is open.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.event_promotion import EventPromotion, EventPromotionGrant, EventPromotionScan
from bot.models.user import User

EVENT_PREFIX = "ev_"
# The operator command accepts only these codes, so ``ev_<code>`` fits a Telegram deep link.
CODE_PATTERN = re.compile(r"[A-Za-z0-9-]{1,40}")

Outcome = Literal["granted", "existing_grant", "outside_window"]


def utcnow() -> datetime:
    """The one clock for window checks; tests replace it."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class PromotionView:
    """What the bot texts need from a promotion."""

    id: int
    title: str
    percent: int
    ends_at: datetime


def promotion_code(args: str) -> str | None:
    """The code of an ``ev_<code>`` deep-link argument, or ``None`` when it is malformed."""
    code = args.removeprefix(EVENT_PREFIX)
    return code if CODE_PATTERN.fullmatch(code) else None


def _view(promotion: EventPromotion) -> PromotionView:
    return PromotionView(promotion.id, promotion.title, promotion.percent, promotion.ends_at)


async def record_scan(
    session: AsyncSession, user: User, is_new: bool, code: str
) -> tuple[Outcome, PromotionView] | None:
    """Record one scan of a known promotion; ``None`` for an unknown code (nothing written).

    The window is checked before any existing grant (R-31): outside it the scan is recorded as
    ``outside_window`` and no grant is created. Inside it the first scan creates the grant with
    ``is_new_user`` from the single user resolution of this update (R-32); a repeat scan records
    ``existing_grant`` and leaves the grant unchanged. The caller owns the savepoint.
    """
    promotion = await session.scalar(select(EventPromotion).where(EventPromotion.code == code))
    if promotion is None:
        return None
    now = utcnow()
    outcome: Outcome
    if not promotion.starts_at <= now < promotion.ends_at:
        outcome = "outside_window"
    else:
        grant_id = await session.scalar(
            select(EventPromotionGrant.id).where(
                EventPromotionGrant.promotion_id == promotion.id,
                EventPromotionGrant.user_id == user.id,
            )
        )
        if grant_id is None:
            outcome = "granted"
            session.add(
                EventPromotionGrant(
                    promotion_id=promotion.id, user_id=user.id, granted_at=now, is_new_user=is_new
                )
            )
        else:
            outcome = "existing_grant"
    session.add(
        EventPromotionScan(
            promotion_id=promotion.id, user_id=user.id, scanned_at=now, outcome=outcome
        )
    )
    await session.flush()
    return outcome, _view(promotion)


async def active_grant_promotion(
    session: AsyncSession, telegram_id: int, promotion_id: int
) -> PromotionView | None:
    """The promotion when this Telegram user holds its grant and its window is open now."""
    now = utcnow()
    promotion = await session.scalar(
        select(EventPromotion)
        .join(EventPromotionGrant, EventPromotionGrant.promotion_id == EventPromotion.id)
        .join(User, User.id == EventPromotionGrant.user_id)
        .where(
            EventPromotion.id == promotion_id,
            User.telegram_id == telegram_id,
            EventPromotion.starts_at <= now,
            EventPromotion.ends_at > now,
        )
    )
    return None if promotion is None else _view(promotion)
