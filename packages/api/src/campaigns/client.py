"""Fixed portal operations, bounded transport and conservative write outcomes."""

import asyncio
import json
import logging
from contextvars import ContextVar
from typing import TypeVar
from urllib.parse import urlsplit

import httpx

from src.campaigns.schemas import (
    BoundaryModel,
    CampaignError,
    CampaignPage,
    CampaignRecord,
    DraftInput,
    EditInput,
    ErrorCode,
    State,
    TransitionInput,
    WriteOutcome,
)
from src.config import Settings

# Ratified operational default in V3 specification revision 2.
OPERATION_TIMEOUT = 10.0
PORTAL_PATH = "/api/v1/internal/campaigns"
_private_http: ContextVar[bool] = ContextVar("campaign_private_http", default=False)


class _CampaignHTTPFilter(logging.Filter):
    def filter(self, _record: logging.LogRecord) -> bool:
        # HTTPcore DEBUG includes raw response headers and exception representations.
        # Suppress only this request context; unrelated HTTP logging is unchanged.
        return not _private_http.get()


_http_filter = _CampaignHTTPFilter()
for _logger_name in (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(_logger_name).addFilter(_http_filter)


def portal_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=True,
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(OPERATION_TIMEOUT),
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
    )


def valid_portal_origin(origin: str) -> bool:
    if (
        origin != origin.strip()
        or "\\" in origin
        or any(character.isspace() for character in origin)
    ):
        return False
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        url = httpx.URL(origin)
    except (ValueError, httpx.InvalidURL):
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.netloc.endswith(":")
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and origin == f"https://{parsed.netloc}"
        and (port is None or 1 <= port <= 65535)
        and url.host
    )


_REFUSALS: dict[tuple[int, str], ErrorCode] = {
    (401, "invalid_service_auth"): "campaign_service_unavailable",
    (404, "campaign_not_found"): "campaign_not_found",
    (409, "campaign_changed"): "campaign_changed",
    (409, "invalid_campaign_state"): "invalid_campaign_state",
    (409, "immutable_campaign"): "immutable_campaign",
    (409, "campaign_overlap"): "campaign_overlap",
    (422, "invalid_request"): "invalid_request",
    (422, "invalid_campaign_dates"): "invalid_campaign_dates",
    (422, "incomplete_campaign"): "incomplete_campaign",
    (422, "invalid_image_url"): "invalid_image_url",
    (422, "product_not_found"): "product_not_found",
    (422, "product_inactive"): "product_inactive",
    (422, "product_not_purchasable"): "product_not_purchasable",
    (422, "product_out_of_stock"): "product_out_of_stock",
    (503, "shop_unavailable"): "shop_unavailable",
}
Reply = TypeVar("Reply", bound=BoundaryModel)


def _decode(
    response: httpx.Response,
    model: type[Reply],
    success: int,
    campaign_id: int | None,
    write: bool,
) -> Reply:
    if response.status_code == success:
        result = model.model_validate_json(response.content)
        if (
            isinstance(result, CampaignRecord)
            and campaign_id is not None
            and result.id != campaign_id
        ):
            raise ValueError("Unexpected campaign identity")
        return result
    data = json.loads(response.content)
    if isinstance(data, dict) and set(data) == {"detail"}:
        detail = data["detail"]
        if isinstance(detail, dict) and set(detail) == {"code"} and isinstance(detail["code"], str):
            pair = (response.status_code, detail["code"])
            if pair == (503, "service_unavailable"):
                raise CampaignError("campaign_result_unknown" if write else "service_unavailable")
            refusal = _REFUSALS.get(pair)
            if refusal is not None:
                raise CampaignError(refusal)
    raise ValueError("Unconfirmed portal response")


class PortalClient:
    def __init__(self, http: httpx.AsyncClient, config: Settings) -> None:
        self.http = http
        self.config = config

    async def _send(
        self,
        method: str,
        suffix: str,
        model: type[Reply],
        *,
        success: int = 200,
        campaign_id: int | None = None,
        params: dict[str, str | int] | None = None,
        body: BoundaryModel | None = None,
        outcome: WriteOutcome | None = None,
    ) -> Reply:
        origin = self.config.campaign_portal_origin
        token = self.config.campaign_service_token.get_secret_value()
        if not valid_portal_origin(origin) or not token.strip():
            raise CampaignError("campaign_service_unavailable")
        payload = (
            body.model_dump(mode="json", exclude_unset=isinstance(body, EditInput))
            if body
            else None
        )
        context = _private_http.set(True)
        try:
            async with asyncio.timeout(OPERATION_TIMEOUT):
                if outcome is not None:
                    outcome.started = True
                response = await self.http.request(
                    method,
                    origin + PORTAL_PATH + suffix,
                    params=params,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=False,
                    timeout=OPERATION_TIMEOUT,
                )
                return await asyncio.to_thread(
                    _decode, response, model, success, campaign_id, outcome is not None
                )
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            raise CampaignError(
                "campaign_result_unknown" if outcome is not None else "campaign_service_unavailable"
            ) from exc
        finally:
            _private_http.reset(context)

    async def listing(self, state: State | None, page: int, size: int) -> CampaignPage:
        params: dict[str, str | int] = {"page": page, "size": size}
        if state is not None:
            params["state"] = state
        return await self._send("GET", "", CampaignPage, params=params)

    async def detail(self, campaign_id: int) -> CampaignRecord:
        return await self._send("GET", f"/{campaign_id}", CampaignRecord, campaign_id=campaign_id)

    async def create(self, body: DraftInput, outcome: WriteOutcome) -> CampaignRecord:
        return await self._send("POST", "", CampaignRecord, success=201, body=body, outcome=outcome)

    async def edit(
        self, campaign_id: int, body: EditInput, outcome: WriteOutcome
    ) -> CampaignRecord:
        return await self._send(
            "PATCH",
            f"/{campaign_id}",
            CampaignRecord,
            campaign_id=campaign_id,
            body=body,
            outcome=outcome,
        )

    async def publish(
        self, campaign_id: int, body: TransitionInput, outcome: WriteOutcome
    ) -> CampaignRecord:
        return await self._send(
            "POST",
            f"/{campaign_id}/publish",
            CampaignRecord,
            campaign_id=campaign_id,
            body=body,
            outcome=outcome,
        )

    async def end(
        self, campaign_id: int, body: TransitionInput, outcome: WriteOutcome
    ) -> CampaignRecord:
        return await self._send(
            "POST",
            f"/{campaign_id}/end",
            CampaignRecord,
            campaign_id=campaign_id,
            body=body,
            outcome=outcome,
        )
