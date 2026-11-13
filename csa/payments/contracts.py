"""Pydantic contracts for payments: invoice statuses, payment methods and payment settings.

Money is `Decimal` (a string in JSON), never a float. Every model refuses fields it does not name.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

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
        self.details = validate_details(self.kind, self.details)
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
