"""The two counters of the customer bot replay script (scripts/customer_bot_replay.py), and its
handoff check, which must match the real turn's.

The replay itself calls the real model inside the staging container and is never run in tests;
the handoff check runs here against a fake model.
"""

from __future__ import annotations

from typing import Any

from csa.contracts.state import Context, RecentMessage
from csa.decide.llm import _BOOKING_COMPOSE_RULE, ClientBrand
from csa.turn.deps import build_llm_provider
from scripts.customer_bot_replay import (
    Conversation,
    _envelope,
    _reply,
    count_formal_markers,
    count_repeated_sentences,
)


def test_marker_counter_finds_formal_malay() -> None:
    text = (
        "Sila semak plan anda. Pihak kami akan memaklumkan anda. "
        "Saya tidak dapat menemukan maklumat itu; anda akan dimaklumkan. PIHAK  KAMI ada."
    )
    counts = count_formal_markers(text)
    assert counts == {
        "anda": 3,
        "sila": 1,
        "pihak kami": 2,
        "menemukan": 1,
        "dimaklumkan": 1,
    }


def test_marker_counter_counts_whole_words_only() -> None:
    # "andaikan", "silap" and "dimaklumkannya" carry the letters, not the words.
    counts = count_formal_markers("Andaikan silap, dimaklumkannya. Okay, saya bagitahu team.")
    assert counts == dict.fromkeys(("anda", "sila", "pihak kami", "menemukan", "dimaklumkan"), 0)


def test_repeat_counter_finds_a_repeated_sentence() -> None:
    replies = [
        "Ada tiga plan. Biasanya guna data untuk apa?",
        "Okay, Plan Besar paling sesuai. Biasanya guna data untuk apa?",
        "Sama-sama!",
    ]
    assert count_repeated_sentences(replies) == 1


def test_repeat_counter_ignores_case_spacing_and_one_reply() -> None:
    # The same sentence twice in one reply is not a repeat across replies; a change of case or
    # spacing does not make a sentence new.
    assert count_repeated_sentences(["Hai! Hai!", "Plan Kecil RM15."]) == 0
    assert count_repeated_sentences(["Nak  dengar lagi?", "nak dengar lagi ?"]) == 1
    assert count_repeated_sentences([]) == 0


class _Model:
    """A model that wants a person and writes `reply` as the handoff text."""

    model = "fake-model"

    def __init__(self, reply: str, note: str | None = None, gap: str | None = None) -> None:
        self.reply = reply
        self.note = note
        self.gap = gap

    async def json_completion(self, schema: Any, **_kwargs: Any) -> Any:
        if schema.__name__ == "Classification":
            return schema.model_validate(
                {
                    "intent": "human_request",
                    "language": "en",
                    "sentiment": "neutral",
                    "wants_human": True,
                    "injection": False,
                    "urgent": False,
                }
            )
        if schema.__name__ == "HandoffOut":
            return schema.model_validate(
                {"reply": self.reply, "staff_note": self.note or "", "gap_topic": self.gap}
            )
        return schema.model_validate({"reply": self.reply, "used_fact_ids": []})


async def _handoff_how(reply: str, recent: list[RecentMessage]) -> str:
    conversation = Conversation(id="c1", language="en", messages=["x"])
    asked = "I want my RM500 refund within 2 days, let me talk to a person"
    _, how, _, _ = await _reply(
        build_llm_provider(_Model(reply)),
        ClientBrand(),
        [],
        _envelope(conversation, asked, 1),
        Context(recent=recent),
    )
    return how


async def test_replay_handoff_is_grounded_only_in_earlier_assistant_messages() -> None:
    # As on a real turn (`_handoff_draft`): the customer's own figures never ground the reply.
    promise = "Your RM500 refund will be processed within 2 days, a team member will confirm."
    customer = [RecentMessage(author="customer", text="RM500 refund within 2 days please")]
    assert await _handoff_how(promise, customer) == "handoff, template"
    told = "Thanks, a team member will check order 4417 and reply in this chat."
    assistant = [RecentMessage(author="assistant", text="Order 4417 is on its way.")]
    assert await _handoff_how(told, assistant) == "handoff, model"


async def test_replay_prints_the_staff_note_of_a_handoff() -> None:
    from scripts.customer_bot_replay import ReplayScript, replay

    note = "The customer asks for a person. Nothing else was said."
    model = build_llm_provider(
        _Model("Okay, a team member will reply here.", note=note, gap="how to pay")
    )
    conversation = Conversation(id="c1", language="en", messages=["let me talk to a person"])
    lines: list[str] = []
    script = ReplayScript.model_validate(
        {"brand": {"brand_name": "Acme"}, "facts": [], "conversations": [conversation.model_dump()]}
    )
    await replay(model, script, [conversation], lines.append)
    assert any("staff note: " + note in line for line in lines)
    assert any("gap topic: how to pay" in line for line in lines)
    # A note that fails the check prints the fixed note of the route instead.
    bad = build_llm_provider(_Model("Okay, a team member will reply here.", note="Owed RM500."))
    lines.clear()
    await replay(bad, script, [conversation], lines.append)
    assert any("staff note: The customer asked for a person." in line for line in lines)
    assert not any("RM500" in line for line in lines)


class _Facts:
    """A model that records the facts compose was shown, answering with an empty reply text."""

    model = "fake-model"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def json_completion(self, schema: Any, **kwargs: Any) -> Any:
        if schema.__name__ == "Classification":
            return schema.model_validate(
                {
                    "intent": "product_info",
                    "language": "en",
                    "sentiment": "neutral",
                    "wants_human": False,
                    "injection": False,
                    "urgent": False,
                }
            )
        self.prompts.append(kwargs["user"])
        self.systems.append(kwargs["system"])
        return schema.model_validate({"reply": "Okay.", "used_fact_ids": []})


async def test_a_conversation_with_its_own_facts_uses_them() -> None:
    from scripts.customer_bot_replay import ReplayScript, replay

    model = _Facts()
    script = ReplayScript.model_validate(
        {
            "brand": {"brand_name": "Acme"},
            "facts": ["SCRIPT-FACT the shop opens at nine."],
            "conversations": [
                {"id": "own", "language": "en", "messages": ["hi"], "facts": ["OWN-FACT 7."]},
                {"id": "shared", "language": "en", "messages": ["hi"]},
                {"id": "none", "language": "en", "messages": ["hi"], "facts": []},
            ],
        }
    )
    await replay(build_llm_provider(model), script, list(script.conversations), lambda _: None)
    own, shared, none = model.prompts
    assert "OWN-FACT 7." in own and "SCRIPT-FACT" not in own
    assert "SCRIPT-FACT" in shared and "OWN-FACT" not in shared
    # An empty list replaces the script's facts too: this conversation is told no facts at all.
    assert "SCRIPT-FACT" not in none and "OWN-FACT" not in none


def test_the_shipped_script_has_the_subscription_and_stock_conversations() -> None:
    from scripts.customer_bot_replay import SCRIPT_FILE, load_script

    script = load_script(SCRIPT_FILE)
    by_id = {c.id: c for c in script.conversations}
    for conversation_id in ("ms-subscription", "ms-stock"):
        conversation = by_id[conversation_id]
        assert conversation.language == "ms"
        assert conversation.facts or conversation.facts_by_turn, conversation_id
    subscription_turns = by_id["ms-subscription"].facts_by_turn
    assert subscription_turns is not None
    # Turns 1-3 (up to and including the customer's ask to cancel) see only the plan fact; the
    # request fact comes on the turn after, and stays.
    for turn, facts in enumerate(subscription_turns):
        assert "Your plan: " in facts[0] and "It renews on " in facts[0]
        has_request = any("was passed to our staff to decide." in f for f in facts)
        assert has_request == (turn >= 3), turn
    assert by_id["ms-subscription"].facts_source == "subscription_agent"
    assert by_id["ms-stock"].facts_source == "order_agent"
    stock = " ".join(by_id["ms-stock"].facts or [])
    assert "available in our shop." in stock and "on our TikTok Shop" in stock
    # The two conversations carry their own facts; the older ones keep the script's.
    assert by_id["ms-plans"].facts is None


def test_the_shipped_script_has_the_decline_then_ok_conversations() -> None:
    # The bot may offer a handoff, the customer declines and asks about plans, the bot answers,
    # and the customer says ok or ya: read the last turn of each in the replay output, it must
    # show wants_human=False and no handoff.
    from scripts.customer_bot_replay import SCRIPT_FILE, load_script

    by_id = {c.id: c for c in load_script(SCRIPT_FILE).conversations}
    english, malay = by_id["en-decline-then-ok"], by_id["ms-decline-then-ok"]
    assert (english.language, malay.language) == ("en", "ms")
    assert english.messages[-1] == "ok" and malay.messages[-1] == "ya"
    assert len(english.messages) == len(malay.messages) == 3
    assert english.facts is None and malay.facts is None


async def test_facts_differ_per_turn_and_carry_their_agent_as_source() -> None:
    from scripts.customer_bot_replay import ReplayScript, replay

    model = _Facts()
    script = ReplayScript.model_validate(
        {
            "brand": {"brand_name": "Acme"},
            "facts": ["SCRIPT-FACT"],
            "conversations": [
                {
                    "id": "sub",
                    "language": "en",
                    "messages": ["when does my plan renew", "cancel it", "did it go through"],
                    "facts_source": "subscription_agent",
                    "facts_by_turn": [["PLAN-FACT"], ["PLAN-FACT"], ["PLAN-FACT", "REQUEST-FACT"]],
                }
            ],
        }
    )
    await replay(build_llm_provider(model), script, list(script.conversations), lambda _: None)
    first, second, third = model.prompts
    assert "PLAN-FACT" in first and "REQUEST-FACT" not in first and "SCRIPT-FACT" not in first
    assert "REQUEST-FACT" not in second
    assert "REQUEST-FACT" in third
    # The dates rule is said only for a subscription or booking fact: the source is the agent's.
    assert all(_BOOKING_COMPOSE_RULE in system for system in model.systems)


async def test_a_conversation_without_facts_by_turn_keeps_one_kb_fact_set_for_every_turn() -> None:
    from scripts.customer_bot_replay import ReplayScript, replay

    model = _Facts()
    script = ReplayScript.model_validate(
        {
            "facts": ["SCRIPT-FACT"],
            "conversations": [{"id": "c", "language": "en", "messages": ["hi", "and now"]}],
        }
    )
    await replay(build_llm_provider(model), script, list(script.conversations), lambda _: None)
    assert len(model.prompts) == 2
    assert all("SCRIPT-FACT" in p for p in model.prompts)
    assert not any(_BOOKING_COMPOSE_RULE in system for system in model.systems)


def test_facts_by_turn_needs_one_list_per_message_and_excludes_facts() -> None:
    import pytest
    from pydantic import ValidationError

    base: dict[str, Any] = {"id": "c", "language": "en", "messages": ["a", "b"]}
    with pytest.raises(ValidationError):
        Conversation.model_validate({**base, "facts_by_turn": [["x"]]})
    with pytest.raises(ValidationError):
        Conversation.model_validate({**base, "facts": ["x"], "facts_by_turn": [["x"], ["y"]]})
    Conversation.model_validate({**base, "facts_by_turn": [["x"], []]})
