"""Application settings from environment variables."""

from pydantic import SecretStr
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

    @property
    def is_production(self) -> bool:
        return not self.debug


settings = Settings()
