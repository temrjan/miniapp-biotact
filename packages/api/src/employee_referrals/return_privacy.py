"""Resolve classification before CORS/dependencies, including late SDK failures."""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import unquote

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.employee_referrals.return_router import private_return_path, return_error

_installed = False

private_return: ContextVar[bool] = ContextVar("private_referral_return", default=False)


def install_return_log_redaction() -> None:
    """Sanitize at record creation, before handlers/SDK see exception arguments."""
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if private_return.get() or (
            record.name.startswith(("httpx", "httpcore"))
            and re.search(
                r"/admin/orders/[^/]+/referral-returns(?:[/ ?\"]|$)", unquote(record.getMessage())
            )
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
                record.msg = "Referral return diagnostic"
                record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    _installed = True
    logging.setLogRecordFactory(record_factory)


@contextmanager
def return_privacy_scope() -> Iterator[None]:
    install_return_log_redaction()
    token = private_return.set(True)
    if sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
        sentry_sdk.get_isolation_scope().set_tag("biotact_referral_return", "yes")
    try:
        with sentry_sdk.isolation_scope() as event_scope:
            event_scope.set_tag("biotact_referral_return", "yes")
            yield
    finally:
        private_return.reset(token)


class ReturnPrivacyMiddleware:
    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix
        install_return_log_redaction()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not private_return_path(scope["path"], self.prefix):
            await self.app(scope, receive, send)
            return
        with return_privacy_scope():
            if scope["method"] not in {"GET", "POST", "OPTIONS"}:
                await return_error("invalid_request", 405)(scope, receive, send)
                return
            started = False

            async def private_send(message: Message) -> None:
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                    message["headers"] = [
                        (key, value)
                        for key, value in message["headers"]
                        if key.lower() != b"cache-control"
                    ] + [(b"cache-control", b"no-store")]
                await send(message)

            try:
                await self.app(scope, receive, private_send)
            except Exception:
                if started:
                    return
                await return_error("unavailable")(scope, receive, private_send)
