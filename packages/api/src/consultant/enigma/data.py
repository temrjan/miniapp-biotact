"""Enigma's data: the cores, the compliance rules, the net lists (spec r2 D-01…D-03) and the
shop core (spec.shop-core r4 SD-01…SD-03).

The JSON files are generated from the approved sources (DocHub, consultant-v2, cores-r1/build.py;
the shop core from spec.shop-core r4 §2.1.1). They are validated at import; an invalid file
stops the start.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATA_DIR = Path(__file__).parent / "data"
DATA_FILES = ("cores.json", "rules.json", "nets.json", "shop.json")
# Option ids of the Jev questions that are not a unit, an item or a name.
RESERVED = frozenset({"doctor", "no_product", "unclear", "none"})


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Text(Model):
    ru: str = Field(min_length=1)
    uz: str = Field(min_length=1)


class Scripts(Model):
    ru: list[str]
    uz_latn: list[str]
    uz_cyrl: list[str]

    def all(self) -> list[str]:
        return [*self.ru, *self.uz_latn, *self.uz_cyrl]


class Phrases(Scripts):
    ru: list[str] = Field(min_length=1)
    uz_latn: list[str] = Field(min_length=1)
    uz_cyrl: list[str] = Field(min_length=1)


SCRIPTS = ("ru", "uz_latn", "uz_cyrl")


class Product(Model):
    shop_id: int
    name: str = Field(min_length=1)
    form: Literal["capsule", "stick"]
    for_whom: Text


class Problem(Model):
    """A line of the instruction with what customers type for it. A line that is itself a
    diagnosis ('Гельминтозы.') has diagnoses and no everyday phrases."""

    id: str
    line: Text
    phrases: Scripts
    diagnoses: Scripts
    applies_to: list[int] | None

    @model_validator(mode="after")
    def worded(self) -> "Problem":
        for script in SCRIPTS:
            if not getattr(self.phrases, script) and not getattr(self.diagnoses, script):
                raise ValueError(f"problem {self.id}: no phrase and no diagnosis in {script}")
        return self


class Nutrient(Model):
    line: str
    names: list[str]


class Unit(Model):
    id: str
    title: Text
    products: list[Product] = Field(min_length=1)
    pair_line: Text | None
    problems: list[Problem] = Field(min_length=1)
    nutrients: list[Nutrient]
    nutrient_phrases: Scripts
    pregnancy: list[int]

    @model_validator(mode="after")
    def own_products(self) -> "Unit":
        ids = {p.shop_id for p in self.products}
        named = set(self.pregnancy).union(*(p.applies_to or [] for p in self.problems))
        if not named <= ids:
            raise ValueError(f"unit {self.id}: a product id outside the unit")
        return self


class Variant(Model):
    shop_id: int
    name: Text


class Item(Model):
    id: str
    name: Text
    products: list[Variant] = Field(min_length=1)
    what: Text
    phrases: Phrases


class Name(Model):
    id: str
    text: str = Field(min_length=1)
    shop_ids: list[int] = Field(min_length=1)


class Cores(Model):
    units: list[Unit] = Field(min_length=1)
    items: list[Item]
    names: list[Name]

    @model_validator(mode="after")
    def distinct(self) -> "Cores":
        supplements = [p.shop_id for u in self.units for p in u.products]
        products = supplements + [p.shop_id for i in self.items for p in i.products]
        twice = [i for i, n in Counter(products).items() if n > 1]
        if twice:
            raise ValueError(f"product ids used twice: {twice}")
        for ids in (
            [u.id for u in self.units],
            [i.id for i in self.items],
            [n.id for n in self.names],
        ):
            if len(set(ids)) != len(ids) or RESERVED & set(ids):
                raise ValueError("an id is repeated or reserved")
        if not {i for n in self.names for i in n.shop_ids} <= set(supplements):
            raise ValueError("a name stands for an unknown product")
        return self


class Dose(Model):
    stems: list[str]
    words: list[str]


class Emergency(Model):
    general: Text
    pregnancy: Text
    self_harm: Text


class Rules(Model):
    version: str
    wording_rules: list[str]
    offer_rules: list[str]
    phrases: dict[str, Text]
    instruction_sentence: Text
    emergency: Emergency
    opening_question: Text
    no_offer_topics: list[str]
    dose: Dose
    treatment_stems: list[str]
    question_stems: list[str]
    more_replies: list[str]
    internal_words: list[str]


class Negation(Model):
    before: list[str]
    after: list[str]
    window_before: int
    window_after: int


class Duration(Model):
    patterns: dict[str, dict[Literal["days", "weeks", "months", "years"], str]]
    words: dict[str, int]
    multipliers_days: dict[Literal["days", "weeks", "months", "years"], int]
    ordinals: dict[str, int]
    ordinal_pattern: str
    unit_first_pattern: str


class Thresholds(Model):
    temperature: float
    fever_days: int
    cold_days: int
    cough_days: int


class Row(Model):
    """One list row in the shape of graf_data/safety.json: the keys nets.py reads and the
    descriptive keys of the source."""

    id: str
    class_: str = Field(alias="class")
    sign: str
    lang: str
    match: Literal["stem", "word"]
    action: str
    terms: list[str] | None = None
    near: list[list[str]] | None = None
    window: int | None = None
    tier: Literal["strong", "weak"] | None = None
    template: str | None = None
    negation: bool = True
    not_prefix: list[str] = []
    unless_any: list[str] = []
    requires_any: list[str] = []
    requires_slot: str | None = None
    slot: str | None = None
    value: bool | None = None
    note: str | None = None
    note_template: str | None = None
    uz_uncertain: bool | None = None

    @model_validator(mode="after")
    def matchable(self) -> "Row":
        if (self.terms is None) == (self.near is None) or (self.near and self.window is None):
            raise ValueError(f"row {self.id}: terms, or near with a window")
        return self


class Nets(Model):
    """The list rows keep the shape of graf_data/safety.json, from which they are copied
    unchanged (C-34); nets.py reads them."""

    version: str
    negation: Negation
    ambulance: list[dict[str, Any]]
    doctor: list[dict[str, Any]]
    intolerance: list[dict[str, Any]]
    diarrhea: list[dict[str, Any]]
    pregnancy: list[dict[str, Any]]
    topics: dict[Literal["fever", "cold", "cough"], list[str]]
    number_words: dict[str, float]
    duration: Duration
    duration_adverbs: list[str]
    temperature: list[str]
    frequency: list[str]
    thresholds: Thresholds

    @model_validator(mode="after")
    def rows(self) -> "Nets":
        for row in self.ambulance:
            if Row.model_validate(row).tier is None or "template" not in row:
                raise ValueError(f"ambulance row {row.get('id')}: tier and template")
        for row in (*self.doctor, *self.intolerance, *self.diarrhea, *self.pregnancy):
            Row.model_validate(row)
        return self


class Approval(Model):
    by: str = Field(min_length=1)
    date: str = Field(min_length=1)


class Topic(Model):
    """A question about the shop: what Jev reads it by, and the approved answer text."""

    id: str = Field(min_length=1)
    description: Text
    text: Text
    approved: Approval | None


class Condition(Model):
    """A shop condition (cash, discount, ...): the stems that make a claim in an answer (spec r7
    CT-04 (b), widened to other word forms and scripts) and the roots by which a customer's
    message names it (spec.shop-core r4 SV-03)."""

    concept: str = Field(min_length=1)
    stems: list[str] = Field(min_length=1)
    customer_roots: list[str] = Field(min_length=1)


class Shop(Model):
    version: str
    source: str
    topic_none: str = Field(min_length=1)
    conditions: list[Condition] = Field(min_length=1)
    topics: list[Topic] = Field(min_length=1)

    @model_validator(mode="after")
    def complete(self) -> "Shop":
        ids = [t.id for t in self.topics]
        if len(set(ids)) != len(ids) or "none" in ids:
            raise ValueError("a topic id is repeated or 'none'")
        if SUPPORT not in ids:
            raise ValueError(f"no topic {SUPPORT}")
        concepts = [c.concept for c in self.conditions]
        if len(set(concepts)) != len(concepts):
            raise ValueError("a condition concept is repeated")
        return self


# The topic whose text sends the customer to support (spec.shop-core r4 SL-01).
SUPPORT = "other_shop"


def load(directory: Path = DATA_DIR) -> tuple[Cores, Rules, Nets, Shop, str]:
    """The validated data of a directory and its hash."""
    raw = [(directory / name).read_bytes() for name in DATA_FILES]
    cores, rules, nets, shop = (json.loads(body) for body in raw)
    return (
        Cores.model_validate(cores),
        Rules.model_validate(rules),
        Nets.model_validate(nets),
        Shop.model_validate(shop),
        hashlib.sha256(b"".join(raw)).hexdigest(),
    )


CORES, RULES, NETS, SHOP, DATA = load()
TOPIC = {t.id: t for t in SHOP.topics}
UNIT = {u.id: u for u in CORES.units}
ITEM = {i.id: i for i in CORES.items}
NAME = {n.id: n for n in CORES.names}
