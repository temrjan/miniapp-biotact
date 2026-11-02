"""The Luna-first hybrid turn (spec r2 §5 to §10): Graf's brief, one Luna call, the merge
and overrule rules, the re-decision, the text selection and the checks. Only Luna's checked
text reaches the customer; when no checked text exists the turn ends with the failure
response (503 answer_unavailable). Nothing here logs a customer text, a Luna text or a
quote."""

import asyncio
import re
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, TypeVar

from src.consultant import luna
from src.consultant.checks import (
    Catalogue,
    Reference,
    anchors,
    customer_numbers,
    has_number,
    has_unit,
    question_reference,
    reference,
)
from src.consultant.checks import check as check_text
from src.consultant.context import FACT_TYPES, Check, Context, FlowState, Subject, digest, resolve
from src.consultant.contracts import Fault, Turn
from src.consultant.graf import (
    DURATION,
    ENTRY,
    PRODUCT,
    QUESTION_KINDS,
    QUESTION_SLOT,
    SITUATION_LEVEL,
    STATUS,
    Attempt,
    Brief,
    Facts,
    Graf,
    Hit,
    Lens,
    Outcome,
    Reply,
    Stage,
    Writer,
    flow_texts,
    hit_words,
    listing,
    norm,
    parse_age,
    repeats,
    signature,
)
from src.consultant.knowledge import Evidence
from src.consultant.record import Counters, TurnRecord

if TYPE_CHECKING:
    from src.consultant.service import Carry, Consultant

T = TypeVar("T")
ORDINAL = re.compile(r"\b(?:first|перв\w*|birinchi\w*|second|втор\w*|ikkinchi\w*)\b")
ADULT_RELATIONS = frozenset({"husband", "wife", "mother", "father"})
CHILD_RELATIONS = frozenset({"son", "daughter", "child"})
SAFETY_KINDS = ("emergency", "check")
CHECK = Outcome("check", None, "emergency.check")
GENERAL = Outcome("emergency", None, "emergency.general")
POST_OFFER = Outcome("post_offer")


class Failure(Exception):
    """The turn ends with the failure response (spec r2 FR-01) for this trigger."""

    def __init__(self, trigger: str, check: str | None = None) -> None:
        super().__init__(trigger)
        self.trigger, self.check = trigger, check


# --- routing (HR-01 steps 5 to 8) ------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    """Where a message goes: the old product path ('product'), a post-offer turn, a
    continuation, a first-message turn, or 'aside' (a version 1 flow whose start left the
    window keeps today's step aside to the product path). `page` is the page product of a
    page-product turn (PP-01)."""

    kind: str
    subject: Subject
    flow: FlowState | None = None
    page: int | None = None

    @property
    def hybrid(self) -> bool:
        return self.kind in {"post_offer", "continuation", "first"}


def route(
    request: Turn,
    selection: list[int],
    evidence: Evidence,
    owned: bool,
    flow: FlowState | None,
) -> Route:
    """HR-01 step 5: the product path for a named product, or for an ordinal or open intent
    with a displayed selection. An ordinal or open-intent word without a displayed selection
    never routes to the product path."""
    page = request.page_product_id if request.page_product_id in evidence.products else None
    subject = resolve(
        request.question, evidence.aliases, selection, page, request.context_token is None
    )
    ordinal = ORDINAL.search(request.question.casefold()) is not None
    if subject.explicit or (selection and (ordinal or subject.open_intent)):
        return Route("product", subject)
    if selection:
        return Route("post_offer", subject)
    if owned:
        if flow is None:
            return Route("aside", subject)
        return Route("continuation", subject, flow, page)
    return Route("first", subject, None, page)


# --- validated reading -------------------------------------------------------------------------


@dataclass
class Reading:
    """Luna's reading after the server's validation (MR-01, LC-07, BO-02, SF-04)."""

    sure: dict[str, float | str | None] = field(default_factory=dict)
    low: dict[str, float | str] = field(default_factory=dict)
    age_number: float | None = None
    pregnant: bool = False
    intolerance: bool = False
    situation: str | None = None
    topic_change: bool = False
    danger: tuple[str, ...] = ()
    accepted: list[Hit] = field(default_factory=list)
    objections: list[dict[str, Any]] = field(default_factory=list)
    check_reply: str | None = None
    intent: tuple[str, str] = ("not_applicable", "low")
    candidates: frozenset[float] = frozenset()
    ignored: list[str] = field(default_factory=list)


def years(number: float, unit: str) -> float:
    """MR-02 age conversion, as parse_age converts."""
    if unit == "months":
        return round(number / 12, 2)
    if unit == "weeks":
        return round(number * 7 / 365, 2)
    return number


class Validator:
    """Validates one Luna answer against the conversation and the brief's hits. A quote
    counts only when it is copied from a customer message of the conversation (MR-01); an
    objection only when its quote comes from its hit's message and holds the hit's words
    (BO-02)."""

    def __init__(
        self, customers: list[str], hits: list[Hit], whom: str | None, child_word: bool
    ) -> None:
        self.customers = customers
        self.hits = {h.id: h for h in hits}
        self.whom, self.child_word = whom, child_word
        self.normed = [norm(m) for m in customers]

    def quoted(self, quote: str, messages: list[str] | None = None) -> bool:
        q = norm(quote).strip()
        return bool(q) and any(q in m for m in (self.normed if messages is None else messages))

    def read(self, answer: luna.Answer) -> Reading:
        r = answer.reading
        out = Reading()
        f = r.facts
        for name, item in (("whom", f.whom), ("sex", f.sex)):
            if item.state == "stated":
                if item.value is not None and self.quoted(item.quote):
                    self.value(out, name, item.value, item.confidence)
                else:
                    out.ignored.append(name)
            elif item.state == "not_stated":
                self.removal(out, name, item.quote, item.confidence)
        self.age(out, f.age)
        self.duration(out, f.duration)
        temp = f.temperature
        if temp.state == "stated":
            if (
                temp.number is not None
                and 35 <= temp.number <= 42
                and self.quoted(temp.quote)
                and has_number(temp.quote, temp.number)
            ):
                self.value(out, "temperature", float(temp.number), temp.confidence)
            else:
                out.ignored.append("temperature")
        elif temp.state == "not_stated":
            self.removal(out, "temperature", temp.quote, temp.confidence)
        # Caution facts in one direction only (MR-07).
        if f.pregnant.state == "stated" and f.pregnant.value is True:
            if self.quoted(f.pregnant.quote):
                out.pregnant = True
            else:
                out.ignored.append("pregnant")
        for name, flag in (("intolerance", f.intolerance), ("topic_change", f.topic_change)):
            if flag.present:
                if self.quoted(flag.quote):
                    setattr(out, name, True)
                else:
                    out.ignored.append(name)
        if f.danger.present:
            q = norm(f.danger.quote).strip()
            holders = [
                digest(m) for m, n in zip(self.customers, self.normed, strict=True) if q and q in n
            ]
            out.danger = tuple(dict.fromkeys(holders))
            if not holders:
                out.ignored.append("danger")
        if r.situation != "none" and r.situation_confidence == "high":
            out.situation = r.situation
        candidates: set[float] = set()
        for c in r.age_candidates:
            if (
                self.quoted(c.quote)
                and has_number(c.quote, c.number)
                and (c.unit == "years" or has_unit(c.quote, c.unit, "age"))
                and 0 < years(c.number, c.unit) <= 110
            ):
                candidates.add(years(c.number, c.unit))
            else:
                out.ignored.append("age_candidate")
        out.candidates = frozenset(candidates)
        for objection in r.objections:
            hit = self.hits.get(objection.hit_id)
            if hit is None:
                out.ignored.append("objection")
                out.objections.append({"hit_id": None, "accepted": False})
                continue
            message = [self.normed[hit.index]] if hit.index < len(self.normed) else []
            valid = (
                objection.confidence == "high"
                and bool(message)
                and self.quoted(objection.quote, message)
                and hit_words(hit.source, objection.quote)
            )
            if valid and hit not in out.accepted:
                out.accepted.append(hit)
            out.objections.append(
                {"hit_id": hit.id, "kind": hit.kind, "reason": objection.reason, "accepted": valid}
            )
        reply = r.check_reply
        if (
            reply.value in {"yes", "no"}
            and self.quoted(reply.quote, [self.normed[-1]])
            and (reply.value == "yes" or reply.confidence == "high")
        ):
            out.check_reply = reply.value
        out.intent = (r.intent.value, r.intent.confidence)
        return out

    def age(self, out: Reading, age: luna.Age) -> None:
        if age.state == "not_stated":
            self.removal(out, "age", age.quote, age.confidence)
            return
        if age.state != "stated":
            return
        if not (
            age.number is not None
            and age.unit is not None
            and self.quoted(age.quote)
            and has_number(age.quote, age.number)
            and (age.unit == "years" or has_unit(age.quote, age.unit, "age"))
            and 0 < years(age.number, age.unit) <= 110
        ):
            out.ignored.append("age")
            return
        value = years(age.number, age.unit)
        whom = out.sure.get("whom", self.whom)
        # MR-06: an adult age for a child relation or a child age for an adult one is low.
        contradiction = (value >= 18 and (whom in CHILD_RELATIONS or self.child_word)) or (
            value < 18 and whom in ADULT_RELATIONS
        )
        if age.confidence == "high" and not contradiction:
            out.sure["age"], out.age_number = value, age.number
        else:
            out.low["age"] = value

    def duration(self, out: Reading, dur: luna.Duration) -> None:
        if dur.state == "not_stated":
            self.removal(out, "duration", dur.quote, dur.confidence)
            return
        if dur.state != "stated":
            return
        if (
            dur.number is not None
            and dur.unit is not None
            and dur.number > 0
            and self.quoted(dur.quote)
            and has_number(dur.quote, dur.number)
            and has_unit(dur.quote, dur.unit, "duration")
        ):
            days = float(dur.number * DURATION["multipliers_days"][dur.unit])
            self.value(out, "duration", days, dur.confidence)
        else:
            out.ignored.append("duration")

    @staticmethod
    def value(out: Reading, name: str, value: float | str, confidence: str) -> None:
        if confidence == "high":
            out.sure[name] = value
        else:
            out.low[name] = value

    def removal(self, out: Reading, name: str, quote: str, confidence: str) -> None:
        if confidence == "high" and self.quoted(quote):
            out.sure[name] = None


# --- helpers ----------------------------------------------------------------------------------


def self_harm(stage: Stage) -> bool:
    return any(ENTRY[i]["template"] == "emergency.self_harm" for i, r in stage.hits if r == "hit")


def target_of(outcome: Outcome) -> str:
    if outcome.kind in SAFETY_KINDS:
        return outcome.kind
    if outcome.kind in SITUATION_LEVEL:
        return "question"
    return "decision"


def slot_template(name: str, whom: str | None) -> str:
    """The question about one slot (MR-04)."""
    if name == "whom" or (name == "age" and whom is None):
        return "ask.whom_age"
    if name == "age":
        return "ask.age_self" if whom == "self" else "ask.age"
    return {"duration": "ask.duration", "temperature": "ask.temperature", "sex": "ask.sex"}[name]


def describe(outcome: Outcome) -> dict[str, Any]:
    """A decision as the brief names it."""
    # Graf's question about one slot is a decision with an approved text, not a
    # situation-level question: the brief names it apart so the target stays "decision".
    kind = "slot_question" if outcome.kind == "question" else outcome.kind
    info: dict[str, Any] = {"kind": kind}
    if outcome.kind == "question" and outcome.template is not None:
        info["slot"] = QUESTION_SLOT.get(outcome.template)
    if outcome.kind == "confirm":
        info["hypothesis"] = outcome.options[0]
    elif outcome.kind == "choose":
        info["options"] = list(outcome.options)
    elif outcome.kind not in SITUATION_LEVEL:
        info.update(situation=outcome.situation, template=outcome.template)
        if outcome.offer:
            info["products"] = [PRODUCT[i["product"]]["shop_name"] for i in outcome.offer]
    return info


def approved(reply: Reply) -> dict[str, list[str]]:
    """The approved facts of an approved text, listed separately (HR-09)."""
    doses, courses, notes, reasons = [], [], [], []
    for part in reply.parts:
        values = dict(part.values)
        if part.template in {"dose.line", "dose.line_no_course"}:
            doses.append(values["dose"])
            if "course" in values:
                courses.append(values["course"])
        elif part.template is not None and (
            part.template.startswith("note") or part.template == "opening.not_treat"
        ):
            notes.append(part.text)
        elif part.template is None:
            reasons.append(part.text)
    sentences = [
        p.text for p in reply.parts if {"doctor", "103"} & anchors(p.text) and p.text not in notes
    ]
    return {
        "products": [p["name"] for p in reply.products],
        "reasons": reasons,
        "dose_lines": doses,
        "course_lines": courses,
        "notes": notes,
        "doctor_or_103": sentences,
    }


# --- the turn ---------------------------------------------------------------------------------


@dataclass
class Prepared:
    """Graf's normal brief (D0) of one message, the hits listed with it, and its route."""

    route: Route
    request: Turn
    brief: Brief
    hits: list[Hit]
    skipped: int
    ambiguous: bool

    @property
    def kind(self) -> str:
        return "post_offer" if self.route.kind == "post_offer" else self.route.kind

    @property
    def question(self) -> Outcome | None:
        """The situation-level question the brief offers as the `question` target."""
        if self.route.kind == "post_offer":
            return POST_OFFER
        return self.brief.outcome if self.brief.outcome.kind in QUESTION_KINDS else None


class HybridTurn:
    """One hybrid turn: everything between the routing and the response envelope."""

    def __init__(
        self,
        consultant: "Consultant",
        request: Turn,
        context: Context,
        record: TurnRecord,
        owner: str,
        deadline: float,
        evidence: Evidence,
        carry: "Carry",
        selection: list[int],
        flow: FlowState | None,
    ) -> None:
        self.c, self.request, self.context, self.record = consultant, request, context, record
        self.owner, self.deadline, self.evidence = owner, deadline, evidence
        self.carry, self.selection, self.incoming = carry, selection, flow
        assert consultant.graf is not None
        self.graf: Graf = consultant.graf
        self.counters: Counters = consultant.counters
        self.memo: dict[str, Attempt] = {}
        self.customers = [p.question for p in request.history] + [request.question]
        self.customer_set = customer_numbers(self.customers)
        aliases = {
            evidence.products[pid]["name"]: items
            for pid, items in evidence.aliases.items()
            if pid in evidence.products
        }
        self.catalogue = Catalogue.of([p["shop_name"] for p in PRODUCT.values()], aliases)
        self.second_used = False
        # False once the turn runs the product path after a follow-up: its timeout stays 504.
        self.hybrid = True

    # -- Graf brief ----------------------------------------------------------------------------

    def run(self, work: Coroutine[Any, Any, T]) -> Awaitable[T]:
        return self.c.waiting(self.owner, work)

    async def compute(self, target: Route, request: Turn, skipped: int, lens: Lens) -> Brief:
        if target.kind == "continuation":
            assert target.flow is not None
            return await self.graf.continuation(request, target.flow, self.run, skipped, lens)
        return await self.graf.first(request, self.run, skipped, lens)

    async def prepare(self, target: Route, request: Turn, skipped: int) -> Prepared:
        brief = await self.compute(target, request, skipped, Lens(memo=self.memo))
        received = [p.question for p in request.history] + [request.question]
        start = brief.origin.start if brief.origin else len(request.history)
        if target.flow is not None:
            start = min(start, flow_texts(request, target.flow)[0])
        texts = [(i, received[i]) for i in range(start, len(received))]
        flow = target.flow
        reply = (
            len(request.history)
            if flow is not None
            and flow.template is not None
            and QUESTION_SLOT.get(flow.template) == "pregnant_or_lactating"
            else None
        )
        hits = listing(texts, brief.facts, reply=reply)
        ambiguous = any(parse_age(t)[0] == "ask" for _, t in texts)
        return Prepared(target, request, brief, hits, skipped, ambiguous)

    def render(self, request: Turn, outcome: Outcome, facts: Facts | None) -> Reply | None:
        """The approved text A of a decision; None for a situation-level question (R-Q)."""
        if outcome.kind in SITUATION_LEVEL:
            return None
        evidence = None if outcome.kind in SAFETY_KINDS else self.evidence
        return self.graf.reply(Writer(request), outcome, evidence, facts, log=False)

    # -- Luna call (LC-01, LC-04, LC-06) -------------------------------------------------------

    async def call(self, brief: dict[str, Any], second: bool = False) -> luna.Parsed:
        payload = luna.payload(brief)
        part = self.record.luna_part()
        part["model"], part["effort"] = luna.MODEL, luna.EFFORT
        await self.c.controls.alive(self.owner)
        reservation = await self.c.controls.reserve(self.owner, "responses")
        loop = asyncio.get_running_loop()
        started = loop.time()

        async def work() -> dict[str, Any] | Fault:
            # Provider faults stay inside the task, so the waiting loop raises only the
            # control faults (cancel, disabled) of this turn.
            try:
                return await self.c.provider.call("responses", payload)
            except Fault as fault:
                return fault

        latency: float | None = None
        body: dict[str, Any] | Fault | None = None
        try:
            async with asyncio.timeout(luna.TIMEOUT):
                body = await self.c.waiting(self.owner, work())
            latency = loop.time() - started
        except TimeoutError:
            body = None
        if body is None:
            parsed = luna.Parsed(None, "timeout")
        elif isinstance(body, Fault):
            failure = "invalid" if body.code == "invalid_answer" else "provider_error"
            parsed = luna.Parsed(None, failure)
        else:
            await self.c.usage(reservation, self.owner, body.get("usage", {}))
            parsed = luna.parse(body)
        self.counters.add("luna.calls")
        self.counters.add("luna.outcome." + (parsed.failure or "ok"))
        self.counters.latency(latency)
        if second:
            self.counters.add("luna.second_calls")
        text = parsed.answer.text if parsed.answer else None
        if text is not None:
            self.counters.add("luna.target." + text.target)
        part["calls"].append(
            {
                "second": second,
                "latency": latency,
                "outcome": parsed.failure or "ok",
                "target": text.target if text else None,
                "text": text.body if text else None,
                "check_id": None,
            }
        )
        return parsed

    # -- dynamic input (HR-09) -------------------------------------------------------------------

    def conversation(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, pair in enumerate(self.request.history):
            out.append({"index": i, "role": "customer", "text": pair.question})
            out.append({"role": "assistant", "text": pair.answer})
        out.append(
            {
                "index": len(self.request.history),
                "role": "customer",
                "text": self.request.question,
                "current": True,
            }
        )
        return out

    def jev(self) -> dict[str, Any]:
        note = "Graf's proposal, may be wrong"
        attempt = next(iter(self.memo.values()), None)
        if attempt is None:
            return {"note": note, "status": "not_run"}
        info: dict[str, Any] = {"note": note, "status": attempt.status}
        if attempt.choice is not None:
            second = attempt.choice.second()
            info.update(
                situation=attempt.choice.situation,
                confidence=attempt.choice.confidence,
                second={"situation": second[0], "confidence": second[1]} if second else None,
            )
        return info

    def stage_text(self, outcome: Outcome) -> str:
        reply = self.render(self.request, outcome, None)
        assert reply is not None
        return reply.answer

    def dynamic(
        self,
        prepared: Prepared | None,
        stage: Stage | None = None,
        decision: Outcome | None = None,
        second: dict[str, Any] | None = None,
        facts: Facts | None = None,
    ) -> dict[str, Any]:
        """The dynamic JSON of one call: the conversation, Graf's brief and the texts. A
        second call names D1 (`decision`) with the facts it rests on."""
        turn = "safety" if stage is not None else (prepared.kind if prepared else "first")
        brief: dict[str, Any] = {
            "language": self.request.language,
            "turn": turn,
            "conversation": self.conversation(),
            "jev": self.jev(),
        }
        texts: dict[str, str] = {"check": self.stage_text(CHECK)}
        hits: list[Hit] = []
        if stage is not None:
            hits += listing([], Facts(), stage.hits, len(self.request.history))
            brief["safety"] = {
                "level": stage.level,
                "primary": "continue" if stage.outcome is None else stage.outcome.kind,
                "check_pending": stage.level == "reply",
                "self_harm": self_harm(stage),
            }
            if stage.outcome is not None and stage.outcome.kind == "emergency":
                texts["emergency"] = self.stage_text(stage.outcome)
            if stage.level == "reply":
                texts["emergency"] = self.stage_text(GENERAL)
        if prepared is not None:
            outcome = decision or prepared.brief.outcome
            facts = facts or prepared.brief.facts
            brief["parser_facts"] = {
                **{n: getattr(facts, n) for n in FACT_TYPES if getattr(facts, n) is not None},
                "topics": sorted(facts.topics),
                "child_word": facts.child_word,
                "intolerance": facts.intolerance,
            }
            flow = prepared.route.flow
            if flow is not None:
                brief["pending"] = (
                    {"slot": QUESTION_SLOT[flow.template]}
                    if flow.pending == "slot" and flow.template is not None
                    else {"question": flow.pending, "options": list(flow.options)}
                )
            hits += prepared.hits
            brief["decision"] = describe(outcome)
            reply = (
                self.render(prepared.request, outcome, facts)
                if outcome.kind not in SAFETY_KINDS
                else None
            )
            if reply is not None:
                texts["decision"] = reply.answer
                brief["approved_facts"] = approved(reply)
            question = prepared.question if decision is None else None
            if question is not None:
                brief["question"] = describe(question)
            if prepared.route.kind == "post_offer":
                brief["shown_products"] = [
                    self.evidence.products[i]["name"] for i in self.selection
                ]
            if prepared.route.page is not None:
                brief["page_product"] = self.evidence.products[prepared.route.page]["name"]
            brief["ambiguous_age"] = prepared.ambiguous
        brief["hits"] = [
            {"id": h.id, "kind": h.kind, "sign": h.sign, "result": h.result, "message": h.index}
            for h in hits
        ]
        brief["texts"] = texts
        if second is not None:
            brief["second_call"] = second
        return brief

    # -- reading -------------------------------------------------------------------------------

    def validate(self, answer: luna.Answer, hits: list[Hit], facts: Facts | None) -> Reading:
        validator = Validator(
            self.customers,
            hits,
            facts.whom if facts else None,
            facts.child_word if facts else False,
        )
        reading = validator.read(answer)
        part = self.record.luna_part()
        part["reading"] = {**answer.reading.model_dump(), "ignored": reading.ignored}
        part["intent"] = answer.reading.intent.value
        part["objections"] = reading.objections
        self.counters.add("intent." + answer.reading.intent.value)
        for item in reading.objections:
            if not item["accepted"]:
                self.counters.add("objection.rejected")
        return reading

    # -- text selection and checks (HR-07, HR-08, §9) --------------------------------------------

    def reference(self, outcome: Outcome, reply: Reply | None, harm: bool = False) -> Reference:
        if outcome.kind in SITUATION_LEVEL or reply is None:
            return question_reference(self.request.language, self.customer_set)
        return reference(
            self.request.language,
            reply.parts,
            [p["name"] for p in reply.products],
            question=outcome.kind == "question",
            safety=outcome.kind in SAFETY_KINDS,
            customer=self.customer_set,
            self_harm=harm,
        )

    def verify(self, text: luna.Text, ref: Reference, named_age: float | None) -> str | None:
        found = check_text(text.body, ref, self.catalogue, self.request.language, named_age)
        calls = self.record.luna_part()["calls"]
        if calls:
            calls[-1]["check_id"] = found
        if found is not None:
            self.counters.add("check.failed." + found)
        return found

    async def text_for(
        self,
        outcome: Outcome,
        reply: Reply | None,
        first: luna.Text | None,
        usable: bool,
        again: Callable[[dict[str, Any]], dict[str, Any]],
        named_age: float | None = None,
        harm: bool = False,
    ) -> str:
        """Luna's first-call text when HR-07 lets it stand and it passes the checks, else a
        second call (HR-08) when time allows, else the failure response (TC-09, FR-01)."""
        ref = self.reference(outcome, reply, harm)
        target = target_of(outcome)
        failed: str | None = None
        if first is not None and usable:
            failed = self.verify(first, ref, named_age)
            if failed is None:
                return self.delivered(first, reply)
        if self.second_used:
            raise Failure("text", failed)
        if self.deadline - asyncio.get_running_loop().time() < luna.SECOND_CALL_MINIMUM:
            raise Failure("second_time", failed)
        self.second_used = True
        instruction = {
            "instruction": "Write the customer text for this decision; text.target must be "
            + target
            + ".",
            "decision": describe(outcome),
            "target": target,
        }
        parsed = await self.call(again(instruction), second=True)
        if parsed.answer is None:
            raise Failure("second_call")
        text = parsed.answer.text
        if text.target != target:
            raise Failure("second_target")
        failed = self.verify(text, ref, named_age)
        if failed is not None:
            raise Failure("text", failed)
        return self.delivered(text, reply)

    def delivered(self, text: luna.Text, reply: Reply | None) -> str:
        part = self.record.luna_part()
        equal = reply is not None and text.body == reply.answer
        part["text_target"], part["equal_to_a"] = text.target, equal
        if equal:
            self.counters.add("luna.equal_to_a")
        return text.body

    # -- outcomes ----------------------------------------------------------------------------------

    async def safety_outcome(
        self,
        outcome: Outcome,
        text: luna.Text,
        check: Check | None,
        prepared: Prepared | None,
        stage: Stage | None,
        harm: bool = False,
    ) -> dict[str, Any]:
        """A delivered 103 or danger-check text keeps the incoming flow and selection
        (TK-10); a check records the pending check (TK-06)."""
        reply = self.render(self.request, outcome, None)
        delivered = await self.text_for(
            outcome,
            reply,
            text,
            text.target == outcome.kind,
            lambda extra: self.dynamic(prepared, stage, None, extra),
            harm=harm,
        )
        self.graf.log(None, outcome.kind)
        self.record.graf_outcome, self.record.template = outcome.kind, outcome.template
        held = replace(self.carry, flow=self.incoming, check=check)
        return self.c.envelope(
            self.request,
            held,
            self.context.ids,
            STATUS[outcome.kind],
            delivered,
            [],
            [],
            None,
            [],
        )

    async def product(self, request: Turn, subject: Subject, strict: bool) -> dict[str, Any]:
        """The old product path. After a follow-up its timeout stays 504 (PO-05); on a
        safety turn a 502/504 ends the turn with the failure response (SF-02)."""
        if not strict:
            self.hybrid = False
        try:
            return await self.c.product(
                request, self.owner, self.carry, self.selection, subject, self.evidence
            )
        except Fault as fault:
            if strict and fault.status in {502, 504}:
                raise Failure("product_path") from None
            raise

    def lens(self, reading: Reading, values: dict[str, float | str | None]) -> Lens:
        pairs = tuple(
            (h.source, digest(self.customers[h.index]))
            for h in reading.accepted
            if h.kind == "K2" or h.kind.startswith("W-")
        )
        return Lens(
            values=dict(values),
            pregnant=reading.pregnant,
            intolerance=reading.intolerance,
            situation=reading.situation,
            topic_change=reading.topic_change,
            inactive=pairs,
            read=True,
            memo=self.memo,
        )

    async def redecide(self, prepared: Prepared, reading: Reading) -> tuple[Brief, Lens]:
        """D1: Graf's decision again with Luna's sure values, accepted objections and
        situation (§7); a low value that would change it gives a question (MR-04)."""
        lens = self.lens(reading, reading.sure)
        target, request = prepared.route, prepared.request
        brief = await self.compute(target, request, prepared.skipped, lens)
        before = prepared.brief.facts
        for name, value in reading.low.items():
            if name in reading.sure or getattr(before, name) == value:
                continue
            trial = self.lens(reading, {**reading.sure, name: value})
            other = await self.compute(target, request, prepared.skipped, trial)
            if signature(other.outcome) == signature(brief.outcome):
                continue
            slot = "age" if name == "whom" else name
            current = brief.outcome
            if current.kind == "question" and QUESTION_SLOT.get(current.template or "") == slot:
                break  # already a question about that slot (HR-05: the re-ask stays)
            situation = brief.outcome.situation
            if brief.origin is None or situation is None:
                break
            template = slot_template(name, brief.facts.whom)
            if repeats(brief.origin.asked, template) >= 2:
                outcome = Outcome("no_product", situation, "no_product.general")
            else:
                outcome = Outcome("question", situation, template)
            brief = self.graf.brief(request, outcome, brief.facts, brief.origin)
            break
        return brief, lens

    def count_overrules(self, prepared: Prepared, reading: Reading, final: Outcome) -> None:
        for hit in reading.accepted:
            if hit.kind.startswith("W-"):
                self.counters.add("overrule.W")
        before = prepared.brief.outcome
        if before.kind == "doctor" and final.kind != "doctor":
            k2 = any(h.kind == "K2" for h in reading.accepted)
            self.counters.add("overrule.K2" if k2 else "overrule.K1")

    async def conclude(
        self, prepared: Prepared | None, reading: Reading, text: luna.Text, stage: Stage | None
    ) -> dict[str, Any]:
        """The normal outcome of a message: SF-05, post-offer (PO-03), page product (PP-03),
        then the re-decision and its text."""
        danger = tuple(d for d in reading.danger if d not in self.carry.cleared)
        if danger:
            # SF-05: a danger quote raises the check, never 103.
            asked = digest(self.request.question)
            return await self.safety_outcome(CHECK, text, Check(asked, danger), prepared, stage)
        if prepared is None:
            raise AssertionError("a product-path message is concluded by the caller")
        target = prepared.route
        current = len(prepared.request.history)
        in_force = [
            h
            for h in prepared.hits
            if h.kind == "K2" and h.index == current and h not in reading.accepted
        ]
        intent, confidence = reading.intent
        strict = stage is not None
        if target.kind == "post_offer" and not in_force:
            if intent == "follow_up" and confidence == "high":
                return await self.product(prepared.request, target.subject, strict)
            if not (intent == "new_complaint" and confidence == "high"):
                return await self.post_offer(prepared, text, stage)
        if (
            target.page is not None
            and not in_force
            and (intent, confidence)
            == (
                "follow_up",
                "high",
            )
        ):
            subject = Subject([target.page], False, False, target.subject.open_intent)
            return await self.product(prepared.request, subject, strict)
        brief, lens = await self.redecide(prepared, reading)
        final = brief.outcome
        self.count_overrules(prepared, reading, final)
        reply = self.render(prepared.request, final, brief.facts)
        before = self.render(prepared.request, prepared.brief.outcome, prepared.brief.facts)
        question = prepared.question
        # HR-07 (a): the same decision, including the approved text it renders (a changed
        # relation word makes another text, so Luna writes again).
        usable = (
            text.target == "decision"
            and final.kind not in SITUATION_LEVEL
            and signature(final) == signature(prepared.brief.outcome)
            and reply is not None
            and before is not None
            and reply.answer == before.answer
        ) or (
            text.target == "question"
            and question is not None
            and final.kind in QUESTION_KINDS
            and signature(final) == signature(question)
        )
        ambiguous = prepared.ambiguous or len(reading.candidates) >= 2
        named = (
            reading.age_number
            if ambiguous
            and brief.facts.origin.get("age") == "luna"
            and final.kind not in {"question", *SITUATION_LEVEL}
            else None
        )
        self.record_decision(final, brief, lens, reply)
        delivered = await self.text_for(
            final,
            reply,
            text,
            usable,
            lambda extra: self.dynamic(prepared, stage, final, extra, brief.facts),
            named,
        )
        facts = brief.facts
        pg = self.carry.pg or (
            facts.pregnant is True and facts.origin.get("pregnant") in {"confirmed", "luna"}
        )
        carry = replace(self.carry, pg=pg, flow=brief.flow, check=None)
        if reply is None:
            return self.c.envelope(
                self.request, carry, [], "clarification", delivered, [], [], None, []
            )
        return self.c.envelope(
            self.request,
            carry,
            reply.ids,
            reply.status,
            delivered,
            reply.sources,
            reply.products,
            None,
            reply.cautions,
        )

    async def post_offer(
        self, prepared: Prepared, text: luna.Text, stage: Stage | None
    ) -> dict[str, Any]:
        """The post-offer question keeps the displayed selection and its cards (PO-04)."""
        delivered = await self.text_for(
            POST_OFFER,
            None,
            text,
            text.target == "question",
            lambda extra: self.dynamic(prepared, stage, POST_OFFER, extra),
        )
        self.record.graf_outcome = "post_offer"
        cards = [self.evidence.products[i] for i in self.selection]
        carry = replace(self.carry, flow=None, check=None)
        return self.c.envelope(
            self.request, carry, self.selection, "clarification", delivered, [], cards, None, []
        )

    def record_decision(
        self, final: Outcome, brief: Brief, lens: Lens, reply: Reply | None
    ) -> None:
        self.graf.log(final.situation, final.kind)
        record = self.record
        record.graf_outcome, record.situation = final.kind, final.situation
        record.template = final.template
        record.offered_product_ids = list(reply.ids) if reply is not None else []
        record.facts = {
            name: [value, brief.facts.origin.get(name, "parser")]
            for name in FACT_TYPES
            if (value := getattr(brief.facts, name)) is not None
        }
        self.record.luna_part()["agreement"] = lens.agreement
        if lens.resolution is not None:
            record.resolution = {"kind": lens.resolution[0], "situation": lens.resolution[1]}
        if final.kind in {"confirm", "choose"}:
            record.pending_question = {
                "kind": final.kind,
                "options": list(final.options),
                "repeat": final.kind == "choose" and lens.repeat,
            }

    # -- entry points ------------------------------------------------------------------------------

    async def normal(self, target: Route) -> dict[str, Any]:
        """A first-message, continuation or post-offer turn (HR-04, HR-05, PO, PP)."""
        prepared = await self.prepare(target, self.request, 0)
        parsed = await self.call(self.dynamic(prepared))
        if parsed.answer is None:
            raise Failure("luna")
        reading = self.validate(parsed.answer, prepared.hits, prepared.brief.facts)
        return await self.conclude(prepared, reading, parsed.answer.text, None)

    async def safety(
        self, stage: Stage, normal: Turn | None, target: Route | None
    ) -> dict[str, Any]:
        """A safety turn (§8.1): one Luna call with the check text, the 103 text of the
        primary outcome (always emergency.general on a check reply) and the normal brief
        of the message when its route is Graf's; the final outcome follows §7.2."""
        prepared = None
        if normal is not None and target is not None and target.hybrid:
            prepared = await self.prepare(target, normal, 1 if stage.level == "reply" else 0)
        parsed = await self.call(self.dynamic(prepared, stage))
        if parsed.answer is None:
            raise Failure("luna")
        k3 = listing([], Facts(), stage.hits, len(self.request.history))
        hits = k3 + (prepared.hits if prepared else [])
        reading = self.validate(parsed.answer, hits, prepared.brief.facts if prepared else None)
        text = parsed.answer.text
        current = digest(self.request.question)
        if stage.level == "strong":
            assert stage.outcome is not None
            strong = [h for h in k3 if h.kind == "K3-strong" and h.result == "hit"]
            if strong and all(h in reading.accepted for h in strong):
                # BO-07: a strong hit goes down one level only, to the danger check.
                self.counters.add("overrule.K3-strong")
                check = Check(current, (current,))
                harm = self_harm(stage)
                return await self.safety_outcome(CHECK, text, check, prepared, stage, harm)
            return await self.safety_outcome(stage.outcome, text, None, prepared, stage)
        if stage.level == "weak":
            if not (k3 and all(h in reading.accepted for h in k3)):
                return await self.safety_outcome(CHECK, text, stage.check, prepared, stage)
            # BO-06: dismissed; the message is processed normally and cleared (TK-07).
            self.counters.add("overrule.K3-weak")
            self.carry = replace(
                self.carry, cleared=tuple(dict.fromkeys((*self.carry.cleared, current)))
            )
        else:
            proceed = stage.outcome is None
            if stage.checked is None or reading.check_reply == "yes":
                proceed = False
            elif reading.check_reply == "no":
                if not proceed:
                    self.counters.add("overrule.K3-reply")
                proceed = True
            if not proceed:
                return await self.safety_outcome(GENERAL, text, None, prepared, stage)
            cleared = (*self.carry.cleared, *stage.cleared)
            self.carry = replace(self.carry, cleared=tuple(dict.fromkeys(cleared)))
        assert normal is not None
        assert target is not None
        if prepared is None:
            danger = tuple(d for d in reading.danger if d not in self.carry.cleared)
            if danger:
                return await self.safety_outcome(
                    CHECK, text, Check(current, danger), prepared, stage
                )
            return await self.product(normal, target.subject, True)
        return await self.conclude(prepared, reading, text, stage)

    def finish(self) -> None:
        """Record Jev (spec r2 OP-03; TR-03)."""
        attempt = next(iter(self.memo.values()), None)
        if attempt is None:
            return
        choice = attempt.choice
        top = (
            sorted(choice.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:3]
            if choice
            else []
        )
        self.record.jev = {
            "model": choice.model if choice else None,
            "choice": choice.situation if choice else None,
            "confidence": choice.confidence if choice else None,
            "top3": [[s, p] for s, p in top],
            "latency": attempt.latency,
            "error": attempt.error or (None if attempt.status == "ok" else attempt.status),
            "input_is_question": attempt.text == self.record.question,
        }


__all__ = ["Failure", "HybridTurn", "Reading", "Route", "Validator", "route"]
