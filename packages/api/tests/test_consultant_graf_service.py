"""Graf inside Consultant.turn on the disposable PostgreSQL fixture: step order of plan r4 §3,
the bypass probes of review r1 B1 / r3 §4, the D31 named-product rule and the envelope that
the website parser accepts. Jev is faked only at its HTTP boundary."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.consultant import graf
from src.consultant.context import Tokens
from src.consultant.contracts import Fault, Turn
from src.consultant.control import Controls
from src.consultant.knowledge import Knowledge
from src.consultant.models import Control, Membership, Release, Run, Section, Source
from src.consultant.provider import EMBEDDING_PROFILE, PROFILE
from src.consultant.router import runtime
from src.consultant.service import REFUSED, Consultant
from src.products.models import Product
from tests.test_consultant import KEY
from tests.test_consultant_graf_fakes import LunaStub, answer
from tests.test_consultant_graf_flow import OpenRouter, variants

# Shop cards named exactly as products.json (shop_name, shop_id, form).
CARDS = ["zincum_c_d3", "zincum_stick", "immunocomplex", "immunocomplex_kids", "active_caps"]


async def seed(db):
    specs = [graf.PRODUCT[c] for c in CARDS]
    for p in specs:
        db.add(
            Product(
                id=p["shop_id"],
                name=p["shop_name"],
                slug=p["shop_slug"] + "-fixture",
                price=100,
                is_active=True,
                is_in_stock=True,
            )
        )
    await db.flush()
    sources = [f"g{p['shop_id']}{lang}" for p in specs for lang in ("ru", "uz")]
    db.add(
        Release(
            id="graf-release",
            manifest={"sources": sources},
            profile=PROFILE,
            approval="fixture-approval",
            prepared=True,
        )
    )
    await db.flush()
    control = await db.get(Control, 1)
    if control is None:
        db.add(Control(id=1, enabled=True, active="graf-release"))
    else:
        control.enabled, control.active = True, "graf-release"
    for p in specs:
        for lang in ("ru", "uz"):
            sid = f"g{p['shop_id']}{lang}"
            db.add(
                Source(
                    id=sid,
                    product_id=p["shop_id"],
                    language=lang,
                    provenance={
                        "title": p["shop_name"],
                        "form": p["form"],
                        "revision": "fixture-1",
                        "pdf_title": p["source_pdf"],
                        "aliases": [p["shop_name"]],
                        "section_count": 4,
                    },
                    withdrawn=False,
                )
            )
            await db.flush()
            db.add(Membership(release_id="graf-release", source_id=sid))
            for ordinal in range(4):
                body = f"Fixture section {ordinal}."
                db.add(
                    Section(
                        id=f"{sid}:{ordinal}",
                        source_id=sid,
                        ordinal=ordinal,
                        body=body,
                        body_hash=hashlib.sha256(body.encode()).hexdigest(),
                        heading=f"Section {ordinal}",
                        caution=ordinal == 2,
                        pdf_page=1,
                    )
                )
                await db.flush()
                await db.execute(
                    text(
                        "INSERT INTO consultant_embeddings(section_id,profile,vector) VALUES (:id,:profile,CAST(:vector AS vector))"
                    ),
                    {
                        "id": f"{sid}:{ordinal}",
                        "profile": EMBEDDING_PROFILE,
                        "vector": str([1.0] + [0.0] * 1535),
                    },
                )
    await db.commit()


def bot(db, router=None, on=True):
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    controls = Controls(sessions, KEY, True)
    engine = None
    if on:
        client = httpx.AsyncClient(transport=httpx.MockTransport((router or OpenRouter()).handler))
        engine = graf.Graf(graf.Jev(client, "fixture-key"))
    return Consultant(controls, Knowledge(sessions), LunaStub(), Tokens(KEY), engine)


def turn(question, previous=None, history=(), language="ru", page=None):
    return Turn.model_validate_json(
        json.dumps(
            {
                "request_id": str(uuid4()),
                "tab_id": str(previous.tab_id if previous else uuid4()),
                "context_revision": 1,
                "language": language,
                "question": question,
                "history": [{"question": q, "answer": a} for q, a in history],
                "page_product_id": page,
                "context_token": previous.token if previous else None,
            }
        )
    )


async def say(consultant, question, previous=None, history=(), page=None):
    request = turn(question, previous, history, page=page)
    response = await consultant.turn(request)
    response["tab_id"], response["token"] = request.tab_id, response["context_token"]
    return SimpleNamespace(**response)


async def runs(consultant):
    async with consultant.controls.sessions() as db:
        return await db.scalar(select(func.count()).select_from(Run))


@pytest.mark.parametrize(
    "message",
    [
        "второй день давит в груди, отдает в руку",
        "чем лечить, задыхается ребенок",
        "Как давать IMMUNOCOMPLEX® KIDS, у сына губы синеют",
        "Открой IMMUNOCOMPLEX®, ребенок не может дышать",
    ],
)
async def test_danger_bypass_branches_reach_103_through_luna(test_db, message):
    # The ambulance tier is computed before register (SF-02); the strong turn is then
    # registered and Luna writes its 103 text (HR-03). No product path; Graf and Jev may
    # prepare the message as Luna's helpers (Captain 2026-11-09, "Все луне").
    await seed(test_db)
    router = OpenRouter({"часто болею": "immunity"})
    consultant = bot(test_db, router)
    reply = await say(consultant, message)
    assert reply.answer in variants("emergency.general")
    assert (reply.status, reply.products, reply.sources) == ("limited", [], [])
    assert consultant.provider.calls == []
    assert len(consultant.provider.luna) == 1
    assert consultant.provider.briefs[0]["safety"]["primary"] == "emergency"
    assert await runs(consultant) == 1


@pytest.mark.parametrize(
    "danger", ["не может дышать", "губы синеют", "nafas ololmayapman", "нафас ололмаяпти"]
)
async def test_danger_in_a_reply_to_a_graf_question_reaches_103_through_luna(test_db, danger):
    # Review r1 TST-01: the ambulance list also runs on a Graf-owned continuation turn.
    await seed(test_db)
    message = "сын часто болеет"
    router = OpenRouter({message: "immunity"})
    consultant = bot(test_db, router)
    first = await say(consultant, message)
    history = [(message, first.answer)]
    assert first.status == "clarification"
    assert graf.Graf.owns(turn("ему 5", first, history))
    reply = await say(consultant, f"ему 5, {danger}", first, history)
    assert reply.answer in variants("emergency.general")
    assert (reply.status, reply.products, reply.sources) == ("limited", [], [])
    assert router.states == [message]
    assert consultant.provider.calls == []
    assert len(consultant.provider.luna) == 2
    assert await runs(consultant) == 2


async def test_snapshot_failure_on_a_safety_turn_is_the_failure_response(test_db):
    # SF-07: an emergency or check turn without a knowledge snapshot ends with the failure
    # response and no Luna call; a non-safety turn keeps knowledge_unavailable (FR-03).
    await seed(test_db)
    consultant = bot(test_db)
    async with consultant.controls.sessions.begin() as db:
        (await db.get(Control, 1)).active = None
    with pytest.raises(Fault) as fault:
        await say(consultant, "давит в груди")
    assert (fault.value.code, fault.value.status) == ("answer_unavailable", 503)
    assert consultant.provider.luna == []
    with pytest.raises(Fault, match="knowledge_unavailable"):
        await say(consultant, "часто болею")


async def test_named_product_question_with_a_cold_word_keeps_the_existing_path(test_db):
    # review r2 NB3 false positive on a product question; D31: only the ambulance list runs.
    await seed(test_db)
    router = OpenRouter()
    consultant = bot(test_db, router)
    reply = await say(consultant, "Как давать IMMUNOCOMPLEX® KIDS, у сына нос не дышит")
    assert router.calls == []
    assert consultant.provider.calls
    assert not any(reply.answer in variants(t) for t in ("emergency.general", "emergency.check"))


async def test_offer_envelope_and_follow_up_on_the_existing_path(test_db):
    await seed(test_db)
    message = "сын часто болеет, ему 5"
    router = OpenRouter({message: "immunity"})
    consultant = bot(test_db, router)
    first = await say(consultant, message)
    kids = graf.PRODUCT["immunocomplex_kids"]["shop_id"]
    assert first.status == "answer"
    assert [p["id"] for p in first.products] == [kids]
    assert first.commercial_checked_at is not None
    assert first.open_product_id is None
    public = {"product_id", "caution", "ordinal"}
    assert all(not (public & set(s)) for s in first.sources)
    assert set(first.cautions) <= {s["source_id"] for s in first.sources}
    assert first.cautions == [f"g{kids}ru:2"]
    assert Tokens(KEY).read(turn("x", first))[0] == [kids]
    consultant.provider.script = [answer(intent=("follow_up", "high"))]
    follow = await say(consultant, "как принимать?", first, [(message, first.answer)])
    # A displayed selection makes a post-offer turn: Graf's brief, then Luna's follow-up
    # reading sends it to the existing path (PO-03).
    assert router.states == [message, "как принимать?"]
    assert consultant.provider.calls
    assert follow.status in {"answer", "clarification", "limited", "refused"}


async def test_ordinal_reply_continues_before_resolve(test_db):
    # review r1 B2.4: resolve("второй день") clarifies; the continuation runs before it.
    await seed(test_db)
    message = "у дочки 5 лет кашель"
    consultant = bot(test_db, OpenRouter({message: "acute_cold"}))
    first = await say(consultant, message)
    second = await say(consultant, "второй день", first, [(message, first.answer)])
    assert [p["id"] for p in second.products] == [graf.PRODUCT["immunocomplex_kids"]["shop_id"]]
    assert consultant.provider.calls == []


async def test_check_answered_no_continues_the_checked_named_product_question(test_db):
    await seed(test_db)
    router = OpenRouter()
    consultant = bot(test_db, router)
    message = "Как давать IMMUNOCOMPLEX® KIDS, у дочки судороги"
    check = await say(consultant, message)
    assert check.answer in variants("emergency.check")
    assert consultant.provider.calls == []
    await say(consultant, "нет", check, [(message, check.answer)])
    (call,) = [c for c in consultant.provider.calls if c[0] == "response"][:1]
    assert json.loads(call[1][0]["content"])["question"] == message
    assert router.calls == []


async def test_treat_words_change_from_refusal_to_graf_only_while_graf_is_on(test_db):
    await seed(test_db)
    message = "чем лечить кашель ребенку 5 лет"
    off = await say(bot(test_db, on=False), message)
    assert off.answer == REFUSED["ru"]
    on = await say(bot(test_db, OpenRouter({message: "acute_cold"})), message)
    assert on.status == "clarification"
    assert any(v in on.answer for v in variants("ask.duration"))


async def test_graf_off_keeps_the_existing_answer_for_a_danger_text(test_db):
    await seed(test_db)
    consultant = bot(test_db, on=False)
    reply = await say(consultant, "давит в груди")
    assert reply.answer not in variants("emergency.general")
    assert consultant.provider.calls


async def test_offer_with_cards_missing_from_the_snapshot_is_the_unavailable_text(test_db):
    await seed(test_db)
    message = "понос, мне 30"
    consultant = bot(test_db, OpenRouter({message: "diarrhea"}))
    async with consultant.controls.sessions.begin() as db:
        for card in ("active_caps", "zincum_stick"):
            (await db.get(Product, graf.PRODUCT[card]["shop_id"])).is_active = False
    reply = await say(consultant, message)
    assert reply.answer in variants("no_product.unavailable")
    assert reply.products == []


@pytest.mark.parametrize(("key", "on"), [("fixture-graf-key", True), ("", False)])
async def test_runtime_turns_graf_on_only_with_its_key(test_db, key, on):
    config = SimpleNamespace(
        consultant_provider_key=SimpleNamespace(get_secret_value=lambda: "provider"),
        consultant_protection_key=SimpleNamespace(get_secret_value=lambda: KEY),
        consultant_graf_key=SimpleNamespace(get_secret_value=lambda: key),
        consultant_origins=[],
        consultant_hybrid=True,
    )
    app = SimpleNamespace(state=SimpleNamespace())
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    async with runtime(app, config, sessions):
        assert (app.state.consultant.graf is not None) is on
        if on:
            assert app.state.consultant.graf.jev.key == key


@pytest.mark.parametrize("earlier", [0, 20])
async def test_graf_flow_after_a_long_history_matches_a_fresh_one(test_db, earlier):
    # Graf assumed the website's 4-pair window; the API now accepts any history length.
    await seed(test_db)
    message = "сын часто болеет"
    router = OpenRouter({message: "immunity"})
    consultant = bot(test_db, router)
    tab = uuid4()
    token, _ = consultant.tokens.issue(tab, "ru", [], "long-history")
    previous = SimpleNamespace(tab_id=tab, token=token)
    history = [(f"Вопрос {n} про витамины", f"Ответ {n} про витамины.") for n in range(earlier)]
    first = await say(consultant, message, previous if history else None, history)
    assert first.status == "clarification"
    history.append((message, first.answer))
    second = await say(consultant, "ему 5", first, history)
    kids = graf.PRODUCT["immunocomplex_kids"]["shop_id"]
    assert second.status == "answer"
    assert [p["id"] for p in second.products] == [kids]
    assert router.states == [message]
    assert len(history) == earlier + 1
