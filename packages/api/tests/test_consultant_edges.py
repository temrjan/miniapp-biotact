"""S2 adverse provider, selection and shared-store boundaries on synthetic PG data."""

import asyncio

import pytest
from sqlalchemy import select

from src.consultant.contracts import Fault
from src.consultant.models import Control, Run, Source
from tests.test_consultant import ProviderStub, request, seed, service


class AdverseProvider(ProviderStub):
    def __init__(self, case):
        super().__init__()
        self.case = case

    async def response(self, inputs, last):
        result = await super().response(inputs, last)
        if self.case in {"duplicate", "unique-second-call"} and len(self.calls) == 2:
            result["output"] = [
                {
                    "type": "function_call",
                    "name": "read_context",
                    "call_id": "synthetic-call"
                    if self.case == "duplicate"
                    else "second-unique-call",
                    "arguments": '{"product_ids":[],"sections":["composition"]}',
                }
            ]
        elif self.case == "unknown-output":
            result["output"].append({"type": "unapproved_tool", "payload": "untrusted"})
        elif self.case == "invalid-output-type":
            result["output"][0]["type"] = []
        elif self.case == "empty-call":
            result["output"][0]["call_id"] = ""
        elif self.case == "invalid-argument-type":
            result["output"][0]["arguments"] = []
        elif self.case == "unknown-tool":
            result["output"][0]["name"] = "write_basket"
        elif self.case == "extra-argument":
            result["output"][0]["arguments"] = '{"product_ids":[],"sections":[],"sql":"SELECT 1"}'
        elif self.case == "unknown-section":
            result["output"][0]["arguments"] = '{"product_ids":[],"sections":["private"]}'
        elif self.case == "duplicate-section":
            result["output"][0]["arguments"] = '{"product_ids":[],"sections":["use","use"]}'
        elif self.case == "unrelated-product":
            result["output"][0]["arguments"] = '{"product_ids":[20],"sections":[]}'
        return result


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "unknown-output",
        "invalid-output-type",
        "empty-call",
        "invalid-argument-type",
        "unknown-tool",
        "extra-argument",
        "unrelated-product",
        "unknown-section",
        "duplicate-section",
    ],
)
async def test_adverse_provider_does_not_execute_an_unapproved_continuation(test_db, case):
    await seed(test_db)
    provider = AdverseProvider(case)
    bot = service(test_db, provider)
    with pytest.raises(Fault, match="invalid_answer") as error:
        await bot.turn(request())
    assert error.value.status == 502
    assert len(provider.calls) == (2 if case == "duplicate" else 1)
    async with bot.controls.sessions() as db:
        assert not (await db.scalars(select(Run).where(Run.done.is_(False)))).all()
        # The public tool channel never owns product/business writes.
        assert len((await db.scalars(select(Source))).all()) == 4


async def test_two_distinct_context_calls_remain_supported(test_db):
    await seed(test_db)
    provider = AdverseProvider("unique-second-call")
    bot = service(test_db, provider)
    response = await bot.turn(request())
    assert response["status"] == "answer"
    assert [p["id"] for p in response["products"]] == [4]
    assert [call[0] for call in provider.calls] == ["response"] * 3
    assert [
        item["call_id"]
        for item in provider.calls[-1][1]
        if item.get("type") == "function_call_output"
    ] == ["synthetic-call", "second-unique-call"]


async def test_reversed_selection_price_ordinal_and_positive_navigation(test_db):
    await seed(test_db)
    bot = service(test_db)
    first = request("DERMACOMPLEX и ACTIVE цена")
    reply = await bot.turn(first)
    assert [p["id"] for p in reply["products"]] == [4, 20]
    followup = request("открой второй", reply["context_token"], first.tab_id)
    opened = await bot.turn(followup)
    assert [p["id"] for p in opened["products"]] == [20]
    assert opened["open_product_id"] == 20
    assert bot.tokens.read(followup.model_copy(update={"context_token": opened["context_token"]}))[
        0
    ] == [20]
    assert len(bot.provider.calls) == 2


@pytest.mark.parametrize("change", ["withdraw", "disable"])
async def test_midturn_admission_or_switch_loss_suppresses_stale_delivery(test_db, change):
    await seed(test_db)
    gate = asyncio.Event()
    provider = ProviderStub(delay=gate)
    bot = service(test_db, provider)
    turn = request()
    pending = asyncio.create_task(bot.turn(turn))
    try:
        await asyncio.wait_for(provider.started.wait(), 5)
        async with bot.controls.sessions.begin() as db:
            if change == "withdraw":
                (await db.get(Source, "s4ru")).withdrawn = True
            else:
                (await db.get(Control, 1)).enabled = False
        gate.set()
        if change == "disable":
            with pytest.raises(Fault, match="disabled"):
                await asyncio.wait_for(pending, 5)
        else:
            result = await asyncio.wait_for(pending, 5)
            assert result["status"] == "limited"
            assert result["products"] == result["sources"] == []
            assert result["open_product_id"] is None
            assert (
                bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[
                    0
                ]
                == []
            )
        assert len(provider.calls) == 1
    finally:
        gate.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_same_subject_source_only_keeps_selection_then_loss_clears_it(test_db):
    await seed(test_db)
    bot = service(test_db)
    first = request("ACTIVE")
    response = await bot.turn(first)
    followup = request("состав", response["context_token"], first.tab_id)
    result = await bot.turn(followup)
    assert [p["id"] for p in result["products"]] == [20]
    assert bot.tokens.read(followup.model_copy(update={"context_token": result["context_token"]}))[
        0
    ] == [20]
    async with bot.controls.sessions.begin() as db:
        (await db.get(Source, "s20ru")).withdrawn = True
    missing = await bot.turn(request("ACTIVE", result["context_token"], first.tab_id))
    assert missing["status"] == "limited"
    assert missing["products"] == []
    bare = await bot.turn(request("цена", missing["context_token"], first.tab_id))
    assert bare["status"] == "clarification"
    assert bare["products"] == []
    assert len(bot.provider.calls) == 4
