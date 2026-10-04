"""Authenticated, bounded consumer resolve; no referral writes or remote calls."""

import json
import re
from collections.abc import Callable, Coroutine

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from src.auth.dependencies import CurrentUser
from src.config import settings
from src.database import engine
from src.employee_referrals.capture import current_referral
from src.employee_referrals.schemas import canonical_token, utc_text

ENTRY_PATH = "/employee-referrals/resolve"
ERRORS = {
    "disabled": (404, "Referral entry is disabled"),
    "payload_too_large": (413, "Referral entry payload exceeds the size limit"),
    "unsupported_media": (415, "Unsupported referral entry media type or encoding"),
    "invalid_request": (422, "Invalid referral entry request"),
    "invalid_auth": (401, "Invalid referral entry authentication"),
    "forbidden": (403, "Referral entry access denied"),
    "unavailable": (503, "Referral entry is temporarily unavailable"),
}


class EntryError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def entry_error(code: str) -> JSONResponse:
    status, message = ERRORS[code]
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Telegram-Init-Data"
    return JSONResponse(
        {"detail": {"code": f"referral_entry_{code}", "message": message}},
        status_code=status,
        headers=headers,
    )


class EntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(max_length=256)


def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


async def entry_body(request: Request) -> str:
    media = request.headers.getlist("content-type")
    encoding = request.headers.getlist("content-encoding")
    if (
        len(media) != 1
        or not re.fullmatch(
            r'application/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?\s*', media[0], re.IGNORECASE
        )
        or (encoding and (len(encoding) != 1 or encoding[0].lower() != "identity"))
    ):
        raise EntryError("unsupported_media")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1024:
            raise EntryError("payload_too_large")
        body.extend(chunk)
    try:
        return EntryRequest.model_validate(
            json.loads(body.decode("utf-8"), object_pairs_hook=unique_keys)
        ).token
    except (ValueError, ValidationError):
        raise EntryError("invalid_request") from None


class EntryRoute(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Also preserve the boundary when this route is mounted under another prefix.
        from src.employee_referrals.entry_privacy import entry_privacy_scope

        with entry_privacy_scope():
            await super().handle(scope, receive, send)

    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                if not (
                    settings.employee_referral_entry_enabled
                    and settings.employee_referral_capture_enabled
                ):
                    return entry_error("disabled")
                maker: async_sessionmaker[AsyncSession] = (
                    request.app.state.employee_referral_sessions
                )
                bind = maker.kw.get("bind")
                if (
                    settings.debug
                    or settings.database_echo
                    or engine.echo
                    or (bind is not None and bind.echo)
                ):
                    return entry_error("unavailable")
                # The handler's complete dependency chain is inside this redaction boundary.
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except EntryError as exc:
                return entry_error(exc.code)
            except HTTPException as exc:
                return entry_error(
                    {401: "invalid_auth", 403: "forbidden"}.get(exc.status_code, "unavailable")
                )
            except Exception:
                return entry_error("unavailable")

        return guarded


router = APIRouter(route_class=EntryRoute)


@router.post(ENTRY_PATH)
@router.post(ENTRY_PATH + "/", include_in_schema=False)
async def resolve(request: Request, _user: CurrentUser) -> dict[str, str]:
    token = await entry_body(request)
    try:
        canonical_token(token)
    except ValueError:
        return {"state": "invalid"}
    maker: async_sessionmaker[AsyncSession] = request.app.state.employee_referral_sessions
    async with maker.begin() as session:
        current = await current_referral(session, token)
        if current is None:
            return {"state": "invalid"}
        campaign, _identity, observed = current
        return {
            "state": "valid",
            "product_id": str(campaign.product_id),
            "observed_at": utc_text(observed),
            "expires_at": utc_text(campaign.end_at),
        }
