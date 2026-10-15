"""Local reads and durable preparation, never provider execution or approval."""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from csa.agents.base import AgentContext, SideEffects
from csa.agents.staged.models import (
    BookingInput,
    DraftModel,
    EmailDraftInput,
    InvoiceDraftInput,
    PreparedRequest,
    ResearchInput,
    SubscriptionInput,
    future_booking,
)
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.tenancy import tx


def result(
    ctx: AgentContext,
    agent: str,
    text: str,
    *,
    code: str | None = None,
    duplicate: bool = False,
) -> AgentResult:
    return AgentResult(
        ok=code is None,
        status="ok" if code is None else "error",
        task_id=ctx.idempotency_key,
        agent=agent,
        data=AgentResultData(
            facts=[] if code else [Fact(id=agent, text=text, source=agent, as_of=ctx.now())]
        ),
        error=AgentError(code=code, category="validation", message=text) if code else None,
        meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0, was_duplicate=duplicate),
    )


async def prepare(
    conn: AsyncConnection[Any],
    ctx: AgentContext,
    agent: str,
    inp: BookingInput | SubscriptionInput | EmailDraftInput | ResearchInput | InvoiceDraftInput,
    *,
    requested_by: str | None = None,
) -> AgentResult:
    if not 1 <= len(ctx.idempotency_key.strip()) <= 256 or "\x00" in ctx.idempotency_key:
        return result(ctx, agent, "A bounded idempotency key is required.", code="invalid_key")
    payload = inp.payload() if isinstance(inp, InvoiceDraftInput) else inp.model_dump(mode="json")
    row = await db.q.fetch_one(
        "agents/prepare_request.sql",
        (
            ctx.client_id,
            ctx.contact_id,
            agent,
            inp.action,
            ctx.idempotency_key,
            Jsonb(payload),
            ctx.trace_id,
            requested_by or str(ctx.contact_id),
        ),
        conn=conn,
        model=PreparedRequest,
    )
    duplicate = row is None
    if row is None:
        row = await db.q.fetch_one(
            "agents/get_prepared_request.sql",
            (ctx.client_id, ctx.contact_id, agent, ctx.idempotency_key),
            conn=conn,
            model=PreparedRequest,
        )
    if row is None:
        raise RuntimeError("prepared_request_missing")
    if row.action != inp.action or row.payload != payload:
        return result(
            ctx,
            agent,
            "Idempotency key already used for different input.",
            code="idempotency_conflict",
        )
    return result(
        ctx,
        agent,
        f"Request {row.id} prepared for staff review only. "
        "NOT executed, approved, booked, sent, fetched, billed or submitted. "
        "Provider execution is not configured.",
        duplicate=duplicate,
    )


async def contact_exists(conn: AsyncConnection[Any], ctx: AgentContext) -> bool:
    return (
        await db.q.fetch_one(
            "agents/preparation_contact.sql", (ctx.client_id, ctx.contact_id), conn=conn
        )
        is not None
    )


class BookingAgent:
    name = "booking_agent"
    Input = BookingInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 10000
    max_retries = 0
    ACTION_RISK: dict[str, RiskClass] = {
        "list": "read",
        "status": "read",
        "request_book": "draft",
        "request_reschedule": "draft",
        "request_cancel": "draft",
    }

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, BookingInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, BookingInput)
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await db.q.fetch_one(
                "agents/preparation_enabled.sql", (ctx.client_id, self.name), conn=conn
            ):
                return result(ctx, self.name, "Booking preparation is disabled.", code="disabled")
            if not await contact_exists(conn, ctx):
                return result(ctx, self.name, "Contact not found.", code="contact_not_found")
            if inp.action == "list":
                rows = await db.q.fetch_all(
                    "agents/booking_list.sql", (ctx.client_id, ctx.contact_id), conn=conn
                )
                return result(ctx, self.name, json.dumps(rows, default=str))
            if inp.booking_id is not None:
                row = await db.q.fetch_one(
                    "agents/booking_get.sql",
                    (ctx.client_id, ctx.contact_id, inp.booking_id),
                    conn=conn,
                )
                if not row:
                    return result(ctx, self.name, "Booking not found.", code="booking_not_found")
                if inp.action == "status":
                    return result(ctx, self.name, json.dumps(row, default=str))
                if row["status"] != "confirmed":
                    return result(
                        ctx, self.name, "Booking is not confirmed.", code="booking_inactive"
                    )
            if not future_booking(inp, ctx.now()):
                return result(
                    ctx, self.name, "Start must be in the future.", code="booking_in_past"
                )
            return await prepare(conn, ctx, self.name, inp)


class SubscriptionAgent:
    name = "subscription_agent"
    Input = SubscriptionInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 10000
    max_retries = 0
    ACTION_RISK: dict[str, RiskClass] = {
        "plans": "read",
        "list": "read",
        "status": "read",
        "request_pause": "draft",
        "request_cancel": "draft",
        "request_change": "draft",
    }

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, SubscriptionInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, SubscriptionInput)
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await db.q.fetch_one(
                "agents/preparation_enabled.sql", (ctx.client_id, self.name), conn=conn
            ):
                return result(
                    ctx, self.name, "Subscription preparation is disabled.", code="disabled"
                )
            if not await contact_exists(conn, ctx):
                return result(ctx, self.name, "Contact not found.", code="contact_not_found")
            if inp.action in {"plans", "list"}:
                path = "subscription_plans" if inp.action == "plans" else "subscription_list"
                params = (
                    (ctx.client_id,) if inp.action == "plans" else (ctx.client_id, ctx.contact_id)
                )
                rows = await db.q.fetch_all(f"agents/{path}.sql", params, conn=conn)
                return result(ctx, self.name, json.dumps(rows, default=str))
            row = await db.q.fetch_one(
                "agents/subscription_get.sql",
                (ctx.client_id, ctx.contact_id, inp.subscription_id),
                conn=conn,
            )
            if not row:
                return result(
                    ctx, self.name, "Subscription not found.", code="subscription_not_found"
                )
            if inp.action == "status":
                return result(ctx, self.name, json.dumps(row, default=str))
            if row["status"] not in {"active", "paused"}:
                return result(
                    ctx, self.name, "Subscription is not active.", code="subscription_inactive"
                )
            if inp.action == "request_change" and not await db.q.fetch_one(
                "agents/subscription_plan.sql", (ctx.client_id, inp.plan_code), conn=conn
            ):
                return result(ctx, self.name, "Active plan not found.", code="plan_not_found")
            return await prepare(conn, ctx, self.name, inp)


class StaffActor(DraftModel):
    """Trusted backend identity, never accepted from agent/model input."""

    client_id: UUID
    actor_id: str = Field(min_length=1, max_length=128)
    role: Literal["platform_admin", "tenant_admin", "tenant_staff", "read_only"]


class StaffPreparationService:
    """Not registered in the customer planner; future RBAC adapters supply the actor."""

    async def prepare(
        self,
        actor: StaffActor,
        ctx: AgentContext,
        inp: EmailDraftInput | ResearchInput | InvoiceDraftInput,
    ) -> AgentResult:
        name = (
            "email_agent"
            if isinstance(inp, EmailDraftInput)
            else "web_research_agent"
            if isinstance(inp, ResearchInput)
            else "invoice_write_agent"
        )
        if (
            actor.client_id != ctx.client_id
            or actor.role == "read_only"
            or not actor.actor_id.strip()
        ):
            return result(ctx, name, "Staff authorization required.", code="forbidden")
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            if not await contact_exists(conn, ctx):
                return result(ctx, name, "Contact not found.", code="contact_not_found")
            if isinstance(inp, EmailDraftInput) and not await db.q.fetch_one(
                "agents/preparation_mailbox.sql",
                (ctx.client_id, inp.mailbox_connection_id),
                conn=conn,
            ):
                return result(ctx, name, "Mailbox not found.", code="mailbox_not_found")
            if (
                isinstance(inp, InvoiceDraftInput)
                and inp.invoice_id
                and not await db.q.fetch_one(
                    "agents/preparation_invoice.sql",
                    (ctx.client_id, ctx.contact_id, inp.invoice_id),
                    conn=conn,
                )
            ):
                return result(ctx, name, "Invoice not found.", code="invoice_not_found")
            return await prepare(conn, ctx, name, inp, requested_by=actor.actor_id)
