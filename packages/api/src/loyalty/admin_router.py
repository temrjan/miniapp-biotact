"""Admin surface of the monthly draw (Stage 5, PR-5.3): freezing the participant snapshot."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_admin
from src.database import get_db
from src.loyalty import lottery
from src.loyalty.router import require_loyalty_enabled
from src.loyalty.schemas import CommitDrawRequest, CommitDrawResponse

router = APIRouter()


@router.post(
    "/commit",
    response_model=CommitDrawResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def commit_draw(
    payload: CommitDrawRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommitDrawResponse:
    """Freeze the participant snapshot of a period and publish its SHA-256. **Irreversible.**

    Guarded by :func:`~src.auth.dependencies.require_admin` (admin-typed token + ``is_admin`` in
    the database), not by the lightweight ``verify_admin_jwt``: this endpoint writes a public,
    unrepeatable artifact.

    409 in three cases, each with its own message: the period is already frozen (repeat call), the
    period was frozen microseconds earlier by a concurrent caller, or nobody qualifies — an empty
    month is refused rather than frozen, because freezing it would consume the period for good.
    """
    outcome = await lottery.commit_draw(db, period=payload.period, prize_label=payload.prize_label)
    return CommitDrawResponse(
        draw_id=outcome.draw_id,
        period=outcome.period,
        total_tickets=outcome.total_tickets,
        participant_hash=outcome.participant_hash,
        committed_at=outcome.committed_at,
    )
