"""Telegram audio download and transcription after durable ingress acknowledgement."""

from __future__ import annotations

import asyncio
import re

import httpx
from pydantic import BaseModel, Field

from csa.contracts.envelope import Attachment
from csa.speech import MAX_AUDIO_BYTES, SpeechTranscriber, TranscriptionError


class TelegramFile(BaseModel):
    file_path: str
    file_size: int | None = Field(default=None, ge=0)


class TelegramFileResponse(BaseModel):
    ok: bool
    result: TelegramFile


async def transcribe_telegram_audio(
    token: str, audio: Attachment, speech: SpeechTranscriber
) -> str:
    if not audio.file_id or (audio.size_bytes or 0) > MAX_AUDIO_BYTES:
        raise TranscriptionError("invalid_audio")
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15, connect=5), follow_redirects=False
            ) as http:
                async with http.stream(
                    "POST",
                    f"https://api.telegram.org/bot{token}/getFile",
                    json={"file_id": audio.file_id},
                ) as response:
                    response.raise_for_status()
                    metadata = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(metadata) + len(chunk) > 65536:
                            raise ValueError("oversized metadata")
                        metadata.extend(chunk)
                record = TelegramFileResponse.model_validate_json(metadata)
                path = record.result.file_path
                if (
                    not record.ok
                    or (record.result.file_size or 0) > MAX_AUDIO_BYTES
                    or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
                    or any(segment in {"", ".", ".."} for segment in path.split("/"))
                ):
                    raise ValueError("invalid Telegram file")
                async with http.stream(
                    "GET", f"https://api.telegram.org/file/bot{token}/{path}"
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > MAX_AUDIO_BYTES:
                            raise ValueError("oversized audio")
                        data.extend(chunk)
        return await speech.transcribe(bytes(data), audio.mime_type)
    except TranscriptionError:
        raise
    except (TimeoutError, httpx.HTTPError, ValueError):
        raise TranscriptionError("audio_download_failed") from None
