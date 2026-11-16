"""The shop core in the Enigma turn (spec.shop-core r4): data, the topic question, routing, the
brief, the shop decision, the checks of a shop text, the response and the record. Jev is a double
at its HTTP boundary, Luna at Provider.call; the database is the real test database. Each test
names the requirement it proves."""

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.consultant.contracts import Fault
from src.consultant.enigma import checks, data, jev, shop
from src.consultant.enigma.data import RULES, TOPIC
from src.journal.models import JournalTurn
from src.journal.writer import Journal
from src.main import create_app
from tests.test_consultant_enigma_core import answer
from tests.test_consultant_enigma_turn import (
    OFFER,
    QUESTION,
    JevDouble,
    bot,
    say,
    seed,
    turn,
)

PHONE = "998 93 555 17 47"
SUPPORT = f"напишите нам в Telegram @Biotact_uz или позвоните по номеру {PHONE}."
HOW_TO_BUY = "Нажмите «В корзину» на карточке нужного товара, он появится в «Корзине»."

# --- SD-01, SD-02: the shop core file ------------------------------------------------------------


def _unknown_key(shop_file: dict[str, Any]) -> None:
    shop_file["topics"][0]["extra"] = 1


def _missing_key(shop_file: dict[str, Any]) -> None:
    del shop_file["topics"][0]["approved"]


def _empty_text(shop_file: dict[str, Any]) -> None:
    shop_file["topics"][0]["text"]["uz"] = ""


def _empty_description(shop_file: dict[str, Any]) -> None:
    shop_file["topics"][0]["description"]["ru"] = ""


def _duplicate(shop_file: dict[str, Any]) -> None:
    shop_file["topics"][1]["id"] = shop_file["topics"][0]["id"]


def _none(shop_file: dict[str, Any]) -> None:
    shop_file["topics"][0]["id"] = "none"


def _no_support(shop_file: dict[str, Any]) -> None:
    shop_file["topics"] = [t for t in shop_file["topics"] if t["id"] != "other_shop"]


@pytest.mark.parametrize(
    "spoil",
    [_unknown_key, _missing_key, _empty_text, _empty_description, _duplicate, _none, _no_support],
)
def test_sd02_invalid_shop_file_is_rejected(tmp_path: Path, spoil: Any) -> None:
    for name in data.DATA_FILES:
        (tmp_path / name).write_bytes((data.DATA_DIR / name).read_bytes())
    assert data.load(tmp_path)[-1] == data.DATA  # the shipped file passes
    shop_file = json.loads((tmp_path / "shop.json").read_text(encoding="utf-8"))
    spoil(shop_file)
    (tmp_path / "shop.json").write_text(json.dumps(shop_file, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        data.load(tmp_path)


def test_sd01_sd03_the_released_topics_and_the_data_hash() -> None:
    assert [t.id for t in data.SHOP.topics] == [
        "how_to_buy",
        "delivery_tashkent",
        "delivery_regions",
        "payment",
        "return",
        "contacts",
        "order_status",
        "order_cancel",
        "channels",
        "storage_expiry",
        "origin",
        "other_shop",
    ]
    assert all(t.approved is not None for t in data.SHOP.topics)  # §5: no delivery otherwise
    assert "zakaz" in TOPIC["how_to_buy"].description.uz
    assert "dostavka" not in TOPIC["how_to_buy"].description.uz
    for delivery_id in ("delivery_tashkent", "delivery_regions"):
        assert "dostavka" in TOPIC[delivery_id].description.uz
        assert "zakaz" not in TOPIC[delivery_id].description.uz
    assert data.DATA_FILES[-1] == "shop.json"


def test_sd04_public_contacts_are_the_shop_phone() -> None:
    assert frozenset({PHONE}) == shop.PUBLIC_CONTACTS


# --- SJ-01, SJ-02: the topic question and its reading --------------------------------------------


def test_sj01_a_body_without_a_valid_topic_answer_is_invalid() -> None:
    assert isinstance(jev.read(answer("unclear", topic="payment")), jev.Reading)
    missing = answer("unclear")
    del missing["answers"]["topic"]
    assert jev.read(missing) == "invalid"
    assert jev.read(answer("unclear", topic="weather")) == "invalid"


@pytest.mark.parametrize(
    ("choice", "probabilities", "top", "chosen"),
    [
        ("none", {"none": 0.8, "payment": 0.2}, ("none", 0.8), ()),
        ("payment", {"payment": 0.9, "return": 0.1}, ("payment", 0.9), ("payment",)),
        (
            "payment",
            {"payment": 0.5, "none": 0.37, "return": 0.13},
            ("payment", 0.5),
            ("payment", "return"),
        ),
        ("payment", {"payment": 0.6, "none": 0.3, "return": 0.1}, ("payment", 0.6), ("payment",)),
        # A tie is broken by the display order of the shop core: payment before return.
        ("return", {"return": 0.5, "payment": 0.5}, ("payment", 0.5), ("payment", "return")),
    ],
    ids=["top none", "one topic", "none second, third at the threshold", "below", "tie"],
)
def test_sj02_chosen_topics(
    choice: str, probabilities: dict[str, float], top: tuple[str, float], chosen: tuple[str, ...]
) -> None:
    reading = jev.read(answer("unclear", topic=choice, topics=probabilities))
    assert isinstance(reading, jev.Reading)
    assert reading.topic == top
    assert tuple(t for t, _ in reading.topics) == chosen


# --- SR-01, SR-02, SL-01, SL-02: routing, the brief, the shop decision ---------------------------


@pytest.mark.parametrize(
    ("question", "topic", "text"),
    [
        ("как оформить заказ?", "how_to_buy", HOW_TO_BUY),
        (
            "доставка в Самарканд",
            "delivery_regions",
            "В регионы доставляет курьерская служба BTS Express.",
        ),
    ],
)
async def test_sr01_shop_questions_are_enigma_turns_with_their_topic(
    test_db: Any, question: str, topic: str, text: str
) -> None:
    await seed(test_db)
    consultant = bot(test_db, JevDouble(answer("unclear", topic=topic)), say("shop", text))
    reply = await consultant.turn(turn(question))
    record = consultant.records[-1]  # type: ignore[attr-defined]
    assert (reply["status"], reply["answer"]) == ("answer", text)
    assert (record.path, record.graf_outcome, record.luna["shop"]) == ("enigma", "shop", [topic])
    # SL-01: the chosen topic's text and the support text, in the turn's language.
    assert consultant.provider.briefs[-1]["shop"] == {  # type: ignore[attr-defined]
        "topics": [{"id": topic, "text": TOPIC[topic].text.ru}],
        "support": TOPIC["other_shop"].text.ru,
    }


async def test_sr02_an_ambulance_hit_drops_the_topics(test_db: Any) -> None:
    await seed(test_db)
    consultant = bot(
        test_db,
        JevDouble(answer("doctor", topic="delivery_tashkent")),
        say("emergency", RULES.emergency.general.ru),
    )
    reply = await consultant.turn(turn("давит в груди отдает в руку, как быстро доставите?"))
    assert reply["status"] == "limited"
    assert "shop" not in consultant.provider.briefs[-1]  # type: ignore[attr-defined]
    assert consultant.records[-1].luna["shop"] == []  # type: ignore[attr-defined]


async def test_sl02_a_shop_decision_without_shop_texts_is_unusable(test_db: Any) -> None:
    await seed(test_db)
    step = say("shop", HOW_TO_BUY)
    consultant = bot(test_db, JevDouble(answer("unclear")), step, step)
    with pytest.raises(Fault, match="answer_unavailable"):
        await consultant.turn(turn("что посоветуете?"))
    calls = consultant.records[-1].luna["calls"]  # type: ignore[attr-defined]
    assert [c["target"] for c in calls] == ["shop", "shop"]


# --- SV-01…SV-07: the checks of a text whose brief held `shop` -----------------------------------

NAMES = checks.Names.of({})


def verdict(
    text: str,
    decision: str,
    topics: list[str],
    language: str = "ru",
    customers: tuple[str, ...] = (),
    shown_ids: frozenset[int] = frozenset(),
) -> str | None:
    reference = shop.reference(language, topics)  # type: ignore[arg-type]
    subject = checks.Subject(
        language,  # type: ignore[arg-type]
        decision,
        shown_ids,
        shown_ids,
        checks.numbers([*reference, *customers]),
        (),
        reference,
        customers,
    )
    return checks.check(text, subject, NAMES)


@pytest.mark.parametrize(
    ("text", "topics", "passes"),
    [
        ("Бесплатная доставка от 20 000 сум.", ["delivery_tashkent"], False),
        ("Доставка займёт 3 дня.", ["delivery_tashkent"], False),
        ("Бесплатно от 300 тысяч сум.", ["delivery_tashkent"], False),
        ("Заказы, оформленные до 13:00, доставляются в тот же день.", ["delivery_tashkent"], True),
        ("Курьер приезжает с 16 до 20 часов.", ["delivery_tashkent"], True),
        (
            "При покупке от 250 000 сум доставка по Ташкенту бесплатная.",
            ["delivery_tashkent"],
            True,
        ),
    ],
)
def test_sv01_values_of_a_shop_answer(text: str, topics: list[str], passes: bool) -> None:
    assert (verdict(text, "shop", topics) is None) is passes


def test_sv01_a_shop_answer_may_name_a_shown_product_with_digits() -> None:
    text = "Нажмите «В корзину» на карточке Термос KD-775, 600 мл."
    assert verdict(text, "shop", ["how_to_buy"], shown_ids=frozenset({59})) is None


@pytest.mark.parametrize(
    ("text", "topics", "language", "passes"),
    [
        ("Пишите нам на @biotact_support.", ["channels"], "ru", False),
        ("Наш сайт biotact.com.", ["channels"], "ru", False),
        ("Каталог на biotact.uz/catalog.", ["channels"], "ru", False),
        (f"Позвоните по номеру {PHONE}.", ["contacts"], "ru", False),
        # D-1: a non-support handle from `A` does not turn the answer into a support referral.
        (
            "Купить наши товары можно на сайте biotact.uz или в Telegram-приложении @BiotactBot.",
            ["channels"],
            "ru",
            True,
        ),
        ("Купить можно в приложении @biotactbot.", ["channels"], "ru", True),
        (
            "Mahsulotlarimizni biotact.uz saytida yoki Telegramdagi @BiotactBot ilovasida "
            "xarid qilishingiz mumkin.",
            ["channels"],
            "uz",
            True,
        ),
        # D-1: the support handle (any case) or the support phone without its pair still fails.
        ("Пишите нам в Telegram @Biotact_uz.", ["contacts"], "ru", False),
        ("Пишите нам в Telegram @biotact_uz.", ["channels"], "ru", False),
        (
            "Купить можно в @BiotactBot; с вопросами пишите в @BIOTACT_UZ.",
            ["channels"],
            "ru",
            False,
        ),
        (f"Купить можно в @BiotactBot, телефон {PHONE}.", ["channels"], "ru", False),
        (
            f"Купить можно на сайте biotact.uz или в Telegram-приложении @BiotactBot; {SUPPORT}",
            ["channels"],
            "ru",
            True,
        ),
        # C-72: Jev chose `return` for a broken blender; the support answer passes.
        (f"Если блендер сломался, {SUPPORT}", ["return"], "ru", True),
        # Handles compare without case.
        (f"Пишите в Telegram @biotact_uz или звоните по номеру {PHONE}.", ["contacts"], "ru", True),
    ],
)
def test_sv02_handles_addresses_and_the_support_answer(
    text: str, topics: list[str], language: str, passes: bool
) -> None:
    assert verdict(text, "shop", topics, language) == (None if passes else "SV-02")


@pytest.mark.parametrize(
    ("text", "topics", "language", "customers", "passes"),
    [
        ("В регионы доставка бесплатная.", ["delivery_regions"], "ru", (), False),
        ("Можно оплатить наличными курьеру.", ["payment"], "ru", ("можно наличкой?",), False),
        ("От 250 000 сум доставка по Ташкенту бесплатная.", ["delivery_tashkent"], "ru", (), True),
        (
            f"Naqd to'lov haqida ma'lumot yo'q, {PHONE} raqamiga qo'ng'iroq qiling yoki "
            "Telegramda @Biotact_uz ga yozing.",
            ["payment"],
            "uz",
            ("нақд пулда тўласа бўладими?",),
            True,
        ),
        ("Naqd to'lash mumkin.", ["payment"], "uz", ("нақд пулда тўласа бўладими?",), False),
        (
            f"Chegirmalar haqida {PHONE} raqamiga qo'ng'iroq qiling yoki Telegramda @Biotact_uz "
            "ga yozing.",
            ["other_shop"],
            "uz",
            ("skidka bormi?",),
            True,
        ),
    ],
    ids=[
        "free elsewhere",
        "cash",
        "free in the text",
        "referral, uz-cyrl",
        "no referral",
        "synonym",
    ],
)
def test_sv03_condition_words(
    text: str, topics: list[str], language: str, customers: tuple[str, ...], passes: bool
) -> None:
    assert (verdict(text, "shop", topics, language, customers) is None) is passes


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Можно оплатить наличкой.", "ru"),
        ("Оплата налом возможна.", "ru"),
        ("Скидок много.", "ru"),
        ("Есть кешбек.", "ru"),
        ("Yetkazib berish бепул.", "uz"),
        ("Yetkazib berish besplatno.", "uz"),
    ],
)
def test_sv03_condition_words_in_other_forms_and_scripts(text: str, language: str) -> None:
    # Review S-01: the answer side sees the condition in other word forms and scripts too.
    assert verdict(text, "shop", ["payment"], language) == "SV-03"


def test_sv04_the_shop_phone_is_masked_and_an_emergency_text_is_delivered() -> None:
    assert verdict(f"Звоните: {PHONE}, Telegram @Biotact_uz.", "shop", ["contacts"]) is None
    assert verdict("Звоните: 998 90 111 22 33, Telegram @Biotact_uz.", "shop", ["contacts"]) == (
        "V-01"
    )
    assert verdict(RULES.emergency.general.ru, "emergency", ["delivery_tashkent"]) is None


def test_sv05_sv06_products_of_the_return_text_and_no_instruction_sentence() -> None:
    text = (
        "Товар возврату и обмену не подлежит; BIFOLAK® ACTIVE и BIFOLAK® MAGNIY меняются по форме."
    )
    assert verdict(text, "shop", ["return"]) is None
    assert verdict("IMMUNOCOMPLEX® можно вернуть.", "shop", ["return"]) == "V-02"


def test_sv07_warranty_words_in_a_shop_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        checks, "RULES", RULES.model_copy(update={"treatment_stems": ["гарант", "kafolat"]})
    )
    assert verdict(f"По вопросам гарантии {SUPPORT}", "shop", ["contacts"]) is None
    assert verdict("Это гарантирует результат.", "shop", ["contacts"]) == "V-06"
    assert verdict("Это гарантирует результат?", "ask", ["contacts"]) == "V-06"
    # SV-07: the noun passes only in a shop answer; V-06 is unchanged for the other decisions.
    assert verdict(f"По вопросам гарантии {SUPPORT}", "ask", ["contacts"]) == "V-06"
    uz_support = f"Kafolat bo'yicha Telegramda @Biotact_uz ga yozing yoki {PHONE} raqamiga qo'ng'iroq qiling."
    assert verdict(uz_support, "shop", ["contacts"], "uz") is None
    assert verdict("Bu natijani kafolatlaydi.", "shop", ["contacts"], "uz") == "V-06"


# --- SO-01, SO-02 and the user path ---------------------------------------------------------------


async def test_user_path_how_to_buy_after_an_offer_keeps_the_cards(test_db: Any) -> None:
    await seed(test_db)
    jev_double = JevDouble(
        answer("immunocomplex", zincum=0.3), answer("unclear", topic="how_to_buy")
    )
    consultant = bot(
        test_db,
        jev_double,
        say("offer", OFFER, "immunocomplex", "zincum"),
        say("shop", HOW_TO_BUY),
    )
    app = create_app()
    app.state.consultant = consultant
    app.state.consultant_origins = ["https://shop.test"]
    first = turn("часто болею")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://shop.test"
    ) as client:
        one = (
            await client.post("/api/v1/consultant/turns", json=json.loads(first.model_dump_json()))
        ).json()
        second = turn(
            "как купить?", one["context_token"], first.tab_id, (("часто болею", one["answer"]),)
        )
        two = await client.post(
            "/api/v1/consultant/turns", json=json.loads(second.model_dump_json())
        )
    assert two.status_code == 200
    body = two.json()
    # SO-01: status answer, the shown cards and the selection kept.
    assert (body["status"], body["answer"]) == ("answer", HOW_TO_BUY)
    assert [p["id"] for p in body["products"]] == [p["id"] for p in one["products"]]
    assert consultant.tokens.read(turn("x", body["context_token"], first.tab_id)).ids == [
        p["id"] for p in one["products"]
    ]


async def test_so02_the_record_of_shop_and_of_no_topic_turns(test_db: Any) -> None:
    await seed(test_db)
    consultant = bot(
        test_db,
        JevDouble(
            answer("unclear", topic="payment", topics={"payment": 0.6, "return": 0.4}),
            answer("unclear"),
        ),
        say("shop", "Оплата производится онлайн через платёжный агрегатор Multicard."),
        say("ask", QUESTION),
    )
    await consultant.turn(turn("как оплатить и можно вернуть?"))
    await consultant.turn(turn("что посоветуете?"))
    shop_record, none_record = consultant.records  # type: ignore[attr-defined]
    assert shop_record.jev["topic"] == [["payment", 0.6], ["return", 0.4]]
    assert (shop_record.graf_outcome, shop_record.situation) == ("shop", "payment")
    assert shop_record.luna["shop"] == ["payment", "return"]
    assert none_record.jev["topic"] == [["none", 0.9]]
    assert consultant.counters.counts["enigma.shop"] == 1
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    await Journal(sessions).write(shop_record)
    async with sessions() as db:
        row = (await db.scalars(select(JournalTurn))).one()
    assert (row.path, row.graf_outcome, row.jev["topic"]) == (
        "enigma",
        "shop",
        [["payment", 0.6], ["return", 0.4]],
    )


@pytest.mark.parametrize("language", ["ru", "uz"])
def test_ux_d03_origin_topic_text_passes_compliance_checks(language: Any) -> None:
    from src.consultant.enigma import checks

    origin = data.TOPIC["origin"]
    txt = getattr(origin.text, language)
    refs = shop.reference(language, ["origin"])
    subj = checks.Subject(
        language,
        "shop",
        frozenset(),
        frozenset(),
        checks.numbers(refs),
        (
            getattr(data.RULES.instruction_sentence, language),
            getattr(data.RULES.opening_question, language),
        ),
        refs,
        (
            "кто производитель и есть ли сертификаты?"
            if language == "ru"
            else "ishlab chiqaruvchi kim va sertifikatlar bormi?",
        ),
    )
    assert checks.check(txt, subj, checks.Names.of({})) is None
