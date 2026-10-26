"""Repeatable bounded retention: python -m src.buyer_auth.cleanup (no daemon)."""

import argparse
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.buyer_auth.models import (
    BuyerAuthLimit,
    BuyerEmailChallenge,
    BuyerSession,
    BuyerTelegramRequest,
)
from src.buyer_auth.security import now
from src.database import async_session_maker


async def cleanup_batch(sessions: async_sessionmaker[AsyncSession], at: datetime) -> int:
    """At most 500 rows total; retain every currently effective limit and authority."""
    removed = 0
    async with sessions.begin() as db:
        await db.execute(text("SET LOCAL lock_timeout='5s'"))
        await db.execute(text("SET LOCAL statement_timeout='30s'"))
        for model, primary, eligible in (
            (
                BuyerTelegramRequest,
                BuyerTelegramRequest.id,
                func.coalesce(BuyerTelegramRequest.terminal_at, BuyerTelegramRequest.expires_at)
                < at - timedelta(hours=24),
            ),
            (
                BuyerEmailChallenge,
                BuyerEmailChallenge.id,
                func.coalesce(BuyerEmailChallenge.terminal_at, BuyerEmailChallenge.expires_at)
                < at - timedelta(hours=24),
            ),
            (
                BuyerSession,
                BuyerSession.id,
                or_(
                    (BuyerSession.revoked_at.is_not(None))
                    & (BuyerSession.revoked_at < at - timedelta(days=7)),
                    func.least(BuyerSession.absolute_expires_at, BuyerSession.idle_expires_at)
                    < at - timedelta(days=7),
                ),
            ),
            (BuyerAuthLimit, BuyerAuthLimit.key, BuyerAuthLimit.expires_at <= at),
        ):
            remaining = 500 - removed
            if remaining == 0:
                break
            keys = (
                select(primary).where(eligible).limit(remaining).with_for_update(skip_locked=True)
            )
            result = await db.scalars(delete(model).where(primary.in_(keys)).returning(primary))
            removed += len(result.all())
    return removed


async def cancel_telegram_batch(sessions: async_sessionmaker[AsyncSession], at: datetime) -> int:
    """Bounded operational cancellation after disabled ingress is quiescent."""
    async with sessions.begin() as db:
        await db.execute(text("SET LOCAL lock_timeout='5s'"))
        await db.execute(text("SET LOCAL statement_timeout='30s'"))
        ids = (
            select(BuyerTelegramRequest.id)
            .where(BuyerTelegramRequest.state.in_(["pending", "approved"]))
            .limit(500)
            .with_for_update(skip_locked=True)
        )
        result = await db.scalars(
            update(BuyerTelegramRequest)
            .where(BuyerTelegramRequest.id.in_(ids))
            .values(state="cancelled", terminal_at=at)
            .returning(BuyerTelegramRequest.id)
        )
        return len(result.all())


async def main() -> None:
    from src.config import settings
    from src.database import engine

    parser = argparse.ArgumentParser(
        description="Bounded buyer-auth retention/disabled-entry cancellation"
    )
    parser.add_argument("--cancel-telegram", action="store_true")
    args = parser.parse_args()
    if args.cancel_telegram and settings.buyer_telegram_auth_enabled:
        parser.error("Disable and quiesce Telegram entry before cancellation")
    operation = cancel_telegram_batch if args.cancel_telegram else cleanup_batch
    try:
        print(await operation(async_session_maker, now()))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
