"""Short serialized transactions; never hold a connection during external I/O."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.consultant.contracts import Fault
from src.consultant.models import Budget, Control, Rate, Reservation, Run


class Controls:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        key: str,
        limits: tuple[int, int, int, int],
    ) -> None:
        self.sessions, self.key, self.limits = sessions, key.encode(), limits

    def digest(self, value: str) -> str:
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    async def locked(self, db: AsyncSession) -> tuple[Control, datetime]:
        row = await db.scalar(select(Control).where(Control.id == 1).with_for_update())
        if row is None:
            raise Fault("disabled")
        now = await db.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        return row, now.astimezone(UTC)

    async def rate(self, network: str, lane: str) -> None:
        async with self.sessions.begin() as db:
            _, now = await self.locked(db)
            key = self.digest("network:" + network)
            dates = list(
                (
                    await db.scalars(
                        select(Rate.created).where(
                            Rate.network == key,
                            Rate.lane == lane,
                            Rate.created > now - timedelta(hours=1),
                        )
                    )
                ).all()
            )
            minute = sum(t > now - timedelta(minutes=1) for t in dates)
            if minute >= (6 if lane == "turn" else 60) or (lane == "turn" and len(dates) >= 60):
                raise Fault("rate_limited", 429)
            db.add(Rate(network=key, lane=lane, created=now))

    async def register(self, request: UUID, tab: UUID, cancel: bool = False) -> str:
        async with self.sessions.begin() as db:
            control, now = await self.locked(db)
            rid, tid = self.digest("request:" + str(request)), self.digest("tab:" + str(tab))
            old = await db.get(Run, rid)
            if old is not None and now < old.created + timedelta(hours=23):
                if cancel:
                    if old.tab == tid:
                        old.cancelled = True
                    return old.owner
                raise Fault(
                    "cancelled" if old.cancelled and old.tab == tid else "duplicate_request", 409
                )
            if old is not None:
                await db.delete(old)
                await db.flush()
            if not cancel:
                if not control.enabled or not all(n > 0 for n in self.limits) or len(self.key) < 32:
                    raise Fault("disabled")
                if control.lease_until is not None and control.lease_until > now:
                    raise Fault("busy", 429)
            owner = str(uuid4())
            db.add(Run(id=rid, tab=tid, owner=owner, created=now, cancelled=cancel, done=False))
            if not cancel:
                control.lease_owner, control.lease_until = owner, now + timedelta(seconds=75)
            return owner

    async def check(self, db: AsyncSession, owner: str, control: Control, now: datetime) -> Run:
        row = await db.scalar(select(Run).where(Run.owner == owner))
        if row is None or row.cancelled or row.done or control.lease_owner != owner:
            raise Fault("cancelled", 409)
        if now >= row.created + timedelta(seconds=60):
            raise Fault("timeout", 504)
        if not control.enabled:
            raise Fault("disabled")
        return row

    async def alive(self, owner: str) -> None:
        async with self.sessions.begin() as db:
            control, now = await self.locked(db)
            await self.check(db, owner, control, now)

    async def reserve(self, owner: str, kind: str) -> str:
        async with self.sessions.begin() as db:
            control, now = await self.locked(db)
            await self.check(db, owner, control, now)
            budget = await db.get(Budget, now.date())
            if budget is None:
                budget = Budget(
                    day=now.date(),
                    attempts=0,
                    received=0,
                    input_tokens=0,
                    output_tokens=0,
                    embedding_tokens=0,
                )
                db.add(budget)
            calls, inputs, outputs, embeddings = self.limits
            if budget.attempts >= calls:
                raise Fault("budget_exhausted", 429)
            if kind == "responses":
                if budget.input_tokens + 12000 > inputs or budget.output_tokens + 4096 > outputs:
                    raise Fault("budget_exhausted", 429)
                budget.input_tokens += 12000
                budget.output_tokens += 4096
            else:
                if budget.embedding_tokens + 8000 > embeddings:
                    raise Fault("budget_exhausted", 429)
                budget.embedding_tokens += 8000
            budget.attempts += 1
            identity = str(uuid4())
            db.add(
                Reservation(
                    id=identity, owner=owner, day=now.date(), created=now, kind=kind, received=False
                )
            )
            return identity

    async def reconcile(self, identity: str, owner: str, inputs: int, outputs: int) -> None:
        async with self.sessions.begin() as db:
            await self.locked(db)
            reservation = await db.get(Reservation, identity)
            if reservation is None or reservation.owner != owner or reservation.received:
                return
            if inputs < 0 or outputs < 0:
                return
            budget = await db.get(Budget, reservation.day)
            assert budget is not None
            reservation.received = True
            budget.received += 1
            if reservation.kind == "responses":
                budget.input_tokens += inputs - 12000
                budget.output_tokens += outputs - 4096
            else:
                budget.embedding_tokens += inputs - 8000

    async def finish(self, owner: str) -> None:
        async with self.sessions.begin() as db:
            control, _ = await self.locked(db)
            row = await db.scalar(select(Run).where(Run.owner == owner))
            if row is not None:
                row.done = True
            if control.lease_owner == owner:
                control.lease_owner = control.lease_until = None

    async def cleanup(self) -> None:
        async with self.sessions.begin() as db:
            control, now = await self.locked(db)
            cutoff = now - timedelta(hours=23)
            for model in (Run, Rate, Reservation):
                await db.execute(delete(model).where(model.created <= cutoff))
            if control.lease_until is not None and control.lease_until <= now:
                control.lease_owner = control.lease_until = None
