"""The shop core in a turn: the texts a brief gets and the checks of a text written from them
(spec.shop-core r4 SD-04, SL-01, SV-01…SV-03).

The value, hour, handle, address and multiplier patterns have the meaning of today's conditions
checks (spec r7 CT-02…CT-05); this is Enigma's own copy (spec.shop-core r4 §1, C-65).
"""

import re
from collections.abc import Iterable, Sequence

from src.consultant.context import SENSITIVE
from src.consultant.contracts import Language
from src.consultant.enigma.data import SHOP, SUPPORT, TOPIC
from src.consultant.enigma.nets import tokens

# CT-02: a value is a maximal run of digit groups (joined by ':', '.' or ',') separated by
# white space inside one line; each white-space run reads as one ASCII space.
VALUE = re.compile(r"\d+(?:[:.,]\d+)*(?:[^\S\n]+\d+(?:[:.,]\d+)*)*")
SPACES = re.compile(r"\s+")
HOUR = re.compile(r"(\d{1,2}):\d\d")
MULTIPLIERS = ("тыс", "млн", "миллион", "млрд", "ming", "минг", "mln", "million", "mlrd")
HANDLE = re.compile(r"@\w+")
# CT-05: an address with or without a scheme ('www.', 'site.uz/...', 'site.uz').
ADDRESS = re.compile(
    r"(?i:\bwww\.[^\s,;]*)"
    r"|(?i:\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[a-z]{2,}/[^\s,;]*)"
    r"|\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:[a-z]{2,}|[A-Z]{2,})\b"
)
# SV-07: the stems V-06 skips in a shop answer (a warranty question is a shop question), except
# their verb forms, which promise a result ("гарантирует", "kafolatlaydi").
WARRANTY = ("гарант", "kafolat")
GUARANTEE_VERBS = ("гарантир", "kafolatla")


def warranty_word(word: str) -> bool:
    return word.startswith(WARRANTY) and not word.startswith(GUARANTEE_VERBS)


def values(text: str) -> list[str]:
    """CT-02: the values of a text, each white-space run read as one space."""
    return [SPACES.sub(" ", m[0]) for m in VALUE.finditer(text)]


def mask(text: str, masked: Iterable[str]) -> str:
    """Every value of the text that is one of `masked` replaced by one space (CT-05)."""
    hidden = frozenset(masked)
    if not hidden:
        return text
    return VALUE.sub(lambda m: " " if SPACES.sub(" ", m[0]) in hidden else m[0], text)


def public_values(texts: Iterable[str]) -> frozenset[str]:
    """The values of the texts that SENSITIVE matches (the shop's phone number)."""
    return frozenset(v for t in texts for v in values(t) if SENSITIVE.search(v))


def handles(text: str) -> set[str]:
    return {h.lower() for h in HANDLE.findall(text)}


def addresses(text: str) -> set[str]:
    return {m[0].lower().rstrip(".") for m in ADDRESS.finditer(text)}


def claims(text: str) -> set[str]:
    """The condition concepts a text makes a claim of, by the CT-04 stems."""
    words = tokens(text)
    return {
        c.concept
        for c in SHOP.conditions
        if any(w.startswith(stem) for w in words for stem in c.stems)
    }


def named_conditions(texts: Iterable[str]) -> set[str]:
    """The condition concepts the customer's messages name, in any script."""
    words = [w for text in texts for w in tokens(text)]
    return {
        c.concept
        for c in SHOP.conditions
        if any(w.startswith(root) for w in words for root in c.customer_roots)
    }


def _texts(language: Language, ids: Iterable[str]) -> list[str]:
    return [getattr(TOPIC[i].text, language) for i in ids]


# SD-04: the values the history filter masks, from every topic in both languages.
PUBLIC_CONTACTS = public_values(text for t in SHOP.topics for text in (t.text.ru, t.text.uz))
# The phone and the handle of the support text: a text that holds both is a support referral.
SUPPORT_VALUES = public_values([TOPIC[SUPPORT].text.ru])
SUPPORT_HANDLES = frozenset(handles(TOPIC[SUPPORT].text.ru))


def brief(language: Language, topics: Sequence[str]) -> dict[str, object]:
    """SL-01: the shop part of a brief."""
    return {
        "topics": [
            {"id": i, "text": t} for i, t in zip(topics, _texts(language, topics), strict=True)
        ],
        "support": getattr(TOPIC[SUPPORT].text, language),
    }


def reference(language: Language, topics: Sequence[str]) -> tuple[str, ...]:
    """`A` of §2.5: the chosen topics' texts and the support text."""
    return tuple(_texts(language, [*topics, SUPPORT]))


def referral(text: str) -> bool:
    return set(values(text)) >= SUPPORT_VALUES and handles(text) >= SUPPORT_HANDLES


def check(
    text: str, masked: str, decision: str, reference: Sequence[str], customers: Sequence[str]
) -> str | None:
    """SV-01…SV-03 on a text whose brief held `shop`; `masked` is the text with product names
    masked. `doctor` and `emergency` get none of them (their SENSITIVE masking is in V-01)."""
    if decision in ("doctor", "emergency"):
        return None
    joined = "\n".join(reference)
    if decision == "shop":
        allowed = set(values(joined))
        hours = {m[1] for v in allowed if (m := HOUR.fullmatch(v))}
        if any(v not in allowed and v not in hours for v in values(masked)):
            return "SV-01"
        if any(w.startswith(MULTIPLIERS) for w in tokens(masked)):
            return "SV-01"
    found = handles(text)
    if found - handles(joined) or addresses(text) - addresses(joined):
        return "SV-02"
    if (
        decision == "shop"
        and (set(values(text)) & SUPPORT_VALUES or found & SUPPORT_HANDLES)
        and not referral(text)
    ):
        return "SV-02"
    extra = claims(text) - claims(joined)
    if extra and referral(text):
        extra -= named_conditions(customers)
    if extra:
        return "SV-03"
    return None
