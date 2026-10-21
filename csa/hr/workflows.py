"""Typed recruitment, attendance, leave and payroll preparation workflows.

No protected-attribute inference, automated hiring decision, wage deduction,
payment or filing. Monetary outcomes and supervisor decisions require humans.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from csa.hr.models import EmployeeRef, HrModel, Version

Region = Literal["peninsular_labuan", "sabah", "sarawak"]
Domain = Literal["recruitment", "attendance", "leave", "payroll"]


class RecruitmentInput(HrModel):
    domain: Literal["recruitment"]
    role_title: str = Field(min_length=1, max_length=160)
    region: Region
    competencies: tuple[Annotated[str, Field(min_length=1, max_length=120)], ...] = Field(
        min_length=1, max_length=8
    )
    candidate_reference: UUID | None = None
    consent_reference: UUID | None = None

    @model_validator(mode="after")
    def consent_required(self) -> Self:
        if self.candidate_reference is not None and self.consent_reference is None:
            raise ValueError("candidate_consent_required")
        return self

    def guide(self) -> tuple[str, ...]:
        return tuple(
            f"Describe a work example demonstrating {skill}. What did you do and learn?"
            for skill in self.competencies
        )


class AttendanceInput(HrModel):
    domain: Literal["attendance"]
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    timezone: Literal["Asia/Kuala_Lumpur", "Asia/Kuching"]
    region: Region
    correction_of: UUID | None = None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def interval(self) -> Self:
        if not timedelta(0) < self.ends_at - self.starts_at <= timedelta(hours=24):
            raise ValueError("attendance_interval_invalid")
        zone = ZoneInfo(self.timezone)
        for stamp in (self.starts_at, self.ends_at):
            if stamp.utcoffset() != stamp.astimezone(zone).utcoffset():
                raise ValueError("attendance_offset_mismatch")
        if self.correction_of is not None and not self.reason:
            raise ValueError("correction_reason_required")
        return self


class LeaveInput(HrModel):
    domain: Literal["leave"]
    starts_on: date
    ends_on: date
    region: Region
    leave_type: Literal["annual", "sick", "unpaid", "other"]
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def period(self) -> Self:
        if not timedelta(0) <= self.ends_on - self.starts_on <= timedelta(days=366):
            raise ValueError("leave_period_invalid")
        return self


class PayrollInput(HrModel):
    """Request to prepare a human-reviewed run, without wages in agent/model input."""

    domain: Literal["payroll"]
    legal_entity_reference: str = Field(min_length=1, max_length=100)
    region: Region
    starts_on: date
    ends_on: date
    cycle: Literal["weekly", "fortnightly", "monthly"]
    currency: Literal["MYR"] = "MYR"
    rule_set_reference: UUID

    @model_validator(mode="after")
    def period(self) -> Self:
        bound = {"weekly": 7, "fortnightly": 14, "monthly": 31}[self.cycle]
        if not 1 <= (self.ends_on - self.starts_on).days + 1 <= bound:
            raise ValueError("payroll_period_invalid")
        return self


WorkflowInput = Annotated[
    RecruitmentInput | AttendanceInput | LeaveInput | PayrollInput, Field(discriminator="domain")
]
WORKFLOW_INPUT = TypeAdapter(WorkflowInput)


class WorkflowRecord(HrModel):
    execution: Literal["local_workflow_only"] = "local_workflow_only"
    id: UUID
    domain: Domain
    employee: EmployeeRef | None
    content: WorkflowInput
    created_by: UUID
    status: Literal["pending", "approved", "rejected", "withdrawn"]
    version: Version
    updated_at: AwareDatetime
    interview_guide: tuple[str, ...] = ()
    decision_reason: str | None = Field(default=None, max_length=500)


class WorkflowCommand(HrModel):
    action: Literal["approve", "reject", "withdraw"]
    expected_version: Version
    payroll_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def rejection(self) -> Self:
        if self.action == "reject" and self.reason is None:
            raise ValueError("rejection_reason_required")
        return self


class WorkflowGrant(HrModel):
    domain: Domain
    action: Literal["read", "prepare", "decide"]
    scope: Literal[
        "self",
        "assigned_manager",
        "hr_operator",
        "client_owner",
        "payroll_operator",
        "payroll_reviewer",
    ]

    @model_validator(mode="after")
    def permitted(self) -> Self:
        if self.domain == "payroll":
            expected = "payroll_reviewer" if self.action == "decide" else "payroll_operator"
            if self.scope != expected:
                raise ValueError("payroll_grant_must_be_explicit")
        elif self.scope in {"payroll_operator", "payroll_reviewer"}:
            raise ValueError("unsupported_workflow_grant")
        elif self.domain == "recruitment" and (
            self.scope not in {"hr_operator", "client_owner"}
            or (self.action == "decide" and self.scope != "client_owner")
        ):
            raise ValueError("unsupported_recruitment_grant")
        elif self.action == "decide" and self.scope in {"self", "hr_operator"}:
            raise ValueError("supervisor_decision_required")
        return self


Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]


class ReviewedPayrollAmounts(HrModel):
    """Private, manually reviewed input. Never an agent argument or generic fact.

    Statutory amounts must come from a reviewed effective-dated rule set; this
    arithmetic does not implement or certify EPF/SOCSO/EIS/PCB statutory rules.
    """

    gross: Money
    employee_epf: Money
    employee_socso: Money
    employee_eis: Money
    pcb: Money
    reviewed_other_deductions: Money
    employer_epf: Money
    employer_socso: Money
    employer_eis: Money

    @model_validator(mode="after")
    def deductions(self) -> Self:
        if self.net < 0:
            raise ValueError("deductions_exceed_gross")
        return self

    @property
    def net(self) -> Decimal:
        return self.gross - sum(
            (
                self.employee_epf,
                self.employee_socso,
                self.employee_eis,
                self.pcb,
                self.reviewed_other_deductions,
            ),
            Decimal("0.00"),
        )
