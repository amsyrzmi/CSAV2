"""Authenticated, owner-private workspace assistant surface."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError

from csa.console.access import tenant
from csa.console.business import business_actor, require_business_read
from csa.console.contracts import ConsoleActor
from csa.core.tenancy import tx
from csa.workspace.contracts import (
    CreateWorkspaceThread,
    WorkspaceThreadOut,
    WorkspaceThreadPage,
    WorkspaceTurnInput,
    WorkspaceTurnOut,
    WorkspaceTurnPage,
)
from csa.workspace.service import (
    WorkspaceConflict,
    WorkspaceNotFound,
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
        try:
            async with tx(request.app.state.pool, str(client_id)) as conn:
                result = await create_turn(conn, actor, client_id, thread_id, payload)
        except WorkspaceNotFound as exc:
            raise HTTPException(404, "workspace conversation or resource not found") from exc
        except WorkspaceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
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

    return router
