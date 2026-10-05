"""One repeatable-read retained source observation, independent of transport jobs."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.employee_referrals.count_wire import (
    MAX_EVENTS,
    CountError,
    Event,
    Readback,
    quantity_values,
)
from src.employee_referrals.delivery_models import K2Delivery
from src.employee_referrals.delivery_privacy import private_operation
from src.employee_referrals.models import (
    CampaignReplica,
    ReferralIdentity,
    ReferralOrderItemSnapshot,
    ReferralOrderSnapshot,
)
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.return_schemas import Intent
from src.employee_referrals.returns import validated
from src.employee_referrals.schemas import utc_text
from src.orders.models import Order

ZONE = ZoneInfo("Asia/Tashkent")


@private_operation
async def observe(
    sessions: async_sessionmaker[AsyncSession], campaign_id: int, day: str
) -> dict[str, object]:
    bind = sessions.kw.get("bind")
    if bind is None or bind.echo:
        raise CountError("unavailable")
    async with sessions() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout = '8s'"))
        campaign = await session.get(CampaignReplica, campaign_id)
        if campaign is None:
            raise CountError("unknown_campaign")
        clock = await session.scalar(select(func.clock_timestamp()))
        if not isinstance(clock, datetime):
            raise CountError("unavailable")
        # Validate attribution before filtering damaged bodies. A bounded scan refuses
        # oversized scope rather than silently excluding an unparseable source.
        roots = list((await session.scalars(select(PaidOutbox).limit(MAX_EVENTS + 1))).all())
        returns = list((await session.scalars(select(ReturnOutbox).limit(MAX_EVENTS + 1))).all())
        if len(roots) + len(returns) > MAX_EVENTS:
            raise CountError("payload_too_large")
        all_sources: list[PaidOutbox | ReturnOutbox] = [*roots, *returns]
        parsed = {r.id: validated(r) for r in all_sources}
        ids = [s.event_id for s in parsed.values()]
        positions = [s.source_position for s in parsed.values()]
        if (
            len(parsed) != len(roots) + len(returns)
            or len(set(ids)) != len(ids)
            or len(set(positions)) != len(positions)
        ):
            raise CountError("unavailable")
        owned = [r for r in roots if parsed[r.id].attribution.campaign_id == campaign_id]
        roots_by_order = {r.order_id: r for r in roots}
        state = "retained_valid"
        for confirmation in returns:
            if confirmation.order_id not in roots_by_order:
                state = "inconsistent"
        captures = list(
            (
                await session.scalars(
                    select(ReferralOrderSnapshot)
                    .where(ReferralOrderSnapshot.campaign_id == campaign_id)
                    .limit(MAX_EVENTS + 1)
                )
            ).all()
        )
        if len(captures) > MAX_EVENTS:
            raise CountError("payload_too_large")
        for header in captures:
            order = await session.get(Order, header.order_id)
            root = roots_by_order.get(header.order_id)
            if order is None:
                raise CountError("unavailable")
            if root is None and order.paid_at is not None:
                if state != "inconsistent":
                    state = "source_gap"
            elif root is None and order.payment_status == "paid":
                state = "inconsistent"
            elif root is not None:
                paid = parsed[root.id]
                attribution = paid.attribution
                if (
                    header.campaign_id,
                    str(header.employee_code),
                    header.product_id,
                    header.content_revision,
                    header.captured_at,
                ) != (
                    attribution.campaign_id,
                    attribution.employee_code,
                    attribution.product_id,
                    attribution.content_revision,
                    attribution.captured_at,
                ) or order.paid_at != paid.occurred_at:
                    state = "inconsistent"
                surviving = (
                    await session.scalars(
                        select(ReferralOrderItemSnapshot).where(
                            ReferralOrderItemSnapshot.order_id == header.order_id
                        )
                    )
                ).all()
                frozen = {
                    line.order_item_id: (line.product_id, line.quantity) for line in paid.lines
                }
                if any(
                    frozen.get(item.order_item_id) != (item.product_id, item.quantity)
                    for item in surviving
                ):
                    state = "inconsistent"
        events: list[Event] = []
        paid_total = returned_total = daily_paid = daily_returned = 0
        delivery = dict.fromkeys(
            ("missing_jobs", "pending", "leased", "blocked", "acknowledged", "ack_unready"), 0
        )
        for root in owned:
            paid = parsed[root.id]
            identity = await session.scalar(
                select(ReferralIdentity).where(
                    ReferralIdentity.campaign_id == campaign_id,
                    ReferralIdentity.employee_code == paid.attribution.employee_code,
                )
            )
            if identity is None or paid.attribution.product_id != campaign.product_id:
                state = "inconsistent"
            chain = sorted(
                (r for r in returns if r.order_id == root.order_id), key=lambda r: r.order_version
            )
            returned = paid.returned()
            bounds = {line.order_item_id: line.quantity for line in paid.lines}
            root_paid = sum(bounds.values())
            paid_total += root_paid
            if paid.occurred_at.astimezone(ZONE).date().isoformat() == day:
                daily_paid += root_paid
            source_rows: list[PaidOutbox | ReturnOutbox] = [root, *chain]
            for version, row in enumerate(source_rows, 1):
                snapshot = parsed[row.id]
                delta = None
                if isinstance(row, ReturnOutbox):
                    intent = Intent.model_validate(
                        {"return_id": str(row.return_id), "lines": row.request_lines}
                    )
                    delta = [
                        {"order_item_id": int(line.order_item_id), "quantity": int(line.quantity)}
                        for line in intent.lines
                    ]
                    if intent.normalized() != row.request_lines:
                        state = "inconsistent"
                    for line in intent.lines:
                        item = int(line.order_item_id)
                        if item not in returned:
                            state = "inconsistent"
                        else:
                            returned[item] += int(line.quantity)
                    if snapshot.occurred_at.astimezone(ZONE).date().isoformat() == day:
                        daily_returned += sum(int(line.quantity) for line in intent.lines)
                if (
                    snapshot.order_version != version
                    or snapshot.identity() != paid.identity()
                    or returned != snapshot.returned()
                    or any(qty > bounds[item] for item, qty in returned.items())
                ):
                    state = "inconsistent"
                events.append(
                    Event.model_validate(
                        {
                            "snapshot": snapshot.model_dump(mode="json"),
                            "body_sha256": row.body_sha256,
                            "return_delta": delta,
                        }
                    )
                )
                job = await session.scalar(
                    select(K2Delivery).where(
                        K2Delivery.paid_id == row.id
                        if isinstance(row, PaidOutbox)
                        else K2Delivery.return_id == row.id
                    )
                )
                if job is None:
                    delivery["missing_jobs"] += 1
                elif (job.source_kind, job.paid_id, job.return_id) != (
                    "paid" if isinstance(row, PaidOutbox) else "return",
                    row.id if isinstance(row, PaidOutbox) else None,
                    row.id if isinstance(row, ReturnOutbox) else None,
                ):
                    state = "inconsistent"
                else:
                    delivery[job.state] += 1
                    if job.ack is not None:
                        projection = job.ack.get("projection")
                        if (
                            not isinstance(projection, dict)
                            or projection.get("has_conflict") is True
                            or projection.get("accounting_state") != "confirmed"
                        ):
                            delivery["ack_unready"] += 1
            returned_total += sum(returned.values())
        events.sort(key=lambda e: (e.snapshot.order_id, e.snapshot.order_version))
        result = {
            "schema_version": 1,
            "campaign_id": str(campaign_id),
            "day": day,
            "source_observed_at": utc_text(clock),
            "source_state": state,
            "coverage_scope": "retained-source-v1",
            "events": events,
            "totals": quantity_values(paid_total, returned_total)
            if state == "retained_valid"
            else None,
            "daily": quantity_values(daily_paid, daily_returned)
            if state == "retained_valid"
            else None,
            "delivery": delivery,
        }
        return Readback.model_validate(result).model_dump(mode="json")
