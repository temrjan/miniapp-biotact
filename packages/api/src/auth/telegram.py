"""Telegram initData validation."""

import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import parse_qs, unquote

from src.auth.schemas import InitData, TelegramUser


class InitDataValidationError(ValueError):
    """Raised when initData validation fails."""

    pass


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 86400,
) -> InitData:
    """
    Validate initData from Telegram Mini App.

    Args:
        init_data: Raw initData string from X-Init-Data header
        bot_token: Telegram bot token for HMAC validation
        max_age_seconds: Maximum age of initData (default 24 hours)

    Returns:
        Parsed and validated InitData

    Raises:
        InitDataValidationError: If validation fails
    """
    if not init_data:
        raise InitDataValidationError("Empty init data")

    # Parse query string
    parsed = parse_qs(init_data, keep_blank_values=True)

    # Extract hash
    received_hash = parsed.get("hash", [None])[0]
    if not received_hash:
        raise InitDataValidationError("Missing hash")

    # Build data check string (sorted, without hash and signature).
    # Telegram excludes BOTH `hash` and the newer `signature` field from the
    # data-check-string; including `signature` breaks HMAC for valid initData.
    # https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    data_check_arr = []
    for key in sorted(parsed.keys()):
        if key not in ("hash", "signature"):
            value = parsed[key][0]
            data_check_arr.append(f"{key}={value}")

    data_check_string = "\n".join(data_check_arr)

    # Create secret key: HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        hashlib.sha256,
    ).digest()

    # Calculate hash: HMAC-SHA256(secret_key, data_check_string)
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    # Compare hashes (timing-safe)
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise InitDataValidationError("Invalid hash")

    # Check auth_date
    auth_date_str = parsed.get("auth_date", ["0"])[0]
    try:
        auth_date = int(auth_date_str)
    except ValueError as exc:
        raise InitDataValidationError("Invalid auth_date") from exc

    current_time = int(datetime.now().timestamp())
    if current_time - auth_date > max_age_seconds:
        raise InitDataValidationError("Init data expired")

    # Parse user
    user_str = parsed.get("user", [None])[0]
    user = None
    if user_str:
        try:
            user_data = json.loads(unquote(user_str))
            user = TelegramUser(**user_data)
        except (json.JSONDecodeError, ValueError) as e:
            raise InitDataValidationError(f"Invalid user data: {e}") from e

    return InitData(
        query_id=parsed.get("query_id", [None])[0],
        user=user,
        auth_date=auth_date,
        hash=received_hash,
    )
