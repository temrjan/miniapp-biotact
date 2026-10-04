"""One count fact in the caller's applied-paid transaction; no network or commit."""

import hashlib
import json
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from uuid import UUID, uuid4

import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.employee_referrals.models import ReferralOrderItemSnapshot, ReferralOrderSnapshot
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.schemas import MAX_BIGINT, canonical_uuid, utc_text
from src.orders.models import Order

_private: ContextVar[bool] = ContextVar("referral_paid_operation", default=False)
_installed = False


class PaidUnavailableError(Exception):
    """No private input or storage diagnostics cross the paid boundary."""


def install_privacy() -> None:
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if _private.get():
            record.msg = "Private referral paid operation"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


@contextmanager
def paid_privacy_scope(session: AsyncSession) -> Iterator[None]:
    install_privacy()
    token = _private.set(True)
    request_scope = sentry_sdk.get_isolation_scope()
    try:
        with sentry_sdk.isolation_scope() as event_scope:
            event_scope.set_tag("biotact_referral_paid", "yes")
            yield
    finally:
        # Retain the late SDK request boundary only for the affected paid unit.
        # An ordinary applied payment must keep its existing telemetry.
        if (
            session.info.get("referral_paid")
            and sentry_sdk.get_client().get_integration(StarletteIntegration) is not None
        ):
            request_scope.set_tag("biotact_referral_paid", "yes")
        _private.reset(token)


def positive(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_BIGINT:
        raise ValueError("Invalid paid identity or quantity")
    return value


def freeze_body(
    header: ReferralOrderSnapshot,
    items: Sequence[ReferralOrderItemSnapshot],
    *,
    event_id: UUID,
    position: int,
    paid_at: datetime,
    recorded_at: datetime,
) -> bytes:
    ids = [item.order_item_id for item in items]
    if (
        not ids
        or ids != sorted(set(ids))
        or any(
            item.order_id != header.order_id or item.product_id != header.product_id
            for item in items
        )
    ):
        raise ValueError("Invalid retained paid items")
    value = {
        "schema_version": 1,
        "event_id": canonical_uuid(str(event_id)),
        "event_type": "sale_paid",
        "source": "biotact-shop",
        "order_id": positive(header.order_id),
        "order_version": 1,
        "source_position": positive(position),
        "occurred_at": utc_text(paid_at),
        "source_recorded_at": utc_text(recorded_at),
        "attribution": {
            "campaign_id": positive(header.campaign_id),
            "employee_code": canonical_uuid(str(header.employee_code)),
            "product_id": positive(header.product_id),
            "content_revision": positive(header.content_revision),
            "captured_at": utc_text(header.captured_at),
        },
        "lines": [
            {
                "order_item_id": positive(item.order_item_id),
                "product_id": positive(item.product_id),
                "quantity": positive(item.quantity),
            }
            for item in items
        ],
    }
    body = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(body) > 1048576:
        raise ValueError("Invalid paid body size")
    return body


async def record_paid(session: AsyncSession, order: Order) -> PaidOutbox | None:
    """Caller has the order lock, or owns its newly inserted fully-points order."""
    with paid_privacy_scope(session):
        try:
            # Eligibility is private too, but unsafe echo must not reject ordinary
            # payments. Keep this read inside the existing log/SDK boundary.
            header = await session.get(ReferralOrderSnapshot, order.id)
            if header is None:
                return None
            session.info["referral_paid"] = True
            bind = session.get_bind()
            if settings.database_echo or bind.engine.echo:
                raise PaidUnavailableError()
            existing = await session.scalar(
                select(PaidOutbox).where(PaidOutbox.order_id == order.id)
            )
            if existing is not None:
                return existing
            if order.payment_status != "paid" or order.paid_at is None:
                raise PaidUnavailableError()
            items = (
                await session.scalars(
                    select(ReferralOrderItemSnapshot)
                    .where(
                        ReferralOrderItemSnapshot.order_id == order.id,
                    )
                    .order_by(ReferralOrderItemSnapshot.order_item_id)
                )
            ).all()
            position = await session.scalar(
                text("SELECT nextval('employee_referral_paid_outbox_id_seq')")
            )
            now = await session.scalar(select(func.clock_timestamp()))
            if type(position) is not int or not isinstance(now, datetime):
                raise PaidUnavailableError()
            event_id = uuid4()
            body = freeze_body(
                header,
                items,
                event_id=event_id,
                position=position,
                paid_at=order.paid_at,
                recorded_at=now,
            )
            row = PaidOutbox(
                id=position,
                order_id=order.id,
                event_id=event_id,
                body=body,
                body_sha256=hashlib.sha256(body).hexdigest(),
                created_at=now,
            )
            session.add(row)
            await session.flush()
            return row
        except Exception:
            raise PaidUnavailableError("Referral paid unavailable") from None


async def commit_paid(session: AsyncSession) -> None:
    """Keep the existing router commit private only when it includes the new fact."""
    if not session.info.get("referral_paid"):
        await session.commit()
        return
    with paid_privacy_scope(session):
        try:
            await session.commit()
        except Exception:
            raise PaidUnavailableError("Referral paid unavailable") from None
