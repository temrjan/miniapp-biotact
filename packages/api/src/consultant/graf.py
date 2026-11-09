"""Graf: deterministic product offer for complaints that name no product (plan r4).

Every word list, parser, route and customer-visible string comes from graf_data/*.json.
Graf stores nothing: the flow state travels in the signed context token (version 2) or, for
a version 1 token, is rebuilt from the history on each turn. Log lines carry only a
situation id, an outcome class or a served model id. Graf's texts are never delivered to the
customer: they are Luna's material and the reference of the text checks (spec r2, LO1).
"""

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from src.consultant.context import FACT_TYPES, FLAGS, Check, Context, FlowState, digest
from src.consultant.contracts import Fault, Turn
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
# Complaint situations: every situation except the four meta situations.
META = frozenset({"unclear", "other_unhandled", "see_doctor_soon", "emergency"})
COMPLAINTS = frozenset(SITUATION) - META
# Situation-level questions: no hypothesis, one hypothesis, two options.
QUESTION_KINDS = ("clarify", "confirm", "choose")
# Every decision that asks a situation-level question: also the post-offer question.
SITUATION_LEVEL = (*QUESTION_KINDS, "post_offer")

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


def active(
    text: str, found: list[tuple[str, str]], inactive: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """The hits of one message that no accepted objection made inactive (spec r2 BO-04,
    BO-08): an inactive hit is a (source id, message digest) pair."""
    if not inactive:
        return found
    message = digest(text)
    return [(i, r) for i, r in found if (i, message) not in inactive]


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
# A reply that is only a number ("2", "два", "3."): read in the unit the question asked.
BARE_NUMBER = re.compile(r"\s*" + NUM + r"\s*[.!]?\s*")
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
# Inside a longer reply to Graf's age question ('для себя, мужчина 47 лет'): a number with
# an age unit (reply units plus the Russian year words, which add 'годиков').
REPLY_UNITS = "(" + AGE["reply_units"] + "|" + DURATION["patterns"]["ru"]["years"] + ")"
REPLY_AGE_IN = re.compile(LEFT + NUM + r"\s*" + REPLY_UNITS + RIGHT)
REPLY_GUARD = MARKERS | ADVERBS
# Words set aside, together with the whom vocabulary, before a reply to Graf's age question
# is read as one bare number ('для себя, 47').
REPLY_FILLER = frozenset({"для", "у", "мне", "uchun", "учун"})


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
            return _reply_years(reply_match), [(0, len(t))]
        # A number with an age unit inside the reply, unless a duration word stands next to
        # it ('болит 3 года') or an anchored pattern above already dropped it as a duration.
        dropped = list(spans)
        for m in REPLY_AGE_IN.finditer(t):
            if any(m.start() < b and a < m.end() for a, b in dropped):
                continue
            if not 0 < number(m[1]) <= 110 or _guarded(t, m.start(), m.end(), REPLY_GUARD):
                continue
            found.add(_reply_years(m))
            spans.append((m.start(), m.end()))
        if len(found) == 1:
            return found.pop(), spans
        if found:
            return "ask", spans
        bare = _bare_reply_age(t)
        if bare is not None:
            years, span = bare
            return years, [*spans, span]
    return None, spans


def _bare_reply_age(t: str) -> tuple[float, tuple[int, int]] | None:
    """A reply that is one bare number once the whom phrases, the REPLY_FILLER words and
    clause punctuation are set aside ('для себя, 47'): (years, span of the reply), else
    None. Nothing else is set aside, so a temperature, weight or dose word, a sign or a
    second number ('38,5') keeps the reply unread."""
    words = Words.of(t)
    # CLAUSE characters are never inside a TOKEN, so these spans line up with words.toks.
    located = [(m.start(), m.end()) for m in TOKEN.finditer(t) if m[0].strip("'")]
    aside = {i for i, tok in enumerate(words.toks) if tok in REPLY_FILLER}
    for item in VOCAB:
        if item["slot"] == "whom":
            for term in item["terms"]:
                for span in positions(words, term, item["match"]):
                    aside.update(span)
    rest = list(t)
    for i in aside:
        a, b = located[i]
        rest[a:b] = " " * (b - a)
    core = CLAUSE.sub(" ", "".join(rest)).strip()
    if not re.fullmatch(NUM, core) or not 0 < number(core) <= 110:
        return None
    return number(core), (0, len(t))


def _reply_years(m: re.Match[str]) -> float:
    value, unit = number(m[1]), m[2] or ""
    if re.match(r"(мес|месяц|oy|ой)", unit):
        return round(value / 12, 2)
    if unit.startswith("недел"):
        return round(value * 7 / 365, 2)
    return value


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
PREGNANCY_IDS = frozenset(e["id"] for e in PREGNANCY_WORDS)
INTOLERANCE_IDS = frozenset(e["id"] for e in INTOLERANCE)
DIARRHEA_IDS = frozenset(e["id"] for e in DIARRHEA)
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
    intolerance: bool = False
    topics: set[str] = field(default_factory=set)
    texts: list[str] = field(default_factory=list)
    # Origin of each FACT_TYPES value: "parser" (read from words), "confirmed" (a parsed
    # reply to Graf's question for that slot) or "luna" (Luna's sure reading).
    origin: dict[str, str] = field(default_factory=dict)
    # Hits made inactive by accepted objections: (entry id or topic slot, message digest).
    inactive: tuple[tuple[str, str], ...] = ()

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

    def absorb(self, text: str, reply_spans: Sequence[tuple[int, int]] = ()) -> None:
        """General extraction: fills what is still unknown, accumulates words. reply_spans
        are the spans parse_age(reply=True) read or dropped as an age in a reply to Graf's
        age question; they are not read again as a duration ('8 месяцев' there is an age,
        not 240 days of illness)."""
        self.texts.append(text)
        age, spans = parse_age(text)
        if self.age is None and isinstance(age, float):
            self.assign("age", age, "parser")
        duration = parse_duration(text, [*spans, *reply_spans])
        if duration is not None:
            self.assign("duration", max(duration, self.duration or 0.0), "parser")
        temperature = parse_temperature(text)
        if temperature is not None:
            self.assign("temperature", max(temperature, self.temperature or 0.0), "parser")
        if self.whom is None:
            self.assign("whom", parse_whom(text), "parser")
        if self.sex is None:
            self.assign("sex", one(vocab(text, "sex")), "parser")
        self.child_word = self.child_word or bool(vocab(text, "child"))
        self.form_stick = self.form_stick or bool(vocab(text, "form_pref"))
        if self.hit(text, PREGNANCY_WORDS):
            self.assign("pregnant", True, "parser")
        self.disease = self.disease or any(r == "hit" for _, r in scan(text, DISEASE))
        self.intolerance = self.intolerance or self.hit(text, INTOLERANCE)
        self.topics |= {t for t in TOPICS if self.topic(text, t)}

    def hit(self, text: str, entries: Iterable[dict[str, Any]]) -> bool:
        return any(r == "hit" for _, r in active(text, scan(text, entries), self.inactive))

    def topic(self, text: str, slot: str) -> bool:
        return bool(vocab(text, slot)) and (slot, digest(text)) not in self.inactive

    def refresh(self, sources: Iterable[str]) -> None:
        """Word facts read again from the texts after objections to their hits (B-1): a
        fact stays set while any of its hits stays active."""
        named = set(sources)
        if (
            named & PREGNANCY_IDS
            and self.origin.get("pregnant") == "parser"
            and not any(self.hit(t, PREGNANCY_WORDS) for t in self.texts)
        ):
            self.pregnant = None
            del self.origin["pregnant"]
        if named & INTOLERANCE_IDS:
            self.intolerance = any(self.hit(t, INTOLERANCE) for t in self.texts)
        for slot in named & set(TOPICS):
            if not any(self.topic(t, slot) for t in self.texts):
                self.topics.discard(slot)

    def assign(self, name: str, value: float | str | bool | None, origin: str) -> None:
        """Set a FACT_TYPES fact; its origin changes only when its value changes."""
        if value is not None and value != getattr(self, name):
            setattr(self, name, value)
            self.origin[name] = origin

    def answer(self, text: str, template: str, situation: str) -> str | bool:
        """Parse a reply to a Graf question. Returns False when unparsed; for a clarifying
        question returns the chosen option's target situation."""
        slot = QUESTION_SLOT[template]
        parsed: str | bool = False
        reply_spans: list[tuple[int, int]] = []
        if slot == "age":
            age, reply_spans = parse_age(text, reply=True)
            if isinstance(age, float):
                self.age, self.origin["age"], parsed = age, "confirmed", True
        elif slot == "duration":
            age, spans = parse_age(text)
            duration = parse_duration(text, spans)
            if duration is None and (bare := BARE_NUMBER.fullmatch(norm(text))):
                # The question asks for days: a bare number is that many days.
                duration = number(bare[1]) or None
            if duration is not None:
                self.duration, self.origin["duration"], parsed = duration, "confirmed", True
        elif slot == "temperature":
            temperature = parse_temperature(text)
            if temperature is not None:
                self.temperature, self.origin["temperature"] = temperature, "confirmed"
                parsed = True
        elif slot == "sex":
            sex = one(vocab(text, "sex"))
            if sex:
                self.sex, self.origin["sex"], parsed = sex, "confirmed", True
        elif slot == "pregnant_or_lactating":
            reply = parse_pregnancy_reply(text)
            if reply is not None:
                self.pregnant, self.origin["pregnant"], parsed = reply, "confirmed", True
        else:
            parsed = self._option(text, template, situation)
        self.absorb(text, reply_spans)
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


@dataclass(frozen=True)
class Attempt:
    """One Jev request: the classified text, the choice (None on an error), the error class
    and the latency in seconds."""

    text: str
    choice: Choice | None
    error: str | None
    latency: float

    @property
    def status(self) -> str:
        """The brief's Jev status: ok, error or mismatch (served model not pinned)."""
        if self.choice is None:
            return "error"
        return "ok" if self.choice.pinned else "mismatch"


class Jev:
    """One Choice over all situations. Errors return None; no text is logged."""

    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self.client, self.key = client, key

    async def attempt(self, text: str) -> Attempt:
        started = time.monotonic()
        choice, error = await self._classify(text)
        return Attempt(text, choice, error, time.monotonic() - started)

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
        return (await self._classify(text))[0]

    async def _classify(self, text: str) -> tuple[Choice | None, str | None]:
        """(choice, None) or (None, error class)."""
        if not self.key:
            return None, "no_key"
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
                        return None, "status"
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            return None, "size"
            choice = self.read(json.loads(body))
            return choice, None if choice is not None else "invalid"
        except (TimeoutError, httpx.TimeoutException):
            return None, "timeout"
        except httpx.HTTPError:
            return None, "transport"
        except ValueError:
            return None, "invalid"

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
    "clarify": "clarification",
    "confirm": "clarification",
    "choose": "clarification",
    "post_offer": "clarification",
    "check": "clarification",
    "offer": "answer",
    # spec r6 BUY-03: the how-to-buy answer (template info.how_to_buy).
    "how_to_buy": "answer",
    # spec r7 CN-01: the shop conditions answer (templates conditions.*).
    "conditions": "answer",
    "emergency": "limited",
    "doctor": "limited",
    "no_product": "limited",
    "intolerance": "limited",
    "uncovered": "limited",
    "unavailable": "limited",
}


@dataclass(frozen=True)
class Outcome:
    # question | offer | emergency | check | doctor | no_product | intolerance | uncovered,
    # or a situation-level question: clarify | confirm | choose | post_offer
    kind: str
    situation: str | None = None
    template: str | None = None
    offer: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()
    magniy_note: bool = False
    dropped: str | None = None
    # A situation-level question (kind in QUESTION_KINDS): its hypothesis or two options.
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Part:
    """One part of an approved text as the Writer rendered it: a template key with its
    placeholder values, or a products.json string (template None). Spec r2 §9 builds the
    template family of a decision from its parts."""

    template: str | None
    values: tuple[tuple[str, str], ...]
    text: str

    def variants(self, language: str) -> tuple[str, ...]:
        """Every variant of the template in the language, filled with the same values."""
        if self.template is None:
            return (self.text,)
        return tuple(
            Writer.fill(variant, dict(self.values)) for variant in TEXT[self.template][language]
        )


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
    parts: tuple[Part, ...] = ()


Runner = Callable[[Coroutine[Any, Any, Attempt]], Awaitable[Attempt]]


def _cap(value: str) -> str:
    return value[:1].upper() + value[1:]


class Writer:
    """Template rendering with deterministic variants (request id) that avoid the previous
    Graf message (templates.json rules)."""

    def __init__(self, request: Turn) -> None:
        self.language = request.language
        self.seed = request.request_id.int
        self.previous = request.history[-1].answer if request.history else ""
        self.parts: list[Part] = []

    def pick(self, template: str, values: dict[str, str] | None = None) -> str:
        variants: list[str] = TEXT[template][self.language]
        offset = int(hashlib.sha256(template.encode()).hexdigest()[:8], 16)
        texts = [self.fill(v, values or {}) for v in variants]
        start = (self.seed + offset) % len(texts)
        ordered = texts[start:] + texts[:start]
        fresh = [t for t in ordered if t not in self.previous]
        chosen = (fresh or ordered)[0]
        self.parts.append(Part(template, tuple(sorted((values or {}).items())), chosen))
        return chosen

    def data(self, text: str) -> str:
        """A products.json string used as it is."""
        self.parts.append(Part(None, (), text))
        return text

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


def doctor(facts: Facts, situation: str | None, words_only: bool = False) -> str | None:
    """Doctor lists and doctor_rules_with_slots on every Graf-owned message (5.4, I5).
    words_only runs only the word-list rows (the rows with 'entries')."""
    slots = facts.slots()
    hit = {
        i
        for text in facts.texts
        for i, r in active(text, scan(text, DOCTOR, slots), facts.inactive)
        if r == "hit"
    }
    age, duration, temperature, topics = facts.age, facts.duration, facts.temperature, facts.topics
    for rule in SAFETY["doctor_rules_with_slots"]:
        if words_only and "entries" not in rule:
            continue
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
    if facts.intolerance or any(facts.hit(text, INTOLERANCE) for text in facts.texts):
        return Outcome("intolerance", situation, "intolerance.answer_no_name")
    magniy = False
    if situation != "diarrhea" and any(facts.hit(text, DIARRHEA) for text in facts.texts):
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


def offer_for(situation: str, facts: Facts) -> Outcome | None:
    """The data's offer for one complaint and the facts known now, without Graf's questions
    or doctor rules: Luna decided to offer (Captain 2026-11-09, Luna decides and Graf helps).
    The instruction prohibitions stay on the server (Captain 2026-11-09, "Запреты по
    инструкции оставить на сервере"): an intolerance gives the answer without a product
    name; diarrhea words route to the diarrhea offer (no magnesium); for a woman of 18 to 60
    whose pregnancy changes the product, the sex or pregnancy is asked first (the same
    product gets the pregnancy note). None when the age is unknown or the data has no
    product row for these facts."""
    if facts.age is None or situation not in COMPLAINTS:
        return None
    if facts.intolerance or any(facts.hit(text, INTOLERANCE) for text in facts.texts):
        return Outcome("intolerance", situation, "intolerance.answer_no_name")
    magniy = False
    if situation != "diarrhea" and any(facts.hit(text, DIARRHEA) for text in facts.texts):
        magniy = GROUP.get(situation) == "magniy"
        situation = "diarrhea"
    outcome = routed_offer(situation, facts, magniy)
    if outcome is None:
        return None
    spec = SITUATION[situation]
    sex = facts.person_sex(spec.get("implied_sex"))
    if (
        spec.get("pregnancy_question") == "ask"
        and 18 <= facts.age <= 60
        and facts.pregnant is None
        and sex != "male"
    ):
        pregnant = routed_offer(situation, replace(facts, sex="female", pregnant=True), magniy)
        same = pregnant is not None and [i["product"] for i in pregnant.offer] == [
            i["product"] for i in outcome.offer
        ]
        if not same:
            return Outcome("question", situation, "ask.sex" if sex is None else "ask.pregnancy")
        if "note_pregnancy" not in outcome.notes:
            outcome = replace(outcome, notes=(*outcome.notes, "note_pregnancy"))
    return outcome


def routed_offer(situation: str, facts: Facts, magniy: bool) -> Outcome | None:
    """The data's routing row for the facts as an offer, or None (no row, no offer row,
    or every product dropped by age)."""
    assert facts.age is not None
    for _ in range(len(SITUATION)):
        spec = SITUATION[situation]
        if spec.get("requires_word") and not facts.preg():
            situation = spec["without_pregnancy_word"]
            continue
        row = next((r["then"] for r in spec["routing"] if when_ok(r["when"], facts)), None)
        if row is None:
            return None
        kind = row.get("outcome", "offer")
        if kind == "reroute":
            situation = row["to"]
            continue
        if kind != "offer":
            return None
        outcome = offer(situation, row, facts, magniy)
        return outcome if outcome.kind == "offer" else None
    return None


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


def pregnancy_slot(request: Turn, context: Context) -> bool:
    """The ambulance slot 'pregnant': the token's pregnancy flag, a non-negated pregnancy word
    in any received customer message, or, for a version 1 token, a reply read from history
    as a yes to Graf's pregnancy question."""
    if context.pg:
        return True
    if context.version == 1:
        return pregnancy_context(request)
    messages = [p.question for p in request.history] + [request.question]
    return any(r == "hit" for text in messages for _, r in scan(text, PREGNANCY_WORDS))


def flow_texts(request: Turn, flow: FlowState) -> tuple[int, list[str]]:
    """(index of the flow's first message, the received customer messages from it through
    the current message). The first message is the one at len(history) - counter when its
    digest matches, else the latest earlier message with that digest, else the oldest."""
    received = [p.question for p in request.history] + [request.question]
    start = len(request.history) - flow.counter
    if not (0 <= start < len(request.history) and digest(received[start]) == flow.first):
        found = [i for i in range(len(request.history)) if digest(received[i]) == flow.first]
        start = found[-1] if found else 0
    return start, received[start:]


WHOM = frozenset(item["value"] for item in VOCAB if item["slot"] == "whom")
SEXES = frozenset(item["value"] for item in VOCAB if item["slot"] == "sex")
OPTIONS = {"slot": 0, "clarify": 0, "confirm": 1, "choose": 2}


def restore(flow: FlowState, texts: list[str]) -> Facts:
    """Facts of a token flow state; ids unknown to the data make the token invalid."""
    values = dict(flow.facts)
    if (
        (flow.situation is not None and flow.situation not in SITUATION)
        or (flow.pending == "slot" and flow.template not in QUESTION_SLOT)
        or (flow.pending == "slot") is (flow.situation is None)
        or len(flow.options) != OPTIONS[flow.pending]
        or not set(flow.options) <= COMPLAINTS
        or len(set(flow.options)) != len(flow.options)
        or any(a not in QUESTION_SLOT and a not in QUESTION_KINDS for a in flow.asked)
        or not set(flow.topics) <= set(TOPICS)
        or values.get("whom", ("self", ""))[0] not in WHOM
        or values.get("sex", ("male", ""))[0] not in SEXES
    ):
        raise Fault("context_invalid", 400)
    facts = Facts(texts=list(texts), topics=set(flow.topics), inactive=flow.overruled)
    for name, (value, origin) in values.items():
        setattr(facts, name, value)
        facts.origin[name] = origin
    for flag in flow.flags:
        setattr(facts, flag, True)
    return facts


def slot_of(item: str) -> str:
    """The repeat class of an asked item: a template's slot, or one class for every
    situation-level question."""
    return "situation" if item in QUESTION_KINDS else slot_name(item)


def repeats(asked: Sequence[str], item: str) -> int:
    """How many of the last asked items ask the same as `item` (graf.py today: the trailing
    run of templates with the asked slot)."""
    count = 0
    for previous in reversed(asked):
        if slot_of(previous) != slot_of(item):
            break
        count += 1
    return count


@dataclass(frozen=True)
class Origin:
    """Where a brief's flow starts: the first message's index among the received customer
    messages, the asked items before this turn, and how many received messages follow the
    processed one (1 after a 'no' to a danger check)."""

    start: int
    asked: tuple[str, ...] = ()
    overruled: tuple[tuple[str, str], ...] = ()
    skipped: int = 0


@dataclass(frozen=True)
class Brief:
    """Graf's result for a turn: the decision, the facts it rests on and, when the decision
    is a question, the flow state the response token carries; `origin` lets the hybrid
    turn build the state of another decision on the same facts."""

    outcome: Outcome
    facts: Facts
    flow: FlowState | None
    origin: Origin | None = None


@dataclass(frozen=True)
class Stage:
    """The ambulance stage of one message (spec r2 HR-01 step 3, SF-01): its level
    ('strong', 'weak', 'reply' to a pending check, or 'none'), the primary outcome
    (emergency or check; None when a reply continues the checked message), the ambulance
    hits of the current message, the checked message to continue (a reply whose previous
    message is the checked one), the digests a 'no' clears, and the check a weak tier
    records."""

    level: str
    outcome: Outcome | None = None
    hits: tuple[tuple[str, str], ...] = ()
    checked: Turn | None = None
    cleared: tuple[str, ...] = ()
    check: Check | None = None

    @property
    def safety(self) -> bool:
        return self.level != "none"


@dataclass
class Lens:
    """Luna's validated reading as Graf's re-decision uses it (spec r2 §7). The default lens
    changes nothing, so a brief computed with it is Graf's own brief. Graf writes back the
    merge results the turn record needs (spec.opyt r2 TR-02, TR-04).

    `values` maps a fact to Luna's value (None removes a parser value); `situation` is
    Luna's sure situation in C; `inactive` holds the hits made inactive on this turn;
    `memo` keeps Jev's attempts by text, so a re-decision never asks Jev twice."""

    values: dict[str, float | str | None] = field(default_factory=dict)
    pregnant: bool = False
    intolerance: bool = False
    situation: str | None = None
    topic_change: bool = False
    inactive: tuple[tuple[str, str], ...] = ()
    read: bool = False
    memo: dict[str, Attempt] = field(default_factory=dict)
    agreement: str | None = None
    resolution: tuple[str, str] | None = None
    repeat: bool = False

    def apply(self, facts: Facts) -> None:
        """Merge Luna's values into the facts (MR-03, MR-07). A value the customer gave in
        reply to Graf's question for that slot (origin 'confirmed') stays; only
        `pregnant = true` may change a confirmed false (spec r3 V-01)."""
        for name, value in self.values.items():
            if facts.origin.get(name) == "confirmed":
                continue
            if value is None:
                if facts.origin.get(name) == "parser":
                    setattr(facts, name, None)
                    del facts.origin[name]
            else:
                setattr(facts, name, value)
                facts.origin[name] = "luna"
        if self.pregnant and facts.pregnant is not True:
            facts.pregnant, facts.origin["pregnant"] = True, "luna"
        if self.intolerance:
            facts.intolerance = True

    def fresh(self) -> Facts:
        """Facts of a new flow: no earlier objections except this turn's."""
        return Facts(inactive=self.inactive)

    def extend(self, facts: Facts) -> None:
        """Add this turn's accepted objections to a flow's inactive hits."""
        new = tuple(p for p in self.inactive if p not in facts.inactive)
        if new:
            facts.inactive = (*facts.inactive, *new)
            facts.refresh(source for source, _ in new)

    @staticmethod
    def filled(facts: Facts, slot: str) -> bool:
        """Luna's sure value fills the slot of a question (HR-05)."""
        name = "pregnant" if slot == "pregnant_or_lactating" else slot
        return (
            name in FACT_TYPES
            and getattr(facts, name) is not None
            and facts.origin.get(name) == "luna"
        )


def signature(outcome: Outcome, situation: bool = True) -> tuple[Any, ...]:
    """What makes two decisions the same: kind, (situation,) template, products, notes,
    options."""
    products = tuple(item["product"] for item in outcome.offer)
    head = (outcome.situation,) if situation else ()
    return (
        outcome.kind,
        *head,
        outcome.template,
        products,
        outcome.notes,
        outcome.dropped,
        outcome.options,
    )


# --- hits (spec r2 §2, HR-09, BO-02) ------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    """One (source, message) occurrence of a word list: a doctor entry (K2), an ambulance
    entry (K3) or a word fact (W). Its id is '<entry id or topic slot>@<message index>'."""

    source: str
    kind: str
    sign: str
    result: str
    index: int

    @property
    def id(self) -> str:
        return f"{self.source}@{self.index}"


W_KINDS = (
    (PREGNANCY_WORDS, "W-pregnancy", "pregnant"),
    (INTOLERANCE, "W-intolerance", "intolerance"),
    (DIARRHEA, "W-diarrhea", "diarrhea"),
)


def listing(
    texts: Sequence[tuple[int, str]],
    facts: Facts,
    ambulance: Sequence[tuple[str, str]] = (),
    current: int | None = None,
    reply: int | None = None,
) -> list[Hit]:
    """The active K2 and W hits of the flow texts (index, text) and the ambulance hits of
    the current message. A reply to Graf's pregnancy question (message index `reply`) holds
    no pregnancy word fact (BO-08)."""
    found: list[Hit] = []
    slots = facts.slots()
    for index, text in texts:
        for i, r in active(text, scan(text, DOCTOR, slots), facts.inactive):
            if r == "hit":
                found.append(Hit(i, "K2", ENTRY[i]["sign"], r, index))
        for entries, kind, sign in W_KINDS:
            if kind == "W-pregnancy" and index == reply:
                continue
            for i, r in active(text, scan(text, entries), facts.inactive):
                if r == "hit":
                    found.append(Hit(i, kind, sign, r, index))
        for slot in TOPICS:
            if facts.topic(text, slot):
                found.append(Hit(slot, "W-topic", slot, "hit", index))
    if current is not None:
        for i, r in ambulance:
            found.append(Hit(i, "K3-" + TIER[i], ENTRY[i]["sign"], r, current))
    unique: dict[str, Hit] = {}
    for hit in found:
        unique.setdefault(hit.id, hit)
    return list(unique.values())


def hit_words(source: str, quote: str) -> bool:
    """BO-02: the quote holds the hit's own words: an entry's terms (or its near groups
    within its window) with its match mode and not_prefix, or a term of the topic's
    vocabulary. requires_any, requires_slot, unless_any and negation are not evaluated."""
    words = Words.of(quote)
    entry = ENTRY.get(source)
    if entry is not None:
        if "near" in entry:
            return bool(_near(words, entry))
        nap = entry.get("not_prefix", [])
        return any(positions(words, t, entry["match"], nap) for t in entry["terms"])
    return any(
        positions(words, term, item["match"])
        for item in VOCAB
        if item["slot"] == source
        for term in item["terms"]
    )


class Graf:
    """Steps 2, 3 and 5 to 7 of plan r4 section 3; service.py runs steps 1 and 4."""

    def __init__(self, jev: Jev) -> None:
        self.jev = jev

    def stage(self, request: Turn, context: Context) -> Stage:
        """Step 2: the ambulance list on every message, before register and snapshot. A
        pending check is the token's check (version 2) or a check text as the last answer
        (version 1)."""
        level, hits = tier(request.question, {"pregnant": pregnancy_slot(request, context)})
        found = tuple(hits)
        if level == "strong":
            templates = {
                ENTRY[i]["template"] for i, r in hits if r == "hit" and TIER[i] == "strong"
            }
            template = next(
                t
                for t in ("emergency.self_harm", "emergency.pregnancy", "emergency.general")
                if t in templates
            )
            return Stage("strong", Outcome("emergency", None, template), found)
        if level == "weak":
            checked = digest(request.question)
            check = Check(checked, (checked,))
            return Stage("weak", Outcome("check", None, "emergency.check"), found, check=check)
        previous = request.history[-1] if request.history else None
        general = Outcome("emergency", None, "emergency.general")
        if context.version == 1:
            if previous is None or previous.answer not in CHECKS:
                return Stage("none")
            cleared: tuple[str, ...] = (digest(previous.question),)
        elif context.check is None:
            return Stage("none")
        elif previous is None or digest(previous.question) != context.check.checked:
            # The message before this reply is not the checked one: an unclear reply.
            return Stage("reply", general)
        else:
            cleared = tuple(dict.fromkeys((context.check.checked, *context.check.quotes)))
        # The checked message continues from step 3 with its ambulance hits ignored.
        checked_request = request.model_copy(
            update={"question": previous.question, "history": request.history[:-1]}
        )
        outcome = None if parse_yes_no(request.question) is False else general
        return Stage("reply", outcome, checked=checked_request, cleared=cleared)

    @staticmethod
    def owns(request: Turn) -> bool:
        """Step 3 condition (version 1): the last answer is a Graf question."""
        return bool(request.history) and decode(request.history[-1].answer) is not None

    def adopt(self, request: Turn) -> tuple[bool, FlowState | None]:
        """The flow state of a version 1 conversation, rebuilt from its history: (owned,
        state). Owned with no state is today's step aside (the start left the window)."""
        if not self.owns(request):
            return False, None
        flow = rebuild(request.history)
        if flow is None:
            return True, None
        first = flow.facts.texts[0]
        start = max(i for i, p in enumerate(request.history) if p.question == first)
        outcome = Outcome("question", flow.asked.situation, flow.asked.template)
        received = len(request.history)
        # The state as the previous response would have issued it: counter up to the
        # message before the current one.
        state = self.state(outcome, flow.facts, tuple(flow.templates[:-1]), first, received - start)
        return True, state

    async def observe(self, text: str, run: Runner, lens: Lens) -> Choice | None:
        """Jev on a text, once per lens memo; a served model that is not pinned is logged
        and treated as unavailable by the callers."""
        if text not in lens.memo:
            attempt = await run(self.jev.attempt(text))
            lens.memo[text] = attempt
            if attempt.choice is not None and not attempt.choice.pinned:
                self.mismatch(attempt.choice)
        return lens.memo[text].choice

    async def continuation(
        self,
        request: Turn,
        flow: FlowState,
        run: Runner,
        skipped: int = 0,
        lens: Lens | None = None,
    ) -> Brief:
        """Step 3: a reply to the flow's pending question."""
        lens = lens or Lens()
        start, texts = flow_texts(request, flow)
        facts = restore(flow, texts[:-1])
        lens.extend(facts)
        origin = Origin(start, flow.asked, flow.overruled, skipped)
        if flow.pending != "slot":
            return await self.answered(request, flow, facts, origin, run, lens)
        assert flow.situation is not None
        assert flow.template is not None
        parsed = facts.answer(request.question, flow.template, flow.situation)
        lens.apply(facts)
        if lens.topic_change:
            return await self.changed(request, flow, parsed is False, run, lens, skipped)
        situation = parsed if isinstance(parsed, str) else flow.situation
        template = doctor(facts, situation)
        if template:
            return self.brief(request, Outcome("doctor", situation, template), facts, origin)
        if parsed is False:
            count = repeats(flow.asked, flow.template)
            choice = await self.observe(request.question, run, lens)
            if lens.filled(facts, slot_name(flow.template)):
                # Luna's sure value fills the slot the parser missed: no re-ask (HR-05).
                return self.brief(
                    request, self.settle(decide(situation, facts), facts), facts, origin
                )
            if (
                choice is not None
                and choice.pinned
                and choice.confidence >= THRESHOLD
                and choice.situation not in {flow.situation, "unclear"}
            ):
                # A new complaint: a new flow from this message.
                new = Origin(len(request.history), skipped=skipped)
                return self.opening(request, choice, new, lens)
            if count >= 2:
                outcome = Outcome("no_product", flow.situation, "no_product.general")
                return self.brief(request, outcome, facts, origin)
            outcome = Outcome("question", flow.situation, REASK[slot_name(flow.template)])
            return self.brief(request, outcome, facts, origin)
        return self.brief(request, self.settle(decide(situation, facts), facts), facts, origin)

    async def changed(
        self,
        request: Turn,
        flow: FlowState,
        missed: bool,
        run: Runner,
        lens: Lens,
        skipped: int,
    ) -> Brief:
        """A topic change inside a flow that Luna read with a valid quote (spec r2 HR-06).
        Jev runs only when the parser missed the reply (HR-05)."""
        choice = await self.observe(request.question, run, lens) if missed else None
        new = Origin(len(request.history), skipped=skipped)
        ok = choice is not None and choice.pinned
        if (
            choice is not None
            and ok
            and choice.confidence >= THRESHOLD
            and choice.situation not in {flow.situation, "unclear"}
        ):
            return self.opening(request, choice, new, lens)
        hypothesis = None
        if lens.situation is not None and lens.situation != flow.situation:
            hypothesis = lens.situation
        elif choice is not None and ok and choice.situation in COMPLAINTS - {flow.situation}:
            hypothesis = choice.situation
        facts = lens.fresh()
        facts.absorb(request.question)
        lens.apply(facts)
        outcome = (
            self.ask("confirm", (hypothesis,), facts)
            if hypothesis
            else self.ask("clarify", (), facts)
        )
        return self.brief(request, outcome, facts, new)

    async def answered(
        self,
        request: Turn,
        flow: FlowState,
        facts: Facts,
        origin: Origin,
        run: Runner,
        lens: Lens,
    ) -> Brief:
        """A reply to a pending situation-level question (spec r2 GO-03 with MR-09)."""
        facts.absorb(request.question)
        lens.apply(facts)
        template = doctor(facts, None, words_only=True)
        if template:
            return self.brief(request, Outcome("doctor", None, template), facts, origin)
        yes = parse_yes_no(request.question) is True
        if flow.pending == "confirm" and (yes or lens.situation == flow.options[0]):
            situation = flow.options[0]
            lens.resolution = ("confirm", situation)
            outcome = self.settle(decide(situation, facts, situation), facts)
            return self.brief(request, outcome, facts, origin)
        choice = await self.observe(request.question, run, lens)
        sure = choice is not None and choice.pinned and choice.confidence >= THRESHOLD
        picked = None
        if flow.pending == "choose":
            if choice is not None and sure and choice.situation in flow.options:
                picked = choice.situation
            elif lens.situation in flow.options:
                picked = lens.situation
        if picked is not None:
            lens.resolution = ("choose", picked)
            outcome = self.settle(decide(picked, facts, picked), facts)
            return self.brief(request, outcome, facts, origin)
        new = (
            choice is not None
            and sure
            and choice.situation != "unclear"
            and choice.situation not in flow.options
        ) or (lens.situation is not None and lens.situation not in flow.options)
        if new:
            return self.brief(request, self.decision(choice, facts, lens), facts, origin)
        if repeats(flow.asked, flow.pending) >= 2:
            outcome = Outcome("no_product", None, "no_product.general")
            return self.brief(request, outcome, facts, origin)
        if flow.pending == "choose":
            lens.repeat = True
            return self.brief(request, self.ask("choose", flow.options, facts), facts, origin)
        return self.brief(request, self.ask("clarify", (), facts), facts, origin)

    async def first(
        self, request: Turn, run: Runner, skipped: int = 0, lens: Lens | None = None
    ) -> Brief:
        """Step 5: a first Graf turn. It never steps aside (spec r2 GO-01)."""
        lens = lens or Lens()
        choice = await self.observe(request.question, run, lens)
        return self.opening(request, choice, Origin(len(request.history), skipped=skipped), lens)

    def opening(
        self, request: Turn, choice: Choice | None, origin: Origin, lens: Lens | None = None
    ) -> Brief:
        lens = lens or Lens()
        facts = lens.fresh()
        facts.absorb(request.question)
        lens.apply(facts)
        return self.brief(request, self.decision(choice, facts, lens), facts, origin)

    def decision(self, choice: Choice | None, facts: Facts, lens: Lens | None = None) -> Outcome:
        """A first-message decision: Jev's choice merged with Luna's sure situation (spec r2
        MR-08 rows M1 to M6; without Luna these are the GO-01 decisions)."""
        lens = lens or Lens()
        luna = lens.situation
        if choice is None or not choice.pinned:  # M6
            if luna is not None:
                return self.ask("confirm", (luna,), facts)
            return self.ask("clarify", (), facts)
        top = choice.situation
        if choice.confidence < THRESHOLD or top == "unclear":  # M5
            if luna is not None:
                return self.ask("confirm", (luna,), facts)
            if top in COMPLAINTS:
                return self.ask("confirm", (top,), facts)
            return self.ask("clarify", (), facts)
        if top == "other_unhandled":  # M4
            if luna is not None:
                return self.ask("confirm", (luna,), facts)
            return Outcome("uncovered", top, "no_product.general")
        second = choice.second()
        if top in COMPLAINTS and second and second[1] >= SECOND and second[0] in COMPLAINTS:
            ours, theirs = GROUP.get(top), GROUP.get(second[0])
            if ours and theirs and ours != theirs:  # M2
                return self.ask("choose", (top, second[0]), facts)
        outcome = self.settle(decide(top, facts, top), facts)
        if top not in COMPLAINTS:  # M3
            return outcome
        # M1: Luna agrees, abstains, or reads another situation with another decision.
        if luna is None:
            lens.agreement = "abstain" if lens.read else None
            return outcome
        if luna == top:
            lens.agreement = "agree"
            return outcome
        theirs_outcome = self.settle(decide(luna, facts, luna), facts)
        if signature(theirs_outcome, situation=False) == signature(outcome, situation=False):
            lens.agreement = "same_decision"
            return outcome
        lens.agreement = "disagree"
        return self.ask("choose", (top, luna), facts)

    def settle(self, outcome: Outcome, facts: Facts) -> Outcome:
        return self.ask("clarify", (), facts) if outcome.kind == "step_aside" else outcome

    @staticmethod
    def ask(kind: str, options: tuple[str, ...], facts: Facts) -> Outcome:
        """A situation-level question, unless a doctor word-list row hits the flow texts
        first (spec r2 GO-02)."""
        template = doctor(facts, None, words_only=True)
        if template:
            return Outcome("doctor", None, template)
        return Outcome(kind, None, None, options=options)

    def brief(self, request: Turn, outcome: Outcome, facts: Facts, origin: Origin) -> Brief:
        received = [p.question for p in request.history] + [request.question]
        counter = len(received) + origin.skipped - origin.start
        state = self.state(
            outcome, facts, origin.asked, received[origin.start], counter, facts.inactive
        )
        return Brief(outcome, facts, state, origin)

    @staticmethod
    def state(
        outcome: Outcome,
        facts: Facts,
        asked: tuple[str, ...],
        first: str,
        counter: int,
        overruled: tuple[tuple[str, str], ...] = (),
    ) -> FlowState | None:
        """The flow state after a question; None after a final outcome (the flow ends)."""
        if outcome.kind == "question":
            assert outcome.template is not None
            pending, item = "slot", outcome.template
        elif outcome.kind in QUESTION_KINDS:
            pending = item = outcome.kind
        else:
            return None
        values = {
            name: (value, facts.origin.get(name, "parser"))
            for name in FACT_TYPES
            if (value := getattr(facts, name)) is not None
        }
        return FlowState(
            outcome.situation,
            pending,
            outcome.template,
            (*asked, item),
            outcome.options,
            digest(first),
            counter,
            values,
            frozenset(flag for flag in FLAGS if getattr(facts, flag)),
            tuple(sorted(facts.topics)),
            overruled,
        )

    def render(self, request: Turn, brief: Brief, evidence: Evidence) -> Reply | None:
        """Graf's approved text for the brief's decision; None for a situation-level
        question, which has no approved text."""
        if brief.outcome.kind in SITUATION_LEVEL:
            self.log(None, brief.outcome.kind)
            return None
        return self.reply(Writer(request), brief.outcome, evidence, brief.facts)

    def reply(
        self,
        writer: Writer,
        outcome: Outcome,
        evidence: Evidence | None,
        facts: Facts | None = None,
        log: bool = True,
    ) -> Reply | None:
        if log:
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
            parts = tuple(writer.parts)
            return Reply(
                "clarification", text, [], [], [], [], "question", outcome.situation, parts
            )
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
        parts = tuple(writer.parts)
        return Reply(
            STATUS[outcome.kind], text, [], [], [], [], outcome.kind, outcome.situation, parts
        )

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
            parts = tuple(writer.parts)
            return Reply("limited", text, [], [], [], [], "unavailable", outcome.situation, parts)
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
            blocks.append((spec["shop_name"], writer.data(reason["reason_" + language]), line))
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
        parts = tuple(writer.parts)
        return Reply(
            "answer", text, ids, products, sources, cautions, "offer", outcome.situation, parts
        )

    @staticmethod
    def mismatch(choice: Choice) -> None:
        served = re.sub(r"[^0-9A-Za-z._/~:-]", "", choice.model)[:80]
        logger.warning("graf jev version mismatch served=%s", served)

    @staticmethod
    def log(situation: str | None, outcome: str) -> None:
        logger.info("graf turn situation=%s outcome=%s", situation or "-", outcome)


__all__ = ["Attempt", "Brief", "Choice", "Graf", "Jev", "Lens", "Part", "Reply", "Stage", "replace"]
