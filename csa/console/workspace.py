"""Authenticated, owner-private workspace assistant surface."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from csa.console.access import tenant
from csa.console.business import business_actor, require_business_read
from csa.console.contracts import ConsoleActor
from csa.console.services import require_package
from csa.core import db
from csa.core.tenancy import tx
from csa.workspace.contracts import (
    CreateWorkspaceThread,
    MyPeopleOut,
    PersonOut,
    ProposalCreate,
    ProposalOut,
    WorkspaceThreadOut,
    WorkspaceThreadPage,
    WorkspaceTurnInput,
    WorkspaceTurnOut,
    WorkspaceTurnPage,
)
from csa.workspace.directory import my_people, search_people, throttle_lookup
from csa.workspace.planner import PlannerError, WorkspacePlanner
from csa.workspace.registry import ProposalActionRefused, ProposalRegistry
from csa.workspace.service import (
    WorkspaceConflict,
    WorkspaceNotFound,
    create_planned_turn,
    create_thread,
    create_turn,
    list_threads,
    list_turns,
)

_MAX_BODY_BYTES = 32768
# Embedded schemas resolve references from the OpenAPI document root.
_TURN_SCHEMA = WorkspaceTurnInput.model_json_schema(
    ref_template=(
        "#/paths/~1console~1v1~1workspace~1conversations~1{{thread_id}}~1turns"
        "/post/requestBody/content/application~1json/schema/$defs/{model}"
    )
)


async def _payload[Payload: BaseModel](request: Request, model: type[Payload]) -> Payload:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            raise HTTPException(413, "workspace request is too large")
    try:
        return model.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "invalid workspace request") from None


def _workspace_access(actor: ConsoleActor, client_id: UUID) -> None:
    require_business_read(actor, client_id)


def workspace_actor(request: Request) -> ConsoleActor:
    return business_actor(request)


def _registry(request: Request) -> ProposalRegistry:
    registry = getattr(request.app.state, "proposal_registry", None)
    if not isinstance(registry, ProposalRegistry):
        raise HTTPException(503, "proposal_registry_unavailable")
    return registry


def _planner(request: Request) -> WorkspacePlanner:
    planner = getattr(request.app.state, "workspace_planner", None)
    if not isinstance(planner, WorkspacePlanner):
        raise HTTPException(503, "planner_unavailable")
    return planner


def build_workspace_router() -> APIRouter:
    router = APIRouter(
        prefix="/workspace",
        dependencies=[Depends(workspace_actor)],
    )

    @router.post(
        "/conversations",
        response_model=WorkspaceThreadOut,
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": CreateWorkspaceThread.model_json_schema()}
                },
            }
        },
    )
    async def create(
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> WorkspaceThreadOut:
        _workspace_access(actor, client_id)
        payload = await _payload(request, CreateWorkspaceThread)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            result = await create_thread(conn, client_id, actor.user_id, payload.request_key)
        if result.duplicate:
            response.status_code = 200
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.get("/conversations", response_model=WorkspaceThreadPage)
    async def conversations(
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
        before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> WorkspaceThreadPage:
        _workspace_access(actor, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            items, cursor = await list_threads(conn, client_id, actor.user_id, before)
        response.headers["Cache-Control"] = "no-store"
        return WorkspaceThreadPage(items=items, next_cursor=cursor)

    @router.get("/people", response_model=list[PersonOut])
    async def people(
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
        q: Annotated[str, Query(min_length=1, max_length=80)],
    ) -> list[PersonOut]:
        _workspace_access(actor, client_id)
        query = q.strip()
        if not query:
            raise HTTPException(422, "invalid workspace request")
        await require_package(request.app.state.pool, client_id, frozenset({"hr"}))
        throttle_lookup(actor.session_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            found = await search_people(conn, client_id, query)
            # Never the query or any name: only how many candidates were returned.
            await db.q.execute(
                "console/audit.sql",
                (
                    actor.user_id,
                    client_id,
                    "workspace.people.lookup",
                    "company_directory",
                    Jsonb({"candidates": len(found)}),
                ),
                conn=conn,
            )
        response.headers["Cache-Control"] = "no-store"
        return found

    @router.get("/people/me", response_model=MyPeopleOut)
    async def people_me(
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> MyPeopleOut:
        _workspace_access(actor, client_id)
        response.headers["Cache-Control"] = "no-store"
        try:
            await require_package(request.app.state.pool, client_id, frozenset({"hr"}))
        except HTTPException as exc:
            if exc.status_code == 403 and exc.detail == "package_inactive":
                return MyPeopleOut(employee_id=None, manager=None, reason="package_inactive")
            raise
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await my_people(conn, client_id, actor.user_id)

    @router.post(
        "/conversations/{thread_id}/turns",
        response_model=WorkspaceTurnOut,
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": _TURN_SCHEMA}},
            }
        },
    )
    async def turn(
        thread_id: UUID,
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> WorkspaceTurnOut:
        _workspace_access(actor, client_id)
        payload = await _payload(request, WorkspaceTurnInput)
        if payload.page_context is not None and payload.page_hint is not None:
            raise HTTPException(422, "invalid workspace request")
        try:
            if payload.page_context is not None:
                # Planner and proposal gates run with no connection held; the service opens
                # its own short transactions.
                result = await create_planned_turn(
                    request,
                    actor,
                    client_id,
                    thread_id,
                    payload,
                    planner=_planner(request),
                    registry=_registry(request),
                )
            else:
                async with tx(request.app.state.pool, str(client_id)) as conn:
                    result = await create_turn(conn, actor, client_id, thread_id, payload)
        except WorkspaceNotFound as exc:
            raise HTTPException(404, "workspace conversation or resource not found") from exc
        except WorkspaceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except PlannerError as exc:
            raise HTTPException(exc.status_code, exc.code) from None
        if result.duplicate:
            response.status_code = 200
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.get(
        "/conversations/{thread_id}/turns",
        response_model=WorkspaceTurnPage,
    )
    async def history(
        thread_id: UUID,
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
        before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> WorkspaceTurnPage:
        _workspace_access(actor, client_id)
        try:
            async with tx(request.app.state.pool, str(client_id)) as conn:
                items, cursor = await list_turns(conn, client_id, actor.user_id, thread_id, before)
        except WorkspaceNotFound as exc:
            raise HTTPException(404, "workspace conversation not found") from exc
        response.headers["Cache-Control"] = "no-store"
        return WorkspaceTurnPage(items=items, next_cursor=cursor)

    @router.post(
        "/proposals",
        response_model=ProposalOut,
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": ProposalCreate.model_json_schema()}},
            }
        },
    )
    async def create_proposal(
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> ProposalOut:
        _workspace_access(actor, client_id)
        body = await _payload(request, ProposalCreate)
        registry = _registry(request)
        # The kind's executor gates the package and validates the payload before any row.
        result = await registry.create(
            request.app.state.pool,
            request,
            actor,
            client_id,
            body.kind,
            dict(body.payload),
            thread_id=body.thread_id,
            origin="tab",
        )
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.get("/proposals/{proposal_id}", response_model=ProposalOut)
    async def get_proposal(
        proposal_id: UUID,
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> ProposalOut:
        _workspace_access(actor, client_id)
        registry = _registry(request)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            result = await registry.get(conn, actor, client_id, proposal_id)
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post("/proposals/{proposal_id}/confirm", response_model=ProposalOut)
    async def confirm_proposal(
        proposal_id: UUID,
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> ProposalOut | JSONResponse:
        _workspace_access(actor, client_id)
        registry = _registry(request)
        # The registry runs its own short transactions (the `expired` write commits before
        # the refusal is raised) and holds none of them while the executor runs.
        try:
            result = await registry.confirm(
                request.app.state.pool, request, actor, client_id, proposal_id
            )
        except ProposalActionRefused as exc:
            # The owning service's body as is: a send refusal carries retry_after or a message.
            headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
            return JSONResponse(exc.content, status_code=exc.status_code, headers=headers)
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post("/proposals/{proposal_id}/cancel", response_model=ProposalOut)
    async def cancel_proposal(
        proposal_id: UUID,
        request: Request,
        response: Response,
        actor: Annotated[ConsoleActor, Depends(workspace_actor)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> ProposalOut:
        _workspace_access(actor, client_id)
        registry = _registry(request)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            result = await registry.cancel(conn, actor, client_id, proposal_id)
        response.headers["Cache-Control"] = "no-store"
        return result

    return router
