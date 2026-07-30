"""Biotact Club loyalty SQLAlchemy models."""

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class ConsentPurpose(enum.StrEnum):
    """Purpose a user consent applies to (per RU/UZ data-protection law)."""

    DATA_PROCESSING = "data_processing"
    MARKETING = "marketing"
    PERSONALIZATION = "personalization"


class Consent(Base):
    """Per-purpose, versioned, timestamped user consent.

    Append-style: a new row per (re)grant/revoke; current state is the latest row
    per (user, purpose). No endpoints at Stage 1 — model + table only.
    """

    __tablename__ = "consent"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[ConsentPurpose] = mapped_column(
        Enum(ConsentPurpose, name="consent_purpose", create_type=True)
    )
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(50))

    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="consents")

    def __repr__(self) -> str:
        return f"<Consent(id={self.id}, user_id={self.user_id}, purpose={self.purpose})>"


class Gender(enum.StrEnum):
    """Customer-declared gender (optional survey field)."""

    MALE = "male"
    FEMALE = "female"


class LoyaltyStatus(enum.StrEnum):
    """Loyalty tier. ``newbie`` → ``biotact_friend`` after 10 lifetime activations."""

    NEWBIE = "newbie"
    BIOTACT_FRIEND = "biotact_friend"


class CustomerProfile(Base):
    """1:1 loyalty/CRM profile attached to a ``users`` row.

    Holds the progressive survey + loyalty status/counters. Created lazily once a
    customer enters the loyalty flow (Stage ≥4); absence breaks nothing. Balance is
    NOT stored here — it is ``SUM(point_ledger)`` (Stage 2). No endpoints at Stage 1.
    """

    __tablename__ = "customer_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    # Progressive survey (filled over time; absence = not asked yet).
    gender: Mapped[Gender | None] = mapped_column(Enum(Gender, name="gender", create_type=True))
    age: Mapped[int | None] = mapped_column(Integer)
    city: Mapped[str | None] = mapped_column(String(100))
    purchase_goal: Mapped[str | None] = mapped_column(String(50))
    purchase_for_whom: Mapped[str | None] = mapped_column(String(50))

    # 18+ self-confirmation (Stage 5 lottery — legal condition of entry). NOT NULL with
    # server_default false so existing rows backfill without a data migration. A stored false
    # never wrongly excludes an existing member: eligibility grandfathers anyone with age>=18
    # (see loyalty.lottery.compute_ticket_count); the checkbox is what new members tick.
    is_adult: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    # Loyalty status + counters.
    status: Mapped[LoyaltyStatus] = mapped_column(
        Enum(LoyaltyStatus, name="loyalty_status", create_type=True),
        default=LoyaltyStatus.NEWBIE,
        server_default=LoyaltyStatus.NEWBIE.name,  # PG stores StrEnum as NAME
    )
    lifetime_activations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_activation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accrual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_activated_product_id: Mapped[int | None] = mapped_column(BigInteger)
    registration_source: Mapped[str | None] = mapped_column(String(20))

    # Growing survey blob — extend without migrations.
    survey_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="profile")

    def __repr__(self) -> str:
        return f"<CustomerProfile(id={self.id}, user_id={self.user_id}, status={self.status})>"


class CustomerEventType(enum.StrEnum):
    """Canonical customer-event vocabulary.

    Stored as a free string (column is ``String``, NOT a PG enum) so the set can
    grow without a migration per new type. These constants exist to avoid magic
    strings in the loyalty services (Stage ≥2).
    """

    JOINED = "joined"
    PHONE_SHARED = "phone_shared"
    QR_ACTIVATED = "qr_activated"
    SURVEY_DONE = "survey_done"
    STATUS_FRIEND = "status_friend"
    RAFFLE_ENTRY = "raffle_entry"
    ORDER_PLACED = "order_placed"
    POINTS_SPENT = "points_spent"
    POINTS_EXPIRED = "points_expired"


class CustomerEvent(Base):
    """Append-only customer timeline.

    High-volume: queried by ``user_id`` directly (no ORM relationship on ``User``),
    never updated or deleted in code — rows disappear only via the DB
    ``ON DELETE CASCADE`` when a user is removed. ``event_type`` is a free string
    (constants in :class:`CustomerEventType`), not a PG enum. No endpoints at Stage 1.
    """

    __tablename__ = "customer_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<CustomerEvent(id={self.id}, user_id={self.user_id}, event_type={self.event_type})>"
        )


class LedgerReason(enum.StrEnum):
    """Canonical point-ledger reason vocabulary (stored as a free String, NOT a PG enum).

    Stage 2 writes only ``CASHBACK`` and ``WELCOME``. The rest are the canonical set
    for later stages (``FRIEND_BONUS`` = Stage 3; ``REDEEM``/``EXPIRY`` = Stage 6;
    ``REFERRAL``/``BIRTHDAY``/``REVIEW``/``SUBSCRIPTION``/``L3`` = Stage 7). A String
    column lets the vocabulary grow without a migration per reason — same approach as
    :class:`CustomerEventType`.
    """

    CASHBACK = "cashback"
    WELCOME = "welcome"
    FRIEND_BONUS = "friend_bonus"
    REFERRAL = "referral"
    BIRTHDAY = "birthday"
    REVIEW = "review"
    SUBSCRIPTION = "subscription"
    L3 = "l3"
    REDEEM = "redeem"
    EXPIRY = "expiry"


class PointLedger(Base):
    """Append-only points ledger. Balance is ``SUM(amount)`` — never a stored column.

    Money truth: written once, never updated or deleted. ``amount`` is a signed integer
    of UZS-points (1 балл = 1 сум) — positive on earn (cashback/welcome/…), negative on
    redeem/expiry. Earn rows snapshot their computation inputs (``product_id``/``unit_price``/
    ``rate_bps``) so each row is self-contained and audit-reconcilable independently of
    later price or status changes; non-earn rows leave them NULL. ``reason`` is a free
    String (constants in :class:`LedgerReason`), not a PG enum.

    Idempotency: partial ``UNIQUE(qr_code_id, reason)`` (earn rows only) — at most one
    cashback and one welcome per activated code, even under a concurrent claim race.
    ``qr_code_id`` is a plain column here; its FK to ``qr_codes`` is added in PR-2.2,
    where that table is created. No endpoints at PR-2.1.
    """

    __tablename__ = "point_ledger"
    __table_args__ = (
        Index(
            "uq_point_ledger_qr_code_id_reason",
            "qr_code_id",
            "reason",
            unique=True,
            postgresql_where=text("qr_code_id IS NOT NULL"),
        ),
        # Redeem side of the same guarantee: one redeem row per order. A double-submit that races
        # past the balance check still cannot debit the same order twice.
        Index(
            "uq_point_ledger_order_id_reason",
            "order_id",
            "reason",
            unique=True,
            postgresql_where=text("order_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(32), index=True)

    # Idempotency key for earn rows tied to a QR activation. RESTRICT keeps money truth
    # intact: a qr_code with ledger rows cannot be deleted (user deletion removes the
    # ledger rows first via user_id CASCADE, so it never blocks deleting a user).
    qr_code_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("qr_codes.id", ondelete="RESTRICT", name="fk_point_ledger_qr_codes"),
        index=True,
    )

    # Idempotency key for the redeem row of an order (Stage 6). Mirrors ``qr_code_id`` on the earn
    # side: at most one ``redeem`` per order, even under a concurrent double-submit.
    #
    # RESTRICT blocks a DIRECT ``DELETE FROM orders`` — and only that. It does NOT survive a cascade
    # through the user: ``point_ledger.user_id`` and ``orders.user_id`` are both ON DELETE CASCADE,
    # so deleting a user first removes this very row, and RESTRICT then has no witness left to
    # protect. Verified on the real schema: DELETE FROM users takes the user, the order and the
    # accounting row with it, silently. There is no DELETE /users endpoint today, so nothing
    # exploits it — but a "delete my data" (PDn/GDPR) feature would, and the fix is an architectural
    # call (RESTRICT on orders.user_id too? soft-delete users with financial history?), not a column
    # tweak. Tracked in the Stage 6 spec backlog; do NOT paper over it here.
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT", name="fk_point_ledger_order"), index=True
    )

    # Provenance snapshot — earn rows only; NULL for redeem/expiry/adjust.
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT", name="fk_point_ledger_product"), index=True
    )
    unit_price: Mapped[int | None] = mapped_column(BigInteger)
    rate_bps: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<PointLedger(id={self.id}, user_id={self.user_id}, "
            f"reason={self.reason}, amount={self.amount})>"
        )


class QrCodeStatus(enum.StrEnum):
    """QR lifecycle, stored as a free String (not a PG enum). No ``armed`` state —
    codes are active on print (anti-fraud arming deferred; leaked-code risk accepted).
    """

    GENERATED = "generated"
    USED = "used"


class CodeBatch(Base):
    """A generated batch of unique QR codes for one product (printed onto boxes)."""

    __tablename__ = "code_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT", name="fk_code_batches_product"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<CodeBatch(id={self.id}, product_id={self.product_id}, quantity={self.quantity})>"


class QrCode(Base):
    """A unique QR code bound to one product. The code string is an opaque random token;
    the product it carries is the authoritative ``product_id`` FK (never parsed from the
    string, which could be spoofed). Status flips ``generated`` → ``used`` atomically on
    activation (PR-2.3b); ``UNIQUE(code)`` is the lookup key for the bot deeplink ``qr_<code>``.
    """

    __tablename__ = "qr_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT", name="fk_qr_codes_product"), index=True
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("code_batches.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=QrCodeStatus.GENERATED,
        server_default=QrCodeStatus.GENERATED.value,
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SET NULL on user delete: the physical code stays 'used' in history, activator forgotten.
    activated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<QrCode(id={self.id}, code={self.code}, "
            f"product_id={self.product_id}, status={self.status})>"
        )


class LotteryDrawStatus(enum.StrEnum):
    """Monthly-draw lifecycle, stored as a free String (not a PG enum) — same approach as
    :class:`QrCodeStatus`. ``open`` (month is accumulating) → ``committed`` (participant
    snapshot frozen + hash published) → ``drawn`` (winning numbers recorded).
    """

    OPEN = "open"
    COMMITTED = "committed"
    DRAWN = "drawn"


class LotteryTicketSource(enum.StrEnum):
    """Why a ticket exists (String, not PG enum).

    ``survey`` and ``friend`` are earned once in a lifetime, for reaching a state: a filled survey,
    and the ``biotact_friend`` status that takes ten activations. ``activation`` is the renewable
    one — a purchase — and only one of them may be unplayed at a time, so buying twice before a draw
    does not quietly outweigh a status that costs ten purchases to reach.
    """

    SURVEY = "survey"
    FRIEND = "friend"
    ACTIVATION = "activation"


class LotteryDraw(Base):
    """One monthly prize draw. Low-volume (one row per calendar month).

    ``period`` names the calendar month being drawn (``YYYY-MM``); the live draw airs on the 1st
    of the FOLLOWING month, with the participant snapshot frozen 24h before. Fairness is
    commit-reveal: at commit (PR-5.3) we snapshot every eligible participant's tickets into
    ``lottery_tickets`` with sequential numbers, publish ``participant_hash`` (SHA-256 of the
    canonical ticket list), and later (PR-5.4) record the winning numbers drawn LIVE via
    random.org — the winner is never chosen by this code. Dark behind ``loyalty_enabled``; no
    endpoints at PR-5.1 (schema only).

    ⚠️ ``open`` is dead and stays dead. :func:`~src.loyalty.lottery.commit_draw` creates the row
    already ``committed``, and the ratified model has no scheduling at all — a draw is frozen by an
    administrator pressing a button, never opened ahead of time. The value is REMOVED in the
    contract phase (Stage-5 step 6), not revived: PR-5.7, which promised to make it real and to
    bring the row lock back with it, was cancelled 2026-07-30 together with the model it belonged
    to. What serialises the freeze instead is a conditional ``UPDATE`` over the clean tickets — the
    same device :func:`~src.loyalty.lottery.record_winners` already uses.
    """

    __tablename__ = "lottery_draws"

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True)  # "YYYY-MM" — month being drawn
    # Public identity of a draw once scheduling is gone: "розыгрыш №1". Values come from a database
    # identity (a sequence), never from ``MAX(draw_number) + 1`` — two concurrent freezes would read
    # the same maximum and collide. ``GENERATED BY DEFAULT AS IDENTITY`` also means an INSERT that
    # omits the column still gets a number, which is what keeps the pre-step-3 ``commit_draw``
    # working unchanged after this migration lands.
    draw_number: Mapped[int] = mapped_column(Integer, Identity(always=False), unique=True)
    prize_label: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(16),
        default=LotteryDrawStatus.OPEN,
        server_default=LotteryDrawStatus.OPEN.value,
    )
    total_tickets: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    participant_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256 hex, set on commit
    hash_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the draw is held. A DATE, not a timestamp: the value is announced to people ("01.09.2026")
    # and never computed against, so storing an instant would only invite timezone arithmetic that
    # nothing needs. Optional by design — the system's own fact is the freeze, not this date — and
    # deliberately OUTSIDE the published hash, so postponing a draw cannot break a fingerprint that
    # has already been published.
    conducted_on: Mapped[date | None] = mapped_column(Date())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Audit trail of a cancelled draw: the canonical snapshot TEXT — the exact bytes the published
    # SHA-256 was taken over. Kept verbatim rather than "the composition in some form" so anyone can
    # re-hash it and check it against ``participant_hash`` in this same row; a journal that cannot be
    # verified only asserts that we kept something.
    cancelled_snapshot: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<LotteryDraw(id={self.id}, period={self.period}, status={self.status})>"


class LotteryTicket(Base):
    """One frozen lottery entry inside a committed draw. High-volume (up to one row per ticket
    per participant per month), so BigInteger PK and no ORM relationship back to ``User`` —
    queried by ``draw_id`` / ``user_id`` directly. Written once at commit; only ever mutated to
    mark winners (``is_winner`` / ``prize_rank``) after the live draw.

    ``ticket_number`` is sequential within a draw (1..``total_tickets``); ``UNIQUE(draw_id,
    ticket_number)`` makes the number the stable public identifier the published hash covers.
    ``prize_rank`` is 1 (main prize) or 2..4 (consolation), NULL for non-winners.
    """

    __tablename__ = "lottery_tickets"
    __table_args__ = (
        Index("uq_lottery_tickets_draw_number", "draw_id", "ticket_number", unique=True),
        # The one hot query of the ticket-as-a-record model: a member's CLEAN tickets — for the
        # ceiling of two and for the freeze that stamps them. PARTIAL on purpose: clean tickets are
        # a small, constantly-shrinking slice (every freeze empties it), while the full history keeps
        # growing, so indexing the whole table for this question would pay for rows the question
        # never asks about. The existing full ``ix_lottery_tickets_user_id`` still serves history
        # reads.
        Index(
            "ix_lottery_tickets_clean_user",
            "user_id",
            postgresql_where=text("draw_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # NULL while the ticket is clean: a ticket now belongs to its owner, not to a draw, and only a
    # freeze stamps these two columns. ``UNIQUE(draw_id, ticket_number)`` keeps working exactly as
    # before, because PostgreSQL treats NULLs as distinct — any number of clean ``(NULL, NULL)`` rows
    # coexist, while numbers stay unique inside a draw.
    draw_id: Mapped[int | None] = mapped_column(
        ForeignKey("lottery_draws.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticket_number: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))
    # Marks the rows written by the one-off backfill, so that backfill — and only backfill — can be
    # undone. A ``created_at`` window is not a marker: ordinary issuance happens in the same window.
    backfill_batch: Mapped[str | None] = mapped_column(String(32))
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    prize_rank: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<LotteryTicket(id={self.id}, draw_id={self.draw_id}, "
            f"user_id={self.user_id}, number={self.ticket_number})>"
        )
