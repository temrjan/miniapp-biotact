"""Current active admin, bounded strict request and durable commit before receipt."""

import re
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPBearer
from jwt import PyJWTError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive, Scope, Send

from src.auth.dependencies import require_admin
from src.config import settings
from src.database import get_db
from src.employee_referrals.auth import bounded_body
from src.employee_referrals.return_schemas import ReturnError, decimal, parse_intent
from src.employee_referrals.returns import confirm, read_history
from src.employee_referrals.schemas import K1Error

_bearer = HTTPBearer(auto_error=False)


def return_error(code: str, status: int | None = None) -> JSONResponse:
    status = (
        status
        or {
            "invalid_auth": 401,
            "forbidden": 403,
            "not_found": 404,
            "conflict": 409,
            "bounds": 409,
            "payload_too_large": 413,
            "unsupported_media": 415,
            "invalid_request": 422,
            "unavailable": 503,
        }[code]
    )
    return JSONResponse(
        {"detail": {"code": "referral_return_" + code}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


class ReturnRoute(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        from src.employee_referrals.return_privacy import return_privacy_scope

        with return_privacy_scope():
            started = False

            async def protected_send(message: Message) -> None:
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                await send(message)

            try:
                await super().handle(scope, receive, protected_send)
            except ReturnError as exc:
                if started:
                    raise
                await return_error(exc.code.removeprefix("referral_return_"))(scope, receive, send)
            except Exception:
                if started:
                    raise
                # Includes dependency teardown before response start.
                await return_error("unavailable")(scope, receive, send)


router = APIRouter(route_class=ReturnRoute)


@router.api_route("/admin/orders/{order_id}/referral-returns", methods=["GET", "POST"])
@router.api_route(
    "/admin/orders/{order_id}/referral-returns/", methods=["GET", "POST"], include_in_schema=False
)
async def returns(
    request: Request, order_id: str, db: Annotated[AsyncSession, Depends(get_db, scope="function")]
) -> JSONResponse:
    # Configuration is checked before any private SQL, including current-admin lookup.
    if settings.database_echo or db.get_bind().engine.echo:
        raise ReturnError("unavailable")
    if request.method == "GET":
        await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    credentials = await _bearer(request)
    if credentials is None or len(request.headers.getlist("authorization")) != 1:
        raise ReturnError("invalid_auth")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            options={"require": ["exp"]},
        )
        subject = payload.get("sub")
        if not isinstance(subject, str):
            raise ValueError("Invalid subject")
        decimal(subject)
        admin = await require_admin(db=db, credentials=credentials)
        if not admin.is_active:
            raise ReturnError("forbidden")
    except (PyJWTError, ValueError, TypeError, OverflowError) as exc:
        raise ReturnError("invalid_auth") from exc
    except HTTPException as exc:
        raise ReturnError("invalid_auth" if exc.status_code == 401 else "forbidden") from exc
    try:
        decimal(order_id)
    except ValueError as exc:
        raise ReturnError() from exc
    expected = f"{settings.api_v1_prefix}/admin/orders/{order_id}/referral-returns"
    if (
        request.scope.get("query_string")
        or request.scope["path"] != expected
        or request.scope.get("raw_path", expected.encode()) != expected.encode()
    ):
        raise ReturnError()
    if request.method == "GET":
        return JSONResponse((await read_history(db, int(order_id))).summary())
    try:
        body = await bounded_body(request)
    except K1Error as exc:
        raise ReturnError(exc.code.removeprefix("k1_")) from exc
    result = await confirm(db, int(order_id), parse_intent(body), admin.id)
    await db.commit()
    return JSONResponse(result)


def private_return_path(path: str, prefix: str) -> bool:
    return bool(re.fullmatch(re.escape(prefix) + r"/admin/orders/[^/]+/referral-returns/?", path))
