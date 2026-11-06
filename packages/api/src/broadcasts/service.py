"""Broadcast service — business logic for mass messaging."""

import asyncio
import logging
import re
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.broadcasts.models import Broadcast, BroadcastStatus
from src.broadcasts.schemas import BroadcastCreate, BroadcastListResponse, BroadcastResponse
from src.config import settings
from src.database import async_session_maker
from src.users.models import User

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
RATE_LIMIT_DELAY = 0.05  # 20 msg/sec (safe margin under 30 limit)

_BOLD_MARKDOWN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_active_broadcast_ids: set[int] = set()


def mark_broadcast_active(broadcast_id: int) -> None:
    """Mark a broadcast ID as actively sending in this process."""
    _active_broadcast_ids.add(broadcast_id)


def unmark_broadcast_active(broadcast_id: int) -> None:
    """Remove a broadcast ID from active in-process tracking."""
    _active_broadcast_ids.discard(broadcast_id)


def has_active_broadcast() -> bool:
    """Return True if any broadcast send is active in this process."""
    return bool(_active_broadcast_ids)


async def recover_stuck_broadcasts(db: AsyncSession) -> None:
    """Transition orphaned SENDING broadcasts (not active in this process) to CANCELLED."""
    stmt = (
        select(Broadcast)
        .where(Broadcast.status == BroadcastStatus.SENDING)
        .execution_options(populate_existing=True)
    )
    if _active_broadcast_ids:
        stmt = stmt.where(Broadcast.id.notin_(_active_broadcast_ids))
    result = await db.execute(stmt)
    stuck_broadcasts = list(result.scalars().all())
    if stuck_broadcasts:
        for broadcast in stuck_broadcasts:
            broadcast.status = BroadcastStatus.CANCELLED
            logger.warning("Recovered stuck SENDING broadcast %d as cancelled", broadcast.id)
        await db.commit()


def _render_message_text(text: str) -> str:
    """Convert **text** Markdown shorthand to <b>text</b> for Telegram HTML parse mode."""
    return _BOLD_MARKDOWN.sub(r"<b>\1</b>", text)


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
        video_url=data.video_url,
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
    *,
    recover_stuck: bool = True,
) -> Broadcast | None:
    """Get broadcast by ID."""
    if recover_stuck:
        await recover_stuck_broadcasts(db)
    return await db.get(Broadcast, broadcast_id)


async def get_broadcasts_paginated(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
) -> BroadcastListResponse:
    """Get paginated list of broadcasts."""
    await recover_stuck_broadcasts(db)
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
    await db.commit()
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
    rendered_text = _render_message_text(broadcast.text)

    if photo:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": rendered_text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        url = f"{TELEGRAM_API}/sendPhoto"
    elif video:
        payload = {
            "chat_id": chat_id,
            "video": video,
            "caption": rendered_text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        url = f"{TELEGRAM_API}/sendVideo"
    else:
        payload = {
            "chat_id": chat_id,
            "text": rendered_text,
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


async def execute_test_send(broadcast_id: int, target_telegram_id: int) -> None:
    """Send broadcast to a single test recipient without touching counters/status.

    Used by admin "Тест (мне)" button to preview the message before mass send.
    """
    async with async_session_maker() as db:
        broadcast = await db.get(Broadcast, broadcast_id)
        if not broadcast:
            logger.warning("Test-send: broadcast %d not found", broadcast_id)
            return

        async with httpx.AsyncClient(timeout=15.0) as client:
            success, error_type = await _send_to_user(client, target_telegram_id, broadcast)

        # Persist photo_file_id / video_file_id cache if populated during the test send
        await db.commit()

        logger.info(
            "Test-send broadcast %d to %d: success=%s error=%s",
            broadcast_id,
            target_telegram_id,
            success,
            error_type or "-",
        )


async def _cancel_sending_broadcast(broadcast_id: int) -> None:
    """Mark a broadcast as CANCELLED in a fresh session if it is still SENDING."""
    try:
        async with async_session_maker() as cleanup_db:
            broadcast = await cleanup_db.get(Broadcast, broadcast_id)
            if broadcast and broadcast.status == BroadcastStatus.SENDING:
                broadcast.status = BroadcastStatus.CANCELLED
                await cleanup_db.commit()
    except Exception:
        logger.exception("Failed to mark broadcast %d as cancelled", broadcast_id)


async def execute_broadcast(broadcast_id: int) -> None:
    """Execute broadcast in background — sends to all active users.

    Creates its own DB session (runs outside request lifecycle).
    """
    _active_broadcast_ids.add(broadcast_id)
    try:
        async with async_session_maker() as db:
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
            telegram_ids = [value for value in result.scalars() if value is not None]

            broadcast.total_users = len(telegram_ids)
            await db.commit()

            logger.info(
                "Starting broadcast %d %s to %d users",
                broadcast_id,
                broadcast.title,
                len(telegram_ids),
            )

            sent_count = broadcast.sent_count
            blocked_count = broadcast.blocked_count
            failed_count = broadcast.failed_count

            async with httpx.AsyncClient(timeout=15.0) as client:
                for i, tid in enumerate(telegram_ids):
                    with db.no_autoflush:
                        await db.refresh(broadcast, ["status"])
                    if broadcast.status == BroadcastStatus.CANCELLED:
                        logger.info("Broadcast %d cancelled during sending", broadcast_id)
                        break

                    success, error_type = await _send_to_user(client, tid, broadcast)

                    if success:
                        sent_count += 1
                    elif error_type == "blocked":
                        blocked_count += 1
                    else:
                        failed_count += 1

                    # Persist counters every 10 messages
                    if (i + 1) % 10 == 0:
                        broadcast.sent_count = sent_count
                        broadcast.blocked_count = blocked_count
                        broadcast.failed_count = failed_count
                        await db.commit()

                    await asyncio.sleep(RATE_LIMIT_DELAY)

            if broadcast.status != BroadcastStatus.CANCELLED:
                completed_id = (
                    await db.execute(
                        update(Broadcast)
                        .where(
                            Broadcast.id == broadcast_id,
                            Broadcast.status == BroadcastStatus.SENDING,
                        )
                        .values(
                            status=BroadcastStatus.COMPLETED,
                            completed_at=datetime.now(UTC),
                            sent_count=sent_count,
                            blocked_count=blocked_count,
                            failed_count=failed_count,
                            photo_file_id=broadcast.photo_file_id,
                            video_file_id=broadcast.video_file_id,
                        )
                        .returning(Broadcast.id)
                    )
                ).scalar_one_or_none()
                if completed_id is not None:
                    db.expire(broadcast)
                    await db.commit()
                    logger.info(
                        "Broadcast %d completed: sent=%d, blocked=%d, failed=%d",
                        broadcast_id,
                        sent_count,
                        blocked_count,
                        failed_count,
                    )
                    return

            await db.execute(
                update(Broadcast)
                .where(Broadcast.id == broadcast_id)
                .values(
                    sent_count=sent_count,
                    blocked_count=blocked_count,
                    failed_count=failed_count,
                    photo_file_id=broadcast.photo_file_id,
                    video_file_id=broadcast.video_file_id,
                )
            )
            db.expire(broadcast)
            await db.commit()
            logger.info(
                "Broadcast %d cancelled: sent=%d, blocked=%d, failed=%d",
                broadcast_id,
                sent_count,
                blocked_count,
                failed_count,
            )

    except asyncio.CancelledError:
        logger.warning("Broadcast %d task cancelled", broadcast_id)
        cleanup_task = asyncio.create_task(_cancel_sending_broadcast(broadcast_id))
        await asyncio.shield(cleanup_task)
        raise
    except Exception:
        logger.exception("Broadcast %d failed with exception", broadcast_id)
        await _cancel_sending_broadcast(broadcast_id)
    finally:
        _active_broadcast_ids.discard(broadcast_id)
