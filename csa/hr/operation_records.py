"""Allowlisted private response and persisted payload contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, TypeAdapter

from csa.hr.automation_models import CalendarSave, PayrollRules
from csa.hr.models import HrModel, Version
from csa.hr.workflows import Region, ReviewedPayrollAmounts


class AuthorityEvent(HrModel):
    subject_id: UUID
    request_key: UUID
    operation: str
    details: dict[str, JsonValue]
    occurred_at: AwareDatetime


class MemberRecord(HrModel):
    user_id: UUID
    display_name: str | None


class AdminEmployee(HrModel):
    employee_id: UUID
    display_name: str
    job_title: str | None
    department: str | None
    work_email: str | None
    version: Version
    active: bool
    manager_id: UUID | None
    account_id: UUID | None
    region: Region | None
    legal_entity_reference: str | None
    employment_category: str | None


class CandidateSummary(HrModel):
    id: UUID
    display_name: str = Field(repr=False)
    consent_id: UUID
    retain_until: AwareDatetime
    version: Version


class ReviewedNote(HrModel):
    by: UUID
    note: str = Field(max_length=1200, repr=False)
    at: AwareDatetime


class CandidateDetails(CandidateSummary):
    cv_text: str = Field(max_length=12000, repr=False)
    consent_evidence: str = Field(max_length=200, repr=False)
    notes: list[ReviewedNote] = Field(max_length=100)
    recorded_by: UUID


class InterviewRecord(HrModel):
    id: UUID
    candidate_id: UUID
    interviewer_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    timezone: str
    delivery: Literal["local_only"]
    status: Literal["scheduled", "cancelled"]


class CalendarRecord(HrModel):
    region: Region
    year: int
    payload: CalendarSave
    version: Version


class BalanceRecord(HrModel):
    employee_id: UUID
    year: int
    leave_type: str
    days: Decimal
    version: Version
    policy_reference: str
    remaining: Decimal


class ShiftRecord(HrModel):
    status: Literal["scheduled", "cancelled"]
    id: UUID
    employee_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class PayrollRuleRecord(HrModel):
    id: UUID
    payload: PayrollRules
    reviewed_by: UUID


class CalculatedLine(HrModel):
    employee_id: UUID
    amounts: ReviewedPayrollAmounts = Field(repr=False)
    net: Decimal = Field(repr=False)
    tax_review_reference: str


class CalculatedPayroll(HrModel):
    lines: list[CalculatedLine] = Field(min_length=1, max_length=100)
    rule_id: UUID
    currency: Literal["MYR"]
    net_total: Decimal = Field(repr=False)


class PayrollSnapshotRecord(HrModel):
    case_id: UUID
    payload: CalculatedPayroll = Field(repr=False)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_by: UUID
    reviewed_by: UUID | None
    status: Literal["prepared", "approved", "reversed"]
    version: Version
    reversal_reason: str | None


class NotificationRecord(HrModel):
    id: UUID
    kind: Literal["workflow_review", "training_review", "interview_upcoming"]
    resource_id: UUID
    created_at: AwareDatetime
    read_at: AwareDatetime | None


OperationRecord = (
    AuthorityEvent
    | MemberRecord
    | AdminEmployee
    | CandidateSummary
    | CandidateDetails
    | InterviewRecord
    | CalendarRecord
    | BalanceRecord
    | ShiftRecord
    | PayrollRuleRecord
    | PayrollSnapshotRecord
    | NotificationRecord
)


class Records(HrModel):
    items: list[OperationRecord] = Field(max_length=200)


RECORD_TYPES = {
    "authority_audit": AuthorityEvent,
    "members": MemberRecord,
    "employees": AdminEmployee,
    "candidates": CandidateSummary,
    "candidate": CandidateDetails,
    "interviews": InterviewRecord,
    "my_interviews": InterviewRecord,
    "calendars": CalendarRecord,
    "balances": BalanceRecord,
    "shifts": ShiftRecord,
    "payroll_rules": PayrollRuleRecord,
    "payroll_snapshot": PayrollSnapshotRecord,
    "notifications": NotificationRecord,
}


def project_records(area: str, rows: list[dict]) -> Records:
    model = RECORD_TYPES[area]
    return Records(
        items=[
            TypeAdapter(OperationRecord).validate_python(
                model.model_validate(
                    {key: value for key, value in row.items() if key in model.model_fields}
                )
            )
            for row in rows
        ]
    )
