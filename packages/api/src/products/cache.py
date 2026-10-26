"""Cache policy for the existing public catalogue GET routes."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException


def catalogue_headers(headers: dict[str, str], *, localized: bool) -> dict[str, str]:
    """Preserve unrelated Vary tokens while adding the language selectors."""
    result = dict(headers)
    result["Cache-Control"] = "no-store"
    if localized:
        vary = next((value for key, value in result.items() if key.lower() == "vary"), "")
        tokens = [token.strip() for token in vary.split(",") if token.strip()]
        for token in ("Accept-Language", "X-Language"):
            if token.lower() not in {value.lower() for value in tokens}:
                tokens.append(token)
        result = {key: value for key, value in result.items() if key.lower() != "vary"}
        result["Vary"] = ", ".join(tokens)
    return result


class CatalogueReadRoute(APIRoute):
    localized = False

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def read(request: Request) -> Response:
            try:
                response = await handler(request)
            except HTTPException as exc:
                if request.method == "GET" and exc.status_code == 404:
                    exc.headers = catalogue_headers(
                        dict(exc.headers or {}), localized=self.localized
                    )
                raise
            if request.method == "GET" and (
                200 <= response.status_code < 300 or response.status_code == 404
            ):
                response.headers.update(
                    catalogue_headers(dict(response.headers), localized=self.localized)
                )
            return response

        return read


class LocalizedCatalogueReadRoute(CatalogueReadRoute):
    localized = True
