"""Drop only K1 telemetry; preserve all other Sentry events and transactions."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint
from urllib.parse import urlsplit

from src.config import settings

K1_PATH = "/internal/employee-referrals/k1"


def filter_k1(event: "Event", _hint: "Hint") -> "Event | None":
    """Sentry's event/hint dictionaries are the SDK's unstructured external boundary."""
    url = event.get("request", {}).get("url", "")
    path = urlsplit(url).path if isinstance(url, str) else ""
    if path == settings.api_v1_prefix + K1_PATH or event.get("tags", {}).get("biotact_k1") == "yes":
        return None
    return event
