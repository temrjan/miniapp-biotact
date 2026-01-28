"""AskBiotact API integration service."""

import logging
from typing import Any

import httpx

from bot.core.config import settings

logger = logging.getLogger(__name__)

ASKBIOTACT_API_URL = settings.askbiotact_api_url
ASKBIOTACT_API_KEY = settings.askbiotact_api_key
TIMEOUT = 30.0


async def ask_biotact(user_id: int, message: str) -> str:
    """Send message to AskBiotact API and get response.
    
    Args:
        user_id: Telegram user ID.
        message: User's message.
        
    Returns:
        AI assistant response.
    """
    if not ASKBIOTACT_API_URL or not ASKBIOTACT_API_KEY:
        logger.error("AskBiotact API not configured")
        return "Сервис временно недоступен."
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{ASKBIOTACT_API_URL}/api/v1/public/ask",
                headers={"X-API-Key": ASKBIOTACT_API_KEY},
                json={
                    "user_id": f"telegram_{user_id}",
                    "message": message,
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("answer", "Не удалось получить ответ.")
            elif response.status_code == 401:
                logger.error("AskBiotact API: Invalid API key")
                return "Ошибка авторизации."
            else:
                logger.error(f"AskBiotact API error: {response.status_code}")
                return "Произошла ошибка. Попробуйте позже."
                
    except httpx.TimeoutException:
        logger.error("AskBiotact API timeout")
        return "Превышено время ожидания. Попробуйте позже."
    except Exception as e:
        logger.exception(f"AskBiotact API error: {e}")
        return "Произошла ошибка. Попробуйте позже."


async def reset_history(user_id: int) -> bool:
    """Reset chat history for user.
    
    Args:
        user_id: Telegram user ID.
        
    Returns:
        True if successful.
    """
    if not ASKBIOTACT_API_URL or not ASKBIOTACT_API_KEY:
        return False
        
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{ASKBIOTACT_API_URL}/api/v1/public/ask/reset",
                headers={"X-API-Key": ASKBIOTACT_API_KEY},
                params={"user_id": f"telegram_{user_id}"},
            )
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Reset history error: {e}")
        return False
