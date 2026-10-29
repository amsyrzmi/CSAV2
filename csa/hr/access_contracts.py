"""Contracts for the HR access page ("Who can do what").

The server works out every switch, warning and version; the page shows what these models carry and
sends back each person's full desired switch set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator

from csa.hr.access_switches import PresetId, SwitchId, WarningKind
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
    """The person's full desired switch set, and the `on` set the page loaded it from."""

    user_id: UUID
    on: list[SwitchId] = Field(max_length=8)
    base: list[SwitchId] = Field(max_length=8)

    @field_validator("on", "base")
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
    """One change: a save on this page, or access set on an invitation when it was accepted.

    For an invitation the actor is whoever chose the preset, and `turned_on` is the preset's
    switches. A platform admin's name is "FourStacks admin"; a name is None once that person is
    no longer an enabled member.
    """

    occurred_at: datetime
    source: Literal["page", "invitation"]
    preset: PresetId | None  # set for invitation events only
    actor_user_id: UUID
    actor_name: str | None
    target_user_id: UUID
    target_name: str | None
    turned_on: list[SwitchId]
    turned_off: list[SwitchId]
    area: str | None
    area_enabled: bool | None


class AccessEventPage(HrModel):
    items: list[AccessEvent] = Field(max_length=PAGE_SIZE)
    next_cursor: str | None = None


class PendingInvitationAccess(HrModel):
    """An accepted invitation whose HR access was neither applied nor dismissed."""

    token_id: UUID
    user_id: UUID
    display_name: str
    preset: PresetId
    chosen_by: UUID
    chooser_name: str | None  # "FourStacks admin", or None once no longer an enabled member
    accepted_at: datetime


class PendingInvitationPage(HrModel):
    items: list[PendingInvitationAccess] = Field(max_length=PAGE_SIZE)
