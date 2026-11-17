"""HR process templates, processes and tasks: the service behind `/templates`, `/processes`,
`/people/{employee_id}/processes` and `/tasks`.

The database decides (0631-0634): who may see which process and which of its fields, what a valid
template is, who may act on a task, and every write with its idempotency receipt and audit. This
module checks the session, keeps template reads and the template and process writes to the HR
team (the HR owner or a Manage people holder; the database checks again), leaves who may act on a
task to the database (the assignee, the approver or the HR team, 0634 `hr_task_apply`), turns
each database refusal into a stable problem code (`PROBLEMS` says what each one means) and
validates every row into `process_models`. No database text ever reaches a response: an unknown
refusal is re-raised and the HR route answers 503 `hr_database_unavailable`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.core.db import q as sql
from csa.core.tenancy import tx
from csa.hr.operations import HrOperations
from csa.hr.policy import HrPolicyError
from csa.hr.process_models import (
    MyTask,
    MyTaskPage,
    PersonProcesses,
    ProcessCancelReceipt,
    ProcessClose,
    ProcessCloseReceipt,
    ProcessDetail,
    ProcessKind,
    ProcessPage,
    ProcessRow,
    ProcessStart,
    ProcessStartReceipt,
    ProcessStatus,
    TaskApprove,
    TaskComplete,
    TaskNote,
    TaskOperation,
    TaskReason,
    TaskReassign,
    TaskReceipt,
    TaskReschedule,
    TaskStatus,
    TemplateCreate,
    TemplateDeactivate,
    TemplateList,
    TemplateOut,
    TemplateReceipt,
    TemplateSummary,
    TemplateUpdate,
)
from csa.hr.records import HrSession
from csa.hr.service import HrService, _fingerprint

__all__ = ["PROBLEMS", "REFUSALS", "HrProcesses"]

# Every RAISE of hr_template_apply, hr_process_start_apply, hr_process_cancel_apply,
# hr_probation_set and hr_task_apply (0631, 0632, 0634), and the problem code it becomes.
# tests/test_hr_process_api.py checks that every code the migrations raise is here.
REFUSALS: dict[str, str] = {
    "hr_people_manager_required": "permission_denied",
    "hr_idempotency_conflict": "idempotency_conflict",
    "hr_operation_unsupported": "unsupported_operation",
    # Templates.
    "hr_template_unavailable": "template_unavailable",
    "hr_template_changed": "template_changed",
    "hr_template_kind_invalid": "template_kind_invalid",
    "hr_template_name_invalid": "template_name_invalid",
    "hr_template_invalid": "template_invalid",
    "hr_template_rules_invalid": "template_rules_invalid",
    "hr_department_unavailable": "department_unavailable",
    "hr_template_steps_invalid": "template_steps_invalid",
    "hr_template_step_title_invalid": "template_step_title_invalid",
    "hr_template_step_text_invalid": "template_step_text_invalid",
    "hr_template_step_owner_invalid": "template_step_owner_invalid",
    "hr_template_step_person_unavailable": "template_step_person_unavailable",
    "hr_template_step_anchor_invalid": "template_step_anchor_invalid",
    "hr_template_step_offset_invalid": "template_step_offset_invalid",
    "hr_template_step_approval_invalid": "template_step_approval_invalid",
    "hr_template_step_check_invalid": "template_step_check_invalid",
    # Processes.
    "hr_process_kind_invalid": "process_kind_invalid",
    "hr_employee_unavailable": "resource_unavailable",
    "hr_person_not_active": "person_not_active",
    "hr_person_not_leaving": "person_not_leaving",
    "hr_process_template_required": "process_template_required",
    "hr_process_no_template": "process_no_template",
    "hr_process_already_active": "process_already_active",
    "hr_process_unavailable": "resource_unavailable",
    "hr_process_closed": "process_closed",
    # Probation (person.probation_set on POST /operations).
    "hr_person_changed": "person_changed",
    "hr_probation_end_invalid": "probation_end_invalid",
    # Tasks (0634, the /tasks routes and POST /processes/{process_id}/close).
    "hr_task_unavailable": "resource_unavailable",
    "hr_task_changed": "task_changed",
    "hr_task_closed": "task_closed",
    "hr_task_not_assignee": "task_not_assignee",
    "hr_task_not_approver": "task_not_approver",
    "hr_task_awaiting_approval": "task_awaiting_approval",
    "hr_task_not_awaiting_approval": "task_not_awaiting_approval",
    "hr_task_self_approval": "task_self_approval",
    "hr_task_own_process": "task_own_process",
    "hr_task_check_not_met": "check_not_met",
    "hr_task_system_only": "task_system_only",
    "hr_task_reason_invalid": "task_reason_invalid",
    "hr_task_note_invalid": "task_note_invalid",
    "hr_task_due_invalid": "task_due_invalid",
    "hr_task_assignee_unavailable": "task_assignee_unavailable",
}
# Each problem code of these routes in a plain sentence, for docs/CONSOLE_API.md and the console.
PROBLEMS: dict[str, str] = {
    "permission_denied": "Only the HR owner or someone with Manage people can do this.",
    "resource_unavailable": "That item does not exist, or you cannot see it.",
    "idempotency_conflict": "This request key was already used for a different change.",
    "unsupported_operation": "That change is not supported.",
    "human_operation_required": "Only a signed-in person can make this change.",
    "template_unavailable": "That template is not available for this kind of process.",
    "template_changed": "Someone changed this template since you opened it. Reload to see it.",
    "template_kind_invalid": "Choose onboarding, offboarding or custom.",
    "template_name_invalid": "Give the template a name of at most 120 characters.",
    "template_invalid": "Active must be yes or no.",
    "template_rules_invalid": "Check who the template applies to.",
    "department_unavailable": "That department is not in this company.",
    "template_steps_invalid": "A template needs 1 to 40 steps.",
    "template_step_title_invalid": "Each step needs an English title of at most 160 characters.",
    "template_step_text_invalid": "A description is at most 2000 characters, a reference 500.",
    "template_step_owner_invalid": "Only a step owned by a named person names that person.",
    "template_step_person_unavailable": "Choose an active person of this company for the step.",
    "template_step_anchor_invalid": (
        "Onboarding steps count from the start date or the probation end, offboarding steps "
        "from the last day."
    ),
    "template_step_offset_invalid": "A step is due at most 365 days before or after its date.",
    "template_step_approval_invalid": "A step is approved by HR or the manager.",
    "template_step_check_invalid": "That automatic check is not available.",
    "process_kind_invalid": "Choose onboarding, offboarding or custom.",
    "person_not_active": "This person is not active.",
    "person_not_leaving": "Mark this person as leaving before starting their offboarding.",
    "process_template_required": "Choose a template for a custom process.",
    "process_no_template": "No active template applies to this person.",
    "process_already_active": "This person already has an active process of this kind.",
    "process_closed": "This process is already completed or cancelled.",
    "person_changed": "Someone changed this person since you opened it. Reload to see it.",
    "probation_end_invalid": "Give the probation end as a date, or clear it.",
    "task_changed": "Someone changed this step since you opened it. Reload to see it.",
    "task_closed": "This step is already done or skipped, or its process is closed.",
    "task_not_assignee": "Only the person this step is assigned to, or HR, can do this.",
    "task_not_approver": "Only the approver of this step can approve it or send it back.",
    "task_awaiting_approval": "This step is waiting for approval.",
    "task_not_awaiting_approval": "This step is not waiting for approval.",
    "task_self_approval": "Someone other than the person who completed this step must approve it.",
    "task_own_process": "Someone else in HR must approve a step of a process about you.",
    "check_not_met": "The automatic check for this step is not met yet.",
    "task_system_only": "The system completes this step when the person's access is removed.",
    "task_reason_invalid": "Give a reason of 1 to 500 characters.",
    "task_note_invalid": "A note is at most 2000 characters.",
    "task_due_invalid": "Give the new due date as a date.",
    "task_assignee_unavailable": "Choose an active person of this company, or the HR pool.",
}
_WARNINGS = {"hr_template_step_after_last_day": "step_after_last_day"}
_PAGE = 200  # rows per page at most; the read returns one more to tell it was cut
TaskBody = TaskComplete | TaskApprove | TaskReason | TaskNote | TaskReschedule | TaskReassign


def _rules(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("department_id", "employment_type", "legal_entity_reference", "region")
    return {key: row[key] for key in keys}


def _warnings(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # An unknown code (a later migration's) passes through without its prefix instead of failing;
    # tests/test_hr_process_models.py makes sure every code the migrations write is listed.
    return [{**w, "code": _WARNINGS.get(w["code"], w["code"].removeprefix("hr_"))} for w in found]


class HrProcesses:
    def __init__(self, core: HrService):
        self.core = core

    @staticmethod
    async def _call(
        conn: AsyncConnection, path: str, params: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        """Run a process write; its database refusals become problem codes."""
        try:
            return await sql.fetch_one(path, params, conn=conn)
        except (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege) as exc:
            code = REFUSALS.get(exc.diag.message_primary or "")
            if code is None:
                raise
            raise HrPolicyError(code) from None

    async def _write(
        self,
        session: HrSession,
        path: str,
        body: dict[str, Any],
        key: UUID,
        *,
        team: bool = True,
    ) -> dict[str, Any]:
        """A write in the caller's session. `team`: for the HR team only, checked here first;
        otherwise (task actions) the database alone decides who may act."""
        if session.csrf_hash is None or session.capability is not None or session.assistant:
            raise HrPolicyError("human_operation_required")
        fingerprint = _fingerprint(body["operation"], body)
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            access = await self.core._access(conn, session)
            if team:
                HrOperations.people_manager(access)
            row = await self._call(conn, path, (session.tenant_id, key, fingerprint, Jsonb(body)))
            await self.core._session(conn, session)
        assert row is not None
        return row["receipt"]

    async def _read(
        self, session: HrSession, path: str, params: tuple[Any, ...], *, team: bool = False
    ) -> list[dict[str, Any]]:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            access = await self.core._access(conn, session)
            if team:
                HrOperations.people_manager(access)
            rows = await sql.fetch_all(path, params, conn=conn)
            await self.core._session(conn, session)
        return rows

    # ---- Templates --------------------------------------------------------------------------

    async def templates(self, session: HrSession) -> TemplateList:
        rows = await self._read(
            session, "hr/process_template_rows.sql", (session.tenant_id,), team=True
        )
        return TemplateList(
            items=[
                TemplateSummary.model_validate(
                    {
                        "template_id": row["template_id"],
                        "kind": row["kind"],
                        "name": row["name"],
                        "version": row["version"],
                        "is_active": row["is_active"],
                        "rules": _rules(row),
                        "step_count": row["step_count"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )
                for row in rows
            ]
        )

    async def template(self, session: HrSession, template_id: UUID) -> TemplateOut:
        rows = await self._read(
            session, "hr/process_template_get.sql", (session.tenant_id, template_id), team=True
        )
        found = rows[0]["template"] if rows else None
        if found is None:
            raise HrPolicyError("resource_unavailable")
        return TemplateOut.model_validate({**found, "warnings": _warnings(found["warnings"])})

    async def _template_write(
        self, session: HrSession, body: dict[str, Any], key: UUID
    ) -> TemplateReceipt:
        try:
            receipt = await self._write(session, "hr/process_template_apply.sql", body, key)
        except HrPolicyError as exc:
            # The template named in the path: as unavailable as any other path resource.
            if exc.code == "template_unavailable":
                raise HrPolicyError("resource_unavailable") from None
            raise
        return TemplateReceipt.model_validate(
            {**receipt, "warnings": _warnings(receipt["warnings"])}
        )

    async def create_template(
        self, session: HrSession, body: TemplateCreate, key: UUID
    ) -> TemplateReceipt:
        payload = {"operation": "template.create", **body.model_dump(mode="json")}
        return await self._template_write(session, payload, key)

    async def update_template(
        self, session: HrSession, template_id: UUID, body: TemplateUpdate, key: UUID
    ) -> TemplateReceipt:
        payload = {
            "operation": "template.update",
            "template_id": str(template_id),
            **body.model_dump(mode="json"),
        }
        return await self._template_write(session, payload, key)

    async def deactivate_template(
        self, session: HrSession, template_id: UUID, body: TemplateDeactivate, key: UUID
    ) -> TemplateReceipt:
        payload = {
            "operation": "template.deactivate",
            "template_id": str(template_id),
            **body.model_dump(mode="json"),
        }
        return await self._template_write(session, payload, key)

    # ---- Processes --------------------------------------------------------------------------

    async def processes(
        self,
        session: HrSession,
        *,
        kind: ProcessKind | None = None,
        status: ProcessStatus | None = None,
        overdue: bool | None = None,
        department: UUID | None = None,
        owner: UUID | None = None,
        limit: int = 50,
        after: UUID | None = None,
    ) -> ProcessPage:
        limit = max(1, min(limit, _PAGE))
        rows = await self._read(
            session,
            "hr/process_rows.sql",
            (session.tenant_id, kind, status, overdue, department, owner, limit + 1, after),
        )
        items = [ProcessRow.model_validate(row) for row in rows[:limit]]
        cut = len(rows) > limit
        return ProcessPage(items=items, next_cursor=items[-1].process_id if cut else None)

    async def process(self, session: HrSession, process_id: UUID) -> ProcessDetail:
        rows = await self._read(session, "hr/process_get.sql", (session.tenant_id, process_id))
        found = rows[0]["process"] if rows else None
        if found is None:
            raise HrPolicyError("resource_unavailable")
        return ProcessDetail.model_validate(found)

    async def person(self, session: HrSession, employee_id: UUID) -> PersonProcesses:
        rows = await self._read(session, "hr/process_person.sql", (session.tenant_id, employee_id))
        found = rows[0]["person"] if rows else None
        if found is None:
            raise HrPolicyError("resource_unavailable")
        return PersonProcesses.model_validate(found)

    async def start(self, session: HrSession, body: ProcessStart, key: UUID) -> ProcessStartReceipt:
        payload = {"operation": "process.start", **body.model_dump(mode="json")}
        return ProcessStartReceipt.model_validate(
            await self._write(session, "hr/process_start.sql", payload, key)
        )

    async def cancel(self, session: HrSession, process_id: UUID, key: UUID) -> ProcessCancelReceipt:
        payload = {"operation": "process.cancel", "process_id": str(process_id)}
        return ProcessCancelReceipt.model_validate(
            await self._write(session, "hr/process_cancel.sql", payload, key)
        )

    # ---- Tasks ------------------------------------------------------------------------------

    async def my_tasks(
        self,
        session: HrSession,
        *,
        status: TaskStatus | None = None,
        limit: int = 50,
        after: UUID | None = None,
    ) -> MyTaskPage:
        limit = max(1, min(limit, _PAGE))
        rows = await self._read(
            session, "hr/task_rows_mine.sql", (session.tenant_id, status, limit + 1, after)
        )
        items = [MyTask.model_validate(row) for row in rows[:limit]]
        cut = len(rows) > limit
        return MyTaskPage(items=items, next_cursor=items[-1].task_id if cut else None)

    async def task_action(
        self,
        session: HrSession,
        operation: TaskOperation,
        task_id: UUID,
        body: TaskBody,
        key: UUID,
    ) -> TaskReceipt:
        payload = {"operation": operation, "task_id": str(task_id), **body.model_dump(mode="json")}
        return TaskReceipt.model_validate(
            await self._write(session, "hr/task_apply.sql", payload, key, team=False)
        )

    async def close(
        self, session: HrSession, process_id: UUID, body: ProcessClose, key: UUID
    ) -> ProcessCloseReceipt:
        payload = {
            "operation": "process.close_anyway",
            "process_id": str(process_id),
            **body.model_dump(mode="json"),
        }
        return ProcessCloseReceipt.model_validate(
            await self._write(session, "hr/task_apply.sql", payload, key)
        )
