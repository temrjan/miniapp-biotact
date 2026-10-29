"""Anonymous POST boundary: bounded bodies, safe errors, no credentials or retries."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.types import ASGIApp, Receive, Scope, Send

from src.consultant.context import Tokens
from src.consultant.contracts import Cancel, Fault, Refresh, Turn
from src.consultant.control import Controls
from src.consultant.graf import Graf, Jev
from src.consultant.knowledge import Knowledge
from src.consultant.provider import Provider
from src.consultant.service import Consultant

router = APIRouter(prefix="/consultant")


def error(exc: Fault, request_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        {"request_id": request_id, "code": exc.code, "retryable": exc.retryable},
        status_code=exc.status,
        headers={
            "Cache-Control": "no-store",
            **({"Retry-After": "60"} if exc.status == 429 else {}),
        },
    )


class Boundary:
    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app, self.prefix = app, prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        # Credentials and forwarding headers are not part of this service's authority.
        scope = {
            **scope,
            "headers": [
                (k, v)
                for k, v in scope.get("headers", [])
                if k.lower() not in {b"cookie", b"authorization"}
            ],
        }
        try:
            await self.app(scope, receive, send)
        except Exception:
            await error(Fault("knowledge_unavailable"))(scope, receive, send)


async def body(request: Request) -> bytes:
    origin = request.headers.get("origin")
    if origin is not None and origin not in request.app.state.consultant_origins:
        raise Fault("invalid_input", 400)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise Fault("invalid_input", 422)
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > 65536:
            raise Fault("invalid_input", 413)
    return bytes(result)


async def dispatch(request: Request, kind: str) -> Response:
    rid: str | None = None
    try:
        raw = await body(request)
        try:
            value = json.loads(raw)
            if isinstance(value, dict) and isinstance(value.get("request_id"), str):
                rid = str(UUID(value["request_id"]))
        except (ValueError, TypeError):
            pass
        service: Consultant = request.app.state.consultant
        if kind == "turn":
            parsed = Turn.model_validate_json(raw)
            rid = str(parsed.request_id)
            result = await service.turn(parsed)
        elif kind == "cancel":
            cancel = Cancel.model_validate_json(raw)
            rid = str(cancel.request_id)
            await service.controls.register(cancel.request_id, cancel.tab_id, cancel=True)
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        else:
            refresh = Refresh.model_validate_json(raw)
            evidence = await service.knowledge.snapshot(refresh.language)
            from datetime import UTC, datetime

            result = {
                "products": [
                    evidence.products[i] for i in refresh.product_ids if i in evidence.products
                ],
                "unavailable_ids": [i for i in refresh.product_ids if i not in evidence.products],
                "checked_at": datetime.now(UTC).isoformat(),
            }
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ValidationError as exc:
        if any(
            item["msg"] == "Value error, context_invalid"
            for item in exc.errors(include_input=False)
        ):
            return error(Fault("context_invalid", 400), rid)
        invalid_language = any(
            item["loc"] == ("language",) for item in exc.errors(include_input=False)
        )
        return error(Fault("invalid_input", 400 if invalid_language else 422), rid)
    except Fault as exc:
        return error(exc, rid)
    except Exception:
        return error(Fault("knowledge_unavailable"), rid)


@router.post("/turns")
async def turns(request: Request) -> Response:
    return await dispatch(request, "turn")


@router.post("/cancel", status_code=204)
async def cancel(request: Request) -> Response:
    return await dispatch(request, "cancel")


@router.post("/products/refresh")
async def refresh(request: Request) -> Response:
    return await dispatch(request, "refresh")


async def cleaner(controls: Controls) -> None:
    while True:
        try:
            await controls.cleanup()
        except Exception:
            # A missing pre-migration schema must not break ordinary Shop startup.
            logging.getLogger(__name__).warning("Consultant cleanup unavailable")
        await asyncio.sleep(300)


@asynccontextmanager
async def runtime(app: Any, config: Any, sessions: Any) -> AsyncIterator[None]:
    provider_key = config.consultant_provider_key.get_secret_value()
    controls = Controls(
        sessions, config.consultant_protection_key.get_secret_value(), bool(provider_key)
    )
    graf_key = config.consultant_graf_key.get_secret_value()
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
        app.state.consultant = Consultant(
            controls,
            Knowledge(sessions),
            Provider(client, provider_key),
            Tokens(config.consultant_protection_key.get_secret_value()),
            Graf(Jev(client, graf_key)) if graf_key else None,
        )
        app.state.consultant_origins = config.consultant_origins
        cleanup = asyncio.create_task(cleaner(controls))
        try:
            yield
        finally:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
