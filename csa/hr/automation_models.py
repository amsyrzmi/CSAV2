"""Private HR operations. These contracts are never customer/model tool inputs."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator

from csa.hr.models import DirectoryValues, HrModel, ResolvedGrant, Version

# From the leaf module, not people_contracts: people_contracts imports access_switches, which
# imports this module.
from csa.hr.people_types import EmploymentProfile, EmploymentType
from csa.hr.workflows import AttendanceInput, Money, Region, ReviewedPayrollAmounts, WorkflowGrant

PrivateArea = Literal["recruitment", "attendance", "payroll"]


class EmployeeSave(HrModel):
    operation: Literal["employee.save"]
    employee_id: UUID
    expected_version: Version | None = None
    values: DirectoryValues
    active: StrictBool = True
    account_id: UUID | None = None
    region: Region | None = None
    legal_entity_reference: str | None = Field(default=None, min_length=1, max_length=100)
    employment_category: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def named(self) -> Self:
        if self.values.display_name is None:
            raise ValueError("employee_name_required")
        if (
            len(
                {
                    self.region is None,
                    self.legal_entity_reference is None,
                    self.employment_category is None,
                }
            )
            > 1
        ):
            raise ValueError("employment_profile_must_be_complete")
        return self


class PersonValues(HrModel):
    """A person's editable Overview fields, for person.add and person.update.

    The department is an existing one (`department_id`) or a name typed in the form
    (`new_department`, matched like department names are, or created), never both.
    """

    display_name: str = Field(min_length=1, max_length=120)
    job_title: str | None = Field(default=None, min_length=1, max_length=120)
    start_date: date | None = None
    manager_id: UUID | None = None
    department_id: UUID | None = None
    new_department: str | None = Field(default=None, min_length=1, max_length=120)
    work_email: str | None = Field(default=None, min_length=3, max_length=254)
    work_phone: str | None = Field(default=None, pattern=r"^[0-9+() -]{3,40}$")
    employment_type: EmploymentType | None = None

    @field_validator("work_email")
    @classmethod
    def plain_work_email(cls, value: str | None) -> str | None:
        return DirectoryValues.plain_work_email(value)

    @model_validator(mode="after")
    def one_department(self) -> Self:
        if self.department_id is not None and self.new_department is not None:
            raise ValueError("one_department_choice")
        return self


class PersonAdd(HrModel):
    """A new active person. Owner or Manage people; never changes access or account links."""

    operation: Literal["person.add"]
    employee_id: UUID
    values: PersonValues
    employment_profile: EmploymentProfile | None = None

    @model_validator(mode="after")
    def title_and_start(self) -> Self:
        if self.values.job_title is None or self.values.start_date is None:
            raise ValueError("title_and_start_required")
        return self


class PersonUpdate(HrModel):
    """Every editable field at once. Never changes whether the person is active (1b)."""

    operation: Literal["person.update"]
    employee_id: UUID
    expected_version: Version
    values: PersonValues
    employment_profile: EmploymentProfile | None = None

    @model_validator(mode="after")
    def not_own_manager(self) -> Self:
        if self.values.manager_id == self.employee_id:
            raise ValueError("manager_self")
        return self


class DepartmentSave(HrModel):
    """Create (no expected_version), rename, archive or restore a department."""

    operation: Literal["department.save"]
    department_id: UUID
    expected_version: Version | None = None
    name: str = Field(min_length=1, max_length=120)
    archived: StrictBool = False


class AccountLink(HrModel):
    """Link a person to a member's console account, or unlink (None). HR owner only."""

    operation: Literal["account.link"]
    employee_id: UUID
    account_id: UUID | None


class AccessSave(HrModel):
    operation: Literal["access.save"]
    user_id: UUID
    grants: tuple[ResolvedGrant, ...] = Field(max_length=32)
    workflows: tuple[WorkflowGrant, ...] = Field(max_length=48)


class PrivateAccessSave(HrModel):
    operation: Literal["private_access.save"]
    user_id: UUID
    area: PrivateArea
    enabled: StrictBool


class CalendarSave(HrModel):
    operation: Literal["calendar.save"]
    region: Region
    year: int = Field(ge=2020, le=2200)
    expected_version: Version | None = None
    working_weekdays: frozenset[int] = Field(min_length=1, max_length=7)
    holidays: frozenset[date] = Field(max_length=100)

    @model_validator(mode="after")
    def dates(self) -> Self:
        if any(not 0 <= d <= 6 for d in self.working_weekdays):
            raise ValueError("weekday_must_be_zero_to_six")
        if any(d.year != self.year for d in self.holidays):
            raise ValueError("holiday_year_mismatch")
        return self


class LeaveBalanceSave(HrModel):
    operation: Literal["leave_balance.save"]
    employee_id: UUID
    year: int = Field(ge=2020, le=2200)
    leave_type: Literal["annual", "sick", "unpaid", "other"]
    days: Decimal = Field(ge=0, le=366, decimal_places=2, allow_inf_nan=False)
    expected_version: Version | None = None
    policy_reference: str = Field(min_length=1, max_length=200)


class CandidateIntake(HrModel):
    operation: Literal["candidate.intake"]
    display_name: str = Field(min_length=1, max_length=120, repr=False)
    cv_text: str = Field(min_length=1, max_length=12000, repr=False)
    consent_evidence: str = Field(min_length=1, max_length=200, repr=False)
    retain_until: AwareDatetime

    @field_validator("cv_text", mode="before")
    @classmethod
    def normalize_lines(cls, value: object) -> object:
        if isinstance(value, str):
            return (
                value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
            )
        return value


class CandidateNote(HrModel):
    operation: Literal["candidate.note"]
    candidate_id: UUID
    expected_version: Version
    note: str = Field(min_length=1, max_length=1200, repr=False)


class CandidateErase(HrModel):
    operation: Literal["candidate.erase"]
    candidate_id: UUID
    expected_version: Version


class InterviewSchedule(HrModel):
    operation: Literal["interview.schedule"]
    candidate_id: UUID
    interviewer_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    timezone: Literal["Asia/Kuala_Lumpur", "Asia/Kuching"]

    @model_validator(mode="after")
    def interval(self) -> Self:
        if not timedelta(minutes=10) <= self.ends_at - self.starts_at <= timedelta(hours=4):
            raise ValueError("interview_interval_invalid")
        return self


class AttendanceImport(HrModel):
    operation: Literal["attendance.import"]
    employee_id: UUID
    records: tuple[AttendanceInput, ...] = Field(min_length=1, max_length=20)


class ShiftSchedule(HrModel):
    operation: Literal["shift.schedule"]
    employee_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def interval(self) -> Self:
        if not timedelta(0) < self.ends_at - self.starts_at <= timedelta(hours=24):
            raise ValueError("shift_interval_invalid")
        return self


class ContributionBand(HrModel):
    """An explicitly reviewed wage band, rather than an inferred flat percentage."""

    above: Money
    through: Money
    employee: Money
    employer: Money

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.through <= self.above:
            raise ValueError("contribution_band_invalid")
        return self


class PayrollRules(HrModel):
    operation: Literal["payroll.rules"]
    rule_id: UUID
    region: Region
    legal_entity_reference: str = Field(min_length=1, max_length=100)
    employment_category: str = Field(min_length=1, max_length=100)
    cycle: Literal["weekly", "fortnightly", "monthly"]
    effective_from: date
    effective_until: date
    source_reference: str = Field(min_length=1, max_length=200)
    reviewer_qualification_reference: str = Field(min_length=1, max_length=200)
    epf: tuple[ContributionBand, ...] = Field(min_length=1, max_length=500)
    socso: tuple[ContributionBand, ...] = Field(min_length=1, max_length=500)
    eis: tuple[ContributionBand, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def periods(self) -> Self:
        if not timedelta(0) <= self.effective_until - self.effective_from <= timedelta(days=366):
            raise ValueError("payroll_rule_period_invalid")
        for bands in (self.epf, self.socso, self.eis):
            if bands[0].above != 0 or any(
                a.through != b.above for a, b in zip(bands, bands[1:], strict=False)
            ):
                raise ValueError("contribution_bands_must_be_contiguous")
        return self

    def contribution(self, bands: tuple[ContributionBand, ...], wage: Decimal) -> ContributionBand:
        if wage == 0:
            return ContributionBand(
                above=Decimal(0), through=Decimal(1), employee=Decimal(0), employer=Decimal(0)
            )
        found = next((band for band in bands if band.above < wage <= band.through), None)
        if found is None:
            raise ValueError("wage_outside_reviewed_rule_bands")
        return found


class PayrollLine(HrModel):
    employee_id: UUID
    gross: Money
    epf_wage: Money
    socso_wage: Money
    eis_wage: Money
    reviewed_pcb: Money
    reviewed_other_deductions: Money
    tax_review_reference: str = Field(min_length=1, max_length=200)

    def calculate(self, rules: PayrollRules) -> ReviewedPayrollAmounts:
        epf = rules.contribution(rules.epf, self.epf_wage)
        socso = rules.contribution(rules.socso, self.socso_wage)
        eis = rules.contribution(rules.eis, self.eis_wage)
        return ReviewedPayrollAmounts(
            gross=self.gross,
            employee_epf=epf.employee,
            employee_socso=socso.employee,
            employee_eis=eis.employee,
            pcb=self.reviewed_pcb,
            reviewed_other_deductions=self.reviewed_other_deductions,
            employer_epf=epf.employer,
            employer_socso=socso.employer,
            employer_eis=eis.employer,
        )


class PayrollSnapshot(HrModel):
    operation: Literal["payroll.snapshot"]
    case_id: UUID
    lines: tuple[PayrollLine, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def employees_unique(self) -> Self:
        if len({line.employee_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate_payroll_employee")
        return self


class PayrollReverse(HrModel):
    operation: Literal["payroll.reverse"]
    case_id: UUID
    expected_version: Version
    reason: str = Field(min_length=1, max_length=500)


class AutomationRun(HrModel):
    operation: Literal["automation.run"]
    remind_after_hours: int = Field(default=24, ge=1, le=720)


class NotificationRead(HrModel):
    operation: Literal["notification.read"]
    notification_id: UUID


class AutomationConfigure(HrModel):
    operation: Literal["automation.configure"]
    enabled: StrictBool
    remind_after_hours: int = Field(default=24, ge=1, le=720)


class ScheduleCancel(HrModel):
    operation: Literal["interview.cancel", "shift.cancel"]
    schedule_id: UUID
    reason: str = Field(min_length=1, max_length=500)


HrOperation = Annotated[
    EmployeeSave
    | AccessSave
    | PrivateAccessSave
    | CalendarSave
    | LeaveBalanceSave
    | CandidateIntake
    | CandidateNote
    | CandidateErase
    | InterviewSchedule
    | AttendanceImport
    | PayrollRules
    | PayrollSnapshot
    | PayrollReverse
    | AutomationRun
    | NotificationRead
    | ShiftSchedule
    | ScheduleCancel
    | AutomationConfigure
    | PersonAdd
    | PersonUpdate
    | DepartmentSave
    | AccountLink,
    Field(discriminator="operation"),
]


class OperationReceipt(HrModel):
    operation: str
    resource_id: UUID
    version: Version = 0
    status: str
    duplicate: bool = False


def leave_days(starts: date, ends: date, calendar: CalendarSave) -> Decimal:
    if starts.year != ends.year or starts.year != calendar.year:
        raise ValueError("leave_must_use_one_configured_year")
    return Decimal(
        sum(
            1
            for offset in range((ends - starts).days + 1)
            if (day := starts + timedelta(days=offset)).weekday() in calendar.working_weekdays
            and day not in calendar.holidays
        )
    )
