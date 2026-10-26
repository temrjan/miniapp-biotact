"""Private administrator auth before CORS/validation and through late SDK capture."""

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint

_private: ContextVar[bool] = ContextVar("private_admin_auth", default=False)
_prefixes: set[str] = {"/api/v1/admin/auth", "/api/v1/buyer/auth", "/api/v1/bot/buyer/telegram"}
_installed = False
_buyer_cookies = ("__Host-biotact_buyer", "__Host-biotact_auth")
_buyer_credential_markers = (
    *(cookie.lower() for cookie in _buyer_cookies),
    "x-csrf-token",
    "csrf_token",
    "x-bot-token",
    "api.telegram.org/bot",
)


def private_path(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def is_private_auth(scope: Scope, prefix: str) -> bool:
    return scope["type"] == "http" and private_path(scope.get("path", ""), prefix)


def unavailable() -> JSONResponse:
    return JSONResponse(
        {"detail": "Authentication temporarily unavailable"},
        status_code=503,
        headers={"Cache-Control": "no-store"},
    )


def install() -> None:
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        message = unquote(record.getMessage())
        if _private.get() or any(
            marker in message for marker in (*_prefixes, *_buyer_cookies, "api.telegram.org/bot")
        ):
            if record.name == "uvicorn.access":
                status = (
                    record.args[-1]
                    if isinstance(record.args, tuple) and len(record.args) == 5
                    else 503
                )
                record.msg = '%s - "%s %s HTTP/%s" %s'
                record.args = ("private", "PRIVATE", "/private", "1.1", status)
            else:
                record.msg = "Private administrator authentication"
                record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


def _strip_buyer_credentials(value: object) -> object:
    """Buyer cookies/CSRF also occur in public requests and SDK middleware span reprs."""
    if isinstance(value, dict):
        return {
            key: (
                "[Filtered]"
                if str(key).lower()
                in {
                    "cookie",
                    "set-cookie",
                    *_buyer_credential_markers,
                }
                else _strip_buyer_credentials(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_strip_buyer_credentials(item) for item in value]
    if isinstance(value, str):
        normalized = unquote(value).lower()
        if any(marker in normalized for marker in _buyer_credential_markers):
            return "[Filtered]"
    return value


def filter_auth_telemetry(event: "Event", hint: "Hint") -> "Event | None":
    from src.employee_referrals.order_privacy import filter_referral_telemetry

    url = unquote(str(event.get("request", {}).get("url", "")))
    if event.get("tags", {}).get("biotact_admin_auth") == "yes" or any(
        prefix in url for prefix in _prefixes
    ):
        return None
    # HTTP client breadcrumbs can be created after the private request scope
    # ends and later accompany an unrelated event. Keep that event, remove only
    # private auth breadcrumbs/spans (including their captured headers/body).
    breadcrumbs = event.get("breadcrumbs", {})
    if isinstance(breadcrumbs, dict) and "values" in breadcrumbs:
        breadcrumbs["values"] = [
            item
            for item in breadcrumbs["values"]
            if not any(
                prefix in unquote(str(item)) for prefix in (*_prefixes, "api.telegram.org/bot")
            )
        ]
    spans = event.get("spans")
    if isinstance(spans, list):
        event["spans"] = [
            item
            for item in spans
            if not any(
                prefix in unquote(str(item)) for prefix in (*_prefixes, "api.telegram.org/bot")
            )
        ]
    result = filter_referral_telemetry(event, hint)
    return cast("Event | None", _strip_buyer_credentials(result))


class AuthPrivacyMiddleware:
    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix
        _prefixes.add(prefix)
        install()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_private_auth(scope, self.prefix):
            await self.app(scope, receive, send)
            return
        token = _private.set(True)
        if sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
            sentry_sdk.get_isolation_scope().set_tag("biotact_admin_auth", "yes")
        started = False

        async def private_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = {
                    **message,
                    "headers": [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() not in {b"cache-control", b"etag", b"last-modified"}
                    ]
                    + [(b"cache-control", b"no-store")],
                }
            await send(message)

        try:
            with sentry_sdk.isolation_scope() as event_scope:
                event_scope.set_tag("biotact_admin_auth", "yes")
                if (
                    scope["method"] == "POST"
                    and scope["path"].rstrip("/") == self.prefix + "/login"
                ):
                    body = bytearray()
                    while True:
                        part = await receive()
                        if part["type"] == "http.disconnect":
                            return
                        chunk = part.get("body", b"")
                        if len(body) + len(chunk) > 4096:
                            await JSONResponse(
                                {"detail": "Authentication request too large"}, status_code=413
                            )(scope, receive, private_send)
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
        except Exception:
            if started:
                raise
            if "/buyer/auth" in self.prefix or "/bot/buyer/telegram" in self.prefix:
                response = JSONResponse(
                    {"error": "authentication_unavailable"},
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )
            else:
                response = unavailable()
            await response(scope, receive, private_send)
        finally:
            _private.reset(token)
