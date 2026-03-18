"""Tests for auth module."""

import pytest

from src.auth.telegram import InitDataValidationError, validate_init_data


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
