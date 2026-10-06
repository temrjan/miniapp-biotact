"""Readback privacy before CORS/validation, through late send and SDK capture."""

import logging
import re
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
        message = (
            unquote(record.getMessage())
            if record.name.startswith(("httpx", "httpcore", "uvicorn.access"))
            else ""
        )
        private_result = (
            re.search(
                r"/api/v1/(?:admin|internal)/campaigns/[^/?\s\"]+/results(?:[/ ?\"]|$)", message
            )
            is not None
        )
        if (
            _private.get()
            or private_result
            or (record.name.startswith(("httpx", "httpcore")) and PATH in message)
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
    def __init__(self, app: ASGIApp, campaign_prefix: str = "/api/v1/admin/campaigns/") -> None:
        self.app = app
        self.campaign_prefix = campaign_prefix
        install()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        is_results = path.startswith(self.campaign_prefix) and path.rstrip("/").endswith("/results")
        if scope["type"] != "http" or not (
            is_results or path == PATH or path.startswith(PATH + "/")
        ):
            await self.app(scope, receive, send)
            return
        token = _private.set(True)
        if sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
            sentry_sdk.get_isolation_scope().set_tag("biotact_referral_count", "yes")
        if is_results and sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
            sentry_sdk.get_isolation_scope().set_tag("biotact_campaign_results", "yes")
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
                event_scope.set_tag("biotact_referral_count", "yes")
                if is_results:
                    event_scope.set_tag("biotact_campaign_results", "yes")
                if not is_results and scope["method"] not in {"POST", "OPTIONS"}:
                    await error("invalid_request", 405)(scope, receive, private_send)
                    return
                try:
                    await self.app(scope, receive, private_send)
                except Exception:
                    if not started:
                        if is_results:
                            from src.campaigns.router import error_response

                            await error_response("campaign_service_unavailable")(
                                scope, receive, private_send
                            )
                        else:
                            await error("unavailable")(scope, receive, private_send)
        finally:
            _private.reset(token)
