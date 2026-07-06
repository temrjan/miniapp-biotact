"""Tests for auth module."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from src.auth.telegram import InitDataValidationError, validate_init_data


def _sign_init_data(fields: dict[str, str], bot_token: str, signature: str | None = None) -> str:
    """Build a correctly-signed initData query string the way Telegram does.

    The data-check-string is every field EXCEPT ``hash``, sorted, with decoded values —
    so when Telegram sends a ``signature`` field it IS part of the string the ``hash``
    covers. The ``hash`` is computed last and appended.
    """
    signed = dict(fields)
    if signature is not None:
        signed["signature"] = signature  # part of the data-check-string (real Telegram behavior)
    data_check_string = "\n".join(f"{k}={signed[k]}" for k in sorted(signed))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signed["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(signed)


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
        init = _sign_init_data({"auth_date": str(int(time.time())), "user": user_json}, token)
        result = validate_init_data(init, token)
        assert result.user is not None
        assert result.user.id == 12345678

    def test_valid_init_data_with_signature_is_accepted(self):
        """Regression (prod incident 2026-07-06): real Telegram initData carries a
        `signature` field that IS part of the data-check-string the `hash` covers.
        A validator that excludes `signature` recomputes a different hash and raises
        'Invalid hash' → a 401 for every real client. This builds initData the way
        Telegram does (hash over all fields including `signature`) and asserts it validates.
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

    def test_signature_not_folded_into_hash_is_rejected(self):
        """A vector whose `hash` was computed WITHOUT `signature` (the old, wrong
        assumption) must be rejected — proof the validator folds `signature` into
        the data-check-string rather than ignoring it. Red against the pre-fix code.
        """
        token = "123456:test-bot-token"
        user_json = '{"id":12345678,"first_name":"Dev"}'
        # Sign WITHOUT signature (hash covers only auth_date + user), then bolt one on.
        signed = _sign_init_data({"auth_date": str(int(time.time())), "user": user_json}, token)
        tampered = signed + "&signature=ZXJ1ZDI1NTE5LXNpZ25hdHVyZQ"
        with pytest.raises(InitDataValidationError, match="Invalid hash"):
            validate_init_data(tampered, token)
