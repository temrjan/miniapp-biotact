"""Broadcast service — business logic for mass messaging."""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.broadcasts.models import Broadcast, BroadcastStatus
from src.broadcasts.schemas import BroadcastCreate, BroadcastListResponse, BroadcastResponse
from src.config import settings
from src.database import async_session_maker
from src.users.models import User

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
RATE_LIMIT_DELAY = 0.05  # 20 msg/sec (safe margin under 30 limit)


async def create_broadcast(
    db: AsyncSession,
    data: BroadcastCreate,
    created_by: int,
) -> Broadcast:
    """Create a new broadcast draft."""
    broadcast = Broadcast(
        title=data.title,
        text=data.text,
        photo_url=data.photo_url,
        button_text=data.button_text,
        button_url=data.button_url,
        status=BroadcastStatus.DRAFT,
        created_by=created_by,
    )
    db.add(broadcast)
    await db.flush()
    await db.refresh(broadcast)
    return broadcast


async def get_broadcast(
    db: AsyncSession,
    broadcast_id: int,
) -> Broadcast | None:
    """Get broadcast by ID."""
    return await db.get(Broadcast, broadcast_id)


async def get_broadcasts_paginated(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
) -> BroadcastListResponse:
    """Get paginated list of broadcasts."""
    count_result = await db.execute(select(func.count(Broadcast.id)))
    total = count_result.scalar_one()

    offset = (page - 1) * size
    result = await db.execute(
        select(Broadcast).order_by(Broadcast.created_at.desc()).offset(offset).limit(size)
    )
    items = list(result.scalars().all())
    pages = (total + size - 1) // size if total > 0 else 0

    return BroadcastListResponse(
        items=[BroadcastResponse.model_validate(b) for b in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def cancel_broadcast(
    db: AsyncSession,
    broadcast: Broadcast,
) -> Broadcast:
    """Cancel a broadcast."""
    broadcast.status = BroadcastStatus.CANCELLED
    await db.flush()
    await db.refresh(broadcast)
    return broadcast


def _build_reply_markup(
    button_text: str | None,
    button_url: str | None,
) -> dict[str, object] | None:
    """Build InlineKeyboardMarkup if button is configured."""
    if not button_text or not button_url:
        return None
    return {
        "inline_keyboard": [
            [
                {
                    "text": button_text,
                    "url": button_url,
                }
            ]
        ]
    }


async def _send_to_user(
    client: httpx.AsyncClient,
    chat_id: int,
    broadcast: Broadcast,
) -> tuple[bool, str]:
    """Send broadcast message to a single user.

    Returns:
        Tuple of (success, error_type).
        error_type: "blocked", "failed", or empty string on success.
    """
    reply_markup = _build_reply_markup(broadcast.button_text, broadcast.button_url)

    photo = broadcast.photo_file_id or broadcast.photo_url
    video = broadcast.video_file_id or broadcast.video_url

    if photo:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": broadcast.text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        url = f"{TELEGRAM_API}/sendPhoto"
    elif video:
        payload = {
            "chat_id": chat_id,
            "video": video,
            "caption": broadcast.text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        url = f"{TELEGRAM_API}/sendVideo"
    else:
        payload = {
            "chat_id": chat_id,
            "text": broadcast.text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        url = f"{TELEGRAM_API}/sendMessage"

    try:
        response = await client.post(url, json=payload)
        data = response.json()

        if data.get("ok"):
            result = data.get("result", {})
            if photo and not broadcast.photo_file_id:
                photos = result.get("photo", [])
                if photos:
                    broadcast.photo_file_id = photos[-1]["file_id"]
            elif video and not broadcast.video_file_id:
                video_obj = result.get("video")
                if video_obj:
                    broadcast.video_file_id = video_obj["file_id"]
            return True, ""

        error_code = data.get("error_code", 0)
        description = data.get("description", "")

        # Rate limit — wait and retry
        if error_code == 429:
            retry_after = data.get("parameters", {}).get("retry_after", 5)
            logger.warning("Rate limited, waiting %d seconds", retry_after)
            await asyncio.sleep(retry_after)
            # Retry once
            response = await client.post(url, json=payload)
            retry_data = response.json()
            if retry_data.get("ok"):
                return True, ""
            return False, "failed"

        # Blocked or deactivated
        if error_code == 403:
            logger.info("User %d blocked bot: %s", chat_id, description)
            return False, "blocked"

        # Chat not found or other client error
        logger.warning(
            "Failed to send to %d: [%d] %s",
            chat_id,
            error_code,
            description,
        )
        return False, "failed"

    except Exception as e:
        logger.error("HTTP error sending to %d: %s", chat_id, e)
        return False, "failed"


async def execute_broadcast(broadcast_id: int) -> None:
    """Execute broadcast in background — sends to all active users.

    Creates its own DB session (runs outside request lifecycle).
    """
    async with async_session_maker() as db:
        try:
            broadcast = await db.get(Broadcast, broadcast_id)
            if not broadcast or broadcast.status != BroadcastStatus.SENDING:
                logger.warning("Broadcast %d not found or not in sending state", broadcast_id)
                return

            # Get all active users with telegram_id
            result = await db.execute(
                select(User.telegram_id)
                .where(User.is_active.is_(True))
                .where(User.telegram_id.isnot(None))
            )
            telegram_ids = list(result.scalars().all())

            broadcast.total_users = len(telegram_ids)
            await db.commit()

            logger.info(
                "Starting broadcast %d %s to %d users",
                broadcast_id,
                broadcast.title,
                len(telegram_ids),
            )

            async with httpx.AsyncClient(timeout=15.0) as client:
                for i, tid in enumerate(telegram_ids):
                    if broadcast.status == BroadcastStatus.CANCELLED:
                        logger.info("Broadcast %d cancelled during sending", broadcast_id)
                        break

                    success, error_type = await _send_to_user(client, tid, broadcast)

                    if success:
                        broadcast.sent_count += 1
                    elif error_type == "blocked":
                        broadcast.blocked_count += 1
                        # Mark user as inactive
                        user_result = await db.execute(select(User).where(User.telegram_id == tid))
                        user = user_result.scalar_one_or_none()
                        if user:
                            user.is_active = False
                    else:
                        broadcast.failed_count += 1

                    # Persist counters every 10 messages
                    if (i + 1) % 10 == 0:
                        await db.commit()

                    await asyncio.sleep(RATE_LIMIT_DELAY)

            # Finalize
            broadcast.status = BroadcastStatus.COMPLETED
            broadcast.completed_at = datetime.now(UTC)
            await db.commit()

            logger.info(
                "Broadcast %d completed: sent=%d, blocked=%d, failed=%d",
                broadcast_id,
                broadcast.sent_count,
                broadcast.blocked_count,
                broadcast.failed_count,
            )

        except Exception:
            logger.exception("Broadcast %d failed with exception", broadcast_id)
            try:
                broadcast = await db.get(Broadcast, broadcast_id)
                if broadcast:
                    broadcast.status = BroadcastStatus.CANCELLED
                    await db.commit()
            except Exception:
                logger.exception("Failed to mark broadcast %d as cancelled", broadcast_id)
