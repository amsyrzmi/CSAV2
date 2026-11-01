"""One HR application service for manual HTTP and future reviewed chat/voice adapters.

Current identity, domain authority, request CAS, receipt and audit share one
transaction. A successfully constructed proposal is never returned as persistence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr.access_switches import (
    SWITCH_IDS,
    AccessRows,
    GrantRow,
    SwitchId,
    WorkflowRow,
    state,
)
from csa.hr.models import (
    DirectoryProjection,
    EmployeeRef,
    GrantScope,
    HrAction,
    TrainingAction,
    TrainingCommandInput,
    TrainingDraftInput,
    TrainingProjection,
    TrainingSnapshot,
    TrainingStatus,
    TrustedAccess,
)
from csa.hr.policy import HrPolicyError, matching_grants, project_directory
from csa.hr.records import (
    AgentSwitch,
    AuthorityRow,
    ConfirmationProjection,
    ConfirmationRow,
    DirectoryPage,
    EmployeeRow,
    HrContext,
    HrGuidance,
    HrSession,
    ReceiptRow,
    RequestRow,
    TrainingPage,
    TrainingReceipt,
)
from csa.hr.training import project_training, propose_draft, propose_transition


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _fingerprint(operation: str, payload: dict[str, object]) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class HrService:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        session_idle_seconds: int,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not 60 <= session_idle_seconds <= 86400:
            raise ValueError("HR requires the configured console session idle limit")
        self.pool = pool
        self.session_idle_seconds = session_idle_seconds
        self.now = now

    async def check_ready(self) -> None:
        async with self.pool.connection() as conn:
            row = await q.fetch_one("hr/readiness.sql", conn=conn)
        if not row or not row["safe_role"] or not row["schema_ready"]:
            raise RuntimeError("HR requires csa_app and the reviewed HR schema installation")

    async def _require_capability(
        self, conn: AsyncConnection, session: HrSession, name: str
    ) -> None:
        row = await q.fetch_one("hr/agent_switch.sql", (session.tenant_id, name), conn=conn)
        if not row or row["enabled"] is not True:
            raise HrPolicyError("agent_disabled")

    async def require_capability(self, session: HrSession, name: str) -> None:
        """Raise ``agent_disabled`` unless the tenant switch is on for this valid session."""
        async with tx(self.pool, str(session.tenant_id)) as conn:
            await self._session(conn, session)
            await self._require_capability(conn, session, name)

    async def context(self, session: HrSession) -> HrContext:
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            areas = tuple(
                row["area"]
                for row in await q.fetch_all(
                    "hr/private_catalog.sql", (session.tenant_id,), conn=conn
                )
            )
            catalog = await q.fetch_all("hr/workflow_catalog.sql", (session.tenant_id,), conn=conn)
            on = state(
                AccessRows(
                    frozenset(
                        GrantRow(
                            grant.action.value,
                            grant.scope.value,
                            tuple(sorted(field.value for field in grant.fields)),
                        )
                        for grant in access.grants
                    ),
                    frozenset(WorkflowRow(r["domain"], r["action"], r["scope"]) for r in catalog),
                    frozenset(areas),
                )
            ).on
            switches: tuple[SwitchId, ...] = tuple(s for s in SWITCH_IDS if s in on)
            result = HrContext(
                private_areas=areas,
                employee=access.employee,
                client_owner=access.client_owner,
                actions=tuple(sorted({grant.action.value for grant in access.grants})),
                switches=switches,
                workflows=tuple(sorted({f"{r['domain']}.{r['action']}" for r in catalog})),
                hr_duties=access.client_owner
                or bool(set(switches) - {"directory", "own_requests"}),
            )
            await self._session(conn, session)
        return result

    async def guidance(self, session: HrSession) -> HrGuidance:
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            # Guidance names the directory and training actions only; people.manage is a
            # switch of the People pages, not something the assistant guides.
            actions = {grant.action for grant in access.grants} - {HrAction.PEOPLE_MANAGE}
            result = HrGuidance(actions=tuple(sorted(actions)), as_of=self.now())
            await self._session(conn, session)
        return result

    async def directory_page(
        self, session: HrSession, after: UUID | None, limit: int
    ) -> DirectoryPage:
        if not 1 <= limit <= 50:
            raise HrPolicyError("invalid_limit")
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            if not any(g.action == HrAction.DIRECTORY_READ for g in access.grants):
                raise HrPolicyError("permission_denied")
            rows = await q.fetch_all(
                "hr/directory_page.sql", (session.tenant_id, after, after, limit + 1), conn=conn
            )
            items = []
            for row in rows[:limit]:
                employee = await self._employee(conn, session, row["id"])
                items.append(project_directory(access, employee.directory(session.tenant_id)))
            result = DirectoryPage(
                items=items,
                next_cursor=items[-1].employee.employee_id if len(rows) > limit else None,
            )
            await self._session(conn, session)
        return result

    async def training_page(
        self, session: HrSession, after: UUID | None, limit: int
    ) -> TrainingPage:
        if not 1 <= limit <= 50:
            raise HrPolicyError("invalid_limit")
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            if not any(g.action == HrAction.TRAINING_READ for g in access.grants):
                raise HrPolicyError("permission_denied")
            rows = await q.fetch_all(
                "hr/training_page.sql",
                (session.tenant_id, after, after, limit + 1),
                conn=conn,
                model=RequestRow,
            )
            items = []
            for row in rows[:limit]:
                employee = await self._employee(conn, session, row.employee_id)
                items.append(
                    project_training(
                        access,
                        employee.directory(session.tenant_id),
                        row.snapshot(session.tenant_id),
                    )
                )
            result = TrainingPage(
                items=items, next_cursor=items[-1].request_id if len(rows) > limit else None
            )
            await self._session(conn, session)
        return result

    async def agent_catalog(self, session: HrSession) -> list[AgentSwitch]:
        async with tx(self.pool, str(session.tenant_id)) as conn:
            await self._access(conn, session)
            result = await q.fetch_all(
                "hr/agent_catalog.sql", (session.tenant_id,), conn=conn, model=AgentSwitch
            )
            await self._session(conn, session)
        return result

    async def configure_agent(self, session: HrSession, switch: AgentSwitch) -> AgentSwitch:
        if session.csrf_hash is None:
            raise HrPolicyError("csrf_required")
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            if not access.client_owner:
                raise HrPolicyError("permission_denied")
            result = await q.fetch_one(
                "hr/agent_configure.sql",
                (switch.enabled, session.tenant_id, switch.name),
                conn=conn,
                model=AgentSwitch,
            )
            if result is None:
                raise HrPolicyError("resource_unavailable")
            await self._session(conn, session)
        return result

    @staticmethod
    def _confirmation_projection(row: ConfirmationRow) -> ConfirmationProjection:
        return ConfirmationProjection(
            confirmation_id=row.confirmation_id,
            request_id=row.request_id,
            action=row.command.action,
            expected_version=row.command.expected_version,
            expires_at=row.expires_at,
            command=row.command,
            consumed=row.consumed_at is not None,
        )

    async def prepare_confirmation(
        self, session: HrSession, request_id: UUID, command: TrainingCommandInput, key: UUID
    ) -> ConfirmationProjection:
        if session.csrf_hash is None:
            raise HrPolicyError("csrf_required")
        fingerprint = _fingerprint(
            "training.confirmation",
            {"request_id": str(request_id), **command.model_dump(mode="json")},
        )
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            await q.execute(
                "hr/key_lock.sql",
                (f"{session.tenant_id}:confirmation:{session.subject_id}:{key}",),
                conn=conn,
            )
            row = await self._request(conn, session, request_id)
            employee = await self._employee(conn, session, row.employee_id)
            self._scope(access, HrAction.TRAINING_DECIDE, employee)
            existing = await q.fetch_one(
                "hr/confirmation_key.sql",
                (session.tenant_id, session.subject_id, key),
                conn=conn,
                model=ConfirmationRow,
            )
            timestamp = self.now()
            if existing is not None:
                if existing.fingerprint != fingerprint or existing.session_id != session.session_id:
                    raise HrPolicyError("idempotency_conflict")
                if existing.consumed_at is None and timestamp >= existing.expires_at:
                    raise HrPolicyError("confirmation_expired")
                result = self._confirmation_projection(existing)
            else:
                propose_transition(
                    access,
                    employee.directory(session.tenant_id),
                    row.snapshot(session.tenant_id),
                    command,
                    now=self.now,
                )
                confirmation = ConfirmationRow(
                    confirmation_id=uuid4(),
                    session_id=session.session_id,
                    request_id=request_id,
                    command=command,
                    fingerprint=fingerprint,
                    created_at=timestamp,
                    expires_at=timestamp + timedelta(minutes=15),
                    consumed_at=None,
                )
                await q.execute(
                    "hr/confirmation_save.sql",
                    (
                        session.tenant_id,
                        confirmation.confirmation_id,
                        session.subject_id,
                        session.session_id,
                        request_id,
                        key,
                        Jsonb(command.model_dump(mode="json")),
                        fingerprint,
                        timestamp,
                        confirmation.expires_at,
                    ),
                    conn=conn,
                )
                result = self._confirmation_projection(confirmation)
            await self._session(conn, session)
        return result

    async def confirm(self, session: HrSession, confirmation_id: UUID) -> TrainingReceipt:
        if session.csrf_hash is None:
            raise HrPolicyError("csrf_required")
        bound = session.model_copy(update={"capability": "hr_training"})
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, bound)
            row = await q.fetch_one(
                "hr/confirmation_get.sql",
                (session.tenant_id, confirmation_id),
                conn=conn,
                model=ConfirmationRow,
            )
            if row is None or row.session_id != session.session_id:
                raise HrPolicyError("resource_unavailable")
            if row.consumed_at is None and self.now() >= row.expires_at:
                raise HrPolicyError("confirmation_expired")
            result = await self._transition_in(
                conn, bound, access, row.request_id, row.command, row.confirmation_id
            )
            if row.consumed_at is None:
                await q.execute(
                    "hr/confirmation_consume.sql", (session.tenant_id, confirmation_id), conn=conn
                )
        return result

    async def _session(self, conn: AsyncConnection, session: HrSession) -> None:
        row = await q.fetch_one(
            "hr/session.sql",
            (
                session.session_id,
                session.subject_id,
                session.tenant_id,
                session.token_hash.get_secret_value(),
                self.session_idle_seconds,
                session.csrf_hash.get_secret_value() if session.csrf_hash is not None else None,
            ),
            conn=conn,
        )
        if not row or row["valid"] is not True:
            raise HrPolicyError("session_unavailable")

    async def _access(self, conn: AsyncConnection, session: HrSession) -> TrustedAccess:
        await self._session(conn, session)
        if session.assistant:
            await self._require_capability(conn, session, "hr_assistant")
        if session.capability is not None:
            await self._require_capability(conn, session, session.capability)
        row = await q.fetch_one(
            "hr/authority.sql",
            (session.tenant_id, session.subject_id),
            conn=conn,
            model=AuthorityRow,
        )
        if row is None:
            raise HrPolicyError("permission_denied")
        return TrustedAccess(
            tenant_id=session.tenant_id,
            subject_id=session.subject_id,
            employee=EmployeeRef(tenant_id=session.tenant_id, employee_id=row.employee_id)
            if row.employee_id is not None
            else None,
            client_owner=row.client_owner,
            grants=row.grants,
        )

    async def _employee(
        self, conn: AsyncConnection, session: HrSession, employee_id: UUID
    ) -> EmployeeRow:
        row = await q.fetch_one(
            "hr/employee.sql", (session.tenant_id, employee_id), conn=conn, model=EmployeeRow
        )
        if row is None:
            raise HrPolicyError("resource_unavailable")
        return row

    async def _request(
        self, conn: AsyncConnection, session: HrSession, request_id: UUID, *, lock: bool = False
    ) -> RequestRow:
        row = await q.fetch_one(
            "hr/lock_request.sql" if lock else "hr/get_request.sql",
            (session.tenant_id, request_id),
            conn=conn,
            model=RequestRow,
        )
        if row is None:
            raise HrPolicyError("resource_unavailable")
        return row

    async def _key(self, conn: AsyncConnection, session: HrSession, key: UUID) -> ReceiptRow | None:
        await q.execute(
            "hr/key_lock.sql", (f"{session.tenant_id}:{session.subject_id}:{key}",), conn=conn
        )
        return await q.fetch_one(
            "hr/get_receipt.sql",
            (session.tenant_id, session.subject_id, key),
            conn=conn,
            model=ReceiptRow,
        )

    @staticmethod
    def _scope(access: TrustedAccess, action: HrAction, employee: EmployeeRow) -> GrantScope:
        if action == HrAction.TRAINING_DECIDE and employee.manager_active is not True:
            raise HrPolicyError("permission_denied")
        grants = matching_grants(access, action, employee.directory(access.tenant_id))
        return grants[0].scope

    async def _replay(
        self,
        conn: AsyncConnection,
        session: HrSession,
        access: TrustedAccess,
        receipt: ReceiptRow,
        fingerprint: str,
        action: HrAction,
    ) -> TrainingReceipt:
        row = await self._request(conn, session, receipt.request_id)
        employee = await self._employee(conn, session, row.employee_id)
        self._scope(access, action, employee)
        if receipt.fingerprint != fingerprint:
            raise HrPolicyError("idempotency_conflict")
        await self._session(conn, session)
        return TrainingReceipt(
            duplicate=True,
            request=TrainingProjection(
                request_id=row.request_id,
                employee=EmployeeRef(tenant_id=session.tenant_id, employee_id=row.employee_id),
                content=TrainingDraftInput(
                    title=row.title, learning_objective=row.learning_objective
                ),
                status=receipt.status,
                version=receipt.version,
                updated_at=receipt.updated_at,
            ),
        )

    async def _record(
        self,
        conn: AsyncConnection,
        session: HrSession,
        key: UUID,
        fingerprint: str,
        operation: str,
        after: TrainingSnapshot,
        before: TrainingStatus | None,
        scope: GrantScope,
        employee: EmployeeRow,
    ) -> None:
        await q.execute(
            "hr/receipt.sql",
            (
                session.tenant_id,
                session.subject_id,
                key,
                after.request_id,
                fingerprint,
                operation,
                after.status.value,
                after.version,
                after.updated_at,
            ),
            conn=conn,
        )
        await q.execute(
            "hr/audit.sql",
            (
                session.tenant_id,
                after.request_id,
                session.subject_id,
                key,
                operation,
                before.value if before is not None else None,
                after.status.value,
                after.version,
                scope.value,
                employee.manager_id,
                after.updated_at,
            ),
            conn=conn,
        )

    @staticmethod
    def _receipt(after: TrainingSnapshot) -> TrainingReceipt:
        return TrainingReceipt(
            request=TrainingProjection.model_validate(after.model_dump(exclude={"decision"}))
        )

    async def directory(self, session: HrSession, employee_id: UUID) -> DirectoryProjection:
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            employee = await self._employee(conn, session, employee_id)
            result = project_directory(access, employee.directory(session.tenant_id))
            await self._session(conn, session)
        return result

    async def training(self, session: HrSession, request_id: UUID) -> TrainingProjection:
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            initial = await self._request(conn, session, request_id)
            employee = await self._employee(conn, session, initial.employee_id)
            current = await self._request(conn, session, request_id)
            result = project_training(
                access, employee.directory(session.tenant_id), current.snapshot(session.tenant_id)
            )
            await self._session(conn, session)
        return result

    async def save_draft(
        self, session: HrSession, content: TrainingDraftInput, key: UUID
    ) -> TrainingReceipt:
        if session.csrf_hash is None:
            raise HrPolicyError("csrf_required")
        fingerprint = _fingerprint("save_draft", content.model_dump(mode="json"))
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            receipt = await self._key(conn, session, key)
            if receipt is not None:
                result = await self._replay(
                    conn, session, access, receipt, fingerprint, HrAction.TRAINING_DRAFT
                )
            else:
                if access.employee is None:
                    # The caller's own prerequisite, not a lookup of someone else's record.
                    raise HrPolicyError("no_employee_link")
                employee = await self._employee(conn, session, access.employee.employee_id)
                draft = propose_draft(access, employee.directory(session.tenant_id), content)
                scope = self._scope(access, HrAction.TRAINING_DRAFT, employee)
                after = TrainingSnapshot(
                    request_id=uuid4(),
                    employee=draft.employee,
                    content=draft.content,
                    status=TrainingStatus.DRAFT,
                    version=0,
                    updated_at=self.now(),
                )
                await self._session(conn, session)
                await q.execute(
                    "hr/create_request.sql",
                    (
                        session.tenant_id,
                        after.request_id,
                        employee.employee_id,
                        session.subject_id,
                        content.title,
                        content.learning_objective,
                        after.updated_at,
                    ),
                    conn=conn,
                )
                await self._record(
                    conn, session, key, fingerprint, "save_draft", after, None, scope, employee
                )
                result = self._receipt(after)
        return result

    async def transition(
        self, session: HrSession, request_id: UUID, command: TrainingCommandInput, key: UUID
    ) -> TrainingReceipt:
        async with tx(self.pool, str(session.tenant_id)) as conn:
            access = await self._access(conn, session)
            result = await self._transition_in(conn, session, access, request_id, command, key)
        return result

    async def _transition_in(
        self,
        conn: AsyncConnection,
        session: HrSession,
        access: TrustedAccess,
        request_id: UUID,
        command: TrainingCommandInput,
        key: UUID,
    ) -> TrainingReceipt:
        if session.csrf_hash is None:
            raise HrPolicyError("csrf_required")
        fingerprint = _fingerprint(
            command.action.value, {"request_id": str(request_id), **command.model_dump(mode="json")}
        )
        action = {
            TrainingAction.SUBMIT: HrAction.TRAINING_SUBMIT,
            TrainingAction.WITHDRAW: HrAction.TRAINING_WITHDRAW,
            TrainingAction.APPROVE: HrAction.TRAINING_DECIDE,
            TrainingAction.REJECT: HrAction.TRAINING_DECIDE,
        }[command.action]
        receipt = await self._key(conn, session, key)
        if receipt is not None:
            result = await self._replay(conn, session, access, receipt, fingerprint, action)
        else:
            initial = await self._request(conn, session, request_id)
            employee = await self._employee(conn, session, initial.employee_id)
            scope = self._scope(access, action, employee)
            row = await self._request(conn, session, request_id, lock=True)
            proposal = propose_transition(
                access,
                employee.directory(session.tenant_id),
                row.snapshot(session.tenant_id),
                command,
                now=self.now,
            )
            after = proposal.after
            await self._session(conn, session)
            updated = await q.fetch_one(
                "hr/transition.sql",
                (
                    after.status.value,
                    after.version,
                    after.updated_at,
                    after.decision.subject_id if after.decision is not None else None,
                    after.decision.note if after.decision is not None else None,
                    session.tenant_id,
                    request_id,
                    command.expected_version,
                ),
                conn=conn,
                model=RequestRow,
            )
            if updated is None:
                raise HrPolicyError("stale_version")
            await self._record(
                conn,
                session,
                key,
                fingerprint,
                command.action.value,
                after,
                row.status,
                scope,
                employee,
            )
            result = self._receipt(after)
        return result
