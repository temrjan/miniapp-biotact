"""Real K2/notice fifth-attempt lifecycle with isolated flock and database."""

import asyncio
import importlib.util
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Literal, cast

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from src.employee_referrals import maintenance, worker
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.returns import validated
from src.employee_referrals.transport import LaneConfig
from tests.test_employee_referral_order_capture import Checkout
from tests.test_employee_referral_order_capture import checkout as checkout

SOURCE = os.environ.get("ADMISSION_TEST_SOURCE", "")
pytestmark = pytest.mark.skipif(
    not SOURCE,
    reason="Paired admission proof requires ADMISSION_TEST_SOURCE; see coordinated referral deployment runbook",
)
CANONICAL = Path(SOURCE)


@pytest.fixture
def real_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("shop_admission_r2", CANONICAL)
    assert spec is not None
    assert spec.loader is not None
    library = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = library
    spec.loader.exec_module(library)
    library.trusted_directory.__defaults__ = (os.geteuid(),)
    library.trusted_fd.__kwdefaults__ = {"uid": os.geteuid()}
    library.read_json.__defaults__ = (os.geteuid(),)
    root = tmp_path / "control"
    root.mkdir(mode=0o755)
    (root / "delivery.lock").touch(mode=0o644)
    (root / "participants" / "shop").mkdir(mode=0o755, parents=True)
    library.__dict__["CONTROL_ROOT"] = root
    monkeypatch.setattr(maintenance, "_loaded", library)
    return library


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_actual_fifth_attempt_keeps_lock_through_durable_completion(
    checkout: Checkout, real_provider: ModuleType, lane: Literal["k2", "notice"]
) -> None:
    # The existing fixture drives mounted checkout/payment and real job producers.
    create = cast(Callable[[dict[str, object]], Awaitable[httpx.Response]], checkout.create)
    response = await create(
        {
            "items": [{"product_id": 39, "quantity": 1}],
            "referral_token": "AAAAAAAAAAAAAAAAAAAAAA",
            "points_to_apply": 10000,
        }
    )
    assert response.status_code == 201, response.text
    assert response.json()["payment_status"] == "paid"
    sessions = cast(async_sessionmaker[AsyncSession], checkout.sessions)
    model = K2Delivery if lane == "k2" else NoticeDelivery
    async with sessions.begin() as session:
        job = await session.scalar(select(model))
        source = await session.scalar(select(PaidOutbox))
        assert job is not None
        assert source is not None
        job.attempt_count = 4
        job_id = job.id
        raw, digest = source.body, source.body_sha256
        snapshot = validated(source)
    stages: list[str] = []

    def locked(stage: str) -> None:
        with pytest.raises(TimeoutError), real_provider.exclusive("delivery.lock", timeout=0):
            pytest.fail(f"drain overtook {stage}")
        stages.append(stage)

    def before_commit(session: Session) -> None:
        if any(
            isinstance(row, model) and row.state in {"sent", "acknowledged"}
            for row in session.dirty
        ):
            locked("before_commit")

    def after_commit(_session: Session) -> None:
        if "before_commit" in stages:
            locked("after_commit")

    def respond(request: httpx.Request) -> httpx.Response:
        locked("transport")
        if lane == "k2":
            assert request.content == raw
            body = {
                "schema_version": 1,
                "replayed": False,
                "receipt": {
                    "event_id": snapshot.event_id,
                    "source": snapshot.source,
                    "order_id": snapshot.order_id,
                    "order_version": snapshot.order_version,
                    "source_position": snapshot.source_position,
                    "body_sha256": digest,
                    "outcome": "applied",
                    "received_at": snapshot.model_dump(mode="json")["source_recorded_at"],
                },
                "projection": {
                    "order_version": snapshot.order_version,
                    "accounting_state": "confirmed",
                    "has_conflict": False,
                },
            }
        else:
            body = {"ok": True, "result": {"message_id": 77}}
        return httpx.Response(
            200,
            stream=httpx.ByteStream(json.dumps(body).encode()),
            headers={"cache-control": "no-store", "content-type": "application/json"},
        )

    config = (
        LaneConfig("true", "https://portal.example", "0" * 64)
        if lane == "k2"
        else LaneConfig("true", token="12345:synthetic", chat="-12345")
    )
    event.listen(Session, "before_commit", before_commit)
    event.listen(Session, "after_commit", after_commit)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await worker.deliver_one(sessions, client, lane, config)
    finally:
        event.remove(Session, "before_commit", before_commit)
        event.remove(Session, "after_commit", after_commit)
    assert stages == ["transport", "before_commit", "after_commit"]
    with real_provider.exclusive("delivery.lock", timeout=0):
        async with sessions() as session:
            finished = await session.get(model, job_id)
            retained = await session.scalar(select(PaidOutbox))
            assert finished is not None
            assert retained is not None
            assert finished.state == ("acknowledged" if lane == "k2" else "sent")
            assert finished.attempt_count == 5
            assert finished.leased_until is None
            assert retained.body == raw
            assert retained.body_sha256 == digest


async def test_actual_worker_start_registers_live_identity_when_lanes_disabled(
    real_provider: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import settings

    monkeypatch.setattr(settings, "employee_referral_k2_sender_enabled", "false")
    monkeypatch.setattr(settings, "employee_referral_notices_enabled", "false")
    written = asyncio.Event()
    original = real_provider.write_json
    loop = asyncio.get_running_loop()

    def observe_write(path: Path, data: dict[str, object]) -> None:
        original(path, data)
        if path.name == "worker.json":
            loop.call_soon_threadsafe(written.set)

    monkeypatch.setattr(real_provider, "write_json", observe_write)
    with real_provider.exclusive("delivery.lock", timeout=0):
        task = asyncio.create_task(worker.command(["run"]))
        try:
            try:
                await asyncio.wait_for(written.wait(), timeout=3)
            except TimeoutError:
                raise AssertionError("Worker startup did not register before admission") from None
            data = real_provider.read_json(
                real_provider.CONTROL_ROOT / "participants/shop/worker.json"
            )
            assert data == {**asdict(real_provider.Identity.current()), "version": 1}
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
