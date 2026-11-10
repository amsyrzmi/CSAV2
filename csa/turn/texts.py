"""Localized fixed texts, language resolution and the light check on a handoff text.

Pure helpers: no database, no model calls. The fixed texts are what the customer sees when a
person takes over (handoff), when a turn fails (holding) and when staff really are following up
(follow-up). Chinese and Tamil copy has not been reviewed by a native speaker.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal, cast

from csa.decide.grounding import ungrounded

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
_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")

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
    return not ungrounded(stripped, sources)
