"""Postgres side effects plus deterministic LG04 deciders for the coordinator.

Persistence (context, emit, finalize, fail-safe, escalation) is real. The
decision providers (guard, signals, plan, compose, verify, gate) are
deterministic placeholders until LG06/LG07 supply model-backed versions, and the
spokes are the LG05 order and escalation agents.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.agents.base import AgentContext
from csa.agents.breaker import CircuitBreaker
from csa.agents.ecommerce.agent import EcommerceAgent
from csa.agents.escalation.agent import EscalationAgent
from csa.agents.invoices.agent import InvoiceAgent
from csa.agents.kb.agent import KbAgent
from csa.agents.order_approval.agent import OrderApprovalAgent
from csa.agents.orders.agent import OrderAgent
from csa.agents.recorder import PostgresBreakerStore, PostgresRunRecorder
from csa.agents.runner import SpokeRunner
from csa.agents.staged.service import BookingAgent, SubscriptionAgent
from csa.agents.tickets.agent import TicketAgent
from csa.approval.service import hold_reply
from csa.contracts.callbacks import CallbackCodec, PendingActionStore
from csa.contracts.envelope import Envelope
from csa.contracts.facts import AgentResult
from csa.contracts.plan import Intent, Plan, RiskClass, Task
from csa.contracts.registry import CapabilityRegistry
from csa.contracts.state import (
    Context,
    Draft,
    GateVerdict,
    GuardResult,
    OutboundMessage,
    Signals,
    TurnState,
    Verification,
)
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx
from csa.decide.completion import JsonCompletionClient
from csa.decide.jev import JevProvider
from csa.decide.llm import ClientBrand, LLMProvider
from csa.decide.providers import DecisionRecord, classify_with
from csa.graph.policy.gate import Policy, evaluate

logger = structlog.get_logger(__name__)

FALLBACK_TEXT = "Thanks for your message. A team member will follow up with you shortly."
HANDOFF_TEXT = "I've passed this to a team member who will get back to you shortly."


CAPABILITIES = {
    "order_agent": "Order status, product search and details, creating order drafts",
    "ticket_agent": "Support tickets: create, look up, list, add a note, cancel, reopen",
    "ecommerce_agent": "E-Commerce operations for TikTok Shop, Shopee, and Lazada",
    "kb_agent": "Knowledge base search and FAQ lookup for business questions and product docs",
    "invoice_agent": "Invoice lookup, status check, and signed download links",
    "booking_agent": "Local booking history and unexecuted requests; never confirms availability",
    "subscription_agent": "Plan/status lookup and unexecuted requests; no billing changes",
}


def build_llm_provider(
    client: JsonCompletionClient,
    *,
    compose_fallback: JsonCompletionClient | None = None,
) -> LLMProvider:
    """Wire the planner to the plannable agents' own input models."""
    agents = {
        "order_agent": OrderAgent(),
        "ticket_agent": TicketAgent(),
        "ecommerce_agent": EcommerceAgent(),
        "kb_agent": KbAgent(),
        "invoice_agent": InvoiceAgent(),
        "booking_agent": BookingAgent(),
        "subscription_agent": SubscriptionAgent(),
    }
    return LLMProvider(
        client,
        capabilities=CAPABILITIES,
        inputs={name: agent.Input for name, agent in agents.items()},
        read_actions={
            name: [action for action, risk in agent.ACTION_RISK.items() if risk == "read"]
            for name, agent in agents.items()
        },
        compose_fallback=compose_fallback,
    )


class PostgresTurnDependencies:
    """Implements ``CoordinatorDependencies`` against the tenant-scoped app pool."""

    known_agents = frozenset(
        {
            "escalation_agent",
            "order_agent",
            "ticket_agent",
            "ecommerce_agent",
            "kb_agent",
            "invoice_agent",
            "booking_agent",
            "subscription_agent",
        }
    )

    def __init__(
        self,
        app_pool: AsyncConnectionPool,
        llm: LLMProvider | None = None,
        jev: JevProvider | None = None,
    ) -> None:
        self._pool = app_pool
        self._llm = llm
        self._jev = jev
        self._pending = PendingActionStore(app_pool)
        self._runner = SpokeRunner(
            {
                "escalation_agent": EscalationAgent(),
                "order_agent": OrderAgent(),
                "ticket_agent": TicketAgent(),
                "order_approval": OrderApprovalAgent(),
                "ecommerce_agent": EcommerceAgent(),
                "kb_agent": KbAgent(),
                "invoice_agent": InvoiceAgent(),
                "booking_agent": BookingAgent(),
                "subscription_agent": SubscriptionAgent(),
            },
            breaker=CircuitBreaker(store=PostgresBreakerStore(app_pool)),
            recorder=PostgresRunRecorder(app_pool),
            capabilities=CapabilityRegistry(app_pool),
        )

    async def load_context(self, envelope: Envelope) -> Context:
        conversation_id = _require_conversation(envelope)
        async with tx(self._pool, str(envelope.client_id)) as conn:
            row = await db.q.fetch_one(
                "turn/get_conversation_state.sql",
                (conversation_id, envelope.client_id),
                conn=conn,
            )
        if row is None:
            raise RuntimeError("conversation_not_found")
        return Context(
            summary=row["summary"],
            clarify_count=row["clarify_count"],
            active_order_ids=row["active_order_ids"],
            flags={"human_muted": bool(row["human_muted"])},
        )

    async def guard(self, envelope: Envelope, context: Context) -> GuardResult:
        return GuardResult(allowed=True)

    async def signals(self, envelope: Envelope, context: Context) -> Signals:
        # Button taps carry no text to classify; routing handles them before signals matter.
        if self._llm is not None and envelope.callback is None:
            policy = await self._policy(envelope.client_id)
            signals, record = await classify_with(
                policy.decide_mode,
                envelope,
                context,
                llm=self._llm,
                jev=self._jev,
                min_confidence=policy.jev_min_confidence,
            )
            if record is not None:
                await self._log_decision(envelope, record)
            return signals
        return Signals(
            intent=Intent.OTHER if envelope.callback is not None else Intent.CHITCHAT,
            provider="policy",
            version="lg04-placeholder",
        )

    async def plan_args(self, envelope: Envelope, context: Context, signals: Signals) -> Plan:
        if self._llm is not None:
            return await self._llm.plan(envelope, context, signals)
        return Plan(
            tasks=[],
            reply_strategy="escalate",
            intent=signals.intent or Intent.OTHER,
            confidence=1,
            source="policy",
        )

    async def resolve_callback(self, envelope: Envelope, context: Context) -> Task | None:
        if envelope.callback is None or envelope.contact.id is None:
            return None
        action_id = envelope.callback.action_id
        data = CallbackCodec.encode(action_id)
        consumed = await self._pending.consume(
            data, client_id=envelope.client_id, contact_id=envelope.contact.id
        )
        if consumed is not None:
            agent, action, payload = consumed.agent, consumed.action, consumed.payload
        else:
            # A repeated tap re-resolves the already-used action; agents are idempotent.
            async with tx(self._pool, str(envelope.client_id)) as conn:
                row = await db.q.fetch_one(
                    "orders/get_used_action.sql",
                    (action_id, envelope.client_id, envelope.contact.id),
                    conn=conn,
                )
            if row is None:
                return None
            agent, action, payload = row["agent"], row["action"], row["payload"]
        if agent not in self.known_agents:
            return None
        return Task(
            task_id=f"callback-{action}",
            agent=agent,
            input={**payload, "action": action, "callback_id": str(action_id)},
        )

    async def next_step(self, state: TurnState) -> Sequence[Task]:
        if self._llm is None:
            return []
        policy = await self._policy(state.envelope.client_id)
        if state.step >= policy.agent_loop_max_steps:
            return []
        return await self._llm.next_step(state)

    def task_risk(self, task: Task) -> RiskClass | None:
        return self._runner.risk(task)

    async def execute_spoke(
        self,
        task: Task,
        envelope: Envelope,
        context: Context,
    ) -> AgentResult:
        contact_id = envelope.contact.id
        if contact_id is None:
            raise RuntimeError("contact_id_missing")
        async with tx(self._pool, str(envelope.client_id)) as conn:
            row = await db.q.fetch_one(
                "turn/get_client_timezone.sql", (envelope.client_id,), conn=conn
            )
        if row is None:
            raise RuntimeError("client_not_found")
        ctx = AgentContext(
            client_id=envelope.client_id,
            contact_id=contact_id,
            trace_id=envelope.trace_id,
            conversation_id=envelope.conversation.id,
            idempotency_key=f"{envelope.idempotency_key}:{task.task_id}",
            channel=envelope.channel.value,
            pool=self._pool,
            pending_actions=self._pending,
            timezone=row["timezone"],
            callback_action_id=envelope.callback.action_id if envelope.callback else None,
        )
        return await self._runner.execute(task, ctx)

    async def handle_muted(
        self,
        envelope: Envelope,
        context: Context,
    ) -> Sequence[OutboundMessage]:
        return []

    async def compose(self, state: TurnState) -> Draft:
        language = state.envelope.message.language
        if state.handoff is not None and state.handoff.required:
            return Draft(text=HANDOFF_TEXT, language=language)
        if state.plan is not None and state.plan.reply_strategy == "escalate":
            return Draft(text=HANDOFF_TEXT, language=language)
        brand = await self._client_brand(state.envelope.client_id)
        if (
            state.envelope.callback is not None
            and state.plan is not None
            and len(state.plan.tasks) == 1
            and state.plan.tasks[0].agent == "order_agent"
            and state.plan.tasks[0].input.get("action") in {"confirm", "cancel"}
            and state.facts
            and not state.failures
        ):
            if brand.verbatim_for_callbacks:
                return Draft(
                    text="\n".join(fact.text for fact in state.facts),
                    language=language,
                    fact_ids=[fact.id for fact in state.facts],
                )
        if self._llm is not None:
            return await self._llm.compose(state, brand=brand)
        body = state.envelope.message.body_redacted.strip()
        text = f"Echo: {body}"[:4096] if body else "Echo: message received."
        return Draft(text=text, language=language)

    async def verify(self, state: TurnState) -> Verification:
        if state.draft is None:
            return Verification(ok=False, issues=["empty_draft"])
        # Handoff text and button-tap acknowledgements are system text, not model claims.
        if self._llm is None or state.draft.text == HANDOFF_TEXT or state.envelope.callback:
            return Verification(ok=True)
        grounded = self._llm.verify(state)
        if not grounded.ok:
            return grounded
        if state.draft.composer == "template":
            return grounded
        if not (await self._policy(state.envelope.client_id)).semantic_verify:
            return grounded
        return await self._llm.review(state)

    async def _log_decision(self, envelope: Envelope, record: DecisionRecord) -> None:
        async with tx(self._pool, str(envelope.client_id)) as conn:
            await db.q.execute(
                "decide/insert_decision.sql",
                (
                    envelope.client_id,
                    envelope.trace_id,
                    "signals",
                    record.mode,
                    record.provider,
                    record.model_version,
                    record.latency_ms,
                    Jsonb(record.answers),
                    Jsonb(record.final),
                    record.disagree,
                    record.fallback_reason,
                ),
                conn=conn,
            )
        if record.disagree or record.fallback_reason:
            logger.info(
                "decision_compare",
                trace_id=str(envelope.trace_id),
                mode=record.mode,
                provider=record.provider,
                disagree=record.disagree,
                fallback=record.fallback_reason,
            )

    async def _policy(self, client_id: UUID) -> Policy:
        async with tx(self._pool, str(client_id)) as conn:
            row = await db.q.fetch_one("turn/get_client_policy.sql", (client_id,), conn=conn)
        if row is None:
            raise RuntimeError("client_not_found")
        return Policy.model_validate(row["policy"] or {})

    async def _client_brand(self, client_id: UUID) -> ClientBrand:
        if self._pool is None:
            return ClientBrand()
        async with tx(self._pool, str(client_id)) as conn:
            row = await db.q.fetch_one("turn/get_client_brand.sql", (client_id,), conn=conn)
        if row is None:
            return ClientBrand()
        composer = row.get("composer") or {}
        persona = row.get("persona") or {}
        return ClientBrand(
            brand_name=row.get("brand_name"),
            brand_tone=row.get("brand_tone"),
            persona_name=persona.get("name"),
            intro_line=persona.get("intro_line"),
            verbatim_for_callbacks=composer.get("verbatim_for_callbacks", True),
        )

    async def gate(self, state: TurnState) -> GateVerdict:
        verdict = evaluate(await self._policy(state.envelope.client_id), state)
        logger.info(
            "gate_verdict",
            trace_id=str(state.envelope.trace_id),
            decision=verdict.decision,
            reason=verdict.reason,
            risk=verdict.risk_score,
        )
        return verdict

    async def hold(self, state: TurnState) -> None:
        if state.draft is None or state.verdict is None:
            raise RuntimeError("hold_draft_missing")
        envelope = state.envelope
        async with tx(self._pool, str(envelope.client_id)) as conn:
            row = await db.q.fetch_one(
                "turn/get_client_policy.sql", (envelope.client_id,), conn=conn
            )
            if row is None:
                raise RuntimeError("client_not_found")
            policy = Policy.model_validate(row["policy"] or {})
            await hold_reply(
                conn,
                client_id=envelope.client_id,
                conversation_id=_require_conversation(envelope),
                trace_id=envelope.trace_id,
                draft=state.draft.text,
                reason=state.verdict.reason,
                risk=state.verdict.risk_score,
                ui=state.ui.model_dump(mode="json", exclude_none=True),
                ttl=timedelta(minutes=policy.approval_ttl_minutes),
            )

    async def emit(self, state: TurnState) -> None:
        envelope = state.envelope
        async with tx(self._pool, str(envelope.client_id)) as conn:
            for message in state.outbound:
                await _insert_outbound(conn, envelope, message)

    async def finalize(self, state: TurnState) -> None:
        conversation_id = _require_conversation(state.envelope)
        asked = state.plan is not None and state.plan.reply_strategy == "ask_clarifying"
        clarify = (state.ctx.clarify_count + 1) if asked and state.ctx is not None else 0
        intent = state.plan.intent.value if state.plan is not None else None
        async with tx(self._pool, str(state.envelope.client_id)) as conn:
            updated = await db.q.fetch_one(
                "turn/update_conversation_turn.sql",
                (clarify, intent, conversation_id, state.envelope.client_id),
                conn=conn,
            )
            if updated is None:
                raise RuntimeError("conversation_not_found")
            locale = _supported_locale(state.envelope.message.language)
            contact_id = state.envelope.contact.id
            if locale is not None and contact_id is not None:
                await db.q.fetch_one(
                    "turn/update_contact_locale.sql",
                    (locale, contact_id, state.envelope.client_id, locale),
                    conn=conn,
                )

    async def fail_safe(
        self,
        envelope: Envelope,
        context: Context | None,
        error_code: str,
    ) -> OutboundMessage:
        contact_id = envelope.contact.id
        if contact_id is None:
            raise RuntimeError("contact_id_missing")
        message = OutboundMessage(
            trace_id=envelope.trace_id,
            contact_id=contact_id,
            channel=envelope.channel,
            body=FALLBACK_TEXT,
            idempotency_key=f"{envelope.trace_id}:fallback",
        )
        async with tx(self._pool, str(envelope.client_id)) as conn:
            await _insert_escalation(conn, envelope, f"fail_safe:{error_code}", "high")
            await _insert_outbound(conn, envelope, message)
        return message


def _require_conversation(envelope: Envelope) -> UUID:
    if envelope.conversation.id is None:
        raise RuntimeError("conversation_id_missing")
    return envelope.conversation.id


async def _insert_escalation(
    conn: psycopg.AsyncConnection[Any],
    envelope: Envelope,
    reason: str,
    priority: str,
) -> None:
    await db.q.fetch_one(
        "turn/insert_escalation.sql",
        (
            envelope.client_id,
            envelope.trace_id,
            envelope.conversation.id,
            envelope.contact.id,
            reason[:500],
            priority,
        ),
        conn=conn,
    )


_SUPPORTED_LOCALES = frozenset({"en", "ms", "zh", "ta"})


def _supported_locale(language: str) -> str | None:
    primary = language.strip().lower().replace("_", "-").split("-", 1)[0]
    return primary if primary in _SUPPORTED_LOCALES else None


async def _insert_outbound(
    conn: psycopg.AsyncConnection[Any],
    envelope: Envelope,
    message: OutboundMessage,
) -> None:
    conversation_id = _require_conversation(envelope)
    row = await db.q.fetch_one(
        "worker/insert_message.sql",
        (
            envelope.client_id,
            conversation_id,
            "outbound",
            "assistant",
            message.body,
            redact(message.body),
            Jsonb([]),
            False,
            message.idempotency_key,
            envelope.trace_id,
        ),
        conn=conn,
    )
    if row is None:
        row = await db.q.fetch_one(
            "worker/get_message.sql",
            (envelope.client_id, message.idempotency_key),
            conn=conn,
        )
        if row is None or row["conversation_id"] != conversation_id:
            raise RuntimeError("outbound idempotency key conflicts with another turn")
    ui = message.ui.model_dump(mode="json", exclude_none=True)
    if ui.get("confirmation") or ui.get("options"):
        await db.q.fetch_one(
            "worker/set_message_ui.sql",
            (Jsonb(ui), row["id"], envelope.client_id),
            conn=conn,
        )
    endpoint = await db.q.fetch_one(
        "turn/get_ingress_endpoint.sql",
        (envelope.trace_id, envelope.client_id),
        conn=conn,
    )
    if endpoint is None:
        raise RuntimeError("ingress_endpoint_not_found")
    await db.q.fetch_one(
        "worker/insert_delivery.sql",
        (
            envelope.client_id,
            endpoint["endpoint_id"],
            envelope.trace_id,
            conversation_id,
            row["id"],
        ),
        conn=conn,
    )
