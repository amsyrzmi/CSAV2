"""Postgres side effects plus deterministic LG04 deciders for the coordinator.

Persistence (context, emit, finalize, fail-safe, escalation) is real. The
decision providers (guard, signals, plan, compose, verify, gate) are
deterministic placeholders until LG06/LG07 supply model-backed versions, and the
spokes are the LG05 order and escalation agents.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.agents.base import AgentContext
from csa.agents.breaker import CircuitBreaker
from csa.agents.ecommerce.agent import EcommerceAgent
from csa.agents.ecommerce.models import (
    ORDER_NUMBER_PLACEHOLDER_PATTERN,
    PLAIN_ORDER_NUMBER_PATTERN,
)
from csa.agents.escalation.agent import EscalationAgent
from csa.agents.invoices.agent import InvoiceAgent
from csa.agents.kb.agent import KbAgent
from csa.agents.order_approval.agent import OrderApprovalAgent
from csa.agents.orders.agent import OrderAgent
from csa.agents.recorder import PostgresBreakerStore, PostgresRunRecorder
from csa.agents.runner import SpokeRunner
from csa.agents.staged.booking_facts import EMAIL_NOT_SEEN
from csa.agents.staged.service import BookingAgent, SubscriptionAgent, reply
from csa.agents.tickets.agent import TicketAgent
from csa.approval.service import hold_reply
from csa.bookings.turn_context import booking_context
from csa.console.mailer import Mailer
from csa.contracts.callbacks import CallbackCodec, PendingActionStore
from csa.contracts.envelope import Channel, Envelope
from csa.contracts.facts import AgentResult, Fact
from csa.contracts.plan import Intent, Plan, RiskClass, Task
from csa.contracts.registry import CapabilityRegistry
from csa.contracts.state import (
    RECENT_WINDOW,
    BookingContext,
    Context,
    Draft,
    GateVerdict,
    GuardResult,
    OutboundMessage,
    RecentMessage,
    Signals,
    TurnState,
    Verification,
)
from csa.core import db
from csa.core.pii import real_email, redact, redact_with_values
from csa.core.tenancy import tx
from csa.decide.completion import JsonCompletionClient
from csa.decide.jev import JevProvider
from csa.decide.llm import ClientBrand, LLMProvider, _recent_messages
from csa.decide.providers import DecideMode, DecisionRecord, classify_with
from csa.graph.policy.gate import Policy, evaluate
from csa.graph.policy.plan_rules import tiktok_stock_fallback
from csa.graph.policy.routing import has_hard_escalate_words
from csa.marketplace.reads import LiveReadAccess, PostgresMarketplaceReader
from csa.staff.ownership import automation_allowed, replies_off
from csa.turn.texts import (
    clip_message,
    handoff_template,
    handoff_text_ok,
    holding_text,
    resolve_language,
)

logger = structlog.get_logger(__name__)

# How long the handoff reply (brand read and model call) may take before the template is sent.
# Under the 10 s compose node timeout, so a slow model never sends a handoff turn to fail_safe.
HANDOFF_COMPOSE_BUDGET_S = 6.0

# The booking read's own share of the context node's 2 s: longer and the customer is planned for
# without it, as every other company is.
BOOKING_CONTEXT_BUDGET_S = 1.0
# The earlier messages and the handoff flag have their own second, for the same reason: a read
# that overruns is left out, the turn is not failed.
RECENT_BUDGET_S = 1.0
# The reply of a turn that wrote a booking is composed and checked inside this, a little under the
# graph's 10 s node limit (which would cancel it without a word): past it the facts are sent.
BOOKING_COMPOSE_BUDGET_S = 9.0
SANDBOX_SCOPE_TEXT = (
    "Sandbox assistant can only answer knowledge and product-information questions. "
    "Customer records, business actions and staff escalation are unavailable."
)
_SANDBOX_ACCOUNT_INTENTS = frozenset(
    {
        Intent.ORDER,
        Intent.REFUND_RETURNS,
        Intent.BILLING_INVOICE,
        Intent.BOOKING,
        Intent.SUBSCRIPTION,
        Intent.WARRANTY,
        Intent.HUMAN_REQUEST,
    }
)


CAPABILITIES = {
    "order_agent": "Order status, product search and details, creating order drafts",
    "ticket_agent": "Support tickets: create, look up, list, add a note, cancel, reopen",
    "ecommerce_agent": (
        "Read-only TikTok Shop answers: the status and tracking of an order by its TikTok "
        "order number, and the stock of items found only on our TikTok Shop (checked after "
        "order_agent finds no match in our own catalogue)"
    ),
    "kb_agent": "Knowledge base search and FAQ lookup for business questions and product docs",
    "invoice_agent": "Invoice lookup, status check, and signed download links",
    "booking_agent": (
        "Free times for a service, booking, moving and cancelling the customer's own "
        "appointments, and looking up their bookings"
    ),
    "subscription_agent": "Plan/status lookup and unexecuted requests; no billing changes",
}


_PLANNABLE: dict[str, Any] = {
    "order_agent": OrderAgent,
    "ticket_agent": TicketAgent,
    "ecommerce_agent": EcommerceAgent,
    "kb_agent": KbAgent,
    "invoice_agent": InvoiceAgent,
    "booking_agent": BookingAgent,
    "subscription_agent": SubscriptionAgent,
}
# Spec 6.7: the client's own catalogue answers stock first. The TikTok stock check runs only
# as the deterministic fallback in ``next_step`` (plan_rules.tiktok_stock_fallback), so the
# model's look-ahead may not propose it.
_NOT_FOR_THE_MODEL_LOOP = frozenset({("ecommerce_agent", "stock_check")})


def booking_write_facts(state: TurnState) -> list[tuple[str, Fact]]:
    """The facts (with their turn ids) of every booking write that ran in this turn."""
    if state.plan is None:
        return []
    writes = {
        task.task_id
        for task in state.plan.tasks
        if task.agent == "booking_agent"
        and BookingAgent.ACTION_RISK.get(str(task.input.get("action"))) == "commit"
    }
    return [
        (f"{result.task_id}:{fact.id}", fact)
        for result in state.results
        if result.ok and result.task_id in writes
        for fact in result.facts
    ]


def _facts_draft(written: list[tuple[str, Fact]], language: str) -> Draft:
    """The facts of a booking write, as they stand, as the reply (the agent writes them to be
    read by a customer). Marked `template`, so the coordinator's check is grounding alone."""
    return Draft(
        text="\n".join(fact.text for _, fact in written)[:8000],
        language=language if len(language) >= 2 else "en",
        fact_ids=[fact_id for fact_id, _ in written][:100],
        composer="template",
    )


def with_real_email(task: Task, raw: str) -> Task | None:
    """The booking task with its email swapped for the address the customer really wrote.

    The planner may name `[EMAIL_n]` (or a literal address, which is only a guess); only an
    address that is in the raw message survives, so a model can never invent one. Without one the
    field is dropped. None when that leaves a `save_details` with nothing to save.
    """
    given = task.input.get("customer_email")
    if given is None:
        return task
    payload = {k: v for k, v in task.input.items() if k != "customer_email"}
    address = real_email(str(given), raw)
    if address is not None:
        payload["customer_email"] = address
    elif task.input.get("action") == "save_details" and not payload.get("customer_name"):
        return None
    return task.model_copy(update={"input": payload})


def build_llm_provider(
    client: JsonCompletionClient,
    *,
    compose_fallback: JsonCompletionClient | None = None,
) -> LLMProvider:
    """Wire the planner to the plannable agents' own input models (classes, not instances)."""
    return LLMProvider(
        client,
        capabilities=CAPABILITIES,
        inputs={name: agent.Input for name, agent in _PLANNABLE.items()},
        read_actions={
            name: [
                action
                for action, risk in agent.ACTION_RISK.items()
                if risk == "read" and (name, action) not in _NOT_FOR_THE_MODEL_LOOP
            ]
            for name, agent in _PLANNABLE.items()
        },
        compose_fallback=compose_fallback,
    )


async def _handoff_recent(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, conversation_id: UUID
) -> bool:
    """True when a person was really asked to take this chat over in the last 24 hours."""
    row = await db.q.fetch_one(
        "turn/recent_handoff.sql",
        {"client_id": client_id, "conversation_id": conversation_id},
        conn=conn,
    )
    return bool(row and row["handoff_recent"])


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
        mailer: Mailer | None = None,
        *,
        marketplace_live: LiveReadAccess | None = None,
        default_decide_mode: Literal["llm", "cascade"] = "llm",
    ) -> None:
        self._pool = app_pool
        self._llm = llm
        self._jev = jev
        # CUSTOMER_DECIDE_MODE_DEFAULT: for a company with no decide_mode stored, with Jev only.
        self._default_decide_mode: Literal["llm", "cascade"] = default_decide_mode
        # Booking mail goes out after a turn's transaction commits, from tasks this agent holds.
        # The worker keeps the one agent so it can `drain` it before it closes the pool.
        self._booking_agent = BookingAgent(mailer)
        self._pending = PendingActionStore(app_pool)
        self._capabilities = CapabilityRegistry(app_pool)
        # Without live access (TikTok off) stock answers come from our copy only.
        marketplace = PostgresMarketplaceReader(app_pool, live=marketplace_live)
        self._runner = SpokeRunner(
            {
                "escalation_agent": EscalationAgent(),
                "order_agent": OrderAgent(),
                "ticket_agent": TicketAgent(),
                "order_approval": OrderApprovalAgent(),
                "ecommerce_agent": EcommerceAgent(marketplace),
                "kb_agent": KbAgent(),
                "invoice_agent": InvoiceAgent(),
                "booking_agent": self._booking_agent,
                "subscription_agent": SubscriptionAgent(),
            },
            breaker=CircuitBreaker(store=PostgresBreakerStore(app_pool)),
            recorder=PostgresRunRecorder(app_pool),
            capabilities=self._capabilities,
        )

    async def drain(self) -> None:
        """Wait for the booking mail still on its way, so a restart does not lose it."""
        await self._booking_agent.drain()

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
        recent, handoff_recent = await self._recent_messages(envelope, conversation_id)
        return Context(
            summary=row["summary"],
            clarify_count=row["clarify_count"],
            active_order_ids=row["active_order_ids"],
            flags={"human_muted": bool(row["human_muted"]), "handoff_recent": handoff_recent},
            booking=await self._booking_context(envelope, conversation_id),
            recent=recent,
        )

    async def _recent_messages(
        self, envelope: Envelope, conversation_id: UUID
    ) -> tuple[list[RecentMessage], bool]:
        """The chat's earlier messages (oldest first) and whether a person was lately asked in.

        For every company and channel, unless the company switched `conversation_memory` off, in
        which case no message is read. Its own budget, and any failure leaves both empty: this
        must never fail the turn.
        """
        try:
            async with asyncio.timeout(RECENT_BUDGET_S):
                policy = await self._policy(envelope.client_id)
                async with tx(self._pool, str(envelope.client_id)) as conn:
                    recent: list[RecentMessage] = []
                    if policy.conversation_memory:
                        rows = await db.q.fetch_all(
                            "turn/recent_messages.sql",
                            {
                                "client_id": envelope.client_id,
                                "conversation_id": conversation_id,
                                "trace_id": envelope.trace_id,
                                "limit": RECENT_WINDOW,
                            },
                            conn=conn,
                        )
                        recent = [
                            RecentMessage(
                                author="customer" if row["direction"] == "inbound" else "assistant",
                                text=clip_message(row["redacted_body"].strip()),
                            )
                            for row in rows
                        ]
                    handoff = await _handoff_recent(conn, envelope.client_id, conversation_id)
                return recent, handoff
        except Exception as exc:  # including TimeoutError
            logger.warning(
                "turn_context_degraded",
                category="recent_messages_failed",
                error_type=type(exc).__name__,
                trace_id=str(envelope.trace_id),
            )
            return [], False

    async def _booking_context(
        self, envelope: Envelope, conversation_id: UUID
    ) -> BookingContext | None:
        """What the planner needs to book; None where the bot cannot, or if reading it fails.

        A failure here must never fail the turn: the turn then runs as it always did.
        """
        contact_id = envelope.contact.id
        # A preview is not a customer: its sandbox contact has no bookings to plan with.
        if contact_id is None or envelope.channel == Channel.WEB:
            return None
        try:
            # Its own budget, inside the context node's limit: a node that times out cancels its
            # work and fails the whole turn, while a read that overruns here is only left out.
            async with asyncio.timeout(BOOKING_CONTEXT_BUDGET_S):
                async with tx(self._pool, str(envelope.client_id)) as conn:
                    return await booking_context(
                        conn,
                        envelope.client_id,
                        contact_id,
                        conversation_id,
                        envelope.trace_id,
                        datetime.now(UTC),
                    )
        except Exception as exc:  # including TimeoutError
            logger.warning(
                "turn_context_degraded",
                category="booking_context_failed",
                error_type=type(exc).__name__,
                trace_id=str(envelope.trace_id),
            )
            return None

    async def guard(self, envelope: Envelope, context: Context) -> GuardResult:
        return GuardResult(allowed=True)

    async def signals(self, envelope: Envelope, context: Context) -> Signals:
        # Button taps carry no text to classify; routing handles them before signals matter.
        if self._llm is not None and envelope.callback is None:
            policy = await self._policy(envelope.client_id)
            signals, record = await classify_with(
                self._decide_mode(policy),
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
        if envelope.channel == Channel.WEB and envelope.callback is not None:
            raise ValueError("sandbox_callbacks_forbidden")
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
        # Local catalogue first (spec 6.7): only a stock question our own products could not
        # answer goes to the TikTok copy, and only for a company with the E-commerce service.
        # The web sandbox answers product questions from the catalogue only.
        fallback = [] if state.envelope.channel == Channel.WEB else tiktok_stock_fallback(state)
        if fallback and await self._capabilities.allows_execution(
            state.envelope.client_id, "ecommerce_agent"
        ):
            return fallback
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
        if task.agent == "booking_agent":
            # The planner saw only redacted text, so it can name an address but not write one.
            real = with_real_email(task, envelope.message.body_raw)
            if real is None:
                return reply(ctx, [EMAIL_NOT_SEEN]).model_copy(update={"task_id": task.task_id})
            task = real
        return await self._runner.execute(_resolve_order_number(task, envelope), ctx)

    async def handle_muted(
        self,
        envelope: Envelope,
        context: Context,
    ) -> Sequence[OutboundMessage]:
        return []

    async def compose(self, state: TurnState) -> Draft:
        written = booking_write_facts(state)
        if not written:
            return await self._compose(state)
        # A booking has been written (or refused: the facts are true either way, say no booking
        # words, and open no escalation). Whatever fails while the reply is composed or checked,
        # the customer is told the facts as they stand, never the fail-safe.
        try:
            async with asyncio.timeout(BOOKING_COMPOSE_BUDGET_S):
                return await self._compose(state)
        except Exception as exc:  # including TimeoutError
            logger.warning(
                "booking_reply_fell_back_to_facts",
                error_type=type(exc).__name__,
                trace_id=str(state.envelope.trace_id),
            )
            return _facts_draft(written, state.envelope.message.language)

    async def _compose(self, state: TurnState) -> Draft:
        if state.envelope.channel == Channel.WEB and (
            (state.handoff is not None and state.handoff.required)
            or any(error.code == "sandbox_action_forbidden" for error in state.failures)
            or (
                state.plan is not None
                and (
                    state.plan.requires_confirmation
                    or state.plan.reply_strategy in {"confirm", "escalate"}
                    or (state.plan.intent in _SANDBOX_ACCOUNT_INTENTS and not state.facts)
                )
            )
        ):
            return Draft(
                text=SANDBOX_SCOPE_TEXT,
                language=state.envelope.message.language,
                composer="template",
            )
        if (state.handoff is not None and state.handoff.required) or (
            state.plan is not None and state.plan.reply_strategy == "escalate"
        ):
            return await self._handoff_draft(state)
        language = state.envelope.message.language
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
            return await self._keep_a_written_booking(
                state, await self._llm.compose(state, brand=brand)
            )
        body = state.envelope.message.body_redacted.strip()
        text = f"Echo: {body}"[:4096] if body else "Echo: message received."
        return Draft(text=text, language=language)

    async def _handoff_draft(self, state: TurnState) -> Draft:
        """The reply that tells the customer a person has been told: written by the model where
        that is safe (`_handoff_may_be_written`) and it passes the light check, else the localized
        template. The check runs here, once; `verify` skips a handoff draft."""
        signal = state.signals.answers.get("language") if state.signals else None
        message = state.envelope.message
        language = resolve_language(
            signal if isinstance(signal, str) else None, message.language, message.body_redacted
        )
        if self._llm is not None and _handoff_may_be_written(state):
            # Slow or failing, the brand read or the model must never fail the compose node: by
            # now the conversation is muted, so a turn sent to fail_safe would tell the customer
            # nothing. Any error (CancelledError is not an Exception) falls to the template.
            try:
                async with asyncio.timeout(HANDOFF_COMPOSE_BUDGET_S):
                    brand = await self._client_brand(state.envelope.client_id)
                    text = await self._llm.compose_handoff(state, brand)
            except Exception as error:
                logger.warning(
                    "handoff_reply_replaced_by_template",
                    trace_id=str(state.envelope.trace_id),
                    error_type=type(error).__name__,
                )
                text = None
            sources = [
                *(m.text for m in _recent_messages(state.ctx or Context())),
                message.body_redacted,
                *(fact.text for fact in state.facts),
            ]
            if text and handoff_text_ok(text, sources):
                return Draft(text=text, language=language, handoff=True, composer="primary")
            if text:
                logger.info(
                    "handoff_reply_replaced_by_template",
                    trace_id=str(state.envelope.trace_id),
                    chars=len(text),
                )
        return Draft(
            text=handoff_template(language), language=language, handoff=True, composer="template"
        )

    async def _keep_a_written_booking(self, state: TurnState, draft: Draft) -> Draft:
        """A turn that wrote a booking must tell the customer what was written.

        The booking has committed, so a reply that cannot pass its checks must never become the
        no-claim fallback or "try again" (a retry would only repeat or contradict it). When the
        composers are down, or the second draft still fails, the facts are sent as they stand: the
        agent writes them to be read by a customer. Every other turn is left as it was.
        """
        written = booking_write_facts(state)
        if not written:
            return draft
        if draft.composer != "template":
            if state.regen_count == 0:
                return draft
            if await self._passes(state.model_copy(update={"draft": draft})):
                return draft
        return _facts_draft(written, draft.language)

    async def _passes(self, state: TurnState) -> bool:
        """Whether this retry draft passes grounding and, if on, the model review. A review that
        cannot run does not pass it. The coordinator's own check then lets it through (`verify`).
        """
        assert self._llm is not None and state.draft is not None
        if not self._llm.verify(state).ok:
            return False
        if not (await self._policy(state.envelope.client_id)).semantic_verify:
            return True
        try:
            return (await self._llm.review(state)).ok
        except Exception:
            return False

    async def verify(self, state: TurnState) -> Verification:
        if state.draft is None:
            return Verification(ok=False, issues=["empty_draft"])
        # A handoff reply was checked where it was written (`_handoff_draft`); button-tap
        # acknowledgements are system text, not model claims.
        if (
            self._llm is None
            or state.draft.handoff
            or state.envelope.callback
            or (state.envelope.channel == Channel.WEB and state.draft.text == SANDBOX_SCOPE_TEXT)
        ):
            return Verification(ok=True)
        grounded = self._llm.verify(state)
        if not grounded.ok:
            return grounded
        if state.draft.composer == "template":
            return grounded
        if not booking_write_facts(state):
            if not (await self._policy(state.envelope.client_id)).semantic_verify:
                return grounded
            try:
                return await self._llm.review(state)
            except Exception as exc:  # advisory: the deterministic check has already passed
                logger.warning(
                    "semantic_review_degraded",
                    error_type=type(exc).__name__,
                    trace_id=str(state.envelope.trace_id),
                )
                return grounded
        if state.regen_count >= 1:
            # A booking turn's retry was already reviewed when it was composed (`_passes`), or it
            # is the facts. A second, different answer from the model must not overturn it.
            return grounded
        try:
            if not (await self._policy(state.envelope.client_id)).semantic_verify:
                return grounded
            return await self._llm.review(state)
        except Exception as exc:  # a check that cannot run: send the draft back for its retry
            logger.warning(
                "booking_check_unavailable",
                error_type=type(exc).__name__,
                trace_id=str(state.envelope.trace_id),
            )
            return Verification(ok=False, issues=["review_unavailable"])

    def _decide_mode(self, policy: Policy) -> DecideMode:
        """A stored decide_mode always wins (an explicit llm included); otherwise the default,
        and that only while there is a Jev provider."""
        if "decide_mode" in policy.model_fields_set:
            return policy.decide_mode
        if self._jev is None:
            return "llm"
        return self._default_decide_mode

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
            # A held handoff reply is kept for staff: the escalation agent has already muted the
            # conversation, which would otherwise drop it without a word.
            if not await automation_allowed(
                conn,
                envelope.client_id,
                _require_conversation(envelope),
                envelope.trace_id,
                handoff=state.draft.handoff,
            ):
                return
            if envelope.channel == Channel.WEB:
                held = await db.q.fetch_one(
                    "console/chat_hold.sql",
                    (
                        state.verdict.reason[:500],
                        state.draft.text,
                        envelope.client_id,
                        envelope.trace_id,
                    ),
                    conn=conn,
                )
                if held is None:
                    raise RuntimeError("sandbox_turn_not_found")
                return
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
        draft = state.draft
        async with tx(self._pool, str(envelope.client_id)) as conn:
            for message in state.outbound:
                # The draft says it is the handoff reply; only the message that carries its text
                # is sent on the handoff's authority (never on the web sandbox).
                handoff = (
                    envelope.channel != Channel.WEB
                    and draft is not None
                    and draft.handoff
                    and message.body == draft.text
                )
                await _insert_outbound(conn, envelope, message, handoff=handoff)

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
            body=(
                "Sandbox assistant could not complete this turn. No business action or "
                "staff escalation was performed."
                if envelope.channel == Channel.WEB
                # Promises nothing a person will do: the escalations row below notifies no one.
                else holding_text(
                    resolve_language(
                        None, envelope.message.language, envelope.message.body_redacted
                    )
                )
            ),
            idempotency_key=f"{envelope.trace_id}:fallback",
        )
        async with tx(self._pool, str(envelope.client_id)) as conn:
            if not await automation_allowed(
                conn, envelope.client_id, _require_conversation(envelope), envelope.trace_id
            ):
                return message
            if envelope.channel != Channel.WEB:
                await _insert_escalation(conn, envelope, f"fail_safe:{error_code}", "high")
                if await replies_off(conn, envelope.client_id):
                    return message
            else:
                failed = await db.q.fetch_one(
                    "console/chat_fail.sql", (envelope.client_id, envelope.trace_id), conn=conn
                )
                if failed is None:
                    raise RuntimeError("sandbox_turn_not_found")
            await _insert_outbound(conn, envelope, message)
        return message


_ORDER_NUMBER_PLACEHOLDER = re.compile(ORDER_NUMBER_PLACEHOLDER_PATTERN)
_PLAIN_ORDER_NUMBER = re.compile(PLAIN_ORDER_NUMBER_PATTERN)


def _resolve_order_number(task: Task, envelope: Envelope) -> Task:
    """A TikTok order number the model saw as "[CARD_0]" becomes its digits again (spec 6.7).

    Only for ecommerce_agent order_status, and only when the placeholder comes from this very
    message: the raw body is redacted again with the same single pass and must give the stored
    redacted body. The digits go to this task's input only (the lookup's SQL parameter); the
    plan, the model's view, the logs and the recorded run (which redacts its input) keep the
    placeholder. Anything unresolvable stays a placeholder, which the agent answers by asking
    for the number again.
    """
    if task.agent != "ecommerce_agent" or task.input.get("action") != "order_status":
        return task
    number = task.input.get("order_number")
    if not isinstance(number, str) or not _ORDER_NUMBER_PLACEHOLDER.match(number):
        return task
    redacted, values = redact_with_values(envelope.message.body_raw)
    if redacted != envelope.message.body_redacted:
        return task
    digits = "".join(char for char in values.get(number, "") if char.isdigit())
    if not _PLAIN_ORDER_NUMBER.match(digits):
        return task
    return task.model_copy(update={"input": {**task.input, "order_number": digits}})


# Routes whose handoff reply is always the template: the guard or the legal-word rule blocked the
# message, there was no transcript, or the language is one the bot does not write.
_TEMPLATE_HANDOFF_ROUTES = frozenset(
    {"hard_escalate", "guard_blocked", "voice_failed", "unsupported_language"}
)
_WRITTEN_HANDOFF_REASONS = frozenset({"human_requested", "angry_customer", "agent_failure"})


def _handoff_may_be_written(state: TurnState) -> bool:
    """Whether the model may write this turn's handoff reply (plan ruling R3).

    Never on a button tap, on an injection turn, on a message with a legal or police word, or for
    a reason other than asking for a person, anger, an agent failure or an escalating plan. An
    escalating plan is written only off the hard routes; a person asked for, or anger, also
    reaches ``hard_escalate`` (from the classifier), and is written unless the guard or the
    route's own rules (voice, language) blocked the message.
    """
    if state.envelope.callback is not None or state.handoff is None:
        return False
    if state.signals is not None and state.signals.answers.get("injection") is True:
        return False
    if has_hard_escalate_words(state.envelope.message.body_raw):
        return False
    reason = state.handoff.reason
    if reason in _WRITTEN_HANDOFF_REASONS:
        return state.route not in _TEMPLATE_HANDOFF_ROUTES - {"hard_escalate"}
    return reason == "plan_escalated" and state.route not in _TEMPLATE_HANDOFF_ROUTES


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
    *,
    handoff: bool = False,
) -> None:
    if envelope.channel == Channel.WEB and (
        message.channel != Channel.WEB
        or message.contact_id != envelope.contact.id
        or message.ui.confirmation is not None
        or message.ui.options
    ):
        raise ValueError("sandbox_outbound_authority_or_action_invalid")
    conversation_id = _require_conversation(envelope)
    if not await automation_allowed(
        conn, envelope.client_id, conversation_id, envelope.trace_id, handoff=handoff
    ):
        return
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
    if handoff:
        await db.q.execute("staff/set_handoff.sql", (envelope.client_id, row["id"]), conn=conn)
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
