"""Pydantic contracts for payments: invoice statuses, payment methods and payment settings.

Money is `Decimal` (a string in JSON), never a float. Every model refuses fields it does not name.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from csa.console.contracts import Contract

InvoiceStatus = Literal[
    "draft", "issued", "proof_submitted", "paid", "overdue", "void", "cash_pending"
]
MethodKind = Literal["qr", "transfer", "cash"]

# What a customer may be told about: never a draft or a void invoice.
CUSTOMER_VISIBLE = frozenset({"issued", "overdue", "proof_submitted", "cash_pending", "paid"})
# The invoices a company still waits for (the same list as the invoices_open index).
OPEN_STATUSES = frozenset({"issued", "overdue", "proof_submitted", "cash_pending"})

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Instructions = Annotated[str, StringConstraints(max_length=500)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
SortOrder = Annotated[int, Field(ge=0, le=100)]
ReminderDay = Annotated[int, Field(ge=-30, le=60)]


class TransferDetails(Contract):
    """A bank transfer: where the customer sends the money."""

    bank_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    account_name: Name
    account_number: str = Field(pattern=r"^[0-9]{6,20}$")


class QrDetails(Contract):
    """A DuitNow QR: the id the QR resolves to."""

    duitnow_id: str = Field(pattern=r"^[0-9A-Za-z-]{4,40}$")
    account_name: Name


class CashDetails(Contract):
    """Cash needs no details (Payments 2 pays it through a booking)."""


DETAILS: dict[str, type[Contract]] = {
    "transfer": TransferDetails,
    "qr": QrDetails,
    "cash": CashDetails,
}


def validate_details(kind: str, details: dict[str, Any]) -> dict[str, Any]:
    """`details` as the kind's model reads it (a pydantic ValidationError when it does not fit)."""
    return DETAILS[kind].model_validate(details).model_dump()


class MethodIn(Contract):
    kind: MethodKind
    label: Label
    details: dict[str, Any]
    instructions: Instructions = ""
    is_active: bool = True
    sort_order: SortOrder = 0

    @model_validator(mode="after")
    def details_fit_the_kind(self) -> MethodIn:
        # The error type `invalid_details` lets the console route answer 422 `invalid_details`,
        # the same body PATCH gives when the service finds the details do not fit.
        try:
            self.details = validate_details(self.kind, self.details)
        except ValidationError as exc:
            raise PydanticCustomError(
                "invalid_details", "details do not fit the method kind"
            ) from exc
        return self


class MethodPatch(Contract):
    """A change: None leaves a field as it is; `details` replaces the whole object.

    The service checks `details` against the stored kind. The kind itself cannot change.
    """

    label: Label | None = None
    details: dict[str, Any] | None = None
    instructions: Instructions | None = None
    is_active: bool | None = None
    sort_order: SortOrder | None = None
    version: int = Field(ge=1)


class MethodOut(Contract):
    id: UUID
    kind: MethodKind
    label: str
    details: dict[str, Any]
    instructions: str
    is_active: bool
    sort_order: int
    version: int


class PaymentSettingsOut(Contract):
    enabled: bool
    lead_days: int  # an invoice is issued this many days before its period starts
    reminder_days: list[int]  # days relative to the due date; negative is before
    suspend_after_days: int | None  # days overdue before the subscription pauses; None = never
    version: int  # 0 until the company has saved settings


class PaymentSettingsIn(Contract):
    enabled: bool
    lead_days: int = Field(ge=1, le=30)
    reminder_days: list[ReminderDay] = Field(max_length=6)
    suspend_after_days: int | None = Field(ge=1, le=90)
    version: int = Field(ge=0)

    @field_validator("reminder_days")
    @classmethod
    def distinct_and_sorted(cls, days: list[int]) -> list[int]:
        if len(set(days)) != len(days):
            raise ValueError("reminder_days must be distinct")
        return sorted(days)


InvoiceFilter = Literal["open", "overdue", "proof_submitted", "paid", "void", "all"]
AttentionKind = Literal["proof_submitted", "needs_nudge", "reminder_failed"]
ReminderChannel = Literal["telegram", "whatsapp", "email", "none"]
ReminderStatus = Literal["queued", "sending", "sent", "failed", "dropped", "skipped", "no_channel"]
PaymentSource = Literal["statement", "staff", "cash"]
MatchedBy = Literal["auto", "staff"]

# The statuses each list filter shows. Drafts are never made in Payments 1; only `all` lists them.
FILTER_STATUSES: dict[str, tuple[str, ...]] = {
    "open": ("issued", "overdue", "proof_submitted", "cash_pending"),
    "overdue": ("overdue",),
    "proof_submitted": ("proof_submitted",),
    "paid": ("paid",),
    "void": ("void",),
    "all": ("draft", "issued", "proof_submitted", "paid", "overdue", "void", "cash_pending"),
}


class ContactBrief(Contract):
    """The invoice's customer: no phone, email or address, only the channels they are on."""

    id: UUID
    full_name: str | None
    channels: list[str]  # the distinct contact_identities channels of the contact now


class InvoiceOut(Contract):
    """An invoice as staff see it, staff-only fields (void reason and time) included."""

    id: str
    reference: str | None
    status: InvoiceStatus
    amount: Decimal
    currency: str
    issued_at: datetime
    due_at: datetime | None
    paid_at: datetime | None
    period_start: datetime | None
    period_end: datetime | None
    subscription_id: UUID | None
    plan_name: str | None  # the first line item's description, frozen at issue
    contact: ContactBrief | None
    claim_note: str | None
    claimed_at: datetime | None
    method_kind: MethodKind | None
    void_reason: str | None
    voided_at: datetime | None
    reminded_at: datetime | None


class PaymentOut(Contract):
    """The invoice's ledger row."""

    id: UUID
    amount: Decimal
    currency: str
    received_at: datetime
    method_kind: MethodKind | None
    source: PaymentSource
    matched_by: MatchedBy
    created_by_name: str | None  # the console user who recorded it; None for the system
    note: str | None
    created_at: datetime


class ReminderOut(Contract):
    """One reminder step. The address it went to is never shown."""

    step: int
    channel: ReminderChannel
    status: ReminderStatus
    created_at: datetime
    sent_at: datetime | None


class InvoiceDetail(InvoiceOut):
    payment: PaymentOut | None
    reminders: list[ReminderOut]  # oldest first


class InvoicePage(Contract):
    items: list[InvoiceOut]
    next_before: str | None  # pass back as `before` for the next page; None on the last page


class MarkPaidIn(Contract):
    request_key: UUID  # the same key again returns the first answer and records nothing
    method_kind: MethodKind
    received_at: AwareDatetime | None = None  # now when left out
    note: str | None = Field(default=None, max_length=300)


class VoidIn(Contract):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class PaymentAttentionItem(Contract):
    """Named apart from the email module's AttentionItem, which the console contract has."""

    kind: AttentionKind
    invoice: InvoiceOut
    since: datetime


class PaymentAttentionPage(Contract):
    items: list[PaymentAttentionItem]
