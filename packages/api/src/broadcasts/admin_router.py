"""Broadcast admin API endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_admin_header
from src.broadcasts import service
from src.broadcasts.models import Broadcast, BroadcastStatus
from src.broadcasts.schemas import BroadcastCreate, BroadcastListResponse, BroadcastResponse
from src.common.exceptions import BadRequestException, NotFoundException
from src.database import get_db
from src.users.models import User

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Delegate to the shared current-account rule, preserving this router's signature."""
    return await require_admin_header(db, authorization)


AdminUser = Annotated[User, Depends(require_admin)]


@router.post("", response_model=BroadcastResponse, status_code=201)
async def create_broadcast(
    data: BroadcastCreate,
    db: DbSession,
    admin: AdminUser,
) -> Broadcast:
    """Create a new broadcast draft."""
    return await service.create_broadcast(db, data, created_by=admin.id)


@router.get("", response_model=BroadcastListResponse)
async def list_broadcasts(
    db: DbSession,
    _admin: AdminUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> BroadcastListResponse:
    """Get paginated list of broadcasts."""
    return await service.get_broadcasts_paginated(db, page=page, size=size)


@router.get("/{broadcast_id}", response_model=BroadcastResponse)
async def get_broadcast(
    broadcast_id: int,
    db: DbSession,
    _admin: AdminUser,
) -> Broadcast:
    """Get broadcast details."""
    broadcast = await service.get_broadcast(db, broadcast_id)
    if not broadcast:
        raise NotFoundException("Broadcast not found")
    return broadcast


@router.post("/{broadcast_id}/send", response_model=BroadcastResponse)
async def send_broadcast(
    broadcast_id: int,
    db: DbSession,
    _admin: AdminUser,
    background_tasks: BackgroundTasks,
) -> Broadcast:
    """Start sending a broadcast to all active users."""
    broadcast = await service.get_broadcast(db, broadcast_id)
    if not broadcast:
        raise NotFoundException("Broadcast not found")

    if broadcast.status not in (BroadcastStatus.DRAFT, BroadcastStatus.CANCELLED):
        raise BadRequestException(f"Cannot send broadcast with status {broadcast.status}")

    # Prevent concurrent broadcasts
    result = await db.execute(
        select(Broadcast.id).where(Broadcast.status == BroadcastStatus.SENDING).limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise BadRequestException("Another broadcast is currently sending")

    # Reset counters for retry
    broadcast.status = BroadcastStatus.SENDING
    broadcast.sent_count = 0
    broadcast.failed_count = 0
    broadcast.blocked_count = 0
    broadcast.total_users = 0
    broadcast.photo_file_id = None
    broadcast.video_file_id = None
    broadcast.started_at = datetime.now(UTC)
    broadcast.completed_at = None
    await db.commit()
    await db.refresh(broadcast)

    background_tasks.add_task(service.execute_broadcast, broadcast_id)

    return broadcast


@router.post("/{broadcast_id}/test-send", response_model=BroadcastResponse)
async def test_send_broadcast(
    broadcast_id: int,
    db: DbSession,
    admin: AdminUser,
    background_tasks: BackgroundTasks,
) -> Broadcast:
    """Send broadcast as a dry-run to the admin's own Telegram only.

    Does NOT change broadcast status or counters — safe preview before mass send.
    """
    broadcast = await service.get_broadcast(db, broadcast_id)
    if not broadcast:
        raise NotFoundException("Broadcast not found")

    if admin.telegram_id is None:
        raise BadRequestException("Admin has no linked Telegram account")

    background_tasks.add_task(service.execute_test_send, broadcast_id, admin.telegram_id)

    return broadcast


@router.post("/{broadcast_id}/cancel", response_model=BroadcastResponse)
async def cancel_broadcast(
    broadcast_id: int,
    db: DbSession,
    _admin: AdminUser,
) -> Broadcast:
    """Cancel a broadcast."""
    broadcast = await service.get_broadcast(db, broadcast_id)
    if not broadcast:
        raise NotFoundException("Broadcast not found")

    if broadcast.status not in (BroadcastStatus.DRAFT, BroadcastStatus.SENDING):
        raise BadRequestException(f"Cannot cancel broadcast with status {broadcast.status}")

    return await service.cancel_broadcast(db, broadcast)
