"""Readback privacy before CORS/validation, through late send and SDK capture."""

import logging
from contextvars import ContextVar
from urllib.parse import unquote

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.employee_referrals.count_api import error
from src.employee_referrals.count_wire import PATH

_private: ContextVar[bool] = ContextVar("private_count_readback", default=False)
_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if _private.get() or (
            record.name.startswith(("httpx", "httpcore")) and PATH in unquote(record.getMessage())
        ):
            if record.name == "uvicorn.access":
                status = (
                    record.args[-1]
                    if isinstance(record.args, tuple) and len(record.args) == 5
                    else 503
                )
                record.msg = '%s - "%s %s HTTP/%s" %s'
                record.args = (
                    "private",
                    "PRIVATE",
                    "/private",
                    "1.1",
                    status if type(status) is int else 503,
                )
            else:
                record.msg = "Private count operation"
                record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


class CountPrivacy:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        install()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == PATH or path.startswith(PATH + "/")):
            await self.app(scope, receive, send)
            return
        token = _private.set(True)
        if sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
            sentry_sdk.get_isolation_scope().set_tag("biotact_referral_count", "yes")
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
                        if k.lower() != b"cache-control"
                    ]
                    + [(b"cache-control", b"no-store")],
                }
            await send(message)

        try:
            with sentry_sdk.isolation_scope() as event_scope:
                event_scope.set_tag("biotact_referral_count", "yes")
                if scope["method"] not in {"POST", "OPTIONS"}:
                    await error("invalid_request", 405)(scope, receive, private_send)
                    return
                try:
                    await self.app(scope, receive, private_send)
                except Exception:
                    if not started:
                        await error("unavailable")(scope, receive, private_send)
        finally:
            _private.reset(token)
