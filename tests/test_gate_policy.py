from __future__ import annotations

import uuid
from typing import Literal

import pytest
from csa.contracts.envelope import Callback, Channel
from csa.contracts.facts import AgentError
from csa.contracts.plan import Intent, Plan
from csa.contracts.state import Draft, Handoff, TurnState
from csa.graph.policy.gate import Policy, evaluate
from pydantic import ValidationError

from tests.test_coordinator import make_envelope


def _state(
    intent: Intent = Intent.PRODUCT_INFO,
    *,
    confidence: float = 0.9,
    strategy: Literal["auto_reply", "escalate"] = "auto_reply",
    failed: bool = False,
    handoff: bool = False,
) -> TurnState:
    return TurnState(
        envelope=make_envelope("hi"),
        plan=Plan(
            tasks=[], reply_strategy=strategy, intent=intent, confidence=confidence, source="policy"
        ),
        draft=Draft(text="ok", language="en"),
        failures=[AgentError(code="x", category="internal", message="boom")] if failed else [],
        handoff=Handoff(required=True, reason="r") if handoff else None,
    )


def _decide(policy: dict | None, state: TurnState) -> tuple[str, str]:
    verdict = evaluate(Policy.model_validate(policy or {}), state)
    return verdict.decision, verdict.reason


def test_default_policy_sends() -> None:
    assert _decide(None, _state()) == ("send", "auto")


def test_shadow_mode_sends_nothing() -> None:
    assert _decide({"deployment_mode": "shadow"}, _state()) == ("reject", "shadow_mode")


def test_suggest_mode_always_reviews_but_never_holds_handoff_acknowledgements() -> None:
    assert _decide({"deployment_mode": "suggest"}, _state()) == ("approve", "suggest_mode")
    assert _decide({"deployment_mode": "suggest"}, _state(strategy="escalate"))[0] == "send"


def test_never_auto_intent_is_held() -> None:
    decision, reason = _decide({"never_auto": ["refund_returns"]}, _state(Intent.REFUND_RETURNS))
    assert (decision, reason) == ("approve", "never_auto:refund_returns")


def test_low_confidence_is_held_and_threshold_met_is_sent() -> None:
    policy = {"auto_send": {"by_intent": {"product_info": 0.7}}}
    assert _decide(policy, _state(confidence=0.6))[0] == "approve"
    assert _decide(policy, _state(confidence=0.7))[0] == "send"


def test_auto_send_default_false_holds_unlisted_intents() -> None:
    policy = {"auto_send": {"default": False, "by_intent": {"order": 0.5}}}
    assert _decide(policy, _state(Intent.ORDER)) == ("send", "auto")
    assert _decide(policy, _state(Intent.PRODUCT_INFO)) == ("approve", "auto_send_disabled")


def test_agent_failure_lowers_confidence_and_triggers_partial_result_review() -> None:
    policy = {"auto_send": {"by_intent": {"product_info": 0.7}}}
    assert _decide(policy, _state(failed=True))[0] == "approve"
    review = {"always_review_if": ["partial_result"]}
    assert _decide(review, _state(failed=True)) == ("approve", "always_review:partial_result")
    assert _decide({"always_review_if": ["requires_human"]}, _state(handoff=True))[0] == "approve"


def test_email_channel_review_and_button_taps_bypass() -> None:
    state = _state()
    email = state.model_copy(
        update={"envelope": state.envelope.model_copy(update={"channel": Channel.EMAIL})}
    )
    assert _decide({"always_review_if": ["email_channel"]}, email)[0] == "approve"
    tap = state.model_copy(
        update={"envelope": make_envelope("", callback=Callback(action_id=uuid.uuid4()))}
    )
    assert _decide({"deployment_mode": "suggest"}, tap) == ("send", "system_reply")


def test_unknown_policy_keys_and_values_are_rejected() -> None:
    for bad in ({"deployment_mod": "auto"}, {"deployment_mode": "yolo"}, {"never_auto": ["nope"]}):
        with pytest.raises(ValidationError):
            Policy.model_validate(bad)


def test_first_contact_review() -> None:
    state = _state()
    first = state.model_copy(
        update={
            "envelope": state.envelope.model_copy(
                update={"meta": state.envelope.meta.model_copy(update={"is_first_contact": True})}
            )
        }
    )
    assert _decide({"always_review_if": ["first_contact"]}, first)[0] == "approve"
    assert _decide({"always_review_if": ["first_contact"]}, state)[0] == "send"
