"""Enigma core (consultant-v2 spec r2, PR-1): the data loader, the deterministic nets, the Jev
request and its reading, the hard rules on the candidates and the item cards. Each test names
the requirement it proves. The Jev boundary is a double (httpx.MockTransport); the database
of the item cards is real."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.consultant.enigma import data, jev, nets, rules
from src.consultant.enigma.cards import item_cards
from src.consultant.knowledge import Knowledge
from src.products.models import Product
from tests.test_consultant import seed

PHRASES = json.loads(
    (Path(__file__).parent / "enigma_fixtures" / "phrases.json").read_text(encoding="utf-8")
)
SERVED = "typesafe/jev-1.14-20261014"


def texts(kind: str) -> list[str]:
    return [p["text"] for p in PHRASES if p["kind"] == kind]


# --- D-02: an invalid data file stops the load ------------------------------------------------


def _unknown_key(cores: dict[str, Any]) -> None:
    cores["units"][0]["note"] = "x"


def _missing_key(cores: dict[str, Any]) -> None:
    del cores["units"][0]["title"]


def _no_product(cores: dict[str, Any]) -> None:
    cores["units"][0]["products"] = []
    cores["units"][0]["pregnancy"] = []
    for problem in cores["units"][0]["problems"]:
        problem["applies_to"] = None
    cores["names"] = []


def _no_wording(cores: dict[str, Any]) -> None:
    problem = cores["units"][0]["problems"][0]
    problem["phrases"]["uz_cyrl"] = []
    problem["diagnoses"]["uz_cyrl"] = []


def _two_units(cores: dict[str, Any]) -> None:
    cores["units"][1]["products"].append(cores["units"][0]["products"][0])


def _unit_and_item(cores: dict[str, Any]) -> None:
    cores["items"][0]["products"][0]["shop_id"] = cores["units"][0]["products"][0]["shop_id"]


def _reserved(cores: dict[str, Any]) -> None:
    cores["items"][0]["id"] = "none"


@pytest.mark.parametrize(
    "spoil",
    [_unknown_key, _missing_key, _no_product, _no_wording, _two_units, _unit_and_item, _reserved],
)
def test_d02_invalid_cores_file_is_rejected(tmp_path: Path, spoil: Any) -> None:
    for name in data.DATA_FILES:
        (tmp_path / name).write_bytes((data.DATA_DIR / name).read_bytes())
    assert data.load(tmp_path)[-1] == data.DATA  # the copy itself is valid
    cores = json.loads((tmp_path / "cores.json").read_text(encoding="utf-8"))
    spoil(cores)
    (tmp_path / "cores.json").write_text(json.dumps(cores, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        data.load(tmp_path)


def test_d02_invalid_net_row_is_rejected(tmp_path: Path) -> None:
    for name in data.DATA_FILES:
        (tmp_path / name).write_bytes((data.DATA_DIR / name).read_bytes())
    lists = json.loads((tmp_path / "nets.json").read_text(encoding="utf-8"))
    del lists["doctor"][0]["match"]
    (tmp_path / "nets.json").write_text(json.dumps(lists, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        data.load(tmp_path)


# --- N-01…N-03: the nets on the phrase set of trial 1 -----------------------------------------


def test_n01_ambulance_list_on_the_phrase_set() -> None:
    def strong(text: str) -> bool:
        found = nets.ambulance(text, False)
        return found is not None and found.tier == "strong"

    assert len(texts("emergency")) == 12
    assert len(texts("other")) == 529
    assert [t for t in texts("emergency") if not strong(t)] == []
    assert [t for t in texts("other") if strong(t)] == []


def test_n02_n03_doctor_list_and_number_rule_on_the_phrase_set() -> None:
    def fires(text: str) -> bool:
        found = nets.signs(text)
        return found.doctor or found.number

    assert len(texts("doctor")) == 12
    assert [t for t in texts("doctor") if not fires(t)] == []
    assert [t for t in texts("other") if fires(t)] == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("температура 38.5", False),
        ("температура 38.6", True),
        ("жар 3 дня", False),
        ("жар 4 дня", True),
        ("насморк 11 дней", True),
        ("кашель 3 недели", False),
        ("каждый месяц сопли", False),
        ("каждые 2 недели насморк", False),
        ("болит спина 4 дня", False),
    ],
)
def test_n03_number_rule_boundaries(text: str, expected: bool) -> None:
    assert nets.number_rule(text) is expected


# --- J-01, J-02: the request -------------------------------------------------------------------


def answer(
    unit: str,
    item: str = "none",
    items: dict[str, float] | None = None,
    name: str = "none",
    topic: str = "none",
    topics: dict[str, float] | None = None,
    **units: float,
) -> dict[str, Any]:
    """A Jev body whose top options are the given ones; `units` adds unit probabilities,
    `items` adds item probabilities (`UX-D01`), and `topics` the shop topic probabilities."""
    return {
        "model": SERVED,
        "answers": {
            "unit": {"choice": unit, "probabilities": {unit: 0.6, **units}},
            "item": {"choice": item, "probabilities": items or {item: 0.9}},
            "name": {"choice": name, "probabilities": {name: 0.9}},
            "topic": {"choice": topic, "probabilities": topics or {topic: 0.9}},
        },
    }


async def test_j01_j02_request_body_and_reading() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer("magniy"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        attempt = await jev.Jev(client, "synthetic-key").ask(["плохо сплю", "и устаю"], "стресс")
    assert attempt.error is None
    assert attempt.reading is not None
    assert attempt.reading.units == (("magniy", 0.6),)
    (request,) = seen
    assert str(request.url) == "https://openrouter.ai/api/v1/systemone"
    assert request.headers["Authorization"] == "Bearer synthetic-key"
    body = json.loads(request.content)
    assert attempt.size == len(request.content)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["state"] == {"earlier": "плохо сплю\nи устаю", "message": "стресс"}
    options = {k: set(q["criteria"]) for k, q in body["questions"].items()}
    assert options == {
        "unit": {u.id for u in data.CORES.units} | {"doctor", "no_product", "unclear"},
        "item": {i.id for i in data.CORES.items} | {"none"},
        "name": {n.id for n in data.CORES.names} | {"none"},
        "topic": {t.id for t in data.SHOP.topics} | {"none"},
    }
    assert {q["type"] for q in body["questions"].values()} == {"choice"}
    assert len(data.CORES.names) == 11


# --- J-03: the reading -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "units", "item", "named", "name_only"),
    [
        (answer("unclear", name="magniy", magniy=0.2), ["magniy"], None, [18, 19], True),
        (
            answer("immunocomplex", name="zincum", zincum_c_d3=0.3),
            ["immunocomplex", "zincum_c_d3", "zincum"],
            None,
            [17],
            False,
        ),
        (answer("unclear", item="termos"), [], "termos", [], False),
        (answer("magniy", item="braslet", neo=0.1), ["magniy", "neo"], "braslet", [], False),
    ],
    ids=["name only", "name with a complaint", "item only", "unit and item"],
)
def test_j03_reading(
    body: dict[str, Any], units: list[str], item: str | None, named: list[int], name_only: bool
) -> None:
    reading = jev.read(body)
    assert isinstance(reading, jev.Reading)
    assert [u for u, _ in reading.units] == units
    assert [k for k, _ in reading.items] == ([item] if item else [])
    assert reading.named == named
    assert reading.name_only is name_only
    assert set(reading.special) == {"doctor", "no_product", "unclear"}


# --- FT-04: the hard rules on the candidates ---------------------------------------------------


def test_ft04_hard_rules_on_candidates() -> None:
    reading = jev.read(answer("magniy", immunocomplex=0.3, neurocomplex_kids=0.1))
    assert isinstance(reading, jev.Reading)
    clear = nets.signs("плохо сплю")

    def units(*messages: str) -> dict[str, rules.Candidate]:
        found = rules.candidates(reading, [clear, *(nets.signs(m) for m in messages)])
        return {c.unit.id: c for c in found.units}

    assert list(units()) == ["magniy", "immunocomplex", "neurocomplex_kids"]
    assert units()["immunocomplex"].pair_line is not None
    # (a) a stated intolerance empties the candidates
    assert units("у меня непереносимость компонентов") == {}
    # (b) diarrhea removes the unit magniy
    assert list(units("у меня понос")) == ["immunocomplex", "neurocomplex_kids"]
    # (c) pregnancy keeps only the products whose instruction has the pregnancy line
    pregnant = units("я беременна")
    assert list(pregnant) == ["magniy", "immunocomplex"]
    assert [p.shop_id for p in pregnant["immunocomplex"].products] == [7]
    assert pregnant["immunocomplex"].pair_line is None


# --- IT-01: item cards from the active catalogue -----------------------------------------------


async def test_it01_item_cards_from_the_catalogue(test_db: Any) -> None:
    await seed(test_db)
    test_db.add_all(
        [
            Product(id=10, name="Блендер", slug="blender", price=300, is_active=True),
            Product(id=13, name="Чайник", slug="chaynik", price=300, is_active=False),
        ]
    )
    await test_db.commit()
    cards = await item_cards(test_db, [10, 13, 20], "ru")
    assert list(cards) == [10]  # 13 is inactive; 20 is a supplement, not an item
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    supplement = (await Knowledge(sessions).snapshot("ru")).products[20]
    assert set(cards[10]) == set(supplement)
    assert cards[10]["name"] == "Блендер"


def test_ux_d01_multi_item_reading_and_candidates() -> None:
    assert jev.ITEM_SECOND == 0.13
    # Top "none" -> () even if another item is above threshold.
    r_none = jev.read(answer("unclear", item="none", items={"none": 0.6, "blender": 0.3}))
    assert isinstance(r_none, jev.Reading)
    assert r_none.items == ()
    # Single item with "none" second (even > 0.13) -> only top item.
    r_one = jev.read(
        answer("unclear", item="blender", items={"blender": 0.5, "none": 0.4, "chaynik": 0.1})
    )
    assert isinstance(r_one, jev.Reading)
    assert r_one.items == (("blender", 0.5),)
    # Second item >= 0.13 included (even if "none" sits in between).
    r_two = jev.read(
        answer("unclear", item="blender", items={"blender": 0.5, "none": 0.3, "chaynik": 0.13})
    )
    assert isinstance(r_two, jev.Reading)
    assert r_two.items == (("blender", 0.5), ("chaynik", 0.13))
    # Below 0.13 excluded.
    r_below = jev.read(answer("unclear", item="blender", items={"blender": 0.5, "chaynik": 0.12}))
    assert isinstance(r_below, jev.Reading)
    assert r_below.items == (("blender", 0.5),)
    # Equal probability tie broken by CORES.items display order.
    first_id, second_id = data.CORES.items[0].id, data.CORES.items[1].id
    r_tie = jev.read(
        answer("unclear", item=second_id, items={second_id: 0.4, first_id: 0.4, "none": 0.2})
    )
    assert isinstance(r_tie, jev.Reading)
    assert r_tie.items == ((first_id, 0.4), (second_id, 0.4))
    # Candidates.items populated cleanly, and cleared to () on intolerance.
    found_ok = rules.candidates(r_two, [nets.signs("есть блендер или чайник?")])
    assert [it.id for it in found_ok.items] == ["blender", "chaynik"]
    found_intol = rules.candidates(r_two, [nets.signs("у меня аллергия на всё, боюсь брать")])
    assert found_intol.items == ()
    assert found_intol.units == ()


def test_ux_d02_pan_allergy_and_dermatitis_nets() -> None:
    positives = [
        "у меня аллергия на всё, боюсь брать",
        "аллергия на все медикаменты",
        "непереносимость любых препаратов и витаминов",
        "hamma dorilarga allergiyam bor",
        "ҳамма дорига аллергиям бор",
        "аллергия на ваш магний",
    ]
    for msg in positives:
        assert nets.signs(msg).intolerance is True, msg

    negatives = [
        "аллергический дерматит у взрослого",
        "у ребенка аллергия на всё молочное",
        "bahorda hamma narsa gullaganda allergiyam bor",
        "баҳорда ҳамма нарса гуллаганда аллергиям бор",
        "у меня аллергия на всё, даже на еду",
        "боюсь пить магний, вдруг будет сонливость",
    ]
    for msg in negatives:
        assert nets.signs(msg).intolerance is False, msg

    # Zero false intolerance hits across phrases.json.
    hits = [p["text"] for p in PHRASES if nets.signs(p["text"]).intolerance]
    assert hits == []


def test_ux_d04_bones_support_and_non_acute_pain_rule() -> None:
    from src.consultant.enigma import checks

    assert data.RULES.version == "compliance r3 (2026-11-13)"
    assert data.RULES.treatment_stems == []
    calciy = data.UNIT["calciy"]
    bones = next(p for p in calciy.problems if p.id == "bones_support")
    for text in bones.phrases.all():
        assert "бол" not in text.lower()
        assert "og'riq" not in text.lower()
        assert "оғриқ" not in text.lower()
    assert any("non-acute pain or aching in joints or bones" in r for r in data.RULES.offer_rules)
    assert any(
        "not pain in the back, lower back, spine, neck or head" in r for r in data.RULES.offer_rules
    )

    assert nets.signs("упала с лестницы, колено опухло и острая боль").doctor is True
    assert nets.signs("у мамы болят суставы и кости").doctor is False

    names = checks.Names.of({})
    ru_answer = (
        "Лучше обратиться к врачу, чтобы уточнить причину боли. Биодобавка не лечит боль и "
        "заболевания суставов; для поддержки костей можно рассмотреть CALCIY® TRIACTIVE D3. "
        f"{data.RULES.phrases['disease'].ru} {data.RULES.instruction_sentence.ru}"
    )
    uz_answer = (
        "Og'riq sababini aniqlash uchun shifokorga murojaat qilgan ma'qul. Biologik faol qo'shimcha "
        "og'riq yoki bo'g'im kasalligini davolamaydi; suyaklarni qo'llab-quvvatlash uchun "
        f"CALCIY® TRIACTIVE D3 ni ko'rib chiqishingiz mumkin. {data.RULES.phrases['disease'].uz} "
        f"{data.RULES.instruction_sentence.uz}"
    )
    for lang, txt, cust in (
        ("ru", ru_answer, ("у мамы болят суставы и кости",)),
        ("uz", uz_answer, ("onamning bo'g'imlari va suyaklari og'riyapti",)),
    ):
        subj = checks.Subject(
            lang,
            "offer",
            frozenset({15}),
            frozenset({15}),
            frozenset(),
            (
                getattr(data.RULES.instruction_sentence, lang),
                getattr(data.RULES.phrases["disease"], lang),
                getattr(bones.line, lang),
            ),
            (),
            cust,
        )
        assert checks.check(txt, subj, names) is None

    no_instr = ru_answer.replace(f" {data.RULES.instruction_sentence.ru}", "")
    subj_ru = checks.Subject(
        "ru",
        "offer",
        frozenset({15}),
        frozenset({15}),
        frozenset(),
        (data.RULES.instruction_sentence.ru, data.RULES.phrases["disease"].ru, bones.line.ru),
        (),
        ("у мамы болят суставы и кости",),
    )
    assert checks.check(no_instr, subj_ru, names) == "V-05"
