"""Behavioral upgrade witnesses and the selected JWT crypto backend."""

from pathlib import Path

import pytest
from ecdsa import SigningKey
from ecdsa.der import UnexpectedDER, remove_octet_string
from jose import JWTError, jwt
from jose.backends import ECKey
from pydantic_settings import BaseSettings, NestedSecretsSettingsSource

from src.auth.admin_router import create_admin_token
from src.config import settings


def test_der_octet_string_rejects_a_truncated_declared_length() -> None:
    with pytest.raises(UnexpectedDER):
        remove_octet_string(b"\x04\x82\x10\x00ABC")


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


def test_jose_elliptic_curve_backend_is_cryptography(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The native python-ecdsa signer has an unpatched timing advisory. Both
    # installation declarations must select the alternative backend explicitly.
    assert ECKey.__module__ == "jose.backends.cryptography_backend"
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def forbidden_sign(*_args: object, **_kwargs: object) -> None:
        pytest.fail("elliptic-curve signing reached python-ecdsa")

    monkeypatch.setattr(SigningKey, "sign_digest", forbidden_sign)
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    token = jwt.encode({"sub": "crypto-probe"}, private, algorithm="ES256")
    assert jwt.decode(token, public, algorithms=["ES256"])["sub"] == "crypto-probe"


def test_admin_token_signing_does_not_use_python_ecdsa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_sign(*_args: object, **_kwargs: object) -> None:
        pytest.fail("admin token signing reached python-ecdsa")

    monkeypatch.setattr(SigningKey, "sign", forbidden_sign)
    monkeypatch.setattr(SigningKey, "sign_digest", forbidden_sign)
    token = create_admin_token(123, 456)
    payload = jwt.decode(
        token, settings.secret_key.get_secret_value(), algorithms=[settings.algorithm]
    )
    assert payload["sub"] == "123"
    assert payload["type"] == "admin"
    assert payload["telegram_id"] == 456


@pytest.mark.parametrize("algorithm", ["ES256", "none", "RS256"])
def test_jwt_rejects_an_unallowed_algorithm_without_ecdsa_signing(
    monkeypatch: pytest.MonkeyPatch, algorithm: str
) -> None:
    from jose.utils import base64url_encode

    def forbidden_sign(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unallowed JWT reached python-ecdsa signing")

    monkeypatch.setattr(SigningKey, "sign_digest", forbidden_sign)
    header = base64url_encode(f'{{"alg":"{algorithm}"}}'.encode())
    payload = base64url_encode(b'{"sub":"123","type":"admin"}')
    token = b".".join([header, payload, b"invalid"])
    with pytest.raises(JWTError):
        jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=["HS256"])
