"""Deterministic precedence rules for coordinator routing (LG04)."""

from __future__ import annotations

import re
from typing import Literal

from csa.contracts.state import TurnState

Route = Literal[
    "muted",
    "callback",
    "empty",
    "voice_failed",
    "hard_escalate",
    "guard_blocked",
    "unsupported_language",
    "chitchat",
    "plan_args",
    "clarify",
]

_HARD_ESCALATE = re.compile(
    r"(?:\blawyer\b|\battorney\b|\blegal\b|\bsue\b|\blawsuit\b|\bcourt\b|"
    r"\bpolice\b|\bpeguam\b|\bmahkamah\b|\bsaman\b|律師|律师|法律|起诉|"
    r"வழக்கறிஞர்|நீதிமன்றம்)",
    re.IGNORECASE,
)
_ACCOUNT_INTENTS = {
    "order",
    "refund_returns",
    "billing_invoice",
    "booking",
    "subscription",
    "warranty",
}


def route_turn(state: TurnState) -> Route:
    """Choose a route using policy precedence, independent of any model."""
    context = state.ctx
    flags = context.flags if context is not None else {}
    if flags.get("human_muted", False):
        return "muted"
    if state.envelope.callback is not None:
        return "callback"

    message = state.envelope.message
    if not message.body_raw.strip() and not message.attachments and message.voice is None:
        return "empty"
    voice = message.voice
    if flags.get("voice_failed", False) or (
        voice is not None and not (voice.transcript or "").strip()
    ):
        return "voice_failed"
    if _HARD_ESCALATE.search(message.body_raw):
        return "hard_escalate"
    if state.guard is not None and not state.guard.allowed:
        return "guard_blocked"
    if flags.get("unsupported_language", False):
        return "unsupported_language"

    signals = state.signals
    if signals is not None and signals.answers.get("injection") is True:
        return "guard_blocked"
    if signals is None or signals.intent is None:
        return "clarify"
    if signals.intent.value == "chitchat":
        return "chitchat"
    if signals.answers.get("wants_human") is True:
        return "hard_escalate"
    if signals.answers.get("sentiment") == "angry":
        return "hard_escalate"
    if any(score > 0 for score in signals.capability_scores.values()):
        return "plan_args"
    if signals.intent.value in _ACCOUNT_INTENTS:
        return "plan_args"
    return "clarify"
