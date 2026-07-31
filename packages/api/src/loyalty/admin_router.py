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
    CancelDrawResponse,
    CommitDrawRequest,
    CommitDrawResponse,
    CurrentDrawInfo,
    CurrentDrawResponse,
    PrintListResponse,
    PrintRowResponse,
    RecordWinnersRequest,
    RecordWinnersResponse,
    UpdateDrawRequest,
    UpdateDrawResponse,
    WinnerRowResponse,
)

router = APIRouter()


@router.get(
    "/current",
    response_model=CurrentDrawResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def get_current_draw(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentDrawResponse:
    """The draw page's single read: the post-filter number to confirm, plus the latest draw.

    ``eligible_tickets`` is computed by the very function the freeze stamps — so the number the
    administrator types and the number the freeze takes are the same number by construction, not by
    discipline. ``clean_tickets`` counts every clean ticket, including owners who would fail the
    re-check; the page shows both so the difference is visible rather than mysterious.

    The draw is the latest BY NUMBER, of ANY status — cancelled included. The member card skips
    cancelled draws; the administrator must publish the cancellation and needs its object for that
    post.

    Guarded by :func:`~src.auth.dependencies.require_admin`, like the rest of this router.
    """
    outcome = await lottery.get_current_draw(db)
    return CurrentDrawResponse(
        eligible_tickets=outcome.eligible_tickets,
        clean_tickets=outcome.clean_tickets,
        draw=_draw_info_response(outcome.draw),
        last_drawn=_draw_info_response(outcome.last_drawn),
    )


def _draw_info_response(draw: lottery.CurrentDrawInfo | None) -> CurrentDrawInfo | None:
    """Map a service-level draw info to its response model; None passes through."""
    if draw is None:
        return None
    return CurrentDrawInfo(
        draw_id=draw.draw_id,
        draw_number=draw.draw_number,
        status=draw.status,
        total_tickets=draw.total_tickets,
        participant_hash=draw.participant_hash,
        conducted_on=draw.conducted_on,
        prize_label=draw.prize_label,
        committed_at=draw.committed_at,
        drawn_at=draw.drawn_at,
        cancelled_at=draw.cancelled_at,
        winners=[
            WinnerRowResponse(
                ticket_number=w.ticket_number,
                prize_rank=w.prize_rank,
                user_id=w.user_id,
                first_name=w.first_name,
            )
            for w in draw.winners
        ],
    )


@router.get(
    "/{draw_id}/print",
    response_model=PrintListResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def get_print_list(
    draw_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PrintListResponse:
    """The printed list of a draw: numbers ascending, each with the owner's last four digits.

    The four digits are computed on the server, zero-padded — ``0042`` and ``42`` are the same
    column a member finds themselves by, and the padding is what keeps them looking like one. The
    full ``telegram_id`` never leaves this endpoint.

    404 — no such draw. 409 — the draw is cancelled: its stamps are released and the list would
    print empty, which reads as tampering; the journal on the export endpoint is its list.
    """
    outcome = await lottery.get_print_list(db, draw_id=draw_id)
    return PrintListResponse(
        draw_number=outcome.draw_number,
        conducted_on=outcome.conducted_on,
        total_tickets=outcome.total_tickets,
        rows=[
            PrintRowResponse(ticket_number=r.ticket_number, telegram_last4=r.telegram_last4)
            for r in outcome.rows
        ],
    )


@router.post(
    "/commit",
    response_model=CommitDrawResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def commit_draw(
    payload: CommitDrawRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommitDrawResponse:
    """Stamp every eligible clean ticket into a new draw and publish its SHA-256.

    Guarded by :func:`~src.auth.dependencies.require_admin` (admin-typed token + ``is_admin`` in
    the database), not by the lightweight ``verify_admin_jwt``: this endpoint writes a public
    artifact and takes tickets out of members\' hands.

    ``expected_tickets`` is the confirmation the ratified design puts on the button, verified here
    and not only in the dialog: a client-side guard protects one client, while the operation is
    irreversible for everyone.

    409 in three cases, each with its own message: a draw is still live (close or cancel it first),
    the confirmation does not match the number ready to freeze, or there is nothing to freeze at
    all — an empty draw is refused rather than recorded.
    """
    outcome = await lottery.commit_draw(
        db,
        expected_tickets=payload.expected_tickets,
        prize_label=payload.prize_label,
        conducted_on=payload.conducted_on,
    )
    return CommitDrawResponse(
        draw_id=outcome.draw_id,
        draw_number=outcome.draw_number,
        total_tickets=outcome.total_tickets,
        participant_hash=outcome.participant_hash,
        committed_at=outcome.committed_at,
        conducted_on=outcome.conducted_on,
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
    is one command — ``sha256sum draw-3.txt`` — with nothing to reassemble on the verifier's
    side. Nothing may be added to it: a header line, the hash itself or a trailing newline would all
    change the file's digest and break the very proof this serves.

    Served as an attachment for the same reason — the file must reach the public as a file. Text
    pasted through an editor or a messenger picks up a trailing newline and stops matching, which
    looks like a discrepancy in the draw rather than in the transport.

    Guarded by :func:`~src.auth.dependencies.require_admin` (admin-typed token + ``is_admin`` in the
    database) like ``POST /commit``, though this one only reads: two different gates inside one
    four-endpoint router is how drift starts.

    A cancelled draw is served from its journal, under a filename that says ``-cancelled``: the
    list still exists as evidence, and nobody must be able to mistake it for a live one.

    404 when no such draw exists; 409 when the stored snapshot no longer matches the published hash.
    """
    outcome = await lottery.export_draw(db, draw_id=draw_id)
    suffix = "-cancelled" if outcome.cancelled else ""
    filename = f"draw-{outcome.draw_number}{suffix}.txt"
    return PlainTextResponse(
        content=outcome.body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
        draw_number=outcome.draw_number,
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


@router.post(
    "/{draw_id}/cancel",
    response_model=CancelDrawResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def cancel_draw(
    draw_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CancelDrawResponse:
    """Cancel a frozen draw: every stamp goes back to its owner as a clean ticket.

    This is the product-level undo of an irreversible-looking action, and the second line of defence
    behind the confirmation on the freeze. Only a frozen draw can be cancelled — once the winning
    numbers are recorded there is a result in the world, and taking it back is not this endpoint's
    business.

    ⚠️ **The cancellation has to be published the same way the fingerprint was.** A draw whose hash
    was announced and which then quietly disappears is indistinguishable from a draw we did not like
    the outcome of. The journal kept here (``cancelled_snapshot``: the exact bytes the hash was taken
    over) is what makes that publication checkable — anyone can re-hash it against the announced
    value.

    404 — no such draw. 409 — it is not frozen (already drawn, or already cancelled), or another
    administrator recorded the winners a moment earlier; in both cases nothing changed.
    """
    outcome = await lottery.cancel_draw(db, draw_id=draw_id)
    return CancelDrawResponse(
        draw_id=outcome.draw_id,
        draw_number=outcome.draw_number,
        cancelled_at=outcome.cancelled_at,
        released_tickets=outcome.released_tickets,
    )


@router.patch(
    "/{draw_id}",
    response_model=UpdateDrawResponse,
    dependencies=[Depends(require_loyalty_enabled), Depends(require_admin)],
)
async def update_draw(
    draw_id: int,
    payload: UpdateDrawRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UpdateDrawResponse:
    """Set or clear the announced date of a draw.

    Editable precisely because the date is outside the published hash: a postponed draw must not
    break a fingerprint that is already public. Refused on a cancelled draw — there is no event left
    to date.

    404 — no such draw. 409 — the draw is cancelled.
    """
    draw_number, conducted_on = await lottery.set_conducted_on(
        db, draw_id=draw_id, conducted_on=payload.conducted_on
    )
    return UpdateDrawResponse(draw_number=draw_number, conducted_on=conducted_on)
