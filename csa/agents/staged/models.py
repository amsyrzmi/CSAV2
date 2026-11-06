"""Validated preparation contracts, separate from external execution commands."""

from __future__ import annotations

import ipaddress
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from csa.bookings.contracts import Email, Name


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_nul(cls, value: object) -> object:
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("NUL characters cannot be persisted")
        return value


class PreparedRequest(DraftModel):
    id: UUID
    action: str
    payload: dict[str, object]


# What the planner sees where the customer typed an address: `redact` numbers each one. The server
# swaps it for the address in the customer's own message before the agent runs.
EmailPlaceholder = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\[EMAIL_\d{1,3}\]$")
]


class BookingInput(DraftModel):
    """What the planner may ask the customer's booking agent to do.

    The planner is shown this schema as it stands, so the descriptions are its instructions. Every
    time is checked by the server: a wrong or invented one only gets the customer other times.
    """

    action: Literal[
        "availability", "book", "reschedule", "cancel", "list", "status", "save_details"
    ] = Field(
        description=(
            "availability: what can be booked, or the free times for one service. book: make a "
            "booking. reschedule: move one of the customer's bookings. cancel: cancel one. "
            "list: the customer's own bookings. status: one of the customer's bookings. "
            "save_details: save the customer's name or email, only what they gave in their "
            "message (for example when they answer a request for their email)."
        )
    )
    type_id: UUID | None = Field(
        default=None,
        description=(
            "The id of the service, taken from context.booking.types (or the facts of an "
            "availability lookup). Never invent one. Leave it out for availability to learn what "
            "can be booked."
        ),
    )
    day: date | None = Field(
        default=None,
        description=(
            "availability only: the day the customer asked about, as YYYY-MM-DD. Leave it out "
            "when they named no day."
        ),
    )
    part_of_day: Literal["morning", "afternoon", "evening"] | None = Field(
        default=None,
        description=(
            "availability only: when the customer asked about part of the day. morning is before "
            "12:00, afternoon 12:00 to 17:00, evening 17:00 or later. Leave it out otherwise."
        ),
    )
    starts_at: datetime | None = Field(
        default=None,
        description=(
            "book and reschedule: the start the customer chose, as the business's local date and "
            "time such as 2031-03-04T15:00 (no offset needed). Use only a time the customer "
            "stated or one from the facts."
        ),
    )
    booking_id: UUID | None = Field(
        default=None,
        description=(
            "reschedule, cancel and status: the id of the customer's booking, taken from "
            "context.booking.upcoming (or the facts of a list lookup). Never invent one."
        ),
    )
    confirmed: bool = Field(
        default=False,
        description=(
            "cancel only: true only when context.booking.cancel_asked is this booking_id and the "
            "customer's message clearly says yes to that question, in any language. Otherwise "
            "leave it out: the server then asks the customer to confirm first."
        ),
    )
    customer_name: Name | None = Field(
        default=None,
        description=(
            "book and save_details: the customer's name, only if they gave it in their message."
        ),
    )
    customer_email: Email | EmailPlaceholder | None = Field(
        default=None,
        description=(
            "book and save_details: the customer's email address, only if they gave one in their "
            "message. The message shows an address as a placeholder such as [EMAIL_0]: copy that "
            "placeholder exactly. Never write an address yourself."
        ),
    )

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_missing(cls, value: object) -> object:
        """A model fills an unused field with an empty string as often as it leaves it out."""
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        needs: dict[str, tuple[str, ...]] = {
            "book": ("type_id", "starts_at"),
            "reschedule": ("booking_id", "starts_at"),
            "cancel": ("booking_id",),
            "status": ("booking_id",),
        }
        for name in needs.get(self.action, ()):
            if getattr(self, name) is None:
                raise ValueError(f"{name} required")
        if self.action == "save_details" and not (self.customer_name or self.customer_email):
            raise ValueError("customer_name or customer_email required")
        return self


class SubscriptionInput(DraftModel):
    action: Literal["plans", "list", "status", "request_pause", "request_cancel", "request_change"]
    subscription_id: UUID | None = None
    plan_code: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$"
    )
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action not in {"plans", "list"} and self.subscription_id is None:
            raise ValueError("subscription_id required")
        if self.action == "request_change" and self.plan_code is None:
            raise ValueError("plan_code required")
        return self


def validate_mailbox_address(value: str) -> str:
    """One plain mailbox address: no display name, whitespace, controls or list separators."""
    if (
        value.count("@") != 1
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "<>,;:" for c in value)
        or not all(value.split("@"))
        or "." not in value.split("@")[1]
    ):
        raise ValueError("one plain mailbox address required")
    return value


def reject_header_control_chars(value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("header control characters forbidden")
    return value


class EmailDraftInput(DraftModel):
    action: Literal["draft_reply"] = "draft_reply"
    mailbox_connection_id: UUID
    thread_id: str = Field(min_length=1, max_length=256)
    recipient: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        return validate_mailbox_address(value)

    @field_validator("subject", "thread_id")
    @classmethod
    def reject_header_controls(cls, value: str) -> str:
        return reject_header_control_chars(value)


class ResearchInput(DraftModel):
    action: Literal["request_fetch"] = "request_fetch"
    urls: list[str] = Field(min_length=1, max_length=10)
    purpose: str = Field(min_length=1, max_length=1000)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, values: list[str]) -> list[str]:
        for value in values:
            parts = urlsplit(value)
            host = parts.hostname
            if (
                len(value) > 2048
                or parts.scheme != "https"
                or not host
                or parts.username is not None
                or parts.password is not None
                or parts.port not in {None, 443}
                or parts.fragment
                or any(c.isspace() or ord(c) < 32 for c in value)
            ):
                raise ValueError("public HTTPS URL without credentials or fragment required")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if (
                    "." not in host
                    or host.endswith((".localhost", ".local", ".internal"))
                    or host.endswith(".")
                    or any(
                        not label or not label.replace("-", "").isalnum()
                        for label in host.split(".")
                    )
                ):
                    raise ValueError("public DNS hostname required") from None
            else:
                raise ValueError("IP literals forbidden")
        if len(values) != len(set(values)):
            raise ValueError("duplicate URLs forbidden")
        return values


class InvoiceLine(DraftModel):
    description: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(gt=0, le=100000, max_digits=10, decimal_places=3, allow_inf_nan=False)
    unit_price: Decimal = Field(
        ge=0, le=10_000_000, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    tax_rate: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4, allow_inf_nan=False)

    def totals(self) -> tuple[Decimal, Decimal]:
        net = (self.quantity * self.unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax = (net * self.tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return net, tax


class InvoiceDraftInput(DraftModel):
    action: Literal["draft_create", "request_void", "draft_credit"]
    invoice_id: str | None = Field(default=None, min_length=1, max_length=128)
    currency: Literal["MYR"] = "MYR"
    lines: list[InvoiceLine] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    due_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action in {"request_void", "draft_credit"} and not self.invoice_id:
            raise ValueError("original invoice_id required")
        if self.action in {"draft_create", "draft_credit"} and not self.lines:
            raise ValueError("invoice lines required")
        if self.action == "request_void" and (self.lines or not self.reason):
            raise ValueError("void requires reason and no lines")
        if self.total > Decimal("9_999_999_999.99"):
            raise ValueError("invoice exceeds persisted amount bound")
        return self

    @property
    def total(self) -> Decimal:
        return sum(
            (net + tax for net, tax in (line.totals() for line in self.lines)), Decimal("0.00")
        )

    def payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["total"] = str(self.total)
        payload["calculation"] = "per_line_round_half_up_2dp"
        return payload
