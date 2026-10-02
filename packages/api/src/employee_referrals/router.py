"""K1-only outer boundary: flag, configuration, auth, bounded bytes, then storage."""

from collections.abc import Callable, Coroutine

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import Receive, Scope, Send

from src.config import settings
from src.database import engine
from src.employee_referrals.auth import auth_headers, bounded_body, decode_key, verify_signature
from src.employee_referrals.privacy import K1_PATH, k1_privacy_scope
from src.employee_referrals.schemas import Ack, ErrorCode, K1Error, parse_snapshot
from src.employee_referrals.service import receive_snapshot

ERRORS: dict[ErrorCode, tuple[int, str]] = {
    "k1_disabled": (404, "K1 receiver is disabled."),
    "k1_invalid_auth": (401, "Invalid K1 authentication."),
    "k1_payload_too_large": (413, "K1 payload exceeds the size limit."),
    "k1_unsupported_media": (415, "Unsupported K1 media type or encoding."),
    "k1_invalid_request": (422, "Invalid K1 request."),
    "k1_event_conflict": (409, "K1 event identity conflict."),
    "k1_revision_conflict": (409, "K1 revision content conflict."),
    "k1_identity_conflict": (409, "K1 referral identity conflict."),
    "k1_campaign_conflict": (409, "K1 campaign transition conflict."),
    "k1_unavailable": (503, "K1 receiver is temporarily unavailable."),
}


def error_response(code: ErrorCode) -> JSONResponse:
    status, message = ERRORS[code]
    headers = {"Cache-Control": "no-store"}
    if code == "k1_invalid_auth":
        headers["WWW-Authenticate"] = "Biotact-K1-HMAC"
    return JSONResponse({"detail": {"code": code, "message": message}}, status, headers=headers)


class K1Route(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Retain matched-route classification even when this router is mounted elsewhere.
        with k1_privacy_scope():
            await super().handle(scope, receive, send)

    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                if not settings.employee_referral_k1_enabled:
                    raise K1Error("k1_disabled")
                key = decode_key(settings.employee_referral_k1_key.get_secret_value())
                maker: async_sessionmaker[AsyncSession] = (
                    request.app.state.employee_referral_sessions
                )
                bind = maker.kw.get("bind")
                if settings.database_echo or engine.echo or (bind is not None and bind.echo):
                    raise K1Error("k1_unavailable")
                stamp, signature = auth_headers(request)
                body = await bounded_body(request)
                path: bytes = request.scope.get("raw_path", request.url.path.encode("utf-8"))
                verify_signature(key, path, stamp, signature, body)
                request.state.employee_referral_body = body

                # Present only authenticated bounded bytes to Sentry's inner FastAPI wrapper.
                async def bounded_receive() -> dict[str, object]:
                    return {"type": "http.request", "body": body, "more_body": False}

                response = await handler(Request(request.scope, receive=bounded_receive))
            except K1Error as exc:
                return error_response(exc.code)
            except Exception:
                # Deliberate final redaction boundary. Never log exception/parameters or let
                # shared handlers/SDK capture them. BaseException/cancellation still propagates.
                return error_response("k1_unavailable")
            response.headers["Cache-Control"] = "no-store"
            return response

        return guarded


router = APIRouter(route_class=K1Route)


@router.post(K1_PATH, response_model=Ack)
async def receive(request: Request) -> Ack:
    body: bytes = request.state.employee_referral_body
    maker: async_sessionmaker[AsyncSession] = request.app.state.employee_referral_sessions
    return await receive_snapshot(maker, parse_snapshot(body), body)
