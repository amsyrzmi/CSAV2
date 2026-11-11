"""Pure plan validation and deterministic order-status resolution (LG04)."""

from __future__ import annotations

import re

from csa.contracts.facts import AgentError
from csa.contracts.plan import Intent, Plan, Task
from csa.contracts.state import Context, Signals
from csa.core.pii import NUMBER_PLACEHOLDER

_ORDER_STATUS = re.compile(
    r"\b(?:where(?:'s| is)?\s+(?:my\s+)?order|"
    r"(?:my\s+)?order\s+(?:status|tracking|track)|track\s+(?:my\s+)?order)\b",
    re.IGNORECASE,
)
# A TikTok order number is a long run of digits; local order ids are 10 hex characters. A
# status question carrying one goes to the planner (ecommerce_agent order_status), not to the
# contact's own local orders.
_MARKETPLACE_ORDER_NUMBER = re.compile(r"(?<!\d)\d{14,}(?!\d)")
# The fast path reads redacted text: a long number that looks like a card or a phone number
# arrives as a placeholder (csa.core.pii), so it defers on those too.
_NUMBER_PLACEHOLDER = re.compile(NUMBER_PLACEHOLDER)
_ACCOUNT_INTENTS = {
    Intent.ORDER,
    Intent.REFUND_RETURNS,
    Intent.BILLING_INVOICE,
    Intent.BOOKING,
    Intent.SUBSCRIPTION,
    Intent.WARRANTY,
}
_CONFIDENCE_FLOOR = 0.35

# The escalation ladder. ``conversations.clarify_count`` counts unresolved turns in a row: below
# PARTIAL_AT the bot asks a clarifying question, at PARTIAL_AT it answers in part with one
# targeted question, from HANDOFF_AT on it hands the chat to staff.
LADDER_REASON = "unresolved_turns"
PARTIAL_AT = 2
HANDOFF_AT = 3
UNRESOLVED = frozenset({"ask_clarifying", "partial_answer"})


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
    if _MARKETPLACE_ORDER_NUMBER.search(body) or _NUMBER_PLACEHOLDER.search(body):
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

    if plan.reply_strategy == "ask_clarifying":
        if clarify_count >= HANDOFF_AT:
            plan = plan.model_copy(
                update={
                    "reply_strategy": "escalate",
                    "source": "policy",
                    "handoff_reason": LADDER_REASON,
                }
            )
        elif clarify_count >= PARTIAL_AT:
            plan = plan.model_copy(update={"reply_strategy": "partial_answer"})
    return plan, errors


def next_unresolved(plan: Plan | None, current: int) -> int:
    """The unresolved-turns count after a turn that ended with ``plan``.

    A question or a partial answer adds one; small talk and an ignored message keep the count
    (they neither resolve nor fail the customer); any other turn resolved something and resets.
    """
    if plan is None:
        return 0
    if plan.reply_strategy in UNRESOLVED:
        return current + 1
    if plan.reply_strategy == "ignore" or (
        plan.reply_strategy == "auto_reply" and plan.intent == Intent.CHITCHAT
    ):
        return current
    return 0


def _plan_error(code: str, message: str) -> AgentError:
    return AgentError(
        code=code,
        category="validation",
        message=message,
        retryable=False,
        customer_safe_hint="I’m connecting you with a team member to help.",
    )
