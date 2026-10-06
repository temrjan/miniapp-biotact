"""Privacy for the two order POST boundaries, including validation/late failures."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.employee_referrals.privacy import filter_k1

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint

logger = logging.getLogger(__name__)


def order_paths(prefix: str) -> frozenset[str]:
    return frozenset(
        {
            f"{prefix}/orders",
            f"{prefix}/orders/",
            f"{prefix}/orders/quote",
            f"{prefix}/orders/quote/",
        }
    )


def is_private_order(scope: Scope, paths: frozenset[str]) -> bool:
    return scope["type"] == "http" and scope["method"] == "POST" and scope["path"] in paths


@contextmanager
def order_privacy_scope() -> Iterator[None]:
    # Keep the SDK-owned request tag alive after the route/middleware returns.
    if sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
        sentry_sdk.get_isolation_scope().set_tag("biotact_order_capture", "yes")
    with sentry_sdk.isolation_scope() as event_scope:
        event_scope.set_tag("biotact_order_capture", "yes")
        yield


class OrderPrivacyMiddleware:
    def __init__(self, app: ASGIApp, paths: frozenset[str]) -> None:
        self.app = app
        self.paths = paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # ASGI decoded path/method are independent of the untrusted Host header.
        if is_private_order(scope, self.paths):
            with order_privacy_scope():
                started = False

                async def private_send(message: Message) -> None:
                    nonlocal started
                    if message["type"] == "http.response.start":
                        started = True
                    await send(message)

                try:
                    await self.app(scope, receive, private_send)
                except Exception:
                    # Preserve send failures after response start, tagged for late SDK capture.
                    # Before start, prevent debug traceback bodies from exposing SQL parameters.
                    if started:
                        raise
                    logger.error("Order request failed | path=%s", scope["path"])
                    await JSONResponse(
                        status_code=500, content={"detail": "Internal server error"}
                    )(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def filter_referral_telemetry(event: "Event", hint: "Hint") -> "Event | None":
    if (
        event.get("tags", {}).get("biotact_order_capture") == "yes"
        or event.get("tags", {}).get("biotact_referral_return") == "yes"
        or event.get("tags", {}).get("biotact_referral_paid") == "yes"
        or event.get("tags", {}).get("biotact_referral_entry") == "yes"
        or event.get("tags", {}).get("biotact_referral_count") == "yes"
        or event.get("tags", {}).get("biotact_campaign_results") == "yes"
    ):
        return None
    return filter_k1(event, hint)
