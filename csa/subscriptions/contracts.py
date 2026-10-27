"""Pydantic contracts for the subscriptions console tab."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from csa.console.contracts import Contract

Status = Literal["active", "paused", "cancelled"]
Interval = Literal["month", "year"]


class PlanOut(Contract):
    code: str
    name: str
    price: Decimal
    currency: str
    billing_interval: Interval
    features: list[str]
    sort_order: int
    is_active: bool


class PlanIn(Contract):
    code: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = "MYR"
    billing_interval: Interval = "month"
    features: list[str] = Field(default_factory=list, max_length=20)
    sort_order: int = 0


class PlanPatch(Contract):
    # None means "leave unchanged"; the bounds mirror PlanIn so a patch cannot store
    # what creation would refuse.
    name: str | None = Field(default=None, min_length=1, max_length=120)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    billing_interval: Interval | None = None
    features: list[str] | None = Field(default=None, max_length=20)
    sort_order: int | None = None
    is_active: bool | None = None


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


class SubscriptionDetail(Contract):
    subscription: SubscriptionOut
    events: list[SubscriptionEventOut]


class SubscriptionCreateIn(Contract):
    contact_id: UUID
    plan_code: str
    started_at: datetime | None = None
    renews_at: datetime | None = None
