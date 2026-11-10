"""Enigma's hard rules on the candidates (spec r2 FT-04; compliance r2 §2.1, §2.2).

They run on Jev's reading before the brief for Luna is built. What they remove, Luna never
sees as a candidate, and the text check refuses a text that names it.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from src.consultant.enigma.data import ITEM, UNIT, Item, Problem, Product, Text, Unit
from src.consultant.enigma.jev import Reading
from src.consultant.enigma.nets import Signs

# The unit that is not offered with diarrhea (compliance §2.1, P-6).
DIARRHEA_EXCLUDES = "magniy"


@dataclass(frozen=True)
class Candidate:
    """A unit as Luna may offer it: its products, problem lines and pair line after the rules."""

    unit: Unit
    probability: float
    products: tuple[Product, ...]
    problems: tuple[Problem, ...]
    pair_line: Text | None


@dataclass(frozen=True)
class Candidates:
    units: tuple[Candidate, ...]
    item: Item | None
    intolerance: bool
    diarrhea: bool
    pregnancy: bool


def candidates(reading: Reading | None, signs: Iterable[Signs]) -> Candidates:
    """The candidates of a turn; `signs` are the net results of every customer message."""
    seen = list(signs)
    intolerance = any(s.intolerance for s in seen)
    diarrhea = any(s.diarrhea for s in seen)
    pregnancy = any(s.pregnancy for s in seen)
    if reading is None or intolerance:
        return Candidates((), None, intolerance, diarrhea, pregnancy)
    units: list[Candidate] = []
    for unit_id, probability in reading.units:
        unit = UNIT[unit_id]
        if diarrhea and unit.id == DIARRHEA_EXCLUDES:
            continue
        products = tuple(p for p in unit.products if not pregnancy or p.shop_id in unit.pregnancy)
        if not products:
            continue
        left = {p.shop_id for p in products}
        units.append(
            Candidate(
                unit,
                probability,
                products,
                tuple(p for p in unit.problems if p.applies_to is None or left & set(p.applies_to)),
                unit.pair_line if len(products) >= 2 else None,
            )
        )
    item = ITEM[reading.item[0]] if reading.item else None
    return Candidates(tuple(units), item, intolerance, diarrhea, pregnancy)
