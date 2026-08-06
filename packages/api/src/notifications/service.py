"""Telegram notification service."""

import html
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


# Экранируется ВСЁ неконстантное, что попадает в сообщение, без списка «эти поля безопасны» —
# такой список есть приглашение ошибиться на следующем поле. Неэкранированным остаётся только
# литеральный текст шаблона и наша собственная разметка.
#
# Почему это важнее, чем выглядит: сообщение уходит с parse_mode="HTML", и если разметка окажется
# невалидной, Telegram ответит ошибкой — а её глотает except в send_message, и все три вызывающих
# места результат не читают. Один символ «<» в адресе доставки способен молча оставить оплаченный
# заказ без уведомления отделу продаж.
def esc(value: object) -> str:
    """Escape a value for Telegram's HTML parse mode."""
    return html.escape(str(value), quote=False)


# Форма выпуска отдельным полем не хранится — она закодирована в служебном slug'е товара
# (`bifolak_active_caps` против `bifolak_active_stik`). Отдел продаж по строке уведомления решает,
# что готовить к отгрузке, а у этих пар названия в каталоге ОДИНАКОВЫЕ и цена одна — без формы
# позиция неразличима, и отгрузить можно не то. Дописать форму в название запрещено руководством,
# поэтому её выводит сервер и только здесь, в уведомлении.
#
# ⚠️ ЭТО СОГЛАШЕНИЕ, А НЕ ДАННЫЕ. Товар, чей slug не оканчивается на известный суффикс, останется
# без пометки МОЛЧА — на 2026-08-06 по соглашению живут 4 товара из 55 активных. Постоянное
# решение — отдельное поле «форма выпуска» у товара: оно закрыло бы ещё фискальный чек, карточку
# заказа в админке, аналитику «Топ товаров» и витрину, которых эта правка не касается.
RELEASE_FORM_BY_SLUG_SUFFIX = {"_caps": "капсулы", "_stik": "стик"}


class TelegramNotifier:
    """Send notifications to Telegram group."""

    def __init__(self) -> None:
        self.bot_token = settings.telegram_notification_bot_token or settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    @property
    def is_configured(self) -> bool:
        """Check if Telegram is configured."""
        return bool(self.bot_token and self.chat_id)

    async def _post_message(self, text: str, parse_mode: str | None) -> None:
        """POST one sendMessage; raises on any non-2xx. ``parse_mode=None`` sends plain text."""
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": text}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{self.base_url}/sendMessage", json=payload)
            response.raise_for_status()

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send message to Telegram chat; falls back to plain text if the markup is refused."""
        if not self.is_configured:
            logger.warning("Telegram not configured, skipping notification")
            return False

        try:
            await self._post_message(text, parse_mode)
            logger.info("Telegram notification sent successfully")
            return True
        except Exception as e:
            # Запасной путь. Экранирование (`esc`) закрывает известную причину отказа разметки, но
            # цена ошибки здесь несимметрична: потеря невидима (все три вызывающих места не читают
            # результат — оплаченный заказ остаётся без уведомления отделу продаж), а лишнее
            # сообщение видно сотруднику и безвредно. Поэтому пробуем ещё раз БЕЗ разметки:
            # текст дойдёт кривым, но дойдёт. Рекурсии здесь нет: повтор идёт в `_post_message`
            # напрямую, мимо этой обработки.
            try:
                await self._post_message(text, None)
            except Exception as fallback_error:
                logger.error(f"Failed to send Telegram notification: {fallback_error}")
                return False

            # Отдельный warning обязателен: без него тихая потеря просто перекрасилась бы в тихую
            # деградацию — сообщения ходят без разметки, и никто не знает, что экранирование течёт.
            logger.warning(
                "Telegram rejected the formatted message (%s); sent as plain text instead", e
            )
            return True

    def _release_form(self, slug: str | None) -> str:
        """Release form for a product slug, ready to append to its line (empty when unknown).

        Matches the END of the slug, never a substring: ``bifolak_active_caps`` is a form, a
        hypothetical ``..._caps_new`` is not, and guessing would put a wrong word in front of the
        person packing the parcel.
        """
        if not slug:
            return ""
        for suffix, form in RELEASE_FORM_BY_SLUG_SUFFIX.items():
            if slug.endswith(suffix):
                return f" ({form})"
        return ""

    def _format_items(self, items: Sequence[dict[str, Any]]) -> str:
        """Format order items for message."""
        lines = []
        for i, item in enumerate(items, 1):
            name = item.get("product_name", "Товар")
            qty = item.get("quantity", 1)
            price = item.get("product_price", 0)
            # `.get`, не индексация: вызывающих мест три, и забытый ключ у любого из них не имеет
            # права уронить уведомление — это означало бы «отдел продаж не узнал об оплаченном
            # заказе». Отсутствие slug'а = отсутствие пометки, строка остаётся прежней.
            form = self._release_form(item.get("slug"))
            # `name` — снимок названия из каталога: им управляет администратор, а `&` в названии
            # товара вполне реален. `form` из нашей таблицы и `qty`/`price` числовые, но правило
            # «экранируем всё неконстантное» держим без исключений.
            lines.append(f"{i}. {esc(name)}{form} - {esc(qty)} шт × {price:,.0f} сум")
        return "\n".join(lines)

    def _format_price(self, price: float) -> str:
        """Format price with spaces."""
        return f"{price:,.0f}".replace(",", " ")

    async def notify_order_created(
        self,
        order_number: str,
        total: float,
        items: Sequence[dict[str, Any]],
        customer_name: str | None = None,
        customer_phone: str | None = None,
        delivery_address: str | None = None,
        invoice_uuid: str | None = None,
        points_applied: int = 0,
    ) -> bool:
        """Notify about a new order whose card invoice was just created (payment initiated).

        ``points_applied`` is the club-points part already covered (Stage 6 partial redemption).
        The headline is what the CARD will collect (``total - points_applied``), plus a debit line —
        so the fulfilment group never reads the full catalogue sum as the card charge.
        """
        items_text = self._format_items(items)
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        amount_due = total - points_applied
        points_line = (
            f"\n💎 <b>Списано баллов:</b> {self._format_price(points_applied)}"
            if points_applied
            else ""
        )

        message = f"""📱 <b>Biotact Mini-App</b>
🔔 <b>НОВЫЙ ЗАКАЗ</b>

📦 <b>Товары:</b>
{items_text}

💰 <b>К оплате картой:</b> {self._format_price(amount_due)} сум{points_line}

👤 <b>Клиент:</b>
Имя: {esc(customer_name) if customer_name else "Не указано"}
Телефон: {esc(customer_phone) if customer_phone else "Не указано"}
{f"Адрес: {esc(delivery_address)}" if delivery_address else ""}

🔖 <b>Заказ:</b> {esc(order_number)}
{f"🧾 Invoice: {esc(invoice_uuid)}" if invoice_uuid else ""}

⏰ {now}"""

        return await self.send_message(message)

    async def notify_payment_success(
        self,
        order_number: str,
        total: float,
        items: Sequence[dict[str, Any]],
        customer_name: str | None = None,
        customer_phone: str | None = None,
        delivery_address: str | None = None,
        invoice_uuid: str | None = None,
        points_applied: int = 0,
    ) -> bool:
        """Notify about a successful payment.

        The header is «ОПЛАЧЕНО БАЛЛАМИ» only when points covered the order in FULL
        (``points_applied >= total``) — such an order has no invoice or fiscal receipt at all. A
        PARTIAL order is settled by the card success callback, where ``0 < points_applied < total``:
        the header stays «ОПЛАЧЕНО» (the card collected the remainder) and the debit line records
        the points part. A pure card order (``points_applied == 0``) shows neither.
        """
        items_text = self._format_items(items)
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        # Full coverage ⇒ «ОПЛАЧЕНО БАЛЛАМИ»; a partial order reaches this via the card callback
        # (points alone never do), so there points_applied < total and «ОПЛАЧЕНО» is the truth.
        fully_points = points_applied >= total
        header = "✅ <b>ОПЛАЧЕНО БАЛЛАМИ</b>" if fully_points else "✅ <b>ОПЛАЧЕНО</b>"
        points_line = (
            f"\n💎 <b>Списано баллов:</b> {self._format_price(points_applied)}"
            if points_applied
            else ""
        )
        # Сколько реально собрала карта — просьба бухгалтерии: до этого «Сумма» и «Списано баллов»
        # стояли рядом, а разницу считали в уме. Печатается ТОЛЬКО при частичном списании: без
        # баллов уплаченное и есть «Сумма» (строка была бы дублем), при полном покрытии карта не
        # участвовала и «0» был бы шумом — об этом уже сказал заголовок. Слово «картой»
        # обязательно: человек заплатил и баллами тоже, «итого оплачено» без него двусмысленно.
        card_paid_line = (
            f"\n💳 <b>Итого оплачено картой:</b> {self._format_price(total - points_applied)} сум"
            if points_applied and not fully_points
            else ""
        )

        message = f"""📱 <b>Biotact Mini-App</b>
{header}

📦 <b>Товары:</b>
{items_text}

💰 <b>Сумма:</b> {self._format_price(total)} сум{points_line}{card_paid_line}

👤 <b>Клиент:</b>
Имя: {esc(customer_name) if customer_name else "Не указано"}
Телефон: {esc(customer_phone) if customer_phone else "Не указано"}
{f"Адрес: {esc(delivery_address)}" if delivery_address else ""}

🔖 <b>Заказ:</b> {esc(order_number)}
{f"🧾 Invoice: {esc(invoice_uuid)}" if invoice_uuid else ""}

⏰ {now}"""

        return await self.send_message(message)


# Singleton instance
telegram_notifier = TelegramNotifier()
