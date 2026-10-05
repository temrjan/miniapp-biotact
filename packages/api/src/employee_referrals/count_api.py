"""Service-only bounded authenticated readback; disabled without key or SQL."""

import asyncio
import hmac
import re
import time

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from src.config import settings
from src.employee_referrals.count_store import observe
from src.employee_referrals.count_wire import (
    MAX_REQUEST,
    MAX_RESPONSE,
    PATH,
    CountError,
    RequestBody,
    canonical,
    decode,
    signature,
)

router = APIRouter()


def error(code: str, status: int = 503) -> JSONResponse:
    return JSONResponse(
        {"detail": {"code": "count_" + code}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def authenticate(request: Request, key: str) -> bytes:
    if (
        request.method != "POST"
        or request.url.path != PATH
        or request.scope.get("raw_path") != PATH.encode()
        or request.scope.get("query_string")
    ):
        raise CountError()
    timestamps = request.headers.getlist("x-biotact-count-timestamp")
    signatures = request.headers.getlist("x-biotact-count-signature")
    if (
        len(timestamps) != 1
        or len(signatures) != 1
        or not re.fullmatch(r"0|[1-9][0-9]{0,11}", timestamps[0])
        or not re.fullmatch(r"[0-9a-f]{64}", signatures[0])
        or abs(int(timestamps[0]) - int(time.time())) > 30
    ):
        raise CountError("invalid_auth")
    media = request.headers.getlist("content-type")
    encoding = request.headers.getlist("content-encoding")
    if (
        len(media) != 1
        or not re.fullmatch(
            r'application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?', media[0], re.IGNORECASE
        )
        or len(encoding) > 1
        or (encoding and encoding[0].lower() != "identity")
    ):
        raise CountError("unsupported_media")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST:
            raise CountError("payload_too_large")
        raw.extend(chunk)
    body = bytes(raw)
    if not hmac.compare_digest(signature(key, body, timestamps[0]), signatures[0]):
        raise CountError("invalid_auth")
    return body


@router.post(PATH)
@router.post(PATH + "/", include_in_schema=False)
async def readback(request: Request) -> Response:
    if settings.employee_referral_count_readback_enabled == "false":
        return error("disabled", 404)
    try:
        started = time.monotonic()
        async with asyncio.timeout(10):
            key = settings.employee_referral_count_readback_key.get_secret_value()
            other = {
                settings.employee_referral_k1_key.get_secret_value(),
                settings.employee_referral_k2_key.get_secret_value(),
                settings.secret_key.get_secret_value(),
                settings.bot_api_token.get_secret_value(),
                settings.campaign_service_token.get_secret_value(),
                settings.telegram_bot_token,
            }
            if (
                settings.employee_referral_count_readback_enabled != "true"
                or settings.database_echo
                or not re.fullmatch(r"[0-9a-f]{64}", key)
                or key in other
            ):
                raise CountError("unavailable")
            body = await authenticate(request, key)
            try:
                query = RequestBody.model_validate(decode(body))
            except (ValueError, RecursionError):
                raise CountError() from None
            value = await observe(
                request.app.state.employee_referral_sessions, query.campaign_id, query.day
            )
            raw = canonical(value)
            if time.monotonic() - started >= 10:
                raise CountError("unavailable")
            if len(raw) > MAX_RESPONSE:
                raise CountError("payload_too_large")
            return Response(
                raw, media_type="application/json", headers={"Cache-Control": "no-store"}
            )
    except CountError as exc:
        code = exc.code.removeprefix("count_")
        return error(
            code,
            {
                "invalid_auth": 401,
                "payload_too_large": 413,
                "unsupported_media": 415,
                "invalid_request": 422,
                "unknown_campaign": 404,
            }.get(code, 503),
        )
    except Exception:
        return error("unavailable")
