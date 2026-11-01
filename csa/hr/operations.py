"""Verified HR administration, private records and local automation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr.automation_models import (
    AccessSave,
    AccountLink,
    AttendanceImport,
    AutomationConfigure,
    AutomationRun,
    CalendarSave,
    CandidateErase,
    CandidateIntake,
    CandidateNote,
    DepartmentSave,
    EmployeeSave,
    HrOperation,
    InterviewSchedule,
    LeaveBalanceSave,
    NotificationRead,
    OperationReceipt,
    PayrollReverse,
    PayrollRules,
    PayrollSnapshot,
    PersonAdd,
    PersonUpdate,
    PrivateAccessSave,
    ScheduleCancel,
    ShiftSchedule,
    leave_days,
)
from csa.hr.models import GrantScope, HrAction, TrustedAccess
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService, _fingerprint
from csa.hr.workflows import LeaveInput, PayrollInput

_PEOPLE_OPERATIONS = (PersonAdd, PersonUpdate, DepartmentSave)
# Database refusals of hr_people_apply and account.link (the RAISE message), and the HR policy
# code each one becomes.
_PEOPLE_REFUSALS = {
    "hr_people_manager_required": "permission_denied",
    "hr_owner_required": "permission_denied",
    "hr_employee_unavailable": "resource_unavailable",
    "hr_employee_exists": "employee_exists",
    "hr_person_changed": "person_changed",
    "hr_manager_unavailable": "manager_unavailable",
    "hr_manager_loop": "manager_loop",
    "hr_department_unavailable": "department_unavailable",
    "hr_department_archived": "department_archived",
    "hr_department_changed": "department_changed",
    "hr_department_name_taken": "department_name_taken",
    "hr_account_taken": "account_taken",
    "hr_member_unavailable": "member_unavailable",
}


class HrOperations:
    def __init__(self, core: HrService):
        self.core = core

    @staticmethod
    def fingerprint(body: HrOperation) -> str:
        payload = body.model_dump(mode="json")
        if isinstance(body, CalendarSave):
            payload["working_weekdays"] = sorted(payload["working_weekdays"])
            payload["holidays"] = sorted(payload["holidays"])
        if isinstance(body, AccessSave):
            for grant in payload["grants"]:
                grant["fields"] = sorted(grant["fields"])
            payload["grants"] = sorted(
                payload["grants"], key=lambda row: (row["action"], row["scope"])
            )
            payload["workflows"] = sorted(
                payload["workflows"], key=lambda row: (row["domain"], row["action"], row["scope"])
            )
        return _fingerprint(body.operation, payload)

    async def private(self, conn: AsyncConnection, session: HrSession, area: str) -> None:
        row = await q.fetch_one("hr/private_access.sql", (session.tenant_id, area), conn=conn)
        if not row or not row["allowed"]:
            raise HrPolicyError("permission_denied")

    @staticmethod
    def owner(access: TrustedAccess) -> None:
        if not access.client_owner:
            raise HrPolicyError("permission_denied")

    @staticmethod
    def people_manager(access: TrustedAccess) -> None:
        """The HR owner, or a holder of the Manage people switch. hr_people_apply checks again."""
        if not access.client_owner and not any(
            grant.action == HrAction.PEOPLE_MANAGE and grant.scope == GrantScope.HR
            for grant in access.grants
        ):
            raise HrPolicyError("permission_denied")

    async def _authorize(
        self, conn: AsyncConnection, session: HrSession, body: HrOperation, access: TrustedAccess
    ) -> None:
        if isinstance(
            body,
            (
                EmployeeSave,
                AccessSave,
                PrivateAccessSave,
                CalendarSave,
                LeaveBalanceSave,
                AutomationConfigure,
                AccountLink,
            ),
        ):
            self.owner(access)
        elif isinstance(body, _PEOPLE_OPERATIONS):
            self.people_manager(access)
        elif isinstance(body, (CandidateIntake, CandidateNote, CandidateErase, InterviewSchedule)):
            await self.private(conn, session, "recruitment")
        elif isinstance(body, (PayrollRules, PayrollSnapshot, PayrollReverse)):
            await self.private(conn, session, "payroll")
            from csa.hr.cases import HrWorkflowService

            await HrWorkflowService(self.core)._permit(
                conn,
                session,
                "payroll",
                "prepare" if isinstance(body, PayrollSnapshot) else "decide",
                None,
            )
        elif isinstance(body, ScheduleCancel):
            await self.private(
                conn, session, "attendance" if body.operation == "shift.cancel" else "recruitment"
            )
        elif isinstance(body, (AttendanceImport, ShiftSchedule)):
            await self.private(conn, session, "attendance")

    async def apply(self, session: HrSession, body: HrOperation, key: UUID) -> OperationReceipt:
        if session.csrf_hash is None or (
            session.capability is not None
            and not (session.capability == "hr_recruitment" and isinstance(body, InterviewSchedule))
        ):
            raise HrPolicyError("human_operation_required")
        fingerprint = self.fingerprint(body)
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            access = await self.core._access(conn, session)
            await self._authorize(conn, session, body, access)
            # One bounded HR operation at a time per tenant; locks are also shared
            # with leave/payroll approvals and private retention maintenance.
            await q.execute("hr/key_lock.sql", (f"{session.tenant_id}:hr-operations",), conn=conn)
            old = await q.fetch_one(
                "hr/op_get.sql", (session.tenant_id, session.subject_id, key), conn=conn
            )
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise HrPolicyError("idempotency_conflict")
                await self.core._session(conn, session)
                return OperationReceipt.model_validate(old["receipt"]).model_copy(
                    update={"duplicate": True}
                )
            if isinstance(body, (*_PEOPLE_OPERATIONS, AccountLink)):
                path = (
                    "hr/admin_apply.sql" if isinstance(body, AccountLink) else "hr/people_apply.sql"
                )
                row = await self._people_apply(
                    conn,
                    path,
                    (session.tenant_id, key, fingerprint, Jsonb(body.model_dump(mode="json"))),
                )
                receipt = OperationReceipt.model_validate(row["receipt"])
            elif isinstance(body, (EmployeeSave, AccessSave, PrivateAccessSave)):
                row = await q.fetch_one(
                    "hr/admin_apply.sql",
                    (session.tenant_id, key, fingerprint, Jsonb(body.model_dump(mode="json"))),
                    conn=conn,
                )
                receipt = OperationReceipt.model_validate(row["receipt"])
            else:
                receipt = await self._mutate(conn, session, body)
                await q.execute(
                    "hr/op_record.sql",
                    (
                        session.tenant_id,
                        session.subject_id,
                        key,
                        body.operation,
                        fingerprint,
                        Jsonb(receipt.model_dump(mode="json")),
                    ),
                    conn=conn,
                )
            if isinstance(body, (CalendarSave, LeaveBalanceSave, AutomationConfigure)):
                await q.execute(
                    "hr/authority_event.sql",
                    (
                        session.tenant_id,
                        session.subject_id,
                        key,
                        body.operation,
                        Jsonb(body.model_dump(mode="json")),
                    ),
                    conn=conn,
                )
            await self.core._session(conn, session)
        return receipt

    @staticmethod
    async def _people_apply(
        conn: AsyncConnection, path: str, params: tuple[Any, ...]
    ) -> dict[str, Any]:
        """Run a people write; its database refusals become HR policy codes."""
        try:
            return await q.fetch_one(path, params, conn=conn)
        except (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege) as exc:
            code = _PEOPLE_REFUSALS.get(exc.diag.message_primary or "")
            if code is None:
                raise
            raise HrPolicyError(code) from None

    async def _mutate(
        self, conn: AsyncConnection, session: HrSession, body: HrOperation
    ) -> OperationReceipt:
        tenant, actor, stamp = session.tenant_id, session.subject_id, self.core.now()
        resource, version, status = uuid4(), 0, "saved"
        if isinstance(body, AutomationConfigure):
            await q.execute(
                "hr/automation_configure.sql",
                (tenant, body.enabled, body.remind_after_hours, actor),
                conn=conn,
            )
            resource, status = tenant, "enabled" if body.enabled else "disabled"
        elif isinstance(body, ScheduleCancel):
            row = await q.fetch_one(
                "hr/shift_cancel.sql"
                if body.operation == "shift.cancel"
                else "hr/interview_cancel.sql",
                (body.reason, tenant, body.schedule_id),
                conn=conn,
            )
            if row is None:
                raise HrPolicyError("schedule_unavailable")
            resource, status = body.schedule_id, "cancelled_locally"
        elif isinstance(body, CalendarSave):
            old = await q.fetch_one(
                "hr/calendar_get.sql", (tenant, body.region, body.year), conn=conn
            )
            self._version(old, body.expected_version)
            row = await q.fetch_one(
                "hr/calendar_save.sql",
                (tenant, body.region, body.year, Jsonb(body.model_dump(mode="json"))),
                conn=conn,
            )
            resource, version = uuid5(tenant, f"calendar:{body.region}:{body.year}"), row["version"]
        elif isinstance(body, LeaveBalanceSave):
            args = (tenant, body.employee_id, body.year, body.leave_type)
            old = await q.fetch_one("hr/balance_get.sql", args, conn=conn)
            self._version(old, body.expected_version)
            used = await q.fetch_one("hr/balance_used.sql", args, conn=conn)
            if body.days < used["used"]:
                raise HrPolicyError("balance_below_approved_usage")
            row = await q.fetch_one(
                "hr/balance_save.sql", (*args, body.days, body.policy_reference), conn=conn
            )
            resource, version = body.employee_id, row["version"]
        elif isinstance(body, CandidateIntake):
            if not stamp < body.retain_until <= stamp + timedelta(days=366):
                raise HrPolicyError("candidate_retention_invalid")
            await q.execute(
                "hr/candidate_intake.sql",
                (
                    tenant,
                    resource,
                    uuid4(),
                    body.display_name,
                    body.cv_text,
                    body.consent_evidence,
                    actor,
                    body.retain_until,
                ),
                conn=conn,
            )
            status = "consent_recorded"
        elif isinstance(body, CandidateNote):
            row = await q.fetch_one(
                "hr/candidate_note.sql",
                (
                    Jsonb([{"by": str(actor), "note": body.note, "at": stamp.isoformat()}]),
                    tenant,
                    body.candidate_id,
                    body.expected_version,
                    stamp,
                ),
                conn=conn,
            )
            resource, version = body.candidate_id, self._updated(row)
        elif isinstance(body, CandidateErase):
            row = await q.fetch_one(
                "hr/candidate_erase.sql",
                (stamp, tenant, body.candidate_id, body.expected_version),
                conn=conn,
            )
            resource, version, status = body.candidate_id, self._updated(row), "erased"
            await q.execute("hr/interview_cancel_erased.sql", (tenant,), conn=conn)
        elif isinstance(body, InterviewSchedule):
            candidate = await q.fetch_one(
                "hr/candidate_get.sql", (tenant, body.candidate_id, stamp), conn=conn
            )
            if (
                candidate is None
                or body.starts_at <= stamp
                or body.ends_at > candidate["retain_until"]
            ):
                raise HrPolicyError("candidate_or_interview_unavailable")
            available = await q.fetch_one(
                "hr/interviewer_available.sql", (tenant, body.interviewer_id), conn=conn
            )
            if not available or not available["allowed"]:
                raise HrPolicyError("resource_unavailable")
            conflict = await q.fetch_one(
                "hr/interview_conflict.sql",
                (tenant, body.interviewer_id, body.candidate_id, body.ends_at, body.starts_at),
                conn=conn,
            )
            if conflict["conflict"]:
                raise HrPolicyError("interview_overlap")
            await q.execute(
                "hr/interview_save.sql",
                (
                    tenant,
                    resource,
                    body.candidate_id,
                    body.interviewer_id,
                    body.starts_at,
                    body.ends_at,
                    body.timezone,
                ),
                conn=conn,
            )
            status = "scheduled_locally"
        elif isinstance(body, ShiftSchedule):
            available = await q.fetch_one(
                "hr/import_employee.sql", (tenant, body.employee_id), conn=conn
            )
            if not available["allowed"]:
                raise HrPolicyError("resource_unavailable")
            conflict = await q.fetch_one(
                "hr/shift_conflict.sql",
                (tenant, body.employee_id, body.ends_at, body.starts_at),
                conn=conn,
            )
            if conflict["conflict"]:
                raise HrPolicyError("shift_overlap")
            await q.execute(
                "hr/shift_save.sql",
                (tenant, resource, body.employee_id, body.starts_at, body.ends_at),
                conn=conn,
            )
        elif isinstance(body, AttendanceImport):
            from csa.hr.cases import HrWorkflowService

            available = await q.fetch_one(
                "hr/import_employee.sql", (tenant, body.employee_id), conn=conn
            )
            if not available["allowed"]:
                raise HrPolicyError("resource_unavailable")
            cases = HrWorkflowService(self.core)
            for index, content in enumerate(body.records):
                await cases._prepare_in(
                    conn,
                    session,
                    content,
                    uuid5(resource, str(index)),
                    employee_override=body.employee_id,
                )
            status = "imported_pending_review"
        elif isinstance(body, PayrollRules):
            await q.execute(
                "hr/rule_save.sql",
                (tenant, body.rule_id, Jsonb(body.model_dump(mode="json")), actor),
                conn=conn,
            )
            resource, status = body.rule_id, "reviewed_configuration_recorded"
        elif isinstance(body, PayrollSnapshot):
            from csa.hr.cases import HrWorkflowService

            case = await HrWorkflowService(self.core)._get(conn, session, body.case_id, lock=True)
            if (
                not isinstance(case.payload, PayrollInput)
                or case.status != "pending"
                or case.created_by != actor
            ):
                raise HrPolicyError("payroll_preparer_required")
            rules = await self._rules(conn, session, case.payload)
            output = []
            for line in body.lines:
                matched = await q.fetch_one(
                    "hr/employment_matches.sql",
                    (
                        tenant,
                        line.employee_id,
                        rules.region,
                        rules.legal_entity_reference,
                        rules.employment_category,
                    ),
                    conn=conn,
                )
                if not matched["allowed"]:
                    raise HrPolicyError("employment_rule_mismatch")
                try:
                    amounts = line.calculate(rules)
                except ValueError as exc:
                    raise HrPolicyError("reviewed_payroll_amounts_invalid") from exc
                output.append(
                    {
                        "employee_id": str(line.employee_id),
                        "amounts": amounts.model_dump(mode="json"),
                        "net": str(amounts.net),
                        "tax_review_reference": line.tax_review_reference,
                    }
                )
            payload = {
                "lines": output,
                "rule_id": str(rules.rule_id),
                "currency": "MYR",
                "net_total": str(sum((Decimal(line["net"]) for line in output), Decimal(0))),
            }
            digest = _fingerprint("payroll.snapshot", payload)
            await q.execute(
                "hr/snapshot_save.sql",
                (tenant, body.case_id, Jsonb(payload), digest, actor),
                conn=conn,
            )
            resource, status = body.case_id, "prepared_for_review"
        elif isinstance(body, PayrollReverse):
            snapshot = await q.fetch_one("hr/snapshot_get.sql", (tenant, body.case_id), conn=conn)
            if snapshot is None or snapshot["prepared_by"] == actor:
                raise HrPolicyError("distinct_payroll_reviewer_required")
            row = await q.fetch_one(
                "hr/snapshot_reverse.sql",
                (body.reason, tenant, body.case_id, body.expected_version),
                conn=conn,
            )
            resource, version, status = body.case_id, self._updated(row), "reversed_locally"
        elif isinstance(body, AutomationRun):
            private = await q.fetch_one("hr/private_access.sql", (tenant, "recruitment"), conn=conn)
            if private["allowed"]:
                await q.fetch_all("hr/candidate_expire.sql", (stamp, tenant, stamp), conn=conn)
            await q.execute(
                "hr/notification_workflows.sql",
                (
                    actor,
                    stamp.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date().isoformat(),
                    tenant,
                    stamp - timedelta(hours=body.remind_after_hours),
                ),
                conn=conn,
            )
            if private["allowed"]:
                await q.execute("hr/interview_cancel_erased.sql", (tenant,), conn=conn)
            await q.execute(
                "hr/notification_training.sql",
                (
                    actor,
                    stamp.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date().isoformat(),
                    tenant,
                    stamp - timedelta(hours=body.remind_after_hours),
                ),
                conn=conn,
            )
            await q.execute(
                "hr/notification_interviews.sql",
                (actor, tenant, actor, stamp, stamp + timedelta(days=1)),
                conn=conn,
            )
            status = "local_maintenance_completed"
        elif isinstance(body, NotificationRead):
            row = await q.fetch_one(
                "hr/notification_read.sql", (stamp, tenant, body.notification_id), conn=conn
            )
            if row is None:
                raise HrPolicyError("resource_unavailable")
            resource = body.notification_id
        else:
            raise HrPolicyError("unsupported_operation")
        return OperationReceipt(
            operation=body.operation, resource_id=resource, version=version, status=status
        )

    @staticmethod
    def _version(old: dict | None, expected: int | None) -> None:
        if (old is None and expected is not None) or (
            old is not None and old["version"] != expected
        ):
            raise HrPolicyError("stale_version")

    @staticmethod
    def _updated(row: dict | None) -> int:
        if row is None:
            raise HrPolicyError("stale_version")
        return row["version"]

    async def _rules(
        self, conn: AsyncConnection, session: HrSession, period: PayrollInput
    ) -> PayrollRules:
        row = await q.fetch_one(
            "hr/rule_get.sql", (session.tenant_id, period.rule_set_reference), conn=conn
        )
        if row is None:
            raise HrPolicyError("reviewed_payroll_rules_required")
        rules = PayrollRules.model_validate(row["payload"])
        if (rules.region, rules.legal_entity_reference, rules.cycle) != (
            period.region,
            period.legal_entity_reference,
            period.cycle,
        ) or not (
            rules.effective_from <= period.starts_on <= period.ends_on <= rules.effective_until
        ):
            raise HrPolicyError("payroll_rule_period_mismatch")
        return rules

    async def approve_leave(
        self,
        conn: AsyncConnection,
        session: HrSession,
        case_id: UUID,
        employee_id: UUID,
        content: LeaveInput,
    ) -> None:
        await q.execute("hr/key_lock.sql", (f"{session.tenant_id}:hr-operations",), conn=conn)
        conflict = await q.fetch_one(
            "hr/leave_overlap.sql",
            (session.tenant_id, employee_id, content.ends_on, content.starts_on),
            conn=conn,
        )
        if conflict["conflict"]:
            raise HrPolicyError("leave_overlap")
        for year in range(content.starts_on.year, content.ends_on.year + 1):
            calendar = await q.fetch_one(
                "hr/calendar_get.sql", (session.tenant_id, content.region, year), conn=conn
            )
            if calendar is None:
                raise HrPolicyError("reviewed_calendar_required")
            days = leave_days(
                max(content.starts_on, date(year, 1, 1)),
                min(content.ends_on, date(year, 12, 31)),
                CalendarSave.model_validate(calendar["payload"]),
            )
            args = (session.tenant_id, employee_id, year, content.leave_type)
            balance = await q.fetch_one("hr/balance_get.sql", args, conn=conn)
            used = await q.fetch_one("hr/balance_used.sql", args, conn=conn)
            if balance is None or balance["days"] - used["used"] < days:
                raise HrPolicyError("insufficient_reviewed_leave_balance")
            await q.execute(
                "hr/leave_usage.sql",
                (session.tenant_id, case_id, employee_id, year, content.leave_type, days),
                conn=conn,
            )

    async def approve_payroll(
        self,
        conn: AsyncConnection,
        session: HrSession,
        case_id: UUID,
        period: PayrollInput,
        digest: str | None,
    ) -> None:
        await self.private(conn, session, "payroll")
        await q.execute("hr/key_lock.sql", (f"{session.tenant_id}:hr-operations",), conn=conn)
        await self._rules(conn, session, period)
        snapshot = await q.fetch_one("hr/snapshot_get.sql", (session.tenant_id, case_id), conn=conn)
        if snapshot is None or digest != snapshot["digest"]:
            raise HrPolicyError("review_exact_payroll_snapshot_required")
        rules = await self._rules(conn, session, period)
        from csa.hr.operation_records import CalculatedPayroll

        calculated = CalculatedPayroll.model_validate(snapshot["payload"])
        for line in calculated.lines:
            matched = await q.fetch_one(
                "hr/employment_matches.sql",
                (
                    session.tenant_id,
                    line.employee_id,
                    rules.region,
                    rules.legal_entity_reference,
                    rules.employment_category,
                ),
                conn=conn,
            )
            if not matched["allowed"]:
                raise HrPolicyError("employment_rule_mismatch")
        if snapshot["prepared_by"] == session.subject_id:
            raise HrPolicyError("distinct_payroll_reviewer_required")
        conflict = await q.fetch_one(
            "hr/payroll_period_conflict.sql",
            (
                session.tenant_id,
                case_id,
                period.legal_entity_reference,
                period.region,
                period.ends_on,
                period.starts_on,
            ),
            conn=conn,
        )
        if conflict["conflict"]:
            raise HrPolicyError("payroll_period_locked")
        row = await q.fetch_one(
            "hr/snapshot_approve.sql", (session.subject_id, session.tenant_id, case_id), conn=conn
        )
        self._updated(row)

    async def read(
        self, session: HrSession, area: str, resource_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            access = await self.core._access(conn, session)
            if area == "authority_audit":
                self.owner(access)
                rows = await q.fetch_all(
                    "hr/authority_event_page.sql", (session.tenant_id,), conn=conn
                )
                await self.core._session(conn, session)
                return rows
            if area == "members":
                self.owner(access)
                rows = await q.fetch_all("hr/admin_members.sql", (session.tenant_id,), conn=conn)
                await self.core._session(conn, session)
                return rows
            if area == "employees":
                self.owner(access)
                rows = await q.fetch_all("hr/admin_employees.sql", (session.tenant_id,), conn=conn)
                await self.core._session(conn, session)
                return [r["value"] for r in rows]
            if area in {"candidates", "interviews"}:
                await self.private(conn, session, "recruitment")
            if area in {"payroll_rules", "payroll_snapshot"}:
                await self.private(conn, session, "payroll")
                from csa.hr.cases import HrWorkflowService

                await HrWorkflowService(self.core)._permit(conn, session, "payroll", "read", None)
            if area == "shifts":
                await self.private(conn, session, "attendance")
            if area == "candidate":
                await self.private(conn, session, "recruitment")
                row = await q.fetch_one(
                    "hr/candidate_get.sql",
                    (session.tenant_id, resource_id, self.core.now()),
                    conn=conn,
                )
                if row is None:
                    raise HrPolicyError("resource_unavailable")
                await self.core._session(conn, session)
                return [row]
            if area == "payroll_snapshot":
                row = await q.fetch_one(
                    "hr/snapshot_get.sql", (session.tenant_id, resource_id), conn=conn
                )
                if row is None:
                    raise HrPolicyError("resource_unavailable")
                await self.core._session(conn, session)
                return [row]
            names = {
                "candidates": "candidate_page",
                "interviews": "interview_page",
                "my_interviews": "interview_mine",
                "calendars": "calendar_page",
                "balances": "balance_page",
                "shifts": "shift_page",
                "payroll_rules": "rule_page",
                "notifications": "notification_page",
            }
            if area not in names:
                raise HrPolicyError("unsupported_operation")
            args = (
                (session.tenant_id, self.core.now())
                if area == "candidates"
                else (session.tenant_id,)
            )
            rows = await q.fetch_all(f"hr/{names[area]}.sql", args, conn=conn)
            await self.core._session(conn, session)
        return rows
