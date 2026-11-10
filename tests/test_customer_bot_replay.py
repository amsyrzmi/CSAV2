"""The two counters of the customer bot replay script (scripts/customer_bot_replay.py).

The replay itself calls the real model inside the staging container and is never run in tests.
"""

from __future__ import annotations

from scripts.customer_bot_replay import count_formal_markers, count_repeated_sentences


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
