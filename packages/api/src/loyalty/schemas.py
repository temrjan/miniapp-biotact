"""Loyalty API schemas (Stage 2): QR activation request/response."""

import enum
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from src.loyalty.models import Gender, LoyaltyStatus


class ActivationStatus(enum.StrEnum):
    """Outcome of an activation call."""

    ACTIVATED = "activated"  # the code was claimed by this call; points were accrued
    ALREADY_ACTIVATED = "already_activated"  # same user re-scanned an own code (idempotent replay)


class RegistrationSource(enum.StrEnum):
    """How the customer entered the loyalty program (persisted once via COALESCE)."""

    QR = "qr"  # opened the mini-app from a scanned QR box code
    DIRECT = "direct"  # opened the mini-app without a code (no points)


# Opaque code: Crockford base32, ~12 chars (the qr_codes.code column is String(32)).
# Trim surrounding whitespace; an unknown/garbage code simply fails the lookup (404).
ActivationCode = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]


class ActivateRequest(BaseModel):
    """Body of ``POST /qr/activate`` — the opaque code carried by the scanned QR."""

    code: ActivationCode


class ActivateResponse(BaseModel):
    """Result of an activation: what was earned now and the user's running balance.

    All amounts are whole points (1 балл = 1 сум). On an idempotent replay
    (``already_activated``) ``points_awarded`` echoes what the code originally earned and
    ``welcome_bonus`` is 0 — no new accrual happens.
    """

    status: ActivationStatus
    tier: LoyaltyStatus = Field(
        description="Loyalty tier after this activation (newbie | biotact_friend)"
    )
    points_awarded: int = Field(
        ge=0, description="Points accrued by this code (cashback + welcome + friend_bonus)"
    )
    welcome_bonus: int = Field(ge=0, description="Welcome half-rate bonus (first activation only)")
    friend_bonus: int = Field(
        ge=0, description="One-time bonus on reaching 'Друг' status (0 otherwise / on replay)"
    )
    balance: int = Field(ge=0, description="Current total balance = SUM(point_ledger.amount)")
    lifetime_activations: int = Field(ge=0, description="Lifetime activations after this one")


class ConsentRequest(BaseModel):
    """Body of ``POST /loyalty/consent`` — the mini-app records data-processing consent.

    ``source`` reflects how the user entered: ``qr`` when a box code was scanned, ``direct``
    otherwise. The mini-app knows this from the presence of ``?code=`` in its launch URL.
    """

    source: RegistrationSource


class ConsentResponse(BaseModel):
    """Result of a consent call — makes idempotency observable (all False on a repeat call).

    Each flag is True only when THIS call performed that write; a second call by the same user
    returns all False (the consent row, profile and phone-shared event already exist).
    """

    consent_recorded: bool = Field(description="A new data_processing consent row was written")
    joined: bool = Field(description="The customer profile was created by this call")
    phone_shared: bool = Field(description="A phone_shared timeline event was written by this call")


class LoyaltySummaryResponse(BaseModel):
    """Read-only loyalty snapshot for the mini-app profile card (GET /loyalty/me)."""

    tier: LoyaltyStatus = Field(description="Loyalty tier (newbie | biotact_friend)")
    balance: int = Field(ge=0, description="Current point balance = SUM(point_ledger.amount)")
    # No ge= bound here, on purpose — same reasoning as the survey echo below. The service already
    # clamps this to >= 0; a duplicated bound would add nothing on the happy path and would turn a
    # data anomaly into a 500 for the whole profile card instead of one wrong number.
    available: int = Field(
        description="Points spendable right now = balance minus this user's live reservations"
    )
    reserved_until: datetime | None = Field(
        description=(
            "When the earliest live reservation expires; null when nothing is reserved. "
            "Reservations are reclaimed by the customer's next redeem attempt, not by a timer — "
            "phrase this to the customer as 'try again after', never as 'points return at'."
        )
    )
    lifetime_activations: int = Field(ge=0, description="Lifetime QR activations")
    activations_to_friend: int = Field(
        ge=0, description="Activations remaining until 'Друг' (0 once a friend)"
    )
    is_member: bool = Field(
        description="Whether the user has joined the club (a customer_profile row exists)"
    )
    # Survey echo — deliberately UNCONSTRAINED Optional types (no SurveyAge/StringConstraints):
    # the response mirrors what is stored, it does not validate input; a copied ge/le bound
    # would 500 the whole /me for a member whose stored value ever falls outside it.
    gender: Gender | None = Field(description="Survey gender; null until answered")
    age: int | None = Field(description="Survey age; null until answered")
    city: str | None = Field(description="Survey city; null until answered")
    # NOT part of the survey echo above: a bool column with a server_default, so "never asked" and
    # "declined" both read as false. Echoed so the mini-app renders the checkbox as the member left
    # it — a legal self-confirmation must never look un-given after a reload.
    is_adult: bool = Field(description="18+ self-confirmation (lottery entry); false until ticked")


SurveyAge = Annotated[int, Field(ge=1, le=120)]
SurveyCity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SurveyRequest(BaseModel):
    """Body of ``POST /loyalty/survey`` — progressive, optional profile fields.

    A present field always overwrites the stored value (no ``COALESCE``) so a member can
    correct/extend the survey on a later call. An all-empty body is rejected (422): a survey
    call that writes nothing is meaningless.

    ``is_adult`` (Stage 5) rides on the same endpoint but is NOT a demographic answer: it is the
    18+ self-confirmation required to enter the lottery. It counts as a field here — a body
    carrying only the checkbox is valid — while the service keeps it out of the one-time
    ``survey_done`` event (see :func:`service.submit_survey`).
    """

    gender: Gender | None = None
    age: SurveyAge | None = None
    city: SurveyCity | None = None
    is_adult: bool | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "SurveyRequest":
        if self.gender is None and self.age is None and self.city is None and self.is_adult is None:
            raise ValueError("At least one of gender, age, city, is_adult is required")
        return self


# Which fields of :class:`SurveyRequest` mean "the member told us about themselves". Lives here,
# beside the model that owns the field list, and is written out BY HAND on purpose: deriving it by
# subtraction (``set(model_fields) - {"is_adult"}``) would silently promote the next
# non-demographic field — the planned «Правила акции» checkbox — into demographics, and a lone
# consent tick would start spending ``survey_done`` again. A pinning test in
# ``tests/test_loyalty_survey.py`` fails on any unclassified field, so whoever adds the next one
# has to place it deliberately.
DEMOGRAPHIC_SURVEY_FIELDS = frozenset({"gender", "age", "city"})
NON_DEMOGRAPHIC_SURVEY_FIELDS = frozenset({"is_adult"})


class SurveyResponse(BaseModel):
    """Result of a survey call — makes idempotency of the ``survey_done`` event observable."""

    updated_fields: list[str] = Field(description="Fields written by THIS call (payload keys)")
    survey_done: bool = Field(
        description="True only if THIS call wrote the one-time survey_done event"
    )


# ⚠️ Dead since step 3, removed with the column in the contract phase (step 6): a freeze targets no
# calendar month, so there is no period left to validate at the edge.
DrawPeriod = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
]


class CommitDrawRequest(BaseModel):
    """Body of ``POST /admin/lottery/commit`` — freeze the clean tickets into a new draw."""

    expected_tickets: int = Field(
        ge=1,
        description=(
            "How many tickets the administrator expects to freeze — the mechanical confirmation "
            "of an irreversible action. Must equal the number of ELIGIBLE clean tickets at the "
            "moment of the call, otherwise nothing is frozen and the actual number is reported."
        ),
    )
    prize_label: str | None = Field(default=None, max_length=200, description="Marketing label")
    conducted_on: date | None = Field(
        default=None,
        description="Announced date of the draw. Optional, editable, outside the published hash",
    )


class CommitDrawResponse(BaseModel):
    """What was frozen — the numbers an administrator publishes for external verification."""

    draw_id: int
    draw_number: int = Field(ge=1, description="Public identity of the draw: «розыгрыш №N»")
    total_tickets: int = Field(
        ge=1, description="Tickets in the snapshot; a draw is never frozen empty"
    )
    participant_hash: str = Field(
        description="SHA-256 (lowercase hex) of the canonical ticket list"
    )
    committed_at: datetime
    conducted_on: date | None


class CancelDrawResponse(BaseModel):
    """What cancelling released. The canonical snapshot stays on the draw row as the audit trail."""

    draw_id: int
    draw_number: int
    cancelled_at: datetime
    released_tickets: int = Field(
        ge=0, description="Tickets that went back to their owners as clean tickets"
    )


class UpdateDrawRequest(BaseModel):
    """Body of ``PATCH /admin/lottery/{draw_id}`` — the announced date, set or cleared.

    Only field, and deliberately so: everything else about a frozen draw is either published or
    derived, and a draw is not an editable document.
    """

    conducted_on: date | None = Field(
        default=None, description="New announced date; null clears it back to «дата уточняется»"
    )


class UpdateDrawResponse(BaseModel):
    """The draw after the edit."""

    draw_number: int
    conducted_on: date | None


class RecordWinnersRequest(BaseModel):
    """Body of ``POST /admin/lottery/{id}/winners`` — the numbers random.org produced, in order.

    Order is meaningful: the first number takes the main prize, the rest follow as ranks 2, 3, …
    No upper bound on the count — the prize fund is a marketing decision that changes month to
    month, and a hardcoded ceiling would be a rule nobody ratified. The real ceiling is the number
    of tickets in the draw, enforced against the snapshot.
    """

    winning_numbers: list[int] = Field(
        min_length=1,
        description="Ticket numbers drawn live, in prize order (first = main prize)",
    )


class WinnerRowResponse(BaseModel):
    """One recorded winner.

    Carries ``first_name`` and no phone (decision 2026-07-28): the admin panel's customer list
    searches by name or phone and cannot search by internal id, so a name is the minimum that makes
    a manual lookup possible — while no contact detail leaves this endpoint.
    """

    ticket_number: int
    prize_rank: int = Field(ge=1, description="1 = main prize, then 2, 3, … by entry order")
    user_id: int
    first_name: str | None


class RecordWinnersResponse(BaseModel):
    """What was recorded — the draw is closed and cannot be replayed."""

    draw_id: int
    draw_number: int
    status: str
    drawn_at: datetime
    winners: list[WinnerRowResponse]


class CurrentDrawInfo(BaseModel):
    """The latest draw as the admin page shows it, of ANY status — cancelled included (P-2)."""

    draw_id: int
    draw_number: int
    status: str
    total_tickets: int
    participant_hash: str | None
    conducted_on: date | None
    prize_label: str | None
    committed_at: datetime | None
    drawn_at: datetime | None
    cancelled_at: datetime | None
    winners: list[WinnerRowResponse]


class CurrentDrawResponse(BaseModel):
    """Body of ``GET /admin/lottery/current`` — the draw page's single read.

    ``eligible_tickets`` is the POST-FILTER count — the same function the freeze stamps, so the
    number the administrator confirms and the number the freeze takes cannot disagree by
    construction. ``clean_tickets`` counts every clean ticket, including owners who no longer
    qualify; the page shows both so the difference is visible.
    """

    eligible_tickets: int
    clean_tickets: int
    draw: CurrentDrawInfo | None


class PrintRowResponse(BaseModel):
    """One line of the printed list. ``telegram_last4`` is zero-padded — ``0042``, not ``42``."""

    ticket_number: int
    telegram_last4: str = Field(min_length=4, max_length=4)


class PrintListResponse(BaseModel):
    """Body of ``GET /admin/lottery/{draw_id}/print`` — the printed list, numbers ascending.

    The full ``telegram_id`` never appears here: the four digits are a projection computed on the
    server. ``total_tickets`` is repeated outside the rows because it sets the range of the draw
    itself — the reason the list exists.
    """

    draw_number: int
    conducted_on: date | None
    total_tickets: int
    rows: list[PrintRowResponse]


class DrawResultResponse(BaseModel):
    """How the previous draw ended for this member (``GET /loyalty/my-tickets``)."""

    period: str = Field(
        description=(
            "Label of the draw that ended, «№3». Still named `period` until step 5 rewrites the "
            "card: the member-facing strings interpolate it, and renaming the field would break "
            "mini-app bundles already cached in members' Telegram clients."
        )
    )
    tickets: int = Field(ge=0, description="Tickets this member held in that draw")
    won: bool = Field(description="Whether any of their tickets was drawn")
    prize_rank: int | None = Field(
        description="Best rank taken (1 = main prize); null when they did not win"
    )
    prize_label: str | None = Field(description="Prize of that draw; null when it was not set")


class MyTicketsResponse(BaseModel):
    """The member's own participation (``GET /loyalty/my-tickets``).

    The top-level fields describe what the member holds NOW; ``result`` describes how the PREVIOUS
    draw ended. They refer to different draws and are deliberately not merged — after a finished
    draw the top block already counts towards the next one.
    """

    tickets: int = Field(ge=0, description="Tickets shown to the member right now")
    is_frozen: bool = Field(
        description=(
            "True when the number comes from a frozen snapshot and can no longer change; "
            "False when it is a live recount that may still change on its own. The wording shown "
            "to the member MUST differ between the two — that distinction is the point."
        )
    )
    period: str | None = Field(
        description=(
            "Label of the live draw, «№3»; null exactly when is_frozen is false. Legacy field name — "
            "see DrawResultResponse.period."
        )
    )
    prize_label: str | None = Field(
        description="Prize of the frozen draw; null unless a frozen draw declares one"
    )
    result: DrawResultResponse | None = Field(
        description="Present only once the latest draw has been drawn"
    )
