"""Persisted, versioned HR workflows with explicit domain grants and human decisions."""

from __future__ import annotations

from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, Field

from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr.models import EmployeeRef, HrModel, Version
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService, _fingerprint
from csa.hr.workflows import (
    AttendanceInput,
    Domain,
    LeaveInput,
    RecruitmentInput,
    WorkflowCommand,
    WorkflowInput,
    WorkflowRecord,
)


class CaseRow(HrModel):
    client_id: UUID
    id: UUID
    domain: Domain
    employee_id: UUID | None
    created_by: UUID
    payload: WorkflowInput
    status: str
    version: Version
    updated_at: AwareDatetime
    decision_reason: str | None

    def public(self) -> WorkflowRecord:
        return WorkflowRecord.model_validate(
            {
                "id": self.id,
                "domain": self.domain,
                "employee": EmployeeRef(tenant_id=self.client_id, employee_id=self.employee_id)
                if self.employee_id is not None
                else None,
                "content": self.payload,
                "created_by": self.created_by,
                "status": self.status,
                "version": self.version,
                "updated_at": self.updated_at,
                "interview_guide": self.payload.guide()
                if isinstance(self.payload, RecruitmentInput)
                else (),
                "decision_reason": self.decision_reason,
            }
        )


class CasePage(HrModel):
    items: list[WorkflowRecord] = Field(max_length=50)
    next_cursor: UUID | None = None


class HrWorkflowService:
    def __init__(self, core: HrService):
        self.core = core

    async def _permit(
        self,
        conn: AsyncConnection,
        session: HrSession,
        domain: str,
        action: str,
        employee: UUID | None,
    ) -> None:
        if session.capability is not None:
            required = {
                "recruitment": "hr_recruitment",
                "attendance": "hr_attendance",
                "leave": "hr_attendance",
                "payroll": "hr_payroll",
            }.get(domain)
            if session.capability != required:
                raise HrPolicyError("permission_denied")
        row = await q.fetch_one(
            "hr/case_access.sql", (session.tenant_id, domain, action, employee), conn=conn
        )
        if action == "read" and (not row or row["allowed"] is not True):
            row = await q.fetch_one(
                "hr/case_access.sql", (session.tenant_id, domain, "decide", employee), conn=conn
            )
        if not row or row["allowed"] is not True:
            raise HrPolicyError("permission_denied")

    async def _get(
        self, conn: AsyncConnection, session: HrSession, case_id: UUID, *, lock: bool = False
    ) -> CaseRow:
        row = await q.fetch_one(
            "hr/case_lock.sql" if lock else "hr/case_get.sql",
            (session.tenant_id, case_id),
            conn=conn,
            model=CaseRow,
        )
        if row is None:
            raise HrPolicyError("resource_unavailable")
        return row

    async def page(self, session: HrSession, after: UUID | None, limit: int) -> CasePage:
        if not 1 <= limit <= 50:
            raise HrPolicyError("invalid_limit")
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            rows = await q.fetch_all(
                "hr/case_page.sql",
                (session.tenant_id, after, after, limit + 1),
                conn=conn,
                model=CaseRow,
            )
            result = CasePage(
                items=[r.public() for r in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )
            await self.core._session(conn, session)
        return result

    async def get(self, session: HrSession, case_id: UUID) -> WorkflowRecord:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            row = await self._get(conn, session, case_id)
            await self._permit(conn, session, row.domain, "read", row.employee_id)
            await self.core._session(conn, session)
        return row.public()

    async def prepare(
        self, session: HrSession, content: WorkflowInput, key: UUID
    ) -> WorkflowRecord:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            return await self._prepare_in(conn, session, content, key)

    async def _prepare_in(
        self,
        conn: AsyncConnection,
        session: HrSession,
        content: WorkflowInput,
        key: UUID,
        *,
        employee_override: UUID | None = None,
    ) -> WorkflowRecord:
        if session.csrf_hash is None:
            raise HrPolicyError("csrf_required")
        fingerprint = _fingerprint(
            "workflow.prepare",
            {
                "content": content.model_dump(mode="json"),
                "employee_override": str(employee_override) if employee_override else None,
            },
        )
        access = await self.core._access(conn, session)
        employee = employee_override
        if employee_override is None and isinstance(content, (AttendanceInput, LeaveInput)):
            if access.employee is None:
                raise HrPolicyError("permission_denied")
            employee = access.employee.employee_id
        if employee_override is not None:
            if not isinstance(content, AttendanceInput):
                raise HrPolicyError("permission_denied")
            check = await q.fetch_one(
                "hr/import_employee.sql", (session.tenant_id, employee_override), conn=conn
            )
            if not check or not check["allowed"]:
                raise HrPolicyError("permission_denied")
        else:
            await self._permit(conn, session, content.domain, "prepare", employee)
        await q.execute(
            "hr/key_lock.sql",
            (f"{session.tenant_id}:case:{session.subject_id}:{key}",),
            conn=conn,
        )
        existing = await q.fetch_one(
            "hr/case_event_get.sql", (session.tenant_id, session.subject_id, key), conn=conn
        )
        if existing is not None:
            row = await self._get(conn, session, existing["case_id"])
            if employee_override is None:
                await self._permit(conn, session, row.domain, "prepare", row.employee_id)
            if existing["fingerprint"] != fingerprint:
                raise HrPolicyError("idempotency_conflict")
            result = row.public().model_copy(
                update={
                    "status": existing["status"],
                    "version": existing["version"],
                    "updated_at": existing["occurred_at"],
                    "decision_reason": None,
                }
            )
        else:
            if isinstance(content, AttendanceInput):
                if content.ends_at > self.core.now():
                    raise HrPolicyError("future_attendance_forbidden")
                if content.correction_of is not None:
                    original = await self._get(conn, session, content.correction_of)
                    if (
                        original.domain != "attendance"
                        or original.employee_id != employee
                        or original.status != "approved"
                    ):
                        raise HrPolicyError("invalid_correction")
            if isinstance(content, RecruitmentInput) and content.candidate_reference is not None:
                available = await q.fetch_one(
                    "hr/candidate_available.sql",
                    (session.tenant_id, content.candidate_reference, content.consent_reference),
                    conn=conn,
                )
                if not available or not available["allowed"]:
                    raise HrPolicyError("verified_candidate_intake_required")
            stamp = self.core.now()
            case_id = uuid4()
            await self.core._session(conn, session)
            await q.execute(
                "hr/case_create.sql",
                (
                    session.tenant_id,
                    case_id,
                    content.domain,
                    employee,
                    session.subject_id,
                    Jsonb(content.model_dump(mode="json")),
                    stamp,
                ),
                conn=conn,
            )
            await q.execute(
                "hr/case_event.sql",
                (
                    session.tenant_id,
                    case_id,
                    0,
                    session.subject_id,
                    key,
                    fingerprint,
                    "prepare",
                    "pending",
                    stamp,
                ),
                conn=conn,
            )
            row = await self._get(conn, session, case_id)
            result = row.public()
        await self.core._session(conn, session)
        return result

    async def decide(
        self, session: HrSession, case_id: UUID, command: WorkflowCommand, key: UUID
    ) -> WorkflowRecord:
        if session.csrf_hash is None:
            raise HrPolicyError("csrf_required")
        fingerprint = _fingerprint(
            "workflow.decide", {"case_id": str(case_id), **command.model_dump(mode="json")}
        )
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            await q.execute(
                "hr/key_lock.sql",
                (f"{session.tenant_id}:case:{session.subject_id}:{key}",),
                conn=conn,
            )
            initial = await self._get(conn, session, case_id)
            action = "prepare" if command.action == "withdraw" else "decide"
            await self._permit(conn, session, initial.domain, action, initial.employee_id)
            if command.action == "withdraw" and initial.created_by != session.subject_id:
                raise HrPolicyError("permission_denied")
            if (
                initial.domain == "payroll"
                and command.action != "withdraw"
                and initial.created_by == session.subject_id
            ):
                raise HrPolicyError("distinct_payroll_reviewer_required")
            existing = await q.fetch_one(
                "hr/case_event_get.sql", (session.tenant_id, session.subject_id, key), conn=conn
            )
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise HrPolicyError("idempotency_conflict")
                result = initial.public().model_copy(
                    update={
                        "status": existing["status"],
                        "version": existing["version"],
                        "updated_at": existing["occurred_at"],
                    }
                )
            else:
                row = await self._get(conn, session, case_id, lock=True)
                if row.version != command.expected_version:
                    raise HrPolicyError("stale_version")
                if row.status != "pending":
                    raise HrPolicyError("invalid_transition")
                if command.action == "approve" and isinstance(row.payload, AttendanceInput):
                    await q.execute(
                        "hr/key_lock.sql",
                        (f"{session.tenant_id}:attendance:{row.employee_id}",),
                        conn=conn,
                    )
                    correction = row.payload.correction_of
                    if correction is not None:
                        used = await q.fetch_one(
                            "hr/attendance_correction_used.sql",
                            (session.tenant_id, row.employee_id, str(correction)),
                            conn=conn,
                        )
                        if used and used["used"]:
                            raise HrPolicyError("attendance_already_corrected")
                    conflict = await q.fetch_one(
                        "hr/attendance_conflict.sql",
                        (
                            session.tenant_id,
                            row.employee_id,
                            row.id,
                            correction,
                            correction,
                            row.payload.ends_at,
                            row.payload.starts_at,
                        ),
                        conn=conn,
                    )
                    if conflict and conflict["conflict"]:
                        raise HrPolicyError("attendance_overlap")
                from csa.hr.operations import HrOperations

                operations = HrOperations(self.core)
                if command.action == "approve" and isinstance(row.payload, LeaveInput):
                    if row.employee_id is None:
                        raise HrPolicyError("resource_unavailable")
                    await operations.approve_leave(
                        conn, session, row.id, row.employee_id, row.payload
                    )
                if command.action == "approve" and row.domain == "payroll":
                    from csa.hr.workflows import PayrollInput

                    if not isinstance(row.payload, PayrollInput):
                        raise HrPolicyError("resource_unavailable")
                    await operations.approve_payroll(
                        conn, session, row.id, row.payload, command.payroll_digest
                    )
                status = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[
                    command.action
                ]
                stamp = self.core.now()
                await self.core._session(conn, session)
                updated = await q.fetch_one(
                    "hr/case_update.sql",
                    (
                        status,
                        stamp,
                        command.reason,
                        session.tenant_id,
                        case_id,
                        command.expected_version,
                    ),
                    conn=conn,
                    model=CaseRow,
                )
                if updated is None:
                    raise HrPolicyError("stale_version")
                await q.execute(
                    "hr/case_event.sql",
                    (
                        session.tenant_id,
                        case_id,
                        updated.version,
                        session.subject_id,
                        key,
                        fingerprint,
                        command.action,
                        status,
                        stamp,
                    ),
                    conn=conn,
                )
                result = updated.public()
        return result
