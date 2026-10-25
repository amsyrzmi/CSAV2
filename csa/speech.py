"""Shared bounded transcription; provider errors never include audio or credential URLs."""

from __future__ import annotations

import asyncio

import httpx
from pydantic import BaseModel, Field

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MIMES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}


class TranscriptionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Transcript(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


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
