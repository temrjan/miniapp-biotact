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

    # AI / OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = ""
    openai_model: str = "gpt-4o-mini"

    @property
    def is_production(self) -> bool:
        return not self.debug


settings = Settings()
