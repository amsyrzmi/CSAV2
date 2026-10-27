"""Console routes for plans and subscriptions (reads, plan changes, creation)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from csa.console.access import authenticate, tenant
from csa.console.contracts import ConsoleActor, Role
from csa.console.services import package_gate
from csa.core.tenancy import tx
from csa.subscriptions import service
from csa.subscriptions.contracts import (
    PlanIn,
    PlanOut,
    PlanPatch,
    Status,
    SubscriptionCreateIn,
    SubscriptionDetail,
    SubscriptionOut,
    SubscriptionPage,
)

WRITE_ROLES = {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}


async def human_session(request: Request) -> None:
    """Human console sessions only; the machine token never reaches subscriptions."""
    if request.headers.get("authorization"):
        raise HTTPException(403, "human session required")
    await authenticate(request, None, frozenset())


def require_subscriptions_read(actor: ConsoleActor, client_id: UUID) -> None:
    if not actor.platform_admin and actor.client_id != client_id:
        raise HTTPException(403, "subscriptions tenant is not authorized")


def require_subscriptions_write(actor: ConsoleActor, client_id: UUID) -> None:
    if actor.role not in WRITE_ROLES:
        raise HTTPException(403, "subscriptions changes require an administrator or staff role")
    if not actor.platform_admin and actor.client_id != client_id:
        raise HTTPException(403, "subscriptions tenant is not authorized")


def build_subscriptions_router() -> APIRouter:
    router = APIRouter(
        prefix="/console/v1/subscriptions",
        tags=["Subscriptions"],
        dependencies=[Depends(human_session), Depends(package_gate("subscriptions"))],
    )

    def reader(request: Request, client_id: UUID) -> None:
        require_subscriptions_read(request.state.console_actor, client_id)

    def writer(request: Request, client_id: UUID) -> ConsoleActor:
        actor: ConsoleActor = request.state.console_actor
        require_subscriptions_write(actor, client_id)
        return actor

    # `/plans` routes are declared before `/{subscription_id}`, which is also typed UUID.
    @router.get("/plans", response_model=list[PlanOut])
    async def plans(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        include_archived: bool = False,
    ) -> list[PlanOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await service.list_plans(conn, client_id, include_archived=include_archived)

    @router.post("/plans", response_model=PlanOut, status_code=201)
    async def add_plan(
        request: Request, payload: PlanIn, client_id: Annotated[UUID, Depends(tenant)]
    ) -> PlanOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await service.create_plan(conn, client_id, actor, payload)

    @router.patch("/plans/{code}", response_model=PlanOut)
    async def change_plan(
        request: Request,
        code: str,
        payload: PlanPatch,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> PlanOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await service.update_plan(conn, client_id, actor, code, payload)

    @router.get("", response_model=SubscriptionPage)
    async def search(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        q: Annotated[str | None, Query(max_length=100)] = None,
        status: Status | None = None,
        plan: Annotated[str | None, Query(max_length=64)] = None,
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> SubscriptionPage:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await service.search_subscriptions(
                conn, client_id, q=q, status=status, plan=plan, before=before, limit=limit
            )

    @router.post("", response_model=SubscriptionOut, status_code=201)
    async def create(
        request: Request,
        payload: SubscriptionCreateIn,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> SubscriptionOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await service.create_subscription(conn, client_id, actor, payload)

    @router.get("/{subscription_id}", response_model=SubscriptionDetail)
    async def detail(
        request: Request,
        subscription_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> SubscriptionDetail:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await service.get_subscription(conn, client_id, subscription_id)

    return router
