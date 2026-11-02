"""Regression for review r1 B1: a danger sign written with an ordinal word got the product
clarification text (service.py CLARIFY) instead of 103. Since PR-3b Luna writes the 103
text; she is faked at the Provider.call boundary, so the runtime makes no outbound call."""

import json
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.consultant.contracts import Turn
from src.consultant.provider import Provider
from src.consultant.router import runtime
from tests.test_consultant import KEY, seed
from tests.test_consultant_graf_fakes import LunaStub


def secret(value):
    return SimpleNamespace(get_secret_value=lambda: value)


async def test_ordinal_danger_text_gets_103_instead_of_the_product_question(test_db, monkeypatch):
    await seed(test_db)
    luna = LunaStub()
    monkeypatch.setattr(Provider, "call", lambda self, route, payload: luna.call(route, payload))
    config = SimpleNamespace(
        consultant_provider_key=secret("provider-key-fixture"),
        consultant_protection_key=secret(KEY),
        consultant_graf_key=secret("graf-key-fixture"),
        consultant_origins=[],
        consultant_hybrid=True,
    )
    app = SimpleNamespace(state=SimpleNamespace())
    async with runtime(app, config, async_sessionmaker(test_db.bind, expire_on_commit=False)):
        request = Turn.model_validate_json(
            json.dumps(
                {
                    "request_id": str(uuid4()),
                    "tab_id": str(uuid4()),
                    "context_revision": 1,
                    "language": "ru",
                    "question": "Второй день давит в груди",
                    "history": [],
                    "page_product_id": None,
                    "context_token": None,
                }
            )
        )
        reply = await app.state.consultant.turn(request)
    assert "О каком товаре" not in reply["answer"]
    assert "103" in reply["answer"]
    assert reply["status"] == "limited"
    assert reply["products"] == []
    assert len(luna.luna) == 1
