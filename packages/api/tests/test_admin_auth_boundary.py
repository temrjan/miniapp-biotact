"""Boundary of the admin surface: only an ADMIN token opens it (PR-S).

**Coverage is one endpoint per mounted router surface — 7 of the 18 endpoints** that declare
``Depends(verify_admin_jwt)``. The sample is per-router on purpose: the dependency is shared, so a
second endpoint on the same router would re-test the same code path, while a router missing from
this list would be genuinely unguarded. Checked at the real HTTP layer, through the app's routing —
a test that mocks the dependency verifies belief in the dependency, not the route, and stays green
even if a route forgets to declare it.

Three parts, each guarding a different failure:

* **refusal** — a non-admin token must be rejected (401);
* **positive control** — an admin token must NOT be rejected. Without it, "deny everything" passes
  the refusal half and silently kills the admin panel;
* **config pinning + route absence** — the two that actually stand guard over the historical hole
  (see :func:`test_removed_login_secrets_are_not_back_in_settings`).

⚠️ **What the refusal half can and cannot prove.** The removed circuit signed its tokens with the
deployed ``JWT_SECRET``; a test cannot know that value, so it cannot forge the exact historical
token. Restoring the old branch alone would therefore NOT redden the refusal tests — measured, not
assumed. Regression of the real hole is caught one step earlier, by the config pinning (the branch
cannot come back without ``jwt_secret``) and by the route-absence test (no circuit to mint tokens).
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, settings
from src.users.models import User

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
    """A non-admin token must not open an admin endpoint — 401, before any handler work.

    Scope of the claim, deliberately narrow: this proves the surface refuses a token that is not an
    admin token. It does NOT reproduce the historical hole — that token was signed with the
    deployed ``JWT_SECRET``, a value no test can know, so on pre-fix code these two forgeries were
    already refused (measured on restored pre-fix code: 21 passed, hole not caught). The historical
    regression is pinned by the config test and the route-absence test below.
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
        (
            "admin key, type=access",
            _user_access_token(secret=settings.secret_key.get_secret_value()),
        ),
    ):
        resp = await _call(client, method, path, token)
        assert resp.status_code == 401, (
            f"{method} {path} admitted a user token ({label}) with {resp.status_code}; "
            "the admin surface must answer only to an admin token"
        )


def test_removed_login_secrets_are_not_back_in_settings() -> None:
    """The deleted circuit's settings must stay deleted — the FIRST step of any regression.

    Restoring the hole takes three steps: bring ``jwt_secret`` back into the config → re-accept
    ``type="access"`` in ``verify_admin_jwt`` → re-mount the minting routes. Step three is caught
    by :func:`test_removed_login_circuit_has_no_routes`, step two only partially (a variant signed
    with ``secret_key``), and step one by nothing else — a forged token cannot stand in for one
    signed with a secret the test does not know.

    Asserting on ``model_fields`` rather than on ``settings`` instances: the class-level schema is
    what a future edit would change, and it does not depend on which ``.env`` happens to be loaded.
    """
    assert "jwt_secret" not in Settings.model_fields, (
        "jwt_secret is back in the config — the only reason to add it is to re-accept the tokens "
        "PR-S stopped accepting"
    )
    assert "jwt_algorithm" not in Settings.model_fields, (
        "jwt_algorithm is back — the admin token is decoded with `algorithm`, the field its issuer "
        "signs with; a second algorithm setting exists only to serve the removed circuit"
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
    client: AsyncClient, test_db: AsyncSession, method: str, path: str
) -> None:
    """POSITIVE CONTROL — the admin token keeps working on every surface.

    Deliberately asserts "not 401" rather than a success code: the ids in the paths do not exist,
    so 404/422 are the honest answers of a handler that was ALLOWED to run. Without this test
    "refuse everything" would satisfy the refusal half above and silently kill the admin panel.
    """
    test_db.add(User(id=1, telegram_id=1, is_admin=True, is_active=True))
    await test_db.commit()
    resp = await _call(client, method, path, _admin_token())
    assert resp.status_code not in (401, 403), (
        f"{method} {path} rejected a valid ADMIN token ({resp.status_code}) — the admin panel "
        "would be locked out"
    )


LOCAL_SURFACES = [
    ("GET", "/api/v1/analytics/dashboard"),
    ("GET", "/api/v1/crm/customers"),
    ("GET", "/api/v1/admin/crm/leads"),
    ("GET", "/api/v1/admin/broadcasts"),
    ("GET", "/api/v1/admin/users"),
    ("GET", "/api/v1/admin/auth/me"),
    ("GET", "/api/v1/admin/lottery/current"),
]


@pytest.mark.parametrize(
    ("method", "path"), [(p.values[0], p.values[1]) for p in ADMIN_SURFACES] + LOCAL_SURFACES
)
@pytest.mark.parametrize("change", ["revoke", "inactive", "delete"])
async def test_old_admin_token_retires_on_next_request(
    client, test_db, method, path, change, monkeypatch
):
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    test_db.add(User(id=1, telegram_id=1, is_admin=True, is_active=True))
    await test_db.commit()
    token = _admin_token()
    positive = await _call(client, method, path, token)
    assert positive.status_code not in (401, 403)
    if change == "delete":
        await test_db.execute(delete(User).where(User.id == 1))
    else:
        await test_db.execute(
            update(User)
            .where(User.id == 1)
            .values(**{"is_admin" if change == "revoke" else "is_active": False})
        )
    await test_db.commit()
    refused = await _call(client, method, path, token)
    assert refused.status_code == 403


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "1", "type": "admin"},
        {"sub": "1", "type": "admin", "exp": "9999999999"},
        {"sub": "1", "type": "admin", "exp": 1},
        {"sub": "01", "type": "admin", "exp": 9999999999},
        {"sub": "2147483648", "type": "admin", "exp": 9999999999},
        {"sub": [], "type": "admin", "exp": 9999999999},
        {"sub": 1, "type": "admin", "exp": 9999999999},
    ],
)
async def test_malformed_admin_claims_refuse(client, claims):
    token = jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
    for path in ("/api/v1/admin/orders", "/api/v1/admin/auth/me"):
        response = await _call(client, "GET", path, token)
        assert response.status_code == 401


@pytest.mark.parametrize(
    ("claims", "canonical_status"),
    [
        ({"type": "admin", "exp": 9999999999}, 403),
        ({"type": "access", "sub": "1", "exp": 9999999999}, 403),
    ],
)
async def test_wrapper_claim_statuses_preserved(client, claims, canonical_status):
    token = jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
    assert (await _call(client, "GET", "/api/v1/admin/orders", token)).status_code == 401
    assert (
        await _call(client, "GET", "/api/v1/admin/auth/me", token)
    ).status_code == canonical_status
