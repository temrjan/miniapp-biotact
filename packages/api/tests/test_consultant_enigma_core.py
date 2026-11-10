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
    cores["units"][1]["products"][0]["shop_id"] = cores["units"][0]["products"][0]["shop_id"]
    cores["units"][1]["pregnancy"] = []


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
    assert data.load(tmp_path)[3] == data.DATA  # the copy itself is valid
    cores = json.loads((tmp_path / "cores.json").read_text(encoding="utf-8"))
    spoil(cores)
    (tmp_path / "cores.json").write_text(json.dumps(cores, ensure_ascii=False), encoding="utf-8")
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
        ("болит спина 4 дня", False),
    ],
)
def test_n03_number_rule_boundaries(text: str, expected: bool) -> None:
    assert nets.number_rule(text) is expected


# --- J-01, J-02: the request -------------------------------------------------------------------


def answer(unit: str, item: str = "none", name: str = "none", **units: float) -> dict[str, Any]:
    """A Jev body whose top options are the given ones; `units` adds unit probabilities."""
    return {
        "model": SERVED,
        "answers": {
            "unit": {"choice": unit, "probabilities": {unit: 0.6, **units}},
            "item": {"choice": item, "probabilities": {item: 0.9}},
            "name": {"choice": name, "probabilities": {name: 0.9}},
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
    assert (reading.item[0] if reading.item else None) == item
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
