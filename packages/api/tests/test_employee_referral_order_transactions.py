"""Real commit boundaries, cancellation and competing K1 transactions."""

import asyncio
import copy
import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import database
from src.employee_referrals import capture
from src.employee_referrals.models import CampaignReplica, ReferralIdentity
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.schemas import parse_snapshot, utc_text
from src.employee_referrals.service import apply_snapshot
from src.loyalty import service as loyalty
from src.notifications.service import telegram_notifier
from src.orders import service as orders
from src.orders.models import Order, OrderStatus
from src.orders.schemas import OrderCreate
from tests.test_employee_referral_k1_contract import encoded
from tests.test_employee_referral_order_capture import TOKEN
from tests.test_employee_referral_order_capture import checkout as checkout


async def wait_for_lock(h):
    """Observe a real PostgreSQL waiter; a timing-only sleep is not a race proof."""
    async with h.sessions() as observer:
        for _ in range(200):
            waiting = await observer.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND cardinality(pg_blocking_pids(pid)) > 0"
                )
            )
            if waiting:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("Expected a real PostgreSQL lock waiter")


def revision(h, change):
    data = copy.deepcopy(h.data)
    data["event_id"] = str(uuid4())
    if change != "renewal":
        data["campaign"]["content_revision"] = 2
    if change == "end":
        data["campaign"]["state"] = "ended"
        data["campaign"]["ended_at"] = data["issued_at"]
    elif change == "omission":
        data["participants"] = []
    elif change == "renewal":
        data["issued_at"] = utc_text(h.now - timedelta(seconds=1))
    return data


@pytest.mark.parametrize("change", ["end", "omission", "renewal"])
async def test_receiver_lock_first_is_observed_by_waiting_capture(checkout, change):
    h = checkout
    data = revision(h, change)
    raw = encoded(data)
    async with h.sessions() as writer:
        await apply_snapshot(writer, parse_snapshot(raw), hashlib.sha256(raw).hexdigest())
        pending = asyncio.create_task(h.create())
        try:
            await wait_for_lock(h)
            assert not pending.done()
            await writer.commit()
            assert (await asyncio.wait_for(pending, 3)).status_code == 201
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert (await h.state())[1:3] == ((1, 1) if change == "renewal" else (0, 0))


@pytest.mark.parametrize("change", ["end", "omission", "renewal"])
async def test_capture_lock_first_preserves_fact_before_receiver_changes(
    checkout, monkeypatch, change
):
    h = checkout
    reached, release = asyncio.Event(), asyncio.Event()
    original = capture.store_snapshot

    async def held(*args):
        await original(*args)
        reached.set()
        await release.wait()

    monkeypatch.setattr(capture, "store_snapshot", held)
    order = asyncio.create_task(h.create())
    await asyncio.wait_for(reached.wait(), 3)
    receiver = asyncio.create_task(h.publish(revision(h, change)))
    try:
        await wait_for_lock(h)
        assert not receiver.done()
        release.set()
        assert (await asyncio.wait_for(order, 3)).status_code == 201
        await asyncio.wait_for(receiver, 3)
    finally:
        release.set()
        for task in (order, receiver):
            if not task.done():
                task.cancel()
        await asyncio.gather(order, receiver, return_exceptions=True)
    assert await h.state() == (1, 1, 1, 100000)


async def test_lease_expires_while_waiting_and_clock_is_read_after_lock(checkout):
    h = checkout
    async with h.sessions() as writer:
        replica = await writer.scalar(
            select(CampaignReplica).where(CampaignReplica.campaign_id == 1).with_for_update()
        )
        now = await writer.scalar(select(func.clock_timestamp()))
        expiry = now + timedelta(milliseconds=200)
        replica.issued_at = expiry - timedelta(seconds=60)
        replica.valid_until = expiry
        await writer.flush()
        pending = asyncio.create_task(h.create())
        try:
            await wait_for_lock(h)
            while await writer.scalar(select(func.clock_timestamp())) < expiry:  # noqa: ASYNC110 — poll external DB clock
                await asyncio.sleep(0.01)
            await writer.commit()
            assert (await asyncio.wait_for(pending, 3)).status_code == 201
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert await h.state() == (1, 0, 0, 100000)


async def test_existing_orm_identity_is_refreshed_after_receiver_update(checkout):
    h = checkout
    async with h.sessions() as session:
        stale_replica = await session.get(CampaignReplica, 1)
        stale_identity = await session.get(ReferralIdentity, TOKEN)
        order = await orders.create_order(
            session, OrderCreate.model_validate(h.body(referral_token=None)), 1
        )
        await h.publish(revision(h, "omission"))
        assert stale_identity.in_current_registry
        await capture.capture_order(session, order, TOKEN)
        assert not stale_identity.in_current_registry
        assert stale_replica.content_revision == 2
        await session.commit()
    assert await h.state() == (1, 0, 0, 100000)


@pytest.mark.parametrize("points", [0, 5000, 10000])
@pytest.mark.parametrize("stage", ["snapshot", "loyalty", "order-commit"])
async def test_precommit_failure_rolls_back_related_facts(checkout, monkeypatch, points, stage):
    h = checkout
    if stage == "snapshot":
        real = capture.store_snapshot

        async def fail_snapshot(*args):
            await real(*args)
            # An actual PostgreSQL failure after snapshot SQL has executed.
            await args[0].execute(text("SELECT 1/0"))

        monkeypatch.setattr(capture, "store_snapshot", fail_snapshot)
    elif stage == "loyalty":
        if points == 0:
            # Card has no loyalty call; its equivalent late service fault is after capture.
            real = capture.store_snapshot

            async def fail_card(*args):
                await real(*args)
                raise RuntimeError("synthetic pre-commit failure")

            monkeypatch.setattr(capture, "store_snapshot", fail_card)
        else:
            real = loyalty.reserve_points

            async def fail_loyalty(*args, **kwargs):
                await real(*args, **kwargs)
                raise RuntimeError("synthetic post-reserve failure")

            monkeypatch.setattr(loyalty, "reserve_points", fail_loyalty)
    else:
        order_commit = 2 if points else 1

        class RefuseOrderCommit(AsyncSession):
            commits = 0

            async def commit(self):
                self.commits += 1
                if self.commits == order_commit:
                    raise RuntimeError("synthetic confirmed refusal before commit")
                await super().commit()

        monkeypatch.setattr(
            database,
            "async_session_maker",
            async_sessionmaker(h.engine, class_=RefuseOrderCommit, expire_on_commit=False),
        )
    if stage == "order-commit" and points != 10000:
        # Card/partial dependency teardown happens after response start.
        with pytest.raises(RuntimeError, match="confirmed refusal"):
            await h.create(h.body(points_to_apply=points))
    else:
        response = await h.create(h.body(points_to_apply=points))
        expected = 503 if stage == "order-commit" and points == 10000 else 500
        assert response.status_code == expected
        if expected == 503:
            assert response.json() == {"detail": {"code": "referral_paid_unavailable"}}
    assert await h.state() == (0, 0, 0, 100000)
    async with h.sessions() as reader:
        assert await reader.scalar(select(func.count()).select_from(PaidOutbox)) == 0


@pytest.mark.parametrize("points", [0, 5000, 10000])
async def test_lost_commit_confirmation_reads_durable_truth(checkout, monkeypatch, points):
    h = checkout
    order_commit = 2 if points else 1

    class LostConfirmation(AsyncSession):
        commits = 0

        async def commit(self):
            self.commits += 1
            await super().commit()
            if self.commits == order_commit:
                raise RuntimeError("synthetic lost commit confirmation")

    monkeypatch.setattr(
        database,
        "async_session_maker",
        async_sessionmaker(h.engine, class_=LostConfirmation, expire_on_commit=False),
    )
    if points != 10000:
        with pytest.raises(RuntimeError, match="lost commit confirmation"):
            await h.create(h.body(points_to_apply=points))
    else:
        response = await h.create(h.body(points_to_apply=points))
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "referral_paid_unavailable"}}
    assert await h.state() == (1, 1, 1, 90000 if points == 10000 else 100000)
    async with h.sessions() as reader:
        assert await reader.scalar(select(func.count()).select_from(PaidOutbox)) == (
            1 if points == 10000 else 0
        )


async def test_failure_of_late_dependency_commit_preserves_full_points_fact(checkout, monkeypatch):
    h = checkout

    class LateFailure(AsyncSession):
        commits = 0

        async def commit(self):
            self.commits += 1
            if self.commits == 3:
                raise RuntimeError("synthetic late dependency failure")
            await super().commit()

    monkeypatch.setattr(
        database,
        "async_session_maker",
        async_sessionmaker(h.engine, class_=LateFailure, expire_on_commit=False),
    )
    with pytest.raises(RuntimeError, match="late dependency failure"):
        await h.create(h.body(points_to_apply=10000))
    assert await h.state() == (1, 1, 1, 90000)


@pytest.mark.parametrize("points", [0, 5000, 10000])
async def test_cancel_before_order_commit_rolls_back(checkout, monkeypatch, points):
    h = checkout
    ready = asyncio.Event()
    real = capture.store_snapshot

    async def held(*args):
        await real(*args)
        ready.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(capture, "store_snapshot", held)
    task = asyncio.create_task(h.create(h.body(points_to_apply=points)))
    await asyncio.wait_for(ready.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await h.state() == (0, 0, 0, 100000)


async def test_cancel_during_telegram_keeps_committed_full_points_order(checkout, monkeypatch):
    h = checkout
    ready = asyncio.Event()

    async def held(**_kwargs):
        ready.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(telegram_notifier, "notify_payment_success", held)
    task = asyncio.create_task(h.create(h.body(points_to_apply=10000)))
    await asyncio.wait_for(ready.wait(), 3)
    assert await h.state() == (1, 1, 1, 90000)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await h.state() == (1, 1, 1, 90000)


async def test_new_order_rollback_does_not_undo_independent_cleanup(checkout, monkeypatch):
    h = checkout
    first = (await h.create(h.body(referral_token=None, points_to_apply=5000))).json()["id"]
    async with h.sessions.begin() as writer:
        row = await writer.get(Order, first)
        row.created_at = h.now - timedelta(hours=1)
    real = capture.store_snapshot

    async def fail(*args):
        await real(*args)
        raise RuntimeError("synthetic new order failure")

    monkeypatch.setattr(capture, "store_snapshot", fail)
    assert (await h.create(h.body(points_to_apply=5000))).status_code == 500
    assert await h.state() == (1, 0, 0, 100000)
    async with h.sessions() as reader:
        assert (await reader.get(Order, first)).status == OrderStatus.CANCELLED
