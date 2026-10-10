"""Privileged, explicitly evidenced inventory activation and payment recovery.

No automatic interpretation of undocumented provider terminal responses. Protected operator
attestations supplement authenticated invoice identity; inspection has no external effects.
"""

import json
import os
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.employee_referrals.paid import commit_paid
from src.orders.models import Order, OrderItem, OrderStatus
from src.payments.service import multicard_client
from src.payments.size_stock import (
    HELD_STATES,
    PROTOCOL,
    close_unpaid,
    conflict,
    invoice_identity,
    lock_order,
    record,
    settle_owned,
)
from src.products.models import Product, ProductSize, SizeStockRecord
from src.products.stock import SIZES, lock_products, lock_sizes, restore_allocations


def read_evidence(path: Path, *, actor: str, action: str) -> dict[str, object]:
    """The deployment operator supplies an authorized record, never a public request body."""
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("Evidence must be an operator-owned file without group/world writes")
    data: object = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
        raise ValueError("Evidence must be a JSON object")
    if (
        data.get("kind") != "vest-size-stock-authorized-record-v1"
        or data.get("authorized") is not True
        or not isinstance(data.get("authorized_by"), str)
        or not data["authorized_by"]
        or not isinstance(data.get("source_ref"), str)
        or not data["source_ref"]
        or data.get("actor") != actor
        or data.get("action") != action
    ):
        raise ValueError("Evidence must identify authorization, source, matching actor and action")
    return data


async def inspect_order(db: AsyncSession, order_id: int) -> dict[str, object]:
    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    records = (
        (
            await db.execute(
                select(SizeStockRecord)
                .where(SizeStockRecord.order_id == order_id)
                .order_by(SizeStockRecord.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "order_id": order.id,
        "size_stock": order.size_stock,
        "creation_token": order.creation_token,
        "cancellation_token": order.cancellation_token,
        "owned_invoice_uuid": order.owned_invoice_uuid,
        "checkout_url": order.checkout_url,
        "accepted_settlement": order.accepted_settlement,
        "records": [
            {
                "id": row.id,
                "action": row.action,
                "actor": row.actor,
                "reason": row.reason,
                "evidence_ref": row.evidence_ref,
                "expected_revision": row.expected_revision,
                "result": row.result,
            }
            for row in records
        ],
    }


async def inspect_vest(db: AsyncSession) -> dict[str, object]:
    product = await db.get(Product, 39)
    if product is None:
        raise HTTPException(status_code=404, detail="Product 39 not found")
    orders = (
        (
            await db.execute(
                select(Order)
                .where(
                    Order.size_stock_protocol.is_(None),
                    Order.id.in_(select(OrderItem.order_id).where(OrderItem.product_id == 39)),
                )
                .order_by(Order.id)
            )
        )
        .scalars()
        .all()
    )
    records = (
        (
            await db.execute(
                select(SizeStockRecord).where(
                    SizeStockRecord.order_id.in_([order.id for order in orders]),
                    SizeStockRecord.action.in_(("legacy_creation_claim", "legacy_invoice_created")),
                )
            )
        )
        .scalars()
        .all()
    )
    claimed = {
        row.result["creation_token"] for row in records if row.action == "legacy_creation_claim"
    }
    completed = {
        row.result["creation_token"] for row in records if row.action == "legacy_invoice_created"
    }
    return {
        "product_id": 39,
        "slug": product.slug,
        "name": product.name,
        "cutover_blocked": product.size_cutover_blocked,
        "initialized_at": product.sizes_initialized_at,
        "sizes": [
            {
                "size": row.size,
                "available_quantity": row.available_quantity,
                "revision": row.revision,
            }
            for row in product.sizes
        ],
        "historical_orders": [
            {
                "order_id": order.id,
                "status": order.status,
                "payment_status": order.payment_status,
                "latest_uuid": order.payment_uuid,
                "creation_token": order.legacy_vest_creation_token,
            }
            for order in orders
        ],
        "unresolved_creation_tokens": sorted(str(token) for token in claimed - completed),
    }


async def initialize_vest(
    db: AsyncSession,
    *,
    action: str,
    actor: str,
    reason: str,
    evidence_ref: str,
    evidence: dict[str, object],
) -> str:
    product = (await lock_products(db, {39})).get(39)
    if (
        product is None
        or product.slug != "zhiletka-healthy-lifestyle"
        or product.name != "Жилетка двусторонняя Healthy Lifestyle"
    ):
        raise conflict("vest_identity_conflict")
    rows = list((await lock_sizes(db, {39})).values())
    if product.sizes_initialized_at is not None and {row.size for row in rows} == set(SIZES):
        await db.commit()
        return "already_initialized"
    if rows or product.sizes_initialized_at is not None:
        raise conflict("size_configuration_conflict")
    if action == "pause":
        product.size_cutover_blocked = True
    elif action == "initialize":
        if not product.size_cutover_blocked:
            raise conflict("cutover_pause_required")
        state = await inspect_vest(db)
        historical = state["historical_orders"]
        assert isinstance(historical, list)
        ids = {entry["order_id"] for entry in historical}
        resolved = evidence.get("resolved_order_ids")
        if (
            not isinstance(resolved, list)
            or set(resolved) != ids
            or evidence.get("resolved_creation_tokens", []) != state["unresolved_creation_tokens"]
            or evidence.get("all_legacy_attempts_resolved") is not True
            or evidence.get("free_units_each_size") != 50
            or evidence.get("original_creators_and_upstream_fenced") is not True
        ):
            raise conflict("legacy_commitments_unresolved")
        db.add_all(
            [ProductSize(product_id=39, size=label, available_quantity=50) for label in SIZES]
        )
        product.sizes_initialized_at = datetime.now(UTC)
        product.size_cutover_blocked = False
    else:
        raise ValueError("Only pause or initialize is supported")
    db.add(
        SizeStockRecord(
            product_id=39,
            action=action,
            actor=actor,
            reason=reason,
            evidence_ref=evidence_ref,
            result={"action": action, "evidence": evidence},
        )
    )
    await db.commit()
    return "applied"


async def recover_order(
    db: AsyncSession,
    *,
    order_id: int,
    expected_revision: int,
    action: str,
    actor: str,
    reason: str,
    evidence_ref: str,
    evidence: dict[str, object],
) -> str:
    """Bounded recovery from authorized final facts; no reset/replacement action exists."""
    # Parse/read all evidence and obtain optional provider identity before locking anything.
    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if (
        order.size_stock_protocol != PROTOCOL
        or evidence.get("order_id") != order_id
        or evidence.get("expected_revision") != expected_revision
        or evidence.get("creation_token") != order.creation_token
    ):
        raise conflict("recovery_identity_conflict")
    invoice_uuid = evidence.get("uuid")
    details = evidence.get("invoice_details")
    if action in ("bind", "verify_cancel"):
        if not isinstance(invoice_uuid, str) or not invoice_uuid:
            raise conflict("recovery_identity_conflict")
        await db.commit()
        details = await multicard_client.get_invoice_status(invoice_uuid)
    order = await lock_order(db, order_id)
    if order.size_revision != expected_revision:
        raise conflict("order_state_conflict")
    if evidence.get("creation_token") != order.creation_token:
        raise conflict("recovery_identity_conflict")
    if action != "no_invoice" and (
        not isinstance(invoice_uuid, str)
        or not invoice_uuid
        or order.owned_invoice_uuid not in (None, invoice_uuid)
        or not isinstance(details, dict)
        or not invoice_identity(details, order, invoice_uuid)
    ):
        raise conflict("recovery_needs_evidence")
    if action == "bind":
        if (
            order.size_owner_state not in ("creating", "uncertain", "live")
            or not order.creation_token
        ):
            raise conflict("order_state_conflict")
        checkout_url = details.get("checkout_url") if isinstance(details, dict) else None
        if (
            order.owned_invoice_uuid == invoice_uuid
            and order.size_owner_state == "live"
            and order.checkout_url
        ):
            await db.commit()
            return "unchanged"
        order.owned_invoice_uuid = str(invoice_uuid)
        order.payment_uuid = str(invoice_uuid)
        if isinstance(checkout_url, str) and checkout_url:
            if order.checkout_url is None:
                order.checkout_url = checkout_url
            order.size_owner_state = "live"
            order.payment_status = "pending"
    elif action == "settled":
        if evidence.get("final_payment_verified") is not True:
            raise conflict("recovery_needs_evidence")
        if order.accepted_settlement:
            await db.commit()
            return "unchanged"
        await settle_owned(db, order, str(invoice_uuid))
    elif action == "full_refund":
        if (
            not order.accepted_settlement
            or evidence.get("full_refund_completed") is not True
            or evidence.get("refund_amount") != int((order.total - order.points_applied) * 100)
        ):
            raise conflict("recovery_needs_evidence")
        if order.size_owner_state == "refunded":
            await db.commit()
            return "unchanged"
        if order.dispatched_at is None:
            await restore_allocations(db, order)
        order.size_owner_state = "refunded"
        order.payment_status = "refunded"
        order.status = OrderStatus.CANCELLED
    elif action in ("annulled", "no_invoice"):
        if order.size_owner_state == "cancelled":
            await db.commit()
            return "unchanged"
        if order.accepted_settlement or order.size_owner_state not in HELD_STATES:
            raise conflict("order_state_conflict")
        if action == "annulled":
            if (
                evidence.get("annulment_completed") is not True
                or evidence.get("no_accepted_or_future_payment") is not True
                or not order.cancellation_token
                or evidence.get("cancellation_token") != order.cancellation_token
            ):
                raise conflict("recovery_needs_evidence")
        elif (
            order.owned_invoice_uuid is not None
            or order.creation_token is None
            or evidence.get("provider_no_invoice_verified") is not True
            or evidence.get("original_creators_and_upstream_fenced") is not True
        ):
            raise conflict("recovery_needs_evidence")
        await close_unpaid(db, order)
    elif action == "verify_cancel":
        if order.accepted_settlement or order.size_owner_state not in (
            "live",
            "cancelling",
            "uncertain",
        ):
            raise conflict("order_state_conflict")
        if (
            order.owned_invoice_uuid != invoice_uuid
            or evidence.get("prior_cancel_request_and_upstream_fenced") is not True
        ):
            raise conflict("recovery_needs_evidence")
        token = str(uuid.uuid4())
        order.cancellation_token = token
        order.size_owner_state = "cancelling"
        order.size_revision += 1
        record(
            db,
            order,
            action,
            actor=actor,
            reason=reason,
            evidence_ref=evidence_ref,
            expected_revision=expected_revision,
            result={"evidence": evidence, "token": token},
        )
        await db.commit()
        if not await multicard_client.delete_owned_invoice(str(invoice_uuid)):
            return "held"
        order = await lock_order(db, order_id)
        if (
            order.cancellation_token != token
            or order.owned_invoice_uuid != invoice_uuid
            or order.accepted_settlement
            or order.size_owner_state != "cancelling"
        ):
            await db.commit()
            return "conflict"
        await close_unpaid(db, order)
    else:
        raise ValueError("Unsupported recovery action")
    order.size_revision += 1
    record(
        db,
        order,
        action,
        actor=actor,
        reason=reason,
        evidence_ref=evidence_ref,
        expected_revision=expected_revision,
        result={"evidence": evidence, "revision": order.size_revision},
    )
    await commit_paid(db)
    return "applied"
