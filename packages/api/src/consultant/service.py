"""One turn: admission, current subject, read-only evidence, validated delivery."""

import asyncio
import json
import re
from collections.abc import Coroutine
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from src.consultant.context import SENSITIVE, Subject, Tokens, pure_open, resolve
from src.consultant.contracts import ContextArgs, Fault, Proposal, Turn
from src.consultant.control import Controls
from src.consultant.graf import Graf, Reply
from src.consultant.knowledge import ADVICE, Evidence, Knowledge
from src.consultant.provider import Provider

T = TypeVar("T")
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
# Cyrillic is also used for Uzbek. Reject clear Russian prose conservatively;
# shared script, product names or a single borrowed/quoted word are insufficient.
RUSSIAN_PROSE = re.compile(
    r"\b(?:это|для|или|при|также|можно|нельзя|следует|состав|указан[аоы]?|"
    r"содержит|детей|взрослых|инструкци[яию]|индивидуальная|непереносимость)\b",
    re.I,
)


class Consultant:
    def __init__(
        self,
        controls: Controls,
        knowledge: Knowledge,
        provider: Provider,
        tokens: Tokens,
        graf: Graf | None = None,
    ) -> None:
        self.controls, self.knowledge, self.provider, self.tokens = (
            controls,
            knowledge,
            provider,
            tokens,
        )
        # Graf is on only when its key is configured (plan r4 section 2).
        self.graf = graf

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
        if SENSITIVE.search(request.question) or any(
            SENSITIVE.search(p.question + " " + p.answer) for p in request.history
        ):
            raise Fault("sensitive_input", 422)
        selection, epoch = (
            self.tokens.read(request) if request.context_token else ([], str(uuid4()))
        )
        if self.graf is not None:
            # Plan r4 step 2: the ambulance list runs before register and snapshot. A "no"
            # to the check question continues with the checked message instead.
            early, request = self.graf.safety(request)
            if early is not None:
                return self.grafted(request, epoch, early)
        owner = await self.controls.register(request.request_id, request.tab_id)
        deadline = asyncio.get_running_loop().time() + 60
        try:
            async with asyncio.timeout_at(deadline):
                evidence = await self.knowledge.snapshot(request.language)

                def jev(work: Coroutine[Any, Any, T]) -> Coroutine[Any, Any, T]:
                    return self.waiting(owner, work)

                owned = self.graf is not None and self.graf.owns(request)
                reply = (
                    await self.graf.continuation(request, evidence, jev)
                    if self.graf is not None and owned
                    else None
                )
                if any(i not in evidence.products for i in selection):
                    selection = []
                subject = resolve(
                    request.question,
                    evidence.aliases,
                    selection,
                    request.page_product_id
                    if request.page_product_id in evidence.products
                    else None,
                    request.context_token is None,
                )
                if self.graf is not None and not owned and not subject.explicit and not selection:
                    reply = await self.graf.first(request, evidence, jev)
                if reply is not None:
                    response = self.grafted(request, epoch, reply)
                elif subject.clarify:
                    response = self.envelope(
                        request,
                        epoch,
                        [],
                        "clarification",
                        CLARIFY[request.language],
                        [],
                        [],
                        None,
                        [],
                    )
                elif any(i not in evidence.products for i in subject.ids):
                    response = self.envelope(
                        request, epoch, [], "limited", LIMITED[request.language], [], [], None, []
                    )
                elif PERSONAL.search(request.question):
                    response = self.envelope(
                        request,
                        epoch,
                        [],
                        "refused",
                        REFUSED[request.language],
                        [],
                        [],
                        None,
                        [],
                    )
                elif (
                    subject.open_intent
                    and len(subject.ids) == 1
                    and pure_open(request.question, evidence.aliases[subject.ids[0]])
                ):
                    fresh = await self.knowledge.snapshot(request.language)
                    if fresh.release != evidence.release or any(
                        i not in fresh.products for i in subject.ids
                    ):
                        response = self.envelope(
                            request,
                            epoch,
                            [],
                            "limited",
                            LIMITED[request.language],
                            [],
                            [],
                            None,
                            [],
                        )
                    else:
                        products = [fresh.products[i] for i in subject.ids]
                        labels = fresh.labels(subject.ids, request.language)
                        if labels is None:
                            response = self.envelope(
                                request,
                                epoch,
                                [],
                                "clarification",
                                CLARIFY[request.language],
                                [],
                                [],
                                None,
                                [],
                            )
                        else:
                            response = self.native(request, epoch, subject, products, labels)
                else:
                    response = await self.generate(
                        request, owner, epoch, selection, subject, evidence
                    )
                await self.controls.alive(owner)
                return response
        except TimeoutError:
            raise Fault("timeout", 504) from None
        finally:
            with suppress(TimeoutError):
                async with asyncio.timeout_at(deadline):
                    await self.controls.finish(owner)

    def grafted(self, request: Turn, epoch: str, reply: Reply) -> dict[str, Any]:
        return self.envelope(
            request,
            epoch,
            reply.ids,
            reply.status,
            reply.answer,
            reply.sources,
            reply.products,
            None,
            reply.cautions,
        )

    def native(
        self,
        request: Turn,
        epoch: str,
        subject: Subject,
        products: list[dict[str, Any]],
        labels: list[str],
    ) -> dict[str, Any]:
        answer = ("Открываю " if request.language == "ru" else "Ochilmoqda: ") + labels[0] + "."
        return self.envelope(
            request,
            epoch,
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
        epoch: str,
        selection: list[int],
        status: str,
        answer: str,
        sources: list[dict[str, Any]],
        products: list[dict[str, Any]],
        opened: int | None,
        cautions: list[str],
    ) -> dict[str, Any]:
        token, expires = self.tokens.issue(request.tab_id, request.language, selection, epoch)
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
        epoch: str,
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
                request, epoch, [], "limited", LIMITED[request.language], [], [], None, []
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
                        request, epoch, [], "limited", LIMITED[request.language], [], [], None, []
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
                request, epoch, [], "limited", LIMITED[request.language], [], [], None, []
            )
        relevant = [supplied[s] for s in proposal.source_ids]
        source_products = {s["product_id"] for s in relevant}
        ids = [i for i in candidates if i in proposal.product_ids]
        if source_products and not ids and proposal.status in {"answer", "limited"}:
            ids = [i for i in candidates if i in source_products]
        if source_products - set(ids) and proposal.status in {"answer", "limited"}:
            return self.envelope(
                request, epoch, [], "clarification", LIMITED[request.language], [], [], None, []
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
                return self.changed(request, epoch)
            labels = fresh.labels(ids, request.language)
            if labels is None:
                return self.envelope(
                    request, epoch, [], "clarification", CLARIFY[request.language], [], [], None, []
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
                epoch,
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
            return self.envelope(request, epoch, [], proposal.status, answer, [], [], None, [])
        # The model may use any supplied Shop fact for its subset, regardless of wording.
        if self.commercial_changed(evidence, fresh, hydrated):
            return self.changed(request, epoch)
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
            epoch,
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

    def changed(self, request: Turn, epoch: str) -> dict[str, Any]:
        answer = (
            "Цены или наличие изменились. Уточните запрос."
            if request.language == "ru"
            else "Narx yoki mavjudlik oʻzgardi. Soʻrovni aniqlashtiring."
        )
        return self.envelope(request, epoch, [], "clarification", answer, [], [], None, [])
