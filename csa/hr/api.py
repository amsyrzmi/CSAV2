"""Authenticated staff HR API, mounted by the configured gateway.

No workspace union or customer agent context is used. All resources are verified
again by HrService against locked server state inside the persistence transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import SecretStr

from csa.agents.hr import HrAgentDispatcher, HrAgentInput
from csa.console.contracts import ConsoleActor
from csa.console.identity import SESSION_COOKIE, IdentityService
from csa.console.security import digest
from csa.console.services import require_package
from csa.core.tenancy import tx
from csa.decide.completion import CompletionError
from csa.hr.access_contracts import (
    AccessChangeResult,
    AccessChanges,
    AccessEventPage,
    AccessPeoplePage,
)
from csa.hr.access_page import HrAccessPage
from csa.hr.access_switches import SwitchId
from csa.hr.agents import HrAgentDescription, describe_agent
from csa.hr.assistant import HrAssistant, HrChatInput, HrChatPlan, TrainingSubmitIntent
from csa.hr.automation_models import HrOperation, OperationReceipt
from csa.hr.cases import CasePage, HrWorkflowService
from csa.hr.models import (
    DirectoryProjection,
    HrModel,
    TrainingCommandInput,
    TrainingDraftInput,
    TrainingProjection,
)
from csa.hr.operation_records import Records, project_records
from csa.hr.operations import HrOperations
from csa.hr.policy import HrPolicyError
from csa.hr.records import (
    AgentSwitch,
    ConfirmationProjection,
    DirectoryPage,
    HrContext,
    HrGuidance,
    HrSession,
    TrainingPage,
    TrainingReceipt,
)
from csa.hr.service import HrService
from csa.hr.workflows import WorkflowCommand, WorkflowInput, WorkflowRecord
from csa.workspace.contracts import ProposalOut
from csa.workspace.registry import ProposalError, ProposalRegistry

_PROPOSAL_NS = UUID("5f0b8a3e-3c1d-4b57-9a52-7d2f1c6e8b40")
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}
# Request body caps in bytes; every other HR path keeps 8192.
_BODY_LIMITS = {"/console/v1/hr/operations": 262144, "/console/v1/hr/access/changes": 65536}
# Extra query keys per GET path; every HR path also takes client_id.
_QUERY_KEYS = {
    "/console/v1/hr/employees": {"limit", "after"},
    "/console/v1/hr/training-requests": {"limit", "after"},
    "/console/v1/hr/workflows": {"limit", "after"},
    "/console/v1/hr/access/people": {"cursor", "q", "team", "has", "reports_to"},
    "/console/v1/hr/access/recent": {"cursor"},
}


def hr_http_error(exc: HrPolicyError) -> HTTPException:
    """Content-free HTTP form of an HR policy refusal, shared with workspace proposals."""
    code = exc.code
    if code in {"session_unavailable", "csrf_required"}:
        return HTTPException(401 if code == "session_unavailable" else 403, code)
    if code in {
        "permission_denied",
        "cross_tenant_reference",
        "resource_unavailable",
        "request_employee_mismatch",
    }:
        return HTTPException(404, "resource_unavailable")
    return HTTPException(409, code)


def hr_session_from(
    request: Request, actor: ConsoleActor, tenant_id: UUID, *, mutation: bool
) -> HrSession:
    """Session proof for an authenticated actor; HrService revalidates every field.

    Callers have already authenticated the actor and, for mutations, checked CSRF.
    """
    return HrSession(
        session_id=actor.session_id,
        subject_id=actor.user_id,
        tenant_id=tenant_id,
        token_hash=SecretStr(digest(request.cookies.get(SESSION_COOKIE, ""))),
        csrf_hash=SecretStr(digest(request.headers.get("x-csrf-token", ""))) if mutation else None,
    )


class HrRoute(APIRoute):
    """Bound request bytes before parsing and never echo HR content on validation errors."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                data = bytearray()
                maximum = _BODY_LIMITS.get(request.url.path, 8192)
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise HTTPException(413, "hr_request_too_large")
                # Starlette caches body bytes on Request; parsing uses the bounded copy.
                request._body = bytes(data)
                response = await handler(request)
            except RequestValidationError as exc:
                response = JSONResponse(
                    status_code=422,
                    content={"detail": "invalid_hr_input", "invalid_fields": len(exc.errors())},
                )
            except HrPolicyError as exc:
                error = hr_http_error(exc)
                response = JSONResponse(
                    status_code=error.status_code, content={"detail": error.detail}
                )
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
                )
            except (
                psycopg.errors.DeadlockDetected,
                psycopg.errors.SerializationFailure,
                psycopg.errors.LockNotAvailable,
            ):
                response = JSONResponse(status_code=409, content={"detail": "hr_retry_required"})
            except psycopg.Error:
                response = JSONResponse(
                    status_code=503, content={"detail": "hr_database_unavailable"}
                )
            except TimeoutError:
                response = JSONResponse(
                    status_code=503, content={"detail": "hr_agent_timeout_check_saved_state"}
                )
            except CompletionError:
                response = JSONResponse(status_code=503, content={"detail": "hr_model_unavailable"})
            response.headers.update(_NO_STORE)
            return response

        return guarded


async def _session(
    request: Request, client_id: Annotated[UUID | None, Query()] = None
) -> HrSession:
    keys = [key for key, _ in request.query_params.multi_items()]
    allowed = {"client_id"}
    if request.method == "GET":
        allowed |= _QUERY_KEYS.get(request.url.path, set())
    if any(key not in allowed for key in keys) or len(keys) != len(set(keys)):
        raise HTTPException(400, "invalid_hr_query")
    if request.headers.get("authorization"):
        raise HTTPException(401, "human_session_required")
    if any(
        key in request.headers
        for key in ("x-employee-id", "x-manager-id", "x-hr-role", "x-hr-grants", "x-client-owner")
    ):
        raise HTTPException(403, "browser_identity_headers_forbidden")
    identity = getattr(request.app.state, "identity_service", None)
    if not isinstance(identity, IdentityService):
        raise HTTPException(503, "identity_unavailable")
    actor = await identity.actor_for_request(request)
    if actor.platform_admin:
        if client_id is None:
            raise HTTPException(400, "explicit_tenant_required")
        tenant_id = client_id
    else:
        if actor.client_id is None or (client_id is not None and client_id != actor.client_id):
            raise HTTPException(403, "tenant_not_authorized")
        tenant_id = actor.client_id
    await require_package(request.app.state.pool, tenant_id, frozenset({"hr"}))
    mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
    if mutation:
        if actor.role == "read_only":
            raise HTTPException(403, "read_only_session")
        await identity.require_csrf(request, actor)
    return hr_session_from(request, actor, tenant_id, mutation=mutation)


def _service(request: Request) -> HrService:
    service = getattr(request.app.state, "hr_service", None)
    if not isinstance(service, HrService):
        raise HTTPException(503, "hr_service_unavailable")
    return service


Session = Annotated[HrSession, Depends(_session)]
Service = Annotated[HrService, Depends(_service)]
RequestKey = Annotated[UUID, Header(alias="Idempotency-Key")]


class ConfirmationAccept(HrModel):
    """Opaque path reference only; callers cannot replace the prepared command."""


def build_hr_router() -> APIRouter:
    """Requires configured identity_service, hr_service and ordered HR schema."""
    router = APIRouter(prefix="/console/v1/hr", tags=["HR"], route_class=HrRoute)

    @router.get("/guidance", response_model=HrGuidance)
    async def guidance(session: Session, service: Service) -> HrGuidance:
        return await service.guidance(session)

    @router.post(
        "/assistant",
        response_model=HrChatPlan
        | DirectoryProjection
        | TrainingProjection
        | TrainingReceipt
        | ConfirmationProjection
        | WorkflowRecord
        | OperationReceipt
        | ProposalOut,
        response_model_exclude_none=True,
        responses={201: {"model": ProposalOut}},
    )
    async def assistant(
        body: HrChatInput,
        request: Request,
        session: Session,
        request_key: RequestKey,
    ):
        planner = getattr(request.app.state, "hr_assistant", None)
        if not isinstance(planner, HrAssistant):
            raise HTTPException(503, "hr_assistant_not_configured")
        result = await planner.run(session, body, request_key)
        if not isinstance(result, TrainingSubmitIntent):
            return result
        registry = getattr(request.app.state, "proposal_registry", None)
        if not isinstance(registry, ProposalRegistry):
            raise HTTPException(503, "proposal_registry_unavailable")
        actor = await request.app.state.identity_service.actor_for_request(request)
        # Deterministic id: a retry or a concurrent twin of this request resolves to one row.
        proposal_id = uuid5(
            _PROPOSAL_NS, f"{session.tenant_id}:{actor.user_id}:{actor.session_id}:{request_key}"
        )
        if result.replayed:
            async with tx(request.app.state.pool, str(session.tenant_id)) as conn:
                try:
                    existing = await registry.get(conn, actor, session.tenant_id, proposal_id)
                except ProposalError:
                    existing = None
            if existing is not None:
                return JSONResponse(existing.model_dump(mode="json"), status_code=200)
        created = await registry.create(
            request.app.state.pool,
            request,
            actor,
            session.tenant_id,
            "training_submit",
            {"action": "training_submit", "request_id": str(result.request_id)},
            thread_id=None,
            origin="assistant",
            proposal_id=proposal_id,
        )
        return JSONResponse(created.model_dump(mode="json"), status_code=201)

    @router.post("/operations", response_model=OperationReceipt)
    async def operation(
        body: HrOperation, session: Session, service: Service, request_key: RequestKey
    ) -> OperationReceipt:
        return await HrOperations(service).apply(session, body, request_key)

    @router.get("/access/people", response_model=AccessPeoplePage)
    async def access_people(
        session: Session,
        service: Service,
        cursor: Annotated[str | None, Query(max_length=16)] = None,
        q: Annotated[str | None, Query(max_length=120)] = None,
        team: Annotated[str | None, Query(max_length=120)] = None,
        has: SwitchId | None = None,
        reports_to: UUID | None = None,
    ) -> AccessPeoplePage:
        return await HrAccessPage(service).people(
            session, cursor=cursor, q=q, team=team, has=has, reports_to=reports_to
        )

    @router.post("/access/changes", response_model=AccessChangeResult)
    async def access_changes(
        body: AccessChanges, session: Session, service: Service
    ) -> AccessChangeResult:
        return await HrAccessPage(service).changes(session, body)

    @router.get("/access/recent", response_model=AccessEventPage)
    async def access_recent(
        session: Session,
        service: Service,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ) -> AccessEventPage:
        return await HrAccessPage(service).recent(session, cursor=cursor)

    @router.get("/records/{area}", response_model=Records)
    async def records(
        area: Literal[
            "authority_audit",
            "members",
            "employees",
            "candidates",
            "interviews",
            "my_interviews",
            "calendars",
            "balances",
            "shifts",
            "payroll_rules",
            "notifications",
        ],
        session: Session,
        service: Service,
    ) -> Records:
        return project_records(area, await HrOperations(service).read(session, area))

    @router.get("/private/{area}/{resource_id}", response_model=Records)
    async def private_record(
        area: Literal["candidate", "payroll_snapshot"],
        resource_id: UUID,
        session: Session,
        service: Service,
    ) -> Records:
        return project_records(area, await HrOperations(service).read(session, area, resource_id))

    @router.get("/context", response_model=HrContext)
    async def context(session: Session, service: Service) -> HrContext:
        return await service.context(session)

    @router.get("/employees", response_model=DirectoryPage, response_model_exclude_none=True)
    async def directory_page(
        session: Session,
        service: Service,
        after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
    ) -> DirectoryPage:
        return await service.directory_page(session, after, limit)

    @router.get("/training-requests", response_model=TrainingPage)
    async def training_page(
        session: Session,
        service: Service,
        after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
    ) -> TrainingPage:
        return await service.training_page(session, after, limit)

    @router.get("/agents", response_model=list[AgentSwitch])
    async def agents(session: Session, service: Service) -> list[AgentSwitch]:
        return await service.agent_catalog(session)

    @router.get("/agents/contracts", response_model=list[HrAgentDescription])
    async def agent_contracts(session: Session, service: Service) -> list[HrAgentDescription]:
        return [
            describe_agent(row.name, row.enabled) for row in await service.agent_catalog(session)
        ]

    @router.put("/agents", response_model=AgentSwitch)
    async def configure_agent(body: AgentSwitch, session: Session, service: Service) -> AgentSwitch:
        return await service.configure_agent(session, body)

    @router.post(
        "/agents/execute",
        response_model=DirectoryProjection
        | TrainingProjection
        | TrainingReceipt
        | ConfirmationProjection
        | WorkflowRecord
        | OperationReceipt,
        response_model_exclude_none=True,
    )
    async def execute_agent(
        body: HrAgentInput, session: Session, service: Service, request_key: RequestKey
    ):
        return await HrAgentDispatcher(service).execute(session, body, request_key)

    @router.post("/confirmations/{confirmation_id}/confirm", response_model=TrainingReceipt)
    async def confirm(
        confirmation_id: UUID, body: ConfirmationAccept, session: Session, service: Service
    ) -> TrainingReceipt:
        return await service.confirm(session, confirmation_id)

    @router.get("/workflows", response_model=CasePage)
    async def workflow_page(
        session: Session,
        service: Service,
        after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
    ) -> CasePage:
        return await HrWorkflowService(service).page(session, after, limit)

    @router.get("/workflows/{case_id}", response_model=WorkflowRecord)
    async def workflow(case_id: UUID, session: Session, service: Service) -> WorkflowRecord:
        return await HrWorkflowService(service).get(session, case_id)

    @router.post("/workflows", response_model=WorkflowRecord, status_code=201)
    async def prepare_workflow(
        body: WorkflowInput,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> WorkflowRecord:
        return await HrWorkflowService(service).prepare(session, body, request_key)

    @router.post("/workflows/{case_id}/transitions", response_model=WorkflowRecord)
    async def decide_workflow(
        case_id: UUID,
        body: WorkflowCommand,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> WorkflowRecord:
        return await HrWorkflowService(service).decide(session, case_id, body, request_key)

    @router.get(
        "/employees/{employee_id}",
        response_model=DirectoryProjection,
        response_model_exclude_none=True,
    )
    async def directory(
        employee_id: UUID, session: Session, service: Service
    ) -> DirectoryProjection:
        return await service.directory(session, employee_id)

    @router.get("/training-requests/{request_id}", response_model=TrainingProjection)
    async def training(request_id: UUID, session: Session, service: Service) -> TrainingProjection:
        return await service.training(session, request_id)

    @router.post("/training-requests", response_model=TrainingReceipt, status_code=201)
    async def draft(
        body: TrainingDraftInput, session: Session, service: Service, request_key: RequestKey
    ) -> TrainingReceipt:
        return await service.save_draft(session, body, request_key)

    @router.post("/training-requests/{request_id}/transitions", response_model=TrainingReceipt)
    async def transition(
        request_id: UUID,
        body: TrainingCommandInput,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> TrainingReceipt:
        return await service.transition(session, request_id, body, request_key)

    return router
