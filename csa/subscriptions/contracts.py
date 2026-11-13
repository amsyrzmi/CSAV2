"""Pydantic contracts for the subscriptions console tab."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from csa.console.contracts import Contract
from csa.payments.contracts import InvoiceStatus

Status = Literal["pending", "active", "paused", "cancelled"]
Interval = Literal["month", "year"]
SignupMode = Literal["staff", "auto"]


class PlanOut(Contract):
    code: str
    name: str
    price: Decimal
    currency: str
    billing_interval: Interval
    features: list[str]
    sort_order: int
    is_active: bool
    signup_mode: SignupMode  # staff decide a sign-up, or it is approved at once
    is_signup_open: bool  # false: customers cannot sign up for the plan in the chat


class SettingsOut(Contract):
    timezone: str  # the tenant's IANA zone (clients.timezone), for showing dates
    pending_expiry_days: int  # days a pending subscription waits before it expires; 0 = never
    version: int  # 0 until the company has saved settings; PUT /settings echoes it back


class SubscriptionSettingsIn(Contract):
    pending_expiry_days: int = Field(ge=0, le=90)
    version: int = Field(ge=0)


class PlanIn(Contract):
    code: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = "MYR"
    billing_interval: Interval = "month"
    features: list[str] = Field(default_factory=list, max_length=20)
    sort_order: int = 0
    signup_mode: SignupMode = "staff"
    is_signup_open: bool = True


class PlanPatch(Contract):
    # None means "leave unchanged"; the bounds mirror PlanIn so a patch cannot store
    # what creation would refuse.
    name: str | None = Field(default=None, min_length=1, max_length=120)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    billing_interval: Interval | None = None
    features: list[str] | None = Field(default=None, max_length=20)
    sort_order: int | None = None
    is_active: bool | None = None
    signup_mode: SignupMode | None = None
    is_signup_open: bool | None = None


class CustomerRef(Contract):
    id: UUID
    full_name: str | None
    phone: str | None
    email: str | None


class SubscriptionOut(Contract):
    id: UUID
    customer: CustomerRef
    plan: str
    plan_name: str | None
    status: Status
    price: Decimal
    currency: str
    started_at: datetime
    renews_at: datetime | None
    paused_at: datetime | None
    cancelled_at: datetime | None
    resume_at: date | None
    pending_plan: str | None
    pending_effective_at: datetime | None
    cancel_at_period_end: bool
    version: int


class SubscriptionPage(Contract):
    items: list[SubscriptionOut]
    next_cursor: UUID | None


class SubscriptionEventOut(Contract):
    id: int
    actor: str
    action: str
    from_status: str | None
    to_status: str | None
    payload: dict[str, Any]
    created_at: datetime


# The console already publishes an `EventOut` schema (runtime events); a second class with that
# name would make FastAPI rename both in the OpenAPI document, so the schema name stays distinct.
EventOut = SubscriptionEventOut


class InvoiceBrief(Contract):
    """An open invoice as the subscription detail shows it (no staff-only fields)."""

    id: str
    reference: str | None  # null only on an invoice written before references existed
    status: InvoiceStatus
    amount: Decimal
    currency: str
    due_at: datetime | None


class SubscriptionDetail(Contract):
    subscription: SubscriptionOut
    events: list[SubscriptionEventOut]
    # The subscription's newest open invoice (issued, overdue, proof_submitted, cash_pending).
    open_invoice: InvoiceBrief | None


class SubscriptionCreateIn(Contract):
    contact_id: UUID
    plan_code: str
    started_at: datetime | None = None
    renews_at: datetime | None = None


Action = Literal[
    "activate",
    "pause",
    "resume",
    "change_plan",
    "cancel_at_period_end",
    "cancel_now",
    "extend_renewal",
]

# The optional fields each action accepts; any other one is refused so a card never shows
# less than the payload carries.
ACTION_FIELDS: dict[str, frozenset[str]] = {
    "activate": frozenset(),
    "pause": frozenset({"resume_at"}),
    "resume": frozenset(),
    "change_plan": frozenset({"plan_code", "effective_at", "price_override"}),
    "cancel_at_period_end": frozenset(),
    "cancel_now": frozenset(),
    "extend_renewal": frozenset({"renews_at"}),
}


class ActionIn(Contract):
    action: Action
    resume_at: date | None = None  # pause only (optional)
    plan_code: str | None = Field(default=None, min_length=1, max_length=64)  # change_plan
    effective_at: AwareDatetime | None = None  # change_plan: absent or past -> immediate
    renews_at: AwareDatetime | None = None  # extend_renewal (required)
    price_override: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _per_action(self) -> ActionIn:
        allowed = ACTION_FIELDS[self.action]
        for name in ("resume_at", "plan_code", "effective_at", "renews_at", "price_override"):
            if getattr(self, name) is not None and name not in allowed:
                raise ValueError(f"{name} does not apply to {self.action}")
        if self.action == "change_plan" and self.plan_code is None:
            raise ValueError("plan_code is required for change_plan")
        if self.action == "extend_renewal" and self.renews_at is None:
            raise ValueError("renews_at is required for extend_renewal")
        if self.price_override is not None and not (self.reason and self.reason.strip()):
            raise ValueError("price_override requires a reason")
        return self


RequestAction = Literal["request_pause", "request_cancel", "request_change"]


class RequestOut(Contract):
    id: UUID
    # Absent only when the agent's payload named none; such a request can be declined, not approved.
    subscription_id: UUID | None
    customer: CustomerRef
    action: RequestAction
    plan_code: str | None
    reason: str | None
    requested_at: datetime
    decision: Literal["approved", "declined"] | None
    decided_at: datetime | None
    decided_by: UUID | None
    note: str | None
    proposal_id: UUID | None


class RequestPage(Contract):
    items: list[RequestOut]
    next_cursor: UUID | None


class DecideIn(Contract):
    decision: Literal["approve", "decline"]
    note: str | None = Field(default=None, max_length=1000)
    resume_at: date | None = None  # pause approvals may set a resume date

    @model_validator(mode="after")
    def _resume_only_when_approving(self) -> DecideIn:
        if self.resume_at is not None and self.decision != "approve":
            raise ValueError("resume_at applies to an approval")
        return self


SignupStatus = Literal["pending", "approved", "declined", "expired"]


class SignupOut(Contract):
    id: UUID
    contact: CustomerRef  # as the contact record stands, not as the customer typed it
    plan_code: str
    plan_name: str | None
    full_name: str  # as typed in the sign-up
    phone: str  # as typed in the sign-up (normalised); never copied to the contact
    # The contact's phone is verified by its channel and is this number; a different typed number
    # reads false.
    phone_verified: bool
    status: SignupStatus
    decided_via: Literal["staff", "auto"] | None
    decline_reason: str | None
    subscription_id: UUID | None
    created_at: datetime
    decided_at: datetime | None
    source_channel: str | None  # the channel of the contact's newest identity
    # The reference of the approved subscription's first invoice (issued at approval while
    # payments are on); null when none was issued or it is void.
    invoice_reference: str | None
    # The address the customer gave in the chat for payment reminders (asked on WhatsApp while
    # payments are on), lower case; a claim nobody checked. Null when none was given.
    email: str | None


class SignupPage(Contract):
    items: list[SignupOut]
    next_cursor: UUID | None


class DeclineIn(Contract):
    reason: str | None = Field(default=None, max_length=300)
