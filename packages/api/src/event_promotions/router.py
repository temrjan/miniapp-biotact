"""Mini App read of the buyer's event discount (spec event-qr-discount S3, R-20, R-21)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.database import get_db
from src.event_promotions import service
from src.event_promotions.schemas import MyPromotion
from src.users.models import User

router = APIRouter()


@router.get("/me", response_model=MyPromotion)
async def my_promotion(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> MyPromotion:
    """The buyer's active grant (the one pricing uses), or ``active: false``; never cached."""
    response.headers["Cache-Control"] = "no-store"
    active = await service.active_promotion(db, user.id)
    if active is None:
        return MyPromotion(active=False)
    await service.mark_opened(db, active.grant_id)
    return MyPromotion(
        active=True, title=active.title, percent=active.percent, ends_at=active.ends_at
    )
