"""Minimal audited tenant onboarding; no provider or runtime process startup."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from csa.agents.ecommerce.agent import EcommerceAgent
from csa.agents.escalation.agent import EscalationAgent
from csa.agents.invoices.agent import InvoiceAgent
from csa.agents.kb.agent import KbAgent
from csa.agents.order_approval.agent import OrderApprovalAgent
from csa.agents.orders.agent import OrderAgent
from csa.agents.staged.service import BookingAgent, SubscriptionAgent
from csa.agents.tickets.agent import TicketAgent
from csa.console.access import authenticate, human_admin, tenant
from csa.core import db
from csa.core.tenancy import tx

# Input contracts come from agents directly; importing turn/graph would violate the boundary.
_AGENTS = (
    OrderAgent,
    EscalationAgent,
    TicketAgent,
    OrderApprovalAgent,
    EcommerceAgent,
    KbAgent,
    InvoiceAgent,
    BookingAgent,
    SubscriptionAgent,
)


class ClientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=160)
    timezone: str = Field(default="Asia/Kuala_Lumpur", max_length=64)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be an IANA zone") from exc
        return value


class ClientOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    slug: str
    name: str
    timezone: str
    status: str


async def session(request: Request) -> None:
    await authenticate(request, None, frozenset())


def build_onboarding_router() -> APIRouter:
    router = APIRouter(prefix="/console/v1", dependencies=[Depends(session)])

    @router.get("/admin/clients", response_model=list[ClientOut])
    async def clients(request: Request) -> list[ClientOut]:
        human_admin(request)
        async with request.app.state.pool.connection() as conn:
            rows = await db.q.fetch_all("console/client_list.sql", conn=conn)
        return [ClientOut.model_validate(row) for row in rows]

    @router.post("/admin/clients", response_model=ClientOut, status_code=201)
    async def create_client(body: ClientIn, request: Request) -> ClientOut:
        actor = human_admin(request)
        client_id = uuid4()
        try:
            async with tx(request.app.state.pool, str(client_id)) as conn:
                row = await db.q.fetch_one(
                    "console/client_create.sql",
                    (client_id, body.slug, body.name, body.timezone),
                    conn=conn,
                )
                for agent_class in _AGENTS:
                    agent = agent_class()
                    await db.q.execute(
                        "console/provision_capabilities.sql",
                        (
                            client_id,
                            agent.name,
                            agent.name.removesuffix("_agent").replace("_", " ").title(),
                            "CSAV2 capability; enable deliberately after configuration",
                            f"python:{agent.name}",
                            Jsonb(agent.Input.model_json_schema()),
                        ),
                        conn=conn,
                    )
                await db.q.execute(
                    "console/audit.sql",
                    (
                        actor.user_id,
                        client_id,
                        "tenant.created",
                        str(client_id),
                        Jsonb({"slug": body.slug, "capabilities_enabled": False}),
                    ),
                    conn=conn,
                )
        except UniqueViolation as exc:
            raise HTTPException(409, "tenant slug already exists") from exc
        return ClientOut.model_validate(row)

    @router.get("/profile", response_model=ClientOut)
    async def profile(request: Request, client_id: Annotated[UUID, Depends(tenant)]) -> ClientOut:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one("console/client_get.sql", (client_id,), conn=conn)
        if row is None:
            raise HTTPException(404, "tenant not found")
        return ClientOut.model_validate(row)

    return router
