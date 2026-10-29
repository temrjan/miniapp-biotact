"""Operator-only derived knowledge lifecycle; never exposed through HTTP.

Manifest: profile, approval, sources. Each source specifies product_id, language,
body_path/body_sha256, pdf_path/pdf_sha256, title, form, revision, pdf_title,
aliases and sections (start/end UTF-8 character offsets, sha256, title, page,
caution). The hash names the exact manifest; source paths remain outside DB.
Prepare requires --expected-manifest and --approval, supplied by the operator.
An uncertain embedding is not retried implicitly: rerun needs --resume-uncertain.
"""

import argparse
import asyncio
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import Field
from sqlalchemy import select, text

from src.consultant.contracts import Fault, Language, ProductId, Strict
from src.consultant.models import Control, Embedding, Membership, Release, Section, Source
from src.consultant.provider import EMBEDDING_PROFILE, PROFILE, Provider
from src.database import async_session_maker
from src.products.service import get_product_by_id


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class SectionInput(Strict):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1)
    page: int = Field(gt=0)
    caution: bool


class SourceInput(Strict):
    product_id: ProductId
    product_slug: str = Field(min_length=1)
    language: Language
    body_path: str
    body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_path: str
    pdf_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1)
    form: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    pdf_title: str = Field(min_length=1)
    aliases: list[str] = Field(min_length=1)
    sections: list[SectionInput] = Field(min_length=1, max_length=6)


class Manifest(Strict):
    profile: str
    approval: str = Field(min_length=1)
    sources: list[SourceInput] = Field(min_length=1, max_length=26)


def validate_files(
    raw: bytes, expected: str, approval: str
) -> tuple[Manifest, list[tuple[SourceInput, list[str]]]]:
    if digest(raw) != expected:
        raise Fault("manifest_mismatch", 422)
    manifest = Manifest.model_validate_json(raw)
    if manifest.profile != PROFILE or manifest.approval != approval or not approval:
        raise Fault("approval_or_profile_mismatch", 422)
    identities: set[tuple[int, str]] = set()
    verified = []
    for source in manifest.sources:
        identity = (source.product_id, source.language)
        if identity in identities:
            raise Fault("duplicate_binding", 422)
        identities.add(identity)
        body = Path(source.body_path).read_bytes()
        if (
            digest(body) != source.body_sha256
            or digest(Path(source.pdf_path).read_bytes()) != source.pdf_sha256
        ):
            raise Fault("provenance_mismatch", 422)
        decoded = body.decode("utf-8")
        original = list(re.finditer(r"===СЕКЦИЯ===\n(.*?)\n===/СЕКЦИЯ===", decoded, re.S))
        if len(original) != len(source.sections) or any(
            (match.start(1), match.end(1)) != (section.start, section.end)
            for match, section in zip(original, source.sections, strict=True)
        ):
            raise Fault("original_section_boundary_mismatch", 422)
        if not decoded.startswith("---\n") or len(decoded.split("---", 2)) != 3:
            raise Fault("provenance_mismatch", 422)
        metadata = dict(re.findall(r"^([a-z_][a-z0-9_]*): (.+)$", decoded.split("---", 2)[1], re.M))
        if (
            metadata.get("language") != source.language
            or (
                metadata.get("product_form") is not None and metadata["product_form"] != source.form
            )
            or metadata.get("source_sha256") != source.pdf_sha256
            or metadata.get("source_pdf") != source.pdf_title
            or any(str(section.page) != metadata.get("source_page") for section in source.sections)
        ):
            raise Fault("provenance_mismatch", 422)
        passages = []
        previous_end = -1
        for section in source.sections:
            passage = decoded[section.start : section.end]
            if (
                section.start < previous_end
                or section.end > len(decoded)
                or not passage.strip()
                or len(passage.encode()) > 8191
                or digest(passage.encode()) != section.sha256
            ):
                raise Fault("section_mismatch", 422)
            previous_end = section.end
            passages.append(passage)
        verified.append((source, passages))
    return manifest, verified


async def validate_release(db: Any, release: Release) -> None:
    if release.profile != PROFILE or not release.approval:
        raise Fault("approval_or_profile_mismatch", 422)
    sources = list(
        (
            await db.scalars(
                select(Source)
                .join(Membership, Membership.source_id == Source.id)
                .where(Membership.release_id == release.id)
            )
        ).all()
    )
    if len(sources) != len(release.manifest["sources"]):
        raise Fault("incomplete_release", 422)
    for source in sources:
        product = await get_product_by_id(db, source.product_id)
        if (
            source.withdrawn
            or product is None
            or not product.is_active
            or product.slug != source.provenance.get("product_slug", product.slug)
            or (product.category is not None and not product.category.is_active)
        ):
            raise Fault("binding_unavailable", 422)
        sections = list(
            (await db.scalars(select(Section).where(Section.source_id == source.id))).all()
        )
        embeddings = list(
            (
                await db.scalars(
                    select(Embedding.section_id)
                    .join(Section, Section.id == Embedding.section_id)
                    .where(Section.source_id == source.id, Embedding.profile == EMBEDDING_PROFILE)
                )
            ).all()
        )
        if len(sections) != source.provenance["section_count"] or len(embeddings) != len(sections):
            raise Fault("incomplete_release", 422)


async def prepare(
    path: Path, expected: str, approval: str, provider: Provider, resume: bool
) -> str:
    manifest, verified = await asyncio.to_thread(
        lambda: validate_files(path.read_bytes(), expected, approval)
    )
    # All external files/approvals are checked before any provider work.
    async with async_session_maker.begin() as db:
        for source, _ in verified:
            product = await get_product_by_id(db, source.product_id)
            if (
                product is None
                or not product.is_active
                or product.slug != source.product_slug
                or (product.category and not product.category.is_active)
            ):
                raise Fault("binding_unavailable", 422)
        release = await db.get(Release, expected)
        if release is None:
            # Only public metadata enters DB; never canonical server paths.
            public_manifest = manifest.model_dump(exclude={"sources"}) | {
                "sources": [s.body_sha256 for s, _ in verified],
                "uncertain": [],
            }
            release = Release(
                id=expected,
                manifest=public_manifest,
                profile=PROFILE,
                approval=approval,
                prepared=False,
            )
            db.add(release)
            await db.flush()
        if release.manifest.get("uncertain") and not resume:
            raise Fault("uncertain_embedding_requires_operator", 409)
        for source, passages in verified:
            sid = digest((source.body_sha256 + str(source.product_id) + source.language).encode())
            stored = await db.get(Source, sid)
            metadata = source.model_dump(
                exclude={"body_path", "pdf_path", "sections", "product_id", "language"}
            ) | {"section_count": len(passages)}
            if stored is not None and stored.provenance != metadata:
                raise Fault("immutable_source_conflict", 409)
            if stored is None:
                db.add(
                    Source(
                        id=sid,
                        product_id=source.product_id,
                        language=source.language,
                        provenance=metadata,
                        withdrawn=False,
                    )
                )
                await db.flush()
                for index, (section, body) in enumerate(
                    zip(source.sections, passages, strict=True)
                ):
                    db.add(
                        Section(
                            id=f"{sid}:{index}",
                            source_id=sid,
                            ordinal=index,
                            body=body,
                            body_hash=section.sha256,
                            heading=section.title,
                            caution=section.caution,
                            pdf_page=section.page,
                        )
                    )
            if await db.get(Membership, (expected, sid)) is None:
                db.add(Membership(release_id=expected, source_id=sid))
    async with async_session_maker() as db:
        sections = list(
            (
                await db.scalars(
                    select(Section)
                    .join(Membership, Membership.source_id == Section.source_id)
                    .where(Membership.release_id == expected)
                )
            ).all()
        )
        missing = [
            (s.id, s.body)
            for s in sections
            if await db.get(Embedding, (s.id, EMBEDDING_PROFILE)) is None
        ]
    for identity, body in missing:
        async with async_session_maker.begin() as db:
            release = await db.get(Release, expected, with_for_update=True)
            assert release is not None
            release.manifest = release.manifest | {
                "uncertain": list(set(release.manifest.get("uncertain", [])) | {identity})
            }
        # No DB transaction or connection while embedding. Source sections may be longer than questions.
        result = await provider.call(
            "embeddings",
            {
                "model": "text-embedding-3-small",
                "dimensions": 1536,
                "input": body,
                "encoding_format": "float",
            },
        )
        vector = result.get("data", [{}])[0].get("embedding")
        if (
            not isinstance(vector, list)
            or len(vector) != 1536
            or not any(vector)
            or any(type(n) not in {int, float} or not math.isfinite(n) for n in vector)
        ):
            raise Fault("embedding_dimension", 422)
        async with async_session_maker.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO consultant_embeddings(section_id,profile,vector) VALUES (:id,:profile,CAST(:vector AS vector)) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": identity,
                    "profile": EMBEDDING_PROFILE,
                    "vector": json.dumps(vector, allow_nan=False),
                },
            )
            release = await db.get(Release, expected, with_for_update=True)
            assert release is not None
            release.manifest = release.manifest | {
                "uncertain": [i for i in release.manifest.get("uncertain", []) if i != identity]
            }
    async with async_session_maker.begin() as db:
        release = await db.get(Release, expected, with_for_update=True)
        assert release is not None
        await validate_release(db, release)
        release.prepared = True
    return expected


async def activate(identity: str, prior: str | None) -> None:
    async with async_session_maker.begin() as db:
        control = await db.get(Control, 1, with_for_update=True)
        release = await db.get(Release, identity)
        if control is None or release is None or not release.prepared:
            raise Fault("incomplete_release", 422)
        await validate_release(db, release)
        if control.active == identity:
            return
        if control.active != prior:
            raise Fault("activation_conflict", 409)
        control.active = identity


async def withdraw(identity: str) -> None:
    async with async_session_maker.begin() as db:
        await db.get(Control, 1, with_for_update=True)
        source = await db.get(Source, identity, with_for_update=True)
        if source is None:
            raise Fault("unknown_source", 422)
        source.withdrawn = True


async def main() -> None:
    import src.models_registry  # noqa: F401 - standalone operator needs all ORM relationships

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "validate",
            "prepare",
            "activate",
            "rollback",
            "withdraw",
            "disable",
            "enable",
            "cleanup",
        ],
    )
    parser.add_argument("identity", nargs="?")
    parser.add_argument("--expected-prior")
    parser.add_argument("--expected-manifest")
    parser.add_argument("--approval")
    parser.add_argument("--resume-uncertain", action="store_true")
    args = parser.parse_args()
    from src.config import settings
    from src.consultant.control import Controls

    if args.action in {"activate", "rollback"}:
        await activate(args.identity, args.expected_prior)
    elif args.action == "withdraw":
        await withdraw(args.identity)
    elif args.action == "cleanup":
        await Controls(async_session_maker, "", False).cleanup()
    elif args.action in {"enable", "disable"}:
        async with async_session_maker.begin() as db:
            control = await db.get(Control, 1, with_for_update=True)
            if control is None:
                raise Fault("schema_unavailable")
            if args.action == "enable":
                if (
                    len(settings.consultant_protection_key.get_secret_value()) < 32
                    or not settings.consultant_provider_key.get_secret_value()
                ):
                    raise Fault("configuration_unavailable")
                release = await db.get(Release, control.active) if control.active else None
                if release is None:
                    raise Fault("knowledge_unavailable")
                await validate_release(db, release)
            control.enabled = args.action == "enable"
    else:
        if not args.identity or not args.expected_manifest or not args.approval:
            parser.error("manifest path, expected hash and approval required")
        path = Path(args.identity)
        if args.action == "validate":
            await asyncio.to_thread(
                lambda: validate_files(path.read_bytes(), args.expected_manifest, args.approval)
            )
        else:
            async with httpx.AsyncClient(
                timeout=60, follow_redirects=False, trust_env=False
            ) as client:
                await prepare(
                    path,
                    args.expected_manifest,
                    args.approval,
                    Provider(client, settings.consultant_provider_key.get_secret_value()),
                    args.resume_uncertain,
                )
    print("completed")


if __name__ == "__main__":
    asyncio.run(main())
