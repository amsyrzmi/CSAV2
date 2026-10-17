"""Tenant-scoped console reads and audited human capability administration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from csa.agents.base import AgentContext
from csa.agents.kb import KbAgent, KbInput
from csa.console import agents, capabilities, runtime, timeline
from csa.console.access import authenticate, tenant
from csa.console.business import build_business_router
from csa.console.capabilities import CapabilityOut, CapabilityToggleIn, SpecializedTabInfo
from csa.console.chat import build_chat_router
from csa.console.contracts import ConsoleActor, Role
from csa.console.inbox import build_inbox_router
from csa.console.kb_contracts import JobOut, public_job
from csa.console.kb_uploads import audit as kb_audit
from csa.console.kb_uploads import build_upload_router
from csa.console.kb_uploads import writer as kb_writer
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.registry import CapabilityRegistry
from csa.core import db
from csa.core.tenancy import tx


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


class KbDocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    category: str = Field(default="general", max_length=64)
    language: str = Field(default="en", max_length=10)
    source_uri: str | None = Field(default=None, max_length=500)


class KbDocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    source_uri: str | None
    category: str | None
    language: str
    version: int
    is_active: bool
    file_name: str | None = None
    chunk_count: int
    kind: str
    embed_model: str | None = None
    created_at: datetime
    updated_at: datetime


class KbGapOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    query: str
    hits: int
    first_seen: datetime
    last_seen: datetime
    resolved: bool


class InvoiceItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    client_id: UUID
    contact_id: UUID | None
    order_id: str | None
    status: str
    amount: float
    currency: str
    issued_at: datetime
    due_at: datetime | None
    paid_at: datetime | None
    payment_ref: str | None
    contact_email: str | None
    contact_name: str | None


class SignedDownloadLinkOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: str
    download_url: str
    expires_at: int


def pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool  # type: ignore[no-any-return]


def build_router(
    token: str | None = None,
    *,
    machine_client_ids: frozenset[UUID] = frozenset(),
    kb_model: str | None = None,
    kb_tei_configured: bool = False,
    kb_upload_root: FilePath | None = None,
) -> APIRouter:
    async def auth(request: Request) -> None:
        await authenticate(request, token, machine_client_ids)

    router = APIRouter(prefix="/console/v1", dependencies=[Depends(auth)])
    router.include_router(build_upload_router(kb_upload_root))
    router.include_router(build_chat_router())
    router.include_router(build_inbox_router())
    router.include_router(build_business_router())

    async def kb_enabled(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> None:
        if not await CapabilityRegistry(db_pool).allows_execution(client_id, "kb_agent"):
            raise HTTPException(409, "KB capability is missing or disabled")

    @router.get("/agents", response_model=list[agents.AgentOut])
    async def agent_catalog(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> list[agents.AgentOut]:
        actor = request.state.console_actor
        return await agents.list_agents(
            db_pool,
            client_id,
            role=actor.role if isinstance(actor, ConsoleActor) else None,
            kb_model=kb_model,
            kb_tei_configured=kb_tei_configured,
        )

    @router.get("/overview", response_model=runtime.OverviewOut)
    async def runtime_overview(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> runtime.OverviewOut:
        return await runtime.overview(db_pool, client_id)

    @router.get("/runs", response_model=runtime.RunPage)
    async def runs(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        before_id: Annotated[int | None, Query(ge=1, le=runtime.MAX_RUN_ID)] = None,
        agent: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=timeline.MAX_PAGE)] = 50,
    ) -> runtime.RunPage:
        return await runtime.list_runs(
            db_pool, client_id, before_id=before_id, agent=agent, limit=limit
        )

    @router.get("/runs/{run_id}", response_model=runtime.RunOut)
    async def run_detail(
        run_id: Annotated[int, Path(ge=1, le=runtime.MAX_RUN_ID)],
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> runtime.RunOut:
        row = await runtime.get_run(db_pool, client_id, run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        return row

    # Timeline & Traces
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

    # Agent Capabilities & Admin Subscriptions
    @router.get("/capabilities", response_model=list[CapabilityOut])
    async def get_capabilities(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> list[CapabilityOut]:
        return await capabilities.list_capabilities(db_pool, client_id)

    @router.patch("/capabilities/{name}", response_model=CapabilityOut)
    async def update_capability(
        request: Request,
        name: str,
        toggle: CapabilityToggleIn,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> CapabilityOut:
        actor = request.state.console_actor
        if not isinstance(actor, ConsoleActor):
            raise HTTPException(403, "human session required")
        if toggle.is_enabled is not None and not actor.platform_admin:
            raise HTTPException(403, "platform admin required to change subscriptions")
        if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}:
            raise HTTPException(403, "tenant admin required")
        if toggle.is_enabled is None and toggle.client_enabled is None:
            raise HTTPException(422, "at least one capability switch is required")
        if name in {"escalation_agent", "order_approval"} and (
            toggle.is_enabled is False or toggle.client_enabled is False
        ):
            raise HTTPException(409, "required safety capabilities cannot be disabled")
        cap = await capabilities.toggle_capability(
            db_pool, client_id, name, toggle, actor_id=actor.user_id
        )
        if not cap:
            raise HTTPException(status_code=404, detail=f"capability {name} not found")
        return cap

    @router.get("/tabs/specialized", response_model=list[SpecializedTabInfo])
    async def get_specialized_tabs(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> list[SpecializedTabInfo]:
        catalog = await agent_catalog(request, client_id, db_pool)
        return [
            SpecializedTabInfo(
                tab_id={
                    "kb_agent": "knowledge_base",
                    "invoice_agent": "invoices",
                    "booking_agent": "bookings",
                    "ecommerce_agent": "ecommerce",
                    "lhdn_agent": "lhdn_myinvois",
                    "subscription_agent": "subscriptions",
                }[agent.name],
                title=agent.title,
                description=agent.detail,
                agent_name=agent.name,
                is_subscribed=bool(agent.capability and agent.capability.is_enabled),
                is_active=bool(agent.capability and agent.capability.active),
                implementation=agent.implementation,
                readiness=agent.readiness,
                can_view=agent.can_view_specialized,
                can_manage=agent.can_toggle,
            )
            for agent in catalog
            if agent.specialized
        ]

    # Knowledge Base
    @router.get("/kb/documents", response_model=list[KbDocumentOut])
    async def list_kb_documents(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        async with tx(db_pool, str(client_id)) as conn:
            return await db.q.fetch_all(
                "kb/list_documents.sql",
                (client_id, None, None, limit),
                conn=conn,
            )

    @router.post("/kb/documents", dependencies=[Depends(kb_enabled)])
    async def ingest_kb_document(
        doc: KbDocumentIn,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> dict[str, Any]:
        agent = KbAgent()
        ctx = AgentContext(
            client_id=client_id,
            contact_id=uuid4(),
            trace_id=uuid4(),
            conversation_id=None,
            idempotency_key=f"kb_ingest_{uuid4().hex[:12]}",
            channel="web_console",
            pool=db_pool,
            pending_actions=PendingActionStore(db_pool),
        )
        inp = KbInput(
            action="ingest",
            title=doc.title,
            content=doc.content,
            category=doc.category,
            language=doc.language,
            source_uri=doc.source_uri,
        )
        res = await agent.run(ctx, inp)
        if not res.ok:
            raise HTTPException(
                status_code=503 if res.error and res.error.category == "upstream" else 400,
                detail=res.error.message if res.error else "failed to ingest document",
            )
        return {"status": "ok", "facts": [f.text for f in res.data.facts]}

    @router.get("/kb/ingest-jobs", response_model=list[JobOut])
    async def list_kb_ingest_jobs(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[JobOut]:
        async with tx(db_pool, str(client_id)) as conn:
            rows = await db.q.fetch_all("kb/list_ingest_jobs.sql", (client_id, limit), conn=conn)
        return [public_job(row) for row in rows]

    @router.get("/kb/ingest-jobs/{job_id}", response_model=JobOut)
    async def get_kb_ingest_job(
        job_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> JobOut:
        async with tx(db_pool, str(client_id)) as conn:
            row = await db.q.fetch_one("kb/get_ingest_job.sql", (client_id, job_id), conn=conn)
        if row is None:
            raise HTTPException(status_code=404, detail="ingest job not found")
        return public_job(row)

    @router.get("/kb/gaps", response_model=list[KbGapOut])
    async def list_kb_gaps(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        async with tx(db_pool, str(client_id)) as conn:
            return await db.q.fetch_all(
                "kb/list_gaps.sql",
                (client_id, False, False, limit),
                conn=conn,
            )

    @router.post("/kb/gaps/{gap_id}/resolve", dependencies=[Depends(kb_enabled)])
    async def resolve_kb_gap(
        gap_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        actor: Annotated[ConsoleActor, Depends(kb_writer)],
    ) -> dict[str, Any]:
        async with tx(db_pool, str(client_id)) as conn:
            row = await db.q.fetch_one("kb/resolve_gap.sql", (client_id, gap_id), conn=conn)
            if row is None:
                raise HTTPException(404, "gap not found")
            await kb_audit(conn, actor, client_id, "kb.gap_resolved", str(gap_id))
        return {"status": "ok", "resolved_id": str(gap_id)}

    # Invoices
    @router.get("/invoices", response_model=list[InvoiceItemOut])
    async def list_invoices(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        async with tx(db_pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "invoices/list_by_client.sql",
                (client_id, limit),
                conn=conn,
            )
        return [
            {
                "id": r["id"],
                "client_id": r["client_id"],
                "contact_id": r["contact_id"],
                "order_id": r["order_id"],
                "status": r["status"],
                "amount": float(r["amount"]),
                "currency": r["currency"],
                "issued_at": r["issued_at"],
                "due_at": r["due_at"],
                "paid_at": r["paid_at"],
                "payment_ref": r["payment_ref"],
                "contact_email": r["email"],
                "contact_name": r["full_name"],
            }
            for r in rows
        ]

    @router.get("/invoices/{invoice_id}/download-link", response_model=SignedDownloadLinkOut)
    async def get_invoice_download_link(
        invoice_id: str,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> SignedDownloadLinkOut:
        raise HTTPException(501, "invoice artifacts are unavailable until the M6 delivery slice")

    return router
