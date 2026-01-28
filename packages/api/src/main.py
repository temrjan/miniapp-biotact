"""Main FastAPI application."""
import logging

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.database import engine

# Import routers
from src.categories.router import router as categories_router
from src.products.router import router as products_router
from src.uploads.router import router as uploads_router
from src.banners.router import router as banners_router, settings_router
from src.users.router import router as users_router
from src.users.admin_router import router as admin_users_router
from src.orders.router import router as orders_router
from src.orders.admin_router import router as admin_orders_router
from src.payments.router import router as payments_router
from src.auth.admin_router import router as admin_auth_router
from src.auth.jwt_router import router as jwt_auth_router
from src.analytics.router import router as analytics_router
from src.crm.admin_router import router as crm_admin_router
from src.crm.bot_router import router as crm_bot_router
from src.crm.router import router as crm_router


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger(__name__)

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

    return app


app = create_app()


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": settings.app_name}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Biotact API",
        "version": "1.0.0",
        "docs": "/docs" if settings.debug else "Disabled in production",
    }
