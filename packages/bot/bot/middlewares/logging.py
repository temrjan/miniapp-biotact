"""Logging middleware."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Update

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    """Log all incoming updates."""

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        if data.get("private_referral_entry"):
            return await handler(event, data)
        user = None
        if event.message:
            user = event.message.from_user
        elif event.callback_query:
            user = event.callback_query.from_user

        update_type = event.event_type

        logger.info(
            "Update received: type=%s, user_id=%s, username=%s",
            update_type,
            user.id if user else None,
            user.username if user else None,
        )

        try:
            result = await handler(event, data)
            logger.debug("Handler completed for user_id=%s", user.id if user else None)
            return result
        except Exception as e:
            logger.exception(
                "Handler error for user_id=%s: %s",
                user.id if user else None,
                str(e),
            )
            raise
