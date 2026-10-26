"""Main FastAPI application."""

# temrjan-2025-12-25
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

import src.models_registry  # noqa: F401 — единый реестр моделей (паритет/create_all)
from src.analytics.router import router as analytics_router
from src.auth.admin_router import router as admin_auth_router
from src.auth.privacy import (
    AuthPrivacyMiddleware,
    filter_auth_telemetry,
    is_private_auth,
    unavailable,
)
from src.banners.router import router as banners_router
from src.banners.router import settings_router
from src.broadcasts.admin_router import router as admin_broadcasts_router
from src.buyer_auth.router import BuyerBoundaryMiddleware, error_response, invalid_input_response
from src.buyer_auth.router import router as buyer_auth_router
from src.buyer_auth.security import BuyerAuthError
from src.buyer_auth.service import BuyerAuthService
from src.buyer_auth.telegram import TelegramAuthService
from src.buyer_auth.telegram_router import bridge_router
from src.buyer_auth.telegram_router import router as telegram_auth_router
from src.campaigns.client import portal_http_client
from src.campaigns.router import router as campaigns_router
from src.categories.router import router as categories_router
from src.config import settings
from src.crm.admin_router import router as crm_admin_router
from src.crm.bot_router import router as crm_bot_router
from src.crm.router import router as crm_router
from src.database import async_session_maker, engine
from src.employee_referrals.count_api import router as count_router
from src.employee_referrals.count_privacy import CountPrivacy
from src.employee_referrals.entry import router as referral_entry_router
from src.employee_referrals.entry_privacy import EntryPrivacyMiddleware
from src.employee_referrals.order_privacy import (
    OrderPrivacyMiddleware,
    is_private_order,
    order_paths,
)
from src.employee_referrals.privacy import K1_PATH, K1PrivacyMiddleware
from src.employee_referrals.return_privacy import ReturnPrivacyMiddleware
from src.employee_referrals.return_router import router as referral_returns_router
from src.employee_referrals.router import router as employee_referrals_router
from src.loyalty.admin_router import router as admin_lottery_router
from src.loyalty.router import router as loyalty_router
from src.loyalty.router import summary_router as loyalty_summary_router
from src.orders.admin_router import router as admin_orders_router
from src.orders.router import router as orders_router
from src.payments.router import router as payments_router
from src.products.admin_router import router as admin_products_router
from src.products.router import router as products_router
from src.uploads.router import router as uploads_router
from src.users.admin_router import router as admin_users_router
from src.users.router import router as users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan events."""
    client = portal_http_client()
    app.state.campaign_http_client = client
    try:
        yield
    finally:
        try:
            await client.aclose()
        finally:
            await engine.dispose()


def create_app() -> FastAPI:
    """Application factory."""
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Initialize Sentry (if DSN provided)
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=0.1,
            before_send=filter_auth_telemetry,
            before_send_transaction=filter_auth_telemetry,
            environment="production" if not settings.debug else "development",
        )
        logger.info("Sentry initialized")

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )
    app.state.campaign_sessions = async_session_maker
    app.state.employee_referral_sessions = async_session_maker
    app.state.buyer_auth = BuyerAuthService(settings, async_session_maker)
    app.state.buyer_telegram_auth = TelegramAuthService(app.state.buyer_auth)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Last added runs outside CORS, inside the SDK-owned request scope. It only
    # classifies K1 telemetry; CORS responses and request handling remain unchanged.
    app.add_middleware(K1PrivacyMiddleware, path=settings.api_v1_prefix + K1_PATH)
    private_order_paths = order_paths(settings.api_v1_prefix)
    app.add_middleware(OrderPrivacyMiddleware, paths=private_order_paths)

    app.add_middleware(EntryPrivacyMiddleware, prefix=settings.api_v1_prefix)
    app.add_middleware(ReturnPrivacyMiddleware, prefix=settings.api_v1_prefix)
    app.add_middleware(CountPrivacy, campaign_prefix=settings.api_v1_prefix + "/admin/campaigns/")
    auth_prefix = settings.api_v1_prefix + "/admin/auth"
    app.add_middleware(AuthPrivacyMiddleware, prefix=auth_prefix)
    buyer_prefix = settings.api_v1_prefix + "/buyer/auth"
    app.add_middleware(BuyerBoundaryMiddleware, prefix=buyer_prefix, config=settings)
    app.add_middleware(AuthPrivacyMiddleware, prefix=buyer_prefix)
    bridge_prefix = settings.api_v1_prefix + "/bot/buyer/telegram"
    app.add_middleware(BuyerBoundaryMiddleware, prefix=bridge_prefix, config=settings)
    app.add_middleware(AuthPrivacyMiddleware, prefix=bridge_prefix)
    app.include_router(buyer_auth_router, prefix=settings.api_v1_prefix)
    app.include_router(telegram_auth_router, prefix=settings.api_v1_prefix)
    app.include_router(bridge_router, prefix=settings.api_v1_prefix)
    app.include_router(count_router)
    app.include_router(referral_returns_router, prefix=settings.api_v1_prefix)
    app.include_router(referral_entry_router, prefix=settings.api_v1_prefix)
    app.include_router(employee_referrals_router, prefix=settings.api_v1_prefix)

    # Include routers
    app.include_router(
        categories_router,
        prefix=f"{settings.api_v1_prefix}/categories",
        tags=["categories"],
    )
    app.include_router(
        products_router,
        prefix=f"{settings.api_v1_prefix}/products",
        tags=["products"],
    )
    app.include_router(
        admin_products_router,
        prefix=f"{settings.api_v1_prefix}/admin/products",
        tags=["admin-products"],
    )

    # Uploads router
    app.include_router(
        uploads_router,
        prefix=f"{settings.api_v1_prefix}/uploads",
        tags=["uploads"],
    )

    # Banners router
    app.include_router(
        banners_router,
        prefix=settings.api_v1_prefix,
        tags=["banners"],
    )

    # Settings router
    app.include_router(
        settings_router,
        prefix=settings.api_v1_prefix,
        tags=["settings"],
    )

    # Users router
    app.include_router(
        users_router,
        prefix=f"{settings.api_v1_prefix}/users",
        tags=["users"],
    )

    # Orders router
    app.include_router(
        orders_router,
        prefix=f"{settings.api_v1_prefix}/orders",
        tags=["orders"],
    )

    # Payments router
    app.include_router(
        payments_router,
        prefix=f"{settings.api_v1_prefix}/payments",
        tags=["payments"],
    )

    # Loyalty (Biotact Club) router — dark behind loyalty_enabled until end of Stage 4
    app.include_router(
        loyalty_router,
        prefix=f"{settings.api_v1_prefix}/qr",
        tags=["loyalty"],
    )
    app.include_router(
        loyalty_summary_router,
        prefix=f"{settings.api_v1_prefix}/loyalty",
        tags=["loyalty"],
    )

    # Admin auth router
    app.include_router(
        admin_auth_router,
        prefix=settings.api_v1_prefix,
        tags=["admin-auth"],
    )

    # Admin Users router
    app.include_router(
        admin_users_router,
        prefix=f"{settings.api_v1_prefix}/admin/users",
        tags=["admin-users"],
    )

    # Admin campaign integration
    app.include_router(
        campaigns_router,
        prefix=f"{settings.api_v1_prefix}/admin/campaigns",
        tags=["admin-campaigns"],
    )

    # Admin Orders router
    app.include_router(
        admin_orders_router,
        prefix=f"{settings.api_v1_prefix}/admin/orders",
        tags=["admin-orders"],
    )

    # Analytics router
    app.include_router(
        admin_lottery_router,
        prefix=f"{settings.api_v1_prefix}/admin/lottery",
        tags=["admin-lottery"],
    )
    app.include_router(
        analytics_router,
        prefix=f"{settings.api_v1_prefix}/analytics",
        tags=["analytics"],
    )

    # CRM router (for frontend)
    app.include_router(
        crm_router,
        prefix=f"{settings.api_v1_prefix}/crm",
        tags=["crm"],
    )

    # CRM Admin router
    app.include_router(
        crm_admin_router,
        prefix=f"{settings.api_v1_prefix}/admin/crm",
        tags=["admin-crm"],
    )

    # CRM Bot router (for AI assistant)
    app.include_router(
        crm_bot_router,
        prefix=f"{settings.api_v1_prefix}/bot",
        tags=["bot-crm"],
    )

    # Admin Broadcasts router
    app.include_router(
        admin_broadcasts_router,
        prefix=f"{settings.api_v1_prefix}/admin/broadcasts",
        tags=["admin-broadcasts"],
    )

    # Global exception handlers
    @app.exception_handler(BuyerAuthError)
    async def buyer_error_handler(_request: Request, exc: BuyerAuthError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def order_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if is_private_auth(request.scope, buyer_prefix) or is_private_auth(
            request.scope, bridge_prefix
        ):
            return await invalid_input_response(request)
        if is_private_auth(request.scope, auth_prefix):
            return JSONResponse(
                status_code=422, content={"detail": "Invalid authentication request"}
            )
        if not is_private_order(request.scope, private_order_paths):
            return await request_validation_exception_handler(request, exc)
        # Pydantic's input/context can contain the raw referral token or its enclosing body.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()
                ]
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        if is_private_auth(request.scope, buyer_prefix) or is_private_auth(
            request.scope, bridge_prefix
        ):
            return error_response(BuyerAuthError(503, "authentication_unavailable"))
        if is_private_auth(request.scope, auth_prefix):
            logger.error("Administrator authentication service failure")
            return unavailable()
        if is_private_order(request.scope, private_order_paths):
            logger.error("Order data conflict | path=%s", request.scope["path"])
        else:
            logger.error("IntegrityError: %s | path=%s", exc.orig, request.url.path)
        return JSONResponse(
            status_code=409,
            content={"detail": "Data conflict: duplicate or constraint violation"},
        )

    @app.exception_handler(OperationalError)
    async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
        if is_private_auth(request.scope, buyer_prefix) or is_private_auth(
            request.scope, bridge_prefix
        ):
            return error_response(BuyerAuthError(503, "authentication_unavailable"))
        if is_private_auth(request.scope, auth_prefix):
            logger.error("Administrator authentication service failure")
            return unavailable()
        if is_private_order(request.scope, private_order_paths):
            logger.error("Order database unavailable | path=%s", request.scope["path"])
        else:
            logger.error("OperationalError: %s | path=%s", exc.orig, request.url.path)
        return JSONResponse(
            status_code=503,
            content={"detail": "Database temporarily unavailable"},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        if is_private_auth(request.scope, buyer_prefix) or is_private_auth(
            request.scope, bridge_prefix
        ):
            return error_response(BuyerAuthError(503, "authentication_unavailable"))
        if is_private_auth(request.scope, auth_prefix):
            logger.error("Administrator authentication service failure")
            return unavailable()
        if is_private_order(request.scope, private_order_paths):
            logger.error("Order request failed | path=%s", request.scope["path"])
        else:
            logger.exception("Unhandled error: %s | path=%s", exc, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint.

    Performs a lightweight database query to ensure the application can
    actually talk to PostgreSQL with the current schema.
    """
    try:
        async with async_session_maker() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")),
                timeout=3.0,
            )
    except Exception as exc:
        logger.exception("Health check failed: database unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc

    return {"status": "healthy", "app": settings.app_name}


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "message": "Biotact API",
        "version": "1.0.0",
        "docs": "/docs" if settings.debug else "Disabled in production",
    }
