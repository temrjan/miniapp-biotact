"""Enigma's checks of a written text (spec r2 V-01…V-07; spec.shop-core r4 SV-01…SV-07). A text
that fails is not sent.

`check` returns the id of the first failed check or None. Product names are found by the
names of the stage-1 products and masked before the dose and number checks, so that a name
like 'Термос KD-775, 600 мл.' is not read as a number or a dose.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from src.consultant.context import SENSITIVE, normalize
from src.consultant.contracts import Language
from src.consultant.enigma import shop
from src.consultant.enigma.data import CORES, NETS, RULES
from src.consultant.enigma.nets import tokens

FORM = re.compile(
    r"https?://|www\.|<[^>]+>|\]\(|\*\*|__|`|^\s*(?:#|[-*•]\s|\d+[.)]\s)", re.I | re.M
)
CYRILLIC = re.compile(r"[а-яё]", re.I)
# Words of Russian prose that an Uzbek text does not hold (two or more fail the check).
RUSSIAN_PROSE = re.compile(
    r"\b(?:это|для|или|при|также|можно|нельзя|следует|состав|указан[аоы]?|содержит|детей|"
    r"взрослых|инструкци[яию]|индивидуальная|непереносимость|который|которая|может|врачу)\b",
    re.I,
)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
SENTENCE = re.compile(r"[^.!?]*[.!?]")
# A digit written together with a word ('1капсуле'): read as two words by the dose check.
GLUED = re.compile(r"(\d)([^\W\d])")
EMERGENCY_NUMBER = "103"


@dataclass(frozen=True)
class Names:
    """The names a text may hold, longest first, each with the products it stands for."""

    entries: tuple[tuple[str, frozenset[int]], ...]

    @staticmethod
    def of(aliases: Mapping[int, Iterable[str]]) -> "Names":
        """`aliases` are the knowledge snapshot's aliases of supplements; the data adds the
        product names of the units, every variant name of an item and the item's own name."""
        found: dict[str, set[int]] = {}

        def add(name: str, ids: Iterable[int]) -> None:
            if normalize(name):
                found.setdefault(normalize(name), set()).update(ids)

        supplements = {p.shop_id for unit in CORES.units for p in unit.products}
        for unit in CORES.units:
            for product in unit.products:
                add(product.name, [product.shop_id])
        for product_id, names in aliases.items():
            if product_id in supplements:
                for name in names:
                    add(name, [product_id])
        for item in CORES.items:
            everyone = [p.shop_id for p in item.products]
            add(item.name.ru, everyone)
            add(item.name.uz, everyone)
            for variant in item.products:
                add(variant.name.ru, [variant.shop_id])
                add(variant.name.uz, [variant.shop_id])
        ordered = sorted(found.items(), key=lambda kv: (-len(kv[0]), kv[0]))
        return Names(tuple((name, frozenset(ids)) for name, ids in ordered))

    def find(self, text: str) -> tuple[list[frozenset[int]], str]:
        """The product sets named in a text, and the text with the names masked."""
        rest = normalize(text)
        named: list[frozenset[int]] = []
        for name, ids in self.entries:
            pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")
            if pattern.search(rest):
                named.append(ids)
                rest = pattern.sub(" ", rest)
        return named, rest


@dataclass(frozen=True)
class Subject:
    """What a text is checked against."""

    language: Language
    decision: str
    # offer: the products of the offered units and items after the hard rules, and the
    # products shown as cards; ask and no_product: the incoming selection in both.
    allowed: frozenset[int]
    shown: frozenset[int]
    # Numbers of the brief and of the customer messages.
    numbers: frozenset[str]
    # Approved texts and instruction lines of the brief.
    approved: tuple[str, ...]
    # spec.shop-core r4 §2.5: `A` (the chosen topics' texts and the support text) when the brief
    # held `shop`, and the customer's messages.
    shop: tuple[str, ...] = ()
    customers: tuple[str, ...] = ()


def numbers(texts: Iterable[str]) -> frozenset[str]:
    return frozenset(n.replace(",", ".") for text in texts for n in NUMBER.findall(text))


def _stems(toks: Sequence[str], stems: Iterable[str]) -> list[int]:
    """Start positions of every stem (one or more words, prefix match per word)."""
    found = []
    for stem in stems:
        parts = stem.split()
        for i in range(len(toks) - len(parts) + 1):
            if all(toks[i + j].startswith(part) for j, part in enumerate(parts)):
                found.append(i)
    return found


def _form(text: str, language: Language) -> bool:
    if not text.strip() or FORM.search(text) or SENSITIVE.search(text):
        return False
    if language == "ru" and not CYRILLIC.search(text):
        return False
    if language == "uz" and len(set(RUSSIAN_PROSE.findall(text.casefold()))) >= 2:
        return False
    return not set(tokens(text)) & set(RULES.internal_words)


def _names(named: list[frozenset[int]], subject: Subject, names: "Names") -> bool:
    if subject.decision in ("doctor", "emergency"):
        return not named
    allowed = set(subject.allowed)
    if subject.decision == "shop":  # SV-05: the products the shop texts name
        for ids in names.find("\n".join(subject.shop))[0]:
            allowed |= ids
    if any(not ids & allowed for ids in named):
        return False
    return subject.decision != "offer" or any(ids & subject.shown for ids in named)


def _dose(toks: Sequence[str]) -> bool:
    dose = RULES.dose
    return not any(
        (a.isdigit() or a in NETS.number_words)
        and (b in dose.words or any(b.startswith(stem) for stem in dose.stems))
        for a, b in pairwise(toks)
    )


def _numbers(masked: str, subject: Subject) -> bool:
    found = numbers([masked])
    if subject.decision == "emergency":
        return EMERGENCY_NUMBER in found and found <= subject.numbers | {EMERGENCY_NUMBER}
    return found <= subject.numbers


def _treatment(toks: Sequence[str], subject: Subject) -> bool:
    pairs = {pair for text in subject.approved for pair in pairwise(tokens(text))}
    for i in _stems(toks, RULES.treatment_stems):
        if subject.decision == "shop" and shop.warranty_word(toks[i]):
            continue  # SV-07: a warranty question gets a support answer
        before = (toks[i - 1], toks[i]) if i else None
        after = (toks[i], toks[i + 1]) if i + 1 < len(toks) else None
        if before not in pairs and after not in pairs:
            return False
    return True


def _questions(text: str) -> bool:
    return not any(
        _stems(tokens(sentence), RULES.question_stems)
        for sentence in SENTENCE.findall(text)
        if sentence.endswith("?")
    )


def check(text: str, subject: Subject, names: Names) -> str | None:
    """The id of the first check the text fails, or None."""
    # SV-04: the shop phone of `A` is masked before the SENSITIVE search of V-01.
    if not _form(shop.mask(text, shop.public_values(subject.shop)), subject.language):
        return "V-01"
    named, masked = names.find(text)
    if not _names(named, subject, names):
        return "V-02"
    toks = tokens(masked)
    if not _dose(tokens(GLUED.sub(r"\1 \2", masked))):
        return "V-03"
    if not _numbers(masked, subject):
        return "V-04"
    sentence = RULES.instruction_sentence.model_dump()[subject.language]
    if subject.decision == "offer" and " ".join(sentence.split()) not in " ".join(text.split()):
        return "V-05"  # SV-06: not for `shop`
    if not _treatment(toks, subject):
        return "V-06"
    if not _questions(text):
        return "V-07"
    if subject.shop:
        return shop.check(text, masked, subject.decision, subject.shop, subject.customers)
    return None
