"""Contracts for the People directory, profiles, departments, org chart and HR home.

The server works out every status, section and permission flag. The HR-only fields
(`employment_profile`, `version`, `account_linked`) stay `None` for every other viewer.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from csa.hr.access_switches import SwitchId
from csa.hr.models import HrModel, Version
from csa.hr.people_types import EmploymentProfile, EmploymentType

__all__ = [
    "DepartmentList",
    "DepartmentOut",
    "EmploymentProfile",
    "EmploymentType",
    "FieldChange",
    "HistoryField",
    "HrHome",
    "LinkedAccount",
    "OrgChart",
    "OrgNode",
    "PeoplePage",
    "PersonAccessOut",
    "PersonChange",
    "PersonHistory",
    "PersonOut",
    "PersonProfile",
    "PersonStatus",
    "ProfileSection",
]

PersonStatus = Literal["active", "starting", "left"]  # 1b adds "leaving"
ProfileSection = Literal["overview", "access", "history"]  # 1b adds "personal", "documents"
HistoryField = Literal[
    "display_name",
    "job_title",
    "department",
    "manager",
    "work_email",
    "work_phone",
    "start_date",
    "employment_type",
    "active",
    "region",
    "legal_entity_reference",
    "employment_category",
]


class PersonOut(HrModel):
    employee_id: UUID
    display_name: str
    job_title: str | None = None
    department_id: UUID | None = None
    department: str | None = None
    manager_id: UUID | None = None
    manager_name: str | None = None
    work_email: str | None = None
    work_phone: str | None = None
    start_date: date | None = None
    employment_type: EmploymentType | None = None
    status: PersonStatus
    # Set only for the `hr` viewer.
    employment_profile: EmploymentProfile | None = None
    version: Version | None = None
    account_linked: bool | None = None


class PeoplePage(HrModel):
    items: list[PersonOut] = Field(max_length=50)
    next_cursor: str | None
    total: int = Field(ge=0)


class PersonProfile(HrModel):
    person: PersonOut
    sections: list[ProfileSection]
    can_edit: bool


class FieldChange(HrModel):
    field: HistoryField
    old: str | None
    new: str | None


class PersonChange(HrModel):
    changed_at: AwareDatetime
    actor_name: str | None
    fields: list[FieldChange]


class PersonHistory(HrModel):
    items: list[PersonChange] = Field(max_length=200)
    truncated: bool


class LinkedAccount(HrModel):
    user_id: UUID
    display_name: str | None


class PersonAccessOut(HrModel):
    account: LinkedAccount | None
    on: list[SwitchId]
    custom: list[str]
    is_owner: bool


class OrgNode(HrModel):
    employee_id: UUID
    display_name: str
    job_title: str | None
    department: str | None
    manager_id: UUID | None


class OrgChart(HrModel):
    nodes: list[OrgNode] = Field(max_length=5000)
    looped: list[UUID]


class DepartmentOut(HrModel):
    department_id: UUID
    name: str
    archived: bool
    version: Version
    people: int = Field(ge=0)


class DepartmentList(HrModel):
    items: list[DepartmentOut] = Field(max_length=500)


class HrHome(HrModel):
    training_waiting: int = Field(ge=0)
    requests_waiting: int = Field(ge=0)
