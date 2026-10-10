"""Status authorization through the current active-administrator rule."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import verify_admin_jwt
from src.database import get_db


async def authorize_order_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())],
) -> int:
    return await verify_admin_jwt(db, credentials)
