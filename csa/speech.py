"""Shared bounded transcription; provider errors never include audio or credential URLs.

``transcribe`` serves voice notes (``response_format=json``, a 4000-character text).
``transcribe_segments`` serves training video chunks: the same endpoint, key and model with
``response_format=verbose_json`` and ``timestamp_granularities[]=segment``, answering timed
segments. Segment field names (``segments[].start``, ``end``, ``text``, ``no_speech_prob``,
``avg_logprob``; top-level ``text``, ``language``, ``duration``) follow the provider's
speech-to-text documentation (https://console.groq.com/docs/speech-to-text) and the
OpenAI-compatible verbose transcription object it mirrors.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MIMES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}

SEGMENTS_MAX_RESPONSE_BYTES = 1024 * 1024
"""Largest timed answer read for one chunk."""

SEGMENTS_MAX_TEXT_CHARS = 20_000
"""Most characters of text one timed answer may carry (whole text, and its segments together)."""

_LANGUAGE_MAX_CHARS = 16


class TranscriptionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Transcript(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@dataclass(frozen=True)
class TimedSegment:
    """One stretch of speech, in seconds from the start of the audio sent."""

    start: float
    end: float
    text: str
    no_speech_prob: float | None = None
    avg_logprob: float | None = None


@dataclass(frozen=True)
class TimedTranscript:
    """A timed answer: the language the provider reported (if any) and its segments."""

    language: str | None
    segments: list[TimedSegment]


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _VerboseSegment(_Loose):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(max_length=SEGMENTS_MAX_TEXT_CHARS)
    no_speech_prob: float | None = Field(default=None, allow_inf_nan=False)
    avg_logprob: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> _VerboseSegment:
        if self.end < self.start:
            raise ValueError("segment ends before it starts")
        return self


class _Verbose(_Loose):
    text: str = Field(max_length=SEGMENTS_MAX_TEXT_CHARS)
    language: str | None = None
    duration: float | None = Field(default=None, allow_inf_nan=False)
    segments: list[_VerboseSegment] | None = None

    @model_validator(mode="after")
    def _bounded(self) -> _Verbose:
        if sum(len(segment.text) for segment in self.segments or []) > SEGMENTS_MAX_TEXT_CHARS:
            raise ValueError("segments longer than a timed answer may be")
        return self


class SpeechTranscriber:
    def __init__(self, endpoint: str, model: str, key: str | None = None) -> None:
        self.endpoint, self.model, self.key = endpoint, model, key

    async def transcribe(self, audio: bytes, mime: str) -> str:
        mime = mime.split(";", 1)[0].strip()
        if mime not in MIMES or not audio or len(audio) > MAX_AUDIO_BYTES:
            raise TranscriptionError("invalid_audio")
        try:
            async with asyncio.timeout(35):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=5), follow_redirects=False
                ) as http:
                    async with http.stream(
                        "POST",
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
                        data={"model": self.model, "response_format": "json"},
                        files={"file": (f"recording.{MIMES[mime]}", audio, mime)},
                    ) as response:
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(data) + len(chunk) > 65536:
                                raise ValueError("oversized transcription")
                            data.extend(chunk)
                        text = Transcript.model_validate_json(data).text.strip()
                        if not text:
                            raise ValueError("empty transcription")
                        return text
        except (TimeoutError, httpx.TimeoutException):
            raise TranscriptionError("transcription_timeout") from None
        except (httpx.HTTPError, ValueError):
            raise TranscriptionError("transcription_failed") from None

    async def transcribe_segments(self, audio: bytes, mime: str) -> TimedTranscript:
        """Timed segments of one audio chunk (90 s overall; 85 s read, 5 s connect).

        Any HTTP error status (429 and 5xx included), an answer over
        ``SEGMENTS_MAX_RESPONSE_BYTES`` or ``SEGMENTS_MAX_TEXT_CHARS``, or one that does not parse
        is ``transcription_failed``; a timeout is ``transcription_timeout``. A valid answer
        without ``segments`` becomes one segment from 0 to the reported ``duration`` (infinite
        when none is reported: the caller clips it to the chunk) holding the whole text.
        """
        mime = mime.split(";", 1)[0].strip()
        if mime not in MIMES or not audio or len(audio) > MAX_AUDIO_BYTES:
            raise TranscriptionError("invalid_audio")
        try:
            async with asyncio.timeout(90):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(85, connect=5), follow_redirects=False
                ) as http:
                    async with http.stream(
                        "POST",
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
                        data={
                            "model": self.model,
                            "response_format": "verbose_json",
                            "timestamp_granularities[]": "segment",
                        },
                        files={"file": (f"recording.{MIMES[mime]}", audio, mime)},
                    ) as response:
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(data) + len(chunk) > SEGMENTS_MAX_RESPONSE_BYTES:
                                raise ValueError("oversized transcription")
                            data.extend(chunk)
            return _timed(_Verbose.model_validate_json(data))
        except (TimeoutError, httpx.TimeoutException):
            raise TranscriptionError("transcription_timeout") from None
        except (httpx.HTTPError, ValueError, ValidationError):
            raise TranscriptionError("transcription_failed") from None


def _timed(answer: _Verbose) -> TimedTranscript:
    language = (answer.language or "").strip() or None
    if language is not None and len(language) > _LANGUAGE_MAX_CHARS:
        language = None
    if answer.segments is None:
        text = answer.text.strip()
        end = answer.duration if answer.duration is not None and answer.duration > 0 else math.inf
        segments = [TimedSegment(0.0, end, text)] if text else []
        return TimedTranscript(language, segments)
    return TimedTranscript(
        language,
        [
            TimedSegment(
                segment.start,
                segment.end,
                segment.text.strip(),
                segment.no_speech_prob,
                segment.avg_logprob,
            )
            for segment in answer.segments
        ],
    )
