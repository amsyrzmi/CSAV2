"""Pure send/hold/suppress policy for a verified draft (LG07 §3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from csa.contracts.envelope import Channel, ContractModel
from csa.contracts.plan import Intent
from csa.contracts.state import GateVerdict, TurnState

POLICY_NAME = "lg07-gate-v1"
FAILURE_PENALTY = 0.25


class AutoSend(ContractModel):
    default: bool = True
    by_intent: dict[Intent, float] = Field(default_factory=dict)


ReviewFlag = Literal["first_contact", "partial_result", "requires_human", "email_channel"]


class Policy(ContractModel):
    """``clients.config.policy``: documented defaults, unknown keys rejected."""

    deployment_mode: Literal["shadow", "suggest", "auto"] = "auto"
    auto_send: AutoSend = Field(default_factory=AutoSend)
    never_auto: list[Intent] = Field(default_factory=list)
    always_review_if: list[ReviewFlag] = Field(default_factory=list)
    approval_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    # Read-only follow-up rounds the agent loop may run per turn (0 disables the loop).
    agent_loop_max_steps: int = Field(default=2, ge=0, le=3)
    # Model check that a draft only claims what the facts support (LG08).
    semantic_verify: bool = True
    # Classify provider (LG06): llm | shadow (log Jev beside the LLM) | cascade (Jev, LLM on
    # error or low confidence) | jev. Jev modes need TYPESAFE_API_KEY.
    decide_mode: Literal["llm", "shadow", "cascade", "jev"] = "llm"
    jev_min_confidence: float = Field(default=0.5, ge=0, le=1)


def confidence(state: TurnState) -> float:
    """Plan confidence, reduced when any spoke failed or returned an error."""
    base = state.plan.confidence if state.plan is not None else 0.0
    return max(0.0, base - (FAILURE_PENALTY if state.failures else 0.0))


def evaluate(policy: Policy, state: TurnState) -> GateVerdict:
    score = confidence(state)
    risk = round(1 - score, 2)

    def verdict(decision: Literal["send", "approve", "reject"], reason: str) -> GateVerdict:
        return GateVerdict(decision=decision, risk_score=risk, reason=reason, policy=POLICY_NAME)

    if policy.deployment_mode == "shadow":
        return verdict("reject", "shadow_mode")
    plan = state.plan
    # Acknowledging a handoff and answering button taps must never wait for a human.
    if state.envelope.callback is not None or (plan and plan.reply_strategy == "escalate"):
        return verdict("send", "system_reply")
    if policy.deployment_mode == "suggest":
        return verdict("approve", "suggest_mode")

    intent = plan.intent if plan is not None else Intent.OTHER
    if intent in policy.never_auto:
        return verdict("approve", f"never_auto:{intent.value}")
    flags = {
        "first_contact": state.envelope.meta.is_first_contact,
        "partial_result": bool(state.failures),
        "requires_human": state.handoff is not None and state.handoff.required,
        "email_channel": state.envelope.channel == Channel.EMAIL,
    }
    for name in policy.always_review_if:
        if flags[name]:
            return verdict("approve", f"always_review:{name}")
    threshold = policy.auto_send.by_intent.get(intent)
    if threshold is None:
        if not policy.auto_send.default:
            return verdict("approve", "auto_send_disabled")
    elif score < threshold:
        return verdict("approve", f"low_confidence:{score:.2f}<{threshold:.2f}")
    return verdict("send", "auto")
