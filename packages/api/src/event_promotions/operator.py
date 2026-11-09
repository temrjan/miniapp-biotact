"""Operator actions on event promotions (spec event-qr-discount S3, R-04, D13).

Dates are Tashkent calendar days: a window from start day 00:00 to the day after the end day 00:00
Tashkent, stored as UTC instants. Every refusal raises :class:`PromotionCommandError` before any
write, so the caller's transaction commits nothing.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.event_promotions.models import EventPromotion

TASHKENT = ZoneInfo("Asia/Tashkent")
# ``ev_`` plus 40 characters stays inside Telegram's 64-character deep-link limit.
_CODE = re.compile(r"[A-Za-z0-9-]{1,40}")
TITLE_MAX = 60


class PromotionCommandError(Exception):
    """A refused operator command; nothing was written."""


@dataclass(frozen=True)
class Window:
    starts_at: datetime
    ends_at: datetime


def day_window(start: date, end: date) -> Window:
    """Start day 00:00 to the day after the end day 00:00, Tashkent, as UTC instants."""
    if end < start:
        raise PromotionCommandError("the end day is before the start day")
    return Window(
        starts_at=datetime.combine(start, time(), TASHKENT).astimezone(UTC),
        ends_at=datetime.combine(end + timedelta(days=1), time(), TASHKENT).astimezone(UTC),
    )


def tashkent(instant: datetime) -> str:
    return instant.astimezone(TASHKENT).strftime("%Y-%m-%d %H:%M")


def describe(promotion: EventPromotion) -> str:
    return (
        f"{promotion.code}  {promotion.percent}%  "
        f"{tashkent(promotion.starts_at)} -> {tashkent(promotion.ends_at)} (Asia/Tashkent)"
    )


async def _find(db: AsyncSession, code: str) -> EventPromotion:
    promotion = await db.scalar(select(EventPromotion).where(EventPromotion.code == code))
    if promotion is None:
        raise PromotionCommandError(f"unknown promotion code {code!r}")
    return promotion


async def create(
    db: AsyncSession, *, code: str, title: str, percent: int, start: date, end: date
) -> EventPromotion:
    if not _CODE.fullmatch(code):
        raise PromotionCommandError("the code must match [A-Za-z0-9-]{1,40}")
    title = title.strip()
    if not 1 <= len(title) <= TITLE_MAX:
        raise PromotionCommandError(f"the title must have 1 to {TITLE_MAX} characters")
    if not 1 <= percent <= 90:
        raise PromotionCommandError("the percent must be from 1 to 90")
    window = day_window(start, end)
    if await db.scalar(select(EventPromotion.id).where(EventPromotion.code == code)) is not None:
        raise PromotionCommandError(f"the code {code!r} already exists")
    promotion = EventPromotion(
        code=code,
        title=title,
        percent=percent,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
    )
    db.add(promotion)
    await db.flush()
    return promotion


async def end(db: AsyncSession, *, code: str, now: datetime | None = None) -> EventPromotion:
    """End a running promotion now. A promotion that has not started or has ended is refused:
    ending it "now" would leave an empty window or reopen a closed one."""
    promotion = await _find(db, code)
    at = now or datetime.now(UTC)
    if at <= promotion.starts_at:
        raise PromotionCommandError("the promotion has not started; change its dates instead")
    if at >= promotion.ends_at:
        raise PromotionCommandError("the promotion has already ended")
    promotion.ends_at = at
    await db.flush()
    return promotion


async def set_dates(db: AsyncSession, *, code: str, start: date, end: date) -> tuple[str, str]:
    """Replace both bounds (D13). Returns the old and the new window, described."""
    window = day_window(start, end)
    promotion = await _find(db, code)
    before = describe(promotion)
    promotion.starts_at = window.starts_at
    promotion.ends_at = window.ends_at
    await db.flush()
    return before, describe(promotion)


async def listing(db: AsyncSession) -> list[str]:
    promotions = (await db.scalars(select(EventPromotion).order_by(EventPromotion.id))).all()
    return [describe(promotion) for promotion in promotions]
