"""F30 regressions: six-section intent union, unknown subjects and explicit form order."""

import hashlib
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from src.categories.models import Category
from src.consultant.context import resolve
from src.consultant.contracts import Fault, Pair
from src.consultant.knowledge import Evidence
from src.consultant.models import Control, Embedding, Membership, Release, Run, Section, Source
from src.consultant.provider import EMBEDDING_PROFILE, Provider, encode
from src.products.models import Product
from tests.test_consultant import ProviderStub, request, seed, service

ALIASES = {
    20: ["BIFOLAK ACTIVE", "BIFOLAK ACTIVE CAPSULE", "BIFOLAK ACTIVE капсулы"],
    21: ["BIFOLAK ACTIVE", "BIFOLAK ACTIVE STICK", "BIFOLAK ACTIVE стики"],
}
# The accepted instructions use description/composition/use+restrictions/effects/quality/storage.
# Complete synthetic passages keep that actual six-section layout without importing a corpus.
PASSAGES = {
    "ru": [
        "Описание продукта и его действия.",
        "Состав: компонент 10 мг на порцию.",
        "Принимать с 3 лет. Для детей старше 3 лет. Индивидуальная непереносимость. "
        "При приёме лекарств или заболеваниях необходима консультация врача.",
        "Назначение и описанное действие продукта.",
        "Контроль качества и сертификат.",
        "Хранить в сухом месте.",
    ],
    "uz": [
        "Mahsulot tavsifi va taʼsiri.",
        "Tarkib: bir porsiyada komponent 10 mg.",
        "3 yoshdan qabul qilish. Individual murosasizlik. "
        "Dori qabul qilish yoki kasalliklarda shifokor bilan maslahat zarur.",
        "Mahsulotning tavsiya etilishi va taʼsiri.",
        "Sifat nazorati va sertifikat.",
        "Quruq joyda saqlash.",
    ],
}


async def seed_six_sections(db):
    await seed(db)
    db.add(
        Product(
            id=21,
            name="BIFOLAK ACTIVE STICK",
            slug="active-stick",
            price=300,
            is_active=True,
            is_in_stock=True,
        )
    )
    (await db.get(Product, 20)).name = "BIFOLAK ACTIVE CAPSULE"
    await db.execute(delete(Embedding))
    await db.execute(delete(Section))
    for pid, form in [(20, "capsule"), (21, "stick")]:
        for language in ("ru", "uz"):
            sid = f"s{pid}{language}"
            source = await db.get(Source, sid)
            if source is None:
                source = Source(
                    id=sid, product_id=pid, language=language, provenance={}, withdrawn=False
                )
                db.add(source)
                await db.flush()
                db.add(Membership(release_id="synthetic-release", source_id=sid))
            source.provenance = {
                "title": "BIFOLAK ACTIVE",
                "form": form,
                "revision": "synthetic-six-sections",
                "pdf_title": "Synthetic instruction",
                "aliases": ALIASES[pid],
                "section_count": 6,
            }
            for ordinal, body in enumerate(PASSAGES[language]):
                identity = f"{sid}:{ordinal}"
                db.add(
                    Section(
                        id=identity,
                        source_id=sid,
                        ordinal=ordinal,
                        body=body,
                        body_hash=hashlib.sha256(body.encode()).hexdigest(),
                        heading=[
                            "Description",
                            "Composition",
                            "Use",
                            "Effects",
                            "Quality",
                            "Storage",
                        ][ordinal],
                        caution=ordinal == 2,
                        pdf_page=1,
                    )
                )
                await db.flush()
                await db.execute(
                    text(
                        "INSERT INTO consultant_embeddings(section_id,profile,vector) "
                        "VALUES (:id,:profile,CAST(:vector AS vector))"
                    ),
                    {
                        "id": identity,
                        "profile": EMBEDDING_PROFILE,
                        "vector": str([1.0] + [0.0] * 1535),
                    },
                )
    # DERMACOMPLEX is not used by these cases; it has no complete source after replacement.
    await db.commit()


@pytest.mark.parametrize(
    ("question", "ordinals"),
    [
        ("состав", [1]),
        ("tarkibi", [1]),
        ("состав и ограничения", [0, 1, 2]),
        ("tarkib va cheklovlar", [0, 1, 2]),
        ("состав и противопоказания", [0, 1, 2]),
        ("tarkib va qarshi koʻrsatmalar", [0, 1, 2]),
        ("состав и как применять", [0, 1, 2]),
        ("tarkibi va qabul qilish", [0, 1, 2]),
        ("состав и с какого возраста", [0, 1, 2]),
        ("tarkib va yosh chegarasi", [0, 1, 2]),
        ("возраст ровно 7 лет", [0, 1, 2]),
        ("aynan 15 yosh", [0, 1, 2]),
        ("как применять", [0, 2]),
        ("qabul qilish", [0, 2]),
        ("состав и эффект", [0, 1, 2, 3]),
        ("tarkibi va taʼsiri", [0, 1, 2, 3]),
        ("состав и хранение", [1, 5]),
        ("tarkibi va saqlash", [1, 5]),
        ("качество и хранение", [4, 5]),
        ("sifat va saqlash", [4, 5]),
        ("Сравни капсулы и стики BIFOLAK MAGNIY. Покажи в этом порядке.", [0, 1, 2]),
        ("BIFOLAK MAGNIY kapsula va stiklarini taqqoslang.", [0, 1, 2]),
        ("BIFOLAK MAGNIY kapsula va stiklarini solishtiring.", [0, 1, 2]),
        ("Чем отличаются эти формы?", [0, 1, 2]),
        ("Ularning farqi nima?", [0, 1, 2]),
        ("Какое действие у BIFOLAK MAGNIY?", [0, 2, 3]),
        ("BIFOLAK MAGNIY taʼsiri qanday?", [0, 2, 3]),
        ("Что поможет поддерживать спокойствие?", []),
        ("Tinchlanish uchun qanday mahsulot bor?", []),
    ],
)
def test_context_combines_complete_six_section_intents(question, ordinals):
    sections = [
        {"source_id": f"s:{i}", "product_id": 20, "ordinal": i, "caution": i == 2, "passage": p}
        for i, p in enumerate(PASSAGES["ru"])
    ]
    context = Evidence("fixture", {20: {"id": 20}}, ALIASES, sections).context([20], question)
    assert [s["ordinal"] for s in context["sections"]] == ordinals
    assert [s["passage"] for s in context["sections"]] == [PASSAGES["ru"][i] for i in ordinals]


class CompositionOnlyProposal(ProviderStub):
    """Deliberately omit caution IDs and advice wording from otherwise supported output."""

    async def response(self, inputs, last):
        result = await super().response(inputs, last)
        if inputs[-1].get("type") == "function_call_output":
            context = json.loads(inputs[-1]["output"])
            proposal = json.loads(result["output"][0]["content"][0]["text"])
            if context["sections"]:
                proposal["source_ids"] = [
                    s["source_id"] for s in context["sections"] if s["ordinal"] == 1
                ]
            # A model's arbitrary card array must not determine resolved/navigation order.
            proposal["product_ids"] = list(reversed([p["id"] for p in context["products"]]))
            proposal["open_product_id"] = None
            result["output"][0]["content"][0]["text"] = json.dumps(proposal)
        return result


@pytest.mark.parametrize(
    ("language", "question"),
    [
        ("ru", "BIFOLAK ACTIVE: состав и ограничения?"),
        ("uz", "BIFOLAK ACTIVE tarkibi va cheklovlari?"),
        ("ru", "BIFOLAK ACTIVE: состав и противопоказания?"),
        ("uz", "BIFOLAK ACTIVE tarkibi va qarshi koʻrsatmalari?"),
    ],
)
async def test_requested_cautions_survive_model_omission(test_db, language, question):
    await seed_six_sections(test_db)
    provider = CompositionOnlyProposal()
    bot = service(test_db, provider)
    turn = request(question, language=language)
    result = await bot.turn(turn)
    assert result["status"] == "answer"
    assert result["cautions"] == [f"s{pid}{language}:2" for pid in (20, 21)]
    for pid in (20, 21):
        caution = next(s for s in result["sources"] if s["source_id"] == f"s{pid}{language}:2")
        assert caution["passage"] == PASSAGES[language][2]
    assert [p["id"] for p in result["products"]] == [20, 21]
    assert bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[
        0
    ] == [20, 21]
    assert [kind for kind, _ in provider.calls] == ["response", "response"]


@pytest.mark.parametrize(
    "question",
    [
        "Какая сейчас цена?",
        "А сколько он стоит?",
        "Какой у него состав?",
        "его состав",
        "narxi qancha?",
        "uning tarkibi qanday?",
    ],
)
def test_clearly_referential_fact_keeps_selection(question):
    result = resolve(question, {20: ["ACTIVE"]}, [20], 20, False)
    assert result.ids == [20]
    assert not result.explicit
    assert not result.clarify


@pytest.mark.parametrize(
    ("language", "question", "followup"),
    [
        ("ru", "Теперь DERMACOMPLEX, какая цена?", "DERMACOMPLEX состав"),
        ("uz", "Endi DERMACOMPLEX, narxi qancha?", "DERMACOMPLEX tarkibi"),
    ],
)
async def test_explicit_price_switch_and_short_composition_keep_new_subject(
    test_db, language, question, followup
):
    await seed(test_db)
    (await test_db.get(Product, 4)).price = 104000
    await test_db.commit()

    class SwitchProposal(ProviderStub):
        async def response(self, inputs, last):
            result = await super().response(inputs, last)
            if (
                json.loads(inputs[0]["content"])["question"] == question
                and inputs[-1].get("type") == "function_call_output"
            ):
                result["output"][0]["content"][0]["text"] = json.dumps(
                    {
                        "status": "answer",
                        "answer": "DERMACOMPLEX в капсулах стоит 104 000 UZS."
                        if language == "ru"
                        else "DERMACOMPLEX kapsula narxi 104 000 UZS.",
                        "source_ids": [],
                        "product_ids": [4],
                        "open_product_id": None,
                    }
                )
            return result

    provider = SwitchProposal()
    bot = service(test_db, provider)
    first = request("ACTIVE цена" if language == "ru" else "ACTIVE narxi", language=language)
    established = await bot.turn(first)
    turn = request(question, established["context_token"], first.tab_id, language)
    turn = turn.model_copy(
        update={"history": [Pair(question=first.question, answer=established["answer"])]}
    )
    price = await bot.turn(turn)
    assert price["status"] == "answer"
    assert [p["id"] for p in price["products"]] == [4]
    assert "104000" in price["answer"]
    assert price["sources"] == []
    assert price["open_product_id"] is None
    assert bot.tokens.read(turn.model_copy(update={"context_token": price["context_token"]}))[
        0
    ] == [4]
    assert len(provider.calls) == 4
    next_turn = request(followup, price["context_token"], first.tab_id, language)
    result = await bot.turn(next_turn)
    assert result["status"] == "answer"
    assert [p["id"] for p in result["products"]] == [4]
    assert result["sources"]
    assert all(s["source_id"].startswith(f"s4{language}:") for s in result["sources"])
    assert bot.tokens.read(next_turn.model_copy(update={"context_token": result["context_token"]}))[
        0
    ] == [4]
    assert len(provider.calls) == 6


@pytest.mark.parametrize("reverse_aliases", [False, True])
@pytest.mark.parametrize(
    ("question", "ids"),
    [
        ("Сравни BIFOLAK ACTIVE в капсулах и стиках: состав и ограничения", [20, 21]),
        ("Сравни BIFOLAK ACTIVE в стиках и капсулах: состав и ограничения", [21, 20]),
        ("BIFOLAK ACTIVE kapsula va stik: tarkib va cheklovlar", [20, 21]),
        ("BIFOLAK ACTIVE stik va kapsula: tarkib va cheklovlar", [21, 20]),
        ("BIFOLAK ACTIVE в стиках: состав", [21]),
        ("BIFOLAK ACTIVE kapsulada: tarkib", [20]),
        ("BIFOLAK ACTIVE CAPSULE и BIFOLAK ACTIVE STICK", [20, 21]),
        ("BIFOLAK ACTIVE STICK и BIFOLAK ACTIVE CAPSULE", [21, 20]),
        ("BIFOLAK ACTIVE CAPSULE и STICK", [20, 21]),
        ("BIFOLAK ACTIVE STICK и CAPSULE", [21, 20]),
        ("BIFOLAK ACTIVE капсулы и стики", [20, 21]),
        ("BIFOLAK ACTIVE стики и капсулы", [21, 20]),
    ],
)
def test_explicit_form_order_is_independent_of_alias_iteration(question, ids, reverse_aliases):
    aliases = dict(reversed(list(ALIASES.items()))) if reverse_aliases else ALIASES
    result = resolve(question, aliases, [], None, True)
    assert not result.clarify
    assert result.ids == ids


@pytest.mark.parametrize("reverse_rows", [False, True])
@pytest.mark.parametrize("language", ["ru", "uz"])
@pytest.mark.parametrize("ids", [[20, 21], [21, 20]])
async def test_form_order_cards_token_and_rendered_ordinal(
    test_db, monkeypatch, reverse_rows, language, ids
):
    await seed_six_sections(test_db)
    bot = service(test_db, CompositionOnlyProposal())
    snapshot = bot.knowledge.snapshot

    async def reordered_snapshot(lang):
        evidence = await snapshot(lang)
        evidence.aliases = dict(sorted(evidence.aliases.items(), reverse=reverse_rows))
        return evidence

    monkeypatch.setattr(bot.knowledge, "snapshot", reordered_snapshot)
    words = {20: "капсулах", 21: "стиках"} if language == "ru" else {20: "kapsula", 21: "stik"}
    question = (
        f"BIFOLAK ACTIVE в {words[ids[0]]} и {words[ids[1]]}: состав и ограничения"
        if language == "ru"
        else f"BIFOLAK ACTIVE {words[ids[0]]} va {words[ids[1]]}: tarkib va cheklovlar"
    )
    turn = request(question, language=language)
    result = await bot.turn(turn)
    assert [p["id"] for p in result["products"]] == ids
    assert [s["source_id"] for s in result["sources"][:2]] == [f"s{i}{language}:1" for i in ids]
    assert (
        bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[0]
        == ids
    )
    second = request(
        "открой второй" if language == "ru" else "ikkinchisini och",
        result["context_token"],
        turn.tab_id,
        language,
    )
    reply = await bot.turn(second)
    assert [p["id"] for p in reply["products"]] == [ids[1]]
    assert reply["open_product_id"] == ids[1]
    assert len(bot.provider.calls) == 2


@pytest.mark.parametrize("language", ["ru", "uz"])
@pytest.mark.parametrize("ids", [[20, 21], [21, 20]])
@pytest.mark.parametrize("ordinal", [0, 1])
async def test_pure_open_uses_rendered_token_and_completed_history_without_provider(
    test_db, language, ids, ordinal
):
    await seed_six_sections(test_db)
    bot = service(test_db, CompositionOnlyProposal())
    words = {20: "капсулах", 21: "стиках"} if language == "ru" else {20: "kapsula", 21: "stik"}
    question = (
        f"Сравни BIFOLAK ACTIVE в {words[ids[0]]} и {words[ids[1]]}: "
        "чем отличаются состав и ограничения? Покажи в этом порядке."
    )
    if language == "uz":
        question = (
            f"BIFOLAK ACTIVE {words[ids[0]]} va {words[ids[1]]}larini tarkibi va "
            "cheklovlari bo‘yicha solishtiring. Shu tartibda ko‘rsating."
        )
    turn = request(question, language=language)
    result = await bot.turn(turn)
    assert [p["id"] for p in result["products"]] == ids
    history = [{"question": turn.question, "answer": result["answer"]}]
    calls = list(bot.provider.calls)
    question = (
        f"Открой {['первый', 'второй'][ordinal]} из показанных."
        if language == "ru"
        else f"Ko‘rsatilganlardan {['birinchisini', 'ikkinchisini'][ordinal]} oching."
    )
    turn = type(turn).model_validate(
        request(question, result["context_token"], turn.tab_id, language).model_dump()
        | {"history": history}
    )
    opened = await bot.turn(turn)
    assert opened["status"] == "answer"
    assert opened["open_product_id"] == ids[ordinal]
    assert [p["id"] for p in opened["products"]] == [ids[ordinal]]
    assert bot.tokens.read(turn.model_copy(update={"context_token": opened["context_token"]}))[
        0
    ] == [ids[ordinal]]
    assert opened["commercial_checked_at"]
    assert opened["sources"] == opened["cautions"] == []
    assert bot.provider.calls == calls  # Both response and embedding interfaces are observed.


@pytest.mark.parametrize(
    ("language", "question", "target"),
    [("ru", "Открой BIFOLAK ACTIVE CAPSULE", 20), ("uz", "BIFOLAK ACTIVE STICK oching", 21)],
)
async def test_pure_named_open_reuses_current_admission_without_provider(
    test_db, language, question, target
):
    await seed_six_sections(test_db)
    bot = service(test_db)
    result = await bot.turn(request(question, language=language))
    assert result["open_product_id"] == target
    assert [p["id"] for p in result["products"]] == [target]
    assert bot.provider.calls == []


@pytest.mark.parametrize("language", ["ru", "uz"])
@pytest.mark.parametrize("ids", [[20, 21], [21, 20]])
async def test_same_name_prices_label_admitted_forms_in_current_order(test_db, language, ids):
    await seed_six_sections(test_db)
    for pid in ids:
        product = await test_db.get(Product, pid)
        product.name = product.name_uz = "BIFOLAK® ACTIVE"
        product.price = 130000 if pid == 20 else 121000
        product.is_sale = pid == 20
        product.sale_price = 120000 if pid == 20 else None
        product.is_in_stock = pid == 20
    await test_db.commit()
    bot = service(test_db)
    forms = {20: "капсулы", 21: "стики"} if language == "ru" else {20: "kapsula", 21: "stik"}
    question = f"BIFOLAK ACTIVE {forms[ids[0]]} и {forms[ids[1]]}"
    if language == "uz":
        question = f"BIFOLAK ACTIVE {forms[ids[0]]} va {forms[ids[1]]}"
    first = request(question + (" цена" if language == "ru" else " narxi"), language=language)
    reply = await bot.turn(first)
    states = (
        {20: "в наличии", 21: "нет в наличии"}
        if language == "ru"
        else {20: "mavjud", 21: "mavjud emas"}
    )
    assert reply["answer"].splitlines() == [
        f"BIFOLAK® ACTIVE ({forms[pid]}): {120000.0 if pid == 20 else 121000.0} UZS — {states[pid]}"
        for pid in ids
    ]
    assert [p["id"] for p in reply["products"]] == ids
    assert [p["name"] for p in reply["products"]] == ["BIFOLAK® ACTIVE"] * 2
    assert reply["commercial_checked_at"]
    assert reply["open_product_id"] is None
    assert len(bot.provider.calls) == 2


@pytest.mark.parametrize("language", ["ru", "uz"])
@pytest.mark.parametrize("facts", ["composition", "use", "effect", "composition-price"])
async def test_mixed_open_keeps_requested_instruction_path(test_db, language, facts):
    await seed_six_sections(test_db)
    bot = service(test_db)
    first = request(
        "BIFOLAK ACTIVE цена" if language == "ru" else "BIFOLAK ACTIVE narxi", language=language
    )
    shown = await bot.turn(first)
    fact = {
        "ru": {
            "composition": "состав",
            "use": "как применять",
            "effect": "эффект",
            "composition-price": "состав и цена",
        },
        "uz": {
            "composition": "tarkibi",
            "use": "qabul qilish",
            "effect": "taʼsiri",
            "composition-price": "tarkibi va narxi",
        },
    }[language][facts]
    question = (
        f"Открой второй и расскажи {fact}" if language == "ru" else f"Ikkinchisini oching, {fact}"
    )
    turn = request(question, shown["context_token"], first.tab_id, language)
    result = await bot.turn(turn)
    assert result["status"] == "answer"
    assert result["open_product_id"] == 21
    assert [kind for kind, _ in bot.provider.calls] == ["response"] * 4
    context = json.loads(bot.provider.calls[-1][1][-1]["output"])
    expected = (
        [1] if facts.startswith("composition") else [0, 2, 3] if facts == "effect" else [0, 2]
    )
    assert [s["ordinal"] for s in context["sections"]] == expected
    assert [s["passage"] for s in context["sections"]] == [PASSAGES[language][i] for i in expected]
    assert result["sources"]  # Native opening alone cannot satisfy the requested facts.


@pytest.mark.parametrize("language", ["ru", "uz"])
@pytest.mark.parametrize(
    "case", ["ambiguous", "out-of-range", "unknown", "mismatch", "expired", "tab", "language"]
)
async def test_pure_open_rejects_invalid_current_references(test_db, language, case):
    await seed_six_sections(test_db)
    bot = service(test_db)
    first = request(
        "BIFOLAK ACTIVE цена" if language == "ru" else "BIFOLAK ACTIVE narxi", language=language
    )
    shown = await bot.turn(first)
    question = (
        "Открой второй из показанных."
        if language == "ru"
        else "Ko‘rsatilganlardan ikkinchisini oching."
    )
    calls = list(bot.provider.calls)
    turn = request(question, shown["context_token"], first.tab_id, language)
    if case == "ambiguous":
        turn.question = "Открой его" if language == "ru" else "Uni oching"
    elif case == "out-of-range":
        turn.context_token = bot.tokens.issue(first.tab_id, language, [20], "fixture")[0]
    elif case == "unknown":
        turn.question = "Открой UNKNOWN" if language == "ru" else "UNKNOWN oching"
    elif case == "mismatch":
        turn.question = (
            "Открой второй BIFOLAK ACTIVE CAPSULE"
            if language == "ru"
            else "Ikkinchisini BIFOLAK ACTIVE CAPSULE oching"
        )
    elif case == "expired":
        turn.context_token = bot.tokens.issue(
            first.tab_id, language, [20, 21], "fixture", int(time.time()) - 1800
        )[0]
    elif case == "tab":
        turn.tab_id = uuid4()
    elif case == "language":
        turn.language = "uz" if language == "ru" else "ru"
    if case in {"expired", "tab", "language"}:
        with pytest.raises(
            Fault, match="context_expired" if case == "expired" else "context_invalid"
        ):
            await bot.turn(turn)
    else:
        result = await bot.turn(turn)
        assert result["status"] == "clarification"
        assert result["open_product_id"] is None
        assert result["products"] == []
        assert (
            bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[0]
            == []
        )
    assert bot.provider.calls == calls


@pytest.mark.parametrize("language", ["ru", "uz"])
@pytest.mark.parametrize(
    "change",
    [
        "withdraw",
        "inactive",
        "category",
        "commercial",
        "language",
        "profile",
        "incomplete",
        "release",
        "disable",
        "cancel",
        "duplicate",
    ],
)
async def test_pure_open_preserves_admission_and_delivery_controls(
    test_db, monkeypatch, language, change
):
    await seed_six_sections(test_db)
    bot = service(test_db)
    first = request(
        "BIFOLAK ACTIVE цена" if language == "ru" else "BIFOLAK ACTIVE narxi", language=language
    )
    shown = await bot.turn(first)
    calls = list(bot.provider.calls)
    turn = request(
        "Открой второй из показанных."
        if language == "ru"
        else "Ko‘rsatilganlardan ikkinchisini oching.",
        shown["context_token"],
        first.tab_id,
        language,
    )
    snapshot = bot.knowledge.snapshot
    snapshots = 0

    async def changed_snapshot(lang):
        nonlocal snapshots
        snapshots += 1
        if snapshots == 2:
            async with bot.controls.sessions.begin() as db:
                source = await db.get(Source, f"s21{language}")
                if change == "withdraw":
                    source.withdrawn = True
                elif change == "inactive":
                    (await db.get(Product, 21)).is_active = False
                elif change == "category":
                    db.add(Category(id=31, name="Synthetic", slug="synthetic", is_active=False))
                    await db.flush()
                    (await db.get(Product, 21)).category_id = 31
                elif change == "commercial":
                    product = await db.get(Product, 21)
                    product.price, product.is_in_stock = 98765, False
                elif change == "language":
                    source.language = "uz" if language == "ru" else "ru"
                elif change == "profile":
                    (await db.get(Release, "synthetic-release")).profile = "different-profile"
                elif change == "incomplete":
                    await db.execute(
                        delete(Embedding).where(Embedding.section_id == f"s21{language}:1")
                    )
                elif change == "disable":
                    (await db.get(Control, 1)).enabled = False
            if change == "cancel":
                await bot.controls.register(turn.request_id, turn.tab_id, cancel=True)
        current = await snapshot(lang)
        if snapshots == 2 and change == "release":
            current.release = "replacement-approved-release"
        return current

    monkeypatch.setattr(bot.knowledge, "snapshot", changed_snapshot)
    if change == "duplicate":
        owner = await bot.controls.register(turn.request_id, turn.tab_id)
        await bot.controls.finish(owner)
    faults = {
        "profile": "knowledge_unavailable",
        "disable": "disabled",
        "cancel": "cancelled",
        "duplicate": "duplicate_request",
    }
    if change in faults:
        with pytest.raises(Fault, match=faults[change]):
            await bot.turn(turn)
    else:
        result = await bot.turn(turn)
        if change == "commercial":
            assert result["status"] == "answer"
            assert result["open_product_id"] == 21
            assert result["products"][0]["price"] == 98765
            assert result["products"][0]["is_in_stock"] is False
        else:
            assert result["status"] == "limited"
            assert result["products"] == result["sources"] == []
            assert result["open_product_id"] is None
    assert bot.provider.calls == calls
    async with bot.controls.sessions() as db:
        assert not (await db.scalars(select(Run).where(Run.done.is_(False)))).all()


@pytest.mark.parametrize("language", ["ru", "uz"])
async def test_same_name_price_does_not_guess_missing_form_metadata(test_db, language):
    await seed_six_sections(test_db)
    for pid in (20, 21):
        product = await test_db.get(Product, pid)
        product.name = product.name_uz = "BIFOLAK® ACTIVE"
    await test_db.commit()
    bot = service(test_db)
    first = request(
        "BIFOLAK ACTIVE цена" if language == "ru" else "BIFOLAK ACTIVE narxi", language=language
    )
    shown = await bot.turn(first)
    # Graf removal step 4: the withdrawn-source case went with the engine-less routing (a
    # product without its source no longer reaches the product path).
    snapshot = bot.knowledge.snapshot

    async def ambiguous_snapshot(lang):
        evidence = await snapshot(lang)
        evidence.sections.append(evidence.sections[-1] | {"product_id": 21, "form": "capsule"})
        return evidence

    bot.knowledge.snapshot = ambiguous_snapshot
    turn = request(
        "BIFOLAK ACTIVE цена" if language == "ru" else "BIFOLAK ACTIVE narxi",
        shown["context_token"],
        first.tab_id,
        language,
    )
    result = await bot.turn(turn)
    assert result["status"] == "clarification"
    assert result["products"] == []
    assert result["open_product_id"] is None
    assert len(bot.provider.calls) == 4


@pytest.mark.parametrize("language", ["ru", "uz"])
async def test_generic_comparison_supplies_composition_and_complete_cautions(test_db, language):
    await seed_six_sections(test_db)
    provider = CompositionOnlyProposal()
    bot = service(test_db, provider)
    question = (
        "Сравни капсулы и стики BIFOLAK ACTIVE. Покажи в этом порядке."
        if language == "ru"
        else "BIFOLAK ACTIVE kapsula va stiklarini taqqoslang."
    )
    result = await bot.turn(request(question, language=language))
    assert result["status"] == "answer"
    assert [p["id"] for p in result["products"]] == [20, 21]
    wire = json.loads(provider.calls[-1][1][-1]["output"])
    assert [s["ordinal"] for s in wire["sections"]] == [0, 1, 2, 0, 1, 2]
    for section in wire["sections"]:
        assert section["passage"] == PASSAGES[language][section["ordinal"]]
        assert {"source_id", "product_id", "form", "language", "section_title"} <= section.keys()
        assert not {"title", "revision", "pdf_title", "pdf_page"} & section.keys()
    assert result["cautions"] == [f"s{pid}{language}:2" for pid in (20, 21)]
    for pid in (20, 21):
        disclosed = next(s for s in result["sources"] if s["source_id"] == f"s{pid}{language}:2")
        assert disclosed["passage"] == PASSAGES[language][2]
        assert disclosed["revision"] == "synthetic-six-sections"
        assert disclosed["pdf_title"] == "Synthetic instruction"
        assert disclosed["pdf_page"] == 1


class AnswerProposal(ProviderStub):
    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    async def response(self, inputs, last):
        result = await super().response(inputs, last)
        if inputs[-1].get("type") == "function_call_output":
            proposal = json.loads(result["output"][0]["content"][0]["text"])
            proposal["answer"] = self.answer
            proposal["open_product_id"] = None
            result["output"][0]["content"][0]["text"] = json.dumps(proposal)
        return result


@pytest.mark.parametrize(
    ("language", "answer", "accepted"),
    [
        ("uz", "Tarkib yoʻriqnomada koʻrsatilgan.", True),
        ("uz", "Йўриқномада таркиб ва қўллаш чекловлари кўрсатилган.", True),
        ("uz", "Tavsif ва қабул қилиш bandlarында yosh chegarасы турлича ифодаланган.", True),
        ("uz", "Состав указан в инструкции.", False),
        ("uz", "Для детей и взрослых состав указан в инструкции.", False),
        ("uz", "Индивидуальная непереносимость.", False),
        ("ru", "Состав указан в инструкции.", True),
        ("ru", "Composition is described in the instruction.", False),
        ("uz", "Tarkib: 999 mg.", False),
        ("uz", "<b>Tarkib</b>", False),
        ("uz", "Tarkib https://example.test", False),
        ("uz", "Shaxsiy davolash buyuraman.", False),
    ],
)
async def test_answer_language_is_not_inferred_from_script(test_db, language, answer, accepted):
    await seed_six_sections(test_db)
    bot = service(test_db, AnswerProposal(answer))
    turn = request("BIFOLAK ACTIVE CAPSULE tarkib", language=language)
    if accepted:
        result = await bot.turn(turn)
        assert result["status"] == "answer"
        assert result["answer"] == answer
    else:
        with pytest.raises(Fault, match="invalid_answer") as error:
            await bot.turn(turn)
        assert error.value.status == 502
    async with bot.controls.sessions() as db:
        assert not (await db.scalars(select(Run).where(Run.done.is_(False)))).all()


class CatalogueProposal(ProviderStub):
    """Exercise production assembly after a controlled admitted read_context call."""

    def __init__(self, ids, answer=None, source=False, change=None, status="answer", sections=()):
        super().__init__()
        self.ids, self.answer, self.source, self.change = ids, answer, source, change
        self.status, self.sections = status, sections
        self.contexts, self.payload_sizes = [], []

    async def response(self, inputs, last):
        self.calls.append(("response", inputs))
        self.payload_sizes.append(len(encode(Provider.payload(inputs, last))))
        user = json.loads(inputs[0]["content"])
        preliminary = user["preliminary"]
        if inputs[-1].get("type") != "function_call_output" and not (
            self.source and preliminary["sections"]
        ):
            output = [
                {
                    "type": "function_call",
                    "name": "read_context",
                    "call_id": "catalogue-read",
                    "arguments": json.dumps({"product_ids": [], "sections": list(self.sections)}),
                }
            ]
        else:
            context = (
                json.loads(inputs[-1]["output"])
                if inputs[-1].get("type") == "function_call_output"
                else preliminary
            )
            self.contexts.append(context)
            language = json.loads(inputs[0]["content"])["language"]
            answer = self.answer or ("Выбраны стики." if language == "ru" else "Stiklar tanlandi.")
            sources = (
                [
                    s["source_id"]
                    for s in context["sections"]
                    if s["product_id"] in self.ids and s["ordinal"] in {0, 1}
                ]
                if self.source
                else []
            )
            proposal = {
                "status": self.status,
                "answer": answer,
                "source_ids": sources,
                "product_ids": self.ids,
                "open_product_id": self.ids[0] if self.ids else None,
            }
            if self.change:
                await self.change()
            output = [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(proposal),
                        }
                    ],
                }
            ]
        return {
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }


def refinement_turn(bot, language, question, ids=(20, 21)):
    turn = request(question, language=language)
    token, _ = bot.tokens.issue(turn.tab_id, language, list(ids), "catalogue-epoch")
    return turn.model_copy(
        update={
            "context_token": token,
            "history": [
                Pair(
                    question="Сравни капсулы и стики BIFOLAK ACTIVE.",
                    answer="Описание двух форм. " * 100,
                )
            ],
        }
    )


async def seed_catalogue_refinement(db):
    await seed_six_sections(db)
    for pid in (20, 21):
        product = await db.get(Product, pid)
        product.name = "BIFOLAK ACTIVE"
        product.price = 130000 if pid == 20 else 119000
    # Unrequested effect bodies would exceed the existing input bound with history.
    for language in ("ru", "uz"):
        for pid in (20, 21):
            section = await db.get(Section, f"s{pid}{language}:3")
            section.body = "Полное описание действия. " * 200
            section.body_hash = hashlib.sha256(section.body.encode()).hexdigest()
    await db.commit()


@pytest.mark.parametrize(
    ("language", "question"),
    [
        ("ru", "Оставь BIFOLAK ACTIVE дешевле 120000 сум."),
        ("uz", "BIFOLAK ACTIVE 120000 soʻmdan arzon variantlarini qoldir."),
    ],
)
async def test_catalogue_only_refinement_uses_current_facts_and_narrows_token(
    test_db, language, question
):
    await seed_catalogue_refinement(test_db)
    provider = CatalogueProposal([21])
    bot = service(test_db, provider)
    turn = refinement_turn(bot, language, question)
    result = await bot.turn(turn)
    assert result["status"] == "answer"
    assert [p["id"] for p in result["products"]] == [21]
    assert "119000" in result["answer"]
    assert "130000" not in result["answer"]
    assert ("стики" if language == "ru" else "stik") in result["answer"]
    assert result["sources"] == result["cautions"] == []
    assert result["open_product_id"] is None
    assert result["commercial_checked_at"] is not None
    assert bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[
        0
    ] == [21]
    assert provider.contexts[0]["sections"] == []
    assert [p["form"] for p in provider.contexts[0]["products"]] == ["capsule", "stick"]
    assert len(provider.calls) == 2
    assert max(provider.payload_sizes) <= 12000


@pytest.mark.parametrize("language", ["ru", "uz"])
async def test_catalogue_no_match_clears_selection_and_next_bare_reference(test_db, language):
    await seed_catalogue_refinement(test_db)
    provider = CatalogueProposal([])
    bot = service(test_db, provider)
    turn = refinement_turn(bot, language, "Оставь только стики BIFOLAK ACTIVE дешевле 100000 сум.")
    result = await bot.turn(turn)
    assert result["status"] == "answer"
    assert result["products"] == []
    assert result["sources"] == []
    assert result["open_product_id"] is None
    assert (
        bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[0] == []
    )
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    "answer",
    [
        "Цена 119000 сум. Принимать по 2 капсулы в день.",
        "Цена 119000 сум. Лечит заболевание.",
        pytest.param(
            "Цена 119000 сум. Рекомендация для лечения заболевания.",
            id="medical-recommendation",
        ),
        "Цена 119000 сум. Доза 119000 мг.",
        "Цена 119000 сум. 1 mg.",
        "Цена 119000 сум. https://example.test",
        "<b>Цена 119000 сум.</b>",
    ],
)
async def test_discarded_commercial_prose_never_delivers_advice_dose_or_links(test_db, answer):
    await seed_catalogue_refinement(test_db)
    bot = service(test_db, CatalogueProposal([21], answer))
    result = await bot.turn(
        refinement_turn(bot, "ru", "Оставь только стики BIFOLAK ACTIVE дешевле 120000 сум."),
    )
    assert result["status"] == "answer"
    assert result["sources"] == result["cautions"] == []
    assert result["answer"] == "BIFOLAK ACTIVE (стики): 119000.0 UZS — в наличии"
    assert answer not in result["answer"]


async def test_composition_cannot_become_a_source_free_catalogue_answer(test_db):
    await seed_catalogue_refinement(test_db)
    provider = CatalogueProposal(
        [], "Какой раздел инструкции уточнить?", status="limited", sections=("composition",)
    )
    bot = service(test_db, provider)
    result = await bot.turn(refinement_turn(bot, "ru", "BIFOLAK ACTIVE состав"))
    assert result["status"] == "limited"
    assert "10 мг" not in result["answer"]
    assert [s["ordinal"] for s in provider.contexts[0]["sections"]] == [1, 1]


async def test_preliminary_facts_support_no_match_without_redundant_tool_read(test_db):
    await seed_catalogue_refinement(test_db)

    class NoRead(ProviderStub):
        async def response(self, inputs, last):
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "status": "answer",
                                        "answer": "Подходящих товаров нет.",
                                        "source_ids": [],
                                        "product_ids": [],
                                        "open_product_id": None,
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }

    bot = service(test_db, NoRead())
    result = await bot.turn(
        refinement_turn(bot, "ru", "Оставь только стики BIFOLAK ACTIVE дешевле 120000 сум."),
    )
    assert result["status"] == "answer"
    assert result["products"] == []
    assert "Подходящих товаров" in result["answer"]


@pytest.mark.parametrize("change", ["price", "withdraw"])
async def test_catalogue_refinement_rechecks_facts_and_admission_before_delivery(test_db, change):
    await seed_catalogue_refinement(test_db)

    async def mutate():
        if change == "price":
            (await test_db.get(Product, 21)).price = 125000
        else:
            (await test_db.get(Source, "s21ru")).withdrawn = True
        await test_db.commit()

    provider = CatalogueProposal([21], change=mutate)
    bot = service(test_db, provider)
    result = await bot.turn(
        refinement_turn(bot, "ru", "Оставь только стики BIFOLAK ACTIVE дешевле 120000 сум.")
    )
    assert result["status"] == ("clarification" if change == "price" else "limited")
    assert result["products"] == []
    assert result["open_product_id"] is None
    assert "119000" not in result["answer"]


@pytest.mark.parametrize(
    ("language", "question"),
    [
        ("ru", "Оставь BIFOLAK ACTIVE стики по цене до 100000 сум."),
        ("uz", "BIFOLAK ACTIVE stiklaridan narxi 100000 soʻmdan arzonini qoldir."),
    ],
)
async def test_explicit_commercial_filter_cannot_use_plain_price_fast_path(
    test_db, language, question
):
    await seed_catalogue_refinement(test_db)
    provider = CatalogueProposal([])
    bot = service(test_db, provider)
    result = await bot.turn(refinement_turn(bot, language, question))
    assert result["status"] == "answer"
    assert result["products"] == []
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    ("language", "question", "source"),
    [
        ("ru", "Оставь BIFOLAK ACTIVE дешевле 120000 сум.", False),
        ("uz", "BIFOLAK ACTIVE 120000 soʻmdan arzon variantlarini qoldir.", False),
        ("ru", "Оставь BIFOLAK ACTIVE дешевле 120000 сум. Расскажи ограничения.", True),
        ("ru", "Оставь BIFOLAK ACTIVE по цене ниже 120000 сум.", False),
        pytest.param(
            "ru",
            "Оставь BIFOLAK ACTIVE до 120000 сум. Расскажи ограничения.",
            True,
            id="mixed-bare-currency",
        ),
    ],
)
async def test_explicit_filtered_subset_is_not_expanded_to_named_candidates(
    test_db, language, question, source
):
    await seed_catalogue_refinement(test_db)
    provider = CatalogueProposal([21], source=source, sections=("use",) if source else ())
    bot = service(test_db, provider)
    turn = refinement_turn(bot, language, question)
    result = await bot.turn(turn)
    assert [p["id"] for p in result["products"]] == [21]
    assert "130000" not in result["answer"]
    assert len(provider.calls) == (1 if source else 2)
    assert bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[
        0
    ] == [21]
    if source:
        assert result["cautions"] == [f"s21{language}:2"]
        assert (
            next(s["passage"] for s in result["sources"] if s["source_id"] == f"s21{language}:2")
            == PASSAGES[language][2]
        )


async def test_spaced_commercial_proposal_is_discarded_before_delivered_text_checks(test_db):
    await seed_catalogue_refinement(test_db)
    provider = CatalogueProposal([21], "120 000 soʻmdan arzon stik — 119 000 soʻm.")
    bot = service(test_db, provider)
    turn = refinement_turn(bot, "uz", "Faqat 120000 soʻmdan arzon BIFOLAK ACTIVE stiklarni qoldir.")
    result = await bot.turn(turn)
    assert result["status"] == "answer"
    assert [p["id"] for p in result["products"]] == [21]
    assert "119000" in result["answer"]
    assert "120 000" not in result["answer"]


class DirectProposal(ProviderStub):
    """A model decision over the real preliminary evidence, without a redundant read."""

    def __init__(self, proposal):
        super().__init__()
        self.proposal = proposal

    async def response(self, inputs, last):
        self.calls.append(("response", inputs))
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(self.proposal)}],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }


@pytest.mark.parametrize(
    ("language", "question"),
    [
        ("ru", "Ну, мне для общего понимания: ACTIVE сейчас сколько стоит?"),
        ("uz", "Shunchaki bilmoqchiman, ACTIVE hozir narxi qancha ekan?"),
        ("ru", "ACTIVE: не состав, а только цена, если можно."),
    ],
)
async def test_preliminary_facts_and_full_question_survive_arbitrary_neutral_words(
    test_db, language, question
):
    await seed(test_db)
    provider = DirectProposal(
        {
            "status": "answer",
            "answer": "119 000 UZS; 999 mg https://example.test",
            "source_ids": [],
            "product_ids": [20],
            "open_product_id": 20,
        }
    )
    bot = service(test_db, provider)
    turn = request(question, language=language)
    result = await bot.turn(turn)
    assert result["status"] == "answer"
    assert "200" in result["answer"]
    assert "999" not in result["answer"]
    assert "https" not in result["answer"]
    assert result["open_product_id"] is None
    assert [p["id"] for p in result["products"]] == [20]
    wire = json.loads(provider.calls[0][1][0]["content"])
    assert wire["question"] == question
    assert wire["candidate_origin"] == "explicit"
    assert wire["preliminary"]["products"][0]["price"] == 200
    assert len(provider.calls) == 1


@pytest.mark.parametrize("status", ["clarification", "limited", "refused"])
async def test_explicit_nonanswer_status_is_not_commercial_no_match_or_old_selection(
    test_db, status
):
    await seed(test_db)
    provider = DirectProposal(
        {
            "status": status,
            "answer": "Уточните, о каком сроке вы спрашиваете?",
            "source_ids": [],
            "product_ids": [],
            "open_product_id": None,
        }
    )
    bot = service(test_db, provider)
    turn = request("А какая будет цена ACTIVE через месяц?")
    result = await bot.turn(turn)
    assert result["status"] == status
    assert result["answer"] == "Уточните, о каком сроке вы спрашиваете?"
    assert result["products"] == result["sources"] == []
    assert result["open_product_id"] is None
    assert (
        bot.tokens.read(turn.model_copy(update={"context_token": result["context_token"]}))[0] == []
    )
    assert len(provider.calls) == 1


async def test_named_missing_section_supplies_full_cautions_without_question_cue(test_db):
    await seed_six_sections(test_db)
    provider = CatalogueProposal(
        [20], "Раздел указан в инструкции.", source=True, sections=("use",)
    )
    bot = service(test_db, provider)
    result = await bot.turn(request("BIFOLAK ACTIVE CAPSULE: расскажи про утренний вариант."))
    wire = json.loads(provider.calls[0][1][0]["content"])
    assert wire["preliminary"]["sections"] == []
    assert [s["ordinal"] for s in provider.contexts[0]["sections"]] == [0, 2]
    assert result["cautions"] == ["s20ru:2"]
    assert (
        next(s["passage"] for s in result["sources"] if s["source_id"] == "s20ru:2")
        == PASSAGES["ru"][2]
    )


@pytest.mark.parametrize(
    "question",
    [
        "BIFOLAK ACTIVE CAPSULE: цена и состав для общего понимания?",
        "BIFOLAK ACTIVE CAPSULE: не цена, а состав для общего понимания?",
    ],
)
async def test_mixed_price_composition_uses_supplied_source_and_server_price(test_db, question):
    await seed_six_sections(test_db)
    provider = DirectProposal(
        {
            "status": "answer",
            "answer": "Состав: компонент 10 мг на порцию.",
            "source_ids": ["s20ru:1"],
            "product_ids": [20],
            "open_product_id": None,
        }
    )
    bot = service(test_db, provider)
    result = await bot.turn(request(question))
    assert result["answer"] == "Состав: компонент 10 мг на порцию."
    assert result["products"][0]["price"] == 200
    assert result["products"][0]["currency"] == "UZS"
    assert [s["source_id"] for s in result["sources"]] == ["s20ru:1"]
    assert len(provider.calls) == 1


async def test_inconsistent_supported_sources_do_not_restore_excluded_candidates(test_db):
    await seed_six_sections(test_db)
    provider = DirectProposal(
        {
            "status": "answer",
            "answer": "Состав указан в инструкции.",
            "source_ids": ["s20ru:1", "s21ru:1"],
            "product_ids": [21],
            "open_product_id": None,
        }
    )
    bot = service(test_db, provider)
    result = await bot.turn(request("BIFOLAK ACTIVE состав"))
    assert result["status"] == "clarification"
    assert result["products"] == result["sources"] == []
