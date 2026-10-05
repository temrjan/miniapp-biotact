"""Root-locked cumulative confirmations in the caller's transaction, no side effects."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.return_schemas import CountSnapshot, Intent, ReturnError, load_json
from src.employee_referrals.schemas import MAX_BIGINT, utc_text


def validated(row: PaidOutbox | ReturnOutbox) -> CountSnapshot:
    try:
        snapshot = CountSnapshot.model_validate(load_json(row.body))
        event = row.event_id if isinstance(row, PaidOutbox) else row.return_id
        version = 1 if isinstance(row, PaidOutbox) else row.order_version
        if (
            hashlib.sha256(row.body).hexdigest() != row.body_sha256
            or snapshot.event_id != str(event)
            or snapshot.order_id != row.order_id
            or snapshot.order_version != version
            or snapshot.source_position != row.id
            or snapshot.source_recorded_at != row.created_at
            or (
                isinstance(row, ReturnOutbox)
                and (
                    snapshot.occurred_at != row.created_at
                    or row.source != "shop-admin-confirmation"
                    or row.confirmed_by <= 0
                )
            )
        ):
            raise ValueError("Invalid metadata")
        return snapshot
    except (ValueError, RecursionError) as exc:
        raise ReturnError("unavailable") from exc


@dataclass(frozen=True)
class History:
    paid: CountSnapshot
    rows: list[ReturnOutbox]
    returned: dict[int, int]

    def summary(self) -> dict[str, object]:
        return {
            "order_id": str(self.paid.order_id),
            "order_version": str(1 + len(self.rows)),
            "lines": [
                {
                    "order_item_id": str(line.order_item_id),
                    "product_id": str(line.product_id),
                    "paid_quantity": str(line.quantity),
                    "returned_quantity": str(self.returned[line.order_item_id]),
                }
                for line in self.paid.lines
            ],
        }


async def read_history(session: AsyncSession, order_id: int, *, lock: bool = False) -> History:
    query = select(PaidOutbox).where(PaidOutbox.order_id == order_id)
    if lock:
        query = query.with_for_update()
    root = await session.scalar(query.execution_options(populate_existing=True))
    if root is None:
        raise ReturnError("not_found")
    paid = validated(root)
    returned = paid.returned()
    rows = list(
        (
            await session.scalars(
                select(ReturnOutbox)
                .where(ReturnOutbox.order_id == order_id)
                .order_by(ReturnOutbox.order_version)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    bounds = {line.order_item_id: line.quantity for line in paid.lines}
    try:
        for version, row in enumerate(rows, 2):
            snapshot = validated(row)
            intent = Intent.model_validate(
                {"return_id": str(row.return_id), "lines": row.request_lines}
            )
            if (
                row.order_version != version
                or snapshot.identity() != paid.identity()
                or intent.normalized() != row.request_lines
            ):
                raise ValueError("Invalid chain")
            for line in intent.lines:
                item = int(line.order_item_id)
                if item not in bounds:
                    raise ValueError("Invalid item")
                returned[item] += int(line.quantity)
                if returned[item] > bounds[item]:
                    raise ValueError("Invalid bounds")
            if returned != snapshot.returned():
                raise ValueError("Invalid cumulative")
    except (ValueError, RecursionError) as exc:
        raise ReturnError("unavailable") from exc
    return History(paid, rows, returned)


def receipt(row: ReturnOutbox) -> dict[str, object]:
    return {
        "return_id": str(row.return_id),
        "order_id": str(row.order_id),
        "order_version": str(row.order_version),
        "confirmed_at": utc_text(row.created_at),
        "lines": row.request_lines,
    }


async def lookup(session: AsyncSession, intent: Intent, order_id: int) -> ReturnOutbox | None:
    event = UUID(intent.return_id)
    if await session.scalar(select(PaidOutbox.id).where(PaidOutbox.event_id == event)) is not None:
        raise ReturnError("conflict")
    row = await session.scalar(select(ReturnOutbox).where(ReturnOutbox.return_id == event))
    if row is not None and row.order_id != order_id:
        raise ReturnError("conflict")
    return row


async def confirm(
    session: AsyncSession, order_id: int, intent: Intent, admin_id: int
) -> dict[str, object]:
    # Cross-order conflicts precede missing-root. Auth has already run.
    await lookup(session, intent, order_id)
    history = await read_history(session, order_id, lock=True)
    existing = await lookup(session, intent, order_id)
    if existing is not None:
        if existing.request_lines != intent.normalized():
            raise ReturnError("conflict")
        return {"receipt": receipt(existing), "replayed": True}
    bounds = {line.order_item_id: line.quantity for line in history.paid.lines}
    returned = dict(history.returned)
    for line in intent.lines:
        item = int(line.order_item_id)
        if item not in returned:
            raise ReturnError()
        returned[item] += int(line.quantity)
        if returned[item] > bounds[item]:
            raise ReturnError("bounds")
    version = len(history.rows) + 2
    if version > MAX_BIGINT:
        raise ReturnError("unavailable")
    position = await session.scalar(text("SELECT nextval('employee_referral_paid_outbox_id_seq')"))
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        type(position) is not int
        or not 1 <= position <= MAX_BIGINT
        or not isinstance(now, datetime)
    ):
        raise ReturnError("unavailable")
    value = history.paid.model_dump(mode="json")
    value.update(
        event_id=intent.return_id,
        event_type="sale_refunded",
        order_version=version,
        source_position=position,
        occurred_at=utc_text(now),
        source_recorded_at=utc_text(now),
        lines=[
            dict(line.model_dump(), returned_quantity=returned[line.order_item_id])
            for line in history.paid.lines
        ],
    )
    body = freeze_snapshot(value)
    row = ReturnOutbox(
        id=position,
        return_id=UUID(intent.return_id),
        order_id=order_id,
        order_version=version,
        confirmed_by=admin_id,
        source="shop-admin-confirmation",
        request_lines=intent.normalized(),
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        created_at=now,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        # Different order locks can race on the global return UUID. Fresh transaction only.
        await session.rollback()
        existing = await lookup(session, intent, order_id)
        if existing is None:
            raise ReturnError("unavailable") from None
        await read_history(session, order_id, lock=True)
        return {"receipt": receipt(existing), "replayed": True}
    from src.employee_referrals.notices import append_jobs

    await append_jobs(session, row)
    return {"receipt": receipt(row), "replayed": False}


def freeze_snapshot(value: dict[str, object]) -> bytes:
    body = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(body) > 1048576:
        raise ReturnError("unavailable")
    return body
