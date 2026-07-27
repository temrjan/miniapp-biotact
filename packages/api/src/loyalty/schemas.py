"""Loyalty API schemas (Stage 2): QR activation request/response."""

import enum
from datetime import datetime
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


class SurveyResponse(BaseModel):
    """Result of a survey call — makes idempotency of the ``survey_done`` event observable."""

    updated_fields: list[str] = Field(description="Fields written by THIS call (payload keys)")
    survey_done: bool = Field(
        description="True only if THIS call wrote the one-time survey_done event"
    )
