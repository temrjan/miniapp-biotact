"""Read-only effectiveness report of event promotions for ``/qr_stats`` (spec event-qr-discount R-41).

Order tables are read through lightweight Core table clauses: the bot neither maps nor writes
them (R-03).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import column, distinct, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.event_promotion import EventPromotion, EventPromotionGrant, EventPromotionScan
from bot.models.user import User

TASHKENT = ZoneInfo("Asia/Tashkent")
TELEGRAM_LIMIT = 4096

orders = table(
    "orders",
    column("id"),
    column("user_id"),
    column("promotion_id"),
    column("payment_status"),
    column("total"),
    column("points_applied"),
    column("order_number"),
    column("paid_at"),
    column("delivery_name"),
    column("created_at"),
)
order_items = table("order_items", column("order_id"), column("promotion_discount"))

PAID = orders.c.payment_status == "paid"


@dataclass(frozen=True)
class Buyer:
    name: str
    username: str | None
    order_number: str
    total: Decimal
    paid_at: datetime | None


@dataclass(frozen=True)
class PromotionStats:
    code: str
    title: str
    percent: int
    starts_at: datetime
    ends_at: datetime
    scans: int
    scan_users: int
    scans_outside: int
    grants: int
    grants_new: int
    grants_existing: int
    opened: int
    orders_created: int
    orders_paid: int
    orders_refunded: int
    paid_amount: Decimal
    points_used: int
    discount_given: Decimal
    holders_other_paid: int
    buyers: list[Buyer]


async def _one(session: AsyncSession, promotion: EventPromotion) -> PromotionStats:
    scans, scan_users, scans_outside = (
        await session.execute(
            select(
                func.count(),
                func.count(distinct(EventPromotionScan.user_id)),
                func.count().filter(EventPromotionScan.outcome == "outside_window"),
            ).where(EventPromotionScan.promotion_id == promotion.id)
        )
    ).one()
    grants, grants_new, grants_existing, opened = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(EventPromotionGrant.is_new_user.is_(True)),
                func.count().filter(EventPromotionGrant.is_new_user.is_(False)),
                func.count(EventPromotionGrant.first_opened_at),
            ).where(EventPromotionGrant.promotion_id == promotion.id)
        )
    ).one()
    created, paid, refunded, amount, points = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(PAID),
                func.count().filter(orders.c.payment_status == "refunded"),
                func.coalesce(func.sum(orders.c.total).filter(PAID), 0),
                func.coalesce(func.sum(orders.c.points_applied).filter(PAID), 0),
            ).where(orders.c.promotion_id == promotion.id)
        )
    ).one()
    discount = await session.scalar(
        select(func.coalesce(func.sum(order_items.c.promotion_discount), 0))
        .select_from(order_items.join(orders, orders.c.id == order_items.c.order_id))
        .where(orders.c.promotion_id == promotion.id, PAID)
    )
    # Ordinary paid orders of grant holders, placed from their scan until the window end (F-03).
    holders_other = await session.scalar(
        select(func.count())
        .select_from(
            orders.join(EventPromotionGrant, EventPromotionGrant.user_id == orders.c.user_id)
        )
        .where(
            EventPromotionGrant.promotion_id == promotion.id,
            orders.c.promotion_id.is_(None),
            PAID,
            orders.c.created_at >= EventPromotionGrant.granted_at,
            orders.c.created_at < promotion.ends_at,
        )
    )
    rows = (
        await session.execute(
            select(
                User.first_name,
                User.last_name,
                User.username,
                orders.c.delivery_name,
                orders.c.order_number,
                orders.c.total,
                orders.c.paid_at,
            )
            .select_from(orders.outerjoin(User, User.id == orders.c.user_id))
            .where(orders.c.promotion_id == promotion.id, PAID)
            .order_by(orders.c.paid_at.asc().nulls_last(), orders.c.id)
        )
    ).all()
    buyers = [
        Buyer(
            name=" ".join(part for part in (first, last) if part) or delivery or "—",
            username=username or None,
            order_number=number,
            total=Decimal(total),
            paid_at=paid_at,
        )
        for first, last, username, delivery, number, total, paid_at in rows
    ]
    return PromotionStats(
        code=promotion.code,
        title=promotion.title,
        percent=promotion.percent,
        starts_at=promotion.starts_at,
        ends_at=promotion.ends_at,
        scans=scans,
        scan_users=scan_users,
        scans_outside=scans_outside,
        grants=grants,
        grants_new=grants_new,
        grants_existing=grants_existing,
        opened=opened,
        orders_created=created,
        orders_paid=paid,
        orders_refunded=refunded,
        paid_amount=Decimal(amount),
        points_used=int(points),
        discount_given=Decimal(discount or 0),
        holders_other_paid=holders_other or 0,
        buyers=buyers,
    )


async def promotion_stats(session: AsyncSession) -> list[PromotionStats]:
    """Figures for every promotion, newest first."""
    promotions = (
        await session.scalars(
            select(EventPromotion).order_by(
                EventPromotion.created_at.desc(), EventPromotion.id.desc()
            )
        )
    ).all()
    return [await _one(session, promotion) for promotion in promotions]


def money(amount: Decimal | int) -> str:
    value = Decimal(amount).quantize(Decimal("0.01"))
    text = f"{int(value):,}" if value == value.to_integral_value() else f"{value:,.2f}"
    return text.replace(",", " ")


def _day(instant: datetime) -> str:
    return instant.astimezone(TASHKENT).strftime("%d.%m.%Y")


def render_report(stats: list[PromotionStats]) -> str:
    """Plain text, one fact per line, so splitting at line boundaries keeps facts whole."""
    lines = ["📊 Статистика QR-акций"]
    if not stats:
        lines += ["", "Акций нет."]
    for item in stats:
        last_day = _day(item.ends_at - timedelta(microseconds=1))
        lines += [
            "",
            f"{item.code} — {item.title}, {item.percent}%",
            f"Период: {_day(item.starts_at)} – {last_day} (Ташкент)",
            f"Сканы: {item.scans}, уникальных: {item.scan_users}, вне периода: {item.scans_outside}",
            f"Скидки выданы: {item.grants} (новые: {item.grants_new}, "
            f"существующие: {item.grants_existing})",
            f"Открыли магазин: {item.opened}",
            f"Заказы со скидкой: создано {item.orders_created}, оплачено {item.orders_paid}, "
            f"возвращено {item.orders_refunded}",
            f"Оплачено: {money(item.paid_amount)} сум, баллами: {money(item.points_used)}",
            f"Скидка дана: {money(item.discount_given)} сум",
            f"Оплаченные заказы держателей без скидки: {item.holders_other_paid}",
            "Покупатели:" if item.buyers else "Покупатели: нет",
        ]
        for buyer in item.buyers:
            handle = f" @{buyer.username}" if buyer.username else ""
            paid = (
                buyer.paid_at.astimezone(TASHKENT).strftime("%d.%m.%Y %H:%M")
                if buyer.paid_at
                else "—"
            )
            lines.append(
                f"• {buyer.name}{handle} — №{buyer.order_number}, {money(buyer.total)} сум, {paid}"
            )
    return "\n".join(lines)


def _units(text: str) -> int:
    """Telegram counts message length in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split at line boundaries into parts of at most ``limit`` UTF-16 code units."""
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        added = _units(line) + (1 if current else 0)
        if current and size + added > limit:
            parts.append("\n".join(current))
            current, size = [], 0
            added = _units(line)
        current.append(line)
        size += added
    if current:
        parts.append("\n".join(current))
    return parts
