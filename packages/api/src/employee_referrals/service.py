"""One locked transaction per snapshot; a plain ACK leaves only after commit."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.employee_referrals.models import CampaignReplica, K1Receipt, ReferralIdentity
from src.employee_referrals.schemas import (
    LEASE_SECONDS,
    Ack,
    K1Error,
    Outcome,
    ReceiptAck,
    RegistryAck,
    Snapshot,
)


async def receive_snapshot(
    maker: async_sessionmaker[AsyncSession], snapshot: Snapshot, raw: bytes
) -> Ack:
    try:
        async with maker.begin() as session:
            ack = await apply_snapshot(session, snapshot, hashlib.sha256(raw).hexdigest())
        return ack
    except IntegrityError as exc:
        # asyncpg's translated exception retains the driver's constraint name on its cause.
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
        if constraint == "pk_employee_referral_k1_receipts":
            raise K1Error("k1_event_conflict") from exc
        if constraint in {"pk_employee_referral_tokens", "uq_employee_referral_tokens_pair"}:
            raise K1Error("k1_identity_conflict") from exc
        raise K1Error("k1_unavailable") from exc


async def apply_snapshot(session: AsyncSession, snapshot: Snapshot, body_hash: str) -> Ack:
    campaign = snapshot.campaign
    content_hash = snapshot.content_sha256()
    # Provisional fields remain invisible until commit. Actual issuance is checked against
    # the database clock AFTER the campaign lock, including first-message races.
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    created = await session.scalar(
        insert(CampaignReplica)
        .values(
            campaign_id=campaign.id,
            content_revision=campaign.content_revision,
            product_id=campaign.product_id,
            name=campaign.name,
            state=campaign.state,
            published_at=campaign.published_at,
            start_at=campaign.start_at,
            end_at=campaign.end_at,
            ended_at=campaign.ended_at,
            content_sha256=content_hash,
            issued_at=epoch,
            valid_until=epoch + timedelta(seconds=LEASE_SECONDS),
            observed_at=epoch,
        )
        .on_conflict_do_nothing(index_elements=[CampaignReplica.campaign_id])
        .returning(CampaignReplica.campaign_id)
    )
    replica = (
        await session.scalars(
            select(CampaignReplica)
            .where(CampaignReplica.campaign_id == campaign.id)
            .with_for_update()
        )
    ).one()
    receipt = await session.get(K1Receipt, UUID(snapshot.event_id))
    if receipt is not None:
        if receipt.body_sha256 != body_hash:
            raise K1Error("k1_event_conflict")
        return await acknowledgement(session, replica, receipt, replayed=True)

    conflicting_revision = await session.scalar(
        select(K1Receipt.event_id)
        .where(
            K1Receipt.campaign_id == campaign.id,
            K1Receipt.content_revision == campaign.content_revision,
            K1Receipt.content_sha256 != content_hash,
        )
        .limit(1)
    )
    if conflicting_revision is not None or (
        created is None
        and campaign.content_revision == replica.content_revision
        and content_hash != replica.content_sha256
    ):
        raise K1Error("k1_revision_conflict")
    for field in ("product_id", "name", "published_at", "start_at", "end_at"):
        if getattr(replica, field) != getattr(campaign, field):
            raise K1Error("k1_campaign_conflict")
    if replica.state == "ended" and (
        (campaign.state == "ended" and campaign.ended_at != replica.ended_at)
        or (campaign.state == "published" and campaign.content_revision >= replica.content_revision)
    ):
        raise K1Error("k1_campaign_conflict")

    identities = list(
        await session.scalars(
            select(ReferralIdentity).where(
                or_(
                    ReferralIdentity.campaign_id == campaign.id,
                    ReferralIdentity.token.in_([p.token for p in snapshot.participants]),
                )
            )
        )
    )
    by_token = {identity.token: identity for identity in identities}
    by_employee = {
        identity.employee_code: identity
        for identity in identities
        if identity.campaign_id == campaign.id
    }
    for participant in snapshot.participants:
        employee_code = UUID(participant.employee_code)
        known_pair = by_employee.get(employee_code)
        known_token = by_token.get(participant.token)
        if (known_pair is not None and known_pair.token != participant.token) or (
            known_token is not None
            and (
                known_token.campaign_id != campaign.id or known_token.employee_code != employee_code
            )
        ):
            raise K1Error("k1_identity_conflict")

    observed_at: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    if snapshot.issued_at > observed_at:
        raise K1Error("k1_invalid_request")
    outcome: Outcome
    if created is not None or campaign.content_revision > replica.content_revision:
        outcome = "applied"
        for previous_identity in by_employee.values():
            previous_identity.in_current_registry = False
        for participant in snapshot.participants:
            employee_code = UUID(participant.employee_code)
            identity = by_employee.get(employee_code)
            if identity is None:
                identity = ReferralIdentity(
                    token=participant.token, campaign_id=campaign.id, employee_code=employee_code
                )
                session.add(identity)
            identity.employee_display_name = participant.employee_display_name
            identity.in_current_registry = True
        replica.content_revision = campaign.content_revision
        replica.content_sha256 = content_hash
        replica.state = campaign.state
        replica.ended_at = campaign.ended_at
    elif campaign.content_revision < replica.content_revision:
        outcome = "superseded"
    elif snapshot.issued_at > replica.issued_at:
        outcome = "renewed"
    else:
        outcome = "unchanged"
    if outcome in ("applied", "renewed"):
        replica.issued_at = snapshot.issued_at
        replica.valid_until = snapshot.issued_at + timedelta(seconds=LEASE_SECONDS)
        replica.observed_at = observed_at
    receipt = K1Receipt(
        event_id=UUID(snapshot.event_id),
        campaign_id=campaign.id,
        content_revision=campaign.content_revision,
        body_sha256=body_hash,
        content_sha256=content_hash,
        issued_at=snapshot.issued_at,
        observed_at=observed_at,
        outcome=outcome,
    )
    session.add(receipt)
    await session.flush()
    return await acknowledgement(session, replica, receipt, replayed=False)


async def acknowledgement(
    session: AsyncSession, replica: CampaignReplica, receipt: K1Receipt, *, replayed: bool
) -> Ack:
    count = await session.scalar(
        select(func.count())
        .select_from(ReferralIdentity)
        .where(
            ReferralIdentity.campaign_id == replica.campaign_id,
            ReferralIdentity.in_current_registry.is_(True),
        )
    )
    observed_at = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    return Ack(
        receipt=ReceiptAck.model_validate(
            {
                "event_id": str(receipt.event_id),
                "campaign_id": receipt.campaign_id,
                "content_revision": receipt.content_revision,
                "body_sha256": receipt.body_sha256,
                "outcome": receipt.outcome,
            }
        ),
        replayed=replayed,
        registry=RegistryAck.model_validate(
            {
                "campaign_id": replica.campaign_id,
                "content_revision": replica.content_revision,
                "content_sha256": replica.content_sha256,
                "state": replica.state,
                "issued_at": replica.issued_at,
                "valid_until": replica.valid_until,
                "participant_count": count,
                "observed_at": observed_at,
            }
        ),
    )
