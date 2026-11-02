"""Validated preparation contracts, separate from external execution commands."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


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


class BookingInput(DraftModel):
    action: Literal["list", "status", "request_book", "request_reschedule", "request_cancel"]
    booking_id: UUID | None = None
    service: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    timezone: str = "Asia/Kuala_Lumpur"
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        if self.action in {"status", "request_reschedule", "request_cancel"}:
            if self.booking_id is None:
                raise ValueError("booking_id required")
        if self.action in {"request_book", "request_reschedule"}:
            if self.starts_at is None or self.ends_at is None:
                raise ValueError("explicit offset-aware start and end required")
            duration = self.ends_at.astimezone(UTC) - self.starts_at.astimezone(UTC)
            if not timedelta(0) < duration <= timedelta(hours=24):
                raise ValueError("booking duration must be positive and at most 24 hours")
            for value in (self.starts_at, self.ends_at):
                if value.utcoffset() != value.astimezone(UTC).astimezone(zone).utcoffset():
                    raise ValueError("timestamp offset does not match timezone")
        if self.action == "request_book" and not self.service:
            raise ValueError("service required")
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


def future_booking(inp: BookingInput, now: datetime) -> bool:
    return inp.starts_at is None or inp.starts_at.astimezone(UTC) > now.astimezone(UTC)
