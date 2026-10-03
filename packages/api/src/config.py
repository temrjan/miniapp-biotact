"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "Biotact API"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Feature flags
    # Biotact Club loyalty: gates loyalty routers/UI; stays dark on prod until ready.
    loyalty_enabled: bool = False
    employee_referral_k1_enabled: bool = False
    employee_referral_k1_key: SecretStr = SecretStr("")
    employee_referral_capture_enabled: bool = False

    # Database
    database_url: str
    database_echo: bool = False

    # Auth
    secret_key: SecretStr
    access_token_expire_minutes: int = 1440
    refresh_token_expire_days: int = 30
    algorithm: str = "HS256"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    # Telegram
    telegram_bot_token: str = ""
    telegram_notification_bot_token: str = ""
    telegram_chat_id: str = ""

    # Bot API (for AI assistant integration)
    bot_api_token: SecretStr = SecretStr("")

    # Server-only campaign integration; checked only at the campaign boundary.
    campaign_portal_origin: str = ""
    campaign_service_token: SecretStr = SecretStr("")

    # Multicard Payment Gateway
    multicard_application_id: str = ""
    multicard_secret: SecretStr = SecretStr("")
    multicard_store_id: str = ""
    multicard_base_url: str = "https://mesh.multicard.uz"

    # Email (Gmail SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: str = ""
    smtp_from_name: str = "Biotact Admin"
    # JWT — the admin token is signed/verified with `secret_key` + `algorithm` above.
    # `jwt_secret`/`jwt_algorithm` served the self-registration circuit removed in PR-S; leftover
    # JWT_SECRET/JWT_ALGORITHM in a deployed .env are harmless (`extra="ignore"` above).
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days
    # Admin
    admin_url: str = "https://admin.biotact.uz"
    admin_password: SecretStr = SecretStr("")
    admin_telegram_id: int = 0

    # Sentry (optional - leave empty to disable)
    sentry_dsn: str = ""


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


settings = get_settings()
