"""Boundary of the admin surface: only an ADMIN token opens it (PR-S).

Every endpoint guarded by ``verify_admin_jwt`` is checked here at the real HTTP layer — through
the app's routing, not by calling the dependency directly. A test that mocks the dependency
verifies belief in the dependency, not the route: it stays green even if a route forgets to
declare it.

Two halves, and both are load-bearing:

* **refusal** — a user token (``type="access"``, signed with ``jwt_secret``) must be rejected;
* **positive control** — an admin token (``type="admin"``, signed with ``secret_key``) must NOT be
  rejected. Without it, "deny everything" passes the refusal half and silently kills the admin
  panel.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from jose import jwt

from src.config import settings

# One endpoint per mounted router surface that declares ``Depends(verify_admin_jwt)``.
# Paths carry the prefixes from ``main.py``; methods without a body are preferred so the response
# reflects authorization, not payload validation. Ids are deliberately non-existent: a refused
# request must never reach the handler, and an admitted one is expected to 404, which is still
# proof that authorization passed.
ADMIN_SURFACES = [
    pytest.param("GET", "/api/v1/admin/orders", id="orders_admin_router"),
    pytest.param("DELETE", "/api/v1/products/999999", id="products_router"),
    pytest.param("DELETE", "/api/v1/banners/999999", id="banners_router"),
    pytest.param("DELETE", "/api/v1/categories/999999", id="categories_router"),
    pytest.param("POST", "/api/v1/uploads/products", id="uploads_router"),
    pytest.param("PATCH", "/api/v1/orders/999999/status", id="orders_router"),
    pytest.param("PUT", "/api/v1/settings/some_key", id="settings_router"),
]

# Control that makes the probe readable: an unparseable token yields 401 on every surface above.
# That is what proves the authorization dependency runs BEFORE body validation — so a 404/422
# answer to a *valid* user token means "admitted, then failed later", not "refused".
GARBAGE_TOKEN = "garbage.token.here"


# The secret of the deleted circuit. A literal on purpose: ``settings.jwt_secret`` is gone with
# PR-S, and a test for "tokens of the removed circuit are refused" must not depend on the config
# it removed — that would make the test un-runnable exactly when the fix is in place.
LEGACY_JWT_SECRET = "legacy-secret-of-the-removed-registration-circuit"


def _user_access_token(user_id: int = 1, *, secret: str = LEGACY_JWT_SECRET) -> str:
    """A plain USER token — the shape ``POST /auth/login`` handed out before PR-S.

    Mirrors the deleted ``jwt_router.create_access_token``: ``type="access"``. ``secret`` is
    parameterised so the refusal can be proven twice over — with a foreign signature AND with the
    admin signing key, which isolates the type check from the signature check.
    """
    payload = {
        "sub": str(user_id),
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=30),
    }
    return jwt.encode(payload, secret, algorithm=settings.algorithm)


def _admin_token(user_id: int = 1, telegram_id: int = 1) -> str:
    """An ADMIN token — what ``/admin/auth/login`` hands out (``admin_router.create_admin_token``).

    Signed with ``secret_key`` and, critically, with ``settings.algorithm`` — the same field the
    real issuer uses. Decoding must agree with it; the two algorithm settings are separate fields
    that merely happen to share a value today.
    """
    payload = {
        "sub": str(user_id),
        "telegram_id": telegram_id,
        "type": "admin",
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


async def _call(client: AsyncClient, method: str, path: str, token: str):
    return await client.request(method, path, headers={"Authorization": f"Bearer {token}"})


# Paths of the login circuit deleted in PR-S. Their absence is asserted, not assumed: a router
# quietly mounted again would restore the hole this PR closed.
REMOVED_AUTH_PATHS = [
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/auth/verify-email"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/forgot-password"),
    ("POST", "/api/v1/auth/reset-password"),
]


# --------------------------------------------------------------------------- #
# Permanent boundary: refusal + positive control, one of each per router surface
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("method", "path"), [(p.values[0], p.values[1]) for p in ADMIN_SURFACES])
async def test_user_access_token_is_refused_on_admin_surface(
    client: AsyncClient, method: str, path: str
) -> None:
    """A plain user token must not open an admin endpoint — 401, before any handler work.

    Before PR-S every one of these answered 200/404/422 to this token: the surface admitted anyone
    holding a token signed with ``jwt_secret``, regardless of role.
    """
    refused = await _call(client, method, path, GARBAGE_TOKEN)
    assert refused.status_code == 401, (
        f"control failed: {method} {path} answers {refused.status_code} to an unparseable token, "
        "so the 401 asserted below would not be evidence of authorization at all"
    )

    # Two independent reasons the token must fail, checked separately so neither can mask the
    # other: foreign signature, and — signed with the ADMIN key — a non-admin ``type``. The second
    # is what catches a future "let's also accept access tokens" written against `secret_key`.
    for label, token in (
        ("legacy signature", _user_access_token()),
        ("admin key, type=access", _user_access_token(secret=settings.secret_key.get_secret_value())),
    ):
        resp = await _call(client, method, path, token)
        assert resp.status_code == 401, (
            f"{method} {path} admitted a user token ({label}) with {resp.status_code}; "
            "the admin surface must answer only to an admin token"
        )


@pytest.mark.parametrize(("method", "path"), REMOVED_AUTH_PATHS)
async def test_removed_login_circuit_has_no_routes(
    client: AsyncClient, method: str, path: str
) -> None:
    """The self-registration circuit is gone: no route, hence 404 — with or without a token."""
    resp = await client.request(method, path, json={})
    assert resp.status_code == 404, f"{method} {path} still routes somewhere ({resp.status_code})"


@pytest.mark.parametrize(("method", "path"), [(p.values[0], p.values[1]) for p in ADMIN_SURFACES])
async def test_admin_token_still_opens_admin_surface(
    client: AsyncClient, method: str, path: str
) -> None:
    """POSITIVE CONTROL — the admin token keeps working on every surface.

    Deliberately asserts "not 401" rather than a success code: the ids in the paths do not exist,
    so 404/422 are the honest answers of a handler that was ALLOWED to run. Without this test
    "refuse everything" would satisfy the refusal half above and silently kill the admin panel.
    """
    resp = await _call(client, method, path, _admin_token())
    assert resp.status_code != 401, (
        f"{method} {path} rejected a valid ADMIN token ({resp.status_code}) — the admin panel "
        "would be locked out"
    )
