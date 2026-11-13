"""Timed transcription for training videos: the request, the parsing and the error codes.

Every provider answer is synthetic (httpx ``MockTransport``); no real endpoint is called.
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest
from csa.speech import (
    SEGMENTS_MAX_RESPONSE_BYTES,
    SEGMENTS_MAX_TEXT_CHARS,
    SpeechTranscriber,
    TimedSegment,
    TimedTranscript,
    TranscriptionError,
)

from tests.test_telegram_voice import mock_http

pytestmark = pytest.mark.asyncio

ENDPOINT = "https://speech.example.test/transcribe"
KEY = "synthetic-stt-key"
AUDIO = b"OggS synthetic chunk audio"


def speech() -> SpeechTranscriber:
    return SpeechTranscriber(ENDPOINT, "synthetic-model", KEY)


def field(request: httpx.Request, name: str) -> bytes | None:
    """The value of one multipart form field of ``request`` (``None`` when absent)."""
    marker = f'name="{name}"\r\n\r\n'.encode()
    body = request.content
    at = body.find(marker)
    if at < 0:
        return None
    start = at + len(marker)
    return body[start : body.index(b"\r\n", start)]


def verbose(**extra: Any) -> dict[str, Any]:
    return {
        "task": "transcribe",
        "language": "english",
        "duration": 300.0,
        "text": "Welcome to the induction. Wash your hands first.",
        "segments": [
            {
                "id": 0,
                "seek": 0,
                "start": 0.5,
                "end": 2.25,
                "text": " Welcome to the induction.",
                "tokens": [1, 2, 3],
                "temperature": 0.0,
                "avg_logprob": -0.2,
                "compression_ratio": 1.1,
                "no_speech_prob": 0.01,
            },
            {
                "id": 1,
                "seek": 0,
                "start": 2.25,
                "end": 4.0,
                "text": " Wash your hands first.",
                "tokens": [4, 5],
                "temperature": 0.0,
                "avg_logprob": -0.4,
                "compression_ratio": 1.2,
                "no_speech_prob": 0.02,
            },
        ],
        **extra,
    }


async def test_verbose_json_is_requested_and_segments_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=verbose())

    mock_http(monkeypatch, handle)
    found = await speech().transcribe_segments(AUDIO, "audio/ogg")

    assert found == TimedTranscript(
        language="english",
        segments=[
            TimedSegment(0.5, 2.25, "Welcome to the induction.", 0.01, -0.2),
            TimedSegment(2.25, 4.0, "Wash your hands first.", 0.02, -0.4),
        ],
    )
    [request] = calls
    assert str(request.url) == ENDPOINT
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert field(request, "model") == b"synthetic-model"
    assert field(request, "response_format") == b"verbose_json"
    assert field(request, "timestamp_granularities[]") == b"segment"
    assert b'filename="recording.ogg"' in request.content
    assert AUDIO in request.content


async def test_answer_without_segments_becomes_one_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = [
        {"text": " Hello and welcome. ", "language": "malay", "duration": 12.5},
        {"text": "No duration reported."},
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answers.pop(0))

    mock_http(monkeypatch, handle)
    first = await speech().transcribe_segments(AUDIO, "audio/ogg")
    assert first == TimedTranscript("malay", [TimedSegment(0.0, 12.5, "Hello and welcome.")])
    # Without a reported duration the segment runs to the end of the chunk (the caller clips).
    second = await speech().transcribe_segments(AUDIO, "audio/ogg")
    assert second.language is None
    [segment] = second.segments
    assert (segment.start, segment.text) == (0.0, "No duration reported.")
    assert math.isinf(segment.end)


async def test_oversized_answer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    long_text = "a" * (SEGMENTS_MAX_TEXT_CHARS + 1)
    answers: list[httpx.Response] = [
        httpx.Response(200, content=b"{" + b" " * SEGMENTS_MAX_RESPONSE_BYTES + b"}"),
        httpx.Response(200, json={"text": long_text}),
        httpx.Response(
            200,
            json={
                "text": "short",
                "segments": [
                    {"start": 0, "end": 1, "text": "b" * (SEGMENTS_MAX_TEXT_CHARS // 2 + 1)},
                    {"start": 1, "end": 2, "text": "c" * (SEGMENTS_MAX_TEXT_CHARS // 2 + 1)},
                ],
            },
        ),
        httpx.Response(
            200,
            json={
                "text": "back to front",
                "segments": [{"start": 3, "end": 1, "text": "time runs backwards"}],
            },
        ),
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    mock_http(monkeypatch, handle)
    for _ in range(4):
        with pytest.raises(TranscriptionError, match=r"^transcription_failed$"):
            await speech().transcribe_segments(AUDIO, "audio/ogg")
    # The request side is bounded too: no call for audio over the cap or of an unknown type.
    with pytest.raises(TranscriptionError, match=r"^invalid_audio$"):
        await speech().transcribe_segments(b"x" * (8 * 1024 * 1024 + 1), "audio/ogg")
    with pytest.raises(TranscriptionError, match=r"^invalid_audio$"):
        await speech().transcribe_segments(AUDIO, "video/mp4")
    assert answers == []


async def test_429_and_timeout_map_to_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    steps: list[Any] = [
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(503, text="unavailable"),
        httpx.ReadTimeout("synthetic read timeout"),
        httpx.ConnectTimeout("synthetic connect timeout"),
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    mock_http(monkeypatch, handle)
    codes = []
    for _ in range(4):
        with pytest.raises(TranscriptionError) as caught:
            await speech().transcribe_segments(AUDIO, "audio/ogg")
        codes.append(caught.value.code)
    assert codes == [
        "transcription_failed",
        "transcription_failed",
        "transcription_timeout",
        "transcription_timeout",
    ]


async def test_errors_carry_no_audio_or_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"echo {KEY} {AUDIO!r} private-provider-payload")

    mock_http(monkeypatch, handle)
    with pytest.raises(TranscriptionError) as caught:
        await speech().transcribe_segments(AUDIO, "audio/ogg")
    error = caught.value
    assert error.code == "transcription_failed"
    assert error.__cause__ is None and error.__suppress_context__
    for text in (str(error), repr(error), str(error.args)):
        assert KEY not in text
        assert "synthetic chunk audio" not in text
        assert "private-provider-payload" not in text
        assert "speech.example.test" not in text


async def test_voice_note_transcribe_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    answers = [{"text": " Apakah waktu operasi? "}, {"text": "a" * 4001}]

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=answers.pop(0))

    mock_http(monkeypatch, handle)
    assert await speech().transcribe(AUDIO, "audio/ogg") == "Apakah waktu operasi?"
    with pytest.raises(TranscriptionError, match=r"^transcription_failed$"):
        await speech().transcribe(AUDIO, "audio/ogg")
    assert [field(request, "response_format") for request in calls] == [b"json", b"json"]
    assert all(field(request, "timestamp_granularities[]") is None for request in calls)
