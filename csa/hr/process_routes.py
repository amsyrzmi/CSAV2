"""The HR process routes: templates, processes, a person's processes and tasks (spec 5.6).

Registered by `build_hr_router` on the HR router, so they share its session, CSRF, tenant,
no-store and error handling (`HrRoute`). Writes need CSRF and an `Idempotency-Key`, and a
read-only session's write is refused by the session dependency (403 `read_only_session`).
`POST /templates/preview` is a read with a body: CSRF as a write, no `Idempotency-Key`.
Template reads and the template and process writes are for the HR team; a task action is for the
assignee, the approver or the HR team, as the database decides (0634). The process reads answer
with `response_model_exclude_unset`: a field the caller may not see is absent, an empty one is
`null`.
"""

# No `from __future__ import annotations`: the route signatures name the dependencies passed to
# register_process_routes, which FastAPI must see as objects, not as names to look up in globals.

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from csa.hr.process_models import (
    MyTaskPage,
    PersonProcesses,
    ProcessCancelReceipt,
    ProcessClose,
    ProcessCloseReceipt,
    ProcessDetail,
    ProcessKind,
    ProcessPage,
    ProcessStart,
    ProcessStartReceipt,
    ProcessStatus,
    TaskApprove,
    TaskComplete,
    TaskNote,
    TaskReason,
    TaskReassign,
    TaskReceipt,
    TaskReschedule,
    TaskStatus,
    TemplateCreate,
    TemplateDeactivate,
    TemplateList,
    TemplateOut,
    TemplatePreview,
    TemplatePreviewRequest,
    TemplateReceipt,
    TemplateUpdate,
)
from csa.hr.processes import HrProcesses
from csa.hr.records import HrSession
from csa.hr.service import HrService

__all__ = [
    "PROCESS_QUERY_KEYS",
    "TASK_QUERY_KEYS",
    "TEMPLATE_BODY_LIMIT",
    "register_process_routes",
]

# Extra query keys of GET /processes and GET /tasks/mine (every HR path also takes client_id).
PROCESS_QUERY_KEYS = {"kind", "status", "overdue", "department", "owner", "limit", "after"}
TASK_QUERY_KEYS = {"status", "limit", "after"}
# A template of 40 steps with long descriptions in two languages is well over the HR default.
TEMPLATE_BODY_LIMIT = 262144

RequestKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def register_process_routes(
    router: APIRouter,
    session_dep: Callable[..., Awaitable[HrSession]],
    service_dep: Callable[..., HrService],
) -> None:
    @router.get("/templates", response_model=TemplateList, response_model_exclude_none=False)
    async def templates(
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
    ) -> TemplateList:
        return await HrProcesses(service).templates(session)

    @router.get(
        "/templates/{template_id}", response_model=TemplateOut, response_model_exclude_none=False
    )
    async def template(
        template_id: UUID,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
    ) -> TemplateOut:
        return await HrProcesses(service).template(session, template_id)

    @router.post(
        "/templates",
        response_model=TemplateReceipt,
        response_model_exclude_none=False,
        status_code=201,
    )
    async def create_template(
        body: TemplateCreate,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TemplateReceipt:
        return await HrProcesses(service).create_template(session, body, request_key)

    @router.post(
        "/templates/preview", response_model=TemplatePreview, response_model_exclude_none=False
    )
    async def preview_template(
        body: TemplatePreviewRequest,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
    ) -> TemplatePreview:
        return await HrProcesses(service).preview(session, body)

    @router.put(
        "/templates/{template_id}",
        response_model=TemplateReceipt,
        response_model_exclude_none=False,
    )
    async def update_template(
        template_id: UUID,
        body: TemplateUpdate,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TemplateReceipt:
        return await HrProcesses(service).update_template(session, template_id, body, request_key)

    @router.post(
        "/templates/{template_id}/deactivate",
        response_model=TemplateReceipt,
        response_model_exclude_none=False,
    )
    async def deactivate_template(
        template_id: UUID,
        body: TemplateDeactivate,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TemplateReceipt:
        return await HrProcesses(service).deactivate_template(
            session, template_id, body, request_key
        )

    @router.get("/processes", response_model=ProcessPage, response_model_exclude_none=False)
    async def processes(
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        kind: ProcessKind | None = None,
        status: ProcessStatus | None = None,
        overdue: bool | None = None,
        department: UUID | None = None,
        owner: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        after: UUID | None = None,
    ) -> ProcessPage:
        return await HrProcesses(service).processes(
            session,
            kind=kind,
            status=status,
            overdue=overdue,
            department=department,
            owner=owner,
            limit=limit,
            after=after,
        )

    @router.get(
        "/processes/{process_id}", response_model=ProcessDetail, response_model_exclude_unset=True
    )
    async def process(
        process_id: UUID,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
    ) -> ProcessDetail:
        return await HrProcesses(service).process(session, process_id)

    @router.post(
        "/processes",
        response_model=ProcessStartReceipt,
        response_model_exclude_none=False,
        status_code=201,
    )
    async def start_process(
        body: ProcessStart,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> ProcessStartReceipt:
        return await HrProcesses(service).start(session, body, request_key)

    @router.post(
        "/processes/{process_id}/cancel",
        response_model=ProcessCancelReceipt,
        response_model_exclude_none=False,
    )
    async def cancel_process(
        process_id: UUID,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> ProcessCancelReceipt:
        return await HrProcesses(service).cancel(session, process_id, request_key)

    @router.get(
        "/people/{employee_id}/processes",
        response_model=PersonProcesses,
        response_model_exclude_unset=True,
    )
    async def person_processes(
        employee_id: UUID,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
    ) -> PersonProcesses:
        return await HrProcesses(service).person(session, employee_id)

    @router.post(
        "/processes/{process_id}/close",
        response_model=ProcessCloseReceipt,
        response_model_exclude_none=False,
    )
    async def close_process(
        process_id: UUID,
        body: ProcessClose,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> ProcessCloseReceipt:
        return await HrProcesses(service).close(session, process_id, body, request_key)

    @router.get("/tasks/mine", response_model=MyTaskPage, response_model_exclude_none=False)
    async def my_tasks(
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        status: TaskStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        after: UUID | None = None,
    ) -> MyTaskPage:
        return await HrProcesses(service).my_tasks(session, status=status, limit=limit, after=after)

    @router.post(
        "/tasks/{task_id}/complete", response_model=TaskReceipt, response_model_exclude_none=False
    )
    async def complete_task(
        task_id: UUID,
        body: TaskComplete,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TaskReceipt:
        return await HrProcesses(service).task_action(
            session, "task.complete", task_id, body, request_key
        )

    @router.post(
        "/tasks/{task_id}/skip", response_model=TaskReceipt, response_model_exclude_none=False
    )
    async def skip_task(
        task_id: UUID,
        body: TaskReason,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TaskReceipt:
        return await HrProcesses(service).task_action(
            session, "task.skip", task_id, body, request_key
        )

    @router.post(
        "/tasks/{task_id}/approve", response_model=TaskReceipt, response_model_exclude_none=False
    )
    async def approve_task(
        task_id: UUID,
        body: TaskApprove,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TaskReceipt:
        return await HrProcesses(service).task_action(
            session, "task.approve", task_id, body, request_key
        )

    @router.post(
        "/tasks/{task_id}/send-back", response_model=TaskReceipt, response_model_exclude_none=False
    )
    async def send_back_task(
        task_id: UUID,
        body: TaskReason,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TaskReceipt:
        return await HrProcesses(service).task_action(
            session, "task.send_back", task_id, body, request_key
        )

    @router.post(
        "/tasks/{task_id}/note", response_model=TaskReceipt, response_model_exclude_none=False
    )
    async def note_task(
        task_id: UUID,
        body: TaskNote,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TaskReceipt:
        return await HrProcesses(service).task_action(
            session, "task.note", task_id, body, request_key
        )

    @router.post(
        "/tasks/{task_id}/reschedule",
        response_model=TaskReceipt,
        response_model_exclude_none=False,
    )
    async def reschedule_task(
        task_id: UUID,
        body: TaskReschedule,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TaskReceipt:
        return await HrProcesses(service).task_action(
            session, "task.reschedule", task_id, body, request_key
        )

    @router.post(
        "/tasks/{task_id}/reassign", response_model=TaskReceipt, response_model_exclude_none=False
    )
    async def reassign_task(
        task_id: UUID,
        body: TaskReassign,
        session: Annotated[HrSession, Depends(session_dep)],
        service: Annotated[HrService, Depends(service_dep)],
        request_key: RequestKey,
    ) -> TaskReceipt:
        return await HrProcesses(service).task_action(
            session, "task.reassign", task_id, body, request_key
        )
