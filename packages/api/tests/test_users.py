"""Tests for users module."""

import pytest
from pydantic import ValidationError

from src.users.schemas import AddressUpdate, PhoneUpdate


class TestPhoneUpdate:
    """Tests for PhoneUpdate schema."""

    def test_valid_phone_with_plus(self):
        """Phone with + should be valid."""
        data = PhoneUpdate(phone="+998901234567")
        assert data.phone == "+998901234567"

    def test_valid_phone_without_plus(self):
        """Phone without + should add it."""
        data = PhoneUpdate(phone="998901234567")
        assert data.phone == "+998901234567"

    def test_phone_removes_spaces(self):
        """Spaces should be removed."""
        data = PhoneUpdate(phone="+998 90 123 45 67")
        assert data.phone == "+998901234567"

    def test_phone_removes_dashes(self):
        """Dashes should be removed."""
        data = PhoneUpdate(phone="+998-90-123-45-67")
        assert data.phone == "+998901234567"

    def test_short_phone_raises_error(self):
        """Too short phone should raise error."""
        with pytest.raises(ValidationError):
            PhoneUpdate(phone="+12345")

    def test_long_phone_raises_error(self):
        """Too long phone should raise error."""
        with pytest.raises(ValidationError):
            PhoneUpdate(phone="+12345678901234567890")


class TestAddressUpdate:
    """Tests for AddressUpdate schema."""

    def test_valid_address(self):
        """Valid address should work."""
        data = AddressUpdate(
            street="Навои",
            house="12",
            apartment="45",
        )
        assert data.street == "Навои"
        assert data.house == "12"
        assert data.apartment == "45"

    def test_address_requires_street(self):
        """Street is required."""
        with pytest.raises(ValidationError):
            AddressUpdate(house="12")

    def test_address_requires_house(self):
        """House is required."""
        with pytest.raises(ValidationError):
            AddressUpdate(street="Навои")

    def test_address_optional_fields(self):
        """Optional fields should be None by default."""
        data = AddressUpdate(street="Навои", house="12")
        assert data.apartment is None
        assert data.entrance is None
        assert data.floor is None
        assert data.comment is None
        assert data.lat is None
        assert data.lng is None

    def test_address_with_coordinates(self):
        """Address with coordinates should work."""
        data = AddressUpdate(
            street="Навои",
            house="12",
            lat=41.311081,
            lng=69.240562,
        )
        assert data.lat == 41.311081
        assert data.lng == 69.240562
