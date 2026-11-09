"""Application configuration using Pydantic Settings."""

import re
from functools import lru_cache
from ipaddress import ip_network
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
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
    # Event QR discount (event-qr-discount R-01); switch ON and OFF together with the bot's.
    event_promotions_enabled: bool = False
    employee_referral_k1_enabled: bool = False
    employee_referral_k1_key: SecretStr = SecretStr("")
    employee_referral_capture_enabled: bool = False
    employee_referral_entry_enabled: bool = False
    buyer_sessions_enabled: bool = False
    buyer_email_auth_enabled: bool = False
    buyer_telegram_auth_enabled: bool = False
    buyer_telegram_bot_username: str = ""
    buyer_protection_key: SecretStr = SecretStr("")
    buyer_public_origin: str = "https://biotact.uz"
    buyer_trusted_proxy_peers: list[str] = []
    # Website guest checkout (G1); reuses the buyer origin, protection key and proxy peers.
    guest_checkout_enabled: bool = False
    payment_callback_base_url: str = ""
    guest_terms_version: str = ""
    guest_privacy_version: str = ""
    # Delivery config is validated lazily by the separate worker, independently per lane.
    employee_referral_k2_sender_enabled: str = "false"
    employee_referral_k2_portal_origin: str = ""
    employee_referral_k2_key: SecretStr = SecretStr("")
    employee_referral_notices_enabled: str = "false"

    employee_referral_count_readback_enabled: str = "false"
    employee_referral_count_readback_key: SecretStr = SecretStr("")

    # Consultant stays disabled until operator activation; it has no admission limits.
    consultant_provider_key: SecretStr = SecretStr("")
    consultant_protection_key: SecretStr = SecretStr("")
    consultant_origins: list[str] = ["https://biotact.uz"]
    # Graf (deterministic product offer through Jev); empty means Graf is off.
    consultant_graf_key: SecretStr = SecretStr("")
    # Luna-first hybrid while Graf is on; false answers hybrid turns with the failure
    # response (named-product questions still work).
    consultant_hybrid: bool = True
    # The consultant journal (spec.opyt r2 JN-04, JG-01); read at startup, off by default.
    consultant_journal: bool = False

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

    @model_validator(mode="after")
    def validate_buyer_configuration(self) -> "Settings":
        if (
            self.buyer_email_auth_enabled or self.buyer_telegram_auth_enabled
        ) and not self.buyer_sessions_enabled:
            raise ValueError("Buyer email entry requires buyer sessions")
        if self.buyer_sessions_enabled:
            if not self._protection_ready() or not _exact_https_origin(self.buyer_public_origin):
                raise ValueError("Invalid private buyer configuration")
            for peer in self.buyer_trusted_proxy_peers:
                ip_network(peer, strict=True)
        if self.guest_checkout_enabled:
            if (
                not self._protection_ready()
                or not _exact_https_origin(self.buyer_public_origin)
                or not _exact_https_origin(self.payment_callback_base_url)
                or not _edition(self.guest_terms_version)
                or not _edition(self.guest_privacy_version)
            ):
                raise ValueError("Invalid guest checkout configuration")
            for peer in self.buyer_trusted_proxy_peers:
                ip_network(peer, strict=True)
        if self.buyer_telegram_auth_enabled and (
            not re.fullmatch(r"[A-Za-z0-9_]{5,32}", self.buyer_telegram_bot_username)
            or not self.buyer_telegram_bot_username.lower().endswith("bot")
            or not self.telegram_bot_token
            or not self.telegram_bot_token.isascii()
            or any(ord(c) < 33 or ord(c) == 127 for c in self.telegram_bot_token)
        ):
            raise ValueError("Invalid buyer Telegram configuration")
        if self.buyer_email_auth_enabled and (
            self.smtp_host != "smtp.gmail.com"
            or self.smtp_port != 587
            or not self.smtp_user
            or not self.smtp_password.get_secret_value()
            or not self.smtp_from_email
            or any(ord(c) < 32 or ord(c) == 127 for c in self.smtp_user + self.smtp_from_email)
        ):
            raise ValueError("Invalid buyer mail configuration")
        return self

    def _protection_ready(self) -> bool:
        key = self.buyer_protection_key.get_secret_value()
        return (
            len(key.encode("utf-8")) >= 32
            and key != self.secret_key.get_secret_value()
            and not self.database_echo
        )


def _exact_https_origin(value: str) -> bool:
    origin = urlsplit(value)
    return not (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username is not None
        or origin.password is not None
        or origin.port == 0
        or origin.path
        or origin.query
        or origin.fragment
        or value != f"https://{origin.netloc}"
    )


def _edition(value: str) -> bool:
    """A published document edition identifier: 1-64 printable ASCII characters."""
    return 1 <= len(value) <= 64 and all(" " <= c <= "~" for c in value)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


settings = get_settings()
