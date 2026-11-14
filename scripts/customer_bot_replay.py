r"""Owner-triggered replay of a synthetic customer chat through the real customer-bot model calls.

For each synthetic customer message in a script file (``scripts/customer_bot_replay_cases.json``
by default) it calls the real ``LLMProvider.classify``, then ``LLMProvider.compose``, or
``LLMProvider.compose_handoff`` when the classifier says the customer asks for a person, with a
hand-built ``Context``: ``recent`` is the replay's own earlier turns, clipped as the worker clips
them (``clip_message``), and ``flags["handoff_recent"]`` turns on after a handoff. Every compose
gets the script's fixed synthetic facts (or the conversation's own, per conversation or per
turn, tagged with the agent that would write them), except
a greeting or thanks (chitchat), which runs no agent on a real turn either. A handoff text that
fails the light check (grounded, as on a real turn, only in the replay's earlier assistant
replies) is replaced by the template, as on a real turn. A handoff also prints the staff note
(the model's when it passes the real turn's check, else the fixed note for a person asked for)
and the gap topic when the model gave one.

It does not run the graph (routing, agents, gate and delivery are what the golden cases test),
touches no database and needs only the model key. It runs inside the staging gateway container::

    docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay \
        [--script PATH] [--language ms|en] [--conversation ID] [--dry-run]

Output: each customer message and reply (synthetic text, printed on purpose), the classifier's
intent, language and wants_human, the brand tone used, then per conversation the counts of the
formal Malay markers (anda, sila, pihak kami, menemukan, dimaklumkan; whole words, any case) in
the replies and the number of sentences repeated across replies. The key is never printed, and
the client's call log goes to stderr. Exit 0 when every message was answered with no formal
marker in a Malay conversation and no repeated sentence, 1 otherwise, 2 on a setup error (no
model key, the model unavailable on the first call, a missing or invalid script file, an unknown
conversation id). ``--dry-run`` only checks the script file and makes no model call.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

import httpx
import structlog
from csa.contracts.envelope import (
    Channel,
    Contact,
    ConversationRef,
    Envelope,
    Message,
    Meta,
)
from csa.contracts.facts import Fact
from csa.contracts.plan import Intent, Plan
from csa.contracts.state import RECENT_WINDOW, Context, Handoff, RecentMessage, TurnState
from csa.core.pii import redact
from csa.core.settings import get_settings
from csa.decide.completion import CompletionError, JsonCompletionClient
from csa.decide.gemini import GeminiClient
from csa.decide.groq import GroqClient
from csa.decide.llm import ClientBrand, LLMProvider
from csa.metering.meter import NullMeter
from csa.turn.deps import CAPABILITIES, build_llm_provider
from csa.turn.texts import (
    clip_message,
    fallback_staff_note,
    gap_topic_or_none,
    handoff_template,
    handoff_text_ok,
    resolve_language,
    staff_note_ok,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCRIPT_FILE = Path(__file__).resolve().parent / "customer_bot_replay_cases.json"
# compose's own default when the company set no tone (csa.decide.llm.LLMProvider.compose).
DEFAULT_TONE = "brief, warm and plain; no markdown"
FORMAL_MARKERS: dict[str, re.Pattern[str]] = {
    "anda": re.compile(r"\banda\b", re.IGNORECASE),
    "sila": re.compile(r"\bsila\b", re.IGNORECASE),
    "pihak kami": re.compile(r"\bpihak\s+kami\b", re.IGNORECASE),
    "menemukan": re.compile(r"\bmenemukan\b", re.IGNORECASE),
    "dimaklumkan": re.compile(r"\bdimaklumkan\b", re.IGNORECASE),
}
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)|[。！？]+")
_SPACES = re.compile(r"\s+")
_IDS = UUID("0c5b1d7e-3a2f-4b8c-9d61-7e4f2a9b1c00")  # synthetic ids, the same in every run


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Brand(_Strict):
    brand_name: str | None = None
    brand_tone: str | None = None
    persona_name: str | None = None


class Conversation(_Strict):
    id: str = Field(min_length=1)
    language: Literal["en", "ms", "zh", "ta"]
    messages: list[str] = Field(min_length=1)
    # Replaces the script's facts for this conversation (an empty list means no facts): the
    # subscription and stock conversations are told what those agents' facts say.
    facts: list[str] | None = None
    # Facts that differ turn by turn, one list per message: the plan facts on the first turns, the
    # cancel request fact only after the customer asks to cancel. Instead of ``facts``, not with it.
    facts_by_turn: list[list[str]] | None = None
    # The agent the facts come from (a real fact carries its agent's name): the dates rule is said
    # only for a ``subscription_agent`` or ``booking_agent`` fact. Default the knowledge base.
    facts_source: str = Field(default="kb_agent", min_length=1)

    @model_validator(mode="after")
    def _facts_by_turn_fits(self) -> Conversation:
        if self.facts_by_turn is None:
            return self
        if self.facts is not None:
            raise ValueError("give facts or facts_by_turn, not both")
        if len(self.facts_by_turn) != len(self.messages):
            raise ValueError("facts_by_turn needs one list of facts per message")
        return self


class ReplayScript(_Strict):
    """Synthetic only: an invented company, invented facts and invented customer messages."""

    brand: Brand = Field(default_factory=Brand)
    facts: list[str] = Field(default_factory=list)
    conversations: list[Conversation] = Field(min_length=1)


def load_script(path: Path) -> ReplayScript:
    return ReplayScript.model_validate_json(path.read_bytes())


def count_formal_markers(text: str) -> dict[str, int]:
    """How often each formal Malay marker appears in ``text``: whole words, any case."""
    return {name: len(pattern.findall(text)) for name, pattern in FORMAL_MARKERS.items()}


def _sentences(reply: str) -> set[str]:
    parts = (_SPACES.sub(" ", part).strip(" ,;:").casefold() for part in _SENTENCE_END.split(reply))
    return {part for part in parts if part}


def count_repeated_sentences(replies: Sequence[str]) -> int:
    """How many distinct sentences appear in two or more of ``replies`` (case and spacing aside).
    A sentence said twice inside one reply is not counted."""
    seen: dict[str, int] = {}
    for reply in replies:
        for sentence in _sentences(reply):
            seen[sentence] = seen.get(sentence, 0) + 1
    return sum(1 for count in seen.values() if count > 1)


class ModelUnavailable(Exception):
    """The provider failed on the run's first call: a setup problem, so the run stops."""


def _envelope(conversation: Conversation, text: str, turn: int) -> Envelope:
    client_id = uuid5(_IDS, "client")
    thread = f"replay-{conversation.id}"
    return Envelope(
        trace_id=uuid4(),
        received_at=datetime.now(UTC),
        channel=Channel.TELEGRAM,
        idempotency_key=f"replay:{conversation.id}:{turn}",
        client_id=client_id,
        contact=Contact(
            id=uuid5(_IDS, thread),
            external_id=thread,
            channel=Channel.TELEGRAM,
            display_name=None,
        ),
        conversation=ConversationRef(
            id=uuid5(_IDS, f"conversation-{thread}"),
            channel_thread=thread,
            turn_count=turn,
            status="open",
        ),
        message=Message(body_raw=text, body_redacted=redact(text), language=conversation.language),
        meta=Meta(is_first_contact=turn == 0),
    )


async def _reply(
    llm: LLMProvider,
    brand: ClientBrand,
    facts: list[Fact],
    envelope: Envelope,
    ctx: Context,
) -> tuple[str, str, dict[str, Any], str | None]:
    """One customer message: (reply, how it was written, the classifier's answers, and for a
    handoff the lines for staff: the staff note and the gap topic, else None)."""
    signals = await llm.classify(envelope, ctx)
    answers = dict(signals.answers)
    answers["intent"] = signals.intent.value if signals.intent else None
    if answers.get("wants_human") is True:
        state = TurnState(
            envelope=envelope,
            ctx=ctx,
            signals=signals,
            handoff=Handoff(required=True, reason="human_requested"),
        )
        written = await llm.compose_handoff(state, brand)
        # As `_handoff_draft`: only the assistant's own earlier words ground a figure in the
        # reply; the staff note may also rest on the customer's words.
        sources = [m.text for m in ctx.recent if m.author == "assistant"]
        note = fallback_staff_note("hard_escalate", "human_requested")
        gap = None
        if written is not None:
            note_sources = [m.text for m in ctx.recent] + [envelope.message.body_redacted]
            if written.staff_note and staff_note_ok(written.staff_note, note_sources):
                note = written.staff_note
            gap = gap_topic_or_none(written.gap_topic)
        staff = f"staff note: {note}" + (f"\ngap topic: {gap}" if gap else "")
        if written is not None and written.reply and handoff_text_ok(written.reply, sources):
            return written.reply, "handoff, model", answers, staff
        signal = answers.get("language")
        language = resolve_language(
            signal if isinstance(signal, str) else None,
            envelope.message.language,
            envelope.message.body_redacted,
        )
        return handoff_template(language), "handoff, template", answers, staff
    intent = signals.intent or Intent.OTHER
    state = TurnState(
        envelope=envelope,
        ctx=ctx,
        signals=signals,
        plan=Plan(
            tasks=[],
            reply_strategy="auto_reply",
            intent=intent,
            confidence=1,
            source="llm",
        ),
        facts=[] if intent == Intent.CHITCHAT else facts,
    )
    draft = await llm.compose(state, brand=brand)
    return draft.text, draft.composer or "primary", answers, None


def _facts_for_turn(conversation: Conversation, shared: Sequence[str], turn: int) -> list[Fact]:
    texts = shared if conversation.facts_by_turn is None else conversation.facts_by_turn[turn]
    return [
        Fact(id=f"fact_{index}", text=text, source=conversation.facts_source)
        for index, text in enumerate(texts, start=1)
    ]


async def replay(
    llm: LLMProvider,
    script: ReplayScript,
    conversations: Sequence[Conversation],
    out: Callable[[str], Any],
) -> int:
    """Every conversation in turn; prints the replies and counts and returns the exit code."""
    brand = ClientBrand(
        brand_name=script.brand.brand_name,
        brand_tone=script.brand.brand_tone,
        persona_name=script.brand.persona_name,
    )
    tone = script.brand.brand_tone or f"{DEFAULT_TONE} (default)"
    problems = 0
    first = True
    for conversation in conversations:
        out(f"== conversation {conversation.id} (language {conversation.language})")
        out(f"brand_tone: {tone}")
        shared = script.facts if conversation.facts is None else conversation.facts
        recent: list[RecentMessage] = []
        replies: list[str] = []
        handoff_told = False
        for turn, text in enumerate(conversation.messages):
            envelope = _envelope(conversation, text, turn)
            facts = _facts_for_turn(conversation, shared, turn)
            # The script's company sells plans and has a TikTok Shop: every agent is on.
            ctx = Context(
                recent=recent[-RECENT_WINDOW:],
                flags={"handoff_recent": handoff_told},
                capabilities=sorted(CAPABILITIES),
            )
            out(f"[{turn + 1}] customer: {text}")
            try:
                reply, how, answers, staff = await _reply(llm, brand, facts, envelope, ctx)
            except CompletionError as error:
                if first:
                    raise ModelUnavailable from error
                out(f"[{turn + 1}] model error: {error.code}")
                problems += 1
                continue
            first = False
            out(
                f"[{turn + 1}] intent={answers.get('intent')} "
                f"language={answers.get('language')} wants_human={answers.get('wants_human')}"
            )
            out(f"[{turn + 1}] bot ({how}): {reply}")
            if staff is not None:
                for line in staff.splitlines():
                    out(f"[{turn + 1}] {line}")
            replies.append(reply)
            handoff_told = handoff_told or how.startswith("handoff")
            recent.append(
                RecentMessage(
                    author="customer", text=clip_message(envelope.message.body_redacted.strip())
                )
            )
            recent.append(RecentMessage(author="assistant", text=clip_message(redact(reply))))
        markers = count_formal_markers("\n".join(replies))
        repeated = count_repeated_sentences(replies)
        out(
            "formal markers: "
            + " ".join(f"{name}={count}" for name, count in markers.items())
            + f" (total {sum(markers.values())})"
        )
        out(f"repeated sentences: {repeated}")
        if conversation.language == "ms":
            problems += sum(markers.values())
        problems += repeated
    out(f"{len(conversations)} conversation(s), {'problems found' if problems else 'clean'}")
    return 1 if problems else 0


def _setup_error(text: str) -> int:
    print(f"setup error: {text}", file=sys.stderr)
    return 2


def _provider(http: httpx.AsyncClient) -> LLMProvider | None:
    """The worker's customer-bot provider (apps/worker/main.py), from settings (``/run/secrets``
    in the container), or None without a model key. Nothing about a failure is printed: a
    settings error can carry a value."""
    try:
        settings = get_settings()
    except ValidationError:
        return None
    key = settings.GROQ_API_KEY.get_secret_value()
    if not key:
        return None
    fallback: JsonCompletionClient | None = (
        GeminiClient(
            settings.GOOGLE_AI_API_KEY.get_secret_value(),
            settings.GOOGLE_AI_MODEL,
            http,
            meter=NullMeter(),
        )
        if settings.GOOGLE_AI_API_KEY is not None
        else None
    )
    return build_llm_provider(
        GroqClient(key, settings.GROQ_MODEL, http, str(settings.GROQ_CHAT_URL), meter=NullMeter()),
        compose_fallback=fallback,
    )


async def _run(script: ReplayScript, conversations: Sequence[Conversation]) -> int:
    async with httpx.AsyncClient(trust_env=False) as http:
        llm = _provider(http)
        if llm is None:
            return _setup_error("no model key configured (GROQ_API_KEY)")
        try:
            return await replay(llm, script, conversations, print)
        except ModelUnavailable:
            return _setup_error("model unavailable on the first call; nothing was replayed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.customer_bot_replay")
    parser.add_argument("--script", type=Path, default=SCRIPT_FILE, help="replay script (JSON)")
    parser.add_argument(
        "--language", choices=["en", "ms", "zh", "ta"], help="only conversations in this language"
    )
    parser.add_argument(
        "--conversation", action="append", default=[], help="only this conversation id"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="check the script file only; no model call"
    )
    args = parser.parse_args(argv)
    try:
        script = load_script(args.script)
    except (OSError, ValidationError):
        # Fixed text: a validation error would quote the file, an OSError its path.
        return _setup_error("the replay script is missing or invalid")
    conversations = list(script.conversations)
    if args.conversation:
        known = {conversation.id for conversation in conversations}
        if unknown := [cid for cid in args.conversation if cid not in known]:
            return _setup_error(f"unknown conversation id {', '.join(unknown)}")
        conversations = [c for c in conversations if c.id in args.conversation]
    if args.language:
        conversations = [c for c in conversations if c.language == args.language]
    if not conversations:
        return _setup_error("no conversation matches")
    if args.dry_run:
        for conversation in conversations:
            count = len(conversation.messages)
            print(f"{conversation.id} language={conversation.language} messages={count}")
        print(f"{len(conversations)} conversation(s), {len(script.facts)} fact(s): script ok")
        return 0
    # The client's call log (model, schema, latency) goes to stderr, apart from the replies.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))
    return asyncio.run(_run(script, conversations))


if __name__ == "__main__":
    raise SystemExit(main())
