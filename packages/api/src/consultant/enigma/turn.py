"""One Enigma turn (spec r2 SW-04, SW-05, FT-01…FT-08, REC-01).

Nets on every customer message, one Jev request, the hard rules, one Luna call (one more
after an unusable or failed text), the text checks, then the response with at most two
cards. Nothing is stored between turns: a "more" turn runs the same steps and shows the next
cards.
"""

import asyncio
import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.consultant.context import FORMS, Context, normalize
from src.consultant.contracts import Fault, PageView, Turn
from src.consultant.enigma import checks, luna, nets, rules, shop
from src.consultant.enigma.cards import (
    ITEM_PRODUCTS,
    SUPPLEMENT_IDS,
    active_category,
    format_price,
    item_cards,
    page_set_card,
)
from src.consultant.enigma.data import CORES, RULES, UNIT, Text
from src.consultant.enigma.jev import Attempt, Jev, Reading
from src.consultant.knowledge import Evidence
from src.consultant.record import TurnRecord

if TYPE_CHECKING:
    from src.consultant.service import Carry, Consultant

MORE = frozenset(" ".join(nets.tokens(reply)) for reply in RULES.more_replies)
STATUS = {
    "offer": "answer",
    "ask": "clarification",
    "doctor": "limited",
    "emergency": "limited",
    "no_product": "limited",
    "shop": "answer",
}
SIGNS = ("doctor", "number", "intolerance", "diarrhea", "pregnancy")
MAGNIY_IDS = frozenset(p.shop_id for p in UNIT["magniy"].products)
PREGNANCY_IDS = frozenset(i for u in CORES.units for i in u.pregnancy)
ITEM_BY_PRODUCT = {p.shop_id: item for item in CORES.items for p in item.products}
UNIT_BY_PRODUCT = {prod.shop_id: (unit, prod) for unit in CORES.units for prod in unit.products}
SHARED_FORMS: dict[frozenset[int], dict[str, int]] = {
    frozenset(p.shop_id for p in unit.products): {str(p.form): p.shop_id for p in unit.products}
    for unit in CORES.units
    if len({p.form for p in unit.products}) == 2 and len(unit.products) == 2
}
PRIVACY_NOTE = "Private account and order records are not transmitted to the consultant."


class Failure(Exception):
    """The Enigma turn ends with the failure response for this trigger."""

    def __init__(self, trigger: str, check: str | None = None) -> None:
        super().__init__(trigger)
        self.trigger, self.check = trigger, check


class Named(Exception):
    """The message only names a supplement: the turn goes on on the named-product path."""

    def __init__(self, ids: list[int]) -> None:
        super().__init__("named")
        self.ids = ids


def more_reply(text: str) -> bool:
    return " ".join(nets.tokens(text)) in MORE


def pregnant(request: Turn, context: Context) -> bool:
    """The conversation's pregnancy flag: the token's, or a pregnancy word in any message."""
    texts = [*(p.question for p in request.history), request.question]
    return context.pg or any(nets.signs(text).pregnancy for text in texts)


def named(reading: Reading, question: str) -> list[int]:
    """SW-04: the products of the named supplement; a form word picks one of two."""
    ids = reading.named
    forms = [form for form, pattern in FORMS.items() if pattern.search(normalize(question))]
    if len(ids) > 1 and len(forms) == 1:
        ids = [
            p.shop_id
            for unit in CORES.units
            for p in unit.products
            if p.shop_id in ids and p.form == forms[0]
        ] or ids
    return ids


class Enigma:
    """The engine: its Jev client. One instance serves every turn."""

    def __init__(self, jev: Jev) -> None:
        self.jev = jev

    @staticmethod
    def ambulance(request: Turn, context: Context) -> nets.Ambulance | None:
        """N-01: the ambulance list on the current message, before any model call."""
        return nets.ambulance(request.question, pregnant(request, context))


class EnigmaTurn:
    def __init__(
        self,
        consultant: "Consultant",
        engine: Enigma,
        request: Turn,
        record: TurnRecord,
        owner: str,
        deadline: float,
        evidence: Evidence,
        carry: "Carry",
        selection: list[int],
        ambulance: nets.Ambulance | None,
    ) -> None:
        self.c, self.engine, self.request, self.record = consultant, engine, request, record
        self.owner, self.deadline, self.evidence = owner, deadline, evidence
        self.selection, self.ambulance = selection, ambulance
        self.customers = [p.question for p in request.history] + [request.question]
        self.signs = [nets.signs(text) for text in self.customers]
        self.carry = replace(carry, pg=carry.pg or any(s.pregnancy for s in self.signs))
        self.more = bool(selection) and more_reply(request.question)
        self.names = checks.Names.of(evidence.aliases)
        self.cards: dict[int, dict[str, Any]] = {}
        self.page_set: dict[str, Any] | None = None
        self.page_category: str | None = None
        # spec.shop-core r4 SJ-02, SR-02: the chosen shop topics; none on an ambulance hit.
        self.topics: tuple[str, ...] = ()

    def _page_view(self) -> PageView | None:
        if self.request.page_context is not None:
            return self.request.page_context.view
        if self.request.page_product_id is not None:
            return "product"
        return None

    # -- steps ---------------------------------------------------------------------------------

    async def run(self) -> dict[str, Any]:
        record = self.record
        record.path = "enigma"
        record.facts = {
            "ambulance": [self.ambulance.tier, self.ambulance.template] if self.ambulance else [],
            **{k: [i for i, s in enumerate(self.signs) if getattr(s, k)] for k in SIGNS},
        }
        attempt = await self.c.waiting(self.owner, self.engine.jev.ask(*self.state()))
        record.jev = self.jev_record(attempt)
        reading = attempt.reading
        if reading is None and self.ambulance is None:
            raise Failure("jev")
        if reading is not None and reading.name_only and not self.ambulance and not self.more:
            raise Named(named(reading, self.request.question))
        if reading is not None and self.ambulance is None:
            self.topics = tuple(t for t, _ in reading.topics)
        if self.topics:  # SO-02: also on a turn that ends with the failure response
            record.luna_part()["shop"] = list(self.topics)
        found = rules.candidates(reading, self.signs)
        await self.load_cards(found)
        pid = self.request.page_product_id
        if (
            self._page_view() == "product"
            and pid is not None
            and pid in ITEM_PRODUCTS
            and pid in self.cards
            and self.ambulance is None
            and not found.intolerance
        ):
            page_item = ITEM_BY_PRODUCT[pid]
            if all(it.id != page_item.id for it in found.items):
                found = replace(found, items=(*found.items, page_item))
        failed: str | None = None
        trigger: tuple[str, str | None] = ("luna", None)
        for second in (False, True):
            remaining = self.deadline - asyncio.get_running_loop().time()
            if second and remaining < luna.SECOND_CALL_MINIMUM:
                break
            brief = self.brief(found, reading, failed)
            parsed = await self.call(brief, found, second)
            if parsed.answer is None or (parsed.answer.decision == "shop" and not self.topics):
                # SL-02: a shop answer without shop texts in the brief is unusable.
                failed, trigger = "answer", ("luna", None)
                continue
            answer = parsed.answer
            allowed, shown = self.products(answer, found)
            subject = checks.Subject(
                self.request.language,
                answer.decision,
                frozenset(allowed),
                frozenset(shown),
                checks.numbers([json.dumps(brief, ensure_ascii=False), *self.customers]),
                tuple(self.approved(brief)),
                shop.reference(self.request.language, self.topics) if self.topics else (),
                tuple(self.customers),
            )
            failed = checks.check(answer.text, subject, self.names)
            self.record.luna_part()["calls"][-1]["check_id"] = failed
            if failed is None:
                return self.respond(answer, shown, found)
            trigger = ("check", failed)
        raise Failure(*trigger)

    def state(self) -> tuple[list[str], str]:
        """J-02: the earlier customer messages and the message Jev reads. In a "more" turn
        the message is the last one that is not a "more" reply."""
        index = len(self.customers) - 1
        if self.more:
            index = next(
                (i for i in range(index - 1, -1, -1) if not more_reply(self.customers[i])), index
            )
        earlier = [t for t in self.customers[:index] if not more_reply(t)]
        return earlier, self.customers[index]

    async def load_cards(self, found: rules.Candidates) -> None:
        """Supplement cards come from the knowledge snapshot, item/set/category data from the
        catalogue in one short detached DB session."""
        view = self._page_view()
        pid = self.request.page_product_id
        wanted = [i for i in self.selection if i in ITEM_PRODUCTS]
        for item in found.items:
            wanted += [p.shop_id for p in item.products]
        on_active_product_page = (
            view == "product"
            and pid is not None
            and self.ambulance is None
            and not found.intolerance
        )
        if on_active_product_page and pid in ITEM_PRODUCTS:
            assert pid is not None
            wanted += [p.shop_id for p in ITEM_BY_PRODUCT[pid].products]
        load_page_set = (
            on_active_product_page
            and pid is not None
            and pid not in SUPPLEMENT_IDS
            and pid not in ITEM_PRODUCTS
        )
        cat_id = (
            self.request.page_context.category_id
            if self.request.page_context is not None
            and self.request.page_context.view == "catalogue"
            else None
        )
        self.cards = dict(self.evidence.products)
        self.page_set = None
        self.page_category = None
        if wanted or load_page_set or cat_id is not None:
            async with self.c.knowledge.sessions() as db:
                if wanted:
                    self.cards.update(await item_cards(db, wanted, self.request.language))
                if load_page_set:
                    assert pid is not None
                    self.page_set = await page_set_card(db, pid, self.request.language)
                if cat_id is not None:
                    self.page_category = await active_category(db, cat_id, self.request.language)

    def _page_info(self, found: rules.Candidates) -> tuple[dict[str, Any] | None, list[int]]:
        """Pure page descriptor for brief['page'] plus flattened product IDs allowed on
        ask/no_product (UX-P02)."""
        view = self._page_view()
        if view is None or view == "invalid":
            return None, []
        if view == "home":
            return {"view": "home"}, []
        if view == "catalogue":
            return {"view": "catalogue", "category": self.page_category}, []
        if view in ("account", "order"):
            return {"view": view, "privacy_note": PRIVACY_NOTE}, []

        pid = self.request.page_product_id
        if pid is None or self.ambulance is not None or found.intolerance:
            return {"view": "product"}, []

        language = self.request.language

        def t(text: Text) -> str:
            return text.ru if language == "ru" else text.uz

        if pid in self.evidence.products:
            if (found.diarrhea and pid in MAGNIY_IDS) or (
                self.carry.pg and pid not in PREGNANCY_IDS
            ):
                return {"view": "product"}, []
            unit, prod = UNIT_BY_PRODUCT[pid]
            problems = list(
                dict.fromkeys(
                    t(pr.line)
                    for pr in unit.problems
                    if pr.applies_to is None or pid in pr.applies_to
                )
            )
            return {
                "view": "product",
                "kind": "supplement",
                "unit_id": unit.id,
                "name": prod.name,
                "form": prod.form,
                "for_whom": t(prod.for_whom),
                "problems": problems,
            }, [pid]

        if pid in ITEM_PRODUCTS and pid in self.cards:
            item = ITEM_BY_PRODUCT[pid]
            card = self.cards[pid]
            return {
                "view": "product",
                "kind": "item",
                "id": item.id,
                "name": t(item.name),
                "variants": [t(v.name) for v in item.products],
                "what": t(item.what),
                "price": format_price(card),
                "in_stock": bool(card["is_in_stock"]),
            }, [v.shop_id for v in item.products]

        if self.page_set is not None:
            desc = str(self.page_set.get("description") or "")
            named, _ = self.names.find(desc)
            constituent_ids = {i for group in named for i in group} & SUPPLEMENT_IDS
            if (found.diarrhea and bool(constituent_ids & MAGNIY_IDS)) or (
                self.carry.pg and not constituent_ids.issubset(PREGNANCY_IDS)
            ):
                return {"view": "product"}, []
            return {
                "view": "product",
                "kind": "set",
                "name": self.page_set["name"],
                "description": desc,
                "price": format_price(self.page_set),
                "in_stock": bool(self.page_set["is_in_stock"]),
            }, sorted(constituent_ids)

        return {"view": "product"}, []

    # -- Luna ----------------------------------------------------------------------------------

    def brief(
        self, found: rules.Candidates, reading: Reading | None, failed: str | None
    ) -> dict[str, Any]:
        """FT-02: everything Luna gets for the turn. No product price and no dose; shop texts may
        hold delivery amounts (spec.shop-core r4 SL-05)."""
        language = self.request.language

        def t(text: Text) -> str:
            return text.ru if language == "ru" else text.uz

        conversation: list[dict[str, Any]] = []
        for index, pair in enumerate(self.request.history):
            conversation.append({"index": index, "role": "customer", "text": pair.question})
            conversation.append({"role": "assistant", "text": pair.answer})
        conversation.append(
            {
                "index": len(self.request.history),
                "role": "customer",
                "text": self.request.question,
                "current": True,
            }
        )
        brief: dict[str, Any] = {
            "language": language,
            "conversation": conversation,
            "candidates": [
                {
                    "id": c.unit.id,
                    "title": t(c.unit.title),
                    "probability": round(c.probability, 2),
                    "products": [
                        {"name": p.name, "form": p.form, "for_whom": t(p.for_whom)}
                        for p in c.products
                    ],
                    "problems": list(dict.fromkeys(t(p.line) for p in c.problems)),
                    "pair_line": t(c.pair_line) if c.pair_line else None,
                }
                for c in found.units
            ],
            "items": [
                {
                    "id": item.id,
                    "name": t(item.name),
                    "variants": [
                        {
                            "name": t(p.name),
                            "price": format_price(self.cards[p.shop_id]),
                            "in_stock": bool(self.cards[p.shop_id]["is_in_stock"]),
                        }
                        if p.shop_id in self.cards
                        else {"name": t(p.name)}
                        for p in item.products
                    ],
                    "what": t(item.what),
                }
                for item in found.items
            ],
            "jev": (
                {k: round(v, 2) for k, v in reading.special.items()}
                if reading
                else {"status": "unavailable"}
            ),
            "nets": {
                "ambulance": (
                    {
                        "tier": self.ambulance.tier,
                        "text": t(getattr(RULES.emergency, self.ambulance.template)),
                    }
                    if self.ambulance
                    else None
                ),
                "messages": [
                    {"index": i, "signs": [k for k in SIGNS if getattr(s, k)]}
                    for i, s in enumerate(self.signs)
                    if s.any
                ],
            },
            "intolerance": found.intolerance,
            "shown_products": [
                {
                    "name": self.cards[i]["name"],
                    "price": format_price(self.cards[i]),
                    "in_stock": bool(self.cards[i]["is_in_stock"]),
                }
                if i in ITEM_PRODUCTS
                else self.cards[i]["name"]
                for i in self.selection
                if i in self.cards
            ],
            "more": self.more,
            "rules": {"wording": RULES.wording_rules, "offer": RULES.offer_rules},
            "texts": {
                "instruction_sentence": t(RULES.instruction_sentence),
                "opening_question": t(RULES.opening_question),
                "phrases": {k: t(v) for k, v in RULES.phrases.items()},
                "emergency": {k: t(getattr(RULES.emergency, k)) for k in nets.TEMPLATES},
            },
        }
        page, _ = self._page_info(found)
        if page is not None:
            brief["page"] = page
        if self.topics:
            brief["shop"] = shop.brief(language, self.topics)
        if failed is not None:
            brief["retry"] = {"failed_check": failed}
        return brief

    @staticmethod
    def approved(brief: dict[str, Any]) -> list[str]:
        """The approved texts and instruction lines of a brief (V-06)."""
        texts = brief["texts"]
        out = [texts["instruction_sentence"], texts["opening_question"]]
        out += [*texts["phrases"].values(), *texts["emergency"].values()]
        for candidate in brief["candidates"]:
            out += candidate["problems"]
            out += [p["for_whom"] for p in candidate["products"]]
            if candidate["pair_line"]:
                out.append(candidate["pair_line"])
        out += [item["what"] for item in brief["items"]]
        page = brief.get("page")
        if isinstance(page, dict):
            kind = page.get("kind")
            if kind == "supplement":
                out += [page["for_whom"], *page["problems"]]
            elif kind == "item":
                out.append(page["what"])
            elif kind == "set" and page.get("description"):
                out.append(page["description"])
        return out

    async def call(
        self, brief: dict[str, Any], found: rules.Candidates, second: bool
    ) -> luna.Parsed:
        """FT-01: one Luna call with registration, reservation, usage and waiting."""
        c = self.c
        payload = luna.payload(brief)
        part = self.record.luna_part()
        part["model"], part["effort"] = luna.MODEL, luna.EFFORT
        await c.controls.alive(self.owner)
        reservation = await c.controls.reserve(self.owner, "responses")
        loop = asyncio.get_running_loop()
        started = loop.time()

        async def work() -> dict[str, Any] | Fault:
            # Provider faults stay inside the task, so the waiting loop raises only the
            # control faults (cancel, disabled) of this turn.
            try:
                return await c.provider.call("responses", payload)
            except Fault as fault:
                return fault

        latency: float | None = None
        body: dict[str, Any] | Fault | None = None
        try:
            async with asyncio.timeout(luna.TIMEOUT):
                body = await c.waiting(self.owner, work())
            latency = loop.time() - started
        except TimeoutError:
            body = None
        if body is None:
            parsed = luna.Parsed(None, "timeout")
        elif isinstance(body, Fault):
            parsed = luna.Parsed(
                None, "invalid" if body.code == "invalid_answer" else "provider_error"
            )
        else:
            await c.usage(reservation, self.owner, body.get("usage", {}))
            ids = frozenset([u.unit.id for u in found.units] + [it.id for it in found.items])
            parsed = luna.parse(body, ids)
        c.counters.add("luna.calls")
        c.counters.add("luna.outcome." + (parsed.failure or "ok"))
        c.counters.latency(latency)
        if second:
            c.counters.add("luna.second_calls")
        answer = parsed.answer
        part["calls"].append(
            {
                "second": second,
                "latency": latency,
                "outcome": parsed.failure or "ok",
                "target": answer.decision if answer else None,
                "text": answer.text if answer else None,
                "check_id": None,
            }
        )
        return parsed

    # -- the answer ----------------------------------------------------------------------------

    def _select_shown_round_robin(
        self, answer_text: str, group_carded: list[list[int]]
    ) -> list[int]:
        """UX-C01: mention-ordered per-unit/item round-robin card selection with local-span
        single-form narrowing on shared-name supplement pairs."""
        norm_text = normalize(answer_text)
        rest = norm_text
        raw_spans: list[tuple[int, frozenset[int]]] = []
        for name, ids in self.names.entries:
            pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")
            matches = list(pattern.finditer(rest))
            if not matches:
                continue
            for m in matches:
                raw_spans.append((m.start(), ids))
            rest = pattern.sub(lambda m: " " * (m.end() - m.start()), rest)
        raw_spans.sort(key=lambda item: item[0])

        spans: list[tuple[int, frozenset[int]]] = []
        for idx, (pos, ids) in enumerate(raw_spans):
            if len(ids) == 2 and ids in SHARED_FORMS:
                next_pos = raw_spans[idx + 1][0] if idx + 1 < len(raw_spans) else len(norm_text)
                local_span = norm_text[0 if idx == 0 else pos : next_pos]
                matched_forms = [form for form, pat in FORMS.items() if pat.search(local_span)]
                if len(matched_forms) == 1:
                    ids = frozenset((SHARED_FORMS[ids][matched_forms[0]],))
            spans.append((pos, ids))

        ranked_groups: list[tuple[tuple[bool, int, int], list[int]]] = []
        for g_idx, g_cards in enumerate(group_carded):
            earliest: dict[int, int] = {}
            for pos, span_ids in spans:
                for pid in g_cards:
                    if pid in span_ids and pid not in earliest:
                        earliest[pid] = pos
            if earliest:
                # Dict comprehension/order preserves g_cards display order for equal positions.
                ordered_ids = sorted(earliest, key=lambda pid: earliest[pid])
                ranked_groups.append(((False, min(earliest.values()), g_idx), ordered_ids))
            elif g_cards:
                ranked_groups.append(((True, 0, g_idx), list(g_cards)))

        ranked_groups.sort(key=lambda item: item[0])
        ordered_lists = [ids for _, ids in ranked_groups]

        shown: list[int] = []
        for pid_list in ordered_lists:
            if len(shown) == 2:
                break
            for pid in pid_list:
                if pid not in shown:
                    shown.append(pid)
                    break
        if len(shown) < 2:
            for pid_list in ordered_lists:
                for pid in pid_list[1:]:
                    if len(shown) == 2:
                        break
                    if pid not in shown:
                        shown.append(pid)
                if len(shown) == 2:
                    break
        return shown

    def products(self, answer: luna.Answer, found: rules.Candidates) -> tuple[list[int], list[int]]:
        """(the products a text may name, the products shown as cards) for a decision."""
        if answer.decision in ("doctor", "emergency"):
            return [], []
        if answer.decision == "shop":
            return self.selection, self.selection
        if answer.decision != "offer":
            _, page_allowed = self._page_info(found)
            return list(dict.fromkeys([*page_allowed, *self.selection])), self.selection
        units = {c.unit.id: c for c in found.units}
        items_by_id = {it.id: it for it in found.items}
        offered: list[int] = []
        group_carded: list[list[int]] = []
        for chosen in answer.offered:
            if chosen in units:
                raw_ids = [p.shop_id for p in units[chosen].products]
            elif chosen in items_by_id:
                raw_ids = [p.shop_id for p in items_by_id[chosen].products]
            else:
                raw_ids = []
            offered += raw_ids
            group_carded.append([i for i in raw_ids if i in self.cards])
        carded = [i for i in offered if i in self.cards]
        if self.more:
            # FT-07: the next two; when none is left, the shown ones again.
            shown = [i for i in carded if i not in self.selection][:2] or [
                i for i in self.selection if i in self.cards
            ]
        else:
            shown = self._select_shown_round_robin(answer.text, group_carded)
        if not shown:
            raise Failure("card")
        return [*offered, *shown], shown

    def respond(
        self, answer: luna.Answer, shown: list[int], found: rules.Candidates
    ) -> dict[str, Any]:
        record = self.record
        record.graf_outcome = answer.decision
        record.situation = answer.offered[0] if answer.offered else None
        if answer.decision == "shop":
            record.situation = self.topics[0]
            self.c.counters.add("enigma.shop")
        record.template = None
        record.offered_product_ids = shown if answer.decision == "offer" else []
        record.luna_part().update(
            decision=answer.decision,
            offered=answer.offered,
            candidates=[[c.unit.id, c.probability] for c in found.units]
            + [[it.id, None] for it in found.items],
            shop=list(self.topics),
        )
        cards = [self.cards[i] for i in shown if i in self.cards]
        return self.c.envelope(
            self.request,
            self.carry,
            [card["id"] for card in cards],
            STATUS[answer.decision],
            answer.text,
            [],
            cards,
            None,
            [],
        )

    @staticmethod
    def jev_record(attempt: Attempt) -> dict[str, Any]:
        reading = attempt.reading
        return {
            "model": reading.model if reading else None,
            "latency": attempt.latency,
            "error": attempt.error,
            "bytes": attempt.size,
            "unit": [[u, p] for u, p in reading.units]
            + [[k, p] for k, p in reading.special.items()]
            if reading
            else [],
            "item": list(reading.items[0]) if reading and reading.items else None,
            "items": [list(x) for x in reading.items] if reading else [],
            "name": list(reading.name) if reading and reading.name else None,
            # SO-02: the top topic option (may be 'none'), then the second chosen topic.
            "topic": [list(reading.topic), *[list(t) for t in reading.topics[1:]]]
            if reading
            else None,
        }
