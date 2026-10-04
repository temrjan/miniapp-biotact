"""Resolve classification before CORS/dependencies, including late SDK failures."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.employee_referrals.entry import ENTRY_PATH, entry_error

_installed = False

private_entry: ContextVar[bool] = ContextVar("private_referral_entry", default=False)


def install_entry_log_redaction() -> None:
    """Sanitize at record creation, before handlers/SDK see exception arguments."""
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if private_entry.get():
            record.msg = "Referral entry diagnostic"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    _installed = True
    logging.setLogRecordFactory(record_factory)


@contextmanager
def entry_privacy_scope() -> Iterator[None]:
    token = private_entry.set(True)
    if sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
        sentry_sdk.get_isolation_scope().set_tag("biotact_referral_entry", "yes")
    try:
        with sentry_sdk.isolation_scope() as event_scope:
            event_scope.set_tag("biotact_referral_entry", "yes")
            yield
    finally:
        private_entry.reset(token)


class EntryPrivacyMiddleware:
    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.paths = {prefix + ENTRY_PATH, prefix + ENTRY_PATH + "/"}
        install_entry_log_redaction()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return
        with entry_privacy_scope():
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
                    raise
                await entry_error("unavailable")(scope, receive, private_send)
