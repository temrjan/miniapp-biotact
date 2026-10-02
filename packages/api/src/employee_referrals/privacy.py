"""Drop only K1 telemetry; preserve all other Sentry events and transactions."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint

K1_PATH = "/internal/employee-referrals/k1"


def filter_k1(event: "Event", _hint: "Hint") -> "Event | None":
    """Sentry's event/hint dictionaries are the SDK's unstructured external boundary."""
    # The route marks its event scope and the enclosing SDK-owned request scope.
    # SDK request URLs include the untrusted Host header and cannot identify a route.
    if event.get("tags", {}).get("biotact_k1") == "yes":
        return None
    return event
