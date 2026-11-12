"""The Enigma turn (consultant-v2 spec r2, PR-2) inside Consultant.turn on the real test
database: the engine without a key and the routing, the Luna answer and its checks, the cards, the "more"
turn and the record. Jev is a double at its HTTP boundary, Luna at Provider.call. Each test
names the requirement it proves."""

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.consultant import record as recording
from src.consultant import service
from src.consultant.context import Tokens
from src.consultant.contracts import Fault, Turn
from src.consultant.control import Controls
from src.consultant.enigma import checks, luna, nets
from src.consultant.enigma.data import DATA, RULES
from src.consultant.enigma.jev import Jev, read
from src.consultant.enigma.turn import Enigma, named
from src.consultant.knowledge import Knowledge
from src.consultant.models import Run
from src.consultant.provider import PROFILE
from src.consultant.router import runtime
from src.consultant.service import Consultant
from src.journal.models import JournalTurn
from src.journal.writer import Journal
from src.main import create_app
from src.products.models import Product
from tests.consultant_doubles import LunaStub, body, echo
from tests.consultant_doubles import seed as supplements
from tests.test_consultant import KEY
from tests.test_consultant_enigma_core import answer

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
    client = httpx.AsyncClient(transport=httpx.MockTransport(jev.handler))
    consultant = Consultant(
        Controls(sessions, KEY, True),
        Knowledge(sessions),
        LunaStub(list(script)),
        Tokens(KEY),
        Enigma(Jev(client, "fixture-key")) if engine else None,
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


# --- G4-02, G4-03: the engine and the no-key state ---------------------------------------------


@pytest.mark.parametrize("key", [KEY, ""])
async def test_g403_without_the_jev_key_every_turn_gets_the_failure_response(
    test_db: Any, key: str
) -> None:
    """graf-removal spec r3 G4-02, G4-03 (C-75): the runtime builds Enigma only with its Jev
    key; without it a turn ends with 503 answer_unavailable before admission, no model call."""
    await seed(test_db)
    secret = SimpleNamespace(get_secret_value=lambda: KEY)
    config = SimpleNamespace(
        consultant_provider_key=secret,
        consultant_protection_key=secret,
        consultant_graf_key=SimpleNamespace(get_secret_value=lambda: key),
        consultant_origins=[],
    )
    app = SimpleNamespace(state=SimpleNamespace())
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    async with runtime(app, config, sessions):
        consultant = app.state.consultant
        assert (consultant.enigma is not None) is bool(key)
        if key:
            return
        consultant.provider = LunaStub([])
        records: list[Any] = []
        consultant.recorder = records.append
        with pytest.raises(Fault) as fault:
            await consultant.turn(turn("часто болею, слабый иммунитет"))
    assert (fault.value.code, fault.value.status) == ("answer_unavailable", 503)
    (record,) = records
    assert (record.path, record.http_status) == ("failure", 503)
    assert record.versions == {"knowledge": None, "profile": PROFILE}
    assert consultant.counters.counts["failure.no_engine"] == 1
    assert consultant.provider.calls == consultant.provider.luna == []
    # No admission: the turn registered nothing.
    async with sessions() as db:
        assert (await db.execute(select(Run))).scalars().all() == []


# --- SW-03, SW-04: the route -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "selection", "path"),
    [
        ("IMMUNOCOMPLEX, давит в груди отдает в левую руку", [], "enigma"),
        ("IMMUNOCOMPLEX", [], "product"),
        # spec.shop-core r4 SR-01: how-to-buy is an Enigma turn.
        ("как оформить заказ?", [], "enigma"),
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
        # SW-05: the token holds the shown ids.
        context = consultant.tokens.read(second)
        assert context.ids == [7, 14]
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
    # spec.shop-core r4 SO-01: a how-to-buy turn after item cards keeps them too.
    buy = bot(
        test_db,
        JevDouble(answer("unclear", topic="how_to_buy")),
        say("shop", "Нажмите «В корзину» на карточке нужного товара."),
    )
    tab, token = shown(buy, [36, 37])
    bought = await buy.turn(turn("как оформить заказ?", token, tab))
    assert (bought["status"], [p["id"] for p in bought["products"]]) == ("answer", [36, 37])
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
    # G4-06: the versions of an Enigma turn, exactly; no graf_data key.
    assert delivered.versions == {
        "knowledge": "graf-release",
        "profile": PROFILE,
        "engine": "enigma",
        "enigma_data": DATA,
    }
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


async def test_fr01_the_deadline_on_an_enigma_turn_is_the_failure_response(
    test_db: Any, monkeypatch: Any
) -> None:
    """graf-removal spec r3 G4-05, spec r2 FR-01 (restated from the former hybrid test): the
    turn deadline on an Enigma turn is the failure response, not 504 timeout."""
    await seed(test_db)
    consultant = bot(test_db, JevDouble(answer("immunocomplex")))
    monkeypatch.setattr(service, "DEADLINE", 0.2)
    consultant.provider.hold = asyncio.Event()  # Luna never answers; its own limit is 16 s
    with pytest.raises(Fault) as fault:
        await consultant.turn(turn("часто болею"))
    assert (fault.value.code, fault.value.status) == ("answer_unavailable", 503)
    (record,) = consultant.records  # type: ignore[attr-defined]
    assert (record.path, record.http_status) == ("failure", 503)
    assert record.luna["failure_trigger"] == "deadline"
    assert consultant.counters.counts["failure.deadline"] == 1


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


# --- Enigma-mode safety tests before the Graf removal changes the service (C-76) --------------

# The approved contacts text of the shop (Graf template conditions.contacts, RU), written out so
# that the test does not depend on Graf data.
CONTACTS = (
    "Если у Вас есть вопросы, можете обратиться\nпо номеру: 998 93 555 17 47\n"
    "или написать в телеграм: @Biotact_uz"
)


async def test_ct06_the_shop_phone_in_an_earlier_answer_is_not_sensitive_input(
    test_db: Any,
) -> None:
    # spec r7 CT-06 in Enigma mode: a delivered contacts answer does not block the next turn;
    # another phone in an answer, and the shop phone in the current question, give 422.
    await seed(test_db)
    consultant = bot(test_db, JevDouble(answer("unclear")), say("ask", QUESTION))
    tab, token = shown(consultant, [])
    reply = await consultant.turn(
        turn("что посоветуете?", token, tab, (("как с вами связаться?", CONTACTS),))
    )
    assert (reply["status"], consultant.records[-1].path) == (  # type: ignore[attr-defined]
        "clarification",
        "enigma",
    )
    other = (("как с вами связаться?", "Позвоните по номеру 998 90 111 22 33."),)
    for history, question in (
        (other, "что посоветуете?"),
        ((("как с вами связаться?", CONTACTS),), "ваш номер 998 93 555 17 47?"),
    ):
        with pytest.raises(Fault) as fault:
            await consultant.turn(turn(question, token, tab, history))
        assert (fault.value.code, fault.value.status) == ("sensitive_input", 422)


async def test_rec02_counters_have_no_text_and_are_logged_by_the_runtime_task(
    test_db: Any, caplog: Any
) -> None:
    await seed(test_db)
    text = "IMMUNOCOMPLEX® поддерживает иммунитет. " + SENTENCE
    failure = Fault("provider_unavailable", 502)
    consultant = bot(
        test_db,
        JevDouble(answer("immunocomplex")),
        say("offer", text, "immunocomplex"),
        failure,
        failure,
    )
    await consultant.turn(turn("часто болею"))
    with pytest.raises(Fault, match="answer_unavailable"):
        await consultant.turn(turn("плохо сплю и устаю"))
    counts = dict(consultant.counters.counts)
    assert (counts["turns.enigma"], counts["turns.failure"], counts["failure.luna"]) == (1, 1, 1)
    assert counts["luna.outcome.provider_error"] == 2
    caplog.set_level(logging.INFO, logger="src.consultant.record")
    task = asyncio.create_task(recording.report(consultant.counters, 0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    lines = [r.getMessage() for r in caplog.records if r.name == "src.consultant.record"]
    assert lines
    assert "turns.enigma=1" in lines[0]
    for secret in ("болею", "устаю", "иммунитет", "IMMUNOCOMPLEX"):
        assert secret not in lines[0]


async def test_the_recorder_runs_once_per_turn_and_a_raising_recorder_changes_nothing(
    test_db: Any,
) -> None:
    await seed(test_db)
    ask = say("ask", QUESTION)
    consultant = bot(test_db, JevDouble(answer("unclear")), ask, ask)
    with pytest.raises(Fault, match="context_invalid"):
        await consultant.turn(turn("что посоветуете?", "broken.token", None, (("x", "y"),)))
    reply = await consultant.turn(turn("что посоветуете?"))
    records = consultant.records  # type: ignore[attr-defined]
    assert [(r.path, r.http_status, r.fault_code) for r in records] == [
        ("failure", 400, "context_invalid"),
        ("enigma", 200, None),
    ]
    assert (records[0].versions, records[0].jev, records[0].luna) == (None, None, None)
    assert records[1].answer == reply["answer"]

    def broken(record: Any) -> None:
        raise RuntimeError("recorder")

    consultant.recorder = broken
    again = await consultant.turn(turn("что посоветуете?"))
    assert (again["status"], again["answer"]) == (reply["status"], reply["answer"])


async def test_journal_rows_equal_the_records_on_every_enigma_mode_path(test_db: Any) -> None:
    # JW-02, JW-06 in Enigma mode: each path a turn can take is written to the real journal
    # tables with the record's value in every mapped column (restated from the former Graf
    # journal test in Graf removal step 4).
    await seed(test_db)
    offer = "IMMUNOCOMPLEX® поддерживает иммунитет. " + SENTENCE
    cases = [
        (JevDouble(answer("immunocomplex")), say("offer", offer, "immunocomplex"), "часто болею"),
        (
            JevDouble(answer("unclear")),
            say("emergency", RULES.emergency.general.ru),
            "давит в груди отдает в левую руку",
        ),
        (JevDouble(answer("unclear")), say("ask", QUESTION), "IMMUNOCOMPLEX"),
        (
            JevDouble(answer("unclear", topic="how_to_buy")),
            say("shop", "Нажмите «В корзину» на карточке нужного товара."),
            "как оформить заказ?",
        ),
        (JevDouble(httpx.Response(500)), say("ask", QUESTION), "плохо сплю"),
    ]
    records: list[Any] = []
    for jev, step, question in cases:
        consultant = bot(test_db, jev, step)
        with contextlib.suppress(Fault):  # the failure case ends with 503
            await consultant.turn(turn(question))
        records += consultant.records  # type: ignore[attr-defined]
    # The product path's 504 when the provider times out.
    consultant = bot(test_db, JevDouble(answer("unclear")))

    async def slow(inputs: Any, last: Any) -> Any:
        raise TimeoutError

    consultant.provider.response = slow  # type: ignore[method-assign]
    with pytest.raises(Fault, match="timeout"):
        await consultant.turn(turn("IMMUNOCOMPLEX"))
    records += consultant.records  # type: ignore[attr-defined]
    assert [(r.path, r.graf_outcome, r.http_status) for r in records] == [
        ("enigma", "offer", 200),
        ("enigma", "emergency", 200),
        ("product", None, 200),
        ("enigma", "shop", 200),
        ("failure", None, 503),
        ("product", None, 504),
    ]
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    journal = Journal(sessions)
    for record in records:
        await journal.write(record)
    async with sessions() as db:
        stored = list(await db.scalars(select(JournalTurn).order_by(JournalTurn.id)))
    assert len(stored) == len(records)

    def plain(value: Any) -> Any:
        return None if value is None else json.loads(json.dumps(value))

    for row, record in zip(stored, records, strict=True):
        assert (
            str(row.request_id),
            str(row.tab_id),
            row.created_at,
            row.template,
            row.asked_question,
            row.resolution,
            row.tokens,
            row.opyt,
            row.memory,
            row.question,
            row.answer,
            row.http_status,
            row.fault_code,
            row.response_status,
            row.path,
            row.graf_outcome,
            row.situation,
            row.offered_product_ids,
            row.jev,
            row.luna,
            row.facts,
            row.versions,
        ) == (
            record.request_id,
            record.tab_id,
            record.created,
            record.template,
            None,
            None,
            nets.tokens(record.question),
            None,
            None,
            record.question,
            record.answer,
            record.http_status,
            record.fault_code,
            record.response_status,
            record.path,
            record.graf_outcome,
            record.situation,
            record.offered_product_ids or [],
            plain(record.jev),
            plain(record.luna),
            plain(record.facts),
            plain(record.versions),
        )
