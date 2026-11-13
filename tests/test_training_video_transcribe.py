"""The transcribing stage on synthetic videos: chunks, offsets, resumes, silence and refusals.

Every video is made by ffmpeg in the test's temporary directory (``tests/training_media.py``);
the speech provider is a stub that records which chunk it was sent and never leaves the process.
``AUDIO_CHUNK_SECONDS`` is 2 here, so a 5-second video is three chunks (2 s, 2 s, 1 s).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from csa.agents.kb.embedder import HASH_MODEL, DefaultKbEmbedder
from csa.core import db
from csa.core.tenancy import tx
from csa.speech import SpeechTranscriber, TimedSegment, TimedTranscript, TranscriptionError
from csa.storage.base import ObjectStore, StorageError
from csa.storage.local import LocalObjectStore
from csa.training import limits, media, video_transcribe
from csa.training.media import MediaUnavailable
from csa.training.video_jobs import Claim, JobHandle, JobLost, VideoProcessor
from csa.training.video_transcribe import (
    MIN_SPEECH_CHARS,
    NO_SPEECH_PROB_MAX,
    SegmentRow,
    TranscribeStage,
    WindowRow,
    build_windows,
    keep_segments,
)
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from tests.conftest import requires_docker, requires_ffmpeg
from tests.test_console_sessions import SessionWorld
from tests.test_training_api import ITEMS, enable, remove
from tests.training_media import AudioKind, Container, make_video

pytestmark = [requires_docker, requires_ffmpeg, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TRANSCRIPTION = "training_transcription_unavailable"
STORAGE = "training_storage_unavailable"
TITLE = "Synthetic induction walkthrough"
Hook = Callable[[int, int], Awaitable[TimedTranscript | None]]


class SourceStore(LocalObjectStore):
    """The local training store that hands out a fresh copy of the video for every source asked.

    Each copy stands for one presigned URL; ``https`` makes it answer an S3-looking URL instead,
    and ``size_answer`` answers ``object_size`` (a number, or an error to raise).
    """

    def __init__(self, root: Path, sources: Path) -> None:
        super().__init__(root)
        self.sources_dir = sources
        self.sources: list[tuple[str, int]] = []
        self.https: str | None = None
        self.size_answer: int | StorageError | None = None

    def media_source(self, tenant: UUID, name: str, ttl_seconds: int) -> str:
        if self.https is not None:
            self.sources.append((self.https, ttl_seconds))
            return self.https
        original = super().media_source(tenant, name, ttl_seconds)
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        fresh = self.sources_dir / f"source-{len(self.sources)}{Path(name).suffix}"
        shutil.copyfile(original, fresh)
        self.sources.append((str(fresh), ttl_seconds))
        return str(fresh)

    def object_size(self, tenant: UUID, name: str) -> int:
        if isinstance(self.size_answer, StorageError):
            raise self.size_answer
        if self.size_answer is not None:
            return self.size_answer
        return super().object_size(tenant, name)


class StubSpeech(SpeechTranscriber):
    """Records ``(chunk index, bytes)`` per call; answers one 0.5-1.5 s segment unless hooked."""

    def __init__(self, extracts: list[tuple[int, int]]) -> None:
        super().__init__("https://speech.example.test/transcribe", "stub-model")
        self.extracts = extracts
        self.calls: list[tuple[int, int]] = []
        self.hook: Hook | None = None

    async def transcribe_segments(self, audio: bytes, mime: str) -> TimedTranscript:
        assert mime == "audio/ogg" and audio.startswith(b"OggS")
        index = self.extracts[-1][0] // limits.AUDIO_CHUNK_SECONDS
        self.calls.append((index, len(audio)))
        if self.hook is not None:
            answer = await self.hook(index, len(self.calls))
            if answer is not None:
                return answer
        return TimedTranscript("english", [TimedSegment(0.5, 1.5, spoken(index), 0.01, -0.2)])


class Transcribing:
    """The worker's stages with only transcribing: no summary job is run in these tests."""

    def __init__(self, stage: TranscribeStage) -> None:
        self.stage = stage

    async def transcribe(self, claim: Claim, job: JobHandle) -> Literal["summarising", "ready"]:
        return await self.stage.transcribe(claim, job)

    async def summarise(self, claim: Claim, job: JobHandle) -> None:
        raise AssertionError("no summary job runs in the transcribing tests")


def spoken(index: int) -> str:
    return f"Chunk {index} carries synthetic speech words."


@pytest.fixture
def training_store(tmp_path: Path) -> ObjectStore:
    return SourceStore(tmp_path / "training", tmp_path / "sources")


@pytest.fixture
async def owner_pool(sessions: SessionWorld) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(sessions.owner_dsn, min_size=1, max_size=2)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


class World:
    """One company's worker with the stub provider, the recorded extractions and heartbeats."""

    def __init__(
        self,
        sessions: SessionWorld,
        owner_pool: AsyncConnectionPool,
        store: SourceStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.sessions = sessions
        self.store = store
        self.tmp_path = tmp_path
        self.tenant = sessions.tenants[0]
        self.extracts: list[tuple[int, int]] = []
        self.extract_sources: list[str] = []
        self.heartbeats = 0
        self.speech = StubSpeech(self.extracts)
        self.stage = TranscribeStage(store, self.speech, DefaultKbEmbedder())
        self.worker = VideoProcessor(owner_pool, sessions.pool, store, Transcribing(self.stage))
        real_extract = media.extract_chunk

        async def recording_extract(
            source: str, container: Any, start_seconds: int, seconds: int, out: Path
        ) -> int:
            self.extracts.append((start_seconds, seconds))
            self.extract_sources.append(source)
            return await real_extract(source, container, start_seconds, seconds, out)

        real_heartbeat = JobHandle.heartbeat

        async def counting_heartbeat(handle: JobHandle) -> None:
            self.heartbeats += 1
            await real_heartbeat(handle)

        monkeypatch.setattr(video_transcribe, "extract_chunk", recording_extract)
        monkeypatch.setattr(JobHandle, "heartbeat", counting_heartbeat)
        monkeypatch.setattr(limits, "AUDIO_CHUNK_SECONDS", 2)

    async def video(
        self,
        *,
        seconds: float,
        audio: AudioKind = "tone",
        container: Container = "mp4",
        video_codec: str = "libx264",
    ) -> tuple[str, str]:
        """A synthetic video stored for the company, its item uploaded and its job queued."""
        stored = "webm" if container == "webm" else "mp4"
        name = f"{uuid4()}.{stored}"
        directory = self.tmp_path / "training" / self.tenant
        directory.mkdir(parents=True, exist_ok=True)
        path = make_video(
            directory / name,
            seconds=seconds,
            audio=audio,
            container=container,
            video_codec=video_codec,
        )
        [made] = await self.sessions.q(
            "INSERT INTO training_items (id, client_id, kind, title, view_mode, storage_name,"
            " original_name, media_type, byte_size, sha256, page_count, request_key,"
            " fingerprint, status, container) VALUES (gen_random_uuid(), %s, 'video', %s, NULL,"
            " %s, 'induction.mp4', %s, %s, %s, NULL, %s, 'synthetic', 'uploaded', %s)"
            " RETURNING id",
            self.tenant,
            TITLE,
            name,
            limits.PLAYBACK_MEDIA_TYPE[stored],
            path.stat().st_size,
            "0" * 64,
            uuid4(),
            stored,
        )
        item = str(made["id"])
        await self.sessions.q(
            "INSERT INTO training_video_jobs (client_id, item_id) VALUES (%s, %s)",
            self.tenant,
            item,
        )
        return item, name

    async def claim(self) -> Claim:
        claim = await self.worker.claim(UUID(self.tenant))
        assert claim is not None
        return claim

    async def run(self) -> str:
        return await self.worker.process(await self.claim())

    async def one(self, statement: Any, *params: Any) -> dict[str, Any]:
        [row] = await self.sessions.q(statement, *params)
        return row

    async def item(self, item: str) -> dict[str, Any]:
        return await self.one("SELECT * FROM training_items WHERE id = %s", item)

    async def job(self, item: str) -> dict[str, Any] | None:
        found = await self.sessions.q("SELECT * FROM training_video_jobs WHERE item_id = %s", item)
        return found[0] if found else None

    async def segments(self, item: str) -> list[dict[str, Any]]:
        return await self.sessions.q(
            "SELECT chunk_index, position, start_ms, end_ms, text"
            " FROM training_transcript_segments WHERE item_id = %s"
            " ORDER BY chunk_index, position",
            item,
        )

    async def windows(self, item: str) -> list[dict[str, Any]]:
        return await self.sessions.q(
            "SELECT position, start_ms, end_ms, text, embed_model"
            " FROM training_transcript_windows WHERE item_id = %s ORDER BY position",
            item,
        )

    async def usage(self, item: str) -> list[int]:
        rows = await self.sessions.q(
            "SELECT seconds FROM training_video_usage WHERE item_id = %s", item
        )
        return [row["seconds"] for row in rows]

    async def make_due(self, item: str) -> None:
        await self.sessions.q(
            "UPDATE training_video_jobs SET next_attempt_at = now() WHERE item_id = %s", item
        )


@pytest.fixture
def world(
    sessions: SessionWorld,
    owner_pool: AsyncConnectionPool,
    training_store: ObjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    assert isinstance(training_store, SourceStore)
    return World(sessions, owner_pool, training_store, tmp_path, monkeypatch)


async def test_tone_video_is_transcribed_with_offset_timestamps(world: World) -> None:
    item, _ = await world.video(seconds=5)

    assert await world.run() == "summarising"

    found = await world.segments(item)
    assert [row["start_ms"] for row in found] == [500, 2500, 4500]
    # Each chunk's times are offset by its start and clipped to it (the last chunk is 1 s).
    assert [row["end_ms"] for row in found] == [1500, 3500, 5000]
    assert [(row["chunk_index"], row["position"]) for row in found] == [(0, 0), (1, 0), (2, 0)]
    assert [row["text"] for row in found] == [spoken(0), spoken(1), spoken(2)]
    job = await world.job(item)
    assert job is not None
    assert (job["stage"], job["status"], job["chunks_done"], job["chunks_total"]) == (
        "summarising",
        "queued",
        3,
        3,
    )
    assert job["parts"] == []
    windows = await world.windows(item)
    assert len(windows) >= 1
    assert all(window["embed_model"] == HASH_MODEL for window in windows)
    assert " ".join(window["text"] for window in windows) == " ".join(
        spoken(index) for index in range(3)
    )
    row = await world.item(item)
    assert (row["status"], row["speech"], row["transcript_language"]) == (
        "summarising",
        "speech",
        "english",
    )
    assert (row["duration_ms"], row["video_codec"], row["audio_codec"]) == (5000, "h264", "aac")
    assert await world.usage(item) == [5]
    # Chunks are planned from the probed duration, each bounded: never read until EOF.
    assert world.extracts == [(0, 2), (2, 2), (4, 1)]
    assert [index for index, _ in world.speech.calls] == [0, 1, 2]
    assert all(size > 0 for _, size in world.speech.calls)
    # One heartbeat per chunk, before its extraction and provider call.
    assert world.heartbeats == 3


async def test_timeout_on_chunk_three_resumes_at_chunk_three(world: World) -> None:
    item, _ = await world.video(seconds=5)

    async def hook(index: int, call: int) -> TimedTranscript | None:
        if index == 2 and call == 3:
            raise TranscriptionError("transcription_timeout")
        return None

    world.speech.hook = hook
    assert await world.run() == "queued"
    job = await world.job(item)
    assert job is not None
    assert (job["status"], job["chunks_done"], job["error_code"]) == ("queued", 2, TRANSCRIPTION)
    assert len(await world.segments(item)) == 2

    await world.make_due(item)
    assert await world.run() == "summarising"
    assert [index for index, _ in world.speech.calls] == [0, 1, 2, 2]
    found = await world.segments(item)
    assert [(row["chunk_index"], row["start_ms"]) for row in found] == [
        (0, 500),
        (1, 2500),
        (2, 4500),
    ]
    assert await world.usage(item) == [5]


async def test_worker_killed_mid_chunk_repeats_only_that_chunk(world: World) -> None:
    item, _ = await world.video(seconds=5)
    inside = asyncio.Event()

    async def hook(index: int, call: int) -> TimedTranscript | None:
        if index == 2 and call == 3:
            inside.set()
            await asyncio.Event().wait()  # the worker dies here
        return None

    world.speech.hook = hook
    first = await world.claim()
    running = asyncio.create_task(world.worker.process(first))
    await asyncio.wait_for(inside.wait(), 60)
    running.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await running
    job = await world.job(item)
    assert job is not None and (job["status"], job["chunks_done"]) == ("processing", 2)

    await world.sessions.q(
        "UPDATE training_video_jobs SET locked_at = now() - interval '11 minutes'"
        " WHERE item_id = %s",
        item,
    )
    second = await world.claim()
    assert (second.attempt, second.chunks_done) == (first.attempt + 1, 2)
    # The killed attempt can no longer write anything.
    with pytest.raises(JobLost):
        async with JobHandle(world.sessions.pool, first).tx():
            pass
    assert await world.worker.process(second) == "summarising"
    assert [index for index, _ in world.speech.calls] == [0, 1, 2, 2]
    assert [row["start_ms"] for row in await world.segments(item)] == [500, 2500, 4500]
    assert await world.usage(item) == [5]


async def test_no_audio_stream_is_no_audio_without_provider_call_or_summary(
    world: World,
) -> None:
    item, _ = await world.video(seconds=3, audio="none")

    assert await world.run() == "ready"

    row = await world.item(item)
    assert (row["status"], row["speech"], row["summary"]) == ("ready", "no_audio", None)
    assert (row["duration_ms"], row["audio_codec"]) == (3000, None)
    job = await world.job(item)
    assert job is not None and (job["stage"], job["status"]) == ("transcribing", "done")
    assert world.speech.calls == [] and world.extracts == []
    assert await world.usage(item) == []
    assert await world.segments(item) == [] and await world.windows(item) == []


async def test_silent_track_is_no_speech_without_provider_call(world: World) -> None:
    item, _ = await world.video(seconds=3, audio="silence")

    assert await world.run() == "ready"

    row = await world.item(item)
    assert (row["status"], row["speech"], row["summary"]) == ("ready", "no_speech", None)
    assert world.speech.calls == [] and world.extracts == []
    assert await world.usage(item) == []
    assert await world.segments(item) == [] and await world.windows(item) == []


async def test_noise_with_a_stray_phrase_ends_no_speech(world: World) -> None:
    item, _ = await world.video(seconds=3, audio="noise")

    async def hook(index: int, call: int) -> TimedTranscript | None:
        if index == 0:
            # The model's classic hallucination over noise, flagged as probably not speech.
            return TimedTranscript("english", [TimedSegment(0.0, 2.0, "Thank you.", 0.9, -1.2)])
        # Kept, but far too little to count as speech.
        return TimedTranscript("english", [TimedSegment(0.0, 1.0, "Bye.", 0.1, -0.3)])

    world.speech.hook = hook
    assert await world.run() == "ready"

    assert [index for index, _ in world.speech.calls] == [0, 1]
    assert await world.segments(item) == [] and await world.windows(item) == []
    row = await world.item(item)
    assert (row["status"], row["speech"], row["summary"]) == ("ready", "no_speech", None)
    assert row["transcript_language"] is None
    job = await world.job(item)
    assert job is not None and (job["stage"], job["status"]) == ("transcribing", "done")


async def test_too_long_video_is_refused_file_deleted_bytes_released(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(limits, "MAX_VIDEO_SECONDS", 2)
    item, name = await world.video(seconds=3)
    path = world.tmp_path / "training" / world.tenant / name
    assert path.exists()

    assert await world.run() == "failed"

    row = await world.item(item)
    assert (row["status"], row["failure_code"]) == ("failed", "training_video_too_long")
    assert not path.exists()
    async with tx(world.sessions.pool, world.tenant) as conn:
        usage = await db.q.fetch_one(
            "training/video_usage.sql", {"client_id": UUID(world.tenant)}, conn=conn
        )
    assert usage is not None and (usage["items"], usage["bytes"]) == (1, 0)
    # Refused before anything was reserved or sent.
    assert await world.usage(item) == []
    assert world.speech.calls == []


async def test_unplayable_codec_is_refused_and_hevc_gets_the_note(world: World) -> None:
    await enable(world.sessions, world.tenant)
    admin, _ = await world.sessions.member(role="tenant_admin")
    mjpeg, name = await world.video(seconds=2, container="mov", video_codec="mjpeg")
    path = world.tmp_path / "training" / world.tenant / name

    assert await world.run() == "failed"
    row = await world.item(mjpeg)
    assert (row["status"], row["failure_code"]) == ("failed", "training_video_codec")
    assert not path.exists()
    assert world.speech.calls == []

    hevc, _ = await world.video(seconds=2, video_codec="libx265")
    assert await world.run() == "summarising"
    assert (await world.item(hevc))["video_codec"] == "hevc"
    listed = {row["id"]: row for row in (await admin.get(ITEMS)).json()["items"]}
    assert listed[hevc]["notice"] == "hevc"
    assert listed[mjpeg]["notice"] is None
    assert listed[mjpeg]["failure"] == "training_video_codec"


async def test_daily_cap_makes_the_job_wait_without_a_provider_call(world: World) -> None:
    await world.sessions.q(
        "INSERT INTO training_video_usage (client_id, item_id, seconds, processed_at)"
        " VALUES (%s, gen_random_uuid(), 3600, now() - interval '1 hour'),"
        " (%s, gen_random_uuid(), 3600, now() - interval '1 hour'),"
        " (%s, gen_random_uuid(), 3600, now() - interval '1 hour'),"
        " (%s, gen_random_uuid(), 3600, now() - interval '1 hour'),"
        " (%s, gen_random_uuid(), 3598, now() - interval '1 hour')",
        *([world.tenant] * 5),
    )
    item, _ = await world.video(seconds=5)

    assert await world.run() == "waiting"

    assert world.speech.calls == [] and world.extracts == []
    job = await world.job(item)
    assert job is not None
    assert (job["status"], job["attempts"], job["error_code"], job["chunks_done"]) == (
        "queued",
        0,
        "training_processing_limit",
        0,
    )
    wait = await world.one(
        "SELECT extract(epoch FROM next_attempt_at - now())::float AS s"
        " FROM training_video_jobs WHERE item_id = %s",
        item,
    )
    assert 22 * 3600 < wait["s"] <= 23 * 3600 + 60
    assert await world.usage(item) == []


async def test_item_deleted_while_transcribing_leaves_no_rows(world: World) -> None:
    await enable(world.sessions, world.tenant)
    admin, _ = await world.sessions.member(role="tenant_admin")
    item, name = await world.video(seconds=5)

    async def hook(index: int, call: int) -> TimedTranscript | None:
        if index == 1:
            answer = await remove(world.sessions, admin, item)
            assert answer.status_code == 204, answer.text
        return None

    world.speech.hook = hook
    assert await world.run() == "lost"

    assert [index for index, _ in world.speech.calls] == [0, 1]
    assert await world.job(item) is None
    assert await world.segments(item) == [] and await world.windows(item) == []
    assert not (world.tmp_path / "training" / world.tenant / name).exists()
    # Deleting a video does not give its processing seconds back.
    assert await world.usage(item) == [5]


async def test_transcript_text_and_urls_never_logged(world: World) -> None:
    item, name = await world.video(seconds=5)

    with capture_logs() as logs:
        assert await world.run() == "summarising"

    assert any(log["event"] == "training_transcribe_chunk" for log in logs)
    secrets = [spoken(index) for index in range(3)]
    secrets += ["synthetic speech", name, TITLE, str(world.tmp_path), "induction.mp4"]
    secrets += [source for source, _ in world.store.sources]
    for log in logs:
        text = repr(log)
        for secret in secrets:
            assert secret not in text, log["event"]
    assert item


async def test_s3_source_is_presigned_per_chunk(world: World) -> None:
    await world.video(seconds=5)

    assert await world.run() == "summarising"

    # A fresh source for the probe, one for the loudness pass, then one per chunk.
    assert len(world.store.sources) == 2 + 3
    assert {ttl for _, ttl in world.store.sources} == {limits.PLAYBACK_URL_SECONDS}
    chunk_sources = [source for source, _ in world.store.sources[2:]]
    assert world.extract_sources == chunk_sources
    assert len(set(chunk_sources)) == 3


async def test_s3_probe_failure_checks_the_stored_size(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://storage.example.test/training/object?X-Amz-Signature=synthetic-signature"
    world.store.https = url

    async def failing_probe(source: str, container: Any) -> media.Probe:
        assert source == url
        raise MediaUnavailable("failed")

    monkeypatch.setattr(video_transcribe, "probe", failing_probe)
    with capture_logs() as logs:
        # Storage answers with the stored size: the file itself is unreadable, but only the last
        # attempt says so (a brief S3 or DNS blip on the first must not delete the upload).
        unreadable, name = await world.video(seconds=2)
        stored = await world.item(unreadable)
        world.store.size_answer = stored["byte_size"]
        for _ in range(limits.JOB_MAX_ATTEMPTS - 1):
            assert await world.run() == "queued"
            job = await world.job(unreadable)
            assert job is not None and (job["status"], job["error_code"]) == ("queued", STORAGE)
            assert (world.tmp_path / "training" / world.tenant / name).exists()
            await world.make_due(unreadable)
        assert await world.run() == "failed"
        row = await world.item(unreadable)
        assert (row["status"], row["failure_code"]) == ("failed", "training_video_unreadable")
        assert not (world.tmp_path / "training" / world.tenant / name).exists()

        # Another size, or no answer at all: storage is the suspect, so try again later.
        for answer in (stored["byte_size"] + 1, StorageError("object storage head failed: 503")):
            world.store.size_answer = answer
            item, _ = await world.video(seconds=2)
            assert await world.run() == "queued"
            job = await world.job(item)
            assert job is not None and (job["status"], job["error_code"]) == ("queued", STORAGE)
            await world.sessions.q("DELETE FROM training_items WHERE id = %s", item)

    assert world.speech.calls == []
    for log in logs:
        assert "storage.example.test" not in repr(log) and "synthetic-signature" not in repr(log)


async def test_a_refusal_after_the_probe_accepted_the_file_is_retried_not_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    item, name = await world.video(seconds=5)
    path = world.tmp_path / "training" / world.tenant / name
    real_loudness = video_transcribe.max_volume_db
    recording_extract = video_transcribe.extract_chunk

    async def refused_loudness(source: str, container: Any) -> float:
        raise media.MediaRefused(media.UNREADABLE)

    # The probe stores the duration, then the loudness pass fails: not a verdict on the file.
    monkeypatch.setattr(video_transcribe, "max_volume_db", refused_loudness)
    assert await world.run() == "queued"
    job = await world.job(item)
    assert job is not None and (job["status"], job["error_code"]) == ("queued", TRANSCRIPTION)
    row = await world.item(item)
    assert (row["duration_ms"], row["failure_code"]) == (5000, None)
    assert path.exists()

    async def refused_second_chunk(
        source: str, container: Any, start_seconds: int, seconds: int, out: Path
    ) -> int:
        if start_seconds == limits.AUDIO_CHUNK_SECONDS:
            raise media.MediaRefused(media.UNREADABLE)
        return await recording_extract(source, container, start_seconds, seconds, out)

    # A later attempt (the duration is stored): chunk 1's extraction fails the same way.
    monkeypatch.setattr(video_transcribe, "max_volume_db", real_loudness)
    monkeypatch.setattr(video_transcribe, "extract_chunk", refused_second_chunk)
    await world.make_due(item)
    assert await world.run() == "queued"
    job = await world.job(item)
    assert job is not None
    assert (job["status"], job["error_code"], job["chunks_done"]) == ("queued", TRANSCRIPTION, 1)
    assert (await world.item(item))["failure_code"] is None
    assert path.exists()

    # It mends: the next attempt resumes at chunk 1 and finishes.
    monkeypatch.setattr(video_transcribe, "extract_chunk", recording_extract)
    await world.make_due(item)
    assert await world.run() == "summarising"
    assert [index for index, _ in world.speech.calls] == [0, 1, 2]


async def test_provider_times_far_outside_the_chunk_never_raise() -> None:
    rows = video_transcribe.chunk_rows(
        1,
        300_000,
        600_000,
        [
            TimedSegment(1e306, 1e306, "Starts past the end of the chunk."),
            TimedSegment(2.0, 1.7976931348623157e308, "Ends far past the end of the chunk."),
        ],
    )
    assert [(row.start_ms, row.end_ms) for row in rows] == [(600_000, 600_000), (302_000, 600_000)]


async def test_a_chunk_past_the_end_of_the_audio_is_not_sent(world: World) -> None:
    path = make_video(world.tmp_path / "short.mp4", seconds=3)
    within = world.tmp_path / "within.ogg"
    past = world.tmp_path / "past.ogg"
    await media.extract_chunk(str(path), "mp4", 1, 2, within)
    await media.extract_chunk(str(path), "mp4", 4, 2, past)
    assert video_transcribe.holds_audio(within.read_bytes())
    assert not video_transcribe.holds_audio(past.read_bytes())
    # Not an Ogg stream at all: let the provider judge it.
    assert video_transcribe.holds_audio(b"not an ogg stream at all")


async def test_keep_segments_and_windows() -> None:
    kept = keep_segments(
        [
            TimedSegment(0, 1, "  "),
            TimedSegment(1, 2, "kept, unknown probability"),
            TimedSegment(2, 3, "kept at the limit", NO_SPEECH_PROB_MAX),
            TimedSegment(3, 4, "dropped", NO_SPEECH_PROB_MAX + 0.01),
        ]
    )
    assert [segment.text for segment in kept] == ["kept, unknown probability", "kept at the limit"]
    assert MIN_SPEECH_CHARS == 20

    rows = [
        SegmentRow(0, position, position * 20_000, position * 20_000 + 15_000, f"line {position}")
        for position in range(7)
    ]
    windows = build_windows(rows)
    # A window closes once it spans 60 s: 0-75 s (four segments), 80-135 s (three).
    assert windows == [
        WindowRow(0, 0, 75_000, "line 0 line 1 line 2 line 3"),
        WindowRow(1, 80_000, 135_000, "line 4 line 5 line 6"),
    ]
    # A window never holds more than 8000 characters: three 2000-character segments, not four.
    long = [SegmentRow(0, position, position, position + 1, "x" * 2000) for position in range(9)]
    assert [len(window.text) for window in build_windows(long)] == [6002, 6002, 6002]
    assert build_windows([]) == []
