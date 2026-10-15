"""Console API (``/console/v1``) for monitoring, capabilities, KB, and invoices (LG13).

The console backend authenticates its own users and calls this API as a trusted service with a
shared bearer token plus the tenant in ``X-Client-Id``. Tenant isolation is enforced by RLS.
"""

from __future__ import annotations

import hmac
import time
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from csa.agents.base import AgentContext
from csa.agents.invoices import generate_invoice_signature
from csa.agents.kb import KbAgent, KbInput
from csa.console import capabilities, timeline
from csa.console.capabilities import CapabilityOut, CapabilityToggleIn, SpecializedTabInfo
from csa.contracts.callbacks import PendingActionStore
from csa.core import db
from csa.core.settings import get_settings
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


class KbIngestJobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    source: str
    status: str
    files: list[Any]
    meta: dict[str, Any]
    requested_by: str | None
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


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


def tenant(x_client_id: Annotated[UUID, Header()]) -> UUID:
    return x_client_id


def pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool  # type: ignore[no-any-return]


def build_router(token: str) -> APIRouter:
    def auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        supplied = (authorization or "").removeprefix("Bearer ")
        if not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="invalid console token")

    router = APIRouter(prefix="/console/v1", dependencies=[Depends(auth)])

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
        name: str,
        toggle: CapabilityToggleIn,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> CapabilityOut:
        cap = await capabilities.toggle_capability(db_pool, client_id, name, toggle)
        if not cap:
            raise HTTPException(status_code=404, detail=f"capability {name} not found")
        return cap

    @router.get("/tabs/specialized", response_model=list[SpecializedTabInfo])
    async def get_specialized_tabs(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> list[SpecializedTabInfo]:
        return await capabilities.get_specialized_tabs(db_pool, client_id)

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

    @router.post("/kb/documents")
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

    @router.delete("/kb/documents/{document_id}")
    async def delete_kb_document(
        document_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> dict[str, Any]:
        agent = KbAgent()
        ctx = AgentContext(
            client_id=client_id,
            contact_id=uuid4(),
            trace_id=uuid4(),
            conversation_id=None,
            idempotency_key=f"kb_del_{uuid4().hex[:12]}",
            channel="web_console",
            pool=db_pool,
            pending_actions=PendingActionStore(db_pool),
        )
        inp = KbInput(action="delete", document_id=document_id)
        res = await agent.run(ctx, inp)
        if not res.ok:
            raise HTTPException(
                status_code=404,
                detail=res.error.message if res.error else "document not found",
            )
        return {"status": "ok", "deleted_id": str(document_id)}

    @router.get("/kb/ingest-jobs", response_model=list[KbIngestJobOut])
    async def list_kb_ingest_jobs(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        async with tx(db_pool, str(client_id)) as conn:
            return await db.q.fetch_all("kb/list_ingest_jobs.sql", (client_id, limit), conn=conn)

    @router.get("/kb/ingest-jobs/{job_id}", response_model=KbIngestJobOut)
    async def get_kb_ingest_job(
        job_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> dict[str, Any]:
        async with tx(db_pool, str(client_id)) as conn:
            row = await db.q.fetch_one("kb/get_ingest_job.sql", (client_id, job_id), conn=conn)
        if row is None:
            raise HTTPException(status_code=404, detail="ingest job not found")
        return row

    @router.get("/kb/gaps", response_model=list[KbGapOut])
    async def list_kb_gaps(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        async with tx(db_pool, str(client_id)) as conn:
            return await db.q.fetch_all(
                "kb/list_gaps.sql",
                (client_id, limit),
                conn=conn,
            )

    @router.post("/kb/gaps/{gap_id}/resolve")
    async def resolve_kb_gap(
        gap_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> dict[str, Any]:
        agent = KbAgent()
        ctx = AgentContext(
            client_id=client_id,
            contact_id=uuid4(),
            trace_id=uuid4(),
            conversation_id=None,
            idempotency_key=f"kb_gap_res_{uuid4().hex[:12]}",
            channel="web_console",
            pool=db_pool,
            pending_actions=PendingActionStore(db_pool),
        )
        inp = KbInput(action="resolve_gap", gap_id=gap_id)
        res = await agent.run(ctx, inp)
        if not res.ok:
            raise HTTPException(
                status_code=404,
                detail=res.error.message if res.error else "gap not found",
            )
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
        try:
            settings = get_settings()
            secret = (
                settings.INVOICE_SIGNING_SECRET.get_secret_value()
                if settings.INVOICE_SIGNING_SECRET
                else settings.CREDENTIALS_KEY.get_secret_value()
            )
            base_url = str(settings.PUBLIC_BASE_URL).rstrip("/")
        except Exception:
            secret = "fallback_dev_signing_key_secret_123"
            base_url = "http://localhost:18080"
        expires_at = int(time.time()) + 900
        sig = generate_invoice_signature(client_id, invoice_id, expires_at, secret)
        download_url = (
            f"{base_url}/api/v1/invoices/{invoice_id}/download"
            f"?client_id={client_id}&expires={expires_at}&sig={sig}"
        )
        return SignedDownloadLinkOut(
            invoice_id=invoice_id,
            download_url=download_url,
            expires_at=expires_at,
        )

    return router
