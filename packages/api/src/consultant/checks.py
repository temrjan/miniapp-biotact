"""Checks of Luna's customer text against the reference of the final decision (spec r2 §9).

Pure functions over code-resident lexicons. Every list holds Russian and Uzbek Latin entries
(some Uzbek Cyrillic ones too) and is compared after Graf `norm`. A reference is built from
the parts Graf's Writer rendered for a decision (template keys with their values, and
products.json strings); a situation-level question has no parts (reference R-Q).
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from itertools import pairwise

from src.consultant.context import SENSITIVE, normalize
from src.consultant.graf import (
    AGE,
    DURATION,
    NUMBER_WORDS,
    TEXT,
    Part,
    Words,
    norm,
    positions,
    tokens,
)

# --- TC-02: numbers, units, periods --------------------------------------------------------

# Number words beyond safety.json number_words (oblique cases, halves, counted Uzbek forms).
NUMBER_TABLE: dict[str, float] = {
    "половина": 0.5,
    "половину": 0.5,
    "половины": 0.5,
    "одного": 1,
    "одной": 1,
    "двух": 2,
    "трёх": 3,
    "трех": 3,
    "четырёх": 4,
    "четырех": 4,
    "пяти": 5,
    "шести": 6,
    "семи": 7,
    "восьми": 8,
    "девяти": 9,
    "десяти": 10,
    "двадцати": 20,
    "тридцати": 30,
    "yarmi": 0.5,
    "ярми": 0.5,
    "bitta": 1,
    "ikkita": 2,
    "uchta": 3,
    "битта": 1,
    "иккита": 2,
    "учта": 3,
}
WORD_NUMBERS: dict[str, float] = {**NUMBER_WORDS, **NUMBER_TABLE}
# Phrases in which a number word names no number.
IDIOMS: tuple[tuple[str, ...], ...] = (
    ("не", "один"),
    ("не", "одна"),
    ("не", "одну"),
    ("bir", "necha"),
    ("бир", "неча"),
    ("bir", "oz"),
    ("biror",),
    ("yana", "bir", "bor"),
    ("bir", "xil"),
)
SKIP = frozenset({"ta", "dona", "та", "дона", "по"})
SELF_BOUND = {"дважды": 2.0, "трижды": 3.0}
FRACTION = frozenset({"yarmi", "ярми"})
# Dose unit stems: (stem, canonical unit, whole token only).
DOSE_UNITS: tuple[tuple[str, str, bool], ...] = (
    ("стик", "stick", False),
    ("stik", "stick", False),
    ("капсул", "capsule", False),
    ("kapsul", "capsule", False),
    ("таблет", "tablet", False),
    ("tabletka", "tablet", False),
    ("мг", "mg", True),
    ("мкг", "mcg", True),
    ("мл", "ml", True),
    ("капл", "drop", False),
    ("tomchi", "drop", False),
    ("томчи", "drop", False),
    ("ложк", "spoon", False),
    ("qoshiq", "spoon", False),
    ("қошиқ", "spoon", False),
    ("саше", "sachet", False),
    ("пакет", "sachet", False),
    ("paket", "sachet", False),
    ("порошк", "powder", False),
    ("kukun", "powder", False),
    ("кукун", "powder", False),
)
DOSE = frozenset(unit for _, unit, _ in DOSE_UNITS)
FREQUENCY_WORDS = frozenset({"раз", "раза", "marta", "марта"})
TIMES = "times"
DURATION_UNITS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), unit)
    for units in DURATION["patterns"].values()
    for unit, pattern in units.items()
)
# safety.json age.reply_units, grouped by unit (a test compares the union with the data).
AGE_UNITS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(лет|год|года|годик|годика|yosh\w*|ёш\w*)"), "years"),
    (re.compile(r"(мес|месяц|месяца|месяцев|oy|oylik|ой|ойлик)"), "months"),
    (re.compile(r"(недел\w*)"), "weeks"),
)
AGE_UNIT_WORDS = re.compile(AGE["reply_units"])
PERIODS: dict[str, tuple[str, ...]] = {
    "per_day": (
        "в день",
        "в сутки",
        "ежедневно",
        "каждый день",
        "kuniga",
        "кунига",
        "har kuni",
        "ҳар куни",
        "sutkasiga",
    ),
    "per_week": ("в неделю", "haftasiga", "haftada"),
    "per_month": ("в месяц", "oyiga"),
    "every_other_day": ("через день", "kun ora"),
}
ITEM = re.compile(r"\d+(?:[.,]\d+)?|[–-]|[0-9a-zа-яёўқғҳ']+")
DIGITS = re.compile(r"\d+(?:[.,]\d+)?")


def unit_of(token: str) -> str | None:
    """The canonical unit a token names: dose, frequency, duration or age unit, else None."""
    for stem, unit, whole in DOSE_UNITS:
        if token == stem if whole else token.startswith(stem):
            return unit
    if token in FREQUENCY_WORDS:
        return TIMES
    for pattern, unit in (*DURATION_UNITS, *AGE_UNITS):
        if pattern.fullmatch(token):
            return unit
    return None


@dataclass(frozen=True)
class Numbers:
    """What TC-02 finds in a text: (number, unit) pairs, counted numbers, every number
    (counted or not) and the period classes."""

    pairs: frozenset[tuple[float, str]]
    counted: frozenset[float]
    every: frozenset[float]
    periods: frozenset[str]


def _items(text: str) -> list[str]:
    return [t.strip("'") if t[0].isalpha() or t[0] == "'" else t for t in ITEM.findall(norm(text))]


def extract(text: str) -> Numbers:
    items = [t for t in _items(text) if t]
    idiom: set[int] = set()
    for phrase in IDIOMS:
        for i in range(len(items) - len(phrase) + 1):
            if tuple(items[i : i + len(phrase)]) == phrase:
                idiom.update(range(i, i + len(phrase)))

    def value(i: int) -> float | None:
        token = items[i]
        if DIGITS.fullmatch(token):
            return float(token.replace(",", "."))
        if i in idiom:
            return None
        return WORD_NUMBERS.get(token)

    def bind(k: int) -> str | None:
        seen = 0
        while k < len(items) and seen < 3:
            token = items[k]
            unit = unit_of(token)
            if unit is not None:
                return unit
            if value(k) is not None:
                return None
            if token not in SKIP:
                seen += 1
            k += 1
        return None

    pairs: set[tuple[float, str]] = set()
    counted: set[float] = set()
    every: set[float] = set()
    consumed: set[int] = set()
    i = 0
    while i < len(items):
        token = items[i]
        unit = unit_of(token)
        if unit in DOSE and i + 1 < len(items) and items[i + 1] in FRACTION:
            pairs.add((0.5, unit))
            counted.add(0.5)
            every.add(0.5)
            consumed.add(i + 1)
        if token in SELF_BOUND:
            pairs.add((SELF_BOUND[token], TIMES))
        number = None if i in consumed else value(i)
        if number is None:
            i += 1
            continue
        group = [(i, number)]
        end = i
        if end + 2 < len(items) and items[end + 1] in {"–", "-"}:
            second = value(end + 2)
            if second is not None:
                group.append((end + 2, second))
                end += 2
        bound = bind(end + 1)
        for k, n in group:
            every.add(n)
            if bound is not None:
                pairs.add((n, bound))
                counted.add(n)
            elif DIGITS.fullmatch(items[k]):
                counted.add(n)
        i = end + 1
    words = Words.of(text)
    periods = {
        name
        for name, phrases in PERIODS.items()
        if any(positions(words, phrase, "word") for phrase in phrases)
    }
    return Numbers(frozenset(pairs), frozenset(counted), frozenset(every), frozenset(periods))


def customer_numbers(messages: Iterable[str]) -> frozenset[float]:
    """Every number of the customer messages, number words counted also when unbound."""
    return frozenset(n for m in messages for n in extract(m).every)


def has_number(quote: str, number: float) -> bool:
    return number in extract(quote).every


def has_unit(quote: str, unit: str, kind: str) -> bool:
    """A unit word of that unit in the quote: age units (age.reply_units) or duration
    units (duration.patterns)."""
    table = AGE_UNITS if kind == "age" else DURATION_UNITS
    return any(
        pattern.fullmatch(token) for token in tokens(quote) for pattern, u in table if u == unit
    )


# --- TC-04, TC-05, TC-10 lexicons ----------------------------------------------------------

ANCHORS: dict[str, tuple[str, ...]] = {
    "urgency": (
        "сразу",
        "срочно",
        "немедленно",
        "прямо сейчас",
        "не ждите",
        "не откладыва",
        "darhol",
        "zudlik",
        "hoziroq",
        "kutmang",
        "kechiktir",
    ),
    "doctor": ("врач", "педиатр", "доктор", "окулист", "shifokor", "pediatr", "okulist", "шифокор"),
    "pregnancy": ("беремен", "кормл", "кормящ", "грудн вскармлив", "homilador", "emiz"),
    # spec r6 TC-04: the website's add-to-basket label; every variant holds it only in
    # info.how_to_buy, so it is required there alone.
    "basket": ("в корзину", "savatga"),
}
TREATMENT = (
    "лечи",
    "леча",
    "лечат",
    "лечен",
    "лечебн",
    "вылеч",
    "излеч",
    "назнач",
    "диагноз",
    "davola",
    "даво",
    "tashxis",
    "ташхис",
)
CLAIMS = (
    "помож",
    "эффектив",
    "безопас",
    "гарант",
    "противопоказ",
    "натощак",
    "утром",
    "вечером",
    "перед ед",
    "после ед",
    "во время ед",
    "можно",
    "нельзя",
    "навсегда",
    "доказан",
    "клиничес",
    "натуральн",
    "без вред",
    "100",
    "yordam ber",
    "samarali",
    "xavfsiz",
    "kafolat",
    "nojo'ya",
    "och qoringa",
    "ertalab",
    "kechqurun",
    "ovqatdan oldin",
    "ovqatdan keyin",
    "mumkin",
    "butunlay",
    "isbotlangan",
    "tabiiy",
)
FORM_WORDS = ("стик", "капсул", "stik", "kapsul")
INTERNAL = frozenset({"graf", "граф", "jev", "джев"})
ANNOTATIONS = (
    "без уточнения",
    "без слова о весе",
    "без жалобы",
    "без продукта",
    "aniqlanmagan",
    "shikoyatsiz",
    "shikoyat yo'q",
)
FORM = re.compile(r"https?://|<[^>]+>|\]\(|\*\*|__|^\s*[#\-*•]", re.M)
# TC-06 web address without a scheme (spec r3 V-03, pattern ADDRESS): www. in any case; dotted
# Latin/digit labels ending in a label of two or more Latin letters followed by '/', in any
# case; the same without '/' when the last label is all lower or all upper case.
ADDRESS = re.compile(
    r"(?i:\bwww\.)"
    r"|(?i:\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[a-z]{2,}/)"
    r"|\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:[a-z]{2,}|[A-Z]{2,})\b"
)
# Cyrillic is also used for Uzbek. Clear Russian prose is rejected conservatively; shared
# script, product names or a single borrowed or quoted word are insufficient.
RUSSIAN_PROSE = re.compile(
    r"\b(?:это|для|или|при|также|можно|нельзя|следует|состав|указан[аоы]?|"
    r"содержит|детей|взрослых|инструкци[яию]|индивидуальная|непереносимость)\b",
    re.I,
)


def bad_form(text: str) -> bool:
    """TC-06: a link, an address without a scheme, HTML, Markdown, a list or heading line,
    or SENSITIVE."""
    return bool(FORM.search(text) or ADDRESS.search(text) or SENSITIVE.search(text))


def language_ok(language: str, text: str) -> bool:
    """Today's language rule of the product path (service.guard)."""
    if language == "ru":
        return re.search(r"[А-Яа-я]", text) is not None
    return len(set(RUSSIAN_PROSE.findall(text.casefold()))) < 2


def anchors(text: str) -> frozenset[str]:
    words = Words.of(text)
    found = {name for name, items in ANCHORS.items() for i in items if positions(words, i, "stem")}
    if "103" in words.toks:
        found.add("103")
    if "?" in text:
        found.add("question")
    return frozenset(found)


def claims(text: str) -> frozenset[str]:
    words = Words.of(text)
    return frozenset(c for c in CLAIMS if positions(words, c, "stem"))


def forms(text: str) -> frozenset[str]:
    words = Words.of(text)
    return frozenset(f for f in FORM_WORDS if positions(words, f, "stem"))


def treatment(token: str) -> bool:
    return token.startswith(TREATMENT) and not token.endswith(("ся", "сь"))


def bigrams(text: str) -> frozenset[tuple[str, str]]:
    toks = tokens(text)
    return frozenset(pairwise(toks))


# --- references ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """The reference of a final decision D1: its template family, its products and how the
    checks treat it. `parts` empty and `question` true is R-Q. `shown`, when set, is the
    displayed-selection set: the products found in the text must be a subset of it instead
    of equal to `products` (spec r6 TC-01 exception)."""

    language: str
    parts: tuple[Part, ...]
    products: frozenset[str]
    question: bool = False
    safety: bool = False
    extra: tuple[str, ...] = ()
    customer: frozenset[float] = frozenset()
    situation_level: bool = False
    shown: frozenset[str] | None = None

    @cached_property
    def families(self) -> tuple[tuple[str, ...], ...]:
        return tuple(p.variants(self.language) for p in self.parts)

    @cached_property
    def everything(self) -> tuple[str, ...]:
        return tuple(v for family in self.families for v in family) + self.extra

    @cached_property
    def numbers(self) -> tuple[Numbers, ...]:
        return tuple(extract(v) for v in self.everything)

    def required(self, items: Callable[[str], frozenset[object]]) -> frozenset[object]:
        out: set[object] = set()
        for family in self.families:
            sets = [items(v) for v in family]
            if sets:
                out |= frozenset.intersection(*sets)
        return frozenset(out)

    @cached_property
    def required_pairs(self) -> frozenset[object]:
        return self.required(lambda v: extract(v).pairs)

    @cached_property
    def required_anchors(self) -> frozenset[object]:
        found = set(self.required(anchors))
        if self.situation_level:
            found.add("question")
        return frozenset(found)

    @cached_property
    def allowed_pairs(self) -> frozenset[tuple[float, str]]:
        return frozenset(p for n in self.numbers for p in n.pairs)

    @cached_property
    def allowed_numbers(self) -> frozenset[float]:
        return frozenset(c for n in self.numbers for c in n.counted)

    @cached_property
    def allowed_periods(self) -> frozenset[str]:
        return frozenset(p for n in self.numbers for p in n.periods)

    @cached_property
    def allowed_claims(self) -> frozenset[str]:
        return frozenset(c for v in self.everything for c in claims(v))

    @cached_property
    def allowed_forms(self) -> frozenset[str]:
        return frozenset(f for v in self.everything for f in forms(v))

    @cached_property
    def allowed_bigrams(self) -> frozenset[tuple[str, str]]:
        return frozenset(b for v in self.everything for b in bigrams(v))


@dataclass(frozen=True)
class Catalogue:
    """Canonical product names by normalized alias: Graf shop names and the knowledge
    aliases of the current snapshot."""

    aliases: Mapping[str, str] = field(default_factory=dict)

    @staticmethod
    def of(names: Iterable[str], aliases: Mapping[str, Iterable[str]]) -> "Catalogue":
        table = {normalize(n): n for n in names}
        for canonical, items in aliases.items():
            for alias in items:
                if normalize(alias):
                    table.setdefault(normalize(alias), canonical)
        return Catalogue(table)

    def find(self, text: str) -> frozenset[str]:
        """Names as context.resolve matches them: substrings of the normalized text, long
        aliases consuming the short ones they overlap."""
        q = normalize(text)
        spans = sorted(
            (m.start(), -len(alias), self.aliases[alias])
            for alias in self.aliases
            for m in re.finditer(re.escape(alias), q)
        )
        found: set[str] = set()
        end = -1
        for start, negative, name in spans:
            if start >= end:
                found.add(name)
                end = start - negative
        return frozenset(found)


# --- the checks ----------------------------------------------------------------------------


def naming(text: str, age: float) -> bool:
    """TC-08: the age number followed within two tokens by an age unit word."""
    items = [t for t in _items(text) if t]
    for i, token in enumerate(items):
        number = (
            float(token.replace(",", ".")) if DIGITS.fullmatch(token) else WORD_NUMBERS.get(token)
        )
        if (
            number is not None
            and abs(number - age) < 1e-9
            and any(AGE_UNIT_WORDS.fullmatch(t) for t in items[i + 1 : i + 3])
        ):
            return True
    return False


def check(
    text: str,
    reference: Reference,
    catalogue: Catalogue,
    language: str,
    named_age: float | None = None,
) -> str | None:
    """The id of the first §9 check the text fails, or None when it passes all of them."""
    if not text.strip():
        return "blank"
    found = catalogue.find(text)
    if reference.shown is not None:
        if not found <= reference.shown:
            return "TC-01"
    elif found != reference.products:
        return "TC-01"
    if forms(text) - reference.allowed_forms:
        return "TC-01"
    numbers = extract(text)
    if not reference.question and reference.required_pairs - numbers.pairs:
        return "TC-03"
    if any(
        (unit in DOSE or unit == TIMES) and (n, unit) not in reference.allowed_pairs
        for n, unit in numbers.pairs
    ):
        return "TC-03"
    if numbers.counted - reference.allowed_numbers - reference.customer:
        return "TC-03"
    if any(unit in DOSE for _, unit in reference.allowed_pairs) and (
        numbers.periods - reference.allowed_periods
    ):
        return "TC-03"
    if reference.required_anchors - anchors(text):
        return "TC-04"
    if reference.safety and found:
        return "TC-04"
    toks = tokens(text)
    for i, token in enumerate(toks):
        if treatment(token):
            near = {(toks[i - 1], token)} if i else set()
            if i + 1 < len(toks):
                near.add((token, toks[i + 1]))
            if not near & reference.allowed_bigrams:
                return "TC-05"
    if claims(text) - reference.allowed_claims:
        return "TC-05"
    if bad_form(text):
        return "TC-06"
    if not language_ok(language, text):
        return "TC-07"
    if named_age is not None and not naming(text, named_age):
        return "TC-08"
    if INTERNAL & set(toks) or any(norm(a) in norm(text) for a in ANNOTATIONS):
        return "TC-10"
    return None


def question_reference(
    language: str, customer: frozenset[float], shown: frozenset[str] | None = None
) -> Reference:
    """R-Q: no parts; a question mark is required; only customer numbers are allowed. The
    post-offer question passes `shown` (spec r6 TC-01 exception)."""
    return Reference(
        language,
        (),
        frozenset(),
        question=True,
        customer=customer,
        situation_level=True,
        shown=shown,
    )


def reference(
    language: str,
    parts: Sequence[Part],
    products: Iterable[str],
    *,
    question: bool,
    safety: bool,
    customer: frozenset[float],
    self_harm: bool = False,
    shown: frozenset[str] | None = None,
) -> Reference:
    extra = TEXT["emergency.self_harm"][language] if self_harm else ()
    return Reference(
        language,
        tuple(parts),
        frozenset(products),
        question=question,
        safety=safety,
        extra=tuple(extra),
        customer=customer,
        shown=shown,
    )


__all__ = [
    "Catalogue",
    "Numbers",
    "Part",
    "Reference",
    "check",
    "customer_numbers",
    "extract",
    "question_reference",
    "reference",
]
