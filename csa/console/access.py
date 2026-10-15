"""Separate human sessions from tenant-allowlisted, read-only machine access."""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from csa.console.contracts import ConsoleActor, Role
from csa.core import db
from csa.core.tenancy import tx


class MachineActor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = "console_machine"
    allowed_clients: frozenset[UUID]


async def authenticate(
    request: Request, token: str | None, allowed_clients: frozenset[UUID]
) -> ConsoleActor | MachineActor:
    authorization = request.headers.get("authorization")
    if authorization:
        # Never let a cookie-authenticated browser select the weaker machine path.
        if request.cookies:
            raise HTTPException(400, "mixed authentication is not allowed")
        supplied = authorization.removeprefix("Bearer ")
        if not token or not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(401, "invalid console token")
        if request.method not in {"GET", "HEAD"}:
            raise HTTPException(403, "machine principal has read-only access")
        actor: ConsoleActor | MachineActor = MachineActor(allowed_clients=allowed_clients)
    else:
        service = getattr(request.app.state, "identity_service", None)
        if service is None:
            raise HTTPException(401, "console session required")
        human: ConsoleActor = await service.actor_for_request(request)
        if any(
            name in request.headers
            for name in ("x-client-id", "x-actor-id", "x-actor-role", "x-user-id", "x-role")
        ):
            raise HTTPException(400, "browser identity headers are not accepted")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            await service.require_csrf(request, human)
            if human.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}:
                raise HTTPException(403, "write permission required")
        actor = human
    request.state.console_actor = actor
    return actor


async def tenant(
    request: Request,
    client_id: Annotated[UUID | None, Query()] = None,
    x_client_id: Annotated[UUID | None, Header()] = None,
) -> UUID:
    actor: ConsoleActor | MachineActor = request.state.console_actor
    if isinstance(actor, MachineActor):
        if x_client_id is None:
            raise HTTPException(422, "X-Client-Id is required for machine access")
        if client_id is not None or x_client_id not in actor.allowed_clients:
            raise HTTPException(403, "machine tenant is not authorized")
        return x_client_id
    if actor.platform_admin:
        if client_id is None:
            raise HTTPException(400, "platform admin must select client_id explicitly")
        async with tx(request.app.state.pool, str(client_id)) as conn:
            exists = await db.q.fetch_one("console/client_get.sql", (client_id,), conn=conn)
        if exists is None:
            raise HTTPException(404, "tenant not found")
        return client_id
    if actor.client_id is None:
        raise HTTPException(403, "active tenant membership required")
    if client_id is not None and client_id != actor.client_id:
        raise HTTPException(403, "tenant is not authorized")
    return actor.client_id


def human_admin(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor) or not actor.platform_admin:
        raise HTTPException(403, "platform admin required")
    return actor
