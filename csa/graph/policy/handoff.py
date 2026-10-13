"""Deterministic escalation and handoff decisions (LG04)."""

from __future__ import annotations

from csa.contracts.facts import AgentResult
from csa.contracts.plan import Plan
from csa.contracts.state import Context, Handoff, Signals

_HARD_FAILURES = {"conflict", "dependency_unmet", "upstream", "internal"}


def decide_handoff(
    plan: Plan,
    results: list[AgentResult],
    *,
    context: Context | None,
    signals: Signals | None,
) -> Handoff | None:
    """Escalate explicit human requests, high-risk sentiment, and hard failures."""
    if signals is not None and signals.answers.get("wants_human") is True:
        reason = "human_requested"
    elif signals is not None and signals.answers.get("sentiment") == "angry":
        reason = "angry_customer"
    elif plan.reply_strategy == "escalate":
        reason = "plan_escalated"
    elif any(
        result.requires_human
        or result.error is not None
        and result.error.category in _HARD_FAILURES
        for result in results
    ):
        reason = "agent_failure"
    else:
        return None

    urgency = (
        "urgent" if (signals is not None and signals.answers.get("urgency") == "urgent") else "high"
    )
    if context is not None and context.flags.get("handoff_disabled", False):
        return Handoff(required=False, reason=f"{reason}:disabled", priority="normal")
    return Handoff(required=True, reason=reason, priority=urgency)
