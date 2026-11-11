"""Compact, typed turn state consumed by the LangGraph layer (LG02 §3)."""

from __future__ import annotations

import operator
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from csa.contracts.envelope import Channel, ContractModel, Envelope
from csa.contracts.facts import AgentError, AgentResult, Fact, Ui
from csa.contracts.plan import Intent, Plan, Task

RECENT_MESSAGES = 6
RECENT_CHARS = 300
# What every customer conversation carries (Context.recent): more messages, each clipped shorter.
RECENT_WINDOW = 20
RECENT_TEXT_CHARS = 200


class BookingType(ContractModel):
    """A service this customer may book, for the planner to name by its id."""

    id: UUID
    name: str = Field(max_length=120)
    minutes: int = Field(ge=1)


class BookingRef(ContractModel):
    """One of this customer's own upcoming bookings, for the planner to name by its id."""

    id: UUID
    name: str = Field(max_length=160)
    when: str = Field(max_length=80)
    status: Literal["pending", "confirmed"]


class RecentMessage(ContractModel):
    """An earlier message of this conversation: redacted text, which the customer controls."""

    author: Literal["customer", "assistant"]
    text: str = Field(max_length=RECENT_CHARS)


class BookingContext(ContractModel):
    """What the classifier and planner need to turn "3 pm please" into a booking.

    Only set for a company whose customers can book. `recent` is untrusted, like the message.
    """

    today: str = Field(max_length=40)
    timezone: str = Field(max_length=64)
    types: list[BookingType] = Field(default_factory=list, max_length=20)
    # True when the company offers more services than `types` holds (the list is cut at 20).
    more_types: bool = False
    upcoming: list[BookingRef] = Field(default_factory=list, max_length=10)
    recent: list[RecentMessage] = Field(default_factory=list, max_length=RECENT_MESSAGES)
    # The booking the last reply asked the customer to confirm cancelling, when this message is
    # their first since: a clear yes to it is a cancel with `confirmed`. The server checks it again.
    cancel_asked: UUID | None = None


class Context(ContractModel):
    summary: str | None = Field(default=None, max_length=4000)
    recent_turn_ids: list[UUID] = Field(default_factory=list, max_length=20)
    customer_facts: list[Fact] = Field(default_factory=list, max_length=50)
    active_order_ids: list[str] = Field(default_factory=list, max_length=10)
    clarify_count: int = Field(default=0, ge=0)
    flags: dict[str, bool] = Field(default_factory=dict)
    booking: BookingContext | None = None
    # The last messages of this conversation, oldest first, before the one being answered. Redacted
    # text, which the customer controls, so untrusted like the message itself.
    recent: list[RecentMessage] = Field(default_factory=list, max_length=RECENT_WINDOW)


class GuardResult(ContractModel):
    allowed: bool
    reason: str | None = None
    risk_flags: list[str] = Field(default_factory=list, max_length=20)


class Signals(ContractModel):
    intent: Intent | None = None
    capability_scores: dict[str, float] = Field(default_factory=dict)
    answers: dict[str, bool | float | str | None] = Field(default_factory=dict)
    provider: Literal["jev", "llm", "policy"]
    version: str = Field(min_length=1)


class Handoff(ContractModel):
    required: bool
    reason: str = Field(min_length=1)
    priority: Literal["normal", "high", "urgent"] = "normal"


class Draft(ContractModel):
    text: str = Field(min_length=1, max_length=8000)
    language: str = Field(min_length=2, max_length=16)
    fact_ids: list[str] = Field(default_factory=list, max_length=100)
    composer: Literal["primary", "fallback", "template"] | None = None
    # The reply that tells the customer a person has been told: it is checked where it is written
    # (`_compose`), and `emit` delivers it while the conversation is muted for that person.
    handoff: bool = False
    # A handoff draft only: the English note for the team member who takes over (stored on the
    # escalation, never sent to the customer) and the topic of an unanswered business question.
    staff_note: str | None = Field(default=None, max_length=300)
    gap_topic: str | None = Field(default=None, max_length=60)


class Verification(ContractModel):
    """Outcome of checking a draft before the gate; ``issues`` are short, model-free codes
    or checker notes that feed the single regeneration attempt."""

    ok: bool
    issues: list[str] = Field(default_factory=list, max_length=5)


class GateVerdict(ContractModel):
    decision: Literal["send", "approve", "reject"]
    risk_score: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    policy: str = Field(min_length=1)


class OutboundMessage(ContractModel):
    trace_id: UUID
    contact_id: UUID
    channel: Channel
    kind: Literal["text", "template", "notification"] = "text"
    body: str = Field(min_length=1, max_length=8000)
    idempotency_key: str = Field(min_length=1, max_length=512)
    ui: Ui = Field(default_factory=Ui)


class TurnState(ContractModel):
    envelope: Envelope
    ctx: Context | None = None
    guard: GuardResult | None = None
    signals: Signals | None = None
    plan: Plan | None = None
    results: Annotated[list[AgentResult], operator.add] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    ui: Ui = Field(default_factory=Ui)
    failures: list[AgentError] = Field(default_factory=list)
    handoff: Handoff | None = None
    draft: Draft | None = None
    verdict: GateVerdict | None = None
    outbound: list[OutboundMessage] = Field(default_factory=list)
    regen_count: int = Field(default=0, ge=0, le=3)
    verified: bool | None = None
    verify_issues: list[str] = Field(default_factory=list, max_length=5)
    route: (
        Literal[
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
        | None
    ) = None
    node_error: str | None = None
    current_task: Task | None = None
    # Bounded agent loop: completed observe rounds, follow-up tasks, results already
    # merged into facts, and why the loop stopped (a code, never model text).
    step: int = Field(default=0, ge=0, le=5)
    loop_tasks: list[Task] = Field(default_factory=list, max_length=12)
    pending_tasks: list[Task] = Field(default_factory=list, max_length=3)
    collected: int = Field(default=0, ge=0)
    loop_stop: str | None = Field(default=None, max_length=64)
