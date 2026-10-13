"""Read-only console API (``/console/v1``).

The console backend authenticates its own users and calls this API as a trusted service with a
shared bearer token plus the tenant in ``X-Client-Id``. Tenant isolation is still enforced by RLS.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from csa.console import timeline


class TraceOut(BaseModel):
    trace_id: UUID
    conversation_id: UUID | None
    started_at: datetime
    finished_at: datetime
    steps: int
    had_error: bool
    route: str | None
    intent: str | None
    gate_decision: str | None
    agents: list[str]


class EventOut(BaseModel):
    id: int
    trace_id: UUID
    conversation_id: UUID | None
    node: str
    agent: str | None
    status: str
    duration_ms: int
    summary: dict[str, Any]
    created_at: datetime


def build_router(token: str) -> APIRouter:
    def auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        supplied = (authorization or "").removeprefix("Bearer ")
        if not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="invalid console token")

    def tenant(x_client_id: Annotated[UUID, Header()]) -> UUID:
        return x_client_id

    def pool(request: Request) -> AsyncConnectionPool:
        return request.app.state.pool  # type: ignore[no-any-return]

    router = APIRouter(prefix="/console/v1", dependencies=[Depends(auth)])

    @router.get("/traces", response_model=list[TraceOut])
    async def traces(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        before: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=timeline.MAX_PAGE)] = 50,
    ) -> list[dict[str, Any]]:
        return await timeline.list_traces(db_pool, client_id, before=before, limit=limit)

    @router.get("/events", response_model=list[EventOut])
    async def events(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        trace_id: UUID | None = None,
        after_id: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=timeline.MAX_PAGE)] = 100,
    ) -> list[dict[str, Any]]:
        return await timeline.list_events(
            db_pool, client_id, trace_id=trace_id, after_id=after_id, limit=limit
        )

    return router
