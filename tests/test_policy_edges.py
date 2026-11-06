"""Policy precedence and boundary cases independent of models and Postgres."""

from __future__ import annotations

import pytest
from csa.contracts.facts import AgentError, AgentResult, Fact
from csa.contracts.plan import Intent, Plan, Task
from csa.contracts.state import Context, TurnState
from csa.graph.policy.handoff import decide_handoff
from csa.graph.policy.plan_rules import (
    resolve_order_status,
    tiktok_stock_fallback,
    validate_plan,
)
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


def _unavailable(agent: str) -> AgentResult:
    result = make_result(Task(task_id="t", agent=agent, input={}), error=True)
    assert result.error is not None
    result.error = AgentError(
        code="capability_unavailable",
        category="validation",
        message="Capability is missing or disabled.",
    )
    return result


def _plan() -> Plan:
    return Plan(
        tasks=[], reply_strategy="auto_reply", intent=Intent.OTHER, confidence=1, source="policy"
    )


@pytest.mark.parametrize("agent", ["kb_agent", "order_agent"])
def test_a_question_only_a_switched_off_service_could_answer_goes_to_staff(agent: str) -> None:
    handoff = decide_handoff(_plan(), [_unavailable(agent)], context=None, signals=None)
    assert handoff is not None and handoff.required
    assert handoff.reason == "service_off"


def test_a_routine_question_for_staff_is_not_high_priority() -> None:
    handoff = decide_handoff(_plan(), [_unavailable("kb_agent")], context=None, signals=None)
    assert handoff is not None and handoff.priority == "normal"
    urgent = make_signals(Intent.OTHER, answers={"urgency": "urgent"})
    handoff = decide_handoff(_plan(), [_unavailable("kb_agent")], context=None, signals=urgent)
    assert handoff is not None and handoff.priority == "urgent"


def test_an_answer_from_another_agent_is_not_thrown_away() -> None:
    answered = make_result(Task(task_id="o", agent="order_agent", input={}))
    results = [answered, _unavailable("kb_agent")]
    assert decide_handoff(_plan(), results, context=None, signals=None) is None


def test_a_hard_failure_stays_an_agent_failure() -> None:
    failed = make_result(Task(task_id="o", agent="order_agent", input={}), error=True)
    results = [failed, _unavailable("kb_agent")]
    handoff = decide_handoff(_plan(), results, context=None, signals=None)
    assert handoff is not None and handoff.reason == "agent_failure"
    assert handoff.priority == "high"


# -- TikTok routing (spec 6.7): order numbers to ecommerce_agent, stock local first -----------


def _plan_of(*tasks: Task) -> Plan:
    return Plan(
        tasks=list(tasks),
        reply_strategy="auto_reply",
        intent=Intent.ORDER,
        confidence=1,
        source="llm",
    )


def _stock_check(query: str, task_id: str = "t0-ecommerce_agent") -> Task:
    return Task(
        task_id=task_id, agent="ecommerce_agent", input={"action": "stock_check", "query": query}
    )


def _search(query: str, task_id: str = "t0-order_agent") -> Task:
    return Task(
        task_id=task_id, agent="order_agent", input={"action": "search_products", "query": query}
    )


AGENTS = frozenset({"order_agent", "ecommerce_agent"})


def test_a_planned_tiktok_stock_check_becomes_a_local_search() -> None:
    plan, errors = validate_plan(
        _plan_of(_stock_check("mug")), known_agents=AGENTS, clarify_count=0
    )
    assert not errors
    assert [(t.agent, t.input) for t in plan.tasks] == [
        ("order_agent", {"action": "search_products", "query": "mug"})
    ]
    # Planned next to the same local search, it is simply dropped.
    plan, _ = validate_plan(
        _plan_of(_search("mug"), _stock_check("mug", "t1-ecommerce_agent")),
        known_agents=AGENTS,
        clarify_count=0,
    )
    assert [(t.task_id, t.agent) for t in plan.tasks] == [("t0-order_agent", "order_agent")]
    # An order-number lookup is left alone.
    lookup = Task(
        task_id="t0",
        agent="ecommerce_agent",
        input={"action": "order_status", "order_number": "576461413038785752"},
    )
    plan, _ = validate_plan(_plan_of(lookup), known_agents=AGENTS, clarify_count=0)
    assert plan.tasks == [lookup]


def _no_products(task: Task) -> AgentResult:
    result = make_result(task)
    result.data.facts = [
        Fact(id="no_products", text="No matching products were found.", source="order_agent")
    ]
    return result


def _not_found(task: Task) -> AgentResult:
    result = make_result(task, error=True)
    result.error = AgentError(code="product_not_found", category="not_found", message="x")
    return result


def _state(tasks: list[Task], results: list[AgentResult], loop: list[Task] | None = None):
    return TurnState(
        envelope=make_envelope("is it in stock?"),
        plan=_plan_of(*tasks),
        results=results,
        loop_tasks=loop or [],
    )


def test_tiktok_stock_is_asked_only_after_the_local_catalogue_found_nothing() -> None:
    search = _search("  mug  ")
    (follow_up,) = tiktok_stock_fallback(_state([search], [_no_products(search)]))
    assert follow_up.agent == "ecommerce_agent"
    assert follow_up.input == {"action": "stock_check", "query": "mug"}

    detail = Task(
        task_id="t1-order_agent",
        agent="order_agent",
        input={"action": "product_detail", "sku": "MUG-BLK-M"},
    )
    (follow_up,) = tiktok_stock_fallback(_state([detail], [_not_found(detail)]))
    assert follow_up.input == {"action": "stock_check", "query": "MUG-BLK-M"}

    # A local answer, a failed search and another agent's empty answer stay local.
    assert tiktok_stock_fallback(_state([search], [make_result(search)])) == []
    assert tiktok_stock_fallback(_state([search], [make_result(search, error=True)])) == []
    other = Task(task_id="t2-kb_agent", agent="kb_agent", input={"action": "query", "query": "x"})
    assert tiktok_stock_fallback(_state([other], [_no_products(other)])) == []
    # Asked once only.
    asked = _stock_check("mug", "s1-0-ecommerce_agent")
    assert tiktok_stock_fallback(_state([search], [_no_products(search)], [asked])) == []


@pytest.mark.parametrize(
    ("body", "deferred"),
    [
        ("where is my order 576461413038785752", True),
        ("track my order #5764614130387857", True),
        ("where is my order", False),
        ("where is my order A1B2C3D4E5", False),
        ("where is my order 0123456789", False),
    ],
)
def test_a_tiktok_order_number_goes_to_the_planner(body: str, deferred: bool) -> None:
    resolved = resolve_order_status(
        task_id="order-status",
        body=body,
        context=Context(active_order_ids=["A1B2C3D4E5"]),
        signals=make_signals(Intent.ORDER),
    )
    assert (resolved is None) is deferred


@pytest.mark.parametrize(
    "raw",
    [
        "where is my order 576461413038785750",  # passes the card check: [CARD_0]
        "track my order 5764 6141 3038 7857 50",
        "where is my order 600000000000000017",  # looks like a Malaysian phone: [PHONE_0]
    ],
)
def test_a_masked_order_number_is_never_answered_with_the_local_order(raw: str) -> None:
    from csa.core.pii import redact

    body = redact(raw)
    assert "[CARD_0]" in body or "[PHONE_0]" in body
    resolved = resolve_order_status(
        task_id="order-status",
        body=body,
        context=Context(active_order_ids=["A1B2C3D4E5"]),
        signals=make_signals(Intent.ORDER),
    )
    assert resolved is None
