"""Contracts for HR process templates and processes (migrations 0630-0636).

Inputs mirror the limits of the database validators (`hr_template_apply`, `hr_process_start_apply`),
so a request the database would refuse for its shape is a 422 here first; rules that need stored
state (a person of this company, the anchor a kind allows) stay with the database and come back as
stable problem codes (`csa.hr.processes.PROBLEMS`).

Outputs keep the visibility shapes the read functions return (0633): the HR team, a manager, the
person and an assignee each get a different subset of fields. Every field another viewer may lack is
optional, and the process routes answer with `response_model_exclude_unset`, so a field the caller
may not see is absent while a visible empty field is `null`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    ValidationInfo,
    field_validator,
)

from csa.hr.models import HrModel, Version
from csa.hr.people_types import EmploymentType
from csa.hr.workflows import Region

__all__ = [
    "Anchor",
    "AnchorDates",
    "ApprovalBy",
    "AutoCheck",
    "MyTask",
    "MyTaskPage",
    "NoMatchingTemplate",
    "OwnerRole",
    "ProcessEventAction",
    "PersonProcess",
    "PersonProcesses",
    "ProcessCancelReceipt",
    "ProcessClose",
    "ProcessCloseReceipt",
    "ProcessDetail",
    "ProcessEvent",
    "ProcessEventDetail",
    "ProcessHeader",
    "ProcessKind",
    "ProcessNextTask",
    "ProcessPage",
    "ProcessRow",
    "ProcessStart",
    "ProcessStartReceipt",
    "ProcessStatus",
    "ProcessTask",
    "ProcessView",
    "TaskApprove",
    "TaskComplete",
    "TaskNote",
    "TaskOperation",
    "TaskReason",
    "TaskReassign",
    "TaskReceipt",
    "TaskReschedule",
    "TaskStatus",
    "TemplateCreate",
    "TemplateDeactivate",
    "TemplateList",
    "TemplateOut",
    "TemplatePreview",
    "TemplatePreviewRequest",
    "TemplatePreviewTie",
    "TemplateReceipt",
    "TemplateRules",
    "TemplateStep",
    "TemplateStepOut",
    "TemplateSummary",
    "TemplateUpdate",
    "TemplateWarning",
    "TemplateWarningCode",
]

ProcessKind = Literal["onboarding", "offboarding", "custom"]
OwnerRole = Literal["hr", "manager", "employee", "person"]
Anchor = Literal["start_date", "last_day", "probation_end"]
ApprovalBy = Literal["hr", "manager"]
# The document categories are the kinds of hr_employee_documents (csa.hr.documents.DocumentKind).
AutoCheck = Literal[
    "account_linked",
    "personal_details_complete",
    "access_removed",
    "document_uploaded:contract",
    "document_uploaded:identity",
    "document_uploaded:certificate",
    "document_uploaded:other",
]
TaskStatus = Literal["todo", "awaiting_approval", "done", "rejected", "skipped"]
ProcessStatus = Literal["active", "completed", "cancelled"]
ProcessView = Literal["hr", "manager", "self", "assignee"]
# The event actions and template warning codes the migrations write today. The output models take
# any short string, so an action a later migration adds never breaks a read;
# tests/test_hr_process_models.py fails until it is listed here (and its detail keys below).
ProcessEventAction = Literal[
    "process.started",
    "process.cancelled",
    "process.completed",
    "task.created",
    "task.completed",
    "task.skipped",
    "task.reassigned",
    "task.date_changed",
    "task.approved",
    "task.sent_back",
    "task.noted",
    "task.rescheduled",
]
TemplateWarningCode = Literal["step_after_last_day"]
TaskOperation = Literal[
    "task.complete",
    "task.skip",
    "task.approve",
    "task.send_back",
    "task.note",
    "task.reschedule",
    "task.reassign",
]
# Text that may hold line breaks: a step's descriptions, a task's note (the database appends
# "person left" to a note on a new line, 0632 hr_process_person_left), its send-back reason, the
# reason of a skip or send back and the note of closing a process anyway.
_MULTILINE = frozenset({"description_en", "description_ms", "note", "sent_back_reason", "reason"})


class _StepText(HrModel):
    """HrModel's control-character rule, except that a step's descriptions and a task's note may
    hold line breaks (line feeds). Every other control character, a tab or a lone carriage return
    included, is still refused."""

    @field_validator("*", mode="after")
    @classmethod
    def reject_controls(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls, value: object, info: ValidationInfo
    ) -> object:
        if isinstance(value, str):
            text = value.replace("\n", "") if info.field_name in _MULTILINE else value
            if any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError("control characters forbidden")
        return value


# ---- Templates: input -------------------------------------------------------------------------


class TemplateRules(HrModel):
    """Who a template applies to; every rule left `null` matches everyone. Text is trimmed and an
    empty string counts as no rule (as the database stores it)."""

    department_id: UUID | None = None
    employment_type: EmploymentType | None = None
    legal_entity_reference: str | None = Field(default=None, max_length=200)
    region: Region | None = None


class TemplateStep(_StepText):
    """One step; its ordinal is its position in `steps`. A `person` step names `owner_employee_id`
    (an active person of this company); no other role may."""

    title_en: str = Field(min_length=1, max_length=160)
    title_ms: str | None = Field(default=None, max_length=160)
    description_en: str | None = Field(default=None, max_length=2000)
    description_ms: str | None = Field(default=None, max_length=2000)
    owner_role: OwnerRole
    owner_employee_id: UUID | None = None
    anchor: Anchor
    due_offset_days: StrictInt = Field(ge=-365, le=365)
    required: StrictBool = True
    approval_by: ApprovalBy | None = None
    auto_check: AutoCheck | None = None
    reference_note: str | None = Field(default=None, max_length=500)

    @field_validator("description_en", "description_ms", mode="before")
    @classmethod
    def unix_line_breaks(cls, value: object) -> object:
        """A browser textarea may send CRLF line breaks; they are stored as LF."""
        return value.replace("\r\n", "\n") if isinstance(value, str) else value


class TemplateCreate(HrModel):
    kind: ProcessKind
    name: str = Field(min_length=1, max_length=120)
    rules: TemplateRules | None = None
    steps: list[TemplateStep] = Field(min_length=1, max_length=40)


class TemplateUpdate(HrModel):
    """Replaces the name, rules and steps (version + 1; the kind never changes). `rules` has no
    default on purpose: a client that forgets it gets a 422 instead of silently clearing the rules;
    send `null` to make the template the company default. `is_active` `null` keeps it."""

    expected_version: Version
    name: str = Field(min_length=1, max_length=120)
    rules: TemplateRules | None
    steps: list[TemplateStep] = Field(min_length=1, max_length=40)
    is_active: StrictBool | None = None


class TemplateDeactivate(HrModel):
    expected_version: Version


# ---- Templates: output ------------------------------------------------------------------------


class TemplateWarning(HrModel):
    """`code` is one of `TemplateWarningCode`: `step_after_last_day`, a step of the leaver in an
    offboarding template is due after the last day; it will be due on the last day instead (spec
    5.7.7). A code a later migration adds comes through unchanged rather than failing the read."""

    code: str = Field(min_length=1, max_length=80)
    ordinal: int


class TemplateReceipt(HrModel):
    operation: Literal["template.create", "template.update", "template.deactivate"]
    template_id: UUID
    version: Version
    # Other active templates of the same kind that may tie with this one for some person.
    conflicts: list[UUID]
    warnings: list[TemplateWarning]
    status: Literal["saved"]
    duplicate: bool


class TemplateSummary(HrModel):
    template_id: UUID
    kind: ProcessKind
    name: str
    version: Version
    is_active: bool
    rules: TemplateRules
    step_count: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TemplateList(HrModel):
    items: list[TemplateSummary] = Field(max_length=500)


class TemplateStepOut(_StepText):
    ordinal: int
    title_en: str
    title_ms: str | None
    description_en: str | None
    description_ms: str | None
    owner_role: OwnerRole
    owner_employee_id: UUID | None
    anchor: Anchor
    due_offset_days: int
    required: bool
    approval_by: ApprovalBy | None
    auto_check: AutoCheck | None
    reference_note: str | None


class TemplateOut(HrModel):
    template_id: UUID
    kind: ProcessKind
    name: str
    version: Version
    is_active: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    rules: TemplateRules
    steps: list[TemplateStepOut] = Field(max_length=40)
    conflicts: list[UUID]
    warnings: list[TemplateWarning]


class TemplatePreviewRequest(HrModel):
    """A draft rule set of the template editor (0636 `hr_template_preview`); nothing is written."""

    kind: ProcessKind
    rules: TemplateRules | None = None


class TemplatePreviewTie(HrModel):
    template_id: UUID
    name: str


class TemplatePreview(HrModel):
    """`matching`: the active people the rules apply to now (as a template's rules match a person);
    `ties`: the other active templates of the kind one person could match as well (the conflicts a
    save reports; none for custom), oldest first; `total_active`: the active people."""

    matching: int
    ties: list[TemplatePreviewTie]
    total_active: int


# ---- Processes: input and receipts ------------------------------------------------------------


class ProcessStart(HrModel):
    """Start a process by hand: a custom one (`template_id` required) or an onboarding or
    offboarding of an existing person (`template_id` optional: the matching template). An active
    onboarding or offboarding of the person is replaced (cancelled) only with `replace` true,
    else the start is refused (`process_already_active`, 0636)."""

    employee_id: UUID
    kind: ProcessKind
    template_id: UUID | None = None
    replace: StrictBool = False


class ProcessStartReceipt(HrModel):
    operation: Literal["process.start"]
    process_id: UUID
    employee_id: UUID
    kind: ProcessKind
    template_id: UUID | None
    status: Literal["saved"]
    duplicate: bool


class ProcessCancelReceipt(HrModel):
    operation: Literal["process.cancel"]
    process_id: UUID
    status: Literal["saved"]
    duplicate: bool


# ---- Processes: output ------------------------------------------------------------------------


class ProcessRow(HrModel):
    """One process in the list. For the person, `next_due_on` and `overdue` count only their own
    tasks."""

    process_id: UUID
    employee_id: UUID
    employee_name: str
    department_id: UUID | None
    kind: ProcessKind
    name: str | None  # the template's name; null without one
    status: ProcessStatus
    started_at: AwareDatetime
    closed_at: AwareDatetime | None
    tasks_total: int
    tasks_done: int
    next_due_on: date | None
    overdue: bool


class ProcessPage(HrModel):
    items: list[ProcessRow] = Field(max_length=200)
    next_cursor: UUID | None = None


class AnchorDates(HrModel):
    """The dates a process counts from. A date a later migration adds is dropped, not an error."""

    model_config = ConfigDict(extra="ignore")

    start_date: date | None = None
    last_day: date | None = None
    probation_end: date | None = None


class ProcessHeader(HrModel):
    """Everyone who may see the process gets the first five fields. `started_at` and `closed_at`:
    HR, manager and person. `employee_id`, `tasks_total` and `tasks_done`: HR and manager.
    `required_total`, `required_done` and `open_tasks`: the person. The template, anchor dates and
    who started it: HR only."""

    process_id: UUID
    kind: ProcessKind
    name: str | None
    status: ProcessStatus
    employee_name: str
    started_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    employee_id: UUID | None = None
    tasks_total: int | None = None
    tasks_done: int | None = None
    required_total: int | None = None
    required_done: int | None = None
    open_tasks: int | None = None
    template_id: UUID | None = None
    template_version: int | None = None
    anchor_dates: AnchorDates | None = None
    started_by: UUID | None = None


class ProcessTask(_StepText):
    """A task as the caller may see it. Everyone: the first eleven fields. The caller's own tasks
    (and every task for HR) add the description, anchor, approval, check, reference note (copied
    from the step when the process started), completion time, note, version, the send-back
    reason (why the approver sent it back; cleared when it is completed again) and `done_by_hr`
    (completed by someone other than the named assignee: only the HR team may, spec section 12).
    HR alone gets the assignee id, `assignee_overridden` (HR reassigned it by hand), the offset
    and override, and who completed and approved it."""

    task_id: UUID
    ordinal: int
    title_en: str
    title_ms: str | None
    owner_role: OwnerRole
    assignee_name: str | None
    due_on: date | None
    status: TaskStatus
    required: bool
    overdue: bool
    mine: bool
    description_en: str | None = None
    description_ms: str | None = None
    anchor: Anchor | None = None
    approval_by: ApprovalBy | None = None
    auto_check: AutoCheck | None = None
    reference_note: str | None = None
    completed_at: AwareDatetime | None = None
    note: str | None = None
    version: Version | None = None
    sent_back_reason: str | None = None
    done_by_hr: bool | None = None
    assignee_employee_id: UUID | None = None
    assignee_overridden: bool | None = None
    due_offset_days: int | None = None
    due_overridden: bool | None = None
    completed_by: UUID | None = None
    approved_by: UUID | None = None
    approved_at: AwareDatetime | None = None


class ProcessEventDetail(BaseModel):
    """The detail of an event; which keys appear depends on the action (0632, 0634). Events are
    immutable, so a stored value that a typed field refused would break the read for good: every
    value is returned exactly as it is stored (any JSON value, not trimmed, no control-character
    rule), and a key a later migration adds is passed through the same way. What the migrations
    write today: `reason` (a code such as `superseded`, `person_left`, `no_manager`,
    `person_inactive`, `skipped_by_hr`, `reassigned_by_hr`, `closed_anyway`), `kind`,
    `template_id`, `template_version`, `owner_role`, `assignee_employee_id`, `due_on`, `fallback`
    (`hr_pool`), `clamped`, `anchor`, `old` and `new` (dates), `from` and `to` (employee ids or
    null), `auto_check`; from 0634 `remark` (HR's own words for a skip, send back or close
    anyway, any text with line breaks; HR-only, like every event), `sent_back` (a send back that
    skipped the task of a person who has left), `awaiting_approval` and `on_behalf` (a completion
    that waits for approval, done by HR for the named assignee), `approver` (`hr` or `manager`)
    and `approved_without_second_person` (the HR owner approved their own step, spec 5.7.2)."""

    model_config = ConfigDict(extra="allow", frozen=True, hide_input_in_errors=True)

    reason: JsonValue = None
    kind: JsonValue = None
    template_id: JsonValue = None
    template_version: JsonValue = None
    owner_role: JsonValue = None
    assignee_employee_id: JsonValue = None
    due_on: JsonValue = None
    fallback: JsonValue = None
    clamped: JsonValue = None
    anchor: JsonValue = None
    old: JsonValue = None
    new: JsonValue = None
    from_: JsonValue = Field(default=None, alias="from")
    to: JsonValue = None
    auto_check: JsonValue = None
    remark: JsonValue = None
    sent_back: JsonValue = None
    awaiting_approval: JsonValue = None
    on_behalf: JsonValue = None
    approver: JsonValue = None
    approved_without_second_person: JsonValue = None


class ProcessEvent(HrModel):
    event_id: UUID
    at: AwareDatetime
    actor: UUID | None  # null: the system
    # One of ProcessEventAction today; any short string, so a later action never fails the read.
    action: str = Field(min_length=1, max_length=80)
    task_id: UUID | None
    detail: ProcessEventDetail


class ProcessDetail(HrModel):
    view: ProcessView
    process: ProcessHeader
    tasks: list[ProcessTask] = Field(max_length=40)
    events: list[ProcessEvent] | None = None  # HR only


class ProcessNextTask(HrModel):
    title_en: str
    title_ms: str | None
    due_on: date | None


class PersonProcess(HrModel):
    process_id: UUID
    kind: ProcessKind
    name: str | None
    status: ProcessStatus
    started_at: AwareDatetime
    closed_at: AwareDatetime | None
    tasks_total: int
    tasks_done: int
    next_task: ProcessNextTask | None


class NoMatchingTemplate(HrModel):
    onboarding: bool
    offboarding: bool


class PersonProcesses(HrModel):
    """A person's processes, newest first. `anchor_dates`: HR and the person.
    `no_matching_template`: HR only."""

    view: Literal["hr", "manager", "self"]
    employee_id: UUID
    processes: list[PersonProcess]
    anchor_dates: AnchorDates | None = None
    no_matching_template: NoMatchingTemplate | None = None


# ---- Tasks: input and receipts (hr_task_apply, 0634) ------------------------------------------


class _TaskText(_StepText):
    """A note or reason typed in a browser textarea: CRLF line breaks are stored as LF."""

    @field_validator("*", mode="before")
    @classmethod
    def unix_line_breaks(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str) and info.field_name in _MULTILINE:
            return value.replace("\r\n", "\n")
        return value


class TaskComplete(_TaskText):
    """Complete a step (the assignee, or the HR team). A `note` replaces the shared note the
    assignee sees; `null` or absent keeps it. A step with an approver then waits for approval."""

    expected_version: Version
    note: str | None = Field(default=None, max_length=2000)


class TaskApprove(HrModel):
    expected_version: Version


class TaskReason(_TaskText):
    """Skip (the HR team) or send back (the approver) a step. The reason of a send back is shown
    to the assignee; the reason of a skip stays in HR's event log."""

    expected_version: Version
    reason: str = Field(min_length=1, max_length=500)


class TaskNote(_TaskText):
    """Replace the shared note (the assignee or the HR team). `note` must be sent: `null` or an
    empty string clears it."""

    note: str | None = Field(max_length=2000)


class TaskReschedule(HrModel):
    """A new due date (the HR team); it then stays put when the anchor date moves."""

    expected_version: Version
    due_on: date


class TaskReassign(HrModel):
    """A new assignee (the HR team): an active person of this company, or `null` for the HR pool.
    `assignee_employee_id` must be sent; the task then stays put when the manager changes."""

    expected_version: Version
    assignee_employee_id: UUID | None


class ProcessClose(_TaskText):
    """Close an active process anyway (the HR team, spec 5.7.4): its open steps are skipped. The
    note stays in HR's event log."""

    note: str = Field(min_length=1, max_length=500)


class TaskReceipt(HrModel):
    operation: TaskOperation
    task_id: UUID
    process_id: UUID
    task_status: TaskStatus
    version: Version
    process_status: ProcessStatus
    status: Literal["saved"]
    duplicate: bool


class ProcessCloseReceipt(HrModel):
    operation: Literal["process.close_anyway"]
    process_id: UUID
    process_status: ProcessStatus
    status: Literal["saved"]
    duplicate: bool


# ---- Tasks: My tasks (hr_task_rows_mine, 0634) ------------------------------------------------


class MyTask(_StepText):
    """One row of My tasks: the caller's own steps (`mine`), the HR pool for the HR team (`pool`),
    for the HR team the steps of people who cannot get their reminders (`mine` false, named in
    `assignee_name`), and the steps waiting for the caller's approval (`needs_my_approval`).
    Every field is always present. `assignee_name` is the HR team's only (`null` for a pool step
    and for anyone else). On a row the caller only approves (a manager's approval of a report's
    step) the descriptions, `reference_note`, `note`, `sent_back_reason` and `done_by_hr` are
    withheld as `null`; the descriptions, `note` and `sent_back_reason` may hold line breaks.
    tests/test_hr_process_models.py checks that these are the columns of the latest migration's
    `hr_task_rows_mine`."""

    task_id: UUID
    process_id: UUID
    process_kind: ProcessKind
    process_name: str | None  # the template's name; null without one
    employee_name: str  # the person the process is about
    ordinal: int
    title_en: str
    title_ms: str | None
    description_en: str | None
    description_ms: str | None
    owner_role: OwnerRole
    due_on: date | None
    required: bool
    approval_by: ApprovalBy | None
    auto_check: AutoCheck | None
    reference_note: str | None
    status: TaskStatus
    note: str | None
    version: Version
    overdue: bool
    pool: bool
    needs_my_approval: bool
    sent_back_reason: str | None
    mine: bool  # the caller is the assignee
    assignee_name: str | None
    done_by_hr: bool | None  # completed by someone other than the named assignee


class MyTaskPage(HrModel):
    items: list[MyTask] = Field(max_length=200)
    next_cursor: UUID | None = None
