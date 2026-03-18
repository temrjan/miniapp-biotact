"""Notification service for sending orders to sales group."""

import logging
import re

import httpx

from bot.core.config import settings

logger = logging.getLogger(__name__)

PHONE_PATTERN = re.compile(
    r"""
    (?:\+?998[\s-]?)?\(?[0-9]{2}\)?[\s-]?[0-9]{3}[\s-]?[0-9]{2}[\s-]?[0-9]{2}|  # UZ format
    (?:\+?7[\s-]?)?\(?[0-9]{3}\)?[\s-]?[0-9]{3}[\s-]?[0-9]{2}[\s-]?[0-9]{2}|    # RU format
    [0-9]{9,12}                                                                  # Plain numbers
    """,
    re.VERBOSE,
)


def extract_phone(text: str) -> str | None:
    """Extract phone number from text."""
    match = PHONE_PATTERN.search(text)
    if match:
        phone = re.sub(r"[\s()-]", "", match.group())
        if len(phone) >= 9:
            return phone
    return None


async def send_order_to_sales(
    user_id: int,
    username: str | None,
    full_name: str,
    phone: str,
    conversation_summary: str,
) -> bool:
    """Send order notification to sales group."""
    if not settings.notification_bot_token or not settings.sales_group_chat_id:
        logger.warning("Notification settings not configured")
        return False

    message = (
        "🆕 <b>Новая заявка от AI Ассистента</b>\n\n"
        f"👤 <b>Клиент:</b> {full_name}\n"
        f"📱 <b>Телефон:</b> {phone}\n"
        f"🆔 <b>Telegram:</b> @{username or 'нет'} (ID: {user_id})\n\n"
        f"💬 <b>Суть обращения:</b>\n{conversation_summary}"
    )

    api_url = f"https://api.telegram.org/bot{settings.notification_bot_token}/sendMessage"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                api_url,
                json={
                    "chat_id": settings.sales_group_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
            )
            response.raise_for_status()
            logger.info(f"Order sent to sales group for user {user_id}")
            return True
    except Exception as e:
        logger.error(f"Failed to send order: {e}")
        return False
