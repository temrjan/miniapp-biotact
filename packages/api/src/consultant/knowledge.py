"""Public admission and detached evidence. No account/order queries."""

import re
from dataclasses import dataclass
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from src.consultant.contracts import Fault, Language
from src.consultant.models import Control, Embedding, Membership, Release, Section, Source
from src.consultant.provider import EMBEDDING_PROFILE, PROFILE
from src.products.models import Product
from src.products.router import localize_product

PUBLIC_FIELDS = frozenset(
    [
        "id",
        "name",
        "price",
        "old_price",
        "sale_price",
        "currency",
        "is_sale",
        "is_in_stock",
        "is_purchasable",
        "requires_size",
        "stock_quantity",
        "available_sizes",
        "category_id",
        "image_url",
        "images",
        "description",
        "advantages",
        "video_url",
        "additional_info_title",
        "additional_info",
        "disclaimer",
    ]
)
ADVICE = re.compile(
    r"огранич|противопоказ|предостереж|предупрежд|неперенос|примен|использ|"
    r"при[её]м|принимать|возраст|\bлет\b|эффект|действ|беремен|"
    r"cheklov|qarshi\s+ko[ʻ‘’ʼ']?rsat|ogohlantir|murosasiz|qo[ʻ‘’ʼ']?ll|"
    r"qabul|yosh|ta[ʻ‘’ʼ']?sir|homilador",
    re.I,
)
EFFECT = re.compile(r"эффект|действ|ta[ʻ‘’ʼ']?sir", re.I)
AGE = re.compile(r"возраст|\bлет\b|yosh", re.I)
COMPARISON = re.compile(r"сравн|отлич|taqqos|solishtir|farq", re.I)


@dataclass
class Evidence:
    release: str
    products: dict[int, dict[str, Any]]
    aliases: dict[int, list[str]]
    sections: list[dict[str, Any]]

    def form(self, product_id: int) -> str | None:
        forms = {s.get("form") for s in self.sections if s["product_id"] == product_id}
        if len(forms) != 1:
            return None
        form = forms.pop()
        if not isinstance(form, str) or not form.strip():
            return None
        return form.strip()

    def form_label(self, product_id: int, language: Language) -> str | None:
        form = self.form(product_id)
        if form is None:
            return None
        return {
            "capsule": "капсулы" if language == "ru" else "kapsula",
            "stick": "стики" if language == "ru" else "stik",
        }.get(form, form)

    def labels(self, ids: list[int], language: Language) -> list[str] | None:
        products = [self.products[i] for i in ids]
        forms = [self.form_label(i, language) for i in ids]
        labels = [
            f"{p['name']} ({form})" if form else p["name"]
            for p, form in zip(products, forms, strict=True)
        ]
        if len({p["name"] for p in products}) < len(products) and (
            not all(forms) or len(set(labels)) != len(labels)
        ):
            return None
        return labels

    def context(
        self, ids: list[int], question: str, sections: list[str] | None = None
    ) -> dict[str, Any]:
        names = ("description", "composition", "use", "effects", "quality", "storage")
        ordinals = {names.index(name) for name in sections} if sections is not None else set()
        # Lexical cues only add preliminary evidence; requested names select independently.
        if sections is None:
            if re.search(r"состав|tarkib|ингредиент", question, re.I):
                ordinals.add(1)
            if re.search(r"хран|saqla", question, re.I):
                ordinals.add(5)
            if re.search(r"качеств|сертифик|sifat", question, re.I):
                ordinals.add(4)
            if COMPARISON.search(question):
                ordinals.update({0, 1, 2})
            if ADVICE.search(question):
                ordinals.update({0, 2})
            if EFFECT.search(question):
                ordinals.add(3)
        advice = bool(ordinals & {2, 3})
        if advice:
            ordinals.update({0, 2})
        if AGE.search(question) and (sections is None or advice):
            # Age boundaries also occur in composition tables.
            ordinals.add(1)
            ordinals.update({0, 2})
            advice = True
        selected = [
            s
            for i in ids
            for s in self.sections
            if s["product_id"] == i and (s["ordinal"] in ordinals or (s["caution"] and advice))
        ]
        # Tool evidence needs only public commercial facts, not duplicate long
        # catalogue descriptions, media or fiscal metadata.
        fields = {
            "id",
            "name",
            "price",
            "sale_price",
            "is_sale",
            "currency",
            "is_in_stock",
            "is_purchasable",
        }
        return {
            "status": "ok" if ids else "needs_reference",
            "products": [
                {
                    **{k: v for k, v in self.products[i].items() if k in fields},
                    "form": self.form(i),
                }
                for i in ids
            ],
            "sections": selected,
        }


def public_section(section: Section, source: Source) -> dict[str, Any]:
    meta = source.provenance
    return {
        "source_id": section.id,
        "product_id": source.product_id,
        "title": meta["title"],
        "form": meta["form"],
        "language": source.language,
        "revision": meta["revision"],
        "section_title": section.heading,
        "pdf_title": meta["pdf_title"],
        "pdf_page": section.pdf_page,
        "passage": section.body,
        "caution": section.caution,
        "ordinal": section.ordinal,
    }


class Knowledge:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def snapshot(self, language: Language) -> Evidence:
        async with self.sessions() as db:
            control = await db.get(Control, 1)
            release = await db.get(Release, control.active) if control and control.active else None
            if (
                release is None
                or not release.prepared
                or release.profile != PROFILE
                or not release.approval
            ):
                raise Fault("knowledge_unavailable")
            rows = (
                await db.execute(
                    select(Source, Product)
                    .join(Membership, Membership.source_id == Source.id)
                    .join(Product, Product.id == Source.product_id)
                    .options(joinedload(Product.category))
                    .where(
                        Membership.release_id == release.id,
                    )
                )
            ).all()
            products: dict[int, dict[str, Any]] = {}
            aliases: dict[int, list[str]] = {}
            sources: dict[str, Source] = {}
            for source, product in rows:
                aliases.setdefault(product.id, []).extend(source.provenance["aliases"])
                if (
                    source.language != language
                    or source.withdrawn
                    or not product.is_active
                    or product.slug != source.provenance.get("product_slug", product.slug)
                    or (product.category is not None and not product.category.is_active)
                ):
                    continue
                projection = localize_product(product, language)
                products[product.id] = jsonable_encoder(
                    {key: projection[key] for key in PUBLIC_FIELDS}
                )
                sources[source.id] = source
            sections = list(
                (
                    await db.scalars(
                        select(Section)
                        .join(Embedding, Embedding.section_id == Section.id)
                        .where(
                            Section.source_id.in_(sources), Embedding.profile == EMBEDDING_PROFILE
                        )
                        .order_by(Section.source_id, Section.ordinal)
                    )
                ).all()
            )
            complete = {
                sid
                for sid, source in sources.items()
                if sum(s.source_id == sid for s in sections) == source.provenance["section_count"]
            }
            admitted = {sources[sid].product_id for sid in complete}
            return Evidence(
                release.id,
                {i: p for i, p in products.items() if i in admitted},
                aliases,
                [
                    public_section(s, sources[s.source_id])
                    for s in sections
                    if s.source_id in complete
                ],
            )

    async def rank(self, evidence: Evidence, vector: list[float]) -> list[int]:
        # Exact scan over admitted sections; no approximate index or post-ranking admission.
        ids = [s["source_id"] for s in evidence.sections]
        if not ids:
            return []
        async with self.sessions() as db:
            result = await db.execute(
                text(
                    "SELECT section_id FROM consultant_embeddings WHERE profile=:profile AND section_id = ANY(:ids) ORDER BY vector <=> CAST(:vector AS vector), section_id LIMIT 6"
                ),
                {"profile": EMBEDDING_PROFILE, "ids": ids, "vector": str(vector)},
            )
            ranked = list(result.scalars())
        mapping = {s["source_id"]: s["product_id"] for s in evidence.sections}
        return list(dict.fromkeys(mapping[s] for s in ranked))[:2]
