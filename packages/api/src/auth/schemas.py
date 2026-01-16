"""Auth Pydantic schemas."""

from typing import Optional

from pydantic import BaseModel


class TelegramUser(BaseModel):
    """Telegram user from initData."""
    
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: Optional[str] = None
    is_premium: Optional[bool] = None
    photo_url: Optional[str] = None


class InitData(BaseModel):
    """Parsed Telegram initData."""
    
    query_id: Optional[str] = None
    user: Optional[TelegramUser] = None
    auth_date: int
    hash: str
