"""Application settings from environment variables."""

from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Bot
    bot_token: SecretStr

    # Database
    database_url: str = "postgresql+asyncpg://localhost/biotact_db"

    # Admin
    admin_ids: list[int] = []

    # Mini App
    webapp_url: str = "https://app.biotact.uz"

    # Biotact Club loyalty — master gate (mirrors the API flag). While off, the bot's
    # qr_<code> deeplink behaves like a normal /start (the program is dark end-to-end).
    # Flipped to true together with the API flag at go-live (Stage 4, PR-4.5).
    loyalty_enabled: bool = False
    employee_referral_entry_enabled: bool = False
    buyer_telegram_auth_enabled: bool = False
    buyer_telegram_api_url: str = "https://app.biotact.uz/api/v1/bot/buyer/telegram"
    # Event QR discount entry (spec event-qr-discount R-01). Switched ON and OFF together with
    # the API flag of the same name; while off, /start ev_<code> behaves as a plain /start.
    event_promotions_enabled: bool = False

    # Debug
    debug: bool = False

    # AskBiotact API
    askbiotact_api_url: str = ""
    askbiotact_api_key: str = ""

    # CRM API
    crm_api_url: str = "https://app.biotact.uz/api/v1/bot"
    crm_api_token: str = ""

    # Redis (for FSM storage)
    redis_url: str = "redis://localhost:6379/0"

    # Notifications
    notification_bot_token: str = ""
    sales_group_chat_id: int = 0

    @model_validator(mode="after")
    def validate_web_login(self) -> "Settings":
        if self.buyer_telegram_auth_enabled:
            origin = urlsplit(self.buyer_telegram_api_url)
            if (
                origin.scheme != "https"
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.query
                or origin.fragment
                or origin.path != "/api/v1/bot/buyer/telegram"
                or not self.bot_token.get_secret_value()
            ):
                raise ValueError("Invalid trusted website-login bridge")
        return self

    @property
    def is_production(self) -> bool:
        return not self.debug


settings = Settings()
