"""Contracts for the HR access page ("Who can do what").

The server works out every switch, warning and version; the page shows what these models carry and
sends back each person's full desired switch set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator

from csa.hr.access_switches import SwitchId, WarningKind
from csa.hr.models import HrModel

PAGE_SIZE = 50


class AccessPerson(HrModel):
    user_id: UUID
    display_name: str | None
    employee_id: UUID | None
    department: str | None
    manager_id: UUID | None
    team_size: int = Field(ge=0)
    is_owner: bool
    on: list[SwitchId]
    included: dict[SwitchId, SwitchId]
    custom: list[str]  # the rows no switch describes, in plain words


class AccessWarningOut(HrModel):
    """Names are resolved by the server; the page writes the sentence."""

    kind: WarningKind
    user_id: UUID | None
    employee_id: UUID | None
    user_name: str | None
    employee_name: str | None
    manager_name: str | None


class AccessPeoplePage(HrModel):
    items: list[AccessPerson] = Field(max_length=PAGE_SIZE)
    next_cursor: str | None = None
    version: str
    counts: dict[SwitchId, int]  # whole client, whatever the filters
    warnings: list[AccessWarningOut]


class AccessChange(HrModel):
    """The person's full desired switch set."""

    user_id: UUID
    on: list[SwitchId] = Field(max_length=8)

    @field_validator("on")
    @classmethod
    def unique_switches(cls, value: list[SwitchId]) -> list[SwitchId]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate_switch")
        return value


class AccessChanges(HrModel):
    batch_id: UUID
    version: str = Field(min_length=1, max_length=64)
    dry_run: StrictBool = False
    changes: list[AccessChange] = Field(min_length=1, max_length=PAGE_SIZE)

    @field_validator("changes")
    @classmethod
    def unique_people(cls, value: list[AccessChange]) -> list[AccessChange]:
        if len({change.user_id for change in value}) != len(value):
            raise ValueError("duplicate_person")
        return value


class AccessPersonResult(HrModel):
    user_id: UUID
    outcome: Literal["saved", "unchanged", "refused"]
    reason: str | None = None


class AccessChangeResult(HrModel):
    results: list[AccessPersonResult] = Field(max_length=PAGE_SIZE)
    version: str
    warnings: list[AccessWarningOut]


class AccessEvent(HrModel):
    occurred_at: datetime
    actor_user_id: UUID
    actor_name: str | None  # None once the member is no longer enabled
    target_user_id: UUID
    target_name: str | None
    turned_on: list[SwitchId]
    turned_off: list[SwitchId]
    area: str | None
    area_enabled: bool | None


class AccessEventPage(HrModel):
    items: list[AccessEvent] = Field(max_length=PAGE_SIZE)
    next_cursor: str | None = None
