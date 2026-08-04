"""Loyalty (Biotact Club) API router — Stage 2 QR activation.

Dark by default: every route sits behind the ``loyalty_enabled`` master flag, which is off
on prod until the end of Stage 4. The activation endpoint authenticates via Telegram initData
(reusing :func:`src.auth.telegram.validate_init_data`) and delegates the money-mutating,
race-safe transaction to :func:`src.loyalty.service.activate_code`.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.schemas import TelegramUser
from src.auth.telegram import InitDataValidationError, validate_init_data
from src.common.exceptions import NotFoundException, UnauthorizedException
from src.config import settings
from src.database import get_db
from src.loyalty import lottery, service
from src.loyalty.pricing import PricingError
from src.loyalty.schemas import (
    ActivateRequest,
    ActivateResponse,
    ConsentRequest,
    ConsentResponse,
    DrawResultResponse,
    LoyaltySummaryResponse,
    MyTicketsResponse,
    SurveyRequest,
    SurveyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()
# Second router mounted at /loyalty (the activation `router` stays at /qr) — carries the read
# summary (GET /me) and the consent write (POST /consent). Distinct object so main.py never
# shadows the activation router.
summary_router = APIRouter()


def require_loyalty_enabled() -> None:
    """Gate the loyalty surface behind the master flag.

    While the flag is off (prod default) the endpoint behaves as if it does not exist — a 404
    fires from this route-level dependency before auth runs, so the dark feature is not probeable.
    """
    if not settings.loyalty_enabled:
        raise NotFoundException("Not found")


async def get_init_data_user(
    x_init_data: Annotated[str | None, Header(alias="X-Init-Data")] = None,
) -> TelegramUser:
    """Authenticate via Telegram initData WITHOUT touching the DB.

    Activation owns one explicit transaction (``async with db.begin()``) and creates the user
    lazily inside it; a DB-writing auth dependency would open the session's transaction first and
    make that explicit ``begin()`` impossible. So auth here is pure HMAC validation.
    """
    if not x_init_data:
        raise UnauthorizedException("Missing X-Init-Data header")
    if not settings.telegram_bot_token:
        raise UnauthorizedException("Bot token not configured")
    try:
        init_data = validate_init_data(x_init_data, settings.telegram_bot_token)
    except InitDataValidationError as e:
        raise UnauthorizedException(str(e)) from e
    if not init_data.user:
        raise UnauthorizedException("User data missing in initData")
    return init_data.user


@router.post(
    "/activate",
    response_model=ActivateResponse,
    dependencies=[Depends(require_loyalty_enabled)],
)
async def activate_qr(
    payload: ActivateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    telegram_user: Annotated[TelegramUser, Depends(get_init_data_user)],
) -> ActivateResponse:
    """Activate a scanned QR code: claim it and accrue cashback (idempotent, race-safe)."""
    try:
        outcome = await service.activate_code(db, telegram_user=telegram_user, code=payload.code)
    except PricingError as e:
        # A claimed code whose product cannot be priced (missing/NULL/fractional). The claim was
        # rolled back with the transaction (no partial credit) — log loudly, ask the client to
        # retry once ops fixes the price. Never a 500, never silent zero accrual.
        logger.error(
            "QR activation: product %s cannot be priced (%s); claim rolled back", e.product_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Code temporarily unavailable, please try again later",
        ) from e
    return ActivateResponse(
        status=outcome.status,
        tier=outcome.tier,
        points_awarded=outcome.points_awarded,
        welcome_bonus=outcome.welcome_bonus,
        friend_bonus=outcome.friend_bonus,
        balance=outcome.balance,
        lifetime_activations=outcome.lifetime_activations,
    )


@summary_router.get(
    "/me",
    response_model=LoyaltySummaryResponse,
    dependencies=[Depends(require_loyalty_enabled)],
)
async def get_loyalty_me(
    db: Annotated[AsyncSession, Depends(get_db)],
    telegram_user: Annotated[TelegramUser, Depends(get_init_data_user)],
) -> LoyaltySummaryResponse:
    """Read-only loyalty summary for the current user: tier, balance, progress to 'Друг'.

    Same dark gate (404 while ``loyalty_enabled`` is off) and initData HMAC auth as activation.
    Pure read — never creates a user/profile row (see :func:`service.get_loyalty_summary`).
    Echoes the stored survey (gender/age/city) so the mini-app stops re-asking once saved, plus
    ``is_adult`` so the 18+ checkbox renders as the member left it.
    """
    summary = await service.get_loyalty_summary(db, telegram_user.id)
    return LoyaltySummaryResponse(
        tier=summary.tier,
        balance=summary.balance,
        available=summary.available,
        reserved_until=summary.reserved_until,
        lifetime_activations=summary.lifetime_activations,
        activations_to_friend=summary.activations_to_friend,
        is_member=summary.is_member,
        gender=summary.gender,
        age=summary.age,
        city=summary.city,
        is_adult=summary.is_adult,
    )


@summary_router.post(
    "/consent",
    response_model=ConsentResponse,
    dependencies=[Depends(require_loyalty_enabled)],
)
async def submit_consent(
    payload: ConsentRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    telegram_user: Annotated[TelegramUser, Depends(get_init_data_user)],
) -> ConsentResponse:
    """Record data-processing consent + entry lifecycle events (idempotent, race-safe).

    Same dark gate (404 while ``loyalty_enabled`` is off) and initData HMAC auth as activation. The
    money path (``/qr/activate``) does NOT hard-block on consent; the happy-path mini-app calls this
    first (writes ``data_processing`` + ``joined``/``phone_shared``, sets ``registration_source``),
    then activates. Idempotent: a repeat call by the same user writes nothing and returns all-False.
    """
    outcome = await service.record_consent(db, telegram_user=telegram_user, source=payload.source)
    return ConsentResponse(
        consent_recorded=outcome.consent_recorded,
        joined=outcome.joined,
        phone_shared=outcome.phone_shared,
    )


@summary_router.post(
    "/survey",
    response_model=SurveyResponse,
    dependencies=[Depends(require_loyalty_enabled)],
)
async def submit_survey(
    payload: SurveyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    telegram_user: Annotated[TelegramUser, Depends(get_init_data_user)],
) -> SurveyResponse:
    """Record progressive survey fields (gender/age/city/is_adult), race-safe and event-idempotent.

    Same dark gate (404 while ``loyalty_enabled`` is off) and initData HMAC auth as activation.
    Requires an existing member (``customer_profile`` row) — 404 otherwise; never creates one.
    A body carrying only ``is_adult`` (the 18+ lottery confirmation) is valid and writes the flag
    without marking the survey done — see :func:`service.submit_survey`.
    """
    outcome = await service.submit_survey(db, telegram_user=telegram_user, payload=payload)
    return SurveyResponse(
        updated_fields=outcome.updated_fields,
        survey_done=outcome.survey_done,
    )


@summary_router.get(
    "/my-tickets",
    response_model=MyTicketsResponse,
    dependencies=[Depends(require_loyalty_enabled)],
)
async def get_my_tickets(
    db: Annotated[AsyncSession, Depends(get_db)],
    telegram_user: Annotated[TelegramUser, Depends(get_init_data_user)],
) -> MyTicketsResponse:
    """The member's own lottery participation: how many tickets, and how the last draw ended.

    Same dark gate (404 while ``loyalty_enabled`` is off) and initData HMAC auth as the rest of the
    mini-app surface. Self-scoped by construction: the user is derived from the signature, never
    from a query parameter, so another member's tickets are unreachable rather than merely
    forbidden.

    Pure read — a member who never joined gets zeroes, not a 404 and not a freshly created row.
    """
    outcome = await lottery.get_my_tickets(db, telegram_id=telegram_user.id)
    return MyTicketsResponse(
        tickets=outcome.tickets,
        is_frozen=outcome.is_frozen,
        draw_number=outcome.draw_number,
        prize_label=outcome.prize_label,
        numbers=list(outcome.numbers),
        clean_tickets=outcome.clean_tickets,
        not_playing_reason=outcome.not_playing_reason,
        result=(
            None
            if outcome.result is None
            else DrawResultResponse(
                draw_number=outcome.result.draw_number,
                tickets=outcome.result.tickets,
                won=outcome.result.won,
                prize_rank=outcome.result.prize_rank,
                prize_label=outcome.result.prize_label,
            )
        ),
    )
