"""Admin surface of the monthly draw: freezing the participant snapshot (PR-5.3) and exporting it
for external verification (PR-5.4a)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_admin
from src.database import get_db
from src.loyalty import lottery
from src.loyalty.router import require_loyalty_enabled
from src.loyalty.schemas import (
    CommitDrawRequest,
    CommitDrawResponse,
    RecordWinnersRequest,
    RecordWinnersResponse,
    WinnerRowResponse,
)

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


@router.get(
    "/{draw_id}/export",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def export_draw(
    draw_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PlainTextResponse:
    """Download the frozen participant list of a draw, for external verification of its hash.

    The body is **byte-for-byte** the string the published SHA-256 was taken over, so verifying us
    is one command — ``sha256sum draw-YYYY-MM.txt`` — with nothing to reassemble on the verifier's
    side. Nothing may be added to it: a header line, the hash itself or a trailing newline would all
    change the file's digest and break the very proof this serves.

    Served as an attachment for the same reason — the file must reach the public as a file. Text
    pasted through an editor or a messenger picks up a trailing newline and stops matching, which
    looks like a discrepancy in the draw rather than in the transport.

    Guarded by :func:`~src.auth.dependencies.require_admin` (admin-typed token + ``is_admin`` in the
    database) like ``POST /commit``, though this one only reads: two different gates inside one
    four-endpoint router is how drift starts.

    404 when no such draw exists; 409 when the stored snapshot no longer matches the published hash.
    """
    outcome = await lottery.export_draw(db, draw_id=draw_id)
    return PlainTextResponse(
        content=outcome.body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="draw-{outcome.period}.txt"'},
    )


@router.post(
    "/{draw_id}/winners",
    response_model=RecordWinnersResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def record_winners(
    draw_id: int,
    payload: RecordWinnersRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RecordWinnersResponse:
    """Record the numbers drawn live on air and close the draw. **Irreversible.**

    The winner is never chosen here: random.org produces the numbers in front of an audience and an
    administrator types what was announced. This endpoint only refuses what would contradict the
    ratified rule — one person takes at most one prize, and a winner leaves the later rounds as a
    person, not merely as a ticket.

    A number whose owner already won is refused with a message naming that number, so the host can
    draw again in the open. It is not skipped silently: the room heard the number, and its quiet
    disappearance from the published result could only be justified by "trust us".

    **All or nothing.** Any rejected position cancels the whole call — nothing is written and the
    draw stays frozen. Guarded by :func:`~src.auth.dependencies.require_admin`, like the freeze:
    this writes a public, unrepeatable artifact.

    404 — no such draw. 400 — a number outside 1..total, or repeated in the list. 409 — the draw is
    not frozen, was already drawn, another administrator closed it first, or a number belongs to
    someone who already won.
    """
    outcome = await lottery.record_winners(
        db, draw_id=draw_id, winning_numbers=payload.winning_numbers
    )
    return RecordWinnersResponse(
        draw_id=outcome.draw_id,
        period=outcome.period,
        status=outcome.status,
        drawn_at=outcome.drawn_at,
        winners=[
            WinnerRowResponse(
                ticket_number=w.ticket_number,
                prize_rank=w.prize_rank,
                user_id=w.user_id,
                first_name=w.first_name,
            )
            for w in outcome.winners
        ],
    )
