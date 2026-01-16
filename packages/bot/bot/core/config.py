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

    @property
    def is_production(self) -> bool:
        return not self.debug


settings = Settings()
