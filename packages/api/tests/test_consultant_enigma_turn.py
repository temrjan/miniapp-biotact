"""The Enigma turn (consultant-v2 spec r2, PR-2) inside Consultant.turn on the real test
database: the switch and the routing, the Luna answer and its checks, the cards, the "more"
turn and the record. Jev is a double at its HTTP boundary, Luna at Provider.call. Each test
names the requirement it proves."""

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import Settings
from src.consultant import graf
from src.consultant.context import Tokens
from src.consultant.contracts import Fault, Turn
from src.consultant.control import Controls
from src.consultant.enigma import checks, luna
from src.consultant.enigma.data import DATA, RULES
from src.consultant.enigma.jev import Jev, read
from src.consultant.enigma.turn import Enigma, named
from src.consultant.knowledge import Knowledge
from src.consultant.router import runtime
from src.consultant.service import Consultant
from src.journal.models import JournalTurn
from src.journal.writer import Journal
from src.main import create_app
from src.products.models import Product
from tests.test_consultant import KEY
from tests.test_consultant_enigma_core import answer
from tests.test_consultant_graf_fakes import LunaStub, body, echo
from tests.test_consultant_graf_flow import OpenRouter
from tests.test_consultant_graf_service import seed as supplements

SENTENCE = RULES.instruction_sentence.ru
QUESTION = RULES.opening_question.ru
OFFER = (
    "IMMUNOCOMPLEX® и IMMUNOCOMPLEX® KIDS поддерживают иммунитет, а BIFOLAK® ZINCUM содержит "
    "цинк. Могу показать ещё. " + SENTENCE
)


async def seed(db: Any) -> None:
    """Supplements 16, 17, 7, 14, 20 in the knowledge release; item products in the catalogue."""
    await supplements(db)
    db.add_all(
        [
            Product(id=36, name="Рюкзак серый Healthy Lifestyle", slug="r36", price=1),
            Product(id=37, name="Рюкзак зеленый Healthy Lifestyle", slug="r37", price=1),
        ]
    )
    await db.commit()


class JevDouble:
    """Scripted Jev bodies, one per request; the last one repeats."""

    def __init__(self, *bodies: Any) -> None:
        self.bodies = list(bodies)
        self.states: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.states.append(json.loads(request.content)["state"])
        step = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, json=step)


def say(decision: str, text: str, *offered: str) -> Any:
    """A Luna step: the Enigma answer for an Enigma brief, today's echo for any other."""

    def step(brief: dict[str, Any]) -> dict[str, Any]:
        if "candidates" not in brief:
            return echo(brief)
        return {"decision": decision, "offered": list(offered), "text": text}

    return step


def bot(db: Any, jev: JevDouble, *script: Any, engine: bool = True) -> Consultant:
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    old = httpx.AsyncClient(transport=httpx.MockTransport(OpenRouter().handler))
    new = httpx.AsyncClient(transport=httpx.MockTransport(jev.handler))
    consultant = Consultant(
        Controls(sessions, KEY, True),
        Knowledge(sessions),
        LunaStub(list(script)),
        Tokens(KEY),
        graf.Graf(graf.Jev(old, "fixture-key")),
        True,
        Enigma(Jev(new, "fixture-key")) if engine else None,
    )
    consultant.records = []  # type: ignore[attr-defined]
    consultant.recorder = consultant.records.append  # type: ignore[attr-defined]
    return consultant


def turn(
    question: str,
    token: str | None = None,
    tab: Any = None,
    history: tuple[tuple[str, str], ...] = (),
) -> Turn:
    return Turn.model_validate(
        {
            "request_id": uuid4(),
            "tab_id": tab or uuid4(),
            "context_revision": 1,
            "language": "ru",
            "question": question,
            "history": [{"question": q, "answer": a} for q, a in history],
            "page_product_id": None,
            "context_token": token,
        }
    )


def shown(consultant: Consultant, ids: list[int]) -> tuple[Any, str]:
    """A tab and its token with these products displayed."""
    tab = uuid4()
    return tab, consultant.tokens.issue(tab, "ru", ids, str(uuid4()))[0]


# --- SW-01: the switch -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("engine", "hybrid", "on"),
    [(None, True, False), ("graf", True, False), ("enigma", True, True), ("enigma", False, False)],
)
async def test_sw01_setting_selects_the_engine(
    test_db: Any, engine: str | None, hybrid: bool, on: bool
) -> None:
    assert Settings.model_fields["consultant_engine"].default == "graf"
    secret = SimpleNamespace(get_secret_value=lambda: KEY)
    config = SimpleNamespace(
        consultant_provider_key=secret,
        consultant_protection_key=secret,
        consultant_graf_key=secret,
        consultant_origins=[],
        consultant_hybrid=hybrid,
    )
    if engine is not None:
        config.consultant_engine = engine
    app = SimpleNamespace(state=SimpleNamespace())
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    async with runtime(app, config, sessions):
        assert (app.state.consultant.enigma is not None) is on


async def test_sw01_default_engine_answers_on_the_existing_path(test_db: Any) -> None:
    await seed(test_db)
    jev = JevDouble(answer("immunocomplex"))
    consultant = bot(test_db, jev, engine=False)
    await consultant.turn(turn("часто болею, слабый иммунитет"))
    (record,) = consultant.records  # type: ignore[attr-defined]
    assert record.path == "hybrid"
    assert "engine" not in record.versions
    assert jev.states == []


# --- SW-03, SW-04: the route -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "selection", "path"),
    [
        ("IMMUNOCOMPLEX, давит в груди отдает в левую руку", [], "enigma"),
        ("IMMUNOCOMPLEX", [], "product"),
        ("как оформить заказ?", [], "hybrid"),
        ("плохо сплю", [], "enigma"),
        ("первый", [36, 37], "enigma"),
    ],
    ids=["ambulance with a name", "named product", "how to buy", "complaint", "ordinal on items"],
)
async def test_sw03_route(test_db: Any, question: str, selection: list[int], path: str) -> None:
    await seed(test_db)
    text = RULES.emergency.general.ru if "груди" in question else QUESTION
    decision = "emergency" if "груди" in question else "ask"
    consultant = bot(test_db, JevDouble(answer("unclear")), say(decision, text))
    tab, token = shown(consultant, selection) if selection else (None, None)
    await consultant.turn(turn(question, token, tab))
    assert consultant.records[0].path == path  # type: ignore[attr-defined]


async def test_sw04_a_name_without_a_complaint_goes_to_the_product_path(test_db: Any) -> None:
    await seed(test_db)
    jev = JevDouble(answer("unclear", name="zincum"))
    consultant = bot(test_db, jev)
    subjects: list[list[int]] = []
    product = consultant.product

    async def spy(*args: Any) -> dict[str, Any]:
        subjects.append(args[4].ids)
        return await product(*args)

    consultant.product = spy  # type: ignore[method-assign]
    await consultant.turn(turn("цинкум стики"))
    assert consultant.records[0].path == "product"  # type: ignore[attr-defined]
    assert subjects == [[17]]
    assert consultant.provider.luna == []  # type: ignore[attr-defined]
    shared = read(answer("unclear", name="magniy"))
    assert named(shared, "магний") == [18, 19]  # type: ignore[arg-type]
    assert named(shared, "магний стики") == [19]  # type: ignore[arg-type]


# --- FT-05…FT-07, SW-05 and the user path: complaint, offer, "more", next cards ---------------


async def test_user_path_offer_then_more_shows_the_next_cards(test_db: Any) -> None:
    await seed(test_db)
    jev = JevDouble(answer("immunocomplex", zincum=0.3))
    offer = say("offer", OFFER, "immunocomplex", "zincum")
    consultant = bot(test_db, jev, offer, offer, say("ask", QUESTION))
    app = create_app()
    app.state.consultant = consultant
    app.state.consultant_origins = ["https://shop.test"]
    first = turn("часто болею")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://shop.test"
    ) as client:
        one = await client.post(
            "/api/v1/consultant/turns", json=json.loads(first.model_dump_json())
        )
        assert one.status_code == 200
        a = one.json()
        # FT-05, FT-06: three offered products, the first two shown.
        assert (a["status"], [p["id"] for p in a["products"]]) == ("answer", [7, 14])
        assert (a["sources"], a["cautions"], a["open_product_id"]) == ([], [], None)
        second = turn("ещё", a["context_token"], first.tab_id, (("часто болею", a["answer"]),))
        # SW-05: the token holds the shown ids and no flow state or pending check.
        context = consultant.tokens.read(second)
        assert (context.ids, context.flow, context.check) == ([7, 14], None, None)
        two = await client.post(
            "/api/v1/consultant/turns", json=json.loads(second.model_dump_json())
        )
        # FT-07: the "more" turn shows the third product; Jev read the complaint again.
        assert [p["id"] for p in two.json()["products"]] == [17]
        assert jev.states[1] == {"earlier": "", "message": "часто болею"}
    # FT-07: without a displayed selection "ещё" is an ordinary message; FT-06: a question.
    third = await consultant.turn(turn("ещё"))
    assert jev.states[2]["message"] == "ещё"
    assert (third["status"], third["products"]) == ("clarification", [])


async def test_ft05_an_item_offer_shows_the_item_card(test_db: Any) -> None:
    await seed(test_db)
    text = "Рюкзак Healthy Lifestyle подойдёт для города. " + SENTENCE
    consultant = bot(
        test_db, JevDouble(answer("unclear", item="ryukzak")), say("offer", text, "ryukzak")
    )
    response = await consultant.turn(turn("нужен рюкзак"))
    assert [p["id"] for p in response["products"]] == [36, 37]
    # FT-06: a doctor answer has no cards and an empty selection.
    doctor = bot(test_db, JevDouble(answer("doctor")), say("doctor", "Обратитесь к врачу."))
    tab, token = shown(doctor, [7])
    after = await doctor.turn(turn("кровь в моче", token, tab))
    assert (after["status"], after["products"]) == ("limited", [])
    assert doctor.tokens.read(turn("x", after["context_token"], tab)).ids == []
    # SW-06, FT-06: a question keeps the displayed item cards.
    keep = bot(test_db, JevDouble(answer("unclear")), say("ask", QUESTION))
    tab, token = shown(keep, [36, 37])
    kept = await keep.turn(turn("а он большой?", token, tab))
    assert (kept["status"], [p["id"] for p in kept["products"]]) == ("clarification", [36, 37])
    # SW-03 (c): a how-to-buy turn after item cards keeps them too.
    buy = bot(test_db, JevDouble(answer("unclear")))
    tab, token = shown(buy, [36, 37])
    bought = await buy.turn(turn("как оформить заказ?", token, tab))
    assert [p["id"] for p in bought["products"]] == [36, 37]
    assert buy.tokens.read(turn("x", bought["context_token"], tab)).ids == [36, 37]


# --- SW-06: refresh of an item product ---------------------------------------------------------


@pytest.mark.parametrize("engine", [True, False])
async def test_sw06_refresh_of_an_item_product(test_db: Any, engine: bool) -> None:
    await seed(test_db)
    app = create_app()
    app.state.consultant = bot(test_db, JevDouble(answer("unclear")), engine=engine)
    app.state.consultant_origins = ["https://shop.test"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://shop.test"
    ) as client:
        response = await client.post(
            "/api/v1/consultant/products/refresh", json={"product_ids": [36], "language": "ru"}
        )
    result = response.json()
    assert [p["id"] for p in result["products"]] == ([36] if engine else [])
    assert result["unavailable_ids"] == ([] if engine else [36])


# --- J-04: a Jev error -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, content=json.dumps(answer("magniy")).encode() + b" " * 70000),
        httpx.Response(200, json=[]),
        {"model": "typesafe/jev-1.14", "answers": {"unit": {"type": "refusal"}}},
        {**answer("magniy"), "model": "typesafe/jev-2"},
    ],
    ids=["status", "timeout", "size", "invalid", "refusal", "model"],
)
async def test_j04_a_jev_error_ends_with_the_failure_response(test_db: Any, failure: Any) -> None:
    await seed(test_db)
    consultant = bot(test_db, JevDouble(failure))
    with pytest.raises(Fault) as raised:
        await consultant.turn(turn("плохо сплю"))
    assert (raised.value.code, raised.value.status) == ("answer_unavailable", 503)
    assert consultant.provider.luna == []  # type: ignore[attr-defined]
    # An ambulance message still reaches Luna, without candidates.
    text = RULES.emergency.general.ru
    urgent = bot(test_db, JevDouble(failure), say("emergency", text))
    response = await urgent.turn(turn("давит в груди отдает в левую руку"))
    assert (response["status"], response["answer"]) == ("limited", text)
    assert urgent.provider.briefs[0]["candidates"] == []  # type: ignore[attr-defined]


# --- FT-03: Luna's answer ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "failure"),
    [
        (body({"decision": "offer", "offered": ["magniy"], "text": "x"}), None),
        ({**body({}), "status": "incomplete"}, "incomplete"),
        (
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal"}]}],
            },
            "refusal",
        ),
        (body({"decision": "offer", "offered": ["magniy"]}), "invalid"),
        (body({"decision": "offer", "offered": [], "text": "x"}), "invalid"),
        (body({"decision": "offer", "offered": ["neo"], "text": "x"}), "invalid"),
        (body({"decision": "sell", "offered": [], "text": "x"}), "invalid"),
    ],
)
def test_ft03_parser(response: dict[str, Any], failure: str | None) -> None:
    parsed = luna.parse(response, frozenset({"magniy"}))
    assert parsed.failure == failure
    assert (parsed.answer is None) is (failure is not None)


# --- FT-08: the second call --------------------------------------------------------------------


async def test_ft08_one_second_call_after_a_failed_text(test_db: Any) -> None:
    await seed(test_db)
    dose = say("ask", "Принимайте по 2 капсулы. Что беспокоит?")
    consultant = bot(test_db, JevDouble(answer("magniy")), dose, say("ask", QUESTION))
    response = await consultant.turn(turn("плохо сплю"))
    assert response["answer"] == QUESTION
    assert consultant.provider.briefs[1]["retry"] == {"failed_check": "V-03"}  # type: ignore[attr-defined]
    twice = bot(test_db, JevDouble(answer("magniy")), dose, dose, say("ask", QUESTION))
    with pytest.raises(Fault) as raised:
        await twice.turn(turn("плохо сплю"))
    assert raised.value.status == 503
    assert len(twice.provider.luna) == 2  # type: ignore[attr-defined]


# --- V-01…V-07: the text checks ----------------------------------------------------------------

NAMES = checks.Names.of({7: ["IMMUNOCOMPLEX"], 14: ["IMMUNOCOMPLEX KIDS"], 17: ["BIFOLAK ZINCUM"]})
UZ = RULES.instruction_sentence.uz


@pytest.mark.parametrize(
    ("decision", "text", "allowed", "expected"),
    [
        ("ask", "   ", [], "V-01"),
        ("ask", "Смотрите https://biotact.uz", [], "V-01"),
        ("ask", "<b>Что</b> беспокоит?", [], "V-01"),
        ("ask", "**Что** беспокоит?", [], "V-01"),
        ("ask", "Варианты:\n- сон\n- стресс", [], "V-01"),
        ("ask", "Напишите на mail@biotact.uz", [], "V-01"),
        ("ask", "What bothers you?", [], "V-01"),
        ("ask", "По данным jev вам подойдёт отдых.", [], "V-01"),
        ("uz ask", "Это средство для детей.", [], "V-01"),
        ("uz ask", "Sizni nima bezovta qilyapti?", [], None),
        ("ask", "Что именно беспокоит?", [], None),
        ("offer", "IMMUNOCOMPLEX® KIDS подойдёт. " + SENTENCE, [7], "V-02"),
        ("offer", "Это поддержит иммунитет. " + SENTENCE, [7], "V-02"),
        ("offer", "IMMUNOCOMPLEX® поддерживает иммунитет. " + SENTENCE, [7], None),
        ("offer", "Рюкзак Healthy Lifestyle вместительный. " + SENTENCE, [36, 37], None),
        ("ask", "Вам показать IMMUNOCOMPLEX®?", [], "V-02"),
        ("ask", "Вам подошёл IMMUNOCOMPLEX®?", [7], None),
        ("doctor", "IMMUNOCOMPLEX® не поможет, идите к врачу.", [7], "V-02"),
        ("doctor", "С этим стоит обратиться к врачу.", [], None),
        ("emergency", "Примите IMMUNOCOMPLEX® и звоните 103.", [7], "V-02"),
        ("ask", "Принимайте по 2 капсулы.", [], "V-03"),
        ("ask", "Принимайте по 1капсуле 2раза в день.", [], "V-03"),
        ("uz ask", "Kuniga ikki mahal iching.", [], "V-03"),
        ("ask", "Он есть в капсулах и стиках.", [], None),
        ("offer", "Термос KD-775, 600 мл. хорошо держит тепло. " + SENTENCE, [59], None),
        ("emergency", "Срочно звоните 112.", [], "V-04"),
        ("emergency", "Срочно звоните 103.", [], None),
        ("ask", "Так делают уже 25 лет. Что беспокоит?", [], "V-04"),
        ("offer", "IMMUNOCOMPLEX® поддерживает иммунитет.", [7], "V-05"),
        ("uz offer", "IMMUNOCOMPLEX® immunitetni qo'llab-quvvatlaydi. " + UZ, [7], None),
        ("ask", "Это вылечит простуду.", [], "V-06"),
        ("ask", "Подходит после лечения кариеса.", [], None),
        ("doctor", RULES.phrases["disease"].ru, [], None),
        ("ask", "Сколько лет ребёнку?", [], "V-07"),
        ("ask", "Вы беременны?", [], "V-07"),
    ],
)
def test_v01_v07_text_checks(
    decision: str, text: str, allowed: list[int], expected: str | None
) -> None:
    language, _, kind = decision.rpartition(" ")
    subject = checks.Subject(
        "uz" if language else "ru",
        kind,
        frozenset(allowed),
        frozenset(allowed),
        frozenset(),
        (RULES.phrases["disease"].ru, "Взрослые – после лечения кариеса."),
    )
    assert checks.check(text, subject, NAMES) == expected


# --- REC-01: the record in the journal tables --------------------------------------------------


async def test_rec01_enigma_turns_in_the_journal(test_db: Any) -> None:
    await seed(test_db)
    text = "IMMUNOCOMPLEX® поддерживает иммунитет, а BIFOLAK® ZINCUM содержит цинк. " + SENTENCE
    offer = say("offer", text, "immunocomplex", "zincum")
    consultant = bot(
        test_db,
        JevDouble(answer("immunocomplex", zincum=0.3), httpx.Response(500)),
        offer,
    )
    await consultant.turn(turn("я беременна, часто болею"))
    with pytest.raises(Fault):
        await consultant.turn(turn("плохо сплю"))
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    journal = Journal(sessions)
    for record in consultant.records:  # type: ignore[attr-defined]
        await journal.write(record)
    async with sessions() as db:
        delivered, failed = list(await db.scalars(select(JournalTurn).order_by(JournalTurn.id)))
    assert (delivered.path, delivered.graf_outcome, delivered.situation, delivered.template) == (
        "enigma",
        "offer",
        "immunocomplex",
        None,
    )
    # The pregnancy rule removed IMMUNOCOMPLEX KIDS (14) before the offer.
    assert delivered.offered_product_ids == [7, 17]
    assert delivered.versions["engine"] == "enigma"
    assert delivered.versions["enigma_data"] == DATA
    assert delivered.jev["unit"][0] == ["immunocomplex", 0.6]
    assert delivered.jev["error"] is None
    assert (delivered.luna["decision"], delivered.luna["offered"]) == (
        "offer",
        ["immunocomplex", "zincum"],
    )
    assert delivered.luna["calls"][0]["check_id"] is None
    assert delivered.facts["pregnancy"] == [0]
    assert (failed.path, failed.http_status, failed.jev["error"]) == ("failure", 503, "status")
    assert failed.versions["engine"] == "enigma"


# --- FT-04: a removed product is not in the brief and may not be named -------------------------


async def test_ft04_a_removed_product_is_absent_from_the_brief_and_fails_v02(test_db: Any) -> None:
    await seed(test_db)
    good = "IMMUNOCOMPLEX® поддерживает иммунитет. " + SENTENCE
    consultant = bot(
        test_db,
        JevDouble(answer("immunocomplex")),
        say("offer", OFFER, "immunocomplex"),
        say("offer", good, "immunocomplex"),
    )
    response = await consultant.turn(turn("я беременна, часто болею"))
    first, second = consultant.provider.briefs  # type: ignore[attr-defined]
    (candidate,) = first["candidates"]
    assert [p["name"] for p in candidate["products"]] == ["IMMUNOCOMPLEX®"]
    assert candidate["pair_line"] is None
    assert second["retry"] == {"failed_check": "V-02"}
    assert [p["id"] for p in response["products"]] == [7]
