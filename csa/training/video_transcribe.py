"""The transcribing stage of a training video: probe, silence, the daily cap, chunks and windows.

``TranscribeStage.transcribe`` runs one claimed attempt, in this fixed order:

1. **Probe** (only while the item has no ``duration_ms``): duration and codecs. A refused file
   (unreadable, a codec outside the library's sets, longer than ``MAX_VIDEO_SECONDS``) is
   ``Permanent``; nothing is stored or reserved for it. Otherwise the duration, both codecs (the
   list's HEVC notice reads ``video_codec``) and the number of chunks are stored in one fenced
   transaction, which also decides ``speech='no_audio'`` for a video without an audio stream.
2. **No audio stream**: ready, without a provider call or a summary.
3. **Silence** (before the first chunk only): a first audio stream whose peak is at most
   ``SILENCE_MAX_DB`` is ``speech='no_speech'``, ready, without a provider call.
4. **The daily cap**: the video's seconds (rounded up) are reserved in their own fenced
   transaction before the first billed chunk, or the job waits (``Waiting``) with no call made.
5. **Chunks** of ``AUDIO_CHUNK_SECONDS``, planned from the probed duration and resumed at
   ``chunks_done``: each starts with a heartbeat, asks the store for a fresh source (on S3 a
   presigned URL for ``PLAYBACK_URL_SECONDS``), extracts exactly that chunk's seconds into a
   temporary directory under ``/tmp`` (never "until the end of the file"), sends it, keeps the
   segments worth keeping, and commits them with ``chunks_done = n + 1 WHERE chunks_done = n`` in
   one fenced transaction. A worker killed mid-chunk therefore repeats that chunk only: earlier
   chunks are neither sent nor written again, and the reservation is not booked twice.
6. **Windows**: fewer than ``MIN_SPEECH_CHARS`` of kept text is ``no_speech`` (segments and
   windows removed, ready). Otherwise the segments are grouped into windows of about
   ``WINDOW_SECONDS``, embedded as passages, and stored with ``speech='speech'`` and the most
   frequent language the provider reported, in one fenced transaction; the job moves on to
   the summary.

Every write goes through ``job.tx()`` (the claim's attempt and stage fence), so an attempt that
lost its claim writes nothing. Errors: ``TranscriptionError``, ``KbEmbeddingError`` and
``MediaUnavailable`` are ``Transient("training_transcription_unavailable")``; ``StorageError``
is ``Transient("training_storage_unavailable")``; ``MediaRefused`` from the probe is
``Permanent(code)``. Once the probe has accepted the file (its duration is stored), an unreadable
answer from the loudness or extract pass is ``Transient("training_transcription_unavailable")``:
the probe read the file, so the cause is the attempt (an OOM kill, a full ``/tmp``), and a
refusal would delete the upload.

**An S3 source the probe cannot read.** ffmpeg's stderr is never read, so over https a bad file
and a network failure both surface as ``MediaUnavailable("failed")``. In the probe step only, the
stage then asks the store for the object's size with its own client: the stored size on the
job's last attempt (``JOB_MAX_ATTEMPTS``) means the storage is fine and the file is unreadable
(``Permanent``); on an earlier attempt, or with any other answer, an error included, it is
``Transient("training_storage_unavailable")``.

Known limits. A WebM without Cues (browser MediaRecorder output) cannot seek, so each chunk's
``-ss`` demuxes from the start of the file: later chunks read more bytes over S3, bounded by the
child's wall clock. The pinned ffmpeg build verifies a presigned URL's certificate chain but not
its hostname (see ``csa/training/media.py``); each URL lives only ``PLAYBACK_URL_SECONDS`` and is
made fresh for each probe, loudness pass and chunk.

Logs carry ids, the company id, the stage, the attempt, chunk indexes, byte counts, durations,
the embedding model name and outcome codes: never transcript text, a title, a file name or a URL.
Transcripts live only in ``training_transcript_segments`` and ``training_transcript_windows``.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog

from csa.agents.kb.embedder import KbEmbedder, KbEmbeddingError, vector_to_sql
from csa.core import db
from csa.speech import SpeechTranscriber, TimedSegment, TimedTranscript, TranscriptionError
from csa.storage.base import StorageError
from csa.storage.multipart import MultipartStore
from csa.training import limits
from csa.training.limits import WINDOW_SECONDS
from csa.training.media import (
    UNREADABLE,
    MediaRefused,
    MediaUnavailable,
    Probe,
    check_playable,
    extract_chunk,
    max_volume_db,
    probe,
)
from csa.training.video_jobs import (
    STORAGE_FAILED,
    TRANSCRIPTION_FAILED,
    Claim,
    JobHandle,
    JobLost,
    Permanent,
    Transient,
    Waiting,
    reserve_processing,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "MIN_SPEECH_CHARS",
    "NO_SPEECH_PROB_MAX",
    "TOO_LONG",
    "SegmentRow",
    "TranscribeStage",
    "WindowRow",
    "build_windows",
    "chunk_rows",
    "holds_audio",
    "keep_segments",
]

NO_SPEECH_PROB_MAX = 0.6
"""A segment the provider thinks is more likely than this not speech is dropped."""

MIN_SPEECH_CHARS = 20
"""A video whose kept segments hold fewer characters than this has no speech."""

TOO_LONG = "training_video_too_long"

SEGMENT_MAX_CHARS = 2000
"""Most characters in one stored segment (the table's check); longer text is split."""

WINDOW_MAX_CHARS = 8000
"""Most characters in one search window (the table's check)."""

_CHUNK_MIME = "audio/ogg"


@dataclass(frozen=True)
class SegmentRow:
    """One stored transcript segment; times in milliseconds from the start of the video."""

    chunk_index: int
    position: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class WindowRow:
    """Consecutive segments grouped for search; times in milliseconds."""

    position: int
    start_ms: int
    end_ms: int
    text: str


def keep_segments(segs: list[TimedSegment]) -> list[TimedSegment]:
    """Drop blank segments and those more likely than ``NO_SPEECH_PROB_MAX`` not speech."""
    return [
        segment
        for segment in segs
        if segment.text.strip()
        and not (segment.no_speech_prob is not None and segment.no_speech_prob > NO_SPEECH_PROB_MAX)
    ]


def chunk_rows(
    chunk_index: int, start_ms: int, end_ms: int, segs: Sequence[TimedSegment]
) -> list[SegmentRow]:
    """The chunk's segments as rows: offset by the chunk's start and clipped to the chunk.

    A segment longer than ``SEGMENT_MAX_CHARS`` is split at spaces into several rows that share
    its time span in proportion to their length.
    """
    rows: list[SegmentRow] = []
    for segment in segs:
        begins = _at(start_ms, segment.start, start_ms, end_ms)
        ends = _at(start_ms, segment.end, begins, end_ms)
        pieces = _pieces(segment.text.strip(), SEGMENT_MAX_CHARS)
        total = sum(len(piece) for piece in pieces)
        done = 0
        for piece in pieces:
            piece_start = begins + (ends - begins) * done // total
            done += len(piece)
            piece_end = begins + (ends - begins) * done // total
            rows.append(SegmentRow(chunk_index, len(rows), piece_start, piece_end, piece))
    return rows


def build_windows(rows: Sequence[SegmentRow], seconds: int = WINDOW_SECONDS) -> list[WindowRow]:
    """Group consecutive segments into windows: a window closes once it spans ``seconds``,
    or before a segment that would take its text past ``WINDOW_MAX_CHARS``.
    """
    windows: list[WindowRow] = []
    current: list[SegmentRow] = []

    def close() -> None:
        windows.append(
            WindowRow(
                position=len(windows),
                start_ms=current[0].start_ms,
                end_ms=max(row.end_ms for row in current),
                text=" ".join(row.text for row in current),
            )
        )
        current.clear()

    for row in rows:
        if current and sum(len(r.text) + 1 for r in current) + len(row.text) > WINDOW_MAX_CHARS:
            close()
        current.append(row)
        if current[-1].end_ms - current[0].start_ms >= seconds * 1000:
            close()
    if current:
        close()
    return windows


def holds_audio(ogg: bytes) -> bool:
    """Whether an Ogg stream carries any audio packet beyond its two header pages.

    A chunk past the end of the audio stream (a video whose sound stops early) is headers only;
    it is not sent. Anything that does not parse as Ogg is left for the provider to judge.
    """
    offset = 0
    while offset + 27 <= len(ogg):
        if ogg[offset : offset + 4] != b"OggS":
            return True
        granule = int.from_bytes(ogg[offset + 6 : offset + 14], "little", signed=True)
        count = ogg[offset + 26]
        table = ogg[offset + 27 : offset + 27 + count]
        payload = sum(table)
        if granule > 0 and payload > 0:
            return True
        offset += 27 + count + payload
    return offset != len(ogg)


class TranscribeStage:
    """``StageRunner.transcribe`` for training videos (see the module docstring)."""

    def __init__(
        self, store: MultipartStore, transcriber: SpeechTranscriber, embedder: KbEmbedder
    ) -> None:
        self._store = store
        self._transcriber = transcriber
        self._embedder = embedder

    async def transcribe(self, claim: Claim, job: JobHandle) -> Literal["summarising", "ready"]:
        """Run the stage for one claimed attempt; ``"summarising"`` only for a video with speech."""
        try:
            return await self._run(claim, job)
        except (TranscriptionError, KbEmbeddingError, MediaUnavailable) as failure:
            self._failed(claim, failure)
            raise Transient(TRANSCRIPTION_FAILED) from None
        except StorageError as failure:
            self._failed(claim, failure)
            raise Transient(STORAGE_FAILED) from None
        except MediaRefused as refused:
            raise Permanent(refused.code) from None

    async def _run(self, claim: Claim, job: JobHandle) -> Literal["summarising", "ready"]:
        # An earlier attempt decided already, and the job ended before it could move on.
        if claim.speech in ("no_audio", "no_speech"):
            return "ready"
        if claim.speech == "speech":
            return "summarising"
        duration_ms = claim.duration_ms
        if duration_ms is None:
            found = await self._probe(claim, job)
            if not found.has_audio:
                self._decided(claim, "no_audio")
                return "ready"
            duration_ms = found.duration_ms
        if duration_ms > limits.MAX_VIDEO_SECONDS * 1000:
            raise Permanent(TOO_LONG)
        total = _chunks(duration_ms)
        if claim.chunks_done == 0:
            async with _probed_already():
                peak = await max_volume_db(await self._source(claim), claim.container)
            if peak <= limits.SILENCE_MAX_DB:
                await self._no_speech(claim, job)
                return "ready"
        async with job.tx() as conn:
            until = await reserve_processing(
                conn, claim.client_id, claim.item_id, max(1, math.ceil(duration_ms / 1000))
            )
        if until is not None:
            raise Waiting(until)
        languages = _languages(claim.parts)
        for index in range(claim.chunks_done, total):
            language = await self._chunk(claim, job, index, total, duration_ms)
            if language is not None:
                languages.append(language)
        return await self._windows(claim, job, languages)

    async def _source(self, claim: Claim) -> str:
        """A fresh path or presigned URL for one media pass; never logged."""
        return await asyncio.to_thread(
            self._store.media_source,
            claim.client_id,
            claim.storage_name,
            limits.PLAYBACK_URL_SECONDS,
        )

    async def _probe(self, claim: Claim, job: JobHandle) -> Probe:
        source = await self._source(claim)
        try:
            found = await probe(source, claim.container)
        except MediaUnavailable as failure:
            if failure.code == "failed" and source.startswith("https://"):
                raise await self._unreadable_or_unavailable(claim, job) from None
            raise
        notice = check_playable(found)
        if found.duration_ms > limits.MAX_VIDEO_SECONDS * 1000:
            raise Permanent(TOO_LONG)
        async with job.tx() as conn:
            stored = await db.q.fetch_one(
                "training/video_item_probe.sql",
                {
                    "client_id": claim.client_id,
                    "item_id": claim.item_id,
                    "attempt": claim.attempt,
                    "duration_ms": found.duration_ms,
                    "video_codec": found.video_codec,
                    "audio_codec": found.audio_codec,
                    "chunks_total": _chunks(found.duration_ms),
                },
                conn=conn,
            )
            if stored is None:
                raise JobLost
        logger.info(
            "training_transcribe_probe",
            client_id=str(claim.client_id),
            item_id=str(claim.item_id),
            stage=claim.stage,
            attempt=claim.attempt,
            duration_ms=found.duration_ms,
            has_audio=found.has_audio,
            notice=notice,
            outcome="probed",
        )
        return found

    async def _unreadable_or_unavailable(
        self, claim: Claim, job: JobHandle
    ) -> Permanent | Transient:
        """After ffprobe failed on a presigned URL: the file, or the storage."""
        async with job.tx() as conn:
            item = await db.q.fetch_one(
                "training/get.sql", (claim.client_id, claim.item_id), conn=conn
            )
        if item is None:
            raise JobLost
        try:
            size = await asyncio.to_thread(
                self._store.object_size, claim.client_id, claim.storage_name
            )
        except StorageError:
            size = None
        # Deleting the file is irreversible, so one failed read is not enough: a brief S3 or DNS
        # blip (or an https problem on the first switch-on) would cost every upload. Only the last
        # attempt calls the file unreadable.
        unreadable = size == item["byte_size"] and claim.attempt >= limits.JOB_MAX_ATTEMPTS
        verdict: Permanent | Transient = (
            Permanent(UNREADABLE) if unreadable else Transient(STORAGE_FAILED)
        )
        logger.warning(
            "training_transcribe_probe",
            client_id=str(claim.client_id),
            item_id=str(claim.item_id),
            stage=claim.stage,
            attempt=claim.attempt,
            outcome=verdict.code,
        )
        return verdict

    async def _chunk(
        self, claim: Claim, job: JobHandle, index: int, total: int, duration_ms: int
    ) -> str | None:
        """Send chunk ``index`` and commit its segments; the language the provider reported."""
        began = time.monotonic()
        chunk_seconds = limits.AUDIO_CHUNK_SECONDS
        start_seconds = index * chunk_seconds
        start_ms = start_seconds * 1000
        end_ms = min(duration_ms, start_ms + chunk_seconds * 1000)
        seconds = max(1, math.ceil((end_ms - start_ms) / 1000))
        # Extraction (up to the child's wall clock) and the call (up to 90 s) fit well inside
        # JOB_STALE_SECONDS after this.
        await job.heartbeat()
        source = await self._source(claim)
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="csa-training-") as directory:
            out = Path(directory) / "chunk.ogg"
            async with _probed_already():
                audio_bytes = await extract_chunk(
                    source, claim.container, start_seconds, seconds, out
                )
            audio = await asyncio.to_thread(out.read_bytes)
        sent = holds_audio(audio)
        answer = (
            await self._transcriber.transcribe_segments(audio, _CHUNK_MIME)
            if sent
            else TimedTranscript(None, [])
        )
        rows = chunk_rows(index, start_ms, end_ms, keep_segments(answer.segments))
        async with job.tx() as conn:
            done = await db.q.fetch_one(
                "training/video_chunk_done.sql",
                {
                    "client_id": claim.client_id,
                    "item_id": claim.item_id,
                    "attempt": claim.attempt,
                    "done": index,
                    "chunks_total": total,
                    "language": answer.language,
                },
                conn=conn,
            )
            if done is None:
                raise JobLost
            if rows:
                await db.q.execute(
                    "training/video_segments_insert.sql",
                    {
                        "client_id": claim.client_id,
                        "item_id": claim.item_id,
                        "chunk_index": index,
                        "positions": [row.position for row in rows],
                        "starts": [row.start_ms for row in rows],
                        "ends": [row.end_ms for row in rows],
                        "texts": [row.text for row in rows],
                    },
                    conn=conn,
                )
        logger.info(
            "training_transcribe_chunk",
            client_id=str(claim.client_id),
            item_id=str(claim.item_id),
            stage=claim.stage,
            attempt=claim.attempt,
            chunk=index,
            chunks_total=total,
            audio_bytes=audio_bytes,
            segments=len(rows),
            elapsed_ms=round((time.monotonic() - began) * 1000),
            outcome="sent" if sent else "no_audio_in_chunk",
        )
        return answer.language

    async def _windows(
        self, claim: Claim, job: JobHandle, languages: list[str]
    ) -> Literal["summarising", "ready"]:
        async with job.tx() as conn:
            found = await db.q.fetch_all(
                "training/video_segments_list.sql",
                {"client_id": claim.client_id, "item_id": claim.item_id},
                conn=conn,
            )
        rows = [SegmentRow(**row) for row in found]
        if sum(len(row.text) for row in rows) < MIN_SPEECH_CHARS:
            await self._no_speech(claim, job)
            return "ready"
        windows = build_windows(rows)
        vectors = await self._embedder.embed_batch([window.text for window in windows])
        if len(vectors) != len(windows):
            raise KbEmbeddingError("embedder returned an unexpected number of vectors")
        model = self._embedder.model_name
        language = _most_frequent(languages)
        async with job.tx() as conn:
            await self._clear(conn, claim, segments=False)
            await db.q.execute(
                "training/video_windows_replace.sql",
                {
                    "client_id": claim.client_id,
                    "item_id": claim.item_id,
                    "positions": [window.position for window in windows],
                    "starts": [window.start_ms for window in windows],
                    "ends": [window.end_ms for window in windows],
                    "texts": [window.text for window in windows],
                    "embeddings": [vector_to_sql(vector) for vector in vectors],
                    "embed_model": model,
                },
                conn=conn,
            )
            await self._speech(conn, claim, "speech", language)
        logger.info(
            "training_transcribe_windows",
            client_id=str(claim.client_id),
            item_id=str(claim.item_id),
            stage=claim.stage,
            attempt=claim.attempt,
            segments=len(rows),
            windows=len(windows),
            model=model,
            outcome="speech",
        )
        return "summarising"

    async def _no_speech(self, claim: Claim, job: JobHandle) -> None:
        async with job.tx() as conn:
            await self._clear(conn, claim, segments=True)
            await self._speech(conn, claim, "no_speech", None)
        self._decided(claim, "no_speech")

    @staticmethod
    async def _clear(conn: Any, claim: Claim, *, segments: bool) -> None:
        await db.q.execute(
            "training/video_transcript_clear.sql",
            {"client_id": claim.client_id, "item_id": claim.item_id, "segments": segments},
            conn=conn,
        )

    @staticmethod
    async def _speech(conn: Any, claim: Claim, speech: str, language: str | None) -> None:
        row = await db.q.fetch_one(
            "training/video_item_speech.sql",
            {
                "client_id": claim.client_id,
                "item_id": claim.item_id,
                "attempt": claim.attempt,
                "speech": speech,
                "language": language,
            },
            conn=conn,
        )
        if row is None:
            raise JobLost

    @staticmethod
    def _decided(claim: Claim, speech: str) -> None:
        logger.info(
            "training_transcribe_decided",
            client_id=str(claim.client_id),
            item_id=str(claim.item_id),
            stage=claim.stage,
            attempt=claim.attempt,
            outcome=speech,
        )

    @staticmethod
    def _failed(claim: Claim, failure: Exception) -> None:
        code = getattr(failure, "code", None)
        logger.warning(
            "training_transcribe_failed",
            client_id=str(claim.client_id),
            item_id=str(claim.item_id),
            stage=claim.stage,
            attempt=claim.attempt,
            outcome=code if isinstance(code, str) else type(failure).__name__,
        )


def _chunks(duration_ms: int) -> int:
    chunk_ms = limits.AUDIO_CHUNK_SECONDS * 1000
    return max(1, -(-duration_ms // chunk_ms))


def _at(base_ms: int, seconds: float, low_ms: int, high_ms: int) -> int:
    """``base_ms`` plus ``seconds``, clipped to ``low_ms..high_ms`` (infinity is the end).

    A provider time past the end is the end before it is multiplied, so a huge finite value
    (``1e306 * 1000`` is infinite) never reaches ``round``.
    """
    if not math.isfinite(seconds) or seconds >= (high_ms - base_ms) / 1000:
        return high_ms
    return min(high_ms, max(low_ms, base_ms + round(seconds * 1000)))


@asynccontextmanager
async def _probed_already() -> AsyncIterator[None]:
    """Around the loudness and extract passes: the probe already accepted this file (its duration
    is stored), so an unreadable answer now is a failed attempt (an OOM kill, a full ``/tmp``, a
    read cut short), retried, never a refusal that deletes the upload.
    """
    try:
        yield
    except MediaRefused as refused:
        if refused.code != UNREADABLE:
            raise
        raise MediaUnavailable("failed") from None


def _pieces(text: str, limit: int) -> list[str]:
    """``text`` cut at spaces into pieces of at most ``limit`` characters."""
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]


def _languages(parts: Sequence[Mapping[str, Any]]) -> list[str]:
    """The languages earlier attempts recorded per chunk, in chunk order."""
    found = [
        (part["chunk"], part["language"])
        for part in parts
        if isinstance(part.get("chunk"), int) and isinstance(part.get("language"), str)
    ]
    return [language for _, language in sorted(found, key=lambda entry: entry[0])]


def _most_frequent(languages: list[str]) -> str | None:
    """The language reported for the most chunks; a tie goes to the one heard first."""
    if not languages:
        return None
    counts = Counter(languages)
    return max(counts, key=lambda language: (counts[language], -languages.index(language)))
