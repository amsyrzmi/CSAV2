"""HR-1 persisted/verified records, never browser or model authority contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr

from csa.hr.access_switches import SwitchId
from csa.hr.models import (
    DirectoryProjection,
    DirectoryRecord,
    DirectoryValues,
    EmployeeIdentity,
    EmployeeRef,
    HrAction,
    HrModel,
    ResolvedGrant,
    TrainingAction,
    TrainingCommandInput,
    TrainingDecision,
    TrainingDraftInput,
    TrainingProjection,
    TrainingSnapshot,
    TrainingStatus,
    Version,
)


class HrSession(HrModel):
    """Trusted HTTP/session adapter input; the service revalidates inside its transaction.

    These proofs are never queued, logged, returned or accepted as request DTOs.
    Constructing this model alone does not authenticate a caller.
    """

    session_id: UUID
    subject_id: UUID
    tenant_id: UUID
    token_hash: SecretStr = Field(exclude=True, repr=False)
    csrf_hash: SecretStr | None = Field(default=None, exclude=True, repr=False)
    assistant: bool = Field(default=False, exclude=True, repr=False)
    capability: (
        Literal[
            "hr_directory",
            "hr_training",
            "hr_recruitment",
            "hr_attendance",
            "hr_payroll",
            "hr_assistant",
        ]
        | None
    ) = None


class AuthorityRow(HrModel):
    employee_id: UUID | None
    client_owner: bool
    grants: tuple[ResolvedGrant, ...] = Field(max_length=32)


class EmployeeRow(HrModel):
    employee_id: UUID
    display_name: str
    job_title: str | None
    department: str | None
    work_email: str | None
    manager_id: UUID | None
    manager_active: bool | None

    def directory(self, tenant_id: UUID) -> DirectoryRecord:
        manager = (
            EmployeeRef(tenant_id=tenant_id, employee_id=self.manager_id)
            if self.manager_id is not None
            else None
        )
        return DirectoryRecord(
            identity=EmployeeIdentity(
                employee=EmployeeRef(tenant_id=tenant_id, employee_id=self.employee_id)
            ),
            values=DirectoryValues(
                display_name=self.display_name,
                job_title=self.job_title,
                department=self.department,
                work_email=self.work_email,
                assigned_manager=manager,
            ),
        )


class RequestRow(HrModel):
    request_id: UUID
    employee_id: UUID
    title: str
    learning_objective: str
    status: TrainingStatus
    version: Version
    updated_at: AwareDatetime
    decision_by: UUID | None
    decision_note: str | None

    def snapshot(self, tenant_id: UUID) -> TrainingSnapshot:
        decision = None
        if self.decision_by is not None:
            decision = TrainingDecision(
                subject_id=self.decision_by,
                action=(
                    TrainingAction.APPROVE
                    if self.status == TrainingStatus.APPROVED
                    else TrainingAction.REJECT
                ),
                note=self.decision_note,
            )
        return TrainingSnapshot(
            request_id=self.request_id,
            employee=EmployeeRef(tenant_id=tenant_id, employee_id=self.employee_id),
            content=TrainingDraftInput(
                title=self.title, learning_objective=self.learning_objective
            ),
            status=self.status,
            version=self.version,
            updated_at=self.updated_at,
            decision=decision,
        )


class ReceiptRow(HrModel):
    request_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["save_draft", "submit", "withdraw", "approve", "reject"]
    status: TrainingStatus
    version: Version
    updated_at: AwareDatetime


class TrainingReceipt(HrModel):
    """Returned only after a successful domain transaction commit (or exact replay)."""

    kind: Literal["persisted_training"] = "persisted_training"
    request: TrainingProjection
    duplicate: bool = False


class ConfirmationRow(HrModel):
    confirmation_id: UUID
    session_id: UUID
    request_id: UUID
    command: TrainingCommandInput
    fingerprint: str
    created_at: AwareDatetime
    expires_at: AwareDatetime
    consumed_at: AwareDatetime | None


class ConfirmationProjection(HrModel):
    kind: Literal["confirmation_required"] = "confirmation_required"
    confirmation_id: UUID
    request_id: UUID
    action: TrainingAction
    expected_version: Version
    expires_at: AwareDatetime
    command: TrainingCommandInput
    consumed: bool = False


class AgentSwitch(HrModel):
    name: Literal[
        "hr_directory",
        "hr_training",
        "hr_recruitment",
        "hr_attendance",
        "hr_payroll",
        "hr_assistant",
    ]
    enabled: bool


class DirectoryPage(HrModel):
    items: list[DirectoryProjection] = Field(max_length=50)
    next_cursor: UUID | None = None


class TrainingPage(HrModel):
    items: list[TrainingProjection] = Field(max_length=50)
    next_cursor: UUID | None = None


class HrContext(HrModel):
    private_areas: tuple[Literal["recruitment", "attendance", "payroll"], ...] = ()
    employee: EmployeeRef | None
    client_owner: bool
    actions: tuple[str, ...]
    # The access switches the caller has on, in the access page's order.
    switches: tuple[SwitchId, ...] = ()
    # Workflow grants as "domain.action", sorted and unique.
    workflows: tuple[str, ...] = ()
    # Whether the caller has any HR work beyond their own requests and the directory.
    hr_duties: bool = False


class HrGuidance(HrModel):
    """Ephemeral grant summary, not a resource authorization or workflow result."""

    kind: Literal["hr_navigation"] = "hr_navigation"
    actions: tuple[HrAction, ...] = Field(max_length=6)
    source: Literal["current_hr_grants"] = "current_hr_grants"
    as_of: AwareDatetime
