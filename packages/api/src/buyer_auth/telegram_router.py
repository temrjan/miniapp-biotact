"""Strict browser and main-bot entrances to the same durable proof owner."""

import hmac
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from starlette.responses import JSONResponse

from src.buyer_auth.router import EmptyInput, peer, require_context, service, set_cookie
from src.buyer_auth.security import SESSION_COOKIE, BuyerAuthError
from src.buyer_auth.telegram import TelegramAuthService

router = APIRouter(prefix="/buyer/auth/telegram", tags=["buyer-auth"])
bridge_router = APIRouter(prefix="/bot/buyer/telegram", tags=["buyer-auth"])


def telegram(request: Request) -> TelegramAuthService:
    value: TelegramAuthService = request.app.state.buyer_telegram_auth
    return value


class CreateInput(EmptyInput):
    request_key: str = Field(pattern=r"^[0-9a-f]{32}$")


class RequestInput(EmptyInput):
    request_id: str


class PresentInput(RequestInput):
    telegram_id: int = Field(gt=0, le=9223372036854775807)


class DecisionInput(PresentInput):
    decision: Literal["confirm", "decline"]


class RetryAfterInput(EmptyInput):
    telegram_id: int = Field(gt=0, le=9223372036854775807)
    retry_after: int = Field(gt=0)


def require_bot(request: Request) -> None:
    auth = service(request)
    supplied = request.headers.get("x-bot-token", "")
    expected = auth.config.telegram_bot_token
    if (
        not expected
        or not supplied
        or not supplied.isascii()
        or not hmac.compare_digest(expected, supplied)
    ):
        raise BuyerAuthError()
    telegram(request).enabled()


@router.post("/requests", status_code=202)
async def create(request: Request, body: CreateInput) -> dict[str, object]:
    auth = service(request)
    return await telegram(request).create(
        body.request_key,
        require_context(request, auth),
        peer(request, auth.config),
        request.cookies.get(SESSION_COOKIE),
    )


@router.post("/status")
async def status(request: Request, body: RequestInput) -> dict[str, object]:
    auth = service(request)
    return await telegram(request).inspect(
        body.request_id, require_context(request, auth), peer(request, auth.config)
    )


@router.post("/cancel")
async def cancel(request: Request, body: RequestInput) -> dict[str, object]:
    auth = service(request)
    return await telegram(request).inspect(
        body.request_id, require_context(request, auth), peer(request, auth.config), cancel=True
    )


@router.post("/complete")
async def complete(request: Request, body: RequestInput) -> JSONResponse:
    auth = service(request)
    data, token, expires = await telegram(request).complete(
        body.request_id,
        require_context(request, auth),
        peer(request, auth.config),
        request.cookies.get(SESSION_COOKIE),
    )
    response = JSONResponse(jsonable_encoder(data))
    set_cookie(response, SESSION_COOKIE, token, expires, auth.clock())
    return response


@bridge_router.post("/present")
async def present(request: Request, body: PresentInput) -> dict[str, object]:
    require_bot(request)
    return await telegram(request).bridge(body.request_id, body.telegram_id)


@bridge_router.post("/decision")
async def decision(request: Request, body: DecisionInput) -> dict[str, object]:
    require_bot(request)
    return await telegram(request).bridge(body.request_id, body.telegram_id, body.decision)


@bridge_router.post("/retry-after")
async def retry_after(request: Request, body: RetryAfterInput) -> dict[str, object]:
    require_bot(request)
    await telegram(request).report_retry_after(body.telegram_id, body.retry_after)
    return {}
