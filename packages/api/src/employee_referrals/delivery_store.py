"""Short DB-clock claims/completion for two independent retained effect lanes."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, false, func, null, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.employee_referrals.delivery_models import DeliveryColumns, K2Delivery, NoticeDelivery
from src.employee_referrals.delivery_privacy import private_operation
from src.employee_referrals.notices import Notice, verify_notice
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.return_schemas import CountSnapshot, ReturnError
from src.employee_referrals.returns import validated
from src.employee_referrals.schemas import MAX_BIGINT
from src.employee_referrals.transport import LaneConfig, Result, parse_ack

Lane = Literal["k2", "notice"]
Job = DeliveryColumns
CATEGORIES = frozenset(
    {
        "storage",
        "protocol",
        "configuration",
        "authentication",
        "conflict",
        "payload_limit",
        "invalid_request",
        "receiver_disabled",
        "transport",
        "recipient_unreachable",
        "lease_expired",
        "attempt_limit",
    }
)
QUARANTINE = frozenset({"storage", "protocol", "conflict", "payload_limit", "invalid_request"})


@dataclass(frozen=True, repr=False)
class Claim:
    lane: Lane
    job_id: int
    generation: int
    body: bytes
    digest: str
    snapshot: CountSnapshot
    notice: Notice | None


async def now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise ValueError("Storage unavailable")
    return value


def block(job: Job, category: str) -> None:
    job.state = "blocked"
    job.leased_until = None
    job.error_category = category if category in CATEGORIES else "storage"


async def source(session: AsyncSession, job: Job) -> PaidOutbox | ReturnOutbox:
    if job.source_kind == "paid" and job.paid_id is not None and job.return_id is None:
        row: PaidOutbox | ReturnOutbox | None = await session.get(PaidOutbox, job.paid_id)
    elif job.source_kind == "return" and job.return_id is not None and job.paid_id is None:
        row = await session.get(ReturnOutbox, job.return_id)
    else:
        raise ValueError("Invalid source reference")
    if row is None:
        raise ValueError("Missing source")
    return row


async def verify(session: AsyncSession, lane: Lane, job: Job) -> Claim:
    row = await source(session, job)
    snapshot = validated(row)
    notice = verify_notice(job, snapshot) if isinstance(job, NoticeDelivery) else None
    if notice is not None and isinstance(row, ReturnOutbox):
        delta = {int(line["order_item_id"]): int(line["quantity"]) for line in row.request_lines}
        if any(line.delta != delta.get(line.order_item_id, 0) for line in notice.lines):
            raise ValueError("Invalid return display delta")
    if lane == "notice" and notice is None:
        raise ValueError("Missing notice")
    return Claim(lane, job.id, job.generation, row.body, row.body_sha256, snapshot, notice)


def due(job: Job, clock: datetime) -> bool:
    return (job.state == "pending" and job.next_attempt_at <= clock) or (
        job.state == "leased" and job.leased_until is not None and job.leased_until <= clock
    )


@private_operation
async def claim_job(sessions: async_sessionmaker[AsyncSession], lane: Lane) -> Claim | None:
    model = K2Delivery if lane == "k2" else NoticeDelivery
    async with sessions.begin() as session:
        clock = await now(session)
        predicate = or_(
            and_(model.state == "pending", model.next_attempt_at <= clock),
            and_(model.state == "leased", model.leased_until <= clock),
        )
        job = await session.scalar(
            select(model)
            .where(predicate)
            .order_by(model.next_attempt_at, model.created_at, model.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        clock = await now(session)
        if not due(job, clock):
            return None
        if job.generation >= MAX_BIGINT:
            block(job, "storage")
            return None
        if job.attempt_count >= 5:
            block(job, "lease_expired" if job.state == "leased" else "attempt_limit")
            return None
        try:
            await verify(session, lane, job)
        except (ValueError, ReturnError, ArithmeticError):
            block(job, "storage")
            return None
        job.state = "leased"
        job.attempt_count += 1
        job.generation += 1
        job.leased_until = clock + timedelta(seconds=30)
        return await verify(session, lane, job)


def current(job: Job | None, claim: Claim, clock: datetime) -> bool:
    return bool(
        job is not None
        and job.state == "leased"
        and job.generation == claim.generation
        and job.leased_until is not None
        and job.leased_until > clock
    )


@private_operation
async def complete(
    sessions: async_sessionmaker[AsyncSession], claim: Claim, result: Result
) -> None:
    model = K2Delivery if claim.lane == "k2" else NoticeDelivery
    async with sessions.begin() as session:
        job = await session.scalar(select(model).where(model.id == claim.job_id).with_for_update())
        clock = await now(session)
        if job is None or not current(job, claim, clock):
            return
        try:
            fresh = await verify(session, claim.lane, job)
            if (
                fresh.body != claim.body
                or fresh.digest != claim.digest
                or fresh.notice != claim.notice
            ):
                raise ValueError("Invalid completion source")
        except (ValueError, ReturnError, ArithmeticError):
            block(job, "storage")
            return
        if isinstance(job, K2Delivery) and result.ack is not None:
            # Revalidate even a caller-supplied Result before persisting a receipt.
            raw = result.ack.model_dump_json().encode()
            ack = parse_ack(raw, claim.snapshot, claim.digest)
            job.ack = ack.model_dump(mode="json")
            job.state = "acknowledged"
        elif (
            isinstance(job, NoticeDelivery)
            and type(result.message_id) is int
            and 1 <= result.message_id <= MAX_BIGINT
        ):
            job.message_id = result.message_id
            job.state = "sent"
        else:
            if result.permanent or job.attempt_count >= 5:
                block(job, result.category)
                return
            delay = (
                (1, 2, 4, 8)[job.attempt_count - 1]
                if claim.lane == "k2"
                else (60, 300, 1800, 7200)[job.attempt_count - 1]
            )
            job.state = "pending"
            job.leased_until = None
            job.error_category = result.category if result.category in CATEGORIES else "protocol"
            job.next_attempt_at = clock + timedelta(seconds=max(delay, result.retry_after))
            return
        job.completed_at = clock
        job.leased_until = None
        job.error_category = None


@private_operation
async def retry(
    sessions: async_sessionmaker[AsyncSession], lane: Lane, event: UUID, config: LaneConfig
) -> tuple[str, int, int]:
    if not config.enabled or not config.valid(lane):
        return "configuration", 0, 2
    model = K2Delivery if lane == "k2" else NoticeDelivery
    async with sessions.begin() as session:
        paid_id = await session.scalar(select(PaidOutbox.id).where(PaidOutbox.event_id == event))
        return_id = await session.scalar(
            select(ReturnOutbox.id).where(ReturnOutbox.return_id == event)
        )
        job = await session.scalar(
            select(model)
            .where(
                or_(
                    model.paid_id == paid_id if paid_id is not None else false(),
                    model.return_id == return_id if return_id is not None else false(),
                )
            )
            .with_for_update()
        )
        if job is None or job.state != "blocked":
            return "no_relevant_work", 0, 0
        if job.error_category in QUARANTINE or job.generation >= MAX_BIGINT:
            return "quarantined", 0, 1
        await verify(session, lane, job)
        job.state = "pending"
        job.attempt_count = 0
        job.generation += 1
        job.leased_until = None
        job.next_attempt_at = await now(session)
        job.error_category = None
        return "queued", 1, 0


@private_operation
async def replay_k2(
    sessions: async_sessionmaker[AsyncSession], event: UUID, config: LaneConfig
) -> tuple[str, int, int]:
    """Explicitly replay one retained fact, including ACK after a Portal restore."""
    if not config.enabled or not config.valid("k2"):
        return "configuration", 0, 2
    async with sessions.begin() as session:
        paid_id = await session.scalar(select(PaidOutbox.id).where(PaidOutbox.event_id == event))
        return_id = await session.scalar(
            select(ReturnOutbox.id).where(ReturnOutbox.return_id == event)
        )
        if paid_id is not None and return_id is not None:
            return "quarantined", 0, 1
        job = await session.scalar(
            select(K2Delivery)
            .where(
                or_(
                    K2Delivery.paid_id == paid_id if paid_id is not None else false(),
                    K2Delivery.return_id == return_id if return_id is not None else false(),
                )
            )
            .with_for_update()
        )
        if job is None:
            return "missing", 0, 1
        if job.state == "leased":
            return "busy", 0, 1
        if job.error_category in QUARANTINE or job.generation >= MAX_BIGINT:
            return "quarantined", 0, 1
        await verify(session, "k2", job)
        from src.employee_referrals.returns import read_history

        source_row = await source(session, job)
        await read_history(session, source_row.order_id)
        # JSONB None means JSON null; the receipt invariant requires SQL NULL.
        await session.execute(
            update(K2Delivery)
            .where(K2Delivery.id == job.id)
            .values(
                state="pending",
                attempt_count=0,
                generation=job.generation + 1,
                leased_until=None,
                next_attempt_at=await now(session),
                error_category=None,
                ack=null(),
                completed_at=None,
            )
        )
        return "queued", 1, 0
