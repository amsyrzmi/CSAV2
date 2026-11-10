"""Localized handoff/holding texts, language resolution and the light handoff-text check."""

from __future__ import annotations

from csa.turn.texts import (
    Language,
    clip_message,
    detect_language,
    followup_text,
    handoff_template,
    handoff_text_ok,
    holding_text,
    resolve_language,
)

LANGUAGES: tuple[Language, ...] = ("en", "ms", "zh", "ta")


def test_resolve_language_prefers_signal_then_envelope_then_detector() -> None:
    """A valid signal wins, then the envelope's primary subtag, then the body detector."""
    assert resolve_language("ms", "und", "hi") == "ms"
    assert resolve_language("other", "en-GB", "nak tanya boleh") == "en"
    assert resolve_language("other", "und", "nak tanya boleh x") == "ms"
    assert resolve_language("other", "und", "你好") == "zh"
    assert resolve_language(None, "und", "hello there") == "en"


def test_detect_language_needs_two_malay_words() -> None:
    """One Malay word is not enough; Tamil and Chinese scripts are detected directly."""
    assert detect_language("boleh") == "en"
    assert detect_language("boleh tak") == "ms"
    assert detect_language("வணக்கம்") == "ta"


def test_clip_message_keeps_start_and_end() -> None:
    """Over 200 characters keeps the first 120 and last 79 around an ellipsis."""
    text = "".join(chr(ord("a") + i % 26) for i in range(480)) + "THE-END-OF-THE-MESSAGE-" + "z"
    assert len(text) == 504
    clipped = clip_message(text)
    assert len(clipped) == 200
    assert clipped.startswith(text[:120])
    assert clipped.endswith(text[-79:])
    assert "…" in clipped
    exact = "x" * 200
    assert clip_message(exact) == exact


def test_every_language_has_every_template() -> None:
    """All four languages have a handoff, holding and follow-up text."""
    for language in LANGUAGES:
        assert handoff_template(language).strip()
        assert holding_text(language).strip()
        assert followup_text(language).strip()


def test_handoff_text_ok_rejects() -> None:
    """Too long, too many sentences, an ungrounded number, or empty text fail the check."""
    assert not handoff_text_ok("a" * 301, [])
    assert not handoff_text_ok("One. Two. Three.", [])
    assert not handoff_text_ok("A team member will reply in 2 hours.", ["no digits here"])
    assert not handoff_text_ok("", [])
    assert not handoff_text_ok("   ", [])


def test_handoff_text_ok_accepts() -> None:
    """Plain short text passes; a number present in a source is grounded; templates pass."""
    assert handoff_text_ok("Saya dah maklumkan pada team ya.", [])
    assert handoff_text_ok("We open at 9 tomorrow.", ["Opening hours: 9 to 5"])
    for language in LANGUAGES:
        assert handoff_text_ok(handoff_template(language), [])


def test_handoff_text_ok_counts_cjk_sentence_ends() -> None:
    """Chinese full stops end a sentence without a space; two pass, three fail."""
    assert not handoff_text_ok("一。二。三。", [])
    assert handoff_text_ok("我已经通知团队。他们会回复您。", [])


def test_handoff_text_ok_keeps_decimal_price_as_one_sentence() -> None:
    """A dot between digits is not a sentence end."""
    assert handoff_text_ok("RM1.50 only.", ["RM1.50"])


def test_handoff_text_ok_rejects_bare_links_unless_in_sources() -> None:
    """A link without a scheme must appear in a source, like a number."""
    assert not handoff_text_ok("Visit www.shop.example now.", [])
    assert handoff_text_ok("Visit www.shop.example now.", ["See www.shop.example for more"])
