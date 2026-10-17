"""Checkpointed per-turn coordinator graph (LG04)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from csa.contracts.envelope import Envelope
from csa.contracts.facts import (
    AgentError,
    AgentResult,
    AgentResultData,
    Fact,
    ResultMeta,
    Ui,
)
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
from csa.graph.events import EventSink, traced
from csa.graph.policy.handoff import decide_handoff
from csa.graph.policy.plan_rules import resolve_order_status, validate_plan
from csa.graph.policy.routing import Route, route_turn

logger = structlog.get_logger(__name__)

_NODE_TIMEOUTS = {
    "load_ctx": 2.0,
    "guard": 3.0,
    "signals": 8.0,
    "plan_args": 8.0,
    "compose": 10.0,
    "spoke": 20.0,
    "observe": 6.0,
}

# Hard ceilings for the agent loop; tenants can only lower them.
LOOP_MAX_STEPS = 3
LOOP_MAX_TASKS = 6
LOOP_STEP_TASKS = 2


class CoordinatorDependencies(Protocol):
    """Injectable providers and side-effect boundaries used by the graph."""

    known_agents: frozenset[str]

    async def load_context(self, envelope: Envelope) -> Context: ...

    async def guard(self, envelope: Envelope, context: Context) -> GuardResult: ...

    async def signals(self, envelope: Envelope, context: Context) -> Signals: ...

    async def plan_args(
        self,
        envelope: Envelope,
        context: Context,
        signals: Signals,
    ) -> Plan: ...

    async def resolve_callback(self, envelope: Envelope, context: Context) -> Task | None: ...

    async def next_step(self, state: TurnState) -> Sequence[Task]:
        """Read-only follow-up tasks after observing results; empty means done."""
        ...

    def task_risk(self, task: Task) -> RiskClass | None: ...

    async def execute_spoke(
        self,
        task: Task,
        envelope: Envelope,
        context: Context,
    ) -> AgentResult: ...

    async def handle_muted(
        self,
        envelope: Envelope,
        context: Context,
    ) -> Sequence[OutboundMessage]: ...

    async def compose(self, state: TurnState) -> Draft: ...

    async def verify(self, state: TurnState) -> Verification: ...

    async def gate(self, state: TurnState) -> GateVerdict: ...

    async def hold(self, state: TurnState) -> None: ...

    async def emit(self, state: TurnState) -> None: ...

    async def finalize(self, state: TurnState) -> None: ...

    async def fail_safe(
        self,
        envelope: Envelope,
        context: Context | None,
        error_code: str,
    ) -> OutboundMessage:
        """Atomically persist the escalation record and an idempotent fallback outbox row."""
        ...


def build_graph(
    deps: CoordinatorDependencies,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    events: EventSink | None = None,
) -> CompiledStateGraph:
    """Build the coordinator graph with injectable deciders and side effects.

    When ``events`` is given, every node execution appends a redacted timeline event.
    """

    async def load_ctx(state: TurnState) -> dict[str, object]:
        try:
            context = await _within(
                _NODE_TIMEOUTS["load_ctx"],
                deps.load_context(state.envelope),
            )
            return {"ctx": context, "node_error": None}
        except Exception as exc:
            return _node_failure("load_ctx", exc)

    async def guard(state: TurnState) -> dict[str, object]:
        try:
            if state.ctx is None:
                raise RuntimeError("context_missing")
            result = await _within(
                _NODE_TIMEOUTS["guard"],
                deps.guard(state.envelope, state.ctx),
            )
            return {"guard": result, "node_error": None}
        except Exception as exc:
            return _node_failure("guard", exc)

    async def signals(state: TurnState) -> dict[str, object]:
        try:
            if state.ctx is None:
                raise RuntimeError("context_missing")
            result = await _within(
                _NODE_TIMEOUTS["signals"],
                deps.signals(state.envelope, state.ctx),
            )
            return {"signals": result, "node_error": None}
        except Exception as exc:
            return _node_failure("signals", exc)

    def route(state: TurnState) -> dict[str, object]:
        if state.node_error is not None:
            return {"route": None}
        selected = route_turn(state)
        logger.info(
            "turn_routed",
            trace_id=str(state.envelope.trace_id),
            route=selected,
        )
        return {"route": selected}

    async def muted_turn(state: TurnState) -> dict[str, object]:
        try:
            if state.ctx is None:
                raise RuntimeError("context_missing")
            outbound = await deps.handle_muted(state.envelope, state.ctx)
            return {"outbound": list(outbound), "node_error": None}
        except Exception as exc:
            return _node_failure("muted_turn", exc)

    async def fast_plan(state: TurnState) -> dict[str, object]:
        try:
            plan = await _fast_plan(state, deps)
            return {"plan": plan, "node_error": None}
        except Exception as exc:
            return _node_failure("fast_plan", exc)

    async def plan_args(state: TurnState) -> dict[str, object]:
        try:
            if state.ctx is None or state.signals is None:
                raise RuntimeError("planning_context_missing")
            resolved = resolve_order_status(
                task_id="order-status",
                body=state.envelope.message.body_redacted,
                context=state.ctx,
                signals=state.signals,
            )
            if resolved is not None:
                updates: dict[str, object] = {"plan": resolved, "node_error": None}
                if not state.ctx.active_order_ids:
                    updates["ctx"] = state.ctx.model_copy(
                        update={
                            "customer_facts": [
                                *state.ctx.customer_facts,
                                Fact(
                                    id="no_active_orders",
                                    text="No active orders were found for this contact.",
                                    source="context.active_orders",
                                    kind="derived",
                                ),
                            ]
                        }
                    )
                return updates
            plan = await _within(
                _NODE_TIMEOUTS["plan_args"],
                deps.plan_args(state.envelope, state.ctx, state.signals),
            )
            return {"plan": plan, "node_error": None}
        except Exception as exc:
            return _node_failure("plan_args", exc)

    def validate(state: TurnState) -> dict[str, object]:
        if state.plan is None or state.ctx is None:
            return {"node_error": "validation_input_missing"}
        plan, errors = validate_plan(
            state.plan,
            known_agents=deps.known_agents,
            clarify_count=state.ctx.clarify_count,
        )
        return {
            "plan": plan,
            "failures": [*state.failures, *errors],
            "node_error": None,
        }

    async def run_spoke(state: TurnState) -> dict[str, object]:
        state = TurnState.model_validate(state)
        task = state.current_task
        if task is None or state.ctx is None:
            return {"results": [_spoke_failure(state, "spoke_input_missing")]}
        try:
            result = await _within(
                _NODE_TIMEOUTS["spoke"],
                deps.execute_spoke(task, state.envelope, state.ctx),
            )
            return {"results": [result]}
        except Exception as exc:
            logger.warning(
                "spoke_execution_failed",
                trace_id=str(state.envelope.trace_id),
                task_id=task.task_id,
                agent=task.agent,
                error_type=type(exc).__name__,
            )
            return {"results": [_spoke_failure(state, type(exc).__name__)]}

    def collect(state: TurnState) -> dict[str, object]:
        # The loop returns here after each round; only merge results not merged yet.
        if state.collected == 0:
            facts = list(state.ctx.customer_facts) if state.ctx is not None else []
            ui = Ui()
        else:
            facts, ui = list(state.facts), state.ui
        failures = list(state.failures)
        for result in state.results[state.collected :]:
            facts.extend(
                fact.model_copy(update={"id": f"{result.task_id}:{fact.id}"})
                for fact in result.facts
            )
            ui = _merge_ui(ui, result.ui)
            if result.error is not None:
                failures.append(result.error)
        return {
            "facts": facts,
            "ui": ui,
            "failures": failures,
            "collected": len(state.results),
            "node_error": None,
        }

    async def observe(state: TurnState) -> dict[str, object]:
        try:
            proposed = await _within(_NODE_TIMEOUTS["observe"], deps.next_step(state))
        except Exception as exc:
            # The turn already has results to answer with; a failed look-ahead only ends the loop.
            logger.warning(
                "agent_loop_observe_failed",
                trace_id=str(state.envelope.trace_id),
                error_type=type(exc).__name__,
            )
            return {"pending_tasks": [], "loop_stop": f"observe_failed:{type(exc).__name__}"}
        accepted, stop = _admit(state, list(proposed), deps)
        return {
            "step": state.step + 1,
            "pending_tasks": accepted,
            "loop_tasks": [*state.loop_tasks, *accepted],
            "loop_stop": stop,
        }

    async def handoff(state: TurnState) -> dict[str, object]:
        if state.plan is None:
            return {"node_error": "handoff_plan_missing"}
        decision = decide_handoff(
            state.plan,
            state.results,
            context=state.ctx,
            signals=state.signals,
        )
        if decision is None or not decision.required:
            return {"handoff": decision, "node_error": None}

        if state.ctx is None:
            return {"handoff": decision, "node_error": "handoff_context_missing"}
        escalation_task = Task(
            task_id="handoff-escalation",
            agent="escalation_agent",
            input={"reason": decision.reason, "priority": decision.priority},
        )
        try:
            result = await _within(
                _NODE_TIMEOUTS["spoke"],
                deps.execute_spoke(escalation_task, state.envelope, state.ctx),
            )
        except Exception as exc:
            result = _spoke_failure(state, type(exc).__name__, task=escalation_task)
        facts = [
            *state.facts,
            *(
                fact.model_copy(update={"id": f"{result.task_id}:{fact.id}"})
                for fact in result.facts
            ),
        ]
        failures = list(state.failures)
        if result.error is not None:
            failures.append(result.error)
        return {
            "handoff": decision,
            "results": [result],
            "facts": facts,
            "ui": _merge_ui(state.ui, result.ui),
            "failures": failures,
            "node_error": None,
        }

    async def compose(state: TurnState) -> dict[str, object]:
        try:
            if state.plan is None:
                raise RuntimeError("compose_plan_missing")
            draft = await _within(_NODE_TIMEOUTS["compose"], deps.compose(state))
            return {"draft": draft, "node_error": None}
        except Exception as exc:
            return _node_failure("compose", exc)

    async def verify(state: TurnState) -> dict[str, object]:
        try:
            if state.draft is None:
                raise RuntimeError("verify_draft_missing")
            result = await _within(_NODE_TIMEOUTS["compose"], deps.verify(state))
            if result.ok:
                return {"verified": True, "verify_issues": [], "node_error": None}
            if state.regen_count < 1:
                return {
                    "verified": False,
                    "verify_issues": result.issues,
                    "regen_count": state.regen_count + 1,
                    "node_error": None,
                }
            return {"verify_issues": result.issues, "node_error": "draft_verification_failed"}
        except Exception as exc:
            return _node_failure("verify", exc)

    async def gate(state: TurnState) -> dict[str, object]:
        try:
            if state.draft is None:
                if state.plan is not None and state.plan.reply_strategy == "ignore":
                    return {"verdict": None, "node_error": None}
                raise RuntimeError("gate_draft_missing")
            verdict = await deps.gate(state)
            return {"verdict": verdict, "node_error": None}
        except Exception as exc:
            return _node_failure("gate", exc)

    async def hold(state: TurnState) -> dict[str, object]:
        try:
            await deps.hold(state)
            return {"node_error": None}
        except Exception as exc:
            return _node_failure("hold", exc)

    async def emit(state: TurnState) -> dict[str, object]:
        try:
            if state.outbound:
                updated = state
            else:
                if state.draft is None or state.plan is None:
                    raise RuntimeError("emit_draft_missing")
                if state.envelope.contact.id is None:
                    raise RuntimeError("contact_id_missing")
                outbound = OutboundMessage(
                    trace_id=state.envelope.trace_id,
                    contact_id=state.envelope.contact.id,
                    channel=state.envelope.channel,
                    body=state.draft.text,
                    idempotency_key=f"{state.envelope.trace_id}:0",
                    ui=state.ui,
                )
                updated = state.model_copy(update={"outbound": [outbound]})
            await deps.emit(updated)
            return {"outbound": updated.outbound, "node_error": None}
        except Exception as exc:
            return _node_failure("emit", exc)

    async def finalize(state: TurnState) -> dict[str, object]:
        # The customer already has their reply, so a fallback would double-send.
        # Raise instead: the turn fails durably and resumes at finalize on retry.
        await deps.finalize(state)
        return {"node_error": None}

    async def fail_safe(state: TurnState) -> dict[str, object]:
        error_code = state.node_error or "unhandled_turn_failure"
        try:
            outbound = await deps.fail_safe(state.envelope, state.ctx, error_code)
            return {"outbound": [outbound], "node_error": None}
        except Exception as exc:
            logger.critical(
                "turn_fail_safe_failed",
                trace_id=str(state.envelope.trace_id),
                error_type=type(exc).__name__,
            )
            raise

    builder = StateGraph(TurnState)

    def add_node(name: str, fn: Any) -> None:
        builder.add_node(name, fn if events is None else traced(name, fn, events))  # type: ignore[call-overload]

    add_node("load_ctx", load_ctx)
    add_node("guard", guard)
    add_node("signals", signals)
    add_node("route", route)
    add_node("muted_turn", muted_turn)
    add_node("fast_plan", fast_plan)
    add_node("plan_args", plan_args)
    add_node("validate", validate)
    add_node("run_spoke", run_spoke)
    add_node("collect", collect)
    add_node("observe", observe)
    add_node("handoff", handoff)
    add_node("compose", compose)
    add_node("verify", verify)
    add_node("gate", gate)
    add_node("hold", hold)
    add_node("emit", emit)
    add_node("finalize", finalize)
    add_node("fail_safe", fail_safe)

    builder.add_edge(START, "load_ctx")
    builder.add_conditional_edges("load_ctx", _on_error("guard"), ["guard", "fail_safe"])
    builder.add_conditional_edges(
        "guard",
        lambda state: (
            "fail_safe"
            if state.node_error
            else ("route" if state.ctx and state.ctx.flags.get("human_muted") else "signals")
        ),
        ["signals", "route", "fail_safe"],
    )
    builder.add_conditional_edges("signals", _on_error("route"), ["route", "fail_safe"])
    builder.add_conditional_edges(
        "route",
        lambda state: "fail_safe" if state.node_error else _route_target(state.route),
        ["muted_turn", "fast_plan", "plan_args", "fail_safe"],
    )
    builder.add_conditional_edges(
        "muted_turn",
        lambda state: (
            "fail_safe" if state.node_error else ("emit" if state.outbound else "finalize")
        ),
        ["emit", "finalize", "fail_safe"],
    )
    builder.add_conditional_edges(
        "fast_plan",
        _on_error("validate"),
        ["validate", "fail_safe"],
    )
    builder.add_conditional_edges(
        "plan_args",
        _on_error("validate"),
        ["validate", "fail_safe"],
    )
    builder.add_conditional_edges(
        "validate",
        lambda state: "fail_safe" if state.node_error else _fan_out(state),
        ["run_spoke", "collect", "fail_safe"],
    )
    builder.add_edge("run_spoke", "collect")
    builder.add_conditional_edges("collect", _after_collect, ["observe", "handoff", "fail_safe"])
    builder.add_conditional_edges("observe", _after_observe, ["run_spoke", "handoff"])
    builder.add_conditional_edges("handoff", _after_handoff, ["compose", "finalize", "fail_safe"])
    builder.add_conditional_edges("compose", _on_error("verify"), ["verify", "fail_safe"])
    builder.add_conditional_edges(
        "verify",
        _after_verify,
        ["compose", "gate", "fail_safe"],
    )
    builder.add_conditional_edges(
        "gate",
        lambda state: "fail_safe" if state.node_error else _after_gate(state),
        ["emit", "hold", "finalize", "fail_safe"],
    )
    builder.add_conditional_edges("hold", _on_error("finalize"), ["finalize", "fail_safe"])
    builder.add_conditional_edges("emit", _on_error("finalize"), ["finalize", "fail_safe"])
    builder.add_conditional_edges("finalize", _end_or_fail_safe, ["fail_safe", END])
    builder.add_edge("fail_safe", END)
    return builder.compile(checkpointer=checkpointer)


async def run_turn(
    envelope: Envelope,
    deps: CoordinatorDependencies,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    graph: CompiledStateGraph | None = None,
) -> TurnState:
    """Run one event with trace-id checkpointing and durable side-effect writes.

    Pass a graph from ``build_graph`` built once at startup; compiling per turn costs ~100 ms.
    """
    if graph is None:
        graph = build_graph(deps, checkpointer=checkpointer)
    config: RunnableConfig = {"configurable": {"thread_id": str(envelope.trace_id)}}
    started = time.perf_counter()
    try:
        invoke = (
            graph.ainvoke(
                TurnState(envelope=envelope),
                config=config,
                durability="sync",
            )
            if checkpointer is not None
            else graph.ainvoke(TurnState(envelope=envelope), config=config)
        )
        result = await asyncio.wait_for(
            invoke,
            timeout=envelope.meta.turn_deadline_ms / 1000,
        )
        return TurnState.model_validate(result)
    except TimeoutError:
        fallback = await deps.fail_safe(envelope, None, "turn_deadline_exceeded")
        return TurnState(envelope=envelope, outbound=[fallback])
    finally:
        logger.info(
            "turn_graph_finished",
            trace_id=str(envelope.trace_id),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )


async def resume_turn(
    envelope: Envelope,
    deps: CoordinatorDependencies,
    *,
    checkpointer: BaseCheckpointSaver[Any],
    graph: CompiledStateGraph | None = None,
) -> TurnState:
    """Continue an interrupted turn from its last checkpoint (same trace_id thread)."""
    if graph is None:
        graph = build_graph(deps, checkpointer=checkpointer)
    config: RunnableConfig = {"configurable": {"thread_id": str(envelope.trace_id)}}
    result = await asyncio.wait_for(
        graph.ainvoke(None, config=config, durability="sync"),
        timeout=envelope.meta.turn_deadline_ms / 1000,
    )
    return TurnState.model_validate(result)


@dataclass(frozen=True)
class CoordinatorRuntime:
    """Coordinator graph compiled once at startup, with its dependencies and saver."""

    deps: CoordinatorDependencies
    checkpointer: BaseCheckpointSaver[Any]
    graph: CompiledStateGraph


def build_runtime(
    deps: CoordinatorDependencies,
    checkpointer: BaseCheckpointSaver[Any],
    events: EventSink | None = None,
) -> CoordinatorRuntime:
    return CoordinatorRuntime(
        deps=deps,
        checkpointer=checkpointer,
        graph=build_graph(deps, checkpointer=checkpointer, events=events),
    )


async def process_turn(
    envelope: Envelope,
    deps: CoordinatorDependencies,
    *,
    checkpointer: BaseCheckpointSaver[Any],
    graph: CompiledStateGraph | None = None,
) -> TurnState:
    """Start, resume, or return the already-finished turn for this trace_id."""
    if graph is None:
        graph = build_graph(deps, checkpointer=checkpointer)
    config: RunnableConfig = {"configurable": {"thread_id": str(envelope.trace_id)}}
    snapshot = await graph.aget_state(config)
    if snapshot.next:
        return await resume_turn(envelope, deps, checkpointer=checkpointer, graph=graph)
    if snapshot.values:
        return TurnState.model_validate(snapshot.values)
    return await run_turn(envelope, deps, checkpointer=checkpointer, graph=graph)


def _route_target(route: Route | None) -> str:
    if route == "muted":
        return "muted_turn"
    if route in {
        "callback",
        "empty",
        "voice_failed",
        "hard_escalate",
        "guard_blocked",
        "unsupported_language",
        "chitchat",
    }:
        return "fast_plan"
    if route == "plan_args":
        return "plan_args"
    if route == "clarify":
        return "fast_plan"
    return "fail_safe"


def _on_error(target: str):
    return lambda state: "fail_safe" if state.node_error else target


def _fan_out(state: TurnState) -> str | list[Send]:
    if state.plan is None or not state.plan.tasks:
        return "collect"
    return [
        Send(
            "run_spoke",
            {
                "envelope": state.envelope,
                "ctx": state.ctx,
                "current_task": task,
            },
        )
        for task in state.plan.tasks
    ]


def _loop_blocker(state: TurnState) -> str | None:
    """Why the agent loop must not observe this turn, or ``None`` if it may."""
    plan = state.plan
    if state.route != "plan_args" or plan is None or plan.reply_strategy != "auto_reply":
        return "not_planned"
    if not state.results:
        return "no_results"
    if state.loop_stop is not None:
        return state.loop_stop
    if state.step >= LOOP_MAX_STEPS:
        return "max_steps"
    if len(state.results) >= LOOP_MAX_TASKS:
        return "max_tasks"
    if state.ui.confirmation is not None or state.ui.options:
        return "awaiting_customer"
    if any(result.requires_human for result in state.results):
        return "requires_human"
    return None


def _after_collect(state: TurnState) -> str:
    if state.node_error:
        return "fail_safe"
    return "handoff" if _loop_blocker(state) else "observe"


def _after_observe(state: TurnState) -> str | list[Send]:
    if not state.pending_tasks:
        return "handoff"
    return [
        Send("run_spoke", {"envelope": state.envelope, "ctx": state.ctx, "current_task": task})
        for task in state.pending_tasks
    ]


def _task_key(task: Task) -> tuple[str, str]:
    payload = {k: v for k, v in task.input.items() if k != "callback_id"}
    return task.agent, json.dumps(payload, sort_keys=True, default=str)


def _admit(
    state: TurnState, proposed: list[Task], deps: CoordinatorDependencies
) -> tuple[list[Task], str | None]:
    """Keep only new, read-only follow-ups within budget; code decides, not the model."""
    if not proposed:
        return [], "done"
    seen = {_task_key(t) for t in [*(state.plan.tasks if state.plan else []), *state.loop_tasks]}
    budget = min(LOOP_STEP_TASKS, LOOP_MAX_TASKS - len(state.results))
    accepted: list[Task] = []
    rejected: list[str] = []
    for task in proposed:
        key = _task_key(task)
        if task.agent not in deps.known_agents or deps.task_risk(task) != "read":
            rejected.append("not_read_only")
        elif key in seen:
            rejected.append("duplicate")
        elif len(accepted) >= budget:
            rejected.append("over_budget")
        else:
            seen.add(key)
            accepted.append(
                task.model_copy(
                    update={"task_id": f"s{state.step + 1}-{len(accepted)}-{task.agent}"}
                )
            )
    if rejected:
        logger.info(
            "agent_loop_tasks_rejected",
            trace_id=str(state.envelope.trace_id),
            reasons=sorted(set(rejected)),
        )
    if accepted:
        return accepted, None
    return [], f"rejected:{sorted(set(rejected))[0]}"


def _after_handoff(state: TurnState) -> str:
    if state.node_error:
        return "fail_safe"
    if state.plan is not None and state.plan.reply_strategy == "ignore":
        return "finalize"
    return "compose"


def _end_or_fail_safe(state: TurnState) -> str:
    return "fail_safe" if state.node_error else END


async def _fast_plan(state: TurnState, deps: CoordinatorDependencies) -> Plan:
    route = state.route
    intent = state.signals.intent if state.signals and state.signals.intent else Intent.OTHER
    if route == "callback":
        if state.ctx is None:
            raise RuntimeError("callback_context_missing")
        task = await deps.resolve_callback(state.envelope, state.ctx)
        if task is None:
            return _escalation_plan(intent, "callback_action_unavailable")
        return Plan(
            tasks=[task],
            reply_strategy="auto_reply",
            intent=Intent.ORDER if task.agent == "order_agent" else intent,
            confidence=1,
            source="fast_path",
        )
    if route == "empty":
        return Plan(
            tasks=[],
            reply_strategy="ignore",
            intent=intent,
            confidence=1,
            source="fast_path",
        )
    if route in {"voice_failed", "hard_escalate", "guard_blocked", "unsupported_language"}:
        return _escalation_plan(intent, route)
    if route == "chitchat":
        return Plan(
            tasks=[],
            reply_strategy="auto_reply",
            intent=Intent.CHITCHAT,
            confidence=1,
            source="fast_path",
        )
    return Plan(
        tasks=[],
        reply_strategy="ask_clarifying",
        intent=intent,
        confidence=1,
        source="policy",
    )


def _after_verify(state: TurnState) -> str:
    if state.node_error:
        return "fail_safe"
    if state.verified is False:
        return "compose"
    return "gate"


def _after_gate(state: TurnState) -> str:
    if state.draft is None:
        return "finalize"
    decision = state.verdict.decision if state.verdict is not None else "send"
    # A rejected verdict is shadow mode: compute everything, send nothing.
    return {"send": "emit", "approve": "hold", "reject": "finalize"}[decision]


def _escalation_plan(intent: Intent, reason: str) -> Plan:
    del reason
    return Plan(
        tasks=[],
        reply_strategy="escalate",
        intent=intent,
        confidence=1,
        source="policy",
    )


def _spoke_failure(
    state: TurnState,
    error_code: str,
    *,
    task: Task | None = None,
) -> AgentResult:
    selected = task or state.current_task
    task_id = selected.task_id if selected is not None else "unknown-task"
    agent = selected.agent if selected is not None else "unknown-agent"
    return AgentResult(
        ok=False,
        status="error",
        task_id=task_id,
        agent=agent,
        error=AgentError(
            code="spoke_execution_failed",
            category="internal",
            message=error_code,
            retryable=False,
        ),
        data=AgentResultData(),
        meta=ResultMeta(trace_id=state.envelope.trace_id, latency_ms=0),
    )


def _merge_ui(left: Ui, right: Ui) -> Ui:
    options_by_action = {
        option.action_id or option.legacy_callback_data or option.label: option
        for option in [*left.options, *right.options]
    }
    confirmation = right.confirmation or left.confirmation
    return Ui(confirmation=confirmation, options=list(options_by_action.values()))


async def _within(timeout: float, awaitable):
    return await asyncio.wait_for(awaitable, timeout=timeout)


def _node_failure(node: str, error: Exception) -> dict[str, object]:
    logger.error(
        "turn_node_failed",
        node=node,
        error_type=type(error).__name__,
    )
    return {"node_error": f"{node}:{type(error).__name__}"}
