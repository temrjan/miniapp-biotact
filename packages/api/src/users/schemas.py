"""User Pydantic schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class AddressSchema(BaseModel):
    """Delivery address schema."""

    street: str | None = None
    house: str | None = None
    apartment: str | None = None
    entrance: str | None = None
    floor: str | None = None
    comment: str | None = None
    lat: float | None = None
    lng: float | None = None


class PhoneUpdate(BaseModel):
    """Schema for updating phone."""

    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = v.replace(" ", "").replace("-", "")
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        if len(cleaned) < 10 or len(cleaned) > 15:
            raise ValueError("Phone must be 10-15 digits")
        return cleaned


class AddressUpdate(BaseModel):
    """Schema for updating address."""

    street: str
    house: str
    apartment: str | None = None
    entrance: str | None = None
    floor: str | None = None
    comment: str | None = None
    lat: float | None = None
    lng: float | None = None


class LanguageUpdate(BaseModel):
    """Schema for updating language."""

    language_code: Literal["ru", "uz"]


class TelegramUserCreate(BaseModel):
    """Schema for creating user from Telegram."""

    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str = "ru"

    @field_validator("language_code")
    @classmethod
    def validate_language(cls, v: str) -> str:
        return v if v in ("ru", "uz") else "ru"


class UserResponse(BaseModel):
    """User response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    language_code: str
    is_active: bool
    created_at: datetime


class UserProfileResponse(UserResponse):
    """User profile with address."""

    address: AddressSchema | None = None

    @classmethod
    def from_user(cls, user) -> "UserProfileResponse":
        """Create from User model with address mapping."""
        address = None
        if user.address_street:
            address = AddressSchema(
                street=user.address_street,
                house=user.address_house,
                apartment=user.address_apartment,
                entrance=user.address_entrance,
                floor=user.address_floor,
                comment=user.address_comment,
                lat=user.address_lat,
                lng=user.address_lng,
            )

        return cls(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            phone=user.phone,
            language_code=user.language_code,
            is_active=user.is_active,
            created_at=user.created_at,
            address=address,
        )
