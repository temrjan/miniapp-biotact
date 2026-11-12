"""Customer product search across scripts, spellings and fields.

The query and every searched text become one search key: lower case, Russian and
Uzbek Cyrillic written in Latin letters, a few spellings unified (``c`` and ``q``
as ``k``, ``x`` as ``ks``, ``w`` as ``v``), and everything except Latin letters and
digits removed (spaces, hyphens, ``®``, apostrophes). A product matches when the
query key occurs in the key of its name, description or category name, in either
language. So «иммуно» finds IMMUNOCOMPLEX, «neuro complex» finds NEUROCOMPLEX.

The same tables build the key in Python (the query) and in PostgreSQL (the
columns), in the same order: lower case, multi-letter, single-letter, ``x``, strip.
"""

import re

from sqlalchemy import ColumnElement, func
from sqlalchemy.orm import QueryableAttribute

# Cyrillic letters written with two or more Latin letters. Both cases are listed so
# the result does not depend on the database locale of lower().
_MULTI: tuple[tuple[str, str], ...] = tuple(
    (cyrillic, latin)
    for lower, latin in (
        ("щ", "sh"),
        ("ш", "sh"),
        ("ч", "ch"),
        ("ж", "zh"),
        ("ю", "yu"),
        ("я", "ya"),
        ("ё", "yo"),
    )
    for cyrillic in (lower, lower.upper())
)
_SINGLE_LOWER = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "z",
    "ы": "y",
    "э": "e",
    # Uzbek Cyrillic.
    "ў": "o",
    "қ": "k",
    "ғ": "g",
    "ҳ": "h",
}
# Latin spellings unified after transliteration (k from Cyrillic and c/q from Latin).
_LATIN = {"c": "k", "q": "k", "w": "v"}
_SINGLE = {
    **_SINGLE_LOWER,
    **{c.upper(): latin for c, latin in _SINGLE_LOWER.items()},
    **_LATIN,
}
_FROM = "".join(_SINGLE)
_TO = "".join(_SINGLE.values())
_TABLE = str.maketrans(_SINGLE)
_STRIP = "[^a-z0-9]"


def search_key(text: str) -> str:
    """The search key of a query; empty when it has no letter or digit."""
    value = text.lower()
    for cyrillic, latin in _MULTI:
        value = value.replace(cyrillic, latin)
    value = value.translate(_TABLE).replace("x", "ks")
    return re.sub(_STRIP, "", value)


def column_key(
    column: QueryableAttribute[str] | QueryableAttribute[str | None],
) -> ColumnElement[str]:
    """The same key computed by PostgreSQL for a text column (NULL stays NULL)."""
    value: ColumnElement[str] = func.lower(column)
    for cyrillic, latin in _MULTI:
        value = func.replace(value, cyrillic, latin)
    value = func.replace(func.translate(value, _FROM, _TO), "x", "ks")
    return func.regexp_replace(value, _STRIP, "", "g")
