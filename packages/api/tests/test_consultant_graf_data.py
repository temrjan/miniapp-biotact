"""Graf data r4 self-tests: every expectation comes from the data files themselves (their
self_test rows, phrase sets and README age walk), never from the engine under test."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from src.consultant import graf, provider

FIXTURES = Path(__file__).parent / "graf_fixtures"
SELF_TEST = graf.SAFETY["self_test"]

# SHA-256 of graf-data-r4 as committed in DocHub e1f0c65 (assignment: copy byte for byte).
R4 = {
    "products.json": "a9449a5ec94f9973c14130e62a53362648ccf1480491f21d26dceddf53995a04",
    "situations.json": "441374406edc32fcc9a72621456544f24570148da160bc06f596565b4a3623f2",
    # spec r6 S4: r4 plus the template info.how_to_buy.
    "templates.json": "c1a2043d53c0e25e35e4dd089f504a42e46276875c3b89e2afe7bbd52924fa8e",
    "safety.json": "635519f2899e3c8cfd7098337c40a20b8c56e9fa78266a88fe7a765a766d942a",
    "phrases-r3.json": "6f90ae092de34b568a4a47b584684e22fd420920a19e3639ec79209fa0803cca",
    "phrases-two-complaints.json": (
        "325f2a01efa9f3fa74b4dfdd32aedfa8e7bf74fee59ac8ae59264d0eec661335"
    ),
}


@pytest.mark.parametrize(("name", "digest"), R4.items())
def test_data_is_r4_byte_for_byte(name, digest):
    path = graf.DATA_DIR / name if name in graf.DATA_FILES else FIXTURES / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_engine_order_equals_the_implemented_order():
    steps = tuple(line.split(" ", 1)[0] for line in graf.ENGINE["order"])
    assert steps == graf.ENGINE_ORDER


def test_knowledge_release_covers_graf_code_and_every_data_file():
    source = Path(provider.__file__).read_text()
    assert '"graf.py"' in source
    for name in graf.DATA_FILES:
        assert f'"{name}"' in source
    assert set(graf.DATA_FILES) == {p.name for p in graf.DATA_DIR.glob("*.json")}


@pytest.mark.parametrize("row", SELF_TEST["entry_rows"], ids=lambda r: f"{r['entry']}:{r['kind']}")
def test_entry_rows_fire_exactly_as_the_data_records(row):
    fired = [f"{i}:{r}" for i, r in graf.scan(row["text"], graf.ENTRIES, row.get("slots"))]
    assert fired == row["fires"]


def test_every_entry_has_positive_and_negative_rows_and_fires():
    rows = SELF_TEST["entry_rows"]
    for entry in graf.ENTRIES:
        mine = [r for r in rows if r["entry"] == entry["id"]]
        assert {r["kind"] for r in mine} == {"positive", "negative"}, entry["id"]
        assert any(f"{entry['id']}:hit" in r["fires"] for r in mine if r["kind"] == "positive")
    totals = SELF_TEST["totals"]
    assert len(graf.ENTRIES) == totals["entries"]
    assert (
        sum(e["class"] == "ambulance" and e["tier"] == "strong" for e in graf.ENTRIES)
        == (totals["ambulance_strong_entries"])
    )


@pytest.mark.parametrize(
    "row", [r for r in SELF_TEST["review_nb3_rows"] if "tier" in r], ids=lambda r: r["text"]
)
def test_review_probes_keep_their_tier(row):
    level, hits = graf.tier(row["text"])
    assert level == row["tier"]
    assert [f"{i}:{r}" for i, r in hits] == row["fires"]
    if row["expect"] == "not_strong":
        assert level != "strong"
    elif row["expect"] in {"strong", "weak", "none"}:
        assert level == row["expect"]


def test_stretch_marks_after_birth_reach_no_doctor_entry():
    (row,) = [r for r in SELF_TEST["review_nb3_rows"] if r["expect"] == "no doctor hit"]
    hits = graf.scan(row["text"], graf.ENTRIES)
    assert [f"{i}:{r}" for i, r in hits] == row["fires"]
    assert not [i for i, r in hits if graf.ENTRY[i]["class"] == "doctor"]
    # The doctor channel can carry such a hit: an injury with swelling does reach it.
    assert ("doc.injury.ru", "hit") in graf.scan("упал, сильный отек ноги", graf.ENTRIES)


def _parsed(row):
    kind, text = row["parser"], row["text"]
    if kind.startswith("age"):
        return graf.parse_age(text, reply="reply" in kind)[0]
    if kind == "duration_days":
        return graf.parse_duration(text, graf.parse_age(text)[1])
    return {
        "temperature": graf.parse_temperature,
        "yes_no": graf.parse_yes_no,
        "pregnancy_reply": graf.parse_pregnancy_reply,
        "whom": graf.parse_whom,
    }[kind](text)


@pytest.mark.parametrize(
    "row", SELF_TEST["parser_rows"], ids=lambda r: f"{r['parser']}:{r['text']}"
)
def test_parser_rows(row):
    got, expected = _parsed(row), row["expect"]
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        assert got == pytest.approx(expected, abs=0.02)
    else:
        assert got is expected if isinstance(expected, bool) else got == expected


def test_phrase_sets_tier_as_the_data_build_records():
    phrases = json.loads((FIXTURES / "phrases-r3.json").read_text())["phrases"]
    two = json.loads((FIXTURES / "phrases-two-complaints.json").read_text())["phrases"]
    tiers = SELF_TEST["phrase_tiers"]
    emergency = [graf.tier(p["text"])[0] for p in phrases if p["expect"] == "emergency"]
    other = [graf.tier(p["text"])[0] for p in phrases if p["expect"] != "emergency"]
    assert len(emergency) == tiers["phrases_r3_emergency"]["total"]
    assert emergency.count("strong") == tiers["phrases_r3_emergency"]["strong"]
    assert len(other) == tiers["phrases_r3_other"]["total"]
    assert set(other) == {"none"}
    assert {graf.tier(p["text"])[0] for p in two} == {"none"}


def test_doctor_rule_conditions_are_the_implemented_ones():
    rules = {r["id"]: r for r in graf.SAFETY["doctor_rules_with_slots"]}
    implemented = {
        "fever_duration": "age >= 18 and duration_days > 3, or age < 18 and duration_days >= 5",
        "cold_over_10_days": "duration_days > 10",
        "cough_over_3_weeks": "duration_days > 21",
        "infant_fever": (
            "age < 0.25 and temperature >= 38, or 0.25 <= age < 0.5 and temperature >= 39"
        ),
        "infant_diarrhea": "age < 1",
        "infant_weight": "age < 1",
    }
    assert {k: r["condition"] for k, r in rules.items() if "condition" in r} == implemented
    for rule_id, entries in ((k, r["entries"]) for k, r in rules.items() if "entries" in r):
        assert [e for e in graf.DOCTOR if e["id"].startswith(entries.rstrip("*"))], rule_id


@pytest.mark.parametrize(
    ("rule", "age", "duration", "temperature", "fires"),
    [
        ("fever_duration", 30, 3, None, False),
        ("fever_duration", 30, 4, None, True),
        ("fever_duration", 8, 4, None, False),
        ("fever_duration", 8, 5, None, True),
        ("fever_duration", None, 9, None, False),
        ("cold_over_10_days", 30, 10, None, False),
        ("cold_over_10_days", 30, 11, None, True),
        ("cough_over_3_weeks", 30, 21, None, False),
        ("cough_over_3_weeks", 30, 22, None, True),
        ("infant_fever", 0.0, None, 38.0, True),
        ("infant_fever", 0.2, None, 37.9, False),
        ("infant_fever", 0.25, None, 38.5, False),
        ("infant_fever", 0.33, None, 39.0, True),
        ("infant_fever", 0.5, None, 40.0, False),
        ("infant_diarrhea", 0.0, None, None, True),
        ("infant_diarrhea", 1.0, None, None, False),
    ],
)
def test_doctor_rule_boundaries_follow_task_req_3(rule, age, duration, temperature, fires):
    # TASK req 3 level 2 and selection-rules r2 rule 3; age 0 is a real age, not "unknown".
    assert graf.slot_rule(rule, age, duration, temperature) is fires


def test_strong_ambulance_entries_use_the_three_emergency_texts():
    strong = {e["template"] for e in graf.AMBULANCE if e["tier"] == "strong"}
    assert strong == {"emergency.general", "emergency.self_harm", "emergency.pregnancy"}
    assert {e["template"] for e in graf.AMBULANCE if e["tier"] == "weak"} == {"emergency.check"}


def _composed():
    question_templates = {q["template"] for q in graf.CONTINUATION["question_map"]}
    relation = graf.TEMPLATES["relation"]
    for ack in graf.CONTINUATION["ack_map"]:
        lang = ack["lang"]
        for template in sorted(question_templates):
            for variant in graf.TEXT[template][lang]:
                fills = (
                    [
                        variant.replace("{rel_dat_ru}", r["dat_ru"]).replace(
                            "{rel_nom_uz_cap}", r["nom_uz"][:1].upper() + r["nom_uz"][1:]
                        )
                        for k, r in relation.items()
                        if k != "self"
                    ]
                    if "{" in variant
                    else [variant]
                )
                for text in fills:
                    yield ack, template, ack["text"] + " " + text


def test_every_question_message_decodes_to_its_situation_and_slot():
    decoded = 0
    for ack, template, message in _composed():
        asked = graf.decode(message)
        assert asked == graf.Asked(ack["situation"], template), message
        decoded += 1
    assert decoded >= graf.CONTINUATION["self_test"]["composed_messages"]
    for check in graf.CONTINUATION["check_map"]:
        assert graf.decode(check["text"]) is None


def test_an_offer_or_doctor_text_after_an_ack_never_decodes_as_a_question():
    ack = graf.CONTINUATION["ack_map"][0]["text"]
    for template in ("offer.one", "doctor.general", "no_product.general"):
        for text in graf.TEXT[template]["ru"]:
            assert graf.decode(ack + " " + text) is None


# --- age walk: the "Age walk" section of graf-data-r4/README.md, copied verbatim --------

WALK_AGES = [0, 0.5, 2, 4, 5, 7, 8, 12, 15, 16, 17, 18, 30, 65]


def _walk_rows():
    text = (FIXTURES / "age-walk-r4.md").read_text()
    table = text.split("## Age walk", 1)[1]
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(WALK_AGES) + 1 or cells[0] in {"situation", "---"}:
            continue
        for age, cell in zip(WALK_AGES, cells[1:], strict=True):
            yield cells[0], age, cell


def _facts(situation, age):
    spec = graf.SITUATION[situation]
    facts = graf.Facts(age=float(age), duration=2.0)
    if spec["pregnancy_question"] == "implied_yes":
        facts.pregnant, facts.sex = True, "female"
    else:
        facts.pregnant, facts.sex = False, "male"
    return facts


# The walk generator sets the pregnancy slot at every age; engine.slots says the slot is never
# set for a person under 15, so those cells are covered by test_no_pregnancy_under_15 instead.
WALK = [
    (situation, age, cell)
    for situation, age, cell in _walk_rows()
    if not (graf.SITUATION[situation]["pregnancy_question"] == "implied_yes" and age < 15)
]


def test_no_pregnancy_under_15():
    for situation in ("pregnancy_general", "pregnancy_tone_cramps", "pregnancy_teeth_bones"):
        facts = graf.Facts(age=14.0, pregnant=True, sex="female")
        outcome = graf.decide(situation, facts)
        assert outcome.situation == graf.SITUATION[situation]["without_pregnancy_word"]


@pytest.mark.parametrize(("situation", "age", "cell"), WALK)
def test_age_walk(situation, age, cell):
    outcome = graf.decide(situation, _facts(situation, age))
    kinds = {
        "doctor_soon": "doctor",
        "no_product": "no_product",
        "clarify": "question",
        "emergency": "emergency",
        "uncovered": "uncovered",
        "step_aside": "step_aside",
    }
    if cell in kinds:
        assert outcome.kind == kinds[cell]
        return
    if cell == "reroute":
        assert outcome.situation != situation
        assert outcome.kind == "offer"
        return
    offered = [p for p in cell.split(" + ") if "dropped" not in p]
    if not offered:
        assert outcome.kind == "no_product"
        return
    assert outcome.kind == "offer"
    expected = [re.fullmatch(r"(\w+) \((.+)\)", p).groups() for p in offered]
    got = []
    for item in outcome.offer:
        band = graf.band(graf.PRODUCT[item["product"]], item["dose"], float(age))
        got.append((item["product"], band["say_ru"].replace(" в день", "")))
    assert got == [(p, d) for p, d in expected]


# --- probes of reviews r1-r3 not already among the data's self-test rows ------------------


@pytest.mark.parametrize(
    ("text", "level"),
    [
        # review r1 I3 false positives (sport cramps, slow rash, breastfeeding, eyes)
        ("после тренировки судорога в ноге", "none"),
        ("сыпь не проходит уже месяц", "none"),
        ("кормлю грудью, боль в груди при кормлении", "none"),
        ("emizaman, ko'krak og'riyapti", "none"),
        ("глаза налились кровью", "none"),
        # review r1 I3 false negatives: third person, reversed order
        ("мама задыхается", "strong"),
        ("ребёнок задыхается", "strong"),
        ("упала в обморок", "strong"),
        ("теряет сознание", "strong"),
        ("губы синие", "strong"),
        ("кашляет кровью", "strong"),
        ("грудь давит", "strong"),
        ("тяжело дышать", "weak"),
        # review r1 B1 bypass phrasings
        ("Задыхаюсь, чем лечить?", "strong"),
        ("Пью BIFOLAK, давит в груди", "strong"),
        # plan r4 §4: plain chest soreness is not a sign without a severity word
        ("болит в груди", "none"),
    ],
)
def test_earlier_review_probes(text, level):
    assert graf.tier(text)[0] == level


@pytest.mark.parametrize(
    ("text", "slot", "expected"),
    [
        ("qanday vitamin ichsam bo'ladi", "disease", False),  # r1 I3: qand only as a word
        ("қандай витамин", "disease", False),
        ("ertalab", "sex", None),  # r1 I3: 'er' whole word only
        ("эртага", "sex", None),
        ("я женат", "sex", None),
        ("нет, не беременна", "pregnant", None),  # r1 B3
        ("мы планируем беременность, мужу 32 года", "pregnant", None),
        ("я беременна, хочу что-нибудь для иммунитета", "pregnant", True),  # r3 I3
        ("беременна, сводит икры, хотим что-то безопасное", "pregnant", True),
        ("ребёнок часто болеет", "child", True),  # r1 B3
        ("o'g'lim tez-tez kasal", "child", True),
        ("ўғлим касал", "child", True),
        ("для дочери 25 лет", "age", 25.0),
        ("для дочери 25 лет", "whom", "daughter"),
        ("пью какао и черный чай", "doctor", False),  # r3 S2
        ("от всего сердца спасибо", "doctor", False),
        ("у ребенка аллергия, подходит ли состав?", "intolerance", False),
        ("школьник плохо запоминает", "topic_adaptation", False),  # r3 nit
    ],
)
def test_earlier_review_word_probes(text, slot, expected):
    facts = graf.Facts()
    facts.absorb(text)
    words = graf.scan(text, graf.ENTRIES)
    got = {
        "disease": facts.disease,
        "sex": facts.sex,
        "pregnant": facts.pregnant,
        "child": facts.child_word,
        "age": facts.age,
        "whom": facts.whom,
        "doctor": any(graf.ENTRY[i]["class"] == "doctor" for i, r in words if r == "hit"),
        "intolerance": any(i.startswith("hx.intol") for i, r in words if r == "hit"),
        "topic_adaptation": "topic_adaptation" in facts.topics,
    }[slot]
    assert got == expected
