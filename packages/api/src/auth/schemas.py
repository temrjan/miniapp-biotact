"""Auth Pydantic schemas."""

from pydantic import BaseModel


class TelegramUser(BaseModel):
    """Telegram user from initData."""

    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool | None = None
    photo_url: str | None = None


class InitData(BaseModel):
    """Parsed Telegram initData."""

    query_id: str | None = None
    user: TelegramUser | None = None
    auth_date: int
    hash: str
