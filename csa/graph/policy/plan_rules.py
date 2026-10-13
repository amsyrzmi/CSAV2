"""Pure plan validation and deterministic order-status resolution (LG04)."""

from __future__ import annotations

import re

from csa.contracts.facts import AgentError
from csa.contracts.plan import Intent, Plan, Task
from csa.contracts.state import Context, Signals

_ORDER_STATUS = re.compile(
    r"\b(?:where(?:'s| is)?\s+(?:my\s+)?order|"
    r"(?:my\s+)?order\s+(?:status|tracking|track)|track\s+(?:my\s+)?order)\b",
    re.IGNORECASE,
)
_ACCOUNT_INTENTS = {
    Intent.ORDER,
    Intent.REFUND_RETURNS,
    Intent.BILLING_INVOICE,
    Intent.BOOKING,
    Intent.SUBSCRIPTION,
    Intent.WARRANTY,
}
_CONFIDENCE_FLOOR = 0.35


def resolve_order_status(
    *,
    task_id: str,
    body: str,
    context: Context,
    signals: Signals,
) -> Plan | None:
    """Resolve common status questions from context before invoking a planner."""
    if signals.intent != Intent.ORDER or not _ORDER_STATUS.search(body):
        return None

    if len(context.active_order_ids) == 1:
        task = Task(
            task_id=task_id,
            agent="order_agent",
            input={"action": "status", "order_id": context.active_order_ids[0]},
        )
        return Plan(
            tasks=[task],
            reply_strategy="auto_reply",
            intent=Intent.ORDER,
            confidence=1,
            source="policy",
        )
    if len(context.active_order_ids) > 1:
        task = Task(task_id=task_id, agent="order_agent", input={"action": "status"})
        return Plan(
            tasks=[task],
            reply_strategy="auto_reply",
            intent=Intent.ORDER,
            confidence=1,
            source="policy",
        )

    return Plan(
        tasks=[],
        reply_strategy="auto_reply",
        intent=Intent.ORDER,
        confidence=1,
        source="policy",
    )


def validate_plan(
    plan: Plan,
    *,
    known_agents: frozenset[str],
    clarify_count: int,
) -> tuple[Plan, list[AgentError]]:
    """Validate task routing and required inputs without executing any agent."""
    errors: list[AgentError] = []
    task_ids: set[str] = set()
    for task in plan.tasks:
        if task.task_id in task_ids:
            errors.append(_plan_error("duplicate_task_id", f"Duplicate task id: {task.task_id}"))
        task_ids.add(task.task_id)

        if task.agent not in known_agents:
            errors.append(_plan_error("unknown_agent", f"Unknown agent: {task.agent}"))
            continue

    if errors:
        plan = plan.model_copy(
            update={
                "tasks": [],
                "reply_strategy": "escalate",
                "source": "policy",
            }
        )
    elif plan.confidence < _CONFIDENCE_FLOOR and plan.reply_strategy == "auto_reply":
        plan = plan.model_copy(
            update={
                "tasks": [],
                "reply_strategy": "ask_clarifying",
                "source": "policy",
            }
        )

    if plan.intent in _ACCOUNT_INTENTS and not plan.tasks:
        if plan.reply_strategy == "auto_reply" and plan.intent == Intent.ORDER:
            return plan, errors
        plan = plan.model_copy(update={"reply_strategy": "ask_clarifying"})

    if plan.reply_strategy == "ask_clarifying" and clarify_count >= 1:
        plan = plan.model_copy(update={"reply_strategy": "escalate", "source": "policy"})
    return plan, errors


def _plan_error(code: str, message: str) -> AgentError:
    return AgentError(
        code=code,
        category="validation",
        message=message,
        retryable=False,
        customer_safe_hint="I’m connecting you with a team member to help.",
    )
