"""Explicit low-privilege browser surface; existing business auth is unchanged."""

from datetime import datetime
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.buyer_auth.security import (
    CONTEXT_COOKIE,
    SESSION_COOKIE,
    BuyerAuthError,
    context_deadline,
    csrf,
    new_context,
    normalize_email,
    require_csrf,
)
from src.buyer_auth.service import BuyerAuthService
from src.config import Settings

router = APIRouter(prefix="/buyer/auth", tags=["buyer-auth"])


def error_response(error: BuyerAuthError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if error.retry:
        headers["Retry-After"] = str(error.retry)
    return JSONResponse({"error": error.code}, status_code=error.status, headers=headers)


def service(request: Request) -> BuyerAuthService:
    value: BuyerAuthService = request.app.state.buyer_auth
    return value


def peer(request: Request, config: Settings) -> str:
    actual = request.client.host if request.client else "unknown"
    try:
        address = ip_address(actual)
        if any(address in ip_network(network) for network in config.buyer_trusted_proxy_peers):
            # The activation contract requires ingress to overwrite this single-address header.
            return str(ip_address(request.headers.get("x-real-ip", "")))
        return str(address)
    except ValueError:
        return "unknown"


def set_cookie(response: Response, name: str, token: str, expires: datetime, at: datetime) -> None:
    response.set_cookie(
        name,
        token,
        max_age=max(0, int((expires - at).total_seconds())),
        expires=expires,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChallengeInput(EmptyInput):
    email: str

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)


class VerifyInput(EmptyInput):
    challenge_id: str
    code: str


def require_context(request: Request, auth: BuyerAuthService) -> str:
    value = request.cookies.get(CONTEXT_COOKIE)
    context_deadline(auth.config, value, auth.clock())
    if value is None:
        raise BuyerAuthError()
    require_csrf(csrf(auth.config, value), request.headers.get("x-csrf-token"))
    return value


async def invalid_input_response(request: Request) -> JSONResponse:
    """Malformed verify fields still consume the valid context's durable peer budget."""
    route = request.scope["path"].rstrip("/")
    if request.method == "POST" and route.endswith("/email/verify"):
        auth = service(request)
        try:
            require_context(request, auth)
            await auth.account_verification(peer(request, auth.config))
        except BuyerAuthError as error:
            return error_response(error)
        except Exception:
            return error_response(BuyerAuthError(503, "authentication_unavailable"))
    return error_response(BuyerAuthError(422, "invalid_request"))


@router.post("/context")
async def context(request: Request, body: EmptyInput) -> JSONResponse:
    del body
    auth = service(request)
    at = auth.clock()
    token = request.cookies.get(CONTEXT_COOKIE)
    try:
        expires = context_deadline(auth.config, token, at)
    except BuyerAuthError:
        await auth.account("context-peer", peer(request, auth.config), 30, 600)
        token = new_context(auth.config, at)
        expires = context_deadline(auth.config, token, at)
    if token is None:
        raise BuyerAuthError()
    response = JSONResponse(
        jsonable_encoder({"csrf_token": csrf(auth.config, token), "expires_at": expires})
    )
    set_cookie(response, CONTEXT_COOKIE, token, expires, at)
    return response


@router.post("/email/challenges", status_code=202)
async def challenge(request: Request, body: ChallengeInput) -> dict[str, object]:
    auth = service(request)
    browser = require_context(request, auth)
    return await auth.reserve(
        body.email, browser, peer(request, auth.config), request.cookies.get(SESSION_COOKIE)
    )


@router.post("/email/verify")
async def verify(request: Request, body: VerifyInput) -> JSONResponse:
    auth = service(request)
    browser = require_context(request, auth)
    data, token, expires = await auth.verify(
        body.challenge_id,
        body.code,
        browser,
        peer(request, auth.config),
        request.cookies.get(SESSION_COOKIE),
    )
    response = JSONResponse(jsonable_encoder(data))
    set_cookie(response, SESSION_COOKIE, token, expires, auth.clock())
    return response


@router.get("/session")
async def session(request: Request) -> dict[str, object]:
    data, _token, _expires = await service(request).use_session(request.cookies.get(SESSION_COOKIE))
    return data


@router.post("/session/rotate")
async def rotate(request: Request, body: EmptyInput) -> JSONResponse:
    del body
    auth = service(request)
    data, token, expires = await auth.use_session(
        request.cookies.get(SESSION_COOKIE),
        rotate=True,
        supplied_csrf=request.headers.get("x-csrf-token"),
    )
    response = JSONResponse(jsonable_encoder(data))
    set_cookie(response, SESSION_COOKIE, token, expires, auth.clock())
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request, body: EmptyInput) -> Response:
    del body
    await service(request).logout(
        request.cookies.get(SESSION_COOKIE), request.headers.get("x-csrf-token")
    )
    response = Response(status_code=204)
    for name in (SESSION_COOKIE, CONTEXT_COOKIE):
        response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")
    return response


class BuyerBoundaryMiddleware:
    """Guard private bodies/origin before Pydantic; run inside auth privacy, outside CORS."""

    def __init__(self, app: ASGIApp, prefix: str, config: Settings) -> None:
        self.app, self.prefix, self.config = app, prefix, config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == self.prefix or path.startswith(self.prefix + "/")
        ):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        route = path.removeprefix(self.prefix).rstrip("/")
        email_route = route in {"/context", "/email/challenges", "/email/verify"}
        error = None
        if route != "/logout" and (
            not self.config.buyer_sessions_enabled
            or (email_route and not self.config.buyer_email_auth_enabled)
        ):
            error = BuyerAuthError(404, "buyer_auth_disabled")
        elif (scope["method"] == "POST" or request.headers.get("origin") is not None) and (
            request.headers.get("origin") != self.config.buyer_public_origin
            or request.headers.get("host") != urlsplit(self.config.buyer_public_origin).netloc
        ):
            error = BuyerAuthError(403, "request_forbidden")
        if (
            error is None
            and scope["method"] == "POST"
            and request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            error = BuyerAuthError(415, "json_required")

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() not in {b"cache-control", b"etag", b"last-modified"}
                        and not k.lower().startswith(b"access-control-")
                    ]
                    + [(b"cache-control", b"no-store")],
                }
            await send(message)

        if error:
            await error_response(error)(scope, receive, private_send)
            return
        body = bytearray()
        if scope["method"] == "POST":
            while True:
                part = await receive()
                if part["type"] == "http.disconnect":
                    return
                chunk = part.get("body", b"")
                if len(body) + len(chunk) > 4096:
                    await error_response(BuyerAuthError(413, "request_too_large"))(
                        scope, receive, private_send
                    )
                    return
                body.extend(chunk)
                if not part.get("more_body", False):
                    break
            delivered = False

            async def replay() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay, private_send)
        else:
            await self.app(scope, receive, private_send)
