"""One turn: admission, current subject, read-only evidence, validated delivery."""

import asyncio
import json
import logging
import re
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import ValidationError

from src.consultant.context import (
    SENSITIVE,
    Check,
    Context,
    FlowState,
    Subject,
    Tokens,
    pure_open,
    resolve,
)
from src.consultant.contracts import ContextArgs, Fault, Proposal, Turn
from src.consultant.control import Controls
from src.consultant.enigma.cards import ITEM_PRODUCTS
from src.consultant.enigma.data import DATA as ENIGMA_DATA
from src.consultant.enigma.nets import Ambulance
from src.consultant.enigma.shop import PUBLIC_CONTACTS, mask
from src.consultant.enigma.turn import Enigma, EnigmaTurn, Failure, Named
from src.consultant.knowledge import ADVICE, Evidence, Knowledge
from src.consultant.provider import PROFILE, Provider
from src.consultant.record import Counters, Recorder, TurnRecord, noop

logger = logging.getLogger(__name__)

T = TypeVar("T")
# The turn deadline in seconds (spec r2 §10).
DEADLINE = 60
CLARIFY = {
    "ru": "О каком товаре вы спрашиваете? Назовите его или откройте карточку товара.",
    "uz": "Qaysi mahsulot haqida soʻrayapsiz? Nomini yozing yoki mahsulot kartasini oching.",
}
LIMITED = {
    "ru": "Не удалось уверенно ответить. Что уточнить сначала: цену, наличие или состав товара?",
    "uz": "Ishonch bilan javob bera olmadim. Avval nimani aniqlashtiraylik: mahsulotning narxinimi, mavjudliginimi yoki tarkibinimi?",
}
REFUSED = {
    "ru": "Я не назначаю индивидуальное лечение. Могу объяснить инструкцию к товару; для личной рекомендации обратитесь к врачу.",
    "uz": "Shaxsiy davolash buyurmayman. Mahsulot yoʻriqnomasini tushuntirishim mumkin; shaxsiy tavsiya uchun shifokorga murojaat qiling.",
}
PERSONAL = re.compile(
    r"(?:лечи|вылеч|назначь|диагноз|davolab|tashxis|davolash buyur)|(?:скрой|убери|yashir).{0,30}(?:предупрежд|противопоказ|ogohlantir)",
    re.I,
)
# The product path's own words of Russian prose in an Uzbek answer (moved unchanged from the
# former checks.py, graf-removal spec r3 G4-04).
RUSSIAN_PROSE = re.compile(
    r"\b(?:это|для|или|при|также|можно|нельзя|следует|состав|указан[аоы]?|"
    r"содержит|детей|взрослых|инструкци[яию]|индивидуальная|непереносимость)\b",
    re.I,
)
ORDINAL = re.compile(r"\b(?:first|перв\w*|birinchi\w*|second|втор\w*|ikkinchi\w*)\b")


def product_subject(request: Turn, selection: list[int], evidence: Evidence) -> Subject | None:
    """The subject of the named-product path, or None (graf-removal spec r3 G4-04): a named
    product, or an ordinal or open-intent word with a displayed selection. An ordinal or
    open-intent word without a displayed selection never routes to the product path."""
    page = request.page_product_id if request.page_product_id in evidence.products else None
    subject = resolve(
        request.question, evidence.aliases, selection, page, request.context_token is None
    )
    ordinal = ORDINAL.search(request.question.casefold()) is not None
    if subject.explicit or (selection and (ordinal or subject.open_intent)):
        return subject
    return None


@dataclass(frozen=True)
class Carry:
    """What the response token carries besides the selection (spec r2 TK-10): the epoch, the
    pregnancy flag, the cleared-message digests, the flow state and a pending check."""

    epoch: str
    pg: bool = False
    cleared: tuple[str, ...] = ()
    flow: FlowState | None = None
    check: Check | None = None


class Consultant:
    def __init__(
        self,
        controls: Controls,
        knowledge: Knowledge,
        provider: Provider,
        tokens: Tokens,
        enigma: Enigma | None = None,
    ) -> None:
        self.controls, self.knowledge, self.provider, self.tokens = (
            controls,
            knowledge,
            provider,
            tokens,
        )
        # graf-removal spec r3 G4-03: the one engine; None (no Jev key) fails every turn.
        self.enigma = enigma
        self.counters = Counters()
        # spec r2 OP-03: called once per turn after the SENSITIVE filter (PR-5 replaces it).
        self.recorder: Recorder = noop

    async def waiting(self, owner: str, work: Coroutine[Any, Any, T]) -> T:
        task = asyncio.create_task(work)
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.25)
                await self.controls.alive(owner)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def usage(
        self, reservation: str, owner: str, usage: dict[str, Any], embedding: bool = False
    ) -> None:
        inputs = usage.get("total_tokens" if embedding else "input_tokens")
        outputs = 0 if embedding else usage.get("output_tokens")
        if type(inputs) is int and type(outputs) is int:
            await self.controls.reconcile(reservation, owner, inputs, outputs)

    async def turn(self, request: Turn) -> dict[str, Any]:
        # spec r7 CT-06: the shop's approved public contacts in an earlier answer are masked.
        if SENSITIVE.search(request.question) or any(
            SENSITIVE.search(p.question + " " + mask(p.answer, PUBLIC_CONTACTS))
            for p in request.history
        ):
            raise Fault("sensitive_input", 422)
        record = TurnRecord.start(request)
        try:
            response = await self.answer(request, record)
        except Fault as fault:
            record.fail(fault)
            raise
        except Exception:
            # The router answers every other exception with knowledge_unavailable.
            record.fail(Fault("knowledge_unavailable"))
            raise
        else:
            record.deliver(response)
            return response
        finally:
            self.counters.add("turns." + record.path)
            try:
                self.recorder(record)
            except Exception:
                logger.warning("consultant recorder failed")

    def failure(self, record: TurnRecord, trigger: str, check: str | None = None) -> Fault:
        """The failure response (spec r2 FR-02): 503 answer_unavailable, no text, no token."""
        record.path = "failure"
        if record.luna is not None:
            record.luna["failure_trigger"] = trigger
        self.counters.add("failure." + trigger)
        if check is not None:
            self.counters.add("failure.check." + check)
        return Fault("answer_unavailable", 503)

    async def answer(self, request: Turn, record: TurnRecord) -> dict[str, Any]:
        """Token, the no-engine response, the ambulance list, admission, snapshot, then the
        route (graf-removal spec r3 G4-03, G4-05)."""
        context = self.tokens.read(request) if request.context_token else Context.fresh()
        record.token_epoch = context.epoch
        record.versions = {"knowledge": None, "profile": PROFILE}
        if self.enigma is None:
            # G4-03 (C-75): without the Jev key every turn gets the failure response, before
            # admission and without any model call.
            raise self.failure(record, "no_engine")
        record.versions.update(engine="enigma", enigma_data=ENIGMA_DATA)
        selection = context.ids
        # Every issued token carries the pregnancy flag and the cleared digests.
        carry = Carry(context.epoch, context.pg, context.cleared)
        # consultant-v2 SW-02: Enigma's ambulance list on the current message.
        ambulance = self.enigma.ambulance(request, context)
        owner = await self.controls.register(request.request_id, request.tab_id)
        deadline = asyncio.get_running_loop().time() + DEADLINE
        try:
            async with asyncio.timeout_at(deadline):
                evidence = await self.knowledge.snapshot(request.language)
                record.versions["knowledge"] = evidence.release
                if any(i not in evidence.products and i not in ITEM_PRODUCTS for i in selection):
                    selection = []
                response = await self.enigma_routed(
                    request, record, owner, deadline, evidence, carry, selection, ambulance
                )
                await self.controls.alive(owner)
                return response
        except Failure as failure:
            raise self.failure(record, failure.trigger, failure.check) from None
        except TimeoutError:
            # FR-01: the deadline on an Enigma turn is the failure response; the product path
            # and a turn without a route yet keep their 504 (PO-05).
            if self.enigma_turn(record):
                raise self.failure(record, "deadline") from None
            raise Fault("timeout", 504) from None
        except Fault as fault:
            # The same 60 s deadline seen by the control layer.
            if fault.code == "timeout" and self.enigma_turn(record):
                raise self.failure(record, "deadline") from None
            raise
        finally:
            with suppress(TimeoutError):
                async with asyncio.timeout_at(deadline):
                    await self.controls.finish(owner)

    @staticmethod
    def enigma_turn(record: TurnRecord) -> bool:
        """The turn has a route other than the named-product path."""
        return record.path not in ("failure", "product")

    async def enigma_routed(
        self,
        request: Turn,
        record: TurnRecord,
        owner: str,
        deadline: float,
        evidence: Evidence,
        carry: Carry,
        selection: list[int],
        ambulance: Ambulance | None,
    ) -> dict[str, Any]:
        """consultant-v2 spec r2 SW-03 as amended by spec.shop-core r4 SR-01: an ambulance hit
        is an Enigma turn; a named product keeps the product path; everything else, how-to-buy
        and shop conditions included, is an Enigma turn. The product path sees only the
        selection's supplements."""
        assert self.enigma is not None
        known = [i for i in selection if i in evidence.products]
        if ambulance is None:
            subject = product_subject(request, selection, evidence)
            # A reply about a shown item card ('первый') stays with Enigma.
            if subject is not None and all(i in evidence.products for i in subject.ids):
                record.path = "product"
                return await self.product(request, owner, carry, known, subject, evidence)
        turn = EnigmaTurn(
            self,
            self.enigma,
            request,
            record,
            owner,
            deadline,
            evidence,
            carry,
            selection,
            ambulance,
        )
        try:
            return await turn.run()
        except Named as named:
            # SW-04: only a supplement is named; the named-product path answers.
            record.path = "product"
            subject = Subject(named.ids, True, False, False)
            return await self.product(request, owner, carry, known, subject, evidence)

    async def product(
        self,
        request: Turn,
        owner: str,
        carry: Carry,
        selection: list[int],
        subject: Subject,
        evidence: Evidence,
    ) -> dict[str, Any]:
        """The named-product path, unchanged: clarification, limited, refused, open, or
        the model answer over the candidates."""
        if subject.clarify:
            return self.envelope(
                request, carry, [], "clarification", CLARIFY[request.language], [], [], None, []
            )
        if any(i not in evidence.products for i in subject.ids):
            return self.envelope(
                request, carry, [], "limited", LIMITED[request.language], [], [], None, []
            )
        if PERSONAL.search(request.question):
            return self.envelope(
                request, carry, [], "refused", REFUSED[request.language], [], [], None, []
            )
        if (
            subject.open_intent
            and len(subject.ids) == 1
            and pure_open(request.question, evidence.aliases[subject.ids[0]])
        ):
            fresh = await self.knowledge.snapshot(request.language)
            if fresh.release != evidence.release or any(
                i not in fresh.products for i in subject.ids
            ):
                return self.envelope(
                    request, carry, [], "limited", LIMITED[request.language], [], [], None, []
                )
            products = [fresh.products[i] for i in subject.ids]
            labels = fresh.labels(subject.ids, request.language)
            if labels is None:
                return self.envelope(
                    request,
                    carry,
                    [],
                    "clarification",
                    CLARIFY[request.language],
                    [],
                    [],
                    None,
                    [],
                )
            return self.native(request, carry, subject, products, labels)
        return await self.generate(request, owner, carry, selection, subject, evidence)

    def native(
        self,
        request: Turn,
        carry: Carry,
        subject: Subject,
        products: list[dict[str, Any]],
        labels: list[str],
    ) -> dict[str, Any]:
        answer = ("Открываю " if request.language == "ru" else "Ochilmoqda: ") + labels[0] + "."
        return self.envelope(
            request,
            carry,
            subject.ids,
            "answer",
            answer,
            [],
            products,
            subject.ids[0] if subject.open_intent and len(subject.ids) == 1 else None,
            [],
        )

    @staticmethod
    def prices(request: Turn, products: list[dict[str, Any]], labels: list[str]) -> str:
        return "\n".join(
            f"{label}: {p['sale_price'] if p['is_sale'] else p['price']} {p['currency']} — "
            + (
                ("в наличии" if p["is_in_stock"] else "нет в наличии")
                if request.language == "ru"
                else ("mavjud" if p["is_in_stock"] else "mavjud emas")
            )
            for p, label in zip(products, labels, strict=True)
        )

    @staticmethod
    def guard(request: Turn, answer: str, forms: set[str], allowed_numbers: set[str]) -> None:
        if (forms == {"capsule"} and re.search(r"стик|stik|stick", answer, re.I)) or (
            forms == {"stick"} and re.search(r"капсул|kapsul|capsul", answer, re.I)
        ):
            raise Fault("invalid_answer", 502)
        if (
            any(n not in allowed_numbers for n in re.findall(r"\d+(?:[.,]\d+)?", answer))
            or re.search(r"https?://|<[^>]+>|\]\(", answer)
            or PERSONAL.search(answer)
        ):
            raise Fault("invalid_answer", 502)
        if request.language == "ru" and not re.search(r"[А-Яа-я]", answer):
            raise Fault("invalid_answer", 502)
        if request.language == "uz" and len(set(RUSSIAN_PROSE.findall(answer.casefold()))) >= 2:
            raise Fault("invalid_answer", 502)

    def envelope(
        self,
        request: Turn,
        carry: Carry,
        selection: list[int],
        status: str,
        answer: str,
        sources: list[dict[str, Any]],
        products: list[dict[str, Any]],
        opened: int | None,
        cautions: list[str],
    ) -> dict[str, Any]:
        token, expires = self.tokens.issue(
            request.tab_id,
            request.language,
            selection,
            carry.epoch,
            pg=carry.pg,
            check=carry.check,
            cleared=carry.cleared,
            flow=carry.flow,
        )
        public = [
            {k: v for k, v in source.items() if k not in {"product_id", "caution", "ordinal"}}
            for source in sources
        ]
        return {
            "request_id": str(request.request_id),
            "context_revision": request.context_revision,
            "language": request.language,
            "status": status,
            "answer": answer,
            "sources": public,
            "cautions": cautions,
            "products": products,
            "open_product_id": opened,
            "context_token": token,
            "context_expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
            "commercial_checked_at": datetime.now(UTC).isoformat() if products else None,
        }

    async def generate(
        self,
        request: Turn,
        owner: str,
        carry: Carry,
        selection: list[int],
        subject: Subject,
        evidence: Evidence,
    ) -> dict[str, Any]:
        candidates = subject.ids
        origin = "explicit" if subject.explicit else "displayed" if selection else "page"
        if not candidates:
            reservation = await self.controls.reserve(owner, "embeddings")
            vector, usage = await self.waiting(owner, self.provider.embedding(request.question))
            await self.usage(reservation, owner, usage, True)
            candidates = await self.knowledge.rank(evidence, vector)
            origin = "retrieval"
        if not candidates:
            return self.envelope(
                request, carry, [], "limited", LIMITED[request.language], [], [], None, []
            )
        preliminary = evidence.context(candidates, request.question)
        inputs: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "language": request.language,
                        "question": request.question,
                        "history": [p.model_dump() for p in request.history],
                        "resolved_product_ids": candidates,
                        "displayed_selection": selection,
                        "candidate_origin": origin,
                        "preliminary": self.model_context(preliminary),
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        supplied = {s["source_id"]: s for s in preliminary["sections"]}
        hydrated = set(candidates)
        caution_products = {
            s["product_id"] for s in preliminary["sections"] if s["ordinal"] in {2, 3}
        }
        call_ids: set[str] = set()
        tools_used = 0
        proposal: Proposal | None = None
        for step in range(3):
            await self.controls.alive(owner)
            reservation = await self.controls.reserve(owner, "responses")
            result = await self.waiting(owner, self.provider.response(inputs, step == 2))
            await self.usage(reservation, owner, result.get("usage", {}))
            if result.get("status") != "completed" or not isinstance(result.get("output"), list):
                raise Fault("invalid_answer", 502)
            output = result["output"]
            if any(
                not isinstance(item, dict)
                or item.get("type") not in ("reasoning", "function_call", "message")
                for item in output
            ):
                raise Fault("invalid_answer", 502)
            calls = [item for item in output if item.get("type") == "function_call"]
            messages = [item for item in output if item.get("type") == "message"]
            if calls:
                if messages or len(calls) != 1 or tools_used >= 2 or step == 2:
                    raise Fault("invalid_answer", 502)
                call = calls[0]
                try:
                    args = ContextArgs.model_validate_json(call["arguments"])
                    if (
                        call.get("name") != "read_context"
                        or len(set(args.product_ids)) != len(args.product_ids)
                        or any(i not in candidates for i in args.product_ids)
                        or not isinstance(call.get("call_id"), str)
                        or not call["call_id"].strip()
                        or call["call_id"] in call_ids
                    ):
                        raise ValueError
                except (ValidationError, ValueError, KeyError):
                    raise Fault("invalid_answer", 502) from None
                call_ids.add(call["call_id"])
                await self.controls.alive(owner)
                current = await self.knowledge.snapshot(request.language)
                ids = args.product_ids or candidates
                if current.release != evidence.release or any(
                    i not in current.products for i in ids
                ):
                    return self.envelope(
                        request, carry, [], "limited", LIMITED[request.language], [], [], None, []
                    )
                context = current.context(ids, request.question, list(args.sections))
                if set(args.sections) & {"use", "effects"}:
                    caution_products.update(ids)
                supplied.update({s["source_id"]: s for s in context["sections"]})
                hydrated.update(ids)
                model_context = self.model_context(context)
                inputs.extend(output)
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(
                            model_context, ensure_ascii=False, separators=(",", ":")
                        ),
                    }
                )
                tools_used += 1
            else:
                try:
                    if (
                        len(messages) != 1
                        or len(messages[0]["content"]) != 1
                        or messages[0]["content"][0]["type"] != "output_text"
                    ):
                        raise ValueError
                    proposal = Proposal.model_validate_json(messages[0]["content"][0]["text"])
                except (ValidationError, ValueError, KeyError, IndexError, TypeError):
                    raise Fault("invalid_answer", 502) from None
                break
        if (
            proposal is None
            or any(i not in hydrated for i in proposal.product_ids)
            or any(i not in supplied for i in proposal.source_ids)
        ):
            raise Fault("invalid_answer", 502)
        if proposal.open_product_id is not None and proposal.open_product_id not in hydrated:
            raise Fault("invalid_answer", 502)
        fresh = await self.knowledge.snapshot(request.language)
        current_sources = {s["source_id"]: s for s in fresh.sections}
        if (
            fresh.release != evidence.release
            or any(i not in fresh.products for i in hydrated)
            or any(
                s not in current_sources or current_sources[s] != supplied[s]
                for s in proposal.source_ids
            )
        ):
            return self.envelope(
                request, carry, [], "limited", LIMITED[request.language], [], [], None, []
            )
        relevant = [supplied[s] for s in proposal.source_ids]
        source_products = {s["product_id"] for s in relevant}
        ids = [i for i in candidates if i in proposal.product_ids]
        if source_products and not ids and proposal.status in {"answer", "limited"}:
            ids = [i for i in candidates if i in source_products]
        if source_products - set(ids) and proposal.status in {"answer", "limited"}:
            return self.envelope(
                request, carry, [], "clarification", LIMITED[request.language], [], [], None, []
            )
        opened = (
            proposal.open_product_id
            if proposal.status == "answer"
            and subject.open_intent
            and len(subject.ids) == 1
            and proposal.open_product_id == subject.ids[0]
            and proposal.open_product_id in ids
            else None
        )
        if proposal.status == "answer" and not relevant:
            # None of the discarded generated prose is delivered or admitted as evidence.
            # This includes spaced prices and injected medical/link text.
            if self.commercial_changed(evidence, fresh, hydrated):
                return self.changed(request, carry)
            labels = fresh.labels(ids, request.language)
            if labels is None:
                return self.envelope(
                    request, carry, [], "clarification", CLARIFY[request.language], [], [], None, []
                )
            answer = (
                self.prices(request, [fresh.products[i] for i in ids], labels)
                if ids
                else "Подходящих товаров среди выбранных нет."
                if request.language == "ru"
                else "Tanlanganlar orasida mos mahsulot yoʻq."
            )
            return self.envelope(
                request,
                carry,
                ids,
                "answer",
                answer,
                [],
                [fresh.products[i] for i in ids],
                opened,
                [],
            )
        if proposal.status in {"clarification", "refused"} or not relevant:
            # Empty arrays never revive a prior subject. Only checked plain text is delivered.
            fallback = REFUSED if proposal.status == "refused" else LIMITED
            try:
                self.guard(request, proposal.answer, set(), set())
                if (
                    relevant
                    or proposal.product_ids
                    or re.search(r"\b(?:UZS|сум|soʻm|som)\b", proposal.answer, re.I)
                ):
                    raise Fault("invalid_answer", 502)
                answer = proposal.answer
            except Fault:
                answer = fallback[request.language]
            return self.envelope(request, carry, [], proposal.status, answer, [], [], None, [])
        # The model may use any supplied Shop fact for its subset, regardless of wording.
        if self.commercial_changed(evidence, fresh, hydrated):
            return self.changed(request, carry)
        cautions: list[str] = []
        if ADVICE.search(proposal.answer):
            caution_products.update(source_products | set(ids))
        for s in fresh.sections:
            if s["product_id"] in set(ids) & caution_products and s["caution"]:
                cautions.append(s["source_id"])
                if s not in relevant:
                    relevant.append(s)
        allowed_numbers = set(
            re.findall(r"\d+(?:[.,]\d+)?", " ".join(s["passage"] for s in relevant))
        )
        self.guard(
            request, proposal.answer, {s["form"].casefold() for s in relevant}, allowed_numbers
        )
        # Generated commercial numbers are never delivered in instruction prose.
        if re.search(r"\b(?:UZS|сум|so[ʻ‘’ʼ']?m|som)\b", proposal.answer, re.I):
            raise Fault("invalid_answer", 502)
        answer = proposal.answer
        return self.envelope(
            request,
            carry,
            ids,
            proposal.status,
            answer,
            relevant,
            [fresh.products[i] for i in ids],
            opened,
            cautions,
        )

    @staticmethod
    def model_context(context: dict[str, Any]) -> dict[str, Any]:
        # Disclosure-only metadata stays server owned; original passages remain complete.
        return {
            **context,
            "sections": [
                {
                    k: v
                    for k, v in s.items()
                    if k
                    in {
                        "source_id",
                        "product_id",
                        "form",
                        "language",
                        "section_title",
                        "passage",
                        "caution",
                        "ordinal",
                    }
                }
                for s in context["sections"]
            ],
        }

    @staticmethod
    def commercial_changed(before: Evidence, after: Evidence, ids: set[int]) -> bool:
        return any(
            before.products[i][field] != after.products[i][field]
            for i in ids
            for field in (
                "price",
                "sale_price",
                "is_sale",
                "currency",
                "is_in_stock",
                "is_purchasable",
            )
        )

    def changed(self, request: Turn, carry: Carry) -> dict[str, Any]:
        answer = (
            "Цены или наличие изменились. Уточните запрос."
            if request.language == "ru"
            else "Narx yoki mavjudlik oʻzgardi. Soʻrovni aniqlashtiring."
        )
        return self.envelope(request, carry, [], "clarification", answer, [], [], None, [])
