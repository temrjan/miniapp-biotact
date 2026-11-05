"""Audit M4 (a): the CRM bot token check refuses with 401, never 500."""

import pytest
from pydantic import SecretStr

from src.config import settings

PATH = "/api/v1/bot/customer/990001"
TOKEN = "ab" * 16


@pytest.mark.parametrize(
    "header",
    # "wrong" differs only in the last byte; "non-ascii" is the real token plus non-ASCII text,
    # so a prefix-only or a non-ASCII-dropping comparison would accept it.
    [None, (TOKEN[:-1] + "c").encode(), (TOKEN + "токен").encode()],
    ids=["missing", "wrong", "non-ascii"],
)
async def test_m4a_bot_token_refusal_is_401_never_500(client, monkeypatch, header) -> None:
    """Audit M4 (a): a missing, wrong or non-ASCII X-Bot-Token is 401; the right one passes."""
    monkeypatch.setattr(settings, "bot_api_token", SecretStr(TOKEN))
    refused = await client.get(PATH, headers={} if header is None else {"X-Bot-Token": header})
    assert refused.status_code == 401
    assert refused.json() == {"detail": "Invalid bot token"}
    allowed = await client.get(PATH, headers={"X-Bot-Token": TOKEN})
    assert allowed.status_code == 200, allowed.text
