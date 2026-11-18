"""Dependency security boundary checks for nested secrets and admin JWT verification."""

import base64
from pathlib import Path

import jwt
import pytest
from jwt import InvalidAlgorithmError
from pydantic_settings import BaseSettings, NestedSecretsSettingsSource

from src.auth.admin_router import create_admin_token
from src.config import settings


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


@pytest.mark.parametrize("outside_link", [False, True], ids=["local", "escape"])
def test_nested_secrets_only_reads_files_inside_the_root(
    tmp_path: Path, outside_link: bool
) -> None:
    class ProbeSettings(BaseSettings):
        token: str = "default"

    root = tmp_path / "secrets"
    root.mkdir()
    if outside_link:
        outside = tmp_path / "outside"
        outside.write_text("outside-marker")
        (root / "token").symlink_to(outside)
    else:
        (root / "token").write_text("inside-marker")
    source = NestedSecretsSettingsSource(ProbeSettings, secrets_dir=root)
    assert source() == ({} if outside_link else {"token": "inside-marker"})


def test_admin_token_roundtrip_and_claims() -> None:
    token = create_admin_token(123, 456)
    payload = jwt.decode(
        token,
        settings.secret_key.get_secret_value(),
        algorithms=[settings.algorithm],
        options={"require": ["exp"], "verify_sub": False},
    )
    assert payload["sub"] == "123"
    assert payload["type"] == "admin"
    assert payload["telegram_id"] == 456
    assert isinstance(payload["exp"], int)


@pytest.mark.parametrize("algorithm", ["ES256", "none", "RS256"])
def test_jwt_rejects_an_unallowed_algorithm(algorithm: str) -> None:
    header = _b64url(f'{{"alg":"{algorithm}","typ":"JWT"}}'.encode())
    payload = _b64url(b'{"sub":"123","type":"admin","exp":9999999999}')
    signature = b"" if algorithm == "none" else _b64url(b"signature-bytes")
    token = b".".join([header, payload, signature])
    with pytest.raises(InvalidAlgorithmError):
        jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=["HS256"])
