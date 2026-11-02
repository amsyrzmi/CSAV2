"""Bounded HR contracts, independent of console/customer/runtime contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


class HrModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, hide_input_in_errors=True
    )

    @field_validator("*", mode="after")
    @classmethod
    def reject_controls(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("control characters forbidden")
        return value


class EmployeeRef(HrModel):
    tenant_id: UUID
    employee_id: UUID


class EmployeeIdentity(HrModel):
    """Trusted directory identity; no email-based identity inference or contact ID."""

    employee: EmployeeRef
    console_account_id: UUID | None = None


class DirectoryField(StrEnum):
    DISPLAY_NAME = "display_name"
    JOB_TITLE = "job_title"
    DEPARTMENT = "department"
    WORK_EMAIL = "work_email"
    ASSIGNED_MANAGER = "assigned_manager"


class DirectoryValues(HrModel):
    """Allowlist only. Salary/bank/medical/CV values have no representation here."""

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    job_title: str | None = Field(default=None, min_length=1, max_length=120)
    department: str | None = Field(default=None, min_length=1, max_length=120)
    work_email: str | None = Field(default=None, min_length=3, max_length=254)
    assigned_manager: EmployeeRef | None = None

    @field_validator("work_email")
    @classmethod
    def plain_work_email(cls, value: str | None) -> str | None:
        if value is not None and (
            value.count("@") != 1
            or not all(value.split("@"))
            or any(c.isspace() or c in "<>,;:" for c in value)
        ):
            raise ValueError("one plain work mailbox required")
        return value


class DirectoryRecord(HrModel):
    """Trusted adapter facts, never a model-generated or HTTP identity claim."""

    identity: EmployeeIdentity
    values: DirectoryValues

    @model_validator(mode="after")
    def same_tenant_manager(self) -> Self:
        manager = self.values.assigned_manager
        if manager is not None and manager.tenant_id != self.identity.employee.tenant_id:
            raise ValueError("cross_tenant_reference")
        return self


class DirectoryProjection(HrModel):
    employee: EmployeeRef
    values: DirectoryValues


class DirectoryLookupInput(HrModel):
    """Untrusted selector only; tenant/actor/permissions must be resolved server-side."""

    employee_id: UUID


class HrAction(StrEnum):
    DIRECTORY_READ = "directory.read"
    TRAINING_READ = "training.read"
    TRAINING_DRAFT = "training.draft"
    TRAINING_SUBMIT = "training.submit"
    TRAINING_WITHDRAW = "training.withdraw"
    TRAINING_DECIDE = "training.decide"
    PEOPLE_MANAGE = "people.manage"  # the Manage people switch; never part of HR guidance


class GrantScope(StrEnum):
    SELF = "self"
    TEAM = "assigned_manager"
    HR = "hr_operator"
    CLIENT_OWNER = "client_owner"


class ResolvedGrant(HrModel):
    """Trusted permission resolution, NOT a login or a caller-selectable HR role."""

    action: HrAction
    scope: GrantScope
    fields: frozenset[DirectoryField] = Field(default_factory=frozenset, max_length=5)

    @model_validator(mode="after")
    def supported_combination(self) -> Self:
        reads = {HrAction.DIRECTORY_READ, HrAction.TRAINING_READ}
        supported = {
            GrantScope.SELF: reads
            | {HrAction.TRAINING_DRAFT, HrAction.TRAINING_SUBMIT, HrAction.TRAINING_WITHDRAW},
            GrantScope.TEAM: reads | {HrAction.TRAINING_DECIDE},
            GrantScope.HR: reads | {HrAction.PEOPLE_MANAGE},
            GrantScope.CLIENT_OWNER: {HrAction.TRAINING_DECIDE},
        }
        if self.action not in supported[self.scope]:
            raise ValueError("unsupported_grant")
        if self.fields and self.action != HrAction.DIRECTORY_READ:
            raise ValueError("fields_only_for_directory")
        return self


class TrustedAccess(HrModel):
    """Adapter requirement: verified subject, tenant, ownership and current grants.

    Construct exclusively from verified server state. Validation is not authentication.
    No console role implies any grant or client ownership. Employee linkage is optional.
    """

    tenant_id: UUID
    subject_id: UUID
    employee: EmployeeRef | None = None
    client_owner: StrictBool = False
    grants: tuple[ResolvedGrant, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def same_tenant_employee(self) -> Self:
        if self.employee is not None and self.employee.tenant_id != self.tenant_id:
            raise ValueError("cross_tenant_reference")
        return self


class TrainingDraftInput(HrModel):
    """Untrusted content proposal, not an employee identity or persisted request."""

    title: str = Field(min_length=1, max_length=160)
    learning_objective: str = Field(min_length=1, max_length=1000)


class TrainingStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class TrainingAction(StrEnum):
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    WITHDRAW = "withdraw"


Version = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]


class TrainingCommandInput(HrModel):
    """Untrusted intent; request/employee/actor/grants come from verified state."""

    action: TrainingAction
    expected_version: Version
    decision_note: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def decision_content(self) -> Self:
        if self.action == TrainingAction.REJECT and self.decision_note is None:
            raise ValueError("rejection_reason_required")
        if self.decision_note is not None and self.action not in {
            TrainingAction.APPROVE,
            TrainingAction.REJECT,
        }:
            raise ValueError("decision_note_only_for_decision")
        return self


class TrainingDecision(HrModel):
    subject_id: UUID
    action: Literal[TrainingAction.APPROVE, TrainingAction.REJECT]
    note: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def rejection_reason(self) -> Self:
        if self.action == TrainingAction.REJECT and self.note is None:
            raise ValueError("rejection_reason_required")
        return self


class TrainingSnapshot(HrModel):
    """Hypothetical/adapter-loaded state. Instantiation proves no database outcome."""

    request_id: UUID
    employee: EmployeeRef
    content: TrainingDraftInput
    status: TrainingStatus
    version: Version
    updated_at: AwareDatetime
    decision: TrainingDecision | None = None

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        decisions = {
            TrainingStatus.APPROVED: TrainingAction.APPROVE,
            TrainingStatus.REJECTED: TrainingAction.REJECT,
        }
        expected = decisions.get(self.status)
        if (expected is None and self.decision is not None) or (
            expected is not None and (self.decision is None or self.decision.action != expected)
        ):
            raise ValueError("invalid_decision_state")
        return self


class TrainingDraftProposal(HrModel):
    kind: Literal["draft_proposal"] = "draft_proposal"
    employee: EmployeeRef
    content: TrainingDraftInput


class TrainingProjection(HrModel):
    """Authorized request view; omits internal decision identity and private note."""

    request_id: UUID
    employee: EmployeeRef
    content: TrainingDraftInput
    status: TrainingStatus
    version: Version
    updated_at: AwareDatetime


class TrainingTransitionProposal(HrModel):
    """Candidate next state only; future storage must CAS and audit atomically."""

    kind: Literal["transition_proposal"] = "transition_proposal"
    before: TrainingSnapshot
    after: TrainingSnapshot
