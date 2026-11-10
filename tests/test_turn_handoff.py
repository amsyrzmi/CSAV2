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
from csa.contracts.facts import Fact
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
from csa.graph.policy.gate import Policy
from csa.turn import deps
from csa.turn.texts import handoff_template

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

    def __init__(self, reply: str | None = "Sorry. A team member will reply here.") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        self.calls.append((messages[0]["content"], messages[1]["content"]))
        if self.reply is None:
            return httpx.Response(500)
        return _ok({"reply": self.reply, "used_fact_ids": []})(request)


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
    assert draft == Draft(text=_KIND, language="en", handoff=True, composer="template"), label


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
        assert draft == Draft(
            text=handoff_template("ms"), language="ms", handoff=True, composer="template"
        ), reply


async def test_compose_handoff_keeps_a_figure_the_conversation_carried() -> None:
    reply = "Thanks, a team member will check order 4417 and reply in this chat."
    model = Recorder(reply)
    recent = [RecentMessage(author="customer", text="where is order 4417?")]
    draft = await _deps(model)._compose(_handoff("agent_failure", route="plan_args", recent=recent))
    assert draft.text == reply and draft.composer == "primary"
    # A figure from a fact is grounded too.
    fact = Fact(id="f1", text="Plan Kecil costs RM15", source="kb_agent")
    priced = "Plan Kecil is RM15; a team member will reply here about the rest."
    draft = await _deps(Recorder(priced))._compose(
        _handoff("agent_failure", route="plan_args", facts=[fact])
    )
    assert draft.text == priced and draft.composer == "primary"


async def test_compose_handoff_template_when_model_errors() -> None:
    model = Recorder(None)
    draft = await _deps(model)._compose(_handoff("angry_customer", language="ms"))
    assert model.calls  # it was asked
    assert draft == Draft(
        text=handoff_template("ms"), language="ms", handoff=True, composer="template"
    )


async def test_compose_handoff_without_a_model_is_the_template() -> None:
    plain = deps.PostgresTurnDependencies(None)  # type: ignore[arg-type]
    draft = await plain._compose(_handoff())
    assert draft == Draft(text=_KIND, language="en", handoff=True, composer="template")


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


# --- a slow or failing handoff reply never fails the turn -------------------------------------


async def test_compose_handoff_template_when_the_model_is_slower_than_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "HANDOFF_COMPOSE_BUDGET_S", 0.05, raising=False)
    made = _deps(Recorder())

    async def slow(_state: TurnState, _brand: object) -> str:
        await asyncio.sleep(1.0)
        return "Sorry about that. A team member will reply in this chat."

    made._llm.compose_handoff = slow  # type: ignore[union-attr, method-assign]
    draft = await made._compose(_handoff())
    assert draft == Draft(text=_KIND, language="en", handoff=True, composer="template")


async def test_compose_handoff_template_when_the_model_raises() -> None:
    made = _deps(Recorder())

    async def broken(_state: TurnState, _brand: object) -> str:
        raise RuntimeError("transport went away")

    made._llm.compose_handoff = broken  # type: ignore[union-attr, method-assign]
    draft = await made._compose(_handoff())
    assert draft == Draft(text=_KIND, language="en", handoff=True, composer="template")


async def test_compose_handoff_template_when_the_brand_read_fails() -> None:
    model = Recorder()
    made = _deps(model)

    async def no_brand(_client_id: UUID) -> object:
        raise RuntimeError("database is down")

    made._client_brand = no_brand  # type: ignore[method-assign]
    draft = await made._compose(_handoff())
    assert draft == Draft(text=_KIND, language="en", handoff=True, composer="template")
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
