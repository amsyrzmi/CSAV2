"""The two counters of the customer bot replay script (scripts/customer_bot_replay.py), and its
handoff check, which must match the real turn's.

The replay itself calls the real model inside the staging container and is never run in tests;
the handoff check runs here against a fake model.
"""

from __future__ import annotations

from typing import Any

from csa.contracts.state import Context, RecentMessage
from csa.decide.llm import ClientBrand
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
