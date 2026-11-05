"""Audit M2: every loyalty route refuses an inactive user and writes nothing for that user.

Activation and consent own an explicit transaction, so seeding and the after-state read use a
second sessionmaker on the same test database (the ``maker`` fixture of the activation tests).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import settings
from src.loyalty.models import (
    Consent,
    CustomerEvent,
    CustomerProfile,
    LotteryTicket,
    PointLedger,
    QrCode,
)
from src.users.models import User
from tests.conftest import _make_init_data
from tests.test_loyalty_activation import _seed_product_and_codes
from tests.test_loyalty_activation import maker as maker

BOT_TOKEN = "test-bot-token-123"
TELEGRAM_ID = 60077
TABLES = tuple(
    model.__table__
    for model in (User, CustomerProfile, Consent, CustomerEvent, PointLedger, QrCode, LotteryTicket)
)

# Every route that authenticates through ``get_init_data_user`` (loyalty/router.py).
ROUTES = [
    ("POST", "/api/v1/qr/activate", {"code": "<seeded>"}),
    ("GET", "/api/v1/loyalty/me", None),
    ("POST", "/api/v1/loyalty/consent", {"source": "qr"}),
    ("POST", "/api/v1/loyalty/consent/marketing", {"granted": True}),
    ("POST", "/api/v1/loyalty/survey", {"is_adult": True}),
    ("GET", "/api/v1/loyalty/my-tickets", None),
]


async def _rows(sm: async_sessionmaker[AsyncSession]) -> dict[str, list[tuple[object, ...]]]:
    async with sm() as s:
        return {
            table.name: [
                tuple(row) for row in (await s.execute(select(table).order_by(table.c.id))).all()
            ]
            for table in TABLES
        }


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[r[1] for r in ROUTES])
async def test_m2_loyalty_refuses_inactive_user_and_writes_nothing(
    client, maker, monkeypatch, method, path, body
) -> None:
    """Audit M2: an existing inactive member gets the buyer-route 403 and no row changes."""
    _, (code,) = await _seed_product_and_codes(maker)
    async with maker() as s, s.begin():
        user = User(telegram_id=TELEGRAM_ID, first_name="Seed", is_active=False)
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id))
    payload = {"code": code} if path.endswith("/activate") else body
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
    # A changed name would be written by get_or_create_user if the refusal did not roll back.
    headers = {
        "X-Init-Data": _make_init_data(BOT_TOKEN, {"id": TELEGRAM_ID, "first_name": "Changed"})
    }
    before = await _rows(maker)

    # Dark-feature rule: with loyalty off the route is "not found" before authentication.
    dark = await client.request(method, path, json=payload, headers=headers)
    assert dark.status_code == 404

    monkeypatch.setattr(settings, "loyalty_enabled", True)
    refused = await client.request(method, path, json=payload, headers=headers)
    assert refused.status_code == 403, refused.text
    assert refused.json() == {"detail": "User is inactive"}
    assert await _rows(maker) == before
