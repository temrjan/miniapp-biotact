"""Explicit admin operations with a route-local, redacted error boundary."""

from collections.abc import Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.types import Receive, Scope, Send

from src.campaigns.client import PortalClient
from src.campaigns.dependencies import authorize_campaign, campaign_client, write_outcome
from src.campaigns.results import CampaignResults
from src.campaigns.schemas import (
    CampaignError,
    CampaignPage,
    CampaignRecord,
    CampaignSync,
    DraftInput,
    EditInput,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    State,
    TransitionInput,
    WriteOutcome,
)

ERRORS: dict[ErrorCode, tuple[int, str]] = {
    "invalid_admin_auth": (401, "Войдите в административную панель заново."),
    "admin_forbidden": (403, "Доступ к кампаниям разрешён только действующему администратору."),
    "invalid_request": (422, "Проверьте формат запроса и обязательные поля."),
    "campaign_not_found": (404, "Кампания не найдена. Обновите список."),
    "campaign_changed": (409, "Кампания изменена другим администратором. Обновите данные."),
    "invalid_campaign_state": (
        409,
        "Действие недоступно в текущем состоянии кампании. Обновите данные.",
    ),
    "immutable_campaign": (
        409,
        "После публикации кампания не редактируется. Завершите её и создайте новую.",
    ),
    "campaign_overlap": (409, "Период пересекается с опубликованной кампанией этого товара."),
    "invalid_campaign_dates": (422, "Проверьте начало и окончание периода кампании."),
    "incomplete_campaign": (
        422,
        "Перед публикацией заполните товар, изображение, описание, условия и даты.",
    ),
    "invalid_image_url": (422, "Используйте изображение из загрузок магазина."),
    "product_not_found": (422, "Товар не найден. Выберите другой товар."),
    "product_inactive": (422, "Товар отключён в магазине."),
    "product_not_purchasable": (422, "Этот товар недоступен для покупки."),
    "product_out_of_stock": (422, "Товар отмечен как отсутствующий в наличии."),
    "shop_unavailable": (503, "Не удалось проверить товар в магазине. Повторите позже."),
    "service_unavailable": (503, "Сервис кампаний временно недоступен. Повторите позже."),
    "campaign_service_unavailable": (
        503,
        "Сервис кампаний недоступен. Обратитесь к ответственному за систему.",
    ),
    "campaign_result_unknown": (
        503,
        "Не удалось подтвердить результат операции. Обновите данные кампаний перед повтором.",
    ),
}


def error_response(code: ErrorCode) -> JSONResponse:
    status, message = ERRORS[code]
    headers = {"Cache-Control": "no-store"}
    if code == "invalid_admin_auth":
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(detail=ErrorDetail(code=code, message=message)).model_dump(),
        headers=headers,
    )


class CampaignRoute(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Starlette rejects methods before the authenticated endpoint wrapper runs.
        if self.methods and scope["method"] not in self.methods:
            raise HTTPException(
                status_code=405,
                headers={"Allow": ", ".join(sorted(self.methods)), "Cache-Control": "no-store"},
            )
        await super().handle(scope, receive, send)

    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            outcome = WriteOutcome()
            request.state.campaign_write_outcome = outcome
            try:
                await authorize_campaign(request)
                response = await handler(request)
            except CampaignError as exc:
                return error_response(exc.code)
            except RequestValidationError:
                return error_response("invalid_request")
            except HTTPException as exc:
                if exc.status_code in (400, 422) and not outcome.started:
                    return error_response("invalid_request")
                return error_response(
                    "campaign_result_unknown" if outcome.started else "campaign_service_unavailable"
                )
            except Exception:
                # Final campaign-only boundary: do not hand private exceptions to shared loggers.
                # Cancellation (BaseException) still propagates; mutations are never retried.
                return error_response(
                    "campaign_result_unknown" if outcome.started else "campaign_service_unavailable"
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return guarded


router = APIRouter(
    route_class=CampaignRoute,
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 503)},
)
Client = Annotated[PortalClient, Depends(campaign_client)]
Outcome = Annotated[WriteOutcome, Depends(write_outcome)]
CampaignId = Annotated[int, Path(gt=0)]


@router.get("", response_model=CampaignPage)
async def listing(
    client: Client,
    state: State | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CampaignPage:
    return await client.listing(state, page, size)


@router.get("/{campaign_id}", response_model=CampaignRecord)
async def detail(campaign_id: CampaignId, client: Client) -> CampaignRecord:
    return await client.detail(campaign_id)


@router.post("", response_model=CampaignRecord, status_code=201)
async def create(body: DraftInput, client: Client, outcome: Outcome) -> CampaignRecord:
    return await client.create(body, outcome)


@router.patch("/{campaign_id}", response_model=CampaignRecord)
async def edit(
    campaign_id: CampaignId, body: EditInput, client: Client, outcome: Outcome
) -> CampaignRecord:
    return await client.edit(campaign_id, body, outcome)


@router.post("/{campaign_id}/publish", response_model=CampaignRecord)
async def publish(
    campaign_id: CampaignId, body: TransitionInput, client: Client, outcome: Outcome
) -> CampaignRecord:
    return await client.publish(campaign_id, body, outcome)


@router.post("/{campaign_id}/end", response_model=CampaignRecord)
async def end(
    campaign_id: CampaignId, body: TransitionInput, client: Client, outcome: Outcome
) -> CampaignRecord:
    return await client.end(campaign_id, body, outcome)


@router.get("/{campaign_id}/k1-sync", response_model=CampaignSync)
async def sync(campaign_id: CampaignId, client: Client) -> CampaignSync:
    return await client.sync(campaign_id)


@router.get("/{campaign_id}/results", response_model=CampaignResults)
async def results(
    request: Request,
    campaign_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    client: Client,
) -> CampaignResults:
    if request.query_params:
        raise CampaignError("invalid_request")
    async for chunk in request.stream():
        if chunk:
            raise CampaignError("invalid_request")
    return await client.results(campaign_id)
