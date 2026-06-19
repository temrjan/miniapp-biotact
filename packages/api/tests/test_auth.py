"""Tests for auth module."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from src.auth.telegram import InitDataValidationError, validate_init_data


def _sign_init_data(
    fields: dict[str, str], bot_token: str, signature: str | None = None
) -> str:
    """Build a correctly-signed initData query string.

    The hash is computed over the data-check-string of ``fields`` (sorted,
    decoded values) and never includes ``hash`` or ``signature`` — matching
    Telegram's algorithm. ``signature`` is appended afterwards when given.
    """
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    out = dict(fields)
    out["hash"] = computed
    if signature is not None:
        out["signature"] = signature
    return urlencode(out)


class TestValidateInitData:
    """Tests for validate_init_data function."""

    def test_empty_init_data_raises_error(self):
        """Empty init data should raise error."""
        with pytest.raises(InitDataValidationError, match="Empty init data"):
            validate_init_data("", "bot_token")

    def test_missing_hash_raises_error(self):
        """Missing hash should raise error."""
        with pytest.raises(InitDataValidationError, match="Missing hash"):
            validate_init_data("user=test&auth_date=123", "bot_token")

    def test_invalid_hash_raises_error(self):
        """Invalid hash should raise error."""
        with pytest.raises(InitDataValidationError, match="Invalid hash"):
            validate_init_data("user=test&auth_date=123&hash=invalid", "bot_token")

    def test_valid_init_data_passes(self):
        """Correctly-signed initData (no signature field) validates."""
        token = "123456:test-bot-token"
        user_json = '{"id":12345678,"first_name":"Dev"}'
        init = _sign_init_data(
            {"auth_date": str(int(time.time())), "user": user_json}, token
        )
        result = validate_init_data(init, token)
        assert result.user is not None
        assert result.user.id == 12345678

    def test_valid_init_data_with_signature_is_accepted(self):
        """Regression: the newer Telegram `signature` field must be excluded
        from the data-check-string. The hash here is computed WITHOUT
        `signature`; a validator that wrongly includes it raises 'Invalid hash'
        and this test fails (the bug it guards against).
        """
        token = "123456:test-bot-token"
        user_json = '{"id":12345678,"first_name":"Dev"}'
        init = _sign_init_data(
            {"auth_date": str(int(time.time())), "user": user_json},
            token,
            signature="ZXJ1ZDI1NTE5LXNpZ25hdHVyZQ",
        )
        result = validate_init_data(init, token)
        assert result.user is not None
        assert result.user.id == 12345678
