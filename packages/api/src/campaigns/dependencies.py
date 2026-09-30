"""Campaign-only current-account authorization and resource access."""

from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.auth.dependencies import require_admin
from src.campaigns.client import PortalClient
from src.campaigns.schemas import CampaignError, WriteOutcome
from src.config import settings

_bearer = HTTPBearer(auto_error=False)


def campaign_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.campaign_sessions
    return sessions


async def authorize_campaign(request: Request) -> None:
    credentials = await _bearer(request)
    if credentials is None:
        raise CampaignError("invalid_admin_auth")
    try:
        async with campaign_sessions(request)() as session:
            admin = await require_admin(db=session, credentials=credentials)
            if not admin.is_active:
                raise CampaignError("admin_forbidden")
    except HTTPException as exc:
        raise CampaignError(
            "invalid_admin_auth" if exc.status_code == 401 else "admin_forbidden"
        ) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise CampaignError("invalid_admin_auth") from exc


def campaign_client(request: Request) -> PortalClient:
    return PortalClient(request.app.state.campaign_http_client, settings)


def write_outcome(request: Request) -> WriteOutcome:
    outcome: WriteOutcome = request.state.campaign_write_outcome
    return outcome
