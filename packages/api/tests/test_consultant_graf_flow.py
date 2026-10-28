"""Graf conversations through the real engine and the real Jev adapter. Only the HTTP
boundary is fake (httpx.MockTransport standing in for OpenRouter). Expected outcomes come
from review.plan.r3 §3 walks, plan r4 §6, selection-rules r2, D1-D32 and products.json."""

import asyncio
import json
import logging
from uuid import UUID, uuid4

import httpx
import pytest

from src.consultant import graf
from src.consultant.contracts import Pair, Turn
from src.consultant.knowledge import Evidence

MODEL = "typesafe/jev-1.14-20261014"  # served model id recorded by Jev pilot r3
SHOP = {p["shop_id"]: p["id"] for p in graf.PRODUCTS["products"]}


class OpenRouter:
    """Fake System One endpoint: state text → Choice answer; records every call."""

    def __init__(self, routes=None, model=MODEL, status=200, fail=None):
        self.routes, self.model, self.status, self.fail = routes or {}, model, status, fail
        self.calls: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        self.headers.append(request.headers)
        if self.fail is not None:
            raise self.fail
        route = self.routes.get(body["state"], ("unclear", 0.9))
        if isinstance(route, str):
            route = (route, 0.95)
        situation, confidence, *rest = route
        probabilities = rest[0] if rest else {situation: confidence}
        return httpx.Response(
            self.status,
            json={
                "model": self.model,
                "answers": {
                    "topic": {
                        "type": "choice",
                        "choice": situation,
                        "confidence": confidence,
                        "probabilities": probabilities,
                    }
                },
                "usage": {"cost": 0.00025},
            },
        )

    @property
    def states(self) -> list[str]:
        return [c["state"] for c in self.calls]


def evidence(out_of_stock=(), missing=()):
    products, sections = {}, []
    for p in graf.PRODUCTS["products"]:
        sid = p["shop_id"]
        if p["id"] in missing:
            continue
        products[sid] = {
            "id": sid,
            "name": p["shop_name"],
            "is_in_stock": p["id"] not in out_of_stock,
        }
        for ordinal in range(6):
            sections.append(
                {
                    "source_id": f"s{sid}:{ordinal}",
                    "product_id": sid,
                    "form": p["form"],
                    "ordinal": ordinal,
                    "caution": ordinal == 2,
                    "passage": "fixture",
                }
            )
    return Evidence("fixture-release", products, {}, sections)


async def direct(work):
    return await work


def make(router: OpenRouter) -> graf.Graf:
    client = httpx.AsyncClient(transport=httpx.MockTransport(router.handler))
    return graf.Graf(graf.Jev(client, "fixture-key"))


async def talk(router, messages, language="ru", stock=None, request_id=None):
    """Run each message as one turn, as service.py does (step 4 is out of scope here)."""
    bot, history, replies = make(router), [], []
    for message in messages:
        turn = Turn(
            request_id=request_id or uuid4(),
            tab_id=uuid4(),
            context_revision=1,
            language=language,
            question=message,
            history=history[-4:],
            page_product_id=None,
            context_token="fixture" if history else None,
        )
        early, turn = bot.safety(turn)
        reply = early
        if reply is None and bot.owns(turn):
            reply = await bot.continuation(turn, stock or evidence(), direct)
        elif reply is None:
            reply = await bot.first(turn, stock or evidence(), direct)
        replies.append(reply)
        if reply is None:
            break
        history.append(Pair(question=message, answer=reply.answer))
    return replies


def variants(template, language="ru"):
    return graf.TEXT[template][language]


def offered(reply):
    return [SHOP[i] for i in reply.ids]


def say(product, mode, age, language="ru"):
    key = {"default": "dose_bands", "acute_cold": "dose_acute_cold", "diarrhea": "dose_diarrhea"}
    for band in graf.PRODUCT[product][key[mode]]:
        low, high = band["age_min"], band["age_max"]
        if (age >= low if band["age_min_inclusive"] else age > low) and (
            high is None or (age <= high if band["age_max_inclusive"] else age < high)
        ):
            return band["say_" + language]
    raise AssertionError(product)


# --- review.plan.r3 §3 conversation walks (expected column), adapted to data r4 parsers ---
# (messages, Jev situation of the first message, expected products with dose mode and age,
#  or a template id for a no-product outcome, extra checks)

WALKS = [
    (
        1,
        ["часто болею", "мне 30", "женщина", "нет"],
        "immunity",
        [("immunocomplex", "default", 30)],
    ),
    (2, ["сын часто болеет, ему 5"], "immunity", [("immunocomplex_kids", "default", 5)]),
    (3, ["ребенку 7 лет, часто болеет"], "immunity", [("immunocomplex", "default", 7)]),
    (
        4,
        ["простыла, горло, температура 38 второй день, мне 25", "женщина", "нет"],
        "acute_cold",
        [("zincum_c_d3", "acute_cold", 25)],
    ),
    (5, ["дочке 10 лет, насморк 3 дня"], "acute_cold", [("immunocomplex", "default", 10)]),
    (6, ["мне 16, простуда 2 дня"], "acute_cold", [("zincum_c_d3", "acute_cold", 16)]),
    (7, ["температура 5 дней у сына 8 лет"], "acute_cold", "doctor.fever_long"),
    (8, ["у дочки 5 лет кашель", "3 дня"], "acute_cold", [("immunocomplex_kids", "default", 5)]),
    (
        9,
        ["сын 2 года болеет насморк", "2", "3 дня"],
        "acute_cold",
        [("immunocomplex_kids", "default", 2)],
    ),
    (10, ["запор у ребенка 3 года"], "constipation", [("magniy_stick", "default", 3)]),
    (11, ["запор, мне 40"], "constipation", [("magniy_caps", "default", 40)]),
    (
        12,
        ["понос и нервы, мне 35"],
        "adult_stress_sleep",
        [("active_caps", "default", 35), ("zincum_stick", "diarrhea", 35)],
    ),
    (13, ["ребенку 8 месяцев понос"], "diarrhea", "doctor.infant_diarrhea"),
    (
        14,
        ["у ребенка 2 года понос"],
        "diarrhea",
        [("neo", "default", 2), ("zincum_stick", "diarrhea", 2)],
    ),
    (15, ["сыпь и зуд у дочки, ей 3"], "skin_allergy", [("active_stick", "default", 3)]),
    (
        16,
        ["сыпь, чешется, мне 30"],
        "skin_allergy",
        [("dermacomplex", "default", 30), ("active_caps", "default", 30)],
    ),
    (17, ["сухая кожа, ребенку 3"], "skin_hair", "no_product.age_skin"),
    (18, ["Ломкие волосы", "мне 28"], "skin_hair", [("dermacomplex", "default", 28)]),
    (
        19,
        ["Растройство кишечника", "мне 30"],
        "diarrhea",
        [("active_caps", "default", 30), ("zincum_stick", "diarrhea", 30)],
    ),
    (20, ["глаза устают от компьютера, мне 35"], "eyes", [("ophtalmocomplex", "default", 35)]),
    (21, ["плохо сплю, стресс, мне 45"], "adult_stress_sleep", [("magniy_caps", "default", 45)]),
    (
        22,
        ["сводит икры, беременна, 28 лет", "28"],
        "pregnancy_tone_cramps",
        [("magniy_caps", "default", 28)],
    ),
    (23, ["сводит икры, мне 28"], "pregnancy_tone_cramps", [("magniy_caps", "default", 28)]),
    (
        24,
        ["я беременна, хочу что-нибудь для иммунитета", "30"],
        "immunity",
        [("zincum_c_d3", "default", 30)],
    ),
    (
        25,
        ["беременна, 30 лет, что попить", "30"],
        "pregnancy_general",
        [("zincum_c_d3", "default", 30), ("calciy", "default", 30)],
    ),
    (26, ["беременна 17, часто болею", "17"], "immunity", [("zincum_c_d3", "default", 17)]),
    (
        27,
        ["часто болею", "мне 30", "женщина", "нет, кормлю грудью"],
        "immunity",
        [("zincum_c_d3", "default", 30)],
    ),
    (28, ["сын 13, борьба, худой", "13"], "sport_mass", [("zincum_stick", "default", 13)]),
    (
        29,
        ["сын 10, после тренировок болят мышцы", "10"],
        "sport_muscles",
        [("magniy_caps", "default", 10)],
    ),
    (30, ["мне 50, суставы при беге"], "sport_joints", [("calciy", "default", 50)]),
    (
        31,
        ["что-нибудь для спорта", "выносливость", "мне 20"],
        "sport_general",
        [("zincum_c_d3", "default", 20)],
    ),
    (
        32,
        ["мне 14, сил нет после зала", "мышцы"],
        "sport_endurance",
        [("magniy_caps", "default", 14)],
    ),
    (
        33,
        ["ребенок 12, невнимательный в школе", "12"],
        "child_memory_attention",
        [("neurocomplex_kids", "default", 12)],
    ),
    (34, ["аллергия на магний"], "general_allergy", "intolerance.answer_no_name"),
    (35, ["болит голова"], "other_unhandled", "no_product.general"),
    (36, ["давление скачет, мне 60"], "other_unhandled", "no_product.general"),
    (38, ["глисты у ребенка 5 лет"], "helminths", [("active_stick", "default", 5)]),
    (39, ["мне 65, кости болят"], "bones_50plus", [("calciy", "default", 65)]),
    (40, ["мне 45, остеопороз"], "bones_50plus", [("calciy", "default", 45)]),
    (41, ["кашель месяц, мне 30"], "acute_cold", "doctor.cough_long"),
    (
        43,
        ["ичим кетяпти, 25 ёшдаман"],
        "diarrhea",
        [("active_caps", "default", 25), ("zincum_stick", "diarrhea", 25)],
    ),
    (44, ["кормлю грудью, часто болею, мне 30"], "immunity", [("zincum_c_d3", "default", 30)]),
    (
        46,
        ["ребенок плохо ест, 4 года", "да", "4"],
        "child_poor_appetite",
        [("zincum_stick", "default", 4)],
    ),
    (47, ["отит у сына 5 лет", "3 дня"], "ent", [("immunocomplex_kids", "default", 5)]),
    (48, ["бир ойдан бери йўталяпман"], "acute_cold", "doctor.cough_long"),
]


@pytest.mark.parametrize(("walk", "messages", "situation", "expected"), WALKS, ids=lambda v: v)
async def test_review_r3_walks(walk, messages, situation, expected):
    router = OpenRouter({messages[0]: situation})
    replies = await talk(router, messages)
    *questions, final = replies
    assert all(r is not None and r.status == "clarification" for r in questions)
    assert final is not None
    assert len(router.calls) == 1  # continuation needs no second Jev call
    if isinstance(expected, str):
        assert final.status == "limited"
        assert final.ids == []
        assert final.answer in variants(expected)
        return
    assert final.status == "answer"
    assert offered(final) == [p for p, _, _ in expected]
    for product, mode, age in expected:
        assert say(product, mode, age) in final.answer
    if walk == 12:  # MAGNIY never with diarrhea words; the note says why (B1, NI7, I8)
        assert any(v in final.answer for v in variants("note_magniy_diarrhea"))
    pregnant = walk in {22, 24, 25, 26, 27, 44}
    note = "note_pregnancy_two" if len(expected) == 2 else "note_pregnancy"
    assert any(v in final.answer for v in variants(note)) is pregnant
    if walk in {20, 33, 18}:  # reason line by topic (situations.json notes; nit adaptation)
        indication = {20: "computer", 33: "adaptation", 18: "hair"}[walk]
        product = expected[0][0]
        reason = next(i for i in graf.PRODUCT[product]["indications"] if i["id"] == indication)
        assert reason["reason_ru"] in final.answer
    if walk == 38:
        assert any(v in final.answer for v in variants("note_disease"))


async def test_walk_42_uzbek_answers_in_latin():
    message = "bolam tez-tez kasal bo'ladi, 4 yoshda"
    (reply,) = await talk(OpenRouter({message: "immunity"}), [message], language="uz")
    assert offered(reply) == ["immunocomplex_kids"]
    assert say("immunocomplex_kids", "default", 4, "uz") in reply.answer  # lower band at 4
    assert any(v in reply.answer for v in variants("ack.immunity", "uz"))


@pytest.mark.parametrize(
    ("out_of_stock", "expected"),
    [(("zincum_stick",), ["active_caps"]), (("active_caps", "zincum_stick"), [])],
)
async def test_walk_45_two_cards_with_one_or_both_unavailable(out_of_stock, expected):
    message = "понос, мне 30"
    (reply,) = await talk(
        OpenRouter({message: "diarrhea"}), [message], stock=evidence(out_of_stock)
    )
    assert offered(reply) == expected
    if expected:
        assert any(v.split("{")[0] in reply.answer for v in variants("offer.one"))
    else:
        assert reply.status == "limited"
        assert reply.answer in variants("no_product.unavailable")


async def test_offer_carries_cards_sources_and_cautions_in_text_order():
    message = "сыпь, чешется, мне 30"
    stock = evidence()
    (reply,) = await talk(OpenRouter({message: "skin_allergy"}), [message], stock=stock)
    derma, active = (
        graf.PRODUCT["dermacomplex"]["shop_id"],
        graf.PRODUCT["active_caps"]["shop_id"],
    )
    assert reply.ids == [derma, active]
    assert [p["id"] for p in reply.products] == [derma, active]
    assert reply.answer.index("DERMACOMPLEX") < reply.answer.index("BIFOLAK® ACTIVE")
    ids = {s["source_id"] for s in reply.sources}
    assert set(reply.cautions) == {f"s{derma}:2", f"s{active}:2"}
    assert set(reply.cautions) <= ids
    # plan r4 §3.6 / data shop_sections: reason → effects 3, dose → use 2
    assert {f"s{derma}:3", f"s{active}:3"} <= ids
    assert len(reply.sources) <= 12


# --- plan r4 §6 flow list -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "language", "expected"),
    [
        ("нет", "ru", "immunocomplex"),
        ("не беременна", "ru", "immunocomplex"),
        ("yo'q", "uz", "immunocomplex"),
        ("нет, кормлю грудью", "ru", "zincum_c_d3"),
        ("не знаю", "ru", "reask.pregnancy"),
    ],
)
async def test_pregnancy_question_replies(reply, language, expected):
    first = "часто болею, мне 30, я женщина"
    replies = await talk(OpenRouter({first: "immunity"}), [first, reply], language)
    assert any(v in replies[0].answer for v in variants("ask.pregnancy", language))
    if expected.startswith("reask"):
        assert any(v in replies[1].answer for v in variants(expected, language))
    else:
        assert offered(replies[1]) == [expected]


async def test_years_of_illness_are_not_an_age():
    message = "уже 2 года часто болею"
    (reply,) = await talk(OpenRouter({message: "immunity"}), [message])
    assert reply.status == "clarification"
    assert any(v in reply.answer for v in variants("ask.whom_age"))


@pytest.mark.parametrize("message", ["boldirim tortib qoladi", "болдирим тортиб қолади"])
async def test_cramps_without_pregnancy_word_route_to_muscle_cramps(message):
    replies = await talk(OpenRouter({message: "pregnancy_tone_cramps"}), [message, "30"], "uz")
    assert any(v in replies[0].answer for v in variants("ack.muscle_cramps", "uz"))
    assert offered(replies[1]) == ["magniy_caps"]
    assert not any(v in replies[1].answer for v in variants("note_pregnancy", "uz"))


async def test_constipation_with_diarrhea_never_offers_magniy():
    message = "запор, но иногда понос, мне 40"
    (reply,) = await talk(OpenRouter({message: "constipation"}), [message])
    assert offered(reply) == ["active_caps", "zincum_stick"]
    assert any(v in reply.answer for v in variants("note_magniy_diarrhea"))
    # The same complaint without the diarrhea word does reach MAGNIY: the guard is live.
    other = "запор, мне 40"
    (plain,) = await talk(OpenRouter({other: "constipation"}), [other])
    assert offered(plain) == ["magniy_caps"]


async def test_infant_low_weight_goes_to_the_doctor():
    message = "малышу 8 месяцев, плохо набирает вес"
    (reply,) = await talk(OpenRouter({message: "child_low_weight"}), [message])
    assert reply.answer in variants("doctor.infant_weight")
    assert reply.ids == []


@pytest.mark.parametrize(
    ("messages", "template"),
    [
        (["кашель, мне 30", "уже 12 дней, насморк"], "doctor.cold_long"),
        (["кашель, мне 30", "4 недели"], "doctor.cough_long"),
        (["температура, мне 30", "4 дня"], "doctor.fever_long"),
        (["простуда, мне 30", "3 дня, и кровь в кале"], "doctor.general"),
    ],
)
async def test_doctor_text_matches_its_trigger_on_a_reply(messages, template):
    replies = await talk(OpenRouter({messages[0]: "acute_cold"}), messages)
    assert any(v in replies[0].answer for v in variants("ask.duration"))
    assert replies[1].answer in variants(template)


async def test_doctor_check_on_an_age_reply_with_dehydration():
    messages = ["у ребенка понос", "5 лет, не писает"]
    replies = await talk(OpenRouter({messages[0]: "diarrhea"}), messages)
    assert replies[1].answer in variants("doctor.infant")


async def test_reask_once_then_the_no_product_answer():
    messages = ["часто болею", "не помню", "забыл"]
    router = OpenRouter({messages[0]: "immunity"})
    replies = await talk(router, messages)
    assert any(v in replies[1].answer for v in variants("reask.age"))
    assert replies[2].answer in variants("no_product.general")
    assert router.states == messages  # one Jev call per unparsed reply, on the reply alone


async def test_unparsed_reply_with_a_new_situation_restarts_the_flow():
    messages = ["часто болею", "а у меня понос, мне 30"]
    router = OpenRouter({messages[0]: "immunity", messages[1]: "diarrhea"})
    replies = await talk(router, messages)
    # "мне 30" answers the age question; the diarrhea words then route the whole turn (NI7).
    assert offered(replies[1]) == ["active_caps", "zincum_stick"]
    assert router.states == messages[:1]
    messages = ["часто болею", "а у меня понос"]
    router = OpenRouter({messages[0]: "immunity", messages[1]: "diarrhea"})
    replies = await talk(router, [*messages, "30"])
    assert any(v in replies[1].answer for v in variants("ack.diarrhea"))
    assert offered(replies[2]) == ["active_caps", "zincum_stick"]


async def test_fifth_turn_after_a_reask_steps_aside():
    # Complaint + whom/age + re-ask + duration + sex + pregnancy: the start pair has left the
    # 4-pair window at the reply to the fifth question (review r1 B2, plan r4 §3.3).
    messages = ["простыла", "не помню", "30", "2 дня", "женщина", "нет"]
    router = OpenRouter({messages[0]: "acute_cold"})
    replies = await talk(router, messages)
    assert [r is not None for r in replies] == [True] * 5 + [False]
    # Without the re-ask the same flow ends with an offer inside the window.
    clean = ["простыла", "30", "2 дня", "женщина", "нет"]
    replies = await talk(OpenRouter({clean[0]: "acute_cold"}), clean)
    assert offered(replies[-1]) == ["zincum_c_d3"]


async def test_treat_words_open_the_offer_with_not_treat():
    messages = ["чем лечить кашель ребенку 5 лет", "второй день"]
    replies = await talk(OpenRouter({messages[0]: "acute_cold"}), messages)
    assert not any(v in replies[0].answer for v in variants("opening.not_treat"))
    assert any(replies[1].answer.startswith(v) for v in variants("opening.not_treat"))
    assert offered(replies[1]) == ["immunocomplex_kids"]


async def test_second_complaint_steps_aside_with_measured_probabilities():
    # Probabilities recorded by the two-complaint measurement run (PR description).
    message = "часто простываю и волосы выпадают"
    router = OpenRouter({message: ("immunity", 0.71, {"immunity": 0.71, "skin_hair": 0.27})})
    assert await talk(router, [message]) == [None]
    same_group = OpenRouter({message: ("immunity", 0.71, {"immunity": 0.71, "acute_cold": 0.27})})
    (reply,) = await talk(same_group, [message])
    assert reply is not None


@pytest.mark.parametrize(
    ("router", "logged"),
    [
        (OpenRouter({"часто болею": ("immunity", 0.69)}), None),
        (OpenRouter({"часто болею": "unclear"}), None),
        (OpenRouter(status=500), None),
        (OpenRouter(fail=httpx.ReadTimeout("fixture")), None),
        (OpenRouter(fail=httpx.ConnectError("fixture")), None),
        (OpenRouter({"часто болею": "immunity"}, model="typesafe/jev-1.15-20261201"), "1.15"),
        (OpenRouter({"часто болею": "immunity"}, model="typesafe/jev-1.140"), "1.140"),
    ],
)
async def test_jev_failures_step_aside(router, logged, caplog):
    caplog.set_level(logging.INFO, logger="src.consultant.graf")
    assert await talk(router, ["часто болею"]) == [None]
    mismatch = [r.getMessage() for r in caplog.records if "mismatch" in r.getMessage()]
    assert bool(mismatch) is bool(logged)
    if logged:
        assert mismatch == [f"graf jev version mismatch served={router.model}"]
    assert "часто болею" not in caplog.text


async def test_jev_timeout_is_bounded_without_a_real_wait(monkeypatch):
    started = asyncio.Event()

    async def hang(request):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(graf, "JEV_TIMEOUT", 0.01)
    client = httpx.AsyncClient(transport=httpx.MockTransport(hang))
    assert await graf.Jev(client, "fixture-key").classify("часто болею") is None
    assert started.is_set()


async def test_jev_cancellation_propagates():
    started = asyncio.Event()

    async def hang(request):
        started.set()
        await asyncio.Event().wait()

    client = httpx.AsyncClient(transport=httpx.MockTransport(hang))
    task = asyncio.create_task(graf.Jev(client, "fixture-key").classify("часто болею"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_jev_request_shape():
    router = OpenRouter({"часто болею": "immunity"})
    await talk(router, ["часто болею"])
    (body,) = router.calls
    assert body["model"] == "~typesafe/jev-latest"
    assert body["state"] == "часто болею"
    topic = body["questions"]["topic"]
    assert topic["type"] == "choice"
    assert topic["criteria"] == {s["id"]: s["jev_criterion_full"] for s in graf.SITUATION.values()}
    assert len(topic["criteria"]) == 46
    assert router.headers[0]["authorization"] == "Bearer fixture-key"


async def test_uncovered_and_intolerance_texts():
    (uncovered,) = await talk(OpenRouter({"болит спина": "other_unhandled"}), ["болит спина"])
    assert uncovered.answer in variants("no_product.general")
    message = "у него аллергия на ваши капсулы"
    (intolerance,) = await talk(OpenRouter({message: "general_allergy"}), [message])
    assert intolerance.answer in variants("intolerance.answer_no_name")
    # Allergy as a complaint is not intolerance (review r2 NI4): it reaches the allergy group.
    other = "у него аллергия на шоколад, ему 10"
    (allergy,) = await talk(OpenRouter({other: "general_allergy"}), [other])
    assert offered(allergy) == ["active_caps"]


# --- safety layer -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "outcome"), [("да", "103"), ("не знаю", "103"), ("ha", "103"), ("нет", "flow")]
)
async def test_emergency_check_replies(reply, outcome):
    message = "у дочки судороги"
    router = OpenRouter({message: "muscle_cramps"})
    first, second = await talk(router, [message, reply])
    assert first.answer in variants("emergency.check")
    assert first.status == "clarification"
    if outcome == "103":
        assert second.answer in variants("emergency.general")
        assert router.calls == []
    else:
        assert router.states == [message]  # the checked message continues, not "нет"
        assert any(v in second.answer for v in variants("ack.muscle_cramps"))


@pytest.mark.parametrize(
    ("message", "template"),
    [
        ("давит в груди", "emergency.general"),
        ("не хочу жить", "emergency.self_harm"),
        ("беременна, резко отекло лицо", "emergency.pregnancy"),
    ],
)
async def test_strong_signs_answer_103_without_jev(message, template):
    router = OpenRouter()
    (reply,) = await talk(router, [message])
    assert reply.answer in variants(template)
    assert (reply.status, reply.ids, reply.products) == ("limited", [], [])
    assert router.calls == []


async def test_danger_in_a_continuation_reply_still_gets_103():
    messages = ["часто болею", "мне 30, и задыхаюсь"]
    replies = await talk(OpenRouter({messages[0]: "immunity"}), messages)
    assert replies[1].answer in variants("emergency.general")


# --- determinism, wording, logs -----------------------------------------------------------


async def test_variants_are_deterministic_and_differ_from_the_previous_message():
    rid = UUID(int=42)
    a = await talk(OpenRouter({"простыла": "acute_cold"}), ["простыла", "30"], request_id=rid)
    b = await talk(OpenRouter({"простыла": "acute_cold"}), ["простыла", "30"], request_id=rid)
    assert [r.answer for r in a] == [r.answer for r in b]
    first_ack = next(v for v in variants("ack.acute_cold") if a[0].answer.startswith(v))
    assert not a[1].answer.startswith(first_ack)


@pytest.mark.parametrize(
    ("message", "words"),
    [("для мамы, часто болеет, ей 65", "для мамы"), ("мужу 40, часто болеет", "для мужа")],
)
async def test_offer_names_the_person_from_the_relation_table(message, words):
    (reply,) = await talk(OpenRouter({message: "immunity"}), [message])
    assert words in reply.answer.lower()


async def test_logs_carry_situation_and_outcome_but_no_customer_text(caplog):
    caplog.set_level(logging.INFO, logger="src.consultant.graf")
    message = "сын часто болеет, ему 5"
    await talk(OpenRouter({message: "immunity"}), [message])
    lines = [r.getMessage() for r in caplog.records if r.name == "src.consultant.graf"]
    assert lines == ["graf turn situation=immunity outcome=offer"]
    assert "сын" not in caplog.text
    assert "5" not in "".join(lines)
