"""The bot-token check of the ``/api/v1/users/telegram`` routes fails closed and compares bytes."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.users.models import User

TOKEN = "ab" * 16
TELEGRAM_ID = 990601
PATH = f"/api/v1/users/telegram/{TELEGRAM_ID}"
REFUSED = {"detail": "Invalid bot token"}


@pytest.mark.parametrize(
    ("configured", "header", "expected"),
    [
        ("", "", 403),
        ("", "x", 403),
        (TOKEN, "", 403),
        # Differs only in the last character.
        (TOKEN, TOKEN[:-1] + "c", 403),
        # The real token plus non-ASCII text, sent as UTF-8 bytes: a str comparison raises,
        # and a prefix-only or a non-ASCII-dropping comparison would accept it.
        (TOKEN, (TOKEN + "токен").encode("utf-8"), 403),
        (TOKEN, TOKEN, 200),
        (TOKEN, None, 422),
    ],
    ids=[
        "unconfigured-empty-header",
        "unconfigured-any-header",
        "empty-header",
        "wrong",
        "non-ascii",
        "right",
        "missing",
    ],
)
async def test_t01_users_bot_token_fails_closed(
    client: AsyncClient,
    test_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    header: str | bytes | None,
    expected: int,
) -> None:
    """T-01: unconfigured, empty, wrong or non-ASCII token is 403; the right one passes."""
    user = User(telegram_id=TELEGRAM_ID, first_name="Bot", language_code="ru")
    test_db.add(user)
    await test_db.commit()
    monkeypatch.setattr(settings, "telegram_bot_token", configured)

    response = await client.get(PATH, headers={} if header is None else {"X-Bot-Token": header})

    assert response.status_code == expected, response.text
    if expected == 403:
        assert response.json() == REFUSED
    elif expected == 200:
        assert response.json()["id"] == user.id


async def test_t05_bot_user_path_with_the_right_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-05: with the right token the bot creates a user, reads it and changes its language."""
    monkeypatch.setattr(settings, "telegram_bot_token", TOKEN)
    headers = {"X-Bot-Token": TOKEN}

    created = await client.post(
        "/api/v1/users/telegram",
        json={"telegram_id": TELEGRAM_ID, "first_name": "Bot", "language_code": "ru"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["telegram_id"] == TELEGRAM_ID

    read = await client.get(PATH, headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["id"] == created.json()["id"]

    changed = await client.patch(f"{PATH}/language", json={"language_code": "uz"}, headers=headers)
    assert changed.status_code == 200, changed.text
    assert changed.json()["language_code"] == "uz"
    assert changed.json()["id"] == created.json()["id"]
