"""Synthetic Telegram audio downloads and durable worker/coordinator replay."""

from __future__ import annotations

from typing import Any

import httpx
import psycopg
import pytest
from csa.channels.voice import transcribe_telegram_audio
from csa.contracts.envelope import Attachment
from csa.speech import MAX_AUDIO_BYTES, SpeechTranscriber, TranscriptionError
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_worker import _configure_test_settings, _seed_events
from tests.test_worker_coordinator import _close, _counts, _requeue, _worker


def mock_http(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "csa.speech.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.asyncio
async def test_audio_download_and_transcription_use_fixed_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("getFile"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_path": "voice/file_123.oga", "file_size": 16}},
            )
        if request.url.host == "api.telegram.org":
            return httpx.Response(200, content=b"synthetic audio")
        assert request.headers["Authorization"] == "Bearer synthetic-stt"
        assert b"synthetic audio" in request.content
        return httpx.Response(200, json={"text": "Apakah waktu operasi?"})

    mock_http(monkeypatch, handle)
    text = await transcribe_telegram_audio(
        "synthetic-bot",
        Attachment(kind="audio", mime_type="audio/ogg", file_id="file-id"),
        SpeechTranscriber(
            "https://speech.example.test/transcribe", "synthetic-model", "synthetic-stt"
        ),
    )
    assert text == "Apakah waktu operasi?"
    assert [request.url.host for request in calls] == [
        "api.telegram.org",
        "api.telegram.org",
        "speech.example.test",
    ]
    assert calls[1].headers.get("Authorization") is None


@pytest.mark.parametrize(
    "path,size",
    [("../secret", 1), ("https://evil.example/a", 1), ("voice/a.oga", MAX_AUDIO_BYTES + 1)],
)
@pytest.mark.asyncio
async def test_rejects_unsafe_downloads(
    monkeypatch: pytest.MonkeyPatch, path: str, size: int
) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"ok": True, "result": {"file_path": path, "file_size": size}}
        )

    mock_http(monkeypatch, handle)
    with pytest.raises(TranscriptionError, match="audio_download_failed"):
        await transcribe_telegram_audio(
            "synthetic-bot",
            Attachment(kind="audio", mime_type="audio/ogg", file_id="id"),
            SpeechTranscriber("https://speech.example.test", "model"),
        )
    assert len(calls) == 1


@requires_docker
@pytest.mark.asyncio
@pytest.mark.usefixtures("lg_setup_dsn")
@pytest.mark.parametrize("failure", [False, True])
async def test_worker_caches_transcription_and_coordinator_outcome(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    _, client_id, ids = _seed_events(owner_dsn)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE ingress_events SET raw=jsonb_set(raw,'{envelope,message,attachments}',%s) "
            "WHERE id=%s",
            (
                Jsonb([{"kind": "audio", "mime_type": "audio/ogg", "file_id": "synthetic-file"}]),
                ids[0],
            ),
        )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/a.oga"}})
        if request.url.host == "api.telegram.org":
            return httpx.Response(200, content=b"synthetic audio")
        if failure:
            return httpx.Response(503, text="private-provider-payload")
        return httpx.Response(200, json={"text": "Contact me at demo@example.test"})

    mock_http(monkeypatch, handle)
    pools = await _worker(owner_dsn, app_dsn)
    worker = pools[3]
    worker._speech = SpeechTranscriber("https://speech.example.test/transcribe", "synthetic-model")
    try:
        assert await worker.process_once()
        first = _counts(owner_dsn, client_id)
        assert first["status"] == "completed"
        with psycopg.connect(owner_dsn) as conn:
            row = conn.execute("SELECT raw FROM ingress_events WHERE id=%s", (ids[0],)).fetchone()
        assert row is not None
        raw = row[0]
        assert raw["voice_processed"] is True
        message = raw["envelope"]["message"]
        assert message["attachments"][0]["kind"] == "audio", "preserve attachment review policy"
        if failure:
            assert message["voice"]["transcript"] is None
            assert first["escalations"], "voice failures enter the normal escalation path"
        else:
            assert "demo@example.test" not in message["voice"]["transcript"]
            assert first["outbound"], "transcript runs through the existing coordinator"
        _requeue(owner_dsn, ids[0])
        assert await worker.process_once()
        assert _counts(owner_dsn, client_id) == first
        assert len(calls) == 3, (
            "replay uses cached transcript or failure, with no download/STT retry"
        )
    finally:
        await _close(*pools[:3])
