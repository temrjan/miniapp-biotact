"""Drop only K1 telemetry; preserve all other Sentry events and transactions."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint

K1_PATH = "/internal/employee-referrals/k1"


@contextmanager
def k1_privacy_scope() -> Iterator[None]:
    # The SDK owns this request scope through early responses, late exception
    # capture and transaction completion. Without its ASGI integration, use only
    # the nested scope so an ordinary caller's scope is never permanently tagged.
    if sentry_sdk.get_client().get_integration(StarletteIntegration) is not None:
        sentry_sdk.get_isolation_scope().set_tag("biotact_k1", "yes")
    with sentry_sdk.isolation_scope() as event_scope:
        event_scope.set_tag("biotact_k1", "yes")
        yield


class K1PrivacyMiddleware:
    """Classify only the configured K1 path before CORS can return early."""

    def __init__(self, app: ASGIApp, path: str) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # ASGI path is decoded by the server; it is independent of Host/URL parsing.
        # All other paths and non-HTTP scopes pass through without scope mutation.
        if scope["type"] == "http" and scope["path"] == self.path:
            with k1_privacy_scope():
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def filter_k1(event: "Event", _hint: "Hint") -> "Event | None":
    """Sentry's event/hint dictionaries are the SDK's unstructured external boundary."""
    # The K1 boundary marks the event scope and enclosing SDK-owned request scope.
    # SDK request URLs include the untrusted Host header and cannot identify a route.
    if event.get("tags", {}).get("biotact_k1") == "yes":
        return None
    return event
