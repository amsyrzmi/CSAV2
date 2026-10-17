"""LG04 coordinator policy and graph behavior."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import pytest
from csa.contracts.envelope import (
    Callback,
    Channel,
    Contact,
    ConversationRef,
    Envelope,
    Message,
    Meta,
)
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import Intent, Plan, RiskClass, Task
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
from csa.graph.coordinator import run_turn
from csa.graph.policy.plan_rules import resolve_order_status, validate_plan
from csa.graph.policy.routing import route_turn
from langgraph.checkpoint.memory import InMemorySaver

TRACE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CLIENT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
CONTACT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CONVERSATION_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


def make_envelope(
    body: str = "hello",
    *,
    callback: Callback | None = None,
    attachments: list | None = None,
) -> Envelope:
    return Envelope(
        trace_id=TRACE_ID,
        received_at=datetime(2026, 10, 11, tzinfo=UTC),
        channel=Channel.TELEGRAM,
        idempotency_key="test:event-1",
        client_id=CLIENT_ID,
        contact=Contact(
            id=CONTACT_ID,
            external_id="test-user",
            channel=Channel.TELEGRAM,
            display_name=None,
        ),
        conversation=ConversationRef(
            id=CONVERSATION_ID,
            channel_thread="test-chat",
            turn_count=1,
            status="open",
        ),
        message=Message(
            body_raw=body,
            body_redacted=body,
            language="en",
            attachments=attachments or [],
        ),
        callback=callback,
        meta=Meta(),
    )


def make_signals(
    intent: Intent = Intent.CHITCHAT,
    *,
    answers: dict | None = None,
) -> Signals:
    return Signals(
        intent=intent,
        answers=answers or {},
        provider="policy",
        version="test",
    )


def make_result(task: Task, *, error: bool = False) -> AgentResult:
    return AgentResult(
        ok=not error,
        status="error" if error else "ok",
        task_id=task.task_id,
        agent=task.agent,
        data=AgentResultData(
            facts=[] if error else [Fact(id="answer", text="A fact", source=task.agent)]
        ),
        error=(
            AgentError(code="failed", category="internal", message="test failure")
            if error
            else None
        ),
        meta=ResultMeta(trace_id=TRACE_ID, latency_ms=1),
    )


class FakeDependencies:
    known_agents = frozenset({"alpha", "beta", "escalation_agent"})

    def __init__(
        self,
        *,
        fail_compose: bool = False,
        parallel: bool = False,
        angry: bool = False,
        regen_once: bool = False,
        always_fail_verify: bool = False,
        muted: bool = False,
    ) -> None:
        self.fail_compose = fail_compose
        self.parallel = parallel
        self.angry = angry
        self.regen_once = regen_once
        self.always_fail_verify = always_fail_verify
        self.verify_seen: list[list[str]] = []
        self.muted = muted
        self.active_spokes = 0
        self.both_spokes_started = asyncio.Event()
        self.compose_calls = 0
        self.held = 0
        self.gate_decision: Literal["send", "approve", "reject"] = "send"
        self.verify_calls = 0
        self.executed_tasks: list[str] = []
        self.emitted: list[OutboundMessage] = []
        self.fallbacks: list[str] = []
        self.finalized = 0
        self.steps: list[list[Task]] = []
        self.observed = 0
        self.fail_observe = False
        self.risks: dict[str, RiskClass] = {}

    async def next_step(self, state: TurnState) -> list[Task]:
        self.observed += 1
        if self.fail_observe:
            raise RuntimeError("synthetic observe failure")
        return self.steps.pop(0) if self.steps else []

    def task_risk(self, task: Task) -> RiskClass | None:
        return self.risks.get(task.agent, "read")

    async def load_context(self, envelope: Envelope) -> Context:
        return Context(flags={"human_muted": self.muted})

    async def guard(self, envelope: Envelope, context: Context) -> GuardResult:
        return GuardResult(allowed=True)

    async def signals(self, envelope: Envelope, context: Context) -> Signals:
        if envelope.callback is not None:
            return make_signals(Intent.ORDER)
        if self.parallel:
            return Signals(
                intent=Intent.PRODUCT_INFO,
                capability_scores={"alpha": 0.9, "beta": 0.8},
                answers={"needs:alpha": True, "needs:beta": True},
                provider="policy",
                version="test",
            )
        if self.angry:
            return Signals(
                intent=Intent.COMPLAINT,
                answers={"sentiment": "angry"},
                provider="policy",
                version="test",
            )
        return make_signals()

    async def plan_args(
        self,
        envelope: Envelope,
        context: Context,
        signals: Signals,
    ) -> Plan:
        if self.parallel:
            tasks = [
                Task(task_id="alpha-1", agent="alpha", input={}),
                Task(task_id="beta-1", agent="beta", input={}),
            ]
            return Plan(
                tasks=tasks,
                reply_strategy="auto_reply",
                intent=Intent.PRODUCT_INFO,
                confidence=0.9,
                source="jev",
            )
        raise AssertionError("Unexpected plan_args call")

    async def resolve_callback(self, envelope: Envelope, context: Context) -> Task:
        return Task(task_id="callback-1", agent="alpha", input={})

    async def execute_spoke(
        self,
        task: Task,
        envelope: Envelope,
        context: Context,
    ) -> AgentResult:
        self.executed_tasks.append(task.agent)
        if self.parallel and task.agent in {"alpha", "beta"}:
            self.active_spokes += 1
            if self.active_spokes == 2:
                self.both_spokes_started.set()
            try:
                await asyncio.wait_for(self.both_spokes_started.wait(), timeout=1)
            finally:
                self.active_spokes -= 1
        return make_result(task)

    async def handle_muted(
        self,
        envelope: Envelope,
        context: Context,
    ) -> list[OutboundMessage]:
        if not self.muted:
            return []
        return [
            OutboundMessage(
                trace_id=envelope.trace_id,
                contact_id=CONTACT_ID,
                channel=envelope.channel,
                body="A human team member is handling this conversation.",
                idempotency_key=f"{envelope.trace_id}:muted",
            )
        ]

    async def compose(self, state: TurnState) -> Draft:
        self.compose_calls += 1
        if self.fail_compose:
            raise RuntimeError("synthetic compose failure")
        return Draft(text="Hello there.", language="en")

    async def verify(self, state: TurnState) -> Verification:
        self.verify_calls += 1
        self.verify_seen.append(list(state.verify_issues))
        if self.always_fail_verify:
            return Verification(ok=False, issues=["ungrounded value 999"])
        if self.regen_once and self.verify_calls == 1:
            return Verification(ok=False, issues=["claims stock not in facts"])
        return Verification(ok=True)

    async def gate(self, state: TurnState) -> GateVerdict:
        if state.verified is False:
            return GateVerdict(
                decision="approve",
                risk_score=1.0,
                reason="draft_verification_failed",
                policy="test",
            )
        return GateVerdict(
            decision=self.gate_decision,
            risk_score=0,
            reason="test policy",
            policy="test",
        )

    async def hold(self, state: TurnState) -> None:
        self.held += 1

    async def emit(self, state: TurnState) -> None:
        self.emitted.extend(state.outbound)

    async def finalize(self, state: TurnState) -> None:
        self.finalized += 1

    async def fail_safe(
        self,
        envelope: Envelope,
        context: Context | None,
        error_code: str,
    ) -> OutboundMessage:
        self.fallbacks.append(error_code)
        return OutboundMessage(
            trace_id=envelope.trace_id,
            contact_id=CONTACT_ID,
            channel=envelope.channel,
            body="A team member will help you.",
            idempotency_key=f"{envelope.trace_id}:fallback",
        )


def test_routing_precedence_mute_and_callback() -> None:
    callback = Callback(action_id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"))
    context = Context(flags={"human_muted": True})
    state = TurnState(envelope=make_envelope(callback=callback), ctx=context)
    state.signals = make_signals(Intent.ORDER)
    assert route_turn(state) == "muted"

    context.flags["human_muted"] = False
    assert route_turn(state) == "callback"


def test_routing_prioritizes_hard_escalation_keyword() -> None:
    state = TurnState(
        envelope=make_envelope("I will sue"),
        ctx=Context(),
        guard=GuardResult(allowed=False),
        signals=make_signals(Intent.CHITCHAT),
    )
    assert route_turn(state) == "hard_escalate"


def test_order_status_policy_does_not_mutate_context() -> None:
    context = Context(active_order_ids=["ORD-1"])
    original_facts = list(context.customer_facts)
    plan = resolve_order_status(
        task_id="status",
        body="Where is my order?",
        context=context,
        signals=make_signals(Intent.ORDER),
    )
    assert plan is not None
    assert plan.tasks[0].input["order_id"] == "ORD-1"
    assert context.customer_facts == original_facts


def test_plan_validation_escalates_unknown_agents_and_caps_clarification() -> None:
    plan = Plan(
        tasks=[Task(task_id="unknown", agent="not_registered", input={})],
        reply_strategy="auto_reply",
        intent=Intent.PRODUCT_INFO,
        confidence=0.9,
        source="jev",
    )
    escalated, errors = validate_plan(
        plan,
        known_agents=frozenset({"alpha"}),
        clarify_count=0,
    )
    assert escalated.reply_strategy == "escalate"
    assert errors[0].code == "unknown_agent"

    clarify = Plan(
        tasks=[],
        reply_strategy="ask_clarifying",
        intent=Intent.PRODUCT_INFO,
        confidence=0.9,
        source="jev",
    )
    capped, _ = validate_plan(clarify, known_agents=frozenset(), clarify_count=1)
    assert capped.reply_strategy == "escalate"


@pytest.mark.asyncio
async def test_coordinator_executes_parallel_spokes_and_emits_once() -> None:
    deps = FakeDependencies(parallel=True)
    started = time.perf_counter()

    result = await run_turn(make_envelope("What is this product?"), deps)

    assert time.perf_counter() - started < 1
    assert len(result.results) == 2
    assert all(item.status == "ok" for item in result.results)
    assert deps.both_spokes_started.is_set()
    assert len(deps.emitted) == 1
    assert deps.emitted[0].body == "Hello there."
    assert deps.finalized == 1


@pytest.mark.asyncio
async def test_coordinator_routes_compose_failure_to_fail_safe() -> None:
    deps = FakeDependencies(fail_compose=True)

    result = await run_turn(make_envelope(), deps)

    assert deps.fallbacks == ["compose:RuntimeError"]
    assert result.outbound[0].idempotency_key.endswith(":fallback")
    assert deps.emitted == []


@pytest.mark.asyncio
async def test_angry_customer_runs_escalation_agent() -> None:
    deps = FakeDependencies(angry=True)

    result = await run_turn(make_envelope("I am angry"), deps)

    assert "escalation_agent" in deps.executed_tasks
    assert result.handoff is not None and result.handoff.required
    assert result.handoff.reason == "angry_customer"


@pytest.mark.asyncio
async def test_muted_conversation_emits_ack_without_running_planner() -> None:
    deps = FakeDependencies(muted=True)

    result = await run_turn(make_envelope(), deps)

    assert len(result.outbound) == 1
    assert result.outbound[0].idempotency_key.endswith(":muted")
    assert deps.emitted == result.outbound
    assert deps.compose_calls == 0
    assert deps.executed_tasks == []


@pytest.mark.asyncio
async def test_muted_conversation_with_no_ack_finishes_silently() -> None:
    class Silent(FakeDependencies):
        async def signals(self, envelope, context):  # type: ignore[no-untyped-def]
            raise AssertionError("muted turns must skip classifier work")

        async def handle_muted(self, envelope, context):  # type: ignore[no-untyped-def]
            return []

    deps = Silent(muted=True)

    result = await run_turn(make_envelope(), deps)

    assert result.outbound == []
    assert deps.fallbacks == []
    assert deps.emitted == []
    assert deps.compose_calls == 0


@pytest.mark.asyncio
async def test_failed_verification_regenerates_once_then_emits() -> None:
    deps = FakeDependencies(regen_once=True)

    result = await run_turn(make_envelope(), deps)

    assert deps.compose_calls == 2
    assert deps.verify_calls == 2
    assert result.regen_count == 1
    assert len(deps.emitted) == 1
    # The second draft is checked with the first rejection's issues in state.
    assert deps.verify_seen == [[], ["claims stock not in facts"]]
    assert result.verify_issues == []


@pytest.mark.asyncio
async def test_ungrounded_number_never_auto_sends_regens_once_then_fails_safe() -> None:
    deps = FakeDependencies(always_fail_verify=True)

    result = await run_turn(make_envelope(), deps)

    assert deps.compose_calls == 2
    assert deps.verify_calls == 2
    assert result.regen_count == 1
    # Never auto-sends unverified draft
    assert deps.emitted == []
    # Routes to fail-safe template reply and human escalation
    assert len(result.outbound) == 1
    assert result.outbound[0].body == "A team member will help you."
    assert deps.fallbacks == ["draft_verification_failed"]


@pytest.mark.asyncio
async def test_coordinator_supports_sync_checkpointing() -> None:
    deps = FakeDependencies()
    saver = InMemorySaver()

    result = await run_turn(make_envelope(), deps, checkpointer=saver)

    assert result.outbound == deps.emitted
    assert len(deps.emitted) == 1


@pytest.mark.asyncio
async def test_approve_verdict_holds_the_reply_instead_of_emitting() -> None:
    deps = FakeDependencies()
    deps.gate_decision = "approve"

    result = await run_turn(make_envelope(), deps)

    assert deps.held == 1
    assert deps.emitted == []
    assert deps.finalized == 1
    assert result.outbound == []


@pytest.mark.asyncio
async def test_reject_verdict_is_shadow_mode_and_sends_nothing() -> None:
    deps = FakeDependencies()
    deps.gate_decision = "reject"

    await run_turn(make_envelope(), deps)

    assert deps.held == 0
    assert deps.emitted == []
    assert deps.finalized == 1


def _loop_deps(*steps: list[Task]) -> FakeDependencies:
    deps = FakeDependencies(parallel=True)
    deps.steps = [list(step) for step in steps]
    return deps


@pytest.mark.asyncio
async def test_agent_loop_runs_read_only_follow_up_then_stops() -> None:
    deps = _loop_deps([Task(task_id="x", agent="alpha", input={"query": "more"})])

    result = await run_turn(make_envelope("What is this product?"), deps)

    assert deps.executed_tasks == ["alpha", "beta", "alpha"]
    assert deps.observed == 2
    assert result.step == 2
    assert result.loop_stop == "done"
    assert [t.task_id for t in result.loop_tasks] == ["s1-0-alpha"]
    assert "s1-0-alpha:answer" in {fact.id for fact in result.facts}
    # Facts and failures from the first round are merged once, not again per round.
    assert len([f for f in result.facts if f.id == "alpha-1:answer"]) == 1
    assert len(deps.emitted) == 1


@pytest.mark.asyncio
async def test_agent_loop_never_runs_a_non_read_task() -> None:
    deps = _loop_deps([Task(task_id="x", agent="beta", input={"write": True})])
    deps.risks = {"beta": "commit"}

    result = await run_turn(make_envelope("What is this product?"), deps)

    assert deps.executed_tasks == ["alpha", "beta"]
    assert result.loop_stop == "rejected:not_read_only"
    assert len(deps.emitted) == 1


@pytest.mark.asyncio
async def test_agent_loop_rejects_repeat_and_unknown_tasks() -> None:
    deps = _loop_deps(
        [
            Task(task_id="x", agent="alpha", input={}),
            Task(task_id="y", agent="not_registered", input={}),
        ]
    )

    result = await run_turn(make_envelope("What is this product?"), deps)

    assert deps.executed_tasks == ["alpha", "beta"]
    assert result.loop_stop == "rejected:duplicate"


@pytest.mark.asyncio
async def test_agent_loop_stops_at_hard_step_ceiling() -> None:
    from csa.graph.coordinator import LOOP_MAX_STEPS

    deps = _loop_deps(*[[Task(task_id="x", agent="alpha", input={"n": n})] for n in range(10)])

    result = await run_turn(make_envelope("What is this product?"), deps)

    assert deps.observed == LOOP_MAX_STEPS
    assert len(deps.executed_tasks) == 2 + LOOP_MAX_STEPS
    assert result.step == LOOP_MAX_STEPS
    assert len(deps.emitted) == 1


@pytest.mark.asyncio
async def test_agent_loop_observe_failure_still_replies() -> None:
    deps = _loop_deps()
    deps.fail_observe = True

    result = await run_turn(make_envelope("What is this product?"), deps)

    assert result.loop_stop == "observe_failed:RuntimeError"
    assert deps.fallbacks == []
    assert len(deps.emitted) == 1


@pytest.mark.asyncio
async def test_agent_loop_skips_fast_path_turns() -> None:
    deps = FakeDependencies()

    await run_turn(make_envelope("hello"), deps)

    assert deps.observed == 0


def test_timeline_summary_reports_loop_decisions() -> None:
    from csa.graph.events import summarize

    _, summary = summarize(
        "observe",
        {
            "step": 1,
            "pending_tasks": [Task(task_id="s1-0-alpha", agent="alpha", input={})],
            "loop_stop": None,
        },
    )
    assert summary == {"next_tasks": ["alpha"], "step": 1}
    _, stopped = summarize("observe", {"step": 2, "pending_tasks": [], "loop_stop": "done"})
    assert stopped == {"next_tasks": [], "step": 2, "loop_stop": "done"}
