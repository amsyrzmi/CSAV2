"""Escalation agent: record the handoff and mute the AI for the conversation (LG05)."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from csa.agents.base import AgentContext, SideEffects
from csa.contracts.facts import AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.notifier import notify

MUTE_FOR = timedelta(hours=24)


class EscalationInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(default="handoff", max_length=500)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"


class EscalationAgent:
    name = "escalation_agent"
    Input = EscalationInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 10000
    max_retries = 1

    def risk(self, inp: BaseModel) -> RiskClass:
        return "commit"

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, EscalationInput)
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            ticket = await db.q.fetch_one(
                "tickets/find_open_escalation.sql",
                (ctx.client_id, ctx.conversation_id),
                conn=conn,
            )
            if ticket is None:
                ticket = await db.q.fetch_one(
                    "tickets/create.sql",
                    (
                        ctx.client_id,
                        ctx.conversation_id,
                        ctx.contact_id,
                        f"Escalation: {inp.reason}"[:200],
                        "escalation",
                        inp.priority,
                        {"urgent": 1, "high": 4, "normal": 24, "low": 72}[inp.priority],
                    ),
                    conn=conn,
                )
            await db.q.fetch_one(
                "turn/insert_escalation.sql",
                (
                    ctx.client_id,
                    ctx.trace_id,
                    ctx.conversation_id,
                    ctx.contact_id,
                    inp.reason,
                    inp.priority,
                ),
                conn=conn,
            )
            await db.q.fetch_one(
                "escalation/mute_conversation.sql",
                (ctx.now() + MUTE_FOR, ctx.conversation_id, ctx.client_id),
                conn=conn,
            )
            await notify(
                conn,
                client_id=ctx.client_id,
                event="escalation",
                priority=inp.priority,
                payload={
                    "conversation_id": str(ctx.conversation_id),
                    "contact_id": str(ctx.contact_id),
                    "ticket_id": ticket["id"] if ticket else None,
                    "reason": inp.reason,
                },
                dedupe_key=str(ctx.trace_id),
            )
        text = (
            f"Escalated to a human, ticket #{ticket['id']}." if ticket else "Escalated to a human."
        )
        facts = [Fact(id="escalated", text=text, source=self.name)]
        return AgentResult(
            ok=True,
            status="ok",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(facts=facts),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
        )
