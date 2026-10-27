"""Website guest checkout routes under ``{api_v1_prefix}/guest`` (G1, spec S2 sections 4-6).

The boundary (flag, Origin/Host, JSON, body cap, ``no-store``, CORS stripping) runs before these
handlers in ``BuyerBoundaryMiddleware`` (guest mode); the order is taken only from the purchase
cookie, never from a path, query or body identifier.
"""

import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.buyer_auth.router import peer
from src.buyer_auth.security import SECRET_PATTERN, keyed
from src.buyer_auth.service import BuyerAuthService
from src.database import get_db
from src.guest import service
from src.guest.schemas import GuestOrderRequest, GuestQuoteRequest

router = APIRouter(prefix="/guest", tags=["guest"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


def refusal(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": code, **extra}, status_code=status)


def _auth(request: Request) -> BuyerAuthService:
    value: BuyerAuthService = request.app.state.buyer_auth
    return value


def _secret(request: Request) -> str | None:
    value = request.cookies.get(service.GUEST_COOKIE)
    return value if value is not None and re.fullmatch(SECRET_PATTERN, value) else None


@router.post("/quote")
async def quote(body: GuestQuoteRequest, db: DbSession) -> JSONResponse:
    if service.too_large(body.items):
        return refusal(422, "order_too_large")
    try:
        result = await service.quote(db, body.items)
    except service.GuestRefusal as exc:
        return refusal(exc.status, exc.code, **exc.extra)
    return JSONResponse(result.model_dump(mode="json"))


@router.post("/orders")
async def create_order(request: Request, body: GuestOrderRequest, db: DbSession) -> JSONResponse:
    if service.too_large(body.items):
        return refusal(422, "order_too_large")
    auth = _auth(request)
    purpose, count, window = service.ORDER_PEER_LIMIT
    await auth.account(purpose, peer(request, auth.config), count, window)
    secret = secrets.token_hex(32)
    try:
        result = await service.create_order(db, auth.config, body, secret)
    except service.GuestRefusal as exc:
        return refusal(exc.status, exc.code, **exc.extra)
    if isinstance(result, service.Replayed):
        return JSONResponse({"order_number": result.order_number, "state": result.state})
    response = JSONResponse(result.projection, status_code=201)
    response.set_cookie(
        service.GUEST_COOKIE,
        secret,
        max_age=service.COOKIE_MAX_AGE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/order")
async def read_order(request: Request, db: DbSession) -> JSONResponse:
    secret = _secret(request)
    config = _auth(request).config
    order = (
        None
        if secret is None
        else await service.find_by_access(
            db, keyed(config, "guest-access", secret), datetime.now(UTC)
        )
    )
    if secret is None or order is None:
        return refusal(404, "order_not_found")
    return JSONResponse(service.projection(order, order.items, service.csrf_for(config, secret)))
