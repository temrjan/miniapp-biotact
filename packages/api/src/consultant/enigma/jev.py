"""Enigma's one Jev request and its reading (spec r2 J-01…J-04; spec.shop-core r4 SJ-01, SJ-02).

Four Choice questions over the conversation: the unit that fits the complaint, the item the
customer wants, the supplement the current message names, and the question about the shop the
current message asks. The option texts are built from the cores and the shop core; their wording
is the one measured in M-01 and M-03. No customer text is logged.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.consultant.enigma.data import CORES, NAME, RULES, SHOP, UNIT, Item, Unit

URL = "https://openrouter.ai/api/v1/systemone"
MODEL = "typesafe/jev-1.14"
VERSION = re.compile(r"typesafe/jev-1\.14(?:-[0-9A-Za-z.]+)?")
TIMEOUT = 3.0
BOUND = 65536
SPECIAL = ("doctor", "no_product", "unclear")
NONE = "none"
CANDIDATES = 5
# SJ-02: the second shop topic is chosen from this probability (M-03 run 2).
TOPIC_SECOND = 0.13

_STATE = (
    "The state holds a conversation of a customer with an online shop of dietary supplements "
    "and household goods: 'earlier' is the customer's earlier messages, 'message' is the "
    "current one. Russian or Uzbek (Latin or Cyrillic), often with typos. "
)
_DOCTOR = (
    "A doctor is needed, not a product: chest pain, difficulty breathing, fainting or another "
    "emergency sign; blood in stool, urine or sputum; severe acute pain; a temperature above "
    "38.5; a fever for more than 3 days; a cold for more than 10 days; a cough for more than "
    "3 weeks."
)
_NO_PRODUCT = "The customer describes a health problem, and none of the listed products is for it."
_TOPIC = (
    "Which question about the shop itself (ordering, delivery, payment, returns, contacts, the "
    "company, the website or the Telegram mini app) does the CURRENT message ('message') ask? "
    "Use 'earlier' only to understand a short follow-up."
)
_UNCLEAR = (
    "The customer names no health problem, or the message is a greeting, a question about the "
    "shop, a product name only, or about a non-health product."
)


def _lines(values: list[str]) -> str:
    return " | ".join(values)


def _unit(unit: Unit) -> str:
    out = [unit.title.ru + " / " + unit.title.uz]
    for problem in unit.problems:
        out.append("PROBLEM: " + problem.line.ru + " / " + problem.line.uz)
        out.append("Customers say: " + _lines(problem.phrases.all()))
        if problem.diagnoses.all():
            out.append("Named illnesses: " + _lines(problem.diagnoses.all()))
    out.extend("COMPOSITION LINE: " + nutrient.line for nutrient in unit.nutrients)
    if unit.nutrient_phrases.all():
        out.append("Asked by nutrient: " + _lines(unit.nutrient_phrases.all()))
    return "\n".join(out)


def _item(item: Item) -> str:
    name = item.products[0].name
    return f"{name.ru} / {name.uz}. {item.what.ru} {item.what.uz} Customers say: " + _lines(
        item.phrases.all()
    )


def _questions() -> dict[str, Any]:
    units = {unit.id: _unit(unit) for unit in CORES.units}
    units["doctor"] = _DOCTOR
    units["no_product"] = " ".join([_NO_PRODUCT, *RULES.no_offer_topics])
    units["unclear"] = _UNCLEAR
    items = {item.id: _item(item) for item in CORES.items}
    items[NONE] = "The message is not about any of these goods."
    names = {name.id: name.text for name in CORES.names}
    names[NONE] = "The current message names none of these products."
    topics = {t.id: t.description.ru + " / " + t.description.uz for t in SHOP.topics}
    topics[NONE] = SHOP.topic_none
    return {
        "unit": {
            "type": "choice",
            "instructions": _STATE + "Which option fits the customer's health problem best?",
            "criteria": units,
        },
        "item": {
            "type": "choice",
            "instructions": _STATE
            + "Which of these household and lifestyle goods does the customer want or ask about?",
            "criteria": items,
        },
        "name": {
            "type": "choice",
            "instructions": _STATE
            + "Which product does the CURRENT message ('message') name, possibly misspelled or "
            "transliterated?",
            "criteria": names,
        },
        "topic": {
            "type": "choice",
            "instructions": _STATE + _TOPIC,
            "criteria": topics,
        },
    }


QUESTIONS = _questions()


def payload(earlier: list[str], message: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "state": {"earlier": "\n".join(earlier), "message": message},
        "questions": QUESTIONS,
    }


# --- reading (J-03) -------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """What one Jev answer says. `units` are candidate unit ids with their probability, best
    first; `special` holds the probabilities of doctor, no_product and unclear."""

    model: str
    units: tuple[tuple[str, float], ...]
    special: dict[str, float]
    item: tuple[str, float] | None
    name: tuple[str, float] | None
    name_only: bool
    # SJ-02: the top option of the topic question (may be 'none') and the chosen topics.
    topic: tuple[str, float]
    topics: tuple[tuple[str, float], ...]

    @property
    def named(self) -> list[int]:
        """The products of the named supplement."""
        return list(NAME[self.name[0]].shop_ids) if self.name else []


class _Choice(BaseModel):
    model_config = ConfigDict(strict=True)
    choice: str
    probabilities: dict[str, float]


class _Answers(BaseModel):
    model_config = ConfigDict(strict=True)
    unit: _Choice
    item: _Choice
    name: _Choice
    topic: _Choice


class _Body(BaseModel):
    model_config = ConfigDict(strict=True)
    model: str = Field(min_length=1)
    answers: _Answers


def _probabilities(answer: _Choice, options: dict[str, Any]) -> dict[str, float] | None:
    """The probability of every option, or None when the answer is not about these options."""
    if answer.choice not in options:
        return None
    found = {k: v for k, v in answer.probabilities.items() if k in options}
    if any(not 0 <= v <= 1 for v in found.values()):
        return None
    return {k: found.get(k, 0.0) for k in options}


def _ordered(probabilities: dict[str, float]) -> list[str]:
    """SJ-02: highest first; equal probabilities in the display order of the shop core, 'none'
    after every topic."""
    rank = {k: i for i, k in enumerate([*(t.id for t in SHOP.topics), NONE])}
    return sorted(probabilities, key=lambda k: (-probabilities[k], rank[k]))


def _topic_top(probabilities: dict[str, float]) -> tuple[str, float]:
    first = _ordered(probabilities)[0]
    return first, probabilities[first]


def _chosen(probabilities: dict[str, float]) -> tuple[tuple[str, float], ...]:
    """SJ-02: no topic when the top option is 'none'; otherwise the top topic and the next topic
    (skipping 'none') when its probability is at least TOPIC_SECOND."""
    order = _ordered(probabilities)
    if order[0] == NONE:
        return ()
    second = next((k for k in order[1:] if k != NONE), None)
    chosen = [order[0]]
    if second is not None and probabilities[second] >= TOPIC_SECOND:
        chosen.append(second)
    return tuple((k, probabilities[k]) for k in chosen)


def _unit_of(name: str) -> str:
    products = set(NAME[name].shop_ids)
    return next(u.id for u in CORES.units if products & {p.shop_id for p in u.products})


def read(value: object) -> Reading | str:
    """A reading, or the error class: 'refusal', 'invalid' or 'model'."""
    answers = value.get("answers") if isinstance(value, dict) else None
    if isinstance(answers, dict) and any(
        isinstance(a, dict) and a.get("type") == "refusal" for a in answers.values()
    ):
        return "refusal"
    try:
        body = _Body.model_validate(value)
    except ValidationError:
        return "invalid"
    if VERSION.fullmatch(body.model) is None:
        return "model"
    unit = _probabilities(body.answers.unit, QUESTIONS["unit"]["criteria"])
    item = _probabilities(body.answers.item, QUESTIONS["item"]["criteria"])
    name = _probabilities(body.answers.name, QUESTIONS["name"]["criteria"])
    topic = _probabilities(body.answers.topic, QUESTIONS["topic"]["criteria"])
    if unit is None or item is None or name is None or topic is None:
        return "invalid"
    top_unit, top_item, top_name = (
        body.answers.unit.choice,
        body.answers.item.choice,
        body.answers.name.choice,
    )
    ranked = sorted(
        ((k, p) for k, p in unit.items() if k in UNIT and p > 0), key=lambda kp: -kp[1]
    )[:CANDIDATES]
    complaint = top_unit not in ("unclear", "no_product")
    if top_name != NONE and complaint and _unit_of(top_name) not in dict(ranked):
        ranked.append((_unit_of(top_name), unit[_unit_of(top_name)]))
    return Reading(
        model=body.model,
        units=tuple(ranked),
        special={k: unit[k] for k in SPECIAL},
        item=(top_item, item[top_item]) if top_item != NONE else None,
        name=(top_name, name[top_name]) if top_name != NONE else None,
        name_only=top_name != NONE and not complaint and top_item == NONE,
        topic=_topic_top(topic),
        topics=_chosen(topic),
    )


# --- the request (J-01, J-04) ---------------------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    """One Jev request: the reading (None on an error), the error class, the latency in
    seconds and the size of the request in bytes."""

    reading: Reading | None
    error: str | None
    latency: float
    size: int


class Jev:
    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self.client, self.key = client, key

    async def ask(self, earlier: list[str], message: str) -> Attempt:
        content = json.dumps(payload(earlier, message), ensure_ascii=False).encode()
        started = time.monotonic()
        result = await self._send(content)
        latency = time.monotonic() - started
        if isinstance(result, Reading):
            return Attempt(result, None, latency, len(content))
        return Attempt(None, result, latency, len(content))

    async def _send(self, content: bytes) -> Reading | str:
        try:
            async with (
                asyncio.timeout(TIMEOUT),
                self.client.stream(
                    "POST",
                    URL,
                    content=content,
                    headers={
                        "Authorization": "Bearer " + self.key,
                        "Content-Type": "application/json",
                    },
                    timeout=TIMEOUT,
                ) as response,
            ):
                if response.status_code != 200:
                    return "status"
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > BOUND:
                        return "size"
            return read(json.loads(body))
        except (TimeoutError, httpx.TimeoutException):
            return "timeout"
        except httpx.HTTPError:
            return "transport"
        except ValueError:
            return "invalid"
