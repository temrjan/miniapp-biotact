"""CRM service for syncing customer data to API."""

import logging
from aiogram.types import User as TelegramUser
import httpx

from bot.core.config import settings

logger = logging.getLogger(__name__)


async def sync_customer_to_crm(user: TelegramUser) -> bool:
    """Sync Telegram user data to CRM.

    Called on every /start to keep customer data up-to-date.
    Creates new customer or updates existing one.

    Args:
        user: Telegram user object from message.from_user

    Returns:
        True if sync successful, False otherwise
    """
    if not settings.crm_api_token:
        logger.debug("CRM API token not configured, skipping sync")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.crm_api_url}/customer",
                headers={
                    "X-Bot-Token": settings.crm_api_token,
                    "Content-Type": "application/json",
                },
                json={
                    "telegram_id": user.id,
                    "name": user.first_name,
                    "username": user.username,
                },
            )

            if response.status_code in (200, 201):
                logger.info(f"CRM sync OK: {user.id} ({user.first_name}, @{user.username})")
                return True
            else:
                logger.warning(f"CRM sync failed: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"CRM sync error for {user.id}: {e}")
        return False


async def sync_customer_full(user: TelegramUser, phone: str | None = None) -> bool:
    """Sync full Telegram user data to CRM including phone and username.

    Args:
        user: Telegram user object from message.from_user
        phone: Phone number if available

    Returns:
        True if sync successful, False otherwise
    """
    if not settings.crm_api_token:
        logger.debug("CRM API token not configured, skipping sync")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.crm_api_url}/customer",
                headers={
                    "X-Bot-Token": settings.crm_api_token,
                    "Content-Type": "application/json",
                },
                json={
                    "telegram_id": user.id,
                    "name": user.first_name,
                    "username": user.username,
                    "phone": phone,
                },
            )

            if response.status_code in (200, 201):
                logger.info(f"CRM full sync OK: {user.id} ({user.first_name}, {phone})")
                return True
            else:
                logger.warning(f"CRM full sync failed: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"CRM full sync error for {user.id}: {e}")
        return False


async def save_customer_symptoms(
    telegram_id: int,
    symptoms: list[str] | None = None,
    ai_notes: str | None = None,
) -> bool:
    """Save customer symptoms and AI notes to CRM.

    Args:
        telegram_id: Customer's Telegram ID
        symptoms: List of symptom tags (immunity, gut, stress, skin)
        ai_notes: AI assistant notes about the customer

    Returns:
        True if save successful, False otherwise
    """
    if not settings.crm_api_token:
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.crm_api_url}/customer",
                headers={
                    "X-Bot-Token": settings.crm_api_token,
                    "Content-Type": "application/json",
                },
                json={
                    "telegram_id": telegram_id,
                    "symptoms": symptoms,
                    "ai_notes": ai_notes,
                },
            )
            return response.status_code in (200, 201)
    except Exception as e:
        logger.error(f"CRM save symptoms error: {e}")
        return False


async def add_family_member(
    telegram_id: int,
    name: str,
    relation: str | None = None,
    age: int | None = None,
    symptoms: list[str] | None = None,
) -> bool:
    """Add family member to customer's profile.

    Args:
        telegram_id: Customer's Telegram ID
        name: Family member's name
        relation: Relationship (ребёнок, супруг, родитель, etc.)
        age: Age of family member
        symptoms: List of symptom tags

    Returns:
        True if add successful, False otherwise
    """
    if not settings.crm_api_token:
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.crm_api_url}/customer/{telegram_id}/family",
                headers={
                    "X-Bot-Token": settings.crm_api_token,
                    "Content-Type": "application/json",
                },
                json={
                    "name": name,
                    "relation": relation,
                    "age": age,
                    "symptoms": symptoms or [],
                },
            )
            return response.status_code in (200, 201)
    except Exception as e:
        logger.error(f"CRM add family error: {e}")
        return False


async def create_lead(
    telegram_id: int,
    request_text: str,
    ai_summary: str | None = None,
    recommended_products: list[str] | None = None,
) -> bool:
    """Create a lead when customer is ready to buy.

    Args:
        telegram_id: Customer's Telegram ID
        request_text: Customer's original request
        ai_summary: AI summary of the conversation
        recommended_products: Products recommended by AI

    Returns:
        True if create successful, False otherwise
    """
    if not settings.crm_api_token:
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.crm_api_url}/lead",
                headers={
                    "X-Bot-Token": settings.crm_api_token,
                    "Content-Type": "application/json",
                },
                json={
                    "telegram_id": telegram_id,
                    "request_text": request_text,
                    "ai_summary": ai_summary,
                    "recommended_products": recommended_products or [],
                },
            )
            return response.status_code in (200, 201)
    except Exception as e:
        logger.error(f"CRM create lead error: {e}")
        return False


async def get_customer_context(telegram_id: int) -> dict | None:
    """Get customer context for AI assistant.

    Args:
        telegram_id: Customer's Telegram ID

    Returns:
        Customer data dict or None if not found
    """
    if not settings.crm_api_token:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.crm_api_url}/customer/{telegram_id}",
                headers={"X-Bot-Token": settings.crm_api_token},
            )
            if response.status_code == 200:
                return response.json()
            return None
    except Exception as e:
        logger.error(f"CRM get customer error: {e}")
        return None
