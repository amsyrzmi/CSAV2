"""The handoff reply: written by the model and checked, or a localized template; delivered by the
draft's flag (not by matching a fixed text), sent while the conversation is muted, and kept for
staff when held.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from csa.contracts.envelope import Callback, Channel
from csa.contracts.facts import Fact, Ui
from csa.contracts.plan import Intent, Plan
from csa.contracts.state import (
    Context,
    Draft,
    GateVerdict,
    Handoff,
    OutboundMessage,
    RecentMessage,
    TurnState,
    Verification,
)
from csa.core.tenancy import tx
from csa.decide.llm import HandoffOut
from csa.graph.policy.gate import Policy
from csa.graph.policy.handoff import decide_handoff
from csa.graph.policy.plan_rules import validate_plan
from csa.turn import deps
from csa.turn.texts import (
    fallback_staff_note,
    gap_topic_or_none,
    handoff_template,
    staff_note_ok,
)

from tests.conftest import requires_docker
from tests.test_booking_agent import envelope_for
from tests.test_bookings_bot import Shop, shop
from tests.test_coordinator import make_envelope, make_signals
from tests.test_llm_provider import _ok, _provider

pytest_plugins = ["tests.test_console_sessions"]
__all__ = ["shop"]  # the fixture, imported so this module can use it

pytestmark = pytest.mark.asyncio

_KIND = "I've passed this to a team member who will get back to you shortly."


class Recorder:
    """A model transport that records every call and answers with ``reply`` (or a 500)."""

    def __init__(
        self,
        reply: str | None = "Sorry. A team member will reply here.",
        *,
        note: str | None = None,
        gap: str | None = None,
    ) -> None:
        self.reply = reply
        self.note = note
        self.gap = gap
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        self.calls.append((messages[0]["content"], messages[1]["content"]))
        if self.reply is None:
            return httpx.Response(500)
        payload: dict[str, Any] = {"reply": self.reply, "gap_topic": self.gap}
        if self.note is not None:
            payload["staff_note"] = self.note
        return _ok(payload)(request)


def _deps(handler: Callable[[httpx.Request], httpx.Response]) -> deps.PostgresTurnDependencies:
    made = deps.PostgresTurnDependencies(None, _provider(handler))  # type: ignore[arg-type]

    async def policy(_client_id: UUID) -> Policy:
        return Policy(semantic_verify=False)

    made._policy = policy  # type: ignore[method-assign]
    return made


def _handoff(
    reason: str = "human_requested",
    *,
    route: str | None = "hard_escalate",
    body: str = "can I talk to someone",
    language: str = "en",
    answers: dict[str, Any] | None = None,
    envelope_language: str = "en",
    callback: bool = False,
    channel: Channel = Channel.TELEGRAM,
    recent: list[RecentMessage] | None = None,
    facts: list[Fact] | None = None,
    required: bool = True,
) -> TurnState:
    envelope = make_envelope(body, callback=Callback(action_id=uuid.uuid4()) if callback else None)
    envelope = envelope.model_copy(
        update={
            "channel": channel,
            "message": envelope.message.model_copy(update={"language": envelope_language}),
        }
    )
    return TurnState(
        envelope=envelope,
        ctx=Context(recent=recent or []),
        signals=make_signals(
            Intent.HUMAN_REQUEST, answers={"language": language, **(answers or {})}
        ),
        plan=Plan(
            tasks=[],
            reply_strategy="escalate",
            intent=Intent.HUMAN_REQUEST,
            confidence=1,
            source="policy",
        ),
        facts=facts or [],
        handoff=Handoff(required=required, reason=reason, priority="high"),
        route=route,  # type: ignore[arg-type]
    )


# --- _compose: who writes the handoff reply ----------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "route"),
    [
        ("human_requested", "hard_escalate"),  # asked for a person
        ("angry_customer", "hard_escalate"),
        ("agent_failure", "plan_args"),
        ("plan_escalated", "clarify"),  # clarified twice
        ("plan_escalated", "plan_args"),
    ],
)
async def test_compose_handoff_is_written_by_the_model_when_allowed(
    reason: str, route: str
) -> None:
    model = Recorder("Sorry about that. A team member will reply in this chat.")
    draft = await _deps(model)._compose(_handoff(reason, route=route))
    assert len(model.calls) == 1
    assert draft == Draft(
        text="Sorry about that. A team member will reply in this chat.",
        language="en",
        handoff=True,
        composer="primary",
        staff_note=fallback_staff_note(route, reason),  # the model gave no note
    )


@pytest.mark.parametrize(
    ("label", "state"),
    [
        ("injection", _handoff(answers={"injection": True}, route="guard_blocked")),
        ("legal words", _handoff(body="I will ask my lawyer about this", route="hard_escalate")),
        ("hard route", _handoff("plan_escalated", route="hard_escalate")),
        ("guard", _handoff("plan_escalated", route="guard_blocked")),
        ("guard, angry", _handoff("angry_customer", route="guard_blocked")),
        ("voice", _handoff("plan_escalated", route="voice_failed")),
        ("language", _handoff("plan_escalated", route="unsupported_language")),
        ("callback", _handoff("agent_failure", route="callback", callback=True)),
        ("service off", _handoff("service_off", route="plan_args")),
        ("disabled", _handoff("human_requested:disabled", route="hard_escalate", required=False)),
    ],
)
async def test_compose_handoff_uses_template_on_injection_or_hard_route(
    label: str, state: TurnState
) -> None:
    model = Recorder()
    draft = await _deps(model)._compose(state)
    assert model.calls == [], label
    assert draft.model_copy(update={"staff_note": None}) == Draft(
        text=_KIND, language="en", handoff=True, composer="template"
    ), label
    assert draft.staff_note, label


async def test_compose_handoff_template_is_in_the_customers_language() -> None:
    model = Recorder()
    ms = await _deps(model)._compose(_handoff(language="ms", route="guard_blocked"))
    assert ms.text == handoff_template("ms") and ms.language == "ms" and ms.handoff
    # No usable signal and no envelope language: the detector reads the message.
    guessed = await _deps(model)._compose(
        _handoff(
            language="other",
            envelope_language="und",
            body="saya nak cakap dengan orang, tak boleh tunggu",
            route="guard_blocked",
        )
    )
    assert guessed.text == handoff_template("ms") and guessed.language == "ms"
    assert model.calls == []


async def test_compose_handoff_rejects_unsafe_generation() -> None:
    for reply in (
        "Sorry, " + "a team member will reply here and " * 10 + "soon.",  # over 300 characters
        "A team member will reply within 2 hours.",  # a time nobody gave
        "One. Two. Three sentences is too many.",
    ):
        model = Recorder(reply)
        draft = await _deps(model)._compose(_handoff(language="ms"))
        assert len(model.calls) == 1, reply
        assert draft.model_copy(update={"staff_note": None}) == Draft(
            text=handoff_template("ms"), language="ms", handoff=True, composer="template"
        ), reply


async def test_compose_handoff_keeps_a_figure_the_assistant_already_said() -> None:
    reply = "Thanks, a team member will check order 4417 and reply in this chat."
    model = Recorder(reply)
    recent = [
        RecentMessage(author="customer", text="where is my order?"),
        RecentMessage(author="assistant", text="Order 4417 is on its way."),
    ]
    draft = await _deps(model)._compose(_handoff("agent_failure", route="plan_args", recent=recent))
    assert draft == Draft(
        text=reply,
        language="en",
        handoff=True,
        composer="primary",
        staff_note=fallback_staff_note("plan_args", "agent_failure"),
    )


async def test_compose_handoff_never_repeats_a_customers_figure_as_a_promise() -> None:
    # The customer's own words, now or earlier, never ground a handoff reply: echoed back, a
    # figure reads as a promise, and this reply is not reviewed.
    asked = "I want my RM500 refund within 2 days"
    promise = "Your RM500 refund will be processed within 2 days, a team member will confirm."
    model = Recorder(promise)
    state = _handoff(
        "agent_failure",
        route="plan_args",
        body=asked,
        recent=[RecentMessage(author="customer", text=asked)],
    )
    draft = await _deps(model)._compose(state)
    assert len(model.calls) == 1  # the model was asked, and its reply was refused
    assert draft.text == _KIND and draft.handoff and draft.composer == "template"
    assert draft.language == "en" and draft.fact_ids == []


async def test_compose_handoff_does_not_ground_a_figure_from_a_fact() -> None:
    # Facts are not shown to the handoff writer, so they do not ground its reply either.
    fact = Fact(id="f1", text="Plan Kecil costs RM15", source="kb_agent")
    priced = "Plan Kecil is RM15; a team member will reply here about the rest."
    model = Recorder(priced)
    draft = await _deps(model)._compose(_handoff("agent_failure", route="plan_args", facts=[fact]))
    assert len(model.calls) == 1 and "Plan Kecil costs RM15" not in model.calls[0][1]
    assert draft.text == _KIND and draft.handoff and draft.composer == "template"
    assert draft.language == "en" and draft.fact_ids == []  # the fact is not carried over


async def test_compose_handoff_template_when_model_errors() -> None:
    model = Recorder(None)
    draft = await _deps(model)._compose(_handoff("angry_customer", language="ms"))
    assert model.calls  # it was asked
    assert draft.text == handoff_template("ms") and draft.language == "ms"
    assert draft.handoff and draft.composer == "template"
    assert draft.fact_ids == []
    # The model gave nothing, so the fixed note of the reason is stored.
    assert draft.staff_note == fallback_staff_note("hard_escalate", "angry_customer")
    assert draft.gap_topic is None


async def test_compose_handoff_without_a_model_is_the_template() -> None:
    plain = deps.PostgresTurnDependencies(None)  # type: ignore[arg-type]
    draft = await plain._compose(_handoff())
    assert draft.text == _KIND and draft.handoff and draft.composer == "template"
    assert draft.language == "en" and draft.fact_ids == []
    assert draft.staff_note == fallback_staff_note("hard_escalate", "human_requested")


async def test_an_escalate_plan_without_a_required_handoff_is_still_a_handoff_draft() -> None:
    state = _handoff().model_copy(update={"handoff": None})
    model = Recorder()
    draft = await _deps(model)._compose(state)
    assert draft.handoff and draft.text == _KIND and model.calls == []


async def test_sandbox_handoff_keeps_sandbox_text_and_flag_off() -> None:
    model = Recorder()
    draft = await _deps(model)._compose(_handoff(channel=Channel.WEB))
    assert draft.text == deps.SANDBOX_SCOPE_TEXT
    assert draft.handoff is False and model.calls == []


# --- the staff note and the gap topic ----------------------------------------------------------

_NOTE = "The customer wants to know how to pay. The assistant has not answered that yet."


async def test_a_written_handoff_keeps_a_grounded_note() -> None:
    model = Recorder(note=_NOTE, gap="how to pay")
    draft = await _deps(model)._compose(_handoff("human_requested", body="how do I pay?"))
    assert draft.staff_note == _NOTE
    assert draft.gap_topic == "how to pay"
    assert draft.composer == "primary" and draft.handoff


async def test_a_note_with_a_figure_nobody_said_becomes_the_fallback_note() -> None:
    model = Recorder(note="The customer is owed RM500 within 2 days.", gap="refund")
    draft = await _deps(model)._compose(_handoff("human_requested"))
    assert draft.staff_note == fallback_staff_note("hard_escalate", "human_requested")
    assert draft.staff_note == "The customer asked for a person."
    # The reply is checked on its own: the model's reply stays.
    assert draft.text == "Sorry. A team member will reply here." and draft.composer == "primary"


async def test_a_ladder_handoff_is_written_by_the_model_with_a_note() -> None:
    note = "The customer's questions stayed unclear. Nothing about the plan was answered yet."
    model = Recorder(note=note)
    state = _handoff("unresolved_turns", route="clarify", body="hmm not sure what I mean")
    draft = await _deps(model)._compose(state)
    assert len(model.calls) == 1
    assert draft.handoff and draft.composer == "primary"
    assert draft.text == "Sorry. A team member will reply here."
    assert draft.staff_note == note
    # The same ladder reason on a hard route is never model-written.
    template = await _deps(Recorder())._compose(
        _handoff("unresolved_turns", route="hard_escalate", body="my lawyer will sue")
    )
    assert template.composer == "template"


async def test_a_customers_own_figure_may_be_in_the_note() -> None:
    asked = "my order 4417 has not arrived"
    note = "The customer says order 4417 has not arrived. Nothing was answered yet."
    model = Recorder(note=note)
    state = _handoff(
        "agent_failure",
        route="plan_args",
        body=asked,
        recent=[RecentMessage(author="customer", text="where is order 4417?")],
    )
    draft = await _deps(model)._compose(state)
    assert draft.staff_note == note


@pytest.mark.parametrize(
    ("reason", "route", "note"),
    [
        ("human_requested", "hard_escalate", "The customer asked for a person."),
        (
            "angry_customer",
            "guard_blocked",
            "The customer is upset; the assistant handed over at once.",
        ),
        (
            "unresolved_turns",
            "clarify",
            "The assistant could not answer after three tries. Read the chat for what is missing.",
        ),
        ("agent_failure", "callback", "A lookup failed, so the assistant could not answer."),
        (
            "service_off",
            "plan_args",
            "The question needs a service this company has not switched on.",
        ),
        (
            "plan_escalated",
            "hard_escalate",
            "The message has a legal or police word, so the assistant handed over without "
            "answering.",
        ),
        (
            "plan_escalated",
            "guard_blocked",
            "The safety check stopped this message, so the assistant handed over without "
            "answering.",
        ),
        ("plan_escalated", "voice_failed", "A voice message could not be understood."),
        (
            "plan_escalated",
            "unsupported_language",
            "The customer wrote in a language the assistant does not answer.",
        ),
        (
            "plan_escalated",
            "clarify",
            "The assistant handed this chat over. Read the chat for details.",
        ),
        ("plan_escalated", None, "The assistant handed this chat over. Read the chat for details."),
        (
            "human_requested:disabled",
            "hard_escalate",
            "The assistant handed this chat over. Read the chat for details.",
        ),
    ],
)
async def test_a_template_handoff_gets_the_fixed_note_for_its_route(
    reason: str, route: str | None, note: str
) -> None:
    assert fallback_staff_note(route, reason) == note
    model = Recorder(note="The model must not be asked.")
    # A turn that never calls the model: legal words in the message make it a template handoff.
    state = _handoff(reason, route=route, body="I will ask my lawyer about this")
    draft = await _deps(model)._compose(state)
    assert model.calls == []
    assert draft.composer == "template" and draft.staff_note == note
    assert draft.gap_topic is None


async def test_the_note_is_never_in_the_customer_reply() -> None:
    model = Recorder(note=_NOTE, gap="how to pay")
    draft = await _deps(model)._compose(_handoff("human_requested"))
    assert draft.staff_note == _NOTE
    assert _NOTE not in draft.text and "how to pay" not in draft.text
    # And the model is told the note is never shown to the customer.
    assert "never shown to the customer" in model.calls[0][0]
    template = await _deps(Recorder())._compose(_handoff(body="my lawyer will sue"))
    assert template.staff_note and template.staff_note not in template.text


def test_staff_note_ok_table() -> None:
    sources = ["where is order 4417?", "Plan Kecil is on offer."]
    assert staff_note_ok("The customer asks about order 4417. Nothing answered yet.", sources)
    assert not staff_note_ok("", sources)
    assert not staff_note_ok("x" * 301, sources)
    assert not staff_note_ok("One. Two. Three.", sources)
    assert not staff_note_ok("The customer was promised RM500.", sources)
    assert not staff_note_ok("Order 4417 is ready; pickup at 1234.", sources)


def test_gap_topic_or_none_table() -> None:
    assert gap_topic_or_none("How to Pay") == "how to pay"
    assert gap_topic_or_none("  which   plan suits me  ") == "which plan suits me"
    assert gap_topic_or_none("sign-up for kid's plan") == "sign-up for kid's plan"
    assert gap_topic_or_none("plan 5g") is None  # digits
    assert gap_topic_or_none("how to sign up for a plan") is None  # seven words
    assert gap_topic_or_none("how to sign up for plans") == "how to sign up for plans"  # six
    assert gap_topic_or_none("hi") is None  # under three characters
    assert gap_topic_or_none("a" * 61) is None
    assert gap_topic_or_none("how to pay!") is None  # punctuation
    assert gap_topic_or_none("") is None
    assert gap_topic_or_none("   ") is None
    assert gap_topic_or_none(None) is None
    # At least one letter: punctuation alone is not a topic.
    assert gap_topic_or_none("---") is None
    assert gap_topic_or_none("'''") is None
    assert gap_topic_or_none("- - -") is None
    assert gap_topic_or_none("a-b") == "a-b"


async def test_a_note_is_kept_when_only_the_reply_fails_its_check() -> None:
    model = Recorder("A team member will reply within 2 hours.", note=_NOTE, gap="how to pay")
    draft = await _deps(model)._compose(_handoff("human_requested"))
    assert draft.composer == "template" and draft.text == _KIND
    assert draft.staff_note == _NOTE and draft.gap_topic == "how to pay"


async def test_a_gap_topic_that_fails_the_check_is_dropped() -> None:
    model = Recorder(note=_NOTE, gap="how to pay 100 now")
    draft = await _deps(model)._compose(_handoff("human_requested"))
    assert draft.gap_topic is None and draft.staff_note == _NOTE


# --- a slow or failing handoff reply never fails the turn -------------------------------------


async def test_compose_handoff_template_when_the_model_is_slower_than_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "HANDOFF_COMPOSE_BUDGET_S", 0.05, raising=False)
    made = _deps(Recorder())

    async def slow(_state: TurnState, _brand: object) -> HandoffOut:
        await asyncio.sleep(1.0)
        return HandoffOut(reply="Sorry about that. A team member will reply in this chat.")

    made._llm.compose_handoff = slow  # type: ignore[union-attr, method-assign]
    draft = await made._compose(_handoff())
    assert draft.text == _KIND and draft.handoff and draft.composer == "template"
    assert draft.staff_note == fallback_staff_note("hard_escalate", "human_requested")
    assert draft.gap_topic is None


async def test_compose_handoff_template_when_the_model_raises() -> None:
    made = _deps(Recorder())

    async def broken(_state: TurnState, _brand: object) -> HandoffOut:
        raise RuntimeError("transport went away")

    made._llm.compose_handoff = broken  # type: ignore[union-attr, method-assign]
    draft = await made._compose(_handoff())
    assert draft.text == _KIND and draft.handoff and draft.composer == "template"
    assert draft.staff_note == fallback_staff_note("hard_escalate", "human_requested")
    assert draft.gap_topic is None


async def test_compose_handoff_template_when_the_brand_read_fails() -> None:
    model = Recorder()
    made = _deps(model)

    async def no_brand(_client_id: UUID) -> object:
        raise RuntimeError("database is down")

    made._client_brand = no_brand  # type: ignore[method-assign]
    draft = await made._compose(_handoff())
    assert draft.text == _KIND and draft.handoff and draft.composer == "template"
    assert model.calls == []


# --- verify: a handoff draft was checked where it was written ---------------------------------


async def test_verify_skips_a_handoff_draft() -> None:
    model = Recorder()
    made = _deps(model)
    text = "A team member will call you at 555 0100."  # a number nobody gave
    handed = _handoff().model_copy(
        update={"draft": Draft(text=text, language="en", handoff=True, composer="primary")}
    )
    assert await made.verify(handed) == Verification(ok=True)
    plain = handed.model_copy(update={"draft": Draft(text=text, language="en")})
    assert (await made.verify(plain)).ok is False
    assert model.calls == []


# --- emit and hold on the real dependencies -----------------------------------------------------


async def _routed_chat(shop: Shop, contact: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A Telegram chat with its route and the inbound message of one turn: (chat, trace)."""
    credential, endpoint, chat, trace = (uuid.uuid4() for _ in range(4))
    w, tenant = shop.w, shop.tenant
    await w.q(
        "INSERT INTO channel_credentials (id, client_id, channel, disconnected_at)"
        " VALUES (%s, %s, 'telegram', CASE WHEN EXISTS (SELECT 1 FROM channel_credentials"
        " WHERE client_id = %s AND channel = 'telegram' AND disconnected_at IS NULL)"
        " THEN now() END)",
        str(credential),
        tenant,
        tenant,
    )
    await w.q(
        "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id, is_enabled)"
        " VALUES (%s, %s, %s, %s, true)",
        str(endpoint),
        tenant,
        uuid.uuid4().hex,
        str(credential),
    )
    await w.q(
        "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread)"
        " VALUES (%s, %s, %s, 'telegram', %s)",
        str(chat),
        tenant,
        str(contact),
        f"thread-{chat}",
    )
    await w.q(
        "INSERT INTO ingress_events (client_id, endpoint_id, contact_id, idempotency_key,"
        " payload_hash, trace_id, raw) VALUES (%s, %s, %s, %s, 'x', %s, '{}'::jsonb)",
        tenant,
        str(endpoint),
        str(contact),
        f"ing:{trace}",
        str(trace),
    )
    return chat, trace


async def _escalated(shop: Shop) -> tuple[TurnState, uuid.UUID]:
    """A turn whose escalation agent has run: an escalation row for the trace, and the
    conversation muted for a person (owned by human_support, no end to the mute)."""
    contact = await shop.customer()
    chat, trace = await _routed_chat(shop, contact)
    await shop.w.q(
        "INSERT INTO escalations (client_id, trace_id, conversation_id, contact_id, reason,"
        " priority) VALUES (%s, %s, %s, %s, 'human_requested', 'high')",
        shop.tenant,
        str(trace),
        str(chat),
        str(contact),
    )
    await shop.w.q(
        "UPDATE conversations SET owned_by = 'human_support', ai_muted_until = NULL WHERE id = %s",
        str(chat),
    )
    envelope = envelope_for(shop, contact, chat).model_copy(update={"trace_id": trace})
    return TurnState(envelope=envelope), chat


def _with_draft(state: TurnState, *, handoff: bool) -> TurnState:
    text = "Baik, saya dah bagitahu team. Mereka akan balas di sini."
    assert text != _KIND and text != handoff_template("ms")
    contact = state.envelope.contact.id
    assert contact is not None
    return state.model_copy(
        update={
            "draft": Draft(text=text, language="ms", handoff=handoff, composer="primary"),
            "outbound": [
                OutboundMessage(
                    trace_id=state.envelope.trace_id,
                    contact_id=contact,
                    channel=Channel.TELEGRAM,
                    body=text,
                    idempotency_key=f"{state.envelope.trace_id}:0",
                )
            ],
        }
    )


async def _outbound(shop: Shop, chat: uuid.UUID) -> list[dict[str, Any]]:
    return await shop.w.q(
        "SELECT m.body, m.send_authority AS authority, count(d.id) AS deliveries"
        " FROM messages m LEFT JOIN outbound_deliveries d ON d.message_id = m.id"
        " WHERE m.conversation_id = %s AND m.direction = 'outbound' GROUP BY m.id",
        str(chat),
    )


@requires_docker
async def test_handoff_draft_with_a_different_text_is_delivered_while_muted(shop: Shop) -> None:
    real = deps.PostgresTurnDependencies(shop.w.pool)
    state, chat = await _escalated(shop)
    await real.emit(_with_draft(state, handoff=True))
    assert await _outbound(shop, chat) == [
        {
            "body": "Baik, saya dah bagitahu team. Mereka akan balas di sini.",
            "authority": "handoff",
            "deliveries": 1,
        }
    ]
    # The same text without the flag is ordinary automation, which the mute stops.
    other, other_chat = await _escalated(shop)
    await real.emit(_with_draft(other, handoff=False))
    assert await _outbound(shop, other_chat) == []


@requires_docker
async def test_hold_keeps_a_handoff_draft_on_a_muted_conversation(shop: Shop) -> None:
    real = deps.PostgresTurnDependencies(shop.w.pool)
    verdict = GateVerdict(
        decision="approve", risk_score=0.1, reason="always_review:x", policy="lg07-gate-v1"
    )
    state, chat = await _escalated(shop)
    await real.hold(_with_draft(state, handoff=True).model_copy(update={"verdict": verdict}))
    held = await shop.w.q(
        "SELECT draft, status FROM approvals WHERE conversation_id = %s", str(chat)
    )
    assert held == [
        {"draft": "Baik, saya dah bagitahu team. Mereka akan balas di sini.", "status": "pending"}
    ]
    other, other_chat = await _escalated(shop)
    await real.hold(_with_draft(other, handoff=False).model_copy(update={"verdict": verdict}))
    left = await shop.w.q("SELECT id FROM approvals WHERE conversation_id = %s", str(other_chat))
    assert left == []


@requires_docker
async def test_a_request_phone_ui_is_stored_with_the_message_and_an_empty_ui_is_not(
    shop: Shop,
) -> None:
    contact = await shop.customer()
    chat, trace = await _routed_chat(shop, contact)
    envelope = envelope_for(shop, contact, chat).model_copy(update={"trace_id": trace})

    def message(key: str, ui: Ui) -> OutboundMessage:
        return OutboundMessage(
            trace_id=trace,
            contact_id=contact,
            channel=Channel.TELEGRAM,
            body=f"body {key}",
            idempotency_key=f"{trace}:{key}",
            ui=ui,
        )

    async with tx(shop.w.pool, shop.tenant) as conn:
        await deps._insert_outbound(conn, envelope, message("ask", Ui(request_phone=True)))
        await deps._insert_outbound(conn, envelope, message("plain", Ui()))
    rows = await shop.w.q(
        "SELECT body, ui FROM messages WHERE conversation_id = %s AND direction = 'outbound'"
        " ORDER BY body",
        str(chat),
    )
    assert rows == [
        {"body": "body ask", "ui": {"options": [], "request_phone": True}},
        {"body": "body plain", "ui": None},
    ]


# --- finalize stores the note and the gap ----------------------------------------------------


def _with_note(state: TurnState, note: str, gap: str | None) -> TurnState:
    return state.model_copy(
        update={
            "draft": Draft(
                text="Okay, a team member will reply.",
                language="en",
                handoff=True,
                composer="primary",
                staff_note=note,
                gap_topic=gap,
            )
        }
    )


async def _notes(shop: Shop, chat: uuid.UUID) -> list[str | None]:
    rows = await shop.w.q("SELECT note FROM escalations WHERE conversation_id = %s", str(chat))
    return [row["note"] for row in rows]


async def _gaps(shop: Shop) -> list[dict[str, Any]]:
    return await shop.w.q(
        "SELECT query, hits FROM kb_gaps WHERE client_id = %s ORDER BY query", shop.tenant
    )


@requires_docker
async def test_finalize_stores_the_note_on_the_escalation_once(shop: Shop) -> None:
    real = deps.PostgresTurnDependencies(shop.w.pool)
    state, chat = await _escalated(shop)
    assert await _notes(shop, chat) == [None]
    await real.finalize(_with_note(state, "First note. Second sentence.", None))
    assert await _notes(shop, chat) == ["First note. Second sentence."]
    # A resumed finalize leaves the stored note as it was.
    await real.finalize(_with_note(state, "A different note.", None))
    assert await _notes(shop, chat) == ["First note. Second sentence."]
    # A turn with no note (an ordinary draft, or a handoff draft without one) writes nothing.
    other, other_chat = await _escalated(shop)
    plain = other.model_copy(update={"draft": Draft(text="Hello.", language="en")})
    await real.finalize(plain)
    await real.finalize(
        _with_note(other, "x", None).model_copy(
            update={"draft": Draft(text="Hi.", language="en", handoff=True)}
        )
    )
    assert await _notes(shop, other_chat) == [None]


@requires_docker
async def test_finalize_never_writes_another_trace_or_company_note(shop: Shop) -> None:
    real = deps.PostgresTurnDependencies(shop.w.pool)
    state, chat = await _escalated(shop)
    sibling, sibling_chat = await _escalated(shop)
    await real.finalize(_with_note(state, "Only this trace.", None))
    assert await _notes(shop, chat) == ["Only this trace."]
    assert await _notes(shop, sibling_chat) == [None]


@requires_docker
async def test_finalize_records_the_gap_topic_once_and_never_on_a_resume(shop: Shop) -> None:
    real = deps.PostgresTurnDependencies(shop.w.pool)
    state, _chat = await _escalated(shop)
    handed = _with_note(state, "The customer asks how to pay.", "how to pay")
    await real.finalize(handed)
    assert await _gaps(shop) == [{"query": "how to pay", "hits": 1}]
    await real.finalize(handed)  # a resumed finalize
    assert await _gaps(shop) == [{"query": "how to pay", "hits": 1}]
    # A second customer asking the same thing counts a second hit.
    other, _ = await _escalated(shop)
    await real.finalize(_with_note(other, "Same question.", "how to pay"))
    assert await _gaps(shop) == [{"query": "how to pay", "hits": 2}]
    # No topic, no gap.
    third, _ = await _escalated(shop)
    await real.finalize(_with_note(third, "No topic.", None))
    assert await _gaps(shop) == [{"query": "how to pay", "hits": 2}]


# --- finalize counts unresolved turns ---------------------------------------------------------


def _planned(state: TurnState, strategy: str, intent: Intent, count: int) -> TurnState:
    return state.model_copy(
        update={
            "ctx": Context(clarify_count=count),
            "plan": Plan(
                tasks=[],
                reply_strategy=strategy,  # type: ignore[arg-type]
                intent=intent,
                confidence=1,
                source="policy",
            ),
        }
    )


async def _clarify_count(shop: Shop, chat: uuid.UUID) -> int:
    rows = await shop.w.q("SELECT clarify_count FROM conversations WHERE id = %s", str(chat))
    return rows[0]["clarify_count"]


@requires_docker
async def test_finalize_counts_unresolved_turns_and_resets_on_an_answer(shop: Shop) -> None:
    real = deps.PostgresTurnDependencies(shop.w.pool)
    state, chat = await _escalated(shop)
    assert await _clarify_count(shop, chat) == 0
    await real.finalize(_planned(state, "ask_clarifying", Intent.OTHER, 0))
    assert await _clarify_count(shop, chat) == 1
    await real.finalize(_planned(state, "ask_clarifying", Intent.OTHER, 1))
    assert await _clarify_count(shop, chat) == 2
    # Small talk and an ignored message keep the count; neither resolves nor adds.
    await real.finalize(_planned(state, "auto_reply", Intent.CHITCHAT, 2))
    assert await _clarify_count(shop, chat) == 2
    await real.finalize(_planned(state, "ignore", Intent.OTHER, 2))
    assert await _clarify_count(shop, chat) == 2
    await real.finalize(_planned(state, "partial_answer", Intent.OTHER, 2))
    assert await _clarify_count(shop, chat) == 3
    # An answer resets it.
    await real.finalize(_planned(state, "auto_reply", Intent.ORDER, 3))
    assert await _clarify_count(shop, chat) == 0
    await real.finalize(_planned(state, "ask_clarifying", Intent.OTHER, 0))
    await real.finalize(_planned(state, "escalate", Intent.OTHER, 1))
    assert await _clarify_count(shop, chat) == 0


@requires_docker
async def test_with_memory_off_the_ladder_still_hands_off_at_three(shop: Shop) -> None:
    await shop.policy({"conversation_memory": False})
    real = deps.PostgresTurnDependencies(shop.w.pool)
    state, chat = await _escalated(shop)
    await shop.w.q("UPDATE conversations SET clarify_count = 3 WHERE id = %s", str(chat))
    ctx = await real.load_context(state.envelope)
    assert ctx.flags["memory_off"] is True and ctx.clarify_count == 3
    asked = Plan(
        tasks=[],
        reply_strategy="ask_clarifying",
        intent=Intent.OTHER,
        confidence=1,
        source="policy",
    )
    plan, _ = validate_plan(asked, known_agents=frozenset(), clarify_count=ctx.clarify_count)
    handoff = decide_handoff(plan, [], context=ctx, signals=make_signals(Intent.OTHER))
    assert plan.reply_strategy == "escalate"
    assert handoff is not None and handoff.required and handoff.reason == "unresolved_turns"
    await real.finalize(state.model_copy(update={"ctx": ctx, "plan": plan}))
    assert await _clarify_count(shop, chat) == 0
