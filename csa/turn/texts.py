"""Localized fixed texts, language resolution and the light check on a handoff text.

Pure helpers: no database, no model calls. The fixed texts are what the customer sees when a
person takes over (handoff), when a turn fails (holding) and when staff really are following up
(follow-up). Chinese and Tamil copy has not been reviewed by a native speaker.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal, cast

from csa.decide.grounding import ungrounded, unsupported_figures

Language = Literal["en", "ms", "zh", "ta"]

_SUPPORTED: frozenset[str] = frozenset({"en", "ms", "zh", "ta"})

_MALAY_WORDS: frozenset[str] = frozenset(
    {
        "tak",
        "nak",
        "boleh",
        "saya",
        "dah",
        "nk",
        "x",
        "tlg",
        "sy",
        "ada",
        "apa",
        "macam",
        "bleh",
        "tgk",
        "yang",
        "untuk",
        "tolong",
    }
)

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_TAMIL = re.compile(r"[஀-௿]")
_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)|[。！？]+")

_HANDOFF: dict[Language, str] = {
    "en": "I've passed this to a team member who will get back to you shortly.",
    "ms": "Baik, saya dah maklumkan pada team kami. Mereka akan balas dalam chat ni ya.",
    "zh": "我已经把这个转给团队成员，他们会在这个聊天里回复您。",
    "ta": "இதை எங்கள் குழு உறுப்பினரிடம் தெரிவித்துவிட்டேன்; அவர்கள் இந்த அரட்டையில் பதிலளிப்பார்கள்.",
}

_HOLDING: dict[Language, str] = {
    "en": "Sorry, something went wrong on our side. Could you send that again in a moment?",
    "ms": "Maaf, ada gangguan sekejap. Boleh hantar semula mesej tu sekejap lagi?",
    "zh": "抱歉，系统暂时出了点问题。请稍后再发一次。",
    "ta": "மன்னிக்கவும், சிறிய தொழில்நுட்பச் சிக்கல். சிறிது நேரம் கழித்து மீண்டும் அனுப்ப முடியுமா?",
}

_FOLLOWUP: dict[Language, str] = {
    "en": "Thanks for your message. A team member will follow up with you shortly.",
    "ms": "Terima kasih. Team kami akan balas dalam chat ni ya.",
    "zh": "谢谢您的留言。团队成员会在这个聊天里回复您。",
    "ta": "உங்கள் செய்திக்கு நன்றி. எங்கள் குழு உறுப்பினர் இந்த அரட்டையில் பதிலளிப்பார்.",
}

_CLIP_LIMIT = 200
_CLIP_HEAD = 120
_CLIP_TAIL = 79
_HANDOFF_MAX_CHARS = 300
_HANDOFF_MAX_SENTENCES = 2
_GAP_TOPIC_MIN_CHARS = 3
_GAP_TOPIC_MAX_CHARS = 60
_GAP_TOPIC_MAX_WORDS = 6
_GAP_TOPIC_CHARS = re.compile(r"[a-z' -]+")

# Staff notes for a handoff the model did not write (or wrote badly): by the handoff's reason
# first, then, for an escalating plan, by the route that produced it.
_NOTE_BY_REASON: dict[str, str] = {
    "human_requested": "The customer asked for a person.",
    "angry_customer": "The customer is upset; the assistant handed over at once.",
    "unresolved_turns": (
        "The assistant could not answer after three tries. Read the chat for what is missing."
    ),
    "agent_failure": "A lookup failed, so the assistant could not answer.",
    "service_off": "The question needs a service this company has not switched on.",
}
_NOTE_BY_ROUTE: dict[str, str] = {
    "hard_escalate": (
        "The message has a legal or police word, so the assistant handed over without answering."
    ),
    "guard_blocked": (
        "The safety check stopped this message, so the assistant handed over without answering."
    ),
    "voice_failed": "A voice message could not be understood.",
    "unsupported_language": "The customer wrote in a language the assistant does not answer.",
}
_NOTE_DEFAULT = "The assistant handed this chat over. Read the chat for details."


def detect_language(text: str) -> Language:
    """Guess the language of ``text``: Chinese or Tamil by script, Malay by common words."""
    if _CJK.search(text):
        return "zh"
    if _TAMIL.search(text):
        return "ta"
    words = _WORD.findall(text.lower())
    if sum(1 for word in words if word in _MALAY_WORDS) >= 2:
        return "ms"
    return "en"


def resolve_language(signal: str | None, envelope_language: str, body: str) -> Language:
    """Pick the reply language: the model's signal, else the envelope's, else the detector."""
    if signal in _SUPPORTED:
        return cast(Language, signal)
    primary = envelope_language.strip().lower().replace("_", "-").split("-")[0]
    if primary in _SUPPORTED:
        return cast(Language, primary)
    return detect_language(body)


def handoff_template(language: Language) -> str:
    """The fixed text telling the customer a team member has been told."""
    return _HANDOFF[language]


def holding_text(language: Language) -> str:
    """The fixed text for a turn that failed on our side."""
    return _HOLDING[language]


def followup_text(language: Language) -> str:
    """The fixed text for when staff really are following up (approval outcomes)."""
    return _FOLLOWUP[language]


def clip_message(text: str) -> str:
    """Cap a message at 200 characters, keeping its start and its end around an ellipsis."""
    if len(text) <= _CLIP_LIMIT:
        return text
    return text[:_CLIP_HEAD] + "…" + text[-_CLIP_TAIL:]


def handoff_text_ok(text: str, sources: Iterable[str]) -> bool:
    """Light check on a model-written handoff text: short, two sentences at most, grounded."""
    stripped = text.strip()
    if not stripped or len(stripped) > _HANDOFF_MAX_CHARS:
        return False
    sentences = [part for part in _SENTENCE_END.split(stripped) if part.strip()]
    if len(sentences) > _HANDOFF_MAX_SENTENCES:
        return False
    pool = list(sources)
    return not ungrounded(stripped, pool) and not unsupported_figures(stripped, pool)


def staff_note_ok(text: str, sources: Iterable[str]) -> bool:
    """Light check on a model-written staff note: as `handoff_text_ok`, grounded in the sources.

    Unlike the customer text, the sources may include the customer's own messages: the note is
    for staff and promises nothing."""
    return handoff_text_ok(text, sources)


def gap_topic_or_none(text: str | None) -> str | None:
    """A model-written gap topic cleaned up, or None: lower case, spaces collapsed, 3 to 60
    characters, at most six words, letters, spaces, apostrophes and hyphens only."""
    if text is None:
        return None
    topic = " ".join(text.lower().split())
    if not _GAP_TOPIC_MIN_CHARS <= len(topic) <= _GAP_TOPIC_MAX_CHARS:
        return None
    if len(topic.split(" ")) > _GAP_TOPIC_MAX_WORDS or not _GAP_TOPIC_CHARS.fullmatch(topic):
        return None
    return topic


def fallback_staff_note(route: str | None, reason: str) -> str:
    """The fixed staff note for a handoff without a usable model note: by reason, then by route
    for an escalating plan, else a general line."""
    if reason in _NOTE_BY_REASON:
        return _NOTE_BY_REASON[reason]
    if reason == "plan_escalated" and route in _NOTE_BY_ROUTE:
        return _NOTE_BY_ROUTE[route]
    return _NOTE_DEFAULT
