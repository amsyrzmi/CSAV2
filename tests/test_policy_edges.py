"""Policy precedence and boundary cases independent of models and Postgres."""

from __future__ import annotations

import pytest
from csa.contracts.plan import Intent, Plan, Task
from csa.contracts.state import Context, TurnState
from csa.graph.policy.handoff import decide_handoff
from csa.graph.policy.plan_rules import validate_plan
from csa.graph.policy.routing import route_turn

from tests.test_coordinator import make_envelope, make_result, make_signals


@pytest.mark.parametrize(
    ("intent", "answers", "flags", "expected"),
    [
        (Intent.OTHER, {}, {"unsupported_language": True}, "unsupported_language"),
        (Intent.OTHER, {"injection": True}, {}, "guard_blocked"),
        (None, {}, {}, "clarify"),
        (Intent.OTHER, {"wants_human": True}, {}, "hard_escalate"),
        (Intent.OTHER, {"sentiment": "angry"}, {}, "hard_escalate"),
        (Intent.WARRANTY, {}, {}, "plan_args"),
        (Intent.OTHER, {}, {}, "clarify"),
    ],
)
def test_routes_without_capability_scores(intent, answers, flags, expected) -> None:
    signals = make_signals(intent) if intent is not None else None
    if signals is not None:
        signals.answers = answers
    state = TurnState(
        envelope=make_envelope("help"),
        ctx=Context(flags=flags),
        signals=signals,
    )
    assert route_turn(state) == expected


def test_missing_signals_clarifies() -> None:
    assert route_turn(TurnState(envelope=make_envelope("help"))) == "clarify"


def test_duplicate_tasks_are_rejected_before_execution() -> None:
    plan = Plan(
        tasks=[
            Task(task_id="same", agent="alpha", input={}),
            Task(task_id="same", agent="alpha", input={}),
        ],
        reply_strategy="auto_reply",
        intent=Intent.PRODUCT_INFO,
        confidence=1,
        source="policy",
    )
    result, errors = validate_plan(plan, known_agents=frozenset({"alpha"}), clarify_count=0)
    assert result.tasks == []
    assert result.reply_strategy == "escalate"
    assert [error.code for error in errors] == ["duplicate_task_id"]


def test_confidence_floor_and_account_intent_clarification() -> None:
    plan = Plan(
        tasks=[Task(task_id="t", agent="alpha", input={})],
        reply_strategy="auto_reply",
        intent=Intent.WARRANTY,
        confidence=0.34,
        source="policy",
    )
    result, errors = validate_plan(plan, known_agents=frozenset({"alpha"}), clarify_count=0)
    assert not errors and result.tasks == []
    assert result.reply_strategy == "ask_clarifying"
    boundary = plan.model_copy(update={"confidence": 0.35})
    result, errors = validate_plan(boundary, known_agents=frozenset({"alpha"}), clarify_count=0)
    assert not errors and result.tasks == boundary.tasks
    assert result.reply_strategy == "auto_reply"


def test_handoff_angry_and_disabled_policy() -> None:
    plan = Plan(
        tasks=[], reply_strategy="auto_reply", intent=Intent.OTHER, confidence=1, source="policy"
    )
    signals = make_signals(Intent.OTHER, answers={"sentiment": "angry"})
    handoff = decide_handoff(plan, [], context=Context(), signals=signals)
    assert handoff is not None and handoff.required
    assert handoff.reason == "angry_customer" and handoff.priority == "high"
    disabled = decide_handoff(
        plan, [], context=Context(flags={"handoff_disabled": True}), signals=signals
    )
    assert disabled is not None and not disabled.required
    assert disabled.reason == "angry_customer:disabled"


def test_handoff_prioritizes_human_request_over_agent_failure() -> None:
    plan = Plan(
        tasks=[], reply_strategy="escalate", intent=Intent.OTHER, confidence=1, source="policy"
    )
    result = make_result(Task(task_id="t", agent="alpha", input={}), error=True)
    signals = make_signals(Intent.OTHER, answers={"wants_human": True, "urgency": "urgent"})
    handoff = decide_handoff(plan, [result], context=None, signals=signals)
    assert handoff is not None and handoff.required
    assert handoff.reason == "human_requested" and handoff.priority == "urgent"
