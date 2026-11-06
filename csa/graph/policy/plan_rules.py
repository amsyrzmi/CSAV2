"""Pure plan validation and deterministic order-status resolution (LG04)."""

from __future__ import annotations

import re

from csa.contracts.facts import AgentError
from csa.contracts.plan import Intent, Plan, Task
from csa.contracts.state import Context, Signals, TurnState
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
_ECOMMERCE = "ecommerce_agent"
_STOCK_CHECK = "stock_check"
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
    plan = local_stock_first(plan)
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


def local_stock_first(plan: Plan) -> Plan:
    """A planned TikTok stock check becomes a local catalogue search (spec 6.7).

    The client's own products answer stock first; the TikTok copy is only asked by
    ``tiktok_stock_fallback`` once the local search found nothing.
    """
    tasks: list[Task] = []
    changed = False
    for task in plan.tasks:
        if task.agent == _ECOMMERCE and task.input.get("action") == _STOCK_CHECK:
            changed = True
            query = task.input.get("query")
            local = {"action": "search_products", "query": query}
            if (
                isinstance(query, str)
                and query.strip()
                and not any(
                    t.agent == "order_agent" and t.input == local for t in [*plan.tasks, *tasks]
                )
            ):
                tasks.append(task.model_copy(update={"agent": "order_agent", "input": local}))
            continue
        tasks.append(task)
    return plan.model_copy(update={"tasks": tasks}) if changed else plan


def tiktok_stock_fallback(state: TurnState) -> list[Task]:
    """TikTok stock checks for the local product lookups that found nothing (spec 6.7).

    A ``search_products`` whose only answer was "no matching products", or a
    ``product_detail`` that answered ``product_not_found``, asks the TikTok copy with the same
    words, once. Everything the local catalogue answered stays local.
    """
    ran = [*(state.plan.tasks if state.plan else []), *state.loop_tasks]
    by_id = {task.task_id: task for task in ran}
    asked = {
        str(task.input.get("query"))
        for task in ran
        if task.agent == _ECOMMERCE and task.input.get("action") == _STOCK_CHECK
    }
    follow_ups: list[Task] = []
    for result in state.results:
        task = by_id.get(result.task_id)
        if task is None or task.agent != "order_agent":
            continue
        action = task.input.get("action")
        words: object = None
        if action == "search_products" and result.ok:
            if result.facts and all(fact.id == "no_products" for fact in result.facts):
                words = task.input.get("query")
        elif action == "product_detail" and result.error is not None:
            if result.error.code == "product_not_found":
                words = task.input.get("sku")
        if not isinstance(words, str) or not words.strip():
            continue
        query = words.strip()[:100]
        if query in asked:
            continue
        asked.add(query)
        follow_ups.append(
            Task(
                task_id=f"tiktok-stock-{len(follow_ups)}",
                agent=_ECOMMERCE,
                input={"action": _STOCK_CHECK, "query": query},
            )
        )
    return follow_ups


def _plan_error(code: str, message: str) -> AgentError:
    return AgentError(
        code=code,
        category="validation",
        message=message,
        retryable=False,
        customer_safe_hint="I’m connecting you with a team member to help.",
    )
