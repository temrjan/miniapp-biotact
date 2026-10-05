"""Immutable local display inputs and bounded pure campaign rendering."""

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.delivery_privacy import private_operation
from src.employee_referrals.models import ReferralOrderItemSnapshot, ReferralOrderSnapshot
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.return_schemas import CountSnapshot, load_json
from src.employee_referrals.returns import validated
from src.employee_referrals.schemas import PositiveId, StrictModel, UuidText, WireTime, utc_text
from src.notifications.service import esc, telegram_notifier
from src.orders.models import Order, OrderItem


class DisplayLine(StrictModel):
    order_item_id: PositiveId
    product_name: str
    quantity: PositiveId
    product_price: str | None
    is_sale_at_order: bool | None
    regular_price_at_order: str | None
    slug: str | None
    delta: Annotated[int, Field(strict=True, ge=0)]
    returned: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if not self.delta <= self.returned <= self.quantity:
            raise ValueError("Invalid display counts")
        for amount in (self.product_price, self.regular_price_at_order):
            if amount is not None and (not Decimal(amount).is_finite() or Decimal(amount) < 0):
                raise ValueError("Invalid display amount")
        return self


class Notice(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    event_id: UuidText
    event_type: Literal["sale_paid", "sale_refunded"]
    order_id: PositiveId
    order_version: PositiveId
    fact_at: WireTime
    campaign_id: PositiveId
    campaign_name: str
    employee_code: UuidText
    employee_name: str
    order_number: str
    total: str | None
    points_applied: Annotated[int, Field(strict=True, ge=0)]
    customer_name: str | None
    customer_phone: str | None
    delivery_address: str | None
    invoice_uuid: str | None
    lines: list[DisplayLine]

    @model_validator(mode="after")
    def display(self) -> Self:
        if self.event_type == "sale_paid":
            if (
                self.total is None
                or not Decimal(self.total).is_finite()
                or Decimal(self.total) <= 0
            ):
                raise ValueError("Invalid paid total")
        elif self.total is not None or self.points_applied or self.invoice_uuid is not None:
            raise ValueError("Invalid return display")
        if not self.lines:
            raise ValueError("Missing display lines")
        return self


def verify_notice(job: NoticeDelivery, source: CountSnapshot) -> Notice:
    if job.payload is None or hashlib.sha256(job.payload).hexdigest() != job.payload_sha256:
        raise ValueError("Invalid notice payload")
    value = Notice.model_validate(load_json(job.payload))
    if (
        value.event_id != source.event_id
        or value.event_type != source.event_type
        or value.order_id != source.order_id
        or value.order_version != source.order_version
        or value.fact_at != source.occurred_at
        or value.campaign_id != source.attribution.campaign_id
        or value.employee_code != source.attribution.employee_code
        or [
            (line.order_item_id, line.quantity, line.returned)
            for line in value.lines
            if line.order_item_id in source.returned()
        ]
        != [
            (line.order_item_id, line.quantity, source.returned()[line.order_item_id])
            for line in source.lines
        ]
        or len({line.order_item_id for line in value.lines}) != len(value.lines)
        or (value.event_type == "sale_refunded" and len(value.lines) != len(source.lines))
    ):
        raise ValueError("Invalid notice metadata")
    return value


async def append_jobs(
    session: AsyncSession, row: PaidOutbox | ReturnOutbox, order: Order | None = None
) -> None:
    """Producer owns transaction and privacy; no commit, send, or retrospective repair."""
    source = validated(row)
    kind = "paid" if isinstance(row, PaidOutbox) else "return"
    refs = {
        "paid_id": row.id if kind == "paid" else None,
        "return_id": row.id if kind == "return" else None,
    }
    common = dict(
        source_kind=kind,
        state="pending",
        attempt_count=0,
        generation=0,
        next_attempt_at=row.created_at,
        created_at=row.created_at,
        **refs,
    )
    session.add(K2Delivery(**common))
    header = await session.get(ReferralOrderSnapshot, row.order_id)
    campaign_name = f"Кампания №{source.attribution.campaign_id}"
    employee_name = f"код {source.attribution.employee_code}"
    names: dict[int, str] = {}
    if header is not None:
        captured = (
            await session.scalars(
                select(ReferralOrderItemSnapshot)
                .where(ReferralOrderItemSnapshot.order_id == row.order_id)
                .order_by(ReferralOrderItemSnapshot.order_item_id)
            )
        ).all()
        if (
            header.campaign_id,
            str(header.employee_code),
            header.product_id,
            header.content_revision,
            header.captured_at,
        ) == (
            source.attribution.campaign_id,
            source.attribution.employee_code,
            source.attribution.product_id,
            source.attribution.content_revision,
            source.attribution.captured_at,
        ) and [(i.order_item_id, i.product_id, i.quantity) for i in captured] == [
            (i.order_item_id, i.product_id, i.quantity) for i in source.lines
        ]:
            campaign_name, employee_name = header.campaign_name, header.employee_display_name
            names = {i.order_item_id: i.product_name for i in captured}
    lines: list[DisplayLine]
    if isinstance(row, PaidOutbox):
        if order is None or header is None:
            raise ValueError("Missing paid display")
        loaded = await session.scalar(
            select(Order)
            .where(Order.id == row.order_id)
            .options(selectinload(Order.items).selectinload(OrderItem.product))
        )
        if loaded is None:
            raise ValueError("Missing paid order")
        lines = [
            DisplayLine(
                order_item_id=i.id,
                product_name=i.product_name,
                quantity=i.quantity,
                product_price=str(i.product_price),
                is_sale_at_order=i.is_sale_at_order,
                regular_price_at_order=str(i.regular_price_at_order)
                if i.regular_price_at_order is not None
                else None,
                slug=i.product.slug if i.product else None,
                delta=0,
                returned=0,
            )
            for i in sorted(loaded.items, key=lambda item: item.id)
        ]
        # Ordinary items remain in the paid card, while K2 verifies only campaign lines.
        campaign_lines = {i.order_item_id: i for i in source.lines}
        if any(
            i.order_item_id in campaign_lines
            and i.quantity != campaign_lines[i.order_item_id].quantity
            for i in lines
        ):
            raise ValueError("Invalid paid display")
        total, points = str(loaded.total), loaded.points_applied
        customer_name, phone, address, invoice = (
            loaded.delivery_name,
            loaded.delivery_phone,
            loaded.delivery_address,
            loaded.payment_uuid,
        )
        number = loaded.order_number
    else:
        paid = await session.scalar(select(PaidOutbox).where(PaidOutbox.order_id == row.order_id))
        if paid is None:
            raise ValueError("Missing paid root")
        job = await session.scalar(select(NoticeDelivery).where(NoticeDelivery.paid_id == paid.id))
        number = f"Заказ №{row.order_id}"
        if job is not None and job.state != "legacy_unknown":
            previous = verify_notice(job, validated(paid))
            campaign_name, employee_name, number = (
                previous.campaign_name,
                previous.employee_name,
                previous.order_number,
            )
            names = {i.order_item_id: i.product_name for i in previous.lines}
        deltas = {int(i["order_item_id"]): int(i["quantity"]) for i in row.request_lines}
        lines = [
            DisplayLine(
                order_item_id=i.order_item_id,
                product_name=names.get(i.order_item_id, f"Товар №{i.order_item_id}"),
                quantity=i.quantity,
                product_price=None,
                is_sale_at_order=None,
                regular_price_at_order=None,
                slug=None,
                delta=deltas.get(i.order_item_id, 0),
                returned=source.returned()[i.order_item_id],
            )
            for i in source.lines
        ]
        total, points, customer_name, phone, address, invoice = None, 0, None, None, None, None
    value = Notice.model_validate(
        {
            "schema_version": 1,
            "event_id": source.event_id,
            "event_type": source.event_type,
            "order_id": source.order_id,
            "order_version": source.order_version,
            "fact_at": utc_text(source.occurred_at),
            "campaign_id": source.attribution.campaign_id,
            "campaign_name": campaign_name,
            "employee_code": source.attribution.employee_code,
            "employee_name": employee_name,
            "order_number": number,
            "total": total,
            "points_applied": points,
            "customer_name": customer_name,
            "customer_phone": phone,
            "delivery_address": address,
            "invoice_uuid": invoice,
            "lines": lines,
        }
    )
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(payload) > 1048576:
        raise ValueError("Invalid notice size")
    session.add(
        NoticeDelivery(
            **common, payload=payload, payload_sha256=hashlib.sha256(payload).hexdigest()
        )
    )
    await session.flush()


@private_operation
async def owns_paid(session: AsyncSession, order_id: int) -> bool:
    """Retained fact selection includes legacy_unknown; independent of current capture/flags."""
    try:
        return (
            await session.scalar(select(PaidOutbox.id).where(PaidOutbox.order_id == order_id))
            is not None
        )
    except Exception:
        raise ValueError("Private selection unavailable") from None


@private_operation
async def invoice_referral_label(session: AsyncSession, order_id: int) -> str:
    """Distinct invoice-created notices retain their existing best-effort path."""
    try:
        captured = await session.scalar(
            select(ReferralOrderSnapshot.order_id).where(ReferralOrderSnapshot.order_id == order_id)
        )
        return "Заказ кампании" if captured is not None else "Без реферала"
    except Exception:
        raise ValueError("Private selection unavailable") from None


def units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def short(value: str | None, limit: int = 90) -> str:
    text = value or "Не указано"
    # Clip before escaping; never split entities, HTML tags, or Unicode characters.
    return esc(text[:limit] + ("…" if len(text) > limit else ""))


def render(value: Notice) -> str:
    time = value.fact_at.astimezone(ZoneInfo("Asia/Tashkent")).strftime(
        "%d.%m.%Y %H:%M:%S Asia/Tashkent"
    )
    campaign = f"Кампания: {short(value.campaign_name)} (№{value.campaign_id})\nСотрудник: {short(value.employee_name)} ({value.employee_code})"
    tail = f"\n{campaign}\nЗаказ: {short(value.order_number)}\nСобытие: {value.event_id}\nВерсия: {value.order_version}\n⏰ {time}"
    blocks = []
    if value.event_type == "sale_paid":
        total = Decimal(value.total or "0")
        full = value.points_applied >= total
        start = (
            "📱 <b>Biotact Mini-App</b>\n✅ <b>"
            + ("ОПЛАЧЕНО БАЛЛАМИ" if full else "ОПЛАЧЕНО")
            + "</b>\n\n📦 <b>Товары:</b>\n"
        )
        money = f"\n💰 <b>Сумма:</b> {total:,.0f} сум".replace(",", " ")
        if value.points_applied:
            money += f"\n💎 <b>Списано баллов:</b> {value.points_applied:,}".replace(",", " ")
            if not full:
                money += f"\n💳 <b>Итого оплачено картой:</b> {total - value.points_applied:,.0f} сум".replace(
                    ",", " "
                )
        tail = (
            money
            + f"\n👤 <b>Клиент:</b>\nИмя: {short(value.customer_name)}\nТелефон: {short(value.customer_phone)}\nАдрес: {short(value.delivery_address)}"
            + (f"\nInvoice: {short(value.invoice_uuid)}" if value.invoice_uuid else "")
            + tail
        )
        for i in value.lines:
            data = i.model_dump()
            data["product_name"] = i.product_name[:90] + ("…" if len(i.product_name) > 90 else "")
            data["product_price"] = Decimal(i.product_price or "0")
            data["regular_price_at_order"] = (
                Decimal(i.regular_price_at_order) if i.regular_price_at_order is not None else None
            )
            blocks.append(telegram_notifier._format_items([data]))
    else:
        start = "<b>ПОДТВЕРЖДЁН ВОЗВРАТ ТОВАРА КАМПАНИИ</b>\n"
        returned = sum(i.returned for i in value.lines)
        net = sum(i.quantity - i.returned for i in value.lines)
        tail = (
            f"\nНакопленно возвращено этой версии: {returned}\nОсталось этой версии: {net}" + tail
        )
        blocks = [
            f"{short(i.product_name)} (№{i.order_item_id}): возврат {i.delta}; накопленно {i.returned}; осталось {i.quantity - i.returned}"
            for i in value.lines
        ]
    message = start
    omitted = "\nОстальные строки — в заказе"
    for block in blocks:
        if units(message + block + omitted + tail) > 3500:
            message += omitted
            break
        message += block + "\n"
    result = message + tail
    if units(result) > 3500:
        raise ValueError("Invalid message size")
    return result
