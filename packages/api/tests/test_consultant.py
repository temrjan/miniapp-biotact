"""Deterministic public boundaries; all database cases use the disposable test fixture."""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.consultant.context import Tokens, resolve
from src.consultant.contracts import Fault, Turn
from src.consultant.control import Controls
from src.consultant.knowledge import Knowledge
from src.consultant.models import (
    Budget,
    Control,
    Membership,
    Release,
    Run,
    Section,
    Source,
)
from src.consultant.provider import EMBEDDING_PROFILE, PROFILE
from src.consultant.router import runtime
from src.consultant.service import Consultant
from src.main import create_app
from src.products.models import Product

KEY = "synthetic-consultant-key-with-32-bytes"


def request(question="DERMACOMPLEX", token=None, tab=None, language="ru"):
    return Turn.model_validate_json(
        json.dumps(
            {
                "request_id": str(uuid4()),
                "tab_id": str(tab or uuid4()),
                "context_revision": 1,
                "language": language,
                "question": question,
                "history": [],
                "page_product_id": 20,
                "context_token": token,
            }
        )
    )


class ProviderStub:
    def __init__(self, source_only=True, delay=None):
        self.calls = []
        self.source_only = source_only
        self.delay = delay
        self.clarify = False
        self.started = asyncio.Event()

    async def embedding(self, query):
        self.calls.append(("embedding", query))
        return [1.0] + [0.0] * 1535, {"total_tokens": 4}

    async def response(self, inputs, last):
        self.calls.append(("response", inputs))
        self.started.set()
        if self.delay:
            await self.delay.wait()
        user = json.loads(inputs[0]["content"])
        if self.clarify:
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
                                        "status": "clarification",
                                        "answer": "Уточните название товара."
                                        if user["language"] == "ru"
                                        else "Mahsulot nomini aniqlashtiring.",
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
        preliminary = user["preliminary"]
        commercial = (
            bool(re.search(r"цен|стоим|стоит|narx|qancha", user["question"], re.I))
            and not preliminary["sections"]
        )
        if inputs[-1].get("type") != "function_call_output":
            names = ["description", "composition", "use", "effects", "quality", "storage"]
            sections = list(dict.fromkeys(names[s["ordinal"]] for s in preliminary["sections"]))
            if not sections and not commercial:
                sections = ["description", "composition", "use"]
            output = [
                {
                    "type": "function_call",
                    "name": "read_context",
                    "call_id": "synthetic-call",
                    "arguments": json.dumps({"product_ids": [], "sections": sections}),
                }
            ]
        else:
            context = json.loads(inputs[-1]["output"])
            user = json.loads(inputs[0]["content"])
            proposal = {
                "status": "answer",
                "answer": "Состав указан в инструкции."
                if user["language"] == "ru"
                else "Tarkib yoʻriqnomada koʻrsatilgan.",
                "source_ids": [] if commercial else [context["sections"][0]["source_id"]],
                "product_ids": []
                if self.source_only and not commercial
                else [p["id"] for p in context["products"]],
                "open_product_id": context["products"][0]["id"],
            }
            output = [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(proposal)}],
                }
            ]
        return {
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }


async def seed(db):
    db.add_all(
        [
            Product(
                id=4,
                name="DERMACOMPLEX",
                slug="dermacomplex",
                price=100,
                is_active=True,
                is_in_stock=False,
            ),
            Product(
                id=20, name="ACTIVE", slug="active", price=200, is_active=True, is_in_stock=True
            ),
        ]
    )
    await db.flush()
    db.add(
        Release(
            id="synthetic-release",
            manifest={"sources": ["s4ru", "s4uz", "s20ru", "s20uz"]},
            profile=PROFILE,
            approval="synthetic-owner-approval",
            prepared=True,
        )
    )
    await db.flush()
    control = await db.get(Control, 1)
    if control is None:
        db.add(Control(id=1, enabled=True, active="synthetic-release"))
    else:
        control.enabled, control.active = True, "synthetic-release"
    for pid, alias in [(4, "DERMACOMPLEX"), (20, "ACTIVE")]:
        for language in ["ru", "uz"]:
            sid = f"s{pid}{language}"
            db.add(
                Source(
                    id=sid,
                    product_id=pid,
                    language=language,
                    provenance={
                        "title": alias,
                        "form": "capsule",
                        "revision": "fixture-1",
                        "pdf_title": "Synthetic instruction",
                        "aliases": [alias],
                        "section_count": 2,
                    },
                    withdrawn=False,
                )
            )
            await db.flush()
            db.add(Membership(release_id="synthetic-release", source_id=sid))
            for n, caution in [(0, False), (1, True)]:
                body = (
                    "Состав: компонент 10 мг."
                    if not caution
                    else "Индивидуальная непереносимость. Консультация врача."
                )
                identity = f"{sid}:{n}"
                db.add(
                    Section(
                        id=identity,
                        source_id=sid,
                        ordinal=n,
                        body=body,
                        body_hash=hashlib.sha256(body.encode()).hexdigest(),
                        heading="Composition" if not caution else "Caution",
                        caution=caution,
                        pdf_page=1,
                    )
                )
                await db.flush()
                await db.execute(
                    text(
                        "INSERT INTO consultant_embeddings(section_id,profile,vector) VALUES (:id,:profile,CAST(:vector AS vector))"
                    ),
                    {
                        "id": identity,
                        "profile": EMBEDDING_PROFILE,
                        "vector": str([1.0] + [0.0] * 1535),
                    },
                )
    await db.commit()


def service(db, provider=None):
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    controls = Controls(sessions, KEY, True)
    return Consultant(controls, Knowledge(sessions), provider or ProviderStub(), Tokens(KEY))


@pytest.mark.parametrize("language", ["ru", "uz"])
async def test_explicit_source_only_change_cards_token_and_followup(test_db, language):
    await seed(test_db)
    bot = service(test_db)
    first = request("ACTIVE", language=language)
    a = await bot.turn(first)
    assert [p["id"] for p in a["products"]] == [20]
    second = request(
        "Теперь DERMACOMPLEX" if language == "ru" else "Endi DERMACOMPLEX",
        a["context_token"],
        first.tab_id,
        language,
    )
    b = await bot.turn(second)
    assert [p["id"] for p in b["products"]] == [4]
    assert b["products"][0]["is_in_stock"] is False
    assert b["open_product_id"] is None
    third = request(
        "цена" if language == "ru" else "narxi", b["context_token"], first.tab_id, language
    )
    c = await bot.turn(third)
    assert [p["id"] for p in c["products"]] == [4]
    assert "100" in c["answer"]
    assert "200" not in c["answer"]
    assert len(bot.provider.calls) == 6  # S4: ordinary prices also use the whole-question path


async def test_unknown_clears_page_fallback_and_no_reference_has_no_provider(test_db):
    await seed(test_db)
    bot = service(test_db)
    bot.provider.clarify = True
    first = request("Теперь UNKNOWN")
    a = await bot.turn(first)
    b = await bot.turn(request("состав", a["context_token"], first.tab_id))
    assert a["products"] == b["products"] == []
    assert b["status"] == "clarification"
    assert len(bot.provider.calls) == 1


def test_tokens_and_resolver_are_bound_to_current_order():
    aliases = {4: ["DERMACOMPLEX"], 20: ["ACTIVE"]}
    assert resolve("открой второй", aliases, [4, 20], None, False).ids == [20]
    assert resolve("DERMACOMPLEX второй", aliases, [4, 20], None, False).clarify
    assert resolve("цена", aliases, [], 20, False).clarify
    turn = request()
    tokens = Tokens(KEY)
    token, _ = tokens.issue(turn.tab_id, "ru", [4, 20], "epoch", 100)
    bound = turn.model_copy(update={"context_token": token})
    assert tokens.read(bound, 1899)[0] == [4, 20]
    with pytest.raises(Fault, match="context_expired"):
        tokens.read(bound, 1900)
    with pytest.raises(Fault, match="context_invalid"):
        tokens.read(bound.model_copy(update={"tab_id": uuid4()}), 101)
    with pytest.raises(ValidationError):
        Turn.model_validate_json(
            turn.model_dump_json().replace(
                '"history":[]', '"history":[{"question":"old","answer":"old"}]'
            )
        )


async def test_shared_dedup_cancel_parallel_runs_and_23_hour_boundary(test_db):
    await seed(test_db)
    a, b = service(test_db).controls, service(test_db).controls
    rid, tab = uuid4(), uuid4()
    owner = await a.register(rid, tab)
    with pytest.raises(Fault, match="duplicate_request"):
        await b.register(rid, uuid4())
    # No global lease: another request runs beside the first and finishes on its own.
    parallel = await b.register(uuid4(), tab)
    assert parallel != owner
    await a.alive(owner)
    await b.alive(parallel)
    await b.finish(parallel)
    await a.alive(owner)
    await b.register(rid, uuid4(), cancel=True)
    await a.alive(owner)  # wrong tab cannot cancel
    await b.register(rid, tab, cancel=True)
    with pytest.raises(Fault, match="cancelled"):
        await a.alive(owner)
    await a.finish(owner)
    with pytest.raises(Fault, match="cancelled"):
        await b.register(rid, tab)
    tombstone, tombtab = uuid4(), uuid4()
    await a.register(tombstone, tombtab, cancel=True)
    with pytest.raises(Fault, match="cancelled"):
        await b.register(tombstone, tombtab)
    # Turn expiry does not expire the ID registration.
    async with a.sessions.begin() as db:
        row = await db.get(Run, a.digest("request:" + str(rid)))
        row.cancelled = False
        row.created = datetime.now(UTC) - timedelta(seconds=76)
    with pytest.raises(Fault, match="duplicate_request"):
        await b.register(rid, tab)
    async with a.sessions.begin() as db:
        row = await db.get(Run, a.digest("request:" + str(rid)))
        row.created = datetime.now(UTC) - timedelta(hours=23, seconds=1)
    newer = await b.register(rid, tab)
    assert newer != owner
    await a.finish(owner)
    await b.alive(newer)  # old owner cannot finish a new registration


async def test_unreported_usage_counts_the_call_but_no_tokens_after_cleanup(test_db):
    await seed(test_db)
    controls = service(test_db).controls
    owner = await controls.register(uuid4(), uuid4())
    reservation = await controls.reserve(owner, "responses")
    async with controls.sessions() as db:
        budget = await db.get(Budget, datetime.now(UTC).date())
        # Counting only: no tokens are reserved in advance.
        assert (budget.attempts, budget.input_tokens, budget.output_tokens) == (1, 0, 0)
    async with controls.sessions.begin() as db:
        from src.consultant.models import Reservation

        row = await db.get(Reservation, reservation)
        row.created = datetime.now(UTC) - timedelta(hours=24)
        run = await db.scalar(select(Run).where(Run.owner == owner))
        run.created = datetime.now(UTC) - timedelta(hours=24)
        control = await db.get(Control, 1)
        control.enabled = False
    await controls.cleanup()
    controls = service(test_db).controls  # a new worker cannot add usage for a purged call
    await controls.reconcile(reservation, owner, 1, 1)
    async with controls.sessions() as db:
        budget = await db.get(Budget, datetime.now(UTC).date())
        assert (budget.attempts, budget.received, budget.input_tokens, budget.output_tokens) == (
            1,
            0,
            0,
            0,
        )
        assert await db.scalar(select(Run).where(Run.owner == owner)) is None


async def test_parallel_workers_count_every_call_and_its_actual_usage(test_db):
    await seed(test_db)
    sessions = service(test_db).controls.sessions
    a, b = Controls(sessions, KEY, True), Controls(sessions, KEY, True)
    owner = await a.register(uuid4(), uuid4())

    async def call(controls, kind, inputs, outputs):
        identity = await controls.reserve(owner, kind)
        await controls.reconcile(identity, owner, inputs, outputs)

    await asyncio.gather(
        *(call(a if n % 2 else b, "responses", 100_000, 30_000) for n in range(10)),
        call(a, "embeddings", 9, 0),
    )
    async with sessions() as db:
        budget = await db.get(Budget, datetime.now(UTC).date())
        assert (
            budget.attempts,
            budget.received,
            budget.input_tokens,
            budget.output_tokens,
            budget.embedding_tokens,
        ) == (11, 11, 1_000_000, 300_000, 9)


async def test_delayed_provider_cancel_releases_connection_and_suppresses_answer(test_db):
    await seed(test_db)
    release = asyncio.Event()
    provider = ProviderStub(delay=release)
    bot = service(test_db, provider)
    turn = request()
    task = asyncio.create_task(bot.turn(turn))
    await asyncio.wait_for(provider.started.wait(), 5)
    # A separate DB connection can acquire the shared control row during provider I/O.
    async with bot.controls.sessions.begin() as db:
        await db.execute(text("SET LOCAL lock_timeout='500ms'"))
        assert await db.get(Control, 1, with_for_update=True) is not None
    await bot.controls.register(turn.request_id, turn.tab_id, cancel=True)
    with pytest.raises(Fault, match="cancelled"):
        await asyncio.wait_for(task, 3)
    assert len(provider.calls) == 1


async def test_http_privacy_origin_hidden_products_and_schema(test_db, caplog):
    await seed(test_db)
    app = create_app()
    bot = service(test_db)
    app.state.consultant = bot
    app.state.consultant_origins = ["https://shop.test"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://shop.test"
    ) as client:
        turn = request()
        raw = json.loads(turn.model_dump_json())
        raw["question"] = "sentinel-private@example.test"
        response = await client.post("/api/v1/consultant/turns", json=raw)
        assert response.status_code == 422
        assert response.json()["code"] == "sensitive_input"
        assert "sentinel" not in response.text
        assert "sentinel" not in caplog.text
        assert response.headers["cache-control"] == "no-store"
        assert bot.provider.calls == []
        response = await client.post(
            "/api/v1/consultant/turns",
            json=json.loads(turn.model_dump_json()),
            headers={"Origin": "https://evil.test"},
        )
        assert response.status_code == 400
        async with bot.controls.sessions.begin() as db:
            p = await db.get(Product, 4)
            p.is_active = False
        response = await client.post(
            "/api/v1/consultant/products/refresh", json={"product_ids": [4, 20], "language": "ru"}
        )
        assert response.status_code == 200
        assert response.json()["unavailable_ids"] == [4]
        assert [p["id"] for p in response.json()["products"]] == [20]
        response = await client.post(
            "/api/v1/consultant/turns",
            json={**json.loads(turn.model_dump_json()), "secret": "sentinel-private"},
        )
        assert response.status_code == 422
        assert "sentinel" not in response.text
        response = await client.post(
            "/api/v1/consultant/turns",
            content=b"x" * 65537,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


async def test_operator_activation_cas_withdrawal_and_rollback(test_db, monkeypatch):
    from src.consultant import operator

    await seed(test_db)
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    monkeypatch.setattr(operator, "async_session_maker", sessions)
    async with sessions.begin() as db:
        for name in ("candidate-a", "candidate-b"):
            db.add(
                Release(
                    id=name,
                    manifest={"sources": ["s4ru", "s4uz", "s20ru", "s20uz"]},
                    profile=PROFILE,
                    approval="fixture-approval",
                    prepared=True,
                )
            )
            await db.flush()
            db.add_all(
                [
                    Membership(release_id=name, source_id=s)
                    for s in ["s4ru", "s4uz", "s20ru", "s20uz"]
                ]
            )
    results = await asyncio.gather(
        operator.activate("candidate-a", "synthetic-release"),
        operator.activate("candidate-b", "synthetic-release"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, Fault) for r in results) == 1
    async with sessions() as db:
        active = (await db.get(Control, 1)).active
    await operator.activate(active, "ignored-for-idempotency")
    await operator.withdraw("s4ru")
    snapshot = await Knowledge(sessions).snapshot("ru")
    assert 4 not in snapshot.products
    assert 20 in snapshot.products
    with pytest.raises(Fault, match="binding_unavailable"):
        await operator.activate(active, active)


async def test_operator_preparation_is_exact_resumable_and_does_not_activate(
    test_db, tmp_path, monkeypatch
):
    import re

    from src.consultant import operator

    await seed(test_db)
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    monkeypatch.setattr(operator, "async_session_maker", sessions)
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"synthetic-pdf-provenance")
    pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    bodies = [
        "Описание без личной рекомендации.",
        "Состав: компонент 10 мг.",
        "Индивидуальная непереносимость. Обратитесь к врачу.",
    ]
    original = (
        "---\nproduct_name: ACTIVE\nproduct_form: capsule\nlanguage: ru\nsource_pdf: synthetic.pdf\nsource_page: 1\nsource_sha256: "
        + pdf_hash
        + "\n---\n"
        + "\n".join("===СЕКЦИЯ===\n" + body + "\n===/СЕКЦИЯ===" for body in bodies)
    )
    body_path = tmp_path / "synthetic.txt"
    body_path.write_text(original)
    sections = [
        {
            "start": m.start(1),
            "end": m.end(1),
            "sha256": hashlib.sha256(m[1].encode()).hexdigest(),
            "title": f"section-{i}",
            "page": 1,
            "caution": i == 2,
        }
        for i, m in enumerate(re.finditer(r"===СЕКЦИЯ===\n(.*?)\n===/СЕКЦИЯ===", original, re.S))
    ]
    manifest = {
        "profile": PROFILE,
        "approval": "fixture-approval",
        "sources": [
            {
                "product_id": 20,
                "product_slug": "active",
                "language": "ru",
                "body_path": str(body_path),
                "body_sha256": hashlib.sha256(body_path.read_bytes()).hexdigest(),
                "pdf_path": str(pdf),
                "pdf_sha256": pdf_hash,
                "title": "ACTIVE",
                "form": "capsule",
                "revision": "fixture-2",
                "pdf_title": "synthetic.pdf",
                "aliases": ["ACTIVE"],
                "sections": sections,
            }
        ],
    }
    path = tmp_path / "manifest.json"
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    identity = hashlib.sha256(raw).hexdigest()

    class Embeddings:
        calls = 0
        failing = True

        async def call(self, route, payload):
            self.calls += 1
            assert route == "embeddings"
            assert payload["input"] in bodies
            if self.failing:
                raise Fault("provider_unavailable")
            return {"data": [{"embedding": [1.0] + [0.0] * 1535}]}

    provider = Embeddings()
    with pytest.raises(Fault, match="approval_or_profile_mismatch"):
        await operator.prepare(path, identity, "wrong", provider, False)
    assert provider.calls == 0
    with pytest.raises(Fault, match="provider_unavailable"):
        await operator.prepare(path, identity, "fixture-approval", provider, False)
    async with sessions() as db:
        assert (await db.get(Control, 1)).active == "synthetic-release"
    provider.failing = False
    with pytest.raises(Fault, match="uncertain_embedding_requires_operator"):
        await operator.prepare(path, identity, "fixture-approval", provider, False)
    assert provider.calls == 1
    assert await operator.prepare(path, identity, "fixture-approval", provider, True) == identity
    assert provider.calls == 4
    await operator.prepare(path, identity, "fixture-approval", provider, False)
    assert provider.calls == 4
    await operator.activate(identity, "synthetic-release")
    assert 20 in (await Knowledge(sessions).snapshot("ru")).products
    assert not (await Knowledge(sessions).snapshot("uz")).products
    body_path.write_text(original + "changed")
    with pytest.raises(Fault, match="provenance_mismatch"):
        await operator.prepare(path, identity, "fixture-approval", provider, False)


async def test_usage_offswitch_and_unknown_hidden_subject(test_db):
    await seed(test_db)
    bot = service(test_db)
    async with bot.controls.sessions.begin() as db:
        product = await db.get(Product, 4)
        product.is_active = False
    reply = await bot.turn(request("DERMACOMPLEX"))
    assert reply["status"] == "limited"
    assert reply["products"] == []
    assert bot.provider.calls == []
    owner = await bot.controls.register(uuid4(), uuid4())
    controls = bot.controls
    reservation = await controls.reserve(owner, "responses")
    await controls.reserve(owner, "responses")
    await controls.reconcile(reservation, owner, 100, 2851)
    await controls.reconcile(reservation, owner, 1, 1)  # usage is added once per call
    async with controls.sessions.begin() as db:
        budget = await db.get(Budget, datetime.now(UTC).date())
        assert budget.attempts == 2
        assert budget.received == 1
        assert budget.input_tokens == 100
        assert budget.output_tokens == 2851
        control = await db.get(Control, 1)
        control.enabled = False
    with pytest.raises(Fault, match="disabled"):
        await controls.alive(owner)


async def test_exact_non_sliding_boundary_and_cancel_race(test_db, monkeypatch):
    await seed(test_db)
    a, b = service(test_db).controls, service(test_db).controls
    original = Controls.locked
    instant = datetime(2026, 1, 1, tzinfo=UTC)

    async def locked(self, db):
        row, _ = await original(self, db)
        return row, instant

    monkeypatch.setattr(Controls, "locked", locked)
    rid, tab = uuid4(), uuid4()
    owner = await a.register(rid, tab, cancel=True)
    instant += timedelta(hours=23) - timedelta(microseconds=1)
    await b.register(rid, tab, cancel=True)
    with pytest.raises(Fault, match="cancelled"):
        await a.register(rid, tab)
    instant += timedelta(microseconds=1)
    fresh = await b.register(rid, tab)
    assert fresh != owner
    await a.finish(owner)
    await b.alive(fresh)
    await b.finish(fresh)
    # Concurrent independent transactions converge to a cancelled registration,
    # whichever acquires the shared row first; neither can create a second owner.
    race, race_tab = uuid4(), uuid4()
    results = await asyncio.gather(
        a.register(race, race_tab),
        b.register(race, race_tab, cancel=True),
        return_exceptions=True,
    )
    owners = [result for result in results if isinstance(result, str)]
    assert len(set(owners)) == 1
    with pytest.raises(Fault, match="cancelled"):
        await a.alive(owners[0])
    async with a.sessions() as db:
        row = await db.get(Run, a.digest("request:" + str(rid)))
        assert row.created == instant
    instant += timedelta(hours=23, minutes=5)
    async with a.sessions.begin() as db:
        (await db.get(Control, 1)).enabled = False
    await b.cleanup()
    async with a.sessions() as db:
        assert await db.get(Run, a.digest("request:" + str(rid))) is None


async def test_past_daily_totals_never_refuse_another_call(test_db):
    await seed(test_db)
    controls = service(test_db).controls
    async with controls.sessions.begin() as db:
        db.add(
            Budget(
                day=datetime.now(UTC).date(),
                attempts=1_000_000,
                received=1_000_000,
                input_tokens=2_000_000_000,
                output_tokens=2_000_000_000,
                embedding_tokens=2_000_000_000,
            )
        )
    owner = await controls.register(uuid4(), uuid4())
    await controls.reserve(owner, "responses")
    await controls.reserve(owner, "embeddings")
    async with controls.sessions() as db:
        budget = await db.get(Budget, datetime.now(UTC).date())
        assert budget.attempts == 1_000_002


class ParallelProvider(ProviderStub):
    """Holds every response until all expected turns are inside the provider at once."""

    def __init__(self, expected):
        super().__init__()
        self.expected, self.inflight, self.peak = expected, 0, 0
        self.together = asyncio.Event()

    async def response(self, inputs, last):
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        if self.inflight == self.expected:
            self.together.set()
        try:
            await asyncio.wait_for(self.together.wait(), 10)
            return await super().response(inputs, last)
        finally:
            self.inflight -= 1


@pytest.mark.parametrize("visitors", [2, 10])
async def test_concurrent_visitors_are_answered_in_parallel(test_db, visitors):
    await seed(test_db)
    provider = ParallelProvider(visitors)
    bot = service(test_db, provider)
    replies = await asyncio.gather(*(bot.turn(request()) for _ in range(visitors)))
    assert provider.peak == visitors
    assert [r["status"] for r in replies] == ["answer"] * visitors
    assert all([p["id"] for p in r["products"]] == [4] for r in replies)
    async with bot.controls.sessions() as db:
        assert not (await db.scalars(select(Run).where(Run.done.is_(False)))).all()


async def test_many_requests_from_one_network_are_all_served(test_db):
    await seed(test_db)
    app = create_app()
    bot = service(test_db)
    app.state.consultant = bot
    app.state.consultant_origins = ["https://shop.test"]
    # More than the former 6/minute and 60/hour turn and 60/minute auxiliary allowances.
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("198.51.100.7", 4000)),
        base_url="https://shop.test",
    ) as client:
        for _ in range(61):
            turn = json.loads(request().model_dump_json())
            response = await client.post("/api/v1/consultant/turns", json=turn)
            assert response.status_code == 200, response.text
            response = await client.post(
                "/api/v1/consultant/cancel",
                json={"request_id": turn["request_id"], "tab_id": turn["tab_id"]},
            )
            assert response.status_code == 204
            response = await client.post(
                "/api/v1/consultant/products/refresh",
                json={"product_ids": [20], "language": "ru"},
            )
            assert response.status_code == 200


def secret(value):
    return SimpleNamespace(get_secret_value=lambda: value)


@pytest.mark.parametrize("provider_key", ["provider-key-fixture", ""])
async def test_runtime_without_budget_settings_serves_turns(test_db, provider_key):
    """No daily settings exist; only a missing provider key still keeps the consultant off."""
    await seed(test_db)
    config = SimpleNamespace(
        consultant_provider_key=secret(provider_key),
        consultant_protection_key=secret(KEY),
        consultant_graf_key=secret(""),
        consultant_origins=[],
    )
    app = SimpleNamespace(state=SimpleNamespace())
    async with runtime(app, config, async_sessionmaker(test_db.bind, expire_on_commit=False)):
        consultant = app.state.consultant
        consultant.provider = ProviderStub()  # no outbound network
        if provider_key:
            reply = await consultant.turn(request())
            assert reply["status"] == "answer"
            assert len(consultant.provider.calls) == 2
        else:
            with pytest.raises(Fault, match="disabled"):
                await consultant.turn(request())


def test_settings_ignore_former_daily_limit_lines(tmp_path):
    from src.config import Settings

    env = tmp_path / ".env"
    env.write_text(
        "DATABASE_URL=postgresql+asyncpg://fixture:fixture@127.0.0.1:1/fixture\n"
        "SECRET_KEY=fixture-only\n"
        "CONSULTANT_DAILY_CALLS=240\n"
        "CONSULTANT_DAILY_INPUT_TOKENS=2880000\n"
        "CONSULTANT_DAILY_OUTPUT_TOKENS=491520\n"
        "CONSULTANT_DAILY_EMBEDDING_TOKENS=0\n"
        "CONSULTANT_TRUSTED_PROXY_PEERS=[]\n"
    )
    loaded = Settings(_env_file=env)
    assert not hasattr(loaded, "consultant_daily_calls")
    assert not hasattr(loaded, "consultant_trusted_proxy_peers")


async def test_long_history_reaches_the_provider_whole(test_db):
    await seed(test_db)
    bot = service(test_db)
    first = request("ACTIVE")
    shown = await bot.turn(first)
    # Above the former 4-pair and 12,000-character history bounds and the 32,000-byte payload
    # bound. Earlier questions up to 2,000 characters remain valid history.
    question = ("Что написано в инструкции ACTIVE о составе? " * 30)[:1000].strip()
    history = [
        {"question": f"Вопрос {n}: " + "состав и применение " * 99, "answer": "Ответ. " * 1000}
        for n in range(10)
    ]
    raw = json.loads(request(question, shown["context_token"], first.tab_id).model_dump_json())
    raw["history"] = history
    turn = Turn.model_validate_json(json.dumps(raw))
    assert max(len(p.question) for p in turn.history) > 1000
    reply = await bot.turn(turn)
    assert reply["status"] == "answer"
    sent = bot.provider.calls[-2][1]
    wire = json.loads(sent[0]["content"])
    assert wire["question"] == turn.question
    assert len(wire["history"]) == 10
    assert len(json.dumps(sent, ensure_ascii=False).encode()) > 32000


@pytest.mark.parametrize(("length", "status"), [(1000, 200), (1001, 422)])
async def test_question_is_capped_at_1000_characters(test_db, length, status):
    await seed(test_db)
    app = create_app()
    bot = service(test_db)
    app.state.consultant = bot
    app.state.consultant_origins = ["https://shop.test"]
    question = ("ACTIVE " + "я" * length)[:length]  # Cyrillic: characters, not bytes
    raw = json.loads(request().model_dump_json())
    raw["question"] = question
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://shop.test"
    ) as client:
        response = await client.post("/api/v1/consultant/turns", json=raw)
    assert response.status_code == status
    if status == 422:
        assert response.json()["code"] == "invalid_input"
        assert bot.provider.calls == []
        with pytest.raises(ValidationError):
            Turn.model_validate_json(json.dumps(raw))
