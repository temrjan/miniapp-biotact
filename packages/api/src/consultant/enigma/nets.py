"""Enigma's deterministic nets (spec r2 N-01…N-06): word lists and stated numbers.

The matching rules are those of graf_data/safety.json match_spec, in Enigma's own copy: lower
case and ASCII apostrophe, word and stem terms, 'near' groups inside a window, negation inside
one clause. A net result never ends a turn: it is material for the hard rules and for Luna.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from src.consultant.enigma.data import NETS

APOSTROPHES = str.maketrans(dict.fromkeys("‘’ʻʼ`´", "'"))
TOKEN = re.compile(r"[0-9a-zа-яёўқғҳ']+")
CLAUSE = re.compile(r"[,.;:!?\n—–]+|\s-\s")
NEGATION = NETS.negation
# The emergency text of a hit, strongest first; a 'check' row has no text of its own.
TEMPLATES = ("emergency.self_harm", "emergency.pregnancy", "emergency.general")


def norm(text: str) -> str:
    """Lower case and ASCII apostrophe; no yo-to-ye folding."""
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
    # A term 'е' also accepts 'ё' in the message; a term 'ё' needs 'ё'.
    if "ё" not in stem:
        return token.replace("ё", "е").startswith(stem)
    return len(token) >= len(stem) and all(
        a == b or (a == "е" and b == "ё") for a, b in zip(stem, token, strict=False)
    )


def _token_match(token: str, part: str, mode: str, not_prefix: Sequence[str]) -> bool:
    matched = _prefix(token, part) and (mode != "word" or len(token) == len(part))
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


def negated(words: Words, matched: Sequence[int]) -> bool:
    inside = set(matched)
    toks, clauses = words.toks, words.clauses
    for p in matched:
        for j in range(max(0, p - NEGATION.window_before), p):
            if j not in inside and clauses[j] == clauses[p] and toks[j] in NEGATION.before:
                return True
    last = max(matched)
    for j in range(last + 1, min(len(toks), last + 1 + NEGATION.window_after)):
        if j not in inside and clauses[j] == clauses[last] and toks[j] in NEGATION.after:
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
    """'hit', 'negated' or None for one list row."""
    if any(positions(words, item, "stem") for item in entry.get("unless_any", ())):
        return None
    if entry.get("requires_any"):
        context = any(
            not negated(words, span)
            for item in entry["requires_any"]
            for span in positions(words, item, "stem")
        )
        if not context and not slots.get(entry.get("requires_slot", ""), False):
            return None
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


def hit(words: Words, entries: Iterable[dict[str, Any]]) -> bool:
    """A non-negated match of any row."""
    return any(run_entry(words, entry, {}) == "hit" for entry in entries)


# --- net 1: the ambulance list (N-01) -------------------------------------------------------


@dataclass(frozen=True)
class Ambulance:
    tier: Literal["strong", "weak"]
    template: str


def ambulance(text: str, pregnant: bool) -> Ambulance | None:
    """The ambulance result of one message; `pregnant` is the conversation's pregnancy flag."""
    words = Words.of(text)
    found = [
        (entry, result)
        for entry in NETS.ambulance
        if (result := run_entry(words, entry, {"pregnant": pregnant}))
    ]
    if not found:
        return None
    strong = [e for e, r in found if r == "hit" and e["tier"] == "strong"]
    templates = {e["template"] for e in strong or [e for e, _ in found]}
    template = next((t for t in TEMPLATES if t in templates), TEMPLATES[-1])
    return Ambulance("strong" if strong else "weak", template)


# --- stated numbers (N-03) ------------------------------------------------------------------

NUM = (
    r"(\d{1,3}(?:[.,]\d)?|"
    + "|".join(re.escape(w) for w in sorted(NETS.number_words, key=len, reverse=True))
    + ")"
)
LEFT = r"(?<![0-9a-zа-яёўқғҳ'])"
RIGHT = r"(?![0-9a-zа-яёўқғҳ'])"
DURATION = NETS.duration
DURATION_UNITS = [
    (re.compile(LEFT + NUM + r"\s*" + unit + RIGHT), name)
    for units in DURATION.patterns.values()
    for name, unit in units.items()
]
ORDINAL = re.compile(DURATION.ordinal_pattern)
UNIT_FIRST = re.compile(DURATION.unit_first_pattern)
TEMPERATURE = [re.compile(pattern) for pattern in NETS.temperature]
ADVERBS = frozenset(NETS.duration_adverbs)
FREQUENCY = frozenset(NETS.frequency)
LIMITS = NETS.thresholds


def number(value: str) -> float:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return NETS.number_words[value]


def _beside(text: str, start: int, end: int, marks: frozenset[str]) -> bool:
    before = tokens(re.split(r"[,.;!?]", text[:start])[-1])[-2:]
    after = tokens(re.split(r"[,.;!?]", text[end:], maxsplit=1)[0])[:2]
    return any(t in marks for t in before + after)


def _frequent(text: str, start: int) -> bool:
    """A duration that directly follows a frequency word ('каждый месяц') is not a duration."""
    before = tokens(text[:start])
    return bool(before) and before[-1] in FREQUENCY


def parse_duration(text: str) -> float | None:
    """The largest stated duration in days."""
    t = norm(text)
    found: list[float] = []
    for pattern, unit in DURATION_UNITS:
        for m in pattern.finditer(t):
            if _frequent(t, m.start()) or (
                unit == "years" and not _beside(t, m.start(), m.end(), ADVERBS)
            ):
                continue
            found.append(number(m[1]) * DURATION.multipliers_days[unit])
    found += [
        float(DURATION.ordinals[m[1]]) for m in ORDINAL.finditer(t) if not _frequent(t, m.start())
    ]
    for m in UNIT_FIRST.finditer(t):
        if _frequent(t, m.start()):
            continue
        first: str = m[1]
        each = 1 if first.startswith(("дн", "сут")) else 7 if first.startswith("недел") else 30
        found.append(number(m[2]) * each)
    if not found:
        toks = tokens(t)
        for word, days in DURATION.words.items():
            parts = word.split()
            for i in range(len(toks) - len(parts) + 1):
                if toks[i : i + len(parts)] == parts and (i == 0 or toks[i - 1] not in FREQUENCY):
                    found.append(float(days))
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


def number_rule(text: str) -> bool:
    """Temperature above the limit, or a fever, cold or cough longer than its limit."""
    temperature = parse_temperature(text)
    if temperature is not None and temperature > LIMITS.temperature:
        return True
    days = parse_duration(text)
    if days is None:
        return False
    words = Words.of(text)

    def topic(name: Literal["fever", "cold", "cough"]) -> bool:
        return any(
            not negated(words, span)
            for term in NETS.topics[name]
            for span in positions(words, term, "stem")
        )

    return (
        (days > LIMITS.fever_days and topic("fever"))
        or (days > LIMITS.cold_days and topic("cold"))
        or (days > LIMITS.cough_days and topic("cough"))
    )


# --- the nets of one customer message (N-02…N-05) -------------------------------------------


@dataclass(frozen=True)
class Signs:
    doctor: bool
    number: bool
    intolerance: bool
    diarrhea: bool
    pregnancy: bool

    @property
    def any(self) -> bool:
        return self.doctor or self.number or self.intolerance or self.diarrhea or self.pregnancy


def signs(text: str) -> Signs:
    words = Words.of(text)
    return Signs(
        doctor=hit(words, NETS.doctor),
        number=number_rule(text),
        intolerance=hit(words, NETS.intolerance),
        diarrhea=hit(words, NETS.diarrhea),
        pregnancy=hit(words, NETS.pregnancy),
    )
