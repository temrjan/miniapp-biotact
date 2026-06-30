"""Main FastAPI application."""

# temrjan-2025-12-25
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

import src.models_registry  # noqa: F401 — единый реестр моделей (паритет/create_all)
from src.analytics.router import router as analytics_router
from src.auth.admin_router import router as admin_auth_router
from src.auth.jwt_router import router as jwt_auth_router
from src.banners.router import router as banners_router
from src.banners.router import settings_router
from src.broadcasts.admin_router import router as admin_broadcasts_router
from src.categories.router import router as categories_router
from src.config import settings
from src.crm.admin_router import router as crm_admin_router
from src.crm.bot_router import router as crm_bot_router
from src.crm.router import router as crm_router
from src.database import async_session_maker, engine
from src.loyalty.router import router as loyalty_router
from src.loyalty.router import summary_router as loyalty_summary_router
from src.orders.admin_router import router as admin_orders_router
from src.orders.router import router as orders_router
from src.payments.router import router as payments_router
from src.products.router import router as products_router
from src.uploads.router import router as uploads_router
from src.users.admin_router import router as admin_users_router
from src.users.router import router as users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan events."""
    # Startup
    yield
    # Shutdown
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

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    # JWT auth router
    app.include_router(
        jwt_auth_router,
        prefix=settings.api_v1_prefix,
        tags=["jwt-auth"],
    )

    # Admin Users router
    app.include_router(
        admin_users_router,
        prefix=f"{settings.api_v1_prefix}/admin/users",
        tags=["admin-users"],
    )

    # Admin Orders router
    app.include_router(
        admin_orders_router,
        prefix=f"{settings.api_v1_prefix}/admin/orders",
        tags=["admin-orders"],
    )

    # Analytics router
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
    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.error("IntegrityError: %s | path=%s", exc.orig, request.url.path)
        return JSONResponse(
            status_code=409,
            content={"detail": "Data conflict: duplicate or constraint violation"},
        )

    @app.exception_handler(OperationalError)
    async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("OperationalError: %s | path=%s", exc.orig, request.url.path)
        return JSONResponse(
            status_code=503,
            content={"detail": "Database temporarily unavailable"},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
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
