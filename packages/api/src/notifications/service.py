"""Telegram notification service."""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


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

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send message to Telegram chat."""
        if not self.is_configured:
            logger.warning("Telegram not configured, skipping notification")
            return False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                    },
                )
                response.raise_for_status()
                logger.info("Telegram notification sent successfully")
                return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def _format_items(self, items: Sequence[dict[str, Any]]) -> str:
        """Format order items for message."""
        lines = []
        for i, item in enumerate(items, 1):
            name = item.get("product_name", "Товар")
            qty = item.get("quantity", 1)
            price = item.get("product_price", 0)
            lines.append(f"{i}. {name} - {qty} шт × {price:,.0f} сум")
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
    ) -> bool:
        """Notify about new order (payment initiated)."""
        items_text = self._format_items(items)
        now = datetime.now().strftime("%d.%m.%Y %H:%M")

        message = f"""📱 <b>Biotact Mini-App</b>
🔔 <b>НОВЫЙ ЗАКАЗ</b>

📦 <b>Товары:</b>
{items_text}

💰 <b>Сумма:</b> {self._format_price(total)} сум

👤 <b>Клиент:</b>
Имя: {customer_name or "Не указано"}
Телефон: {customer_phone or "Не указано"}
{f"Адрес: {delivery_address}" if delivery_address else ""}

🔖 <b>Заказ:</b> {order_number}
{f"🧾 Invoice: {invoice_uuid}" if invoice_uuid else ""}

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
        """Notify about successful payment.

        ``points_applied > 0`` marks the same request «ОПЛАЧЕНО БАЛЛАМИ» and shows the debit —
        an all-or-nothing points order has no invoice and no fiscal receipt, so this line is
        how the fulfilment group tells the two payment kinds apart.
        """
        items_text = self._format_items(items)
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        header = "✅ <b>ОПЛАЧЕНО БАЛЛАМИ</b>" if points_applied else "✅ <b>ОПЛАЧЕНО</b>"
        points_line = (
            f"\n💎 <b>Списано баллов:</b> {self._format_price(points_applied)}"
            if points_applied
            else ""
        )

        message = f"""📱 <b>Biotact Mini-App</b>
{header}

📦 <b>Товары:</b>
{items_text}

💰 <b>Сумма:</b> {self._format_price(total)} сум{points_line}

👤 <b>Клиент:</b>
Имя: {customer_name or "Не указано"}
Телефон: {customer_phone or "Не указано"}
{f"Адрес: {delivery_address}" if delivery_address else ""}

🔖 <b>Заказ:</b> {order_number}
{f"🧾 Invoice: {invoice_uuid}" if invoice_uuid else ""}

⏰ {now}"""

        return await self.send_message(message)


# Singleton instance
telegram_notifier = TelegramNotifier()
