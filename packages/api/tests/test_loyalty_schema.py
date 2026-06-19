"""Schema-level tests for the loyalty models (Biotact Club phase 1)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.models import (
    CodeBatch,
    LotteryDraw,
    LotteryTicket,
    LoyaltyStatus,
    PointTransaction,
    QrCode,
    SurveyAnswer,
    TicketSource,
    TransactionReason,
)
from src.users.models import User


async def _make_user(db: AsyncSession, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_user_loyalty_defaults(test_db: AsyncSession):
    """A new user starts as NEWBIE with a zero balance and no activations."""
    user = await _make_user(test_db, telegram_id=1001)

    assert user.balance == 0
    assert user.loyalty_status is LoyaltyStatus.NEWBIE
    assert user.lifetime_activations == 0
    assert user.last_activation_at is None


@pytest.mark.asyncio
async def test_code_batch_and_qr_code(test_db: AsyncSession):
    """A QR code links to its batch; codes default to unused."""
    batch = CodeBatch(
        sku_code="ZNK",
        sku_name="BIFOLAK-ZINCUM стик",
        unit_price=Decimal("75500"),
        quantity=1,
    )
    test_db.add(batch)
    await test_db.flush()

    code = QrCode(code="BTQR-2026-ZNK-0000001", batch_id=batch.id)
    test_db.add(code)
    await test_db.commit()

    stored = (
        await test_db.execute(select(QrCode).where(QrCode.code == "BTQR-2026-ZNK-0000001"))
    ).scalar_one()
    assert stored.batch_id == batch.id
    assert stored.is_used is False
    assert batch.is_armed is False


@pytest.mark.asyncio
async def test_point_transactions_sum_to_balance(test_db: AsyncSession):
    """The ledger is the source of truth: SUM(amount) is the user's balance."""
    user = await _make_user(test_db, telegram_id=1002)
    test_db.add_all(
        [
            PointTransaction(
                user_id=user.id, amount=3775, reason=TransactionReason.ACTIVATION
            ),
            PointTransaction(
                user_id=user.id, amount=1888, reason=TransactionReason.WELCOME_BONUS
            ),
        ]
    )
    await test_db.commit()

    total = (
        await test_db.execute(
            select(func.coalesce(func.sum(PointTransaction.amount), 0)).where(
                PointTransaction.user_id == user.id
            )
        )
    ).scalar_one()
    assert total == 5663


@pytest.mark.asyncio
async def test_survey_answer_unique_per_question(test_db: AsyncSession):
    """One answer per (user, question_key) — re-answering must not duplicate."""
    user = await _make_user(test_db, telegram_id=1003)
    test_db.add(SurveyAnswer(user_id=user.id, level=1, question_key="gender", answer="female"))
    await test_db.commit()

    test_db.add(SurveyAnswer(user_id=user.id, level=1, question_key="gender", answer="male"))
    with pytest.raises(IntegrityError):
        await test_db.commit()


@pytest.mark.asyncio
async def test_lottery_ticket_unique_per_source(test_db: AsyncSession):
    """A user gets at most one ticket per source per draw (phase-1 cap)."""
    user = await _make_user(test_db, telegram_id=1004)
    draw = LotteryDraw(period="2026-07", draw_at=datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc))
    test_db.add(draw)
    await test_db.flush()

    test_db.add(
        LotteryTicket(
            draw_id=draw.id, user_id=user.id, ticket_number=1, source=TicketSource.SURVEY_L1
        )
    )
    await test_db.commit()

    test_db.add(
        LotteryTicket(
            draw_id=draw.id, user_id=user.id, ticket_number=2, source=TicketSource.SURVEY_L1
        )
    )
    with pytest.raises(IntegrityError):
        await test_db.commit()
