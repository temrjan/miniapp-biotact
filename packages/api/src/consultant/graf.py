"""Graf: deterministic product offer for complaints that name no product (plan r4).

Every word list, parser, route and customer-visible string comes from graf_data/*.json.
Graf stores nothing: the conversation state is rebuilt from the bounded history on each
turn, and log lines carry only a situation id, an outcome class or a served model id.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from src.consultant.contracts import Turn
from src.consultant.knowledge import Evidence

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "graf_data"
DATA_FILES = ("products.json", "situations.json", "templates.json", "safety.json")


def _load(name: str) -> dict[str, Any]:
    value = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(name)
    return value


PRODUCTS: dict[str, Any] = _load("products.json")
SITUATIONS: dict[str, Any] = _load("situations.json")
TEMPLATES: dict[str, Any] = _load("templates.json")
SAFETY: dict[str, Any] = _load("safety.json")

ENGINE = SITUATIONS["engine"]
SITUATION = {s["id"]: s for s in SITUATIONS["situations"]}
PRODUCT = {p["id"]: p for p in PRODUCTS["products"]}
TEXT = TEMPLATES["templates"]
ENTRIES: list[dict[str, Any]] = SAFETY["entries"]
VOCAB: list[dict[str, Any]] = SAFETY["vocab"]
NEGATION: dict[str, Any] = SAFETY["negation"]
PARSERS: dict[str, Any] = SAFETY["parsers"]
GROUP = {s["id"]: s["product_group"] for s in SITUATIONS["situations"]}

# Steps of plan r4 §3 in the order this module and service.py run them; a test compares
# them with the leading step numbers of situations.json engine.order.
ENGINE_ORDER: tuple[str, ...] = ("1", "2", "3", "4", "5", "5.1", "5.2", "5.3", "5.4")
ENGINE_ORDER += ("5.5", "5.6", "5.7", "5.8", "6", "7")

# Jev through OpenRouter System One (plan r4 §3.5.1). The instruction sentence is the one
# pilot r3 measured; criteria are the data's jev_criterion_full. The request names the pinned
# 1.14 id (Captain D37 (1)); the served model is still checked against JEV_VERSION.
JEV_URL = "https://openrouter.ai/api/v1/systemone"
JEV_MODEL = "typesafe/jev-1.14"
JEV_VERSION = re.compile(r"typesafe/jev-1\.14(?:-[0-9A-Za-z.]+)?")
JEV_TIMEOUT = 3.0
JEV_INSTRUCTIONS = (
    "The message is from a customer of a dietary supplement shop, written in Russian or "
    "Uzbek (Latin or Cyrillic), often with typos. Which situation does the customer describe?"
)
THRESHOLD = 0.7
SECOND = float(ENGINE["second_complaint"]["threshold"])

# --- text --------------------------------------------------------------------------------

APOSTROPHES = str.maketrans(dict.fromkeys("‘’ʻʼ`´", "'"))
TOKEN = re.compile(r"[0-9a-zа-яёўқғҳ']+")
CLAUSE = re.compile(r"[,.;:!?\n—–]+|\s-\s")


def norm(text: str) -> str:
    """Lower case and ASCII apostrophe; no yo-to-ye folding (NI1)."""
    return text.translate(APOSTROPHES).lower()


def tokens(text: str) -> list[str]:
    return [t.strip("'") for t in TOKEN.findall(norm(text)) if t.strip("'")]


@dataclass(frozen=True)
class Words:
    toks: tuple[str, ...]
    clauses: tuple[int, ...]

    @staticmethod
    def of(text: str) -> "Words":
        toks: list[str] = []
        clauses: list[int] = []
        for index, part in enumerate(CLAUSE.split(norm(text))):
            found = tokens(part)
            toks.extend(found)
            clauses.extend([index] * len(found))
        return Words(tuple(toks), tuple(clauses))


def _prefix(token: str, stem: str) -> bool:
    # A term 'е' also accepts 'ё' in the message; a term 'ё' needs 'ё' (letter_rule).
    if "ё" not in stem:
        return token.replace("ё", "е").startswith(stem)
    return len(token) >= len(stem) and all(
        a == b or (a == "е" and b == "ё") for a, b in zip(stem, token, strict=False)
    )


def _token_match(token: str, part: str, mode: str, not_prefix: Sequence[str]) -> bool:
    matched = (
        _prefix(token, part) and len(token) == len(part) if mode == "word" else _prefix(token, part)
    )
    return matched and not any(_prefix(token, p) for p in not_prefix)


def positions(
    words: Words, item: str, mode: str, not_prefix: Sequence[str] = ()
) -> list[tuple[int, ...]]:
    """Every run of consecutive tokens matching a (possibly multi-word) item."""
    parts = norm(item).split()
    toks = words.toks
    return [
        tuple(range(i, i + len(parts)))
        for i in range(len(toks) - len(parts) + 1)
        if all(_token_match(toks[i + j], parts[j], mode, not_prefix) for j in range(len(parts)))
    ]


def any_item(words: Words, items: Iterable[str], mode: str = "stem") -> bool:
    return any(positions(words, item, mode) for item in items)


def negated(words: Words, matched: Sequence[int]) -> bool:
    inside = set(matched)
    toks, clauses = words.toks, words.clauses
    for p in matched:
        for j in range(max(0, p - NEGATION["window_before"]), p):
            if j not in inside and clauses[j] == clauses[p] and toks[j] in NEGATION["before"]:
                return True
    last = max(matched)
    for j in range(last + 1, min(len(toks), last + 1 + NEGATION["window_after"])):
        if j not in inside and clauses[j] == clauses[last] and toks[j] in NEGATION["after"]:
            return True
    return False


def _near(words: Words, entry: dict[str, Any]) -> list[tuple[int, ...]]:
    mode, nap, window = entry["match"], entry.get("not_prefix", []), entry["window"]
    groups: list[list[tuple[int, ...]]] = []
    for group in entry["near"]:
        found = [span for item in group for span in positions(words, item, mode, nap)]
        if not found:
            return []
        groups.append(found)
    hits: list[tuple[int, ...]] = []

    def walk(k: int, chosen: tuple[int, ...]) -> None:
        if chosen and (max(chosen) - min(chosen) >= window or len(set(chosen)) != len(chosen)):
            return
        if k == len(groups):
            hits.append(tuple(sorted(chosen)))
            return
        for span in groups[k]:
            walk(k + 1, chosen + span)

    walk(0, ())
    return hits


def run_entry(words: Words, entry: dict[str, Any], slots: dict[str, bool]) -> str | None:
    """'hit', 'negated' or None, per safety.json match_spec and negation."""
    if entry.get("unless_any") and any_item(words, entry["unless_any"]):
        return None
    slot = entry.get("requires_slot")
    if entry.get("requires_any"):
        context = any(
            not negated(words, span)
            for item in entry["requires_any"]
            for span in positions(words, item, "stem")
        )
        if not context and not (slot and slots.get(slot)):
            return None
    # requires_slot is read only together with requires_any, as in the data's self-test
    # (doc.dehyd.* rows fire without the child slot).
    if "near" in entry:
        spans = _near(words, entry)
    else:
        nap = entry.get("not_prefix", [])
        spans = [s for t in entry["terms"] for s in positions(words, t, entry["match"], nap)]
    if not spans:
        return None
    if entry.get("negation", True) is False:
        return "hit"
    return "negated" if all(negated(words, s) for s in spans) else "hit"


def scan(
    text: str, entries: Iterable[dict[str, Any]], slots: dict[str, bool] | None = None
) -> list[tuple[str, str]]:
    words = Words.of(text)
    found = []
    for entry in entries:
        result = run_entry(words, entry, slots or {})
        if result:
            found.append((entry["id"], result))
    return found


AMBULANCE = [e for e in ENTRIES if e["class"] == "ambulance"]
DOCTOR = [e for e in ENTRIES if e["class"] == "doctor"]
INTOLERANCE = [e for e in ENTRIES if e["action"] == "intolerance_answer"]
DIARRHEA = [e for e in ENTRIES if e["action"] == "route_to_diarrhea"]
PREGNANCY_WORDS = [e for e in ENTRIES if e.get("slot") == "pregnant_or_lactating"]
DISEASE = [e for e in ENTRIES if e.get("note_template") == "note_disease"]
TIER = {e["id"]: e["tier"] for e in AMBULANCE}
ENTRY = {e["id"]: e for e in ENTRIES}


def tier(text: str, slots: dict[str, bool] | None = None) -> tuple[str, list[tuple[str, str]]]:
    """Message tier per match_spec.ambulance_tiers: 'strong', 'weak' or 'none'."""
    hits = scan(text, AMBULANCE, slots)
    if any(r == "hit" and TIER[i] == "strong" for i, r in hits):
        return "strong", hits
    if any(r == "negated" or TIER[i] == "weak" for i, r in hits):
        return "weak", hits
    return "none", hits


def vocab(text: str, slot: str, *, negation: bool = True) -> set[Any]:
    words = Words.of(text)
    found = set()
    for item in VOCAB:
        if item["slot"] != slot:
            continue
        for term in item["terms"]:
            spans = positions(words, term, item["match"])
            if any(not negation or not negated(words, s) for s in spans):
                found.add(item["value"])
                break
    return found


# --- parsers (safety.json parsers) --------------------------------------------------------

NUMBER_WORDS: dict[str, float] = {
    k: float(v) for words in PARSERS["number_words"].values() for k, v in words.items()
}
NUM = (
    r"(\d{1,3}(?:[.,]\d)?|"
    + "|".join(re.escape(w) for w in sorted(NUMBER_WORDS, key=len, reverse=True))
    + ")"
)
LEFT = r"(?<![0-9a-zа-яёўқғҳ'])"
RIGHT = r"(?![0-9a-zа-яёўқғҳ'])"
AGE = PARSERS["age"]
DURATION = PARSERS["duration"]
AGE_PATTERNS = [(re.compile(p["re"]), p) for patterns in AGE["patterns"].values() for p in patterns]
MARKERS = {m for marks in AGE["duration_markers"].values() for m in marks}
ADVERBS = set(AGE["duration_adverbs"])
MONTHS = re.compile(r"(мес|месяц|месяца|месяцев)|месячн")
WEEKS = re.compile(r"(недел[яиьюе]|недель)")
DURATION_UNITS = [
    (re.compile(LEFT + NUM + r"\s*" + unit + RIGHT), name)
    for units in DURATION["patterns"].values()
    for name, unit in units.items()
]
ORDINAL = re.compile(DURATION["ordinal_pattern"])
UNIT_FIRST = re.compile(DURATION["unit_first_pattern"])
TEMPERATURE = [
    re.compile(PARSERS["temperature"]["unit_re"]),
    re.compile(PARSERS["temperature"]["fever_word_re"]),
]
REPLY_AGE = re.compile(NUM + r"\s*" + AGE["reply_units"] + "?")


def number(value: str) -> float:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return NUMBER_WORDS[value]


def _guarded(text: str, start: int, end: int, marks: set[str]) -> bool:
    before = tokens(re.split(r"[,.;!?]", text[:start])[-1])[-2:]
    after = tokens(re.split(r"[,.;!?]", text[end:], maxsplit=1)[0])[:2]
    return any(t in marks for t in before + after)


def parse_age(text: str, reply: bool = False) -> tuple[float | str | None, list[tuple[int, int]]]:
    """(years | 'ask' | None, spans read or dropped as an age)."""
    t = norm(text)
    found: set[float] = set()
    spans: list[tuple[int, int]] = []
    for pattern, spec in AGE_PATTERNS:
        for m in pattern.finditer(t):
            if spec.get("value") is not None:
                value = float(spec["value"])
            else:
                nums = [g for g in m.groups() if g and re.fullmatch(NUM, g)]
                if not nums:
                    continue
                value = number(nums[0])
                if spec.get("unit") == "months" or MONTHS.search(m[0]):
                    value = round(value / 12, 2)
                elif WEEKS.search(m[0]):
                    value = round(value * 7 / 365, 2)
            spans.append((m.start(), m.end()))
            if spec["anchor"] == "weak" and _guarded(t, m.start(), m.end(), MARKERS):
                continue
            if spec["anchor"] == "genitive" and _guarded(t, m.start(), m.end(), ADVERBS):
                continue
            found.add(value)
    if len(found) == 1:
        return found.pop(), spans
    if found:
        return "ask", spans
    if reply:
        core = re.sub(r"[.!,]+", " ", t).strip()
        if core in AGE["reply_words"]:
            return float(AGE["reply_words"][core]), [(0, len(t))]
        reply_match = REPLY_AGE.fullmatch(core)
        if reply_match and 0 < number(reply_match[1]) <= 110:
            value, unit = number(reply_match[1]), reply_match[2] or ""
            if re.match(r"(мес|месяц|oy|ой)", unit):
                value = round(value / 12, 2)
            elif unit.startswith("недел"):
                value = round(value * 7 / 365, 2)
            return value, [(0, len(t))]
    return None, spans


def parse_duration(text: str, age_spans: Sequence[tuple[int, int]] = ()) -> float | None:
    t = norm(text)
    for a, b in age_spans:
        t = t[:a] + " " * (b - a) + t[b:]
    found: list[float] = []
    for pattern, unit in DURATION_UNITS:
        for m in pattern.finditer(t):
            if unit == "years" and not _guarded(t, m.start(), m.end(), ADVERBS):
                continue
            found.append(number(m[1]) * DURATION["multipliers_days"][unit])
    found += [float(DURATION["ordinals"][m[1]]) for m in ORDINAL.finditer(t)]
    for m in UNIT_FIRST.finditer(t):
        unit = m[1]
        days = 1 if unit.startswith(("дн", "сут")) else 7 if unit.startswith("недел") else 30
        found.append(number(m[2]) * days)
    if not found:
        joined = " " + " ".join(tokens(t)) + " "
        found = [float(d) for w, d in DURATION["words"].items() if f" {w} " in joined]
    return max(found) if found else None


def parse_temperature(text: str) -> float | None:
    t = norm(text)
    for pattern in TEMPERATURE:
        for m in pattern.finditer(t):
            groups = [g for g in m.groups() if g is not None]
            whole = [g for g in groups if re.fullmatch(r"3[5-9]|4[0-2]", g)]
            if whole:
                i = groups.index(whole[0])
                tail = groups[i + 1] if i + 1 < len(groups) else ""
                decimal = tail if re.fullmatch(r"\d", tail) else "0"
                return float(whole[0] + "." + decimal)
    return None


def parse_yes_no(text: str) -> bool | None:
    values = vocab(text, "yes_no", negation=False)
    if "unknown" in values or len(values) != 1:
        return None
    value = values.pop()
    return value if isinstance(value, bool) else None


LACTATION = SAFETY["vocab_rules"]["pregnancy_reply"]["lactation_reply_words"]


def parse_pregnancy_reply(text: str) -> bool | None:
    if any(r == "hit" for _, r in scan(text, PREGNANCY_WORDS)):
        return True
    words = Words.of(text)
    for terms in LACTATION.values():
        for term in terms:
            if any(not negated(words, s) for s in positions(words, term, "word")):
                return True
    return parse_yes_no(text)


def parse_whom(text: str) -> str | None:
    found = vocab(text, "whom", negation=False)
    if len(found) > 1:
        found.discard("self")
    return found.pop() if len(found) == 1 else None


def one(values: set[Any]) -> Any:
    return next(iter(values)) if len(values) == 1 else None


# --- facts --------------------------------------------------------------------------------

RELATION_SEX = {
    "husband": "male",
    "father": "male",
    "son": "male",
    "wife": "female",
    "mother": "female",
    "daughter": "female",
}
CHILD_RELATIONS = {"son", "daughter", "child"}
TOPICS = sorted({v["slot"] for v in VOCAB if v["slot"].startswith("topic_")})
AGE_SLOT_TEMPLATES = {"ask.whom_age", "ask.age", "ask.age_self", "reask.age"}


@dataclass
class Facts:
    age: float | None = None
    whom: str | None = None
    sex: str | None = None
    pregnant: bool | None = None
    duration: float | None = None
    temperature: float | None = None
    child_word: bool = False
    form_stick: bool = False
    disease: bool = False
    after_clarify: bool = False
    topics: set[str] = field(default_factory=set)
    texts: list[str] = field(default_factory=list)

    @property
    def child(self) -> bool:
        if self.age is not None:
            return self.age < 18
        return self.child_word or self.whom in CHILD_RELATIONS

    def person_sex(self, implied: str | None = None) -> str | None:
        if self.sex:
            return self.sex
        if self.whom in RELATION_SEX:
            return RELATION_SEX[self.whom]
        if self.pregnant:
            return "female"
        return implied

    def preg(self) -> bool:
        """A pregnancy word counts only for a person who can be pregnant (D5)."""
        too_young = (self.age is not None and self.age < 15) or self.whom in CHILD_RELATIONS
        return bool(self.pregnant) and not too_young and self.person_sex() != "male"

    def slots(self) -> dict[str, bool]:
        return {"child": self.child, "pregnant": self.preg()}

    def absorb(self, text: str) -> None:
        """General extraction: fills what is still unknown, accumulates words."""
        self.texts.append(text)
        age, spans = parse_age(text)
        if self.age is None and isinstance(age, float):
            self.age = age
        duration = parse_duration(text, spans)
        if duration is not None:
            self.duration = max(duration, self.duration or 0.0)
        temperature = parse_temperature(text)
        if temperature is not None:
            self.temperature = max(temperature, self.temperature or 0.0)
        if self.whom is None:
            self.whom = parse_whom(text)
        if self.sex is None:
            self.sex = one(vocab(text, "sex"))
        self.child_word = self.child_word or bool(vocab(text, "child"))
        self.form_stick = self.form_stick or bool(vocab(text, "form_pref"))
        if any(r == "hit" for _, r in scan(text, PREGNANCY_WORDS)):
            self.pregnant = True
        self.disease = self.disease or any(r == "hit" for _, r in scan(text, DISEASE))
        self.topics |= {t for t in TOPICS if vocab(text, t)}

    def answer(self, text: str, template: str, situation: str) -> str | bool:
        """Parse a reply to a Graf question. Returns False when unparsed; for a clarifying
        question returns the chosen option's target situation."""
        slot = QUESTION_SLOT[template]
        parsed: str | bool = False
        if slot == "age":
            age, _ = parse_age(text, reply=True)
            if isinstance(age, float):
                self.age, parsed = age, True
        elif slot == "duration":
            age, spans = parse_age(text)
            duration = parse_duration(text, spans)
            if duration is not None:
                self.duration, parsed = duration, True
        elif slot == "temperature":
            temperature = parse_temperature(text)
            if temperature is not None:
                self.temperature, parsed = temperature, True
        elif slot == "sex":
            sex = one(vocab(text, "sex"))
            if sex:
                self.sex, parsed = sex, True
        elif slot == "pregnant_or_lactating":
            reply = parse_pregnancy_reply(text)
            if reply is not None:
                self.pregnant, parsed = reply, True
        else:
            parsed = self._option(text, template, situation)
        self.absorb(text)
        return parsed

    def _option(self, text: str, template: str, situation: str) -> str | bool:
        block = SITUATION[situation].get("clarify") or {}
        if slot_name(template) == "weight_low":
            value = parse_yes_no(text)
            chosen = None if value is None else ("yes" if value else "no")
        else:
            chosen = one(vocab(text, "clarify_" + template.split(".", 1)[1]))
        for option in block.get("options", []):
            if option["id"] == chosen:
                if option.get("set", {}).get("after_clarify"):
                    self.after_clarify = True
                target: str = option["to"]
                return target
        return False


def slot_name(template: str) -> str:
    return str(QUESTION_SLOT[template])


# --- continuation maps (templates.json continuation) --------------------------------------

CONTINUATION = TEMPLATES["continuation"]
ACKS = sorted(CONTINUATION["ack_map"], key=lambda a: len(a["text"]), reverse=True)
QUESTIONS = [(re.compile(q["pattern"]), q) for q in CONTINUATION["question_map"]]
QUESTION_SLOT = {q["template"]: q["slot"] for q in CONTINUATION["question_map"]}
CHECKS = {c["text"] for c in CONTINUATION["check_map"]}
REASK = {
    "age": "reask.age",
    "duration": "reask.duration",
    "pregnant_or_lactating": "reask.pregnancy",
    "sex": "reask.sex",
    "weight_low": "reask.weight_low",
    "clarify": "reask.clarify",
    "temperature": "ask.temperature",
}


@dataclass(frozen=True)
class Asked:
    situation: str
    template: str

    @property
    def slot(self) -> str:
        return str(QUESTION_SLOT[self.template])


def decode(answer: str) -> Asked | None:
    """A Graf question = ack variant + ' ' + one question pattern (NB2)."""
    for ack in ACKS:
        if answer.startswith(ack["text"] + " "):
            rest = answer[len(ack["text"]) + 1 :]
            for pattern, question in QUESTIONS:
                if pattern.fullmatch(rest):
                    return Asked(ack["situation"], question["template"])
            return None
    return None


# --- Jev -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    situation: str
    confidence: float
    probabilities: dict[str, float]
    model: str

    @property
    def pinned(self) -> bool:
        return JEV_VERSION.fullmatch(self.model) is not None

    def second(self) -> tuple[str, float] | None:
        ranked = sorted(
            ((p, s) for s, p in self.probabilities.items() if s != self.situation), reverse=True
        )
        return (ranked[0][1], ranked[0][0]) if ranked else None


def _probability(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) and 0 <= number <= 1 else None


class Jev:
    """One Choice over all situations. Errors return None; no text is logged."""

    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self.client, self.key = client, key

    @staticmethod
    def payload(text: str) -> dict[str, Any]:
        return {
            "model": JEV_MODEL,
            "state": text,
            "questions": {
                "topic": {
                    "type": "choice",
                    "instructions": JEV_INSTRUCTIONS,
                    "criteria": {s["id"]: s["jev_criterion_full"] for s in SITUATION.values()},
                }
            },
        }

    async def classify(self, text: str) -> Choice | None:
        if not self.key:
            return None
        try:
            async with asyncio.timeout(JEV_TIMEOUT):
                async with self.client.stream(
                    "POST",
                    JEV_URL,
                    content=json.dumps(self.payload(text), ensure_ascii=False).encode(),
                    headers={
                        "Authorization": "Bearer " + self.key,
                        "Content-Type": "application/json",
                    },
                    timeout=JEV_TIMEOUT,
                ) as response:
                    if response.status_code != 200:
                        return None
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            return None
            return self.read(json.loads(body))
        except (TimeoutError, httpx.HTTPError, ValueError):
            return None

    @staticmethod
    def read(value: object) -> Choice | None:
        try:
            if not isinstance(value, dict):
                return None
            model = value["model"]
            topic = value["answers"]["topic"]
            choice = topic["choice"]
            confidence = _probability(topic["confidence"])
            raw = topic.get("probabilities") or {}
            if (
                not isinstance(model, str)
                or not isinstance(raw, dict)
                or choice not in SITUATION
                or confidence is None
            ):
                return None
            probabilities = {k: _probability(v) for k, v in raw.items() if k in SITUATION}
            clean = {k: v for k, v in probabilities.items() if v is not None}
            clean.setdefault(choice, confidence)
            return Choice(choice, confidence, clean, model)
        except (KeyError, TypeError, AttributeError):
            return None


# --- answers ------------------------------------------------------------------------------

STATUS = {
    "question": "clarification",
    "check": "clarification",
    "offer": "answer",
    "emergency": "limited",
    "doctor": "limited",
    "no_product": "limited",
    "intolerance": "limited",
    "uncovered": "limited",
    "unavailable": "limited",
}


@dataclass(frozen=True)
class Outcome:
    kind: str  # question | offer | emergency | doctor | no_product | intolerance | uncovered
    situation: str | None = None
    template: str | None = None
    offer: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()
    magniy_note: bool = False
    dropped: str | None = None


@dataclass(frozen=True)
class Reply:
    status: str
    answer: str
    ids: list[int]
    products: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    cautions: list[str]
    outcome: str
    situation: str | None


Runner = Callable[[Coroutine[Any, Any, Choice | None]], Awaitable[Choice | None]]


def _cap(value: str) -> str:
    return value[:1].upper() + value[1:]


class Writer:
    """Template rendering with deterministic variants (request id) that avoid the previous
    Graf message (templates.json rules)."""

    def __init__(self, request: Turn) -> None:
        self.language = request.language
        self.seed = request.request_id.int
        self.previous = request.history[-1].answer if request.history else ""

    def pick(self, template: str, values: dict[str, str] | None = None) -> str:
        variants: list[str] = TEXT[template][self.language]
        offset = int(hashlib.sha256(template.encode()).hexdigest()[:8], 16)
        texts = [self.fill(v, values or {}) for v in variants]
        start = (self.seed + offset) % len(texts)
        ordered = texts[start:] + texts[:start]
        fresh = [t for t in ordered if t not in self.previous]
        return (fresh or ordered)[0]

    @staticmethod
    def fill(text: str, values: dict[str, str]) -> str:
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        assert "{" not in text, text
        return text

    def relation(self, whom: str | None) -> dict[str, str]:
        rel = TEMPLATES["relation"][whom if whom in TEMPLATES["relation"] else "self"]
        return {
            "rel_for_ru": rel["for_ru"],
            "rel_for_ru_cap": _cap(rel["for_ru"]),
            "rel_dat_ru": rel["dat_ru"],
            "rel_for_uz_cap": _cap(rel["for_uz"]),
            "rel_nom_uz_cap": _cap(rel["nom_uz"]),
        }

    def ack(self, situation: str) -> str:
        template = SITUATION[situation]["ack_template"]
        return self.pick(template) if template else ""


def join(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


# --- engine ---------------------------------------------------------------------------------


def band(product: dict[str, Any], mode: str, age: float) -> dict[str, Any] | None:
    key = {"default": "dose_bands", "acute_cold": "dose_acute_cold", "diarrhea": "dose_diarrhea"}
    for item in product.get(key[mode]) or []:
        low, high = item["age_min"], item["age_max"]
        above = age >= low if item["age_min_inclusive"] else age > low
        below = high is None or (age <= high if item["age_max_inclusive"] else age < high)
        if above and below:
            return dict(item)
    return None


def admitted_age(product: dict[str, Any], age: float) -> str | None:
    """None when the age is inside the original range, else 'under' or 'over' (rule 4)."""
    low, high = product["age_min"], product["age_max"]
    if (age < low) or (age == low and not product.get("age_min_inclusive", True)):
        return "under"
    if high is not None and (age > high or (age == high and not product["age_max_inclusive"])):
        return "over"
    return None


def resolve_family(item: dict[str, Any], facts: Facts) -> str:
    family = PRODUCTS["families"].get(item["product"])
    if family is None:
        return str(item["product"])
    assert facts.age is not None
    if facts.age <= family["stick_up_to_incl"] or facts.form_stick:
        return str(family["alt"] if facts.age > family["stick_up_to_incl"] else family["under_7"])
    return str(family["from_7"])


def when_ok(when: dict[str, Any], facts: Facts) -> bool:
    age = facts.age
    for key, value in when.items():
        if key == "age_lt" and not (age is not None and age < value):
            return False
        if key == "age_ge" and not (age is not None and age >= value):
            return False
        if key == "pregnant_or_lactating" and facts.preg() is not value:
            return False
        if key == "sex" and facts.person_sex() != value:
            return False
        if key == "topic" and "topic_" + value not in facts.topics:
            return False
        if key == "after_clarify" and facts.after_clarify is not value:
            return False
    return True


def doctor(facts: Facts, situation: str | None) -> str | None:
    """Doctor lists and doctor_rules_with_slots on every Graf-owned message (5.4, I5)."""
    slots = facts.slots()
    hit = {i for text in facts.texts for i, r in scan(text, DOCTOR, slots) if r == "hit"}
    age, duration, temperature, topics = facts.age, facts.duration, facts.temperature, facts.topics
    for rule in SAFETY["doctor_rules_with_slots"]:
        if "entries" in rule:
            prefix = rule["entries"].rstrip("*")
            for entry in DOCTOR:
                if entry["id"].startswith(prefix) and entry["id"] in hit:
                    return str(entry["template"])
            continue
        if "if_vocab" in rule and rule["if_vocab"] not in topics:
            continue
        if "if_situation" in rule and situation not in rule["if_situation"]:
            continue
        if slot_rule(rule["id"], age, duration, temperature):
            return str(rule["template"])
    return None


def slot_rule(
    rule: str, age: float | None, duration: float | None, temperature: float | None
) -> bool:
    """Conditions of doctor_rules_with_slots; a rule needs every slot it reads."""
    if rule == "fever_duration":
        return (
            age is not None
            and duration is not None
            and ((age >= 18 and duration > 3) or (age < 18 and duration >= 5))
        )
    if rule == "cold_over_10_days":
        return duration is not None and duration > 10
    if rule == "cough_over_3_weeks":
        return duration is not None and duration > 21
    if rule == "infant_fever":
        return (
            age is not None
            and temperature is not None
            and ((age < 0.25 and temperature >= 38) or (0.25 <= age < 0.5 and temperature >= 39))
        )
    if rule in {"infant_diarrhea", "infant_weight"}:
        return age is not None and age < 1
    raise KeyError(rule)


# The data states each condition in words; a test pins these strings to the code above.
DOCTOR_CONDITIONS = {
    "fever_duration": "age >= 18 and duration_days > 3, or age < 18 and duration_days >= 5",
    "cold_over_10_days": "duration_days > 10",
    "cough_over_3_weeks": "duration_days > 21",
    "infant_fever": (
        "age < 0.25 and temperature >= 38, or 0.25 <= age < 0.5 and temperature >= 39"
    ),
    "infant_diarrhea": "age < 1",
    "infant_weight": "age < 1",
}


def decide(situation: str, facts: Facts, origin: str | None = None) -> Outcome:
    """Steps 5.4 to 5.8 and 6 for one situation and the facts known now."""
    template = doctor(facts, situation)
    if template:
        return Outcome("doctor", situation, template)
    if any(r == "hit" for text in facts.texts for _, r in scan(text, INTOLERANCE)):
        return Outcome("intolerance", situation, "intolerance.answer_no_name")
    magniy = False
    if situation != "diarrhea" and any(
        r == "hit" for text in facts.texts for _, r in scan(text, DIARRHEA)
    ):
        magniy = GROUP.get(origin or situation) == "magniy"
        situation = "diarrhea"
    for _ in range(len(SITUATION)):
        spec = SITUATION[situation]
        if spec.get("requires_word") and not facts.preg():
            situation = spec["without_pregnancy_word"]
            continue
        question = next_question(spec, facts)
        if question:
            return Outcome("question", situation, question)
        row = next((r["then"] for r in spec["routing"] if when_ok(r["when"], facts)), None)
        if row is None:
            return Outcome("step_aside", situation)
        kind = row.get("outcome", "offer")
        if kind == "reroute":
            situation = row["to"]
            continue
        if kind == "clarify":
            name = row["clarify"]
            return Outcome("question", situation, "clarify." + name)
        if kind == "offer":
            return offer(situation, row, facts, magniy)
        if kind == "doctor_soon":
            return Outcome("doctor", situation, row["template"])
        if kind == "emergency":
            return Outcome("emergency", situation, row["template"])
        if kind == "no_product":
            return Outcome("no_product", situation, row["template"])
        if kind == "uncovered":
            return Outcome("uncovered", situation, "no_product.general")
        return Outcome("step_aside", situation)
    return Outcome("step_aside", situation)


def next_question(spec: dict[str, Any], facts: Facts) -> str | None:
    required = spec["required_slots"]
    if "age" in required and facts.age is None:
        if facts.whom is None:
            return "ask.whom_age"
        return "ask.age_self" if facts.whom == "self" else "ask.age"
    if "duration" in required and facts.duration is None:
        return "ask.duration"
    age = facts.age
    if age is None:
        return None
    if age < 0.5 and "topic_fever" in facts.topics and facts.temperature is None:
        return "ask.temperature"
    if spec["pregnancy_question"] == "ask" and 18 <= age <= 60:
        sex = facts.person_sex(spec.get("implied_sex"))
        if sex is None:
            return "ask.sex"
        if sex == "female" and facts.pregnant is None:
            return "ask.pregnancy"
    return None


def offer(situation: str, row: dict[str, Any], facts: Facts, magniy: bool) -> Outcome:
    assert facts.age is not None
    items: list[dict[str, Any]] = []
    dropped: tuple[str, str] | None = None
    for item in row["offer"]:
        pid = resolve_family(item, facts)
        product = PRODUCT[pid]
        mode = admitted_age(product, facts.age)
        if mode or band(product, item["dose"], facts.age) is None:
            dropped = dropped or (pid, mode or "under")
            continue
        items.append({**item, "product": pid, "indication": indication(situation, item, facts)})
    if not items:
        assert dropped is not None
        if dropped[1] == "under":
            return Outcome("no_product", situation, "under_age", dropped=dropped[0])
        return Outcome("no_product", situation, "no_product.general")
    notes = list(row.get("notes", []))
    if facts.preg() and "note_pregnancy" not in notes:
        notes.append("note_pregnancy")
    if facts.disease or (situation in TOPIC_DISEASE and TOPIC_DISEASE[situation] & facts.topics):
        notes.append("note_disease")
    return Outcome("offer", situation, None, tuple(items[:2]), tuple(dict.fromkeys(notes)), magniy)


# Reason by topic for the two situations whose notes in situations.json name it.
TOPIC_DISEASE = {"eyes": {"topic_eye_disease"}, "skin_hair": {"topic_fungus"}}


def indication(situation: str, item: dict[str, Any], facts: Facts) -> str:
    if situation == "skin_hair" and "topic_hair" in facts.topics:
        return "hair"
    if situation == "eyes":
        for topic, value in (
            ("topic_computer", "computer"),
            ("topic_glasses_lenses", "glasses_lenses"),
        ):
            if topic in facts.topics:
                return value
        if facts.age is not None and facts.age >= 40:
            return "age_40"
        if "topic_eye_disease" in facts.topics:
            return "eye_disease"
    return str(item["indication"])


# --- turn ---------------------------------------------------------------------------------


@dataclass
class Flow:
    """A Graf conversation rebuilt from history: facts, the question just asked and the
    slots asked since the flow started."""

    facts: Facts
    asked: Asked
    templates: list[str]


def logical(history: Sequence[Any]) -> list[tuple[str, str]]:
    """(customer message, answer) pairs; an emergency.check answered 'no' folds into the
    message it checked (engine 2)."""
    out: list[tuple[str, str]] = []
    skip = False
    for k, pair in enumerate(history):
        if skip:
            skip = False
            continue
        if pair.answer in CHECKS and k + 1 < len(history):
            reply = history[k + 1]
            if parse_yes_no(reply.question) is False:
                out.append((pair.question, reply.answer))
                skip = True
                continue
        out.append((pair.question, pair.answer))
    return out


def rebuild(history: Sequence[Any]) -> Flow | None:
    pairs = logical(history)
    asked = [decode(answer) for _, answer in pairs]
    start = len(pairs)
    while start > 0 and asked[start - 1] is not None:
        start -= 1
    if start == len(pairs):
        return None
    first = asked[start]
    assert first is not None
    facts = Facts()
    facts.absorb(pairs[start][0])
    templates = [first.template]
    restarted = False
    for j in range(start + 1, len(pairs)):
        before, now = asked[j - 1], asked[j]
        assert before is not None
        assert now is not None
        parsed = facts.answer(pairs[j][0], before.template, before.situation)
        if parsed is False and now.situation != before.situation:
            facts, templates, restarted = Facts(), [], True
            facts.absorb(pairs[j][0])
        templates.append(now.template)
    # With a full window the oldest pair may be a reply whose start pair has left the 4-pair
    # window (plan r4 section 3.3). It is the start only if Graf would have asked exactly that
    # question after it as a first message.
    if start == 0 and not restarted and len(history) >= 4:
        opening = Facts()
        opening.absorb(pairs[0][0])
        expected = decide(first.situation, opening)
        if (expected.kind, expected.template) != ("question", first.template):
            return None
    last = asked[-1]
    assert last is not None
    return Flow(facts, last, templates)


def pregnancy_context(request: Turn) -> bool:
    """The ambulance slot 'pregnant' (match_spec requires_slot) for every customer message in
    the window: a non-negated pregnancy word, or a reply that answers Graf's own pregnancy
    question as pregnant, parsed as Facts.answer parses that slot (review r1 SAF-01)."""
    messages = [p.question for p in request.history] + [request.question]
    before: list[str | None] = [None] + [p.answer for p in request.history]
    for text, answer in zip(messages, before, strict=True):
        if any(r == "hit" for _, r in scan(text, PREGNANCY_WORDS)):
            return True
        asked = decode(answer) if answer is not None else None
        if (
            asked is not None
            and asked.slot == "pregnant_or_lactating"
            and parse_pregnancy_reply(text)
        ):
            return True
    return False


class Graf:
    """Steps 2, 3 and 5 to 7 of plan r4 section 3; service.py runs steps 1 and 4."""

    def __init__(self, jev: Jev) -> None:
        self.jev = jev

    def safety(self, request: Turn) -> tuple[Reply | None, Turn]:
        """Step 2: ambulance list on every message, before register and snapshot."""
        level, hits = tier(request.question, {"pregnant": pregnancy_context(request)})
        writer = Writer(request)
        if level == "strong":
            templates = {
                ENTRY[i]["template"] for i, r in hits if r == "hit" and TIER[i] == "strong"
            }
            template = next(
                t
                for t in ("emergency.self_harm", "emergency.pregnancy", "emergency.general")
                if t in templates
            )
            return self.reply(writer, Outcome("emergency", None, template), None), request
        if level == "weak":
            return self.reply(writer, Outcome("check", None, "emergency.check"), None), request
        if request.history and request.history[-1].answer in CHECKS:
            if parse_yes_no(request.question) is not False:
                outcome = Outcome("emergency", None, "emergency.general")
                return self.reply(writer, outcome, None), request
            previous = request.history[-1]
            # 'No': the checked message continues from step 3, its ambulance hits ignored.
            return None, request.model_copy(
                update={"question": previous.question, "history": request.history[:-1]}
            )
        return None, request

    @staticmethod
    def owns(request: Turn) -> bool:
        """Step 3 condition: the last answer is a Graf question (ack + question pattern)."""
        return bool(request.history) and decode(request.history[-1].answer) is not None

    async def continuation(self, request: Turn, evidence: Evidence, run: Runner) -> Reply | None:
        """Step 3 for an owned turn. None means step aside to the existing path."""
        if not self.owns(request):
            return None
        flow = rebuild(request.history)
        writer = Writer(request)
        if flow is None:
            self.log(None, "step_aside")
            return None
        facts, asked = flow.facts, flow.asked
        parsed = facts.answer(request.question, asked.template, asked.situation)
        situation = parsed if isinstance(parsed, str) else asked.situation
        template = doctor(facts, situation)
        if template:
            return self.reply(writer, Outcome("doctor", situation, template), evidence, facts)
        if parsed is False:
            repeats = 0
            for t in reversed(flow.templates):
                if QUESTION_SLOT[t] != asked.slot:
                    break
                repeats += 1
            choice = await run(self.jev.classify(request.question))
            if (
                choice is not None
                and choice.pinned
                and choice.confidence >= THRESHOLD
                and choice.situation not in {asked.situation, "unclear"}
            ):
                return self.start(request, evidence, choice)
            if choice is not None and not choice.pinned:
                self.mismatch(choice)
            if repeats >= 2:
                outcome = Outcome("no_product", asked.situation, "no_product.general")
                return self.reply(writer, outcome, evidence, facts)
            outcome = Outcome("question", asked.situation, REASK[asked.slot])
            return self.reply(writer, outcome, evidence, facts)
        return self.reply(writer, decide(situation, facts), evidence, facts)

    async def first(self, request: Turn, evidence: Evidence, run: Runner) -> Reply | None:
        """Step 5: a first Graf turn."""
        choice = await run(self.jev.classify(request.question))
        if choice is None:
            self.log(None, "step_aside")
            return None
        if not choice.pinned:
            self.mismatch(choice)
            return None
        return self.start(request, evidence, choice)

    def start(self, request: Turn, evidence: Evidence, choice: Choice) -> Reply | None:
        writer = Writer(request)
        top = choice.situation
        if choice.confidence < THRESHOLD or top == "unclear":
            self.log(top, "step_aside")
            return None
        if top == "other_unhandled":
            return self.reply(writer, Outcome("uncovered", top, "no_product.general"), evidence)
        second = choice.second()
        if second and second[1] >= SECOND:
            ours, theirs = GROUP.get(top), GROUP.get(second[0])
            if ours and theirs and ours != theirs:
                self.log(top, "step_aside")
                return None
        facts = Facts()
        facts.absorb(request.question)
        return self.reply(writer, decide(top, facts, top), evidence, facts)

    def reply(
        self,
        writer: Writer,
        outcome: Outcome,
        evidence: Evidence | None,
        facts: Facts | None = None,
    ) -> Reply | None:
        self.log(outcome.situation, outcome.kind)
        if outcome.kind == "step_aside":
            return None
        if facts is None:
            facts = Facts()
        if outcome.kind == "question":
            assert outcome.situation
            assert outcome.template
            values = writer.relation(facts.whom)
            text = join(writer.ack(outcome.situation), writer.pick(outcome.template, values))
            return Reply("clarification", text, [], [], [], [], "question", outcome.situation)
        if outcome.kind == "offer":
            assert evidence is not None
            return self.offer(writer, outcome, evidence, facts)
        assert outcome.template is not None
        values = {}
        if outcome.dropped:
            product = PRODUCT[outcome.dropped]
            age_min = product["age_min"]
            values = {
                "product": product["shop_name"],
                "age_min": str(int(age_min)) if float(age_min).is_integer() else str(age_min),
            }
        text = writer.pick(outcome.template, values)
        return Reply(STATUS[outcome.kind], text, [], [], [], [], outcome.kind, outcome.situation)

    def offer(self, writer: Writer, outcome: Outcome, evidence: Evidence, facts: Facts) -> Reply:
        assert outcome.situation is not None
        assert facts.age is not None
        chosen: list[tuple[dict[str, Any], int]] = []
        for item in outcome.offer:
            spec = PRODUCT[item["product"]]
            matches = [
                pid
                for pid, card in evidence.products.items()
                if card.get("name") == spec["shop_name"] and evidence.form(pid) == spec["form"]
            ]
            if len(matches) == 1 and evidence.products[matches[0]].get("is_in_stock") is True:
                chosen.append((item, matches[0]))
        if not chosen:
            self.log(outcome.situation, "unavailable")
            text = writer.pick("no_product.unavailable")
            return Reply("limited", text, [], [], [], [], "unavailable", outcome.situation)
        language = writer.language
        blocks: list[tuple[str, str, str]] = []
        for item, _ in chosen:
            spec = PRODUCT[item["product"]]
            reason = next(i for i in spec["indications"] if i["id"] == item["indication"])
            dose = band(spec, item["dose"], facts.age)
            assert dose is not None
            if item["dose"] == "default":
                line = writer.pick(
                    "dose.line",
                    {"dose": dose["say_" + language], "course": spec["course"]["say_" + language]},
                )
            else:
                line = writer.pick("dose.line_no_course", {"dose": dose["say_" + language]})
            blocks.append((spec["shop_name"], reason["reason_" + language], line))
        relation = writer.relation(facts.whom)
        notes = list(outcome.notes)
        if len(chosen) == 2 and "note_pregnancy" in notes:
            notes[notes.index("note_pregnancy")] = "note_pregnancy_two"
        opening = writer.pick("opening.not_treat") if "topic_treat" in facts.topics else ""
        magniy = writer.pick("note_magniy_diarrhea") if outcome.magniy_note else ""
        note_text = join(*(writer.pick(n) for n in notes))
        ack = writer.ack(outcome.situation)
        if len(blocks) == 1:
            name, reason_text, line = blocks[0]
            text = join(
                opening,
                magniy,
                ack,
                writer.pick("offer.one", {**relation, "product": name}),
                reason_text,
                line,
                note_text,
            )
        else:
            (n1, r1, d1), (n2, r2, d2) = blocks
            pair = {**relation, "product1": n1, "product2": n2}
            text = join(
                opening,
                magniy,
                ack,
                writer.pick("offer.two", pair),
                writer.pick("offer.first", {"product1": n1, "reason1": r1, "dose1": d1}),
                writer.pick("offer.second", {"product2": n2, "reason2": r2, "dose2": d2}),
                note_text,
            )
        ids = [pid for _, pid in chosen]
        wanted = {3, 2}
        if any(n.startswith("note_pregnancy") for n in notes):
            wanted.add(1)
        sources: list[dict[str, Any]] = []
        cautions: list[str] = []
        for pid in ids:
            for section in evidence.sections:
                if section["product_id"] != pid:
                    continue
                if section["caution"]:
                    cautions.append(section["source_id"])
                if section["caution"] or section["ordinal"] in wanted:
                    sources.append(section)
        if len(sources) > 12:
            keep = set(cautions)
            extra = [s for s in sources if s["source_id"] not in keep]
            sources = [s for s in sources if s["source_id"] in keep] + extra
            sources = sources[:12]
            cautions = [c for c in cautions if c in {s["source_id"] for s in sources}]
        products = [evidence.products[pid] for pid in ids]
        return Reply("answer", text, ids, products, sources, cautions, "offer", outcome.situation)

    @staticmethod
    def mismatch(choice: Choice) -> None:
        served = re.sub(r"[^0-9A-Za-z._/~:-]", "", choice.model)[:80]
        logger.warning("graf jev version mismatch served=%s", served)

    @staticmethod
    def log(situation: str | None, outcome: str) -> None:
        logger.info("graf turn situation=%s outcome=%s", situation or "-", outcome)


__all__ = ["Choice", "Graf", "Jev", "Reply", "replace"]
