"""Size and count limits for the training library."""

from __future__ import annotations

from typing import Literal

VideoContainer = Literal["mp4", "webm"]
VideoMediaType = Literal["video/mp4", "video/webm"]

MAX_FILE_BYTES = 25 * 1024 * 1024
"""Largest PDF a single upload may carry."""

MAX_PAGES = 300
"""Most pages one PDF may have."""

MAX_ITEMS = 300
"""Most items one company's library may hold."""

MAX_TOTAL_BYTES = 500 * 1024 * 1024
"""Most bytes of PDFs one company's library may hold."""

SUMMARY_MAX_CHARS = 24_000
"""Most characters of PDF text sent to the model for a summary."""

# Parsing bounds for a hostile PDF. Flate packs about 1000:1, so the file cap says nothing
# about decoded size, and pypdf's own defaults (75 MB a stream) let a small file tie up a
# worker for minutes. pypdf parses decoded bytes in Python at roughly 0.2-4 µs a byte, so
# these budgets are what bound the CPU a file can cost. Images are never decoded here.

PDF_STREAM_MAX_DECODED_BYTES = 2 * 1024 * 1024
"""Most bytes any one stream may decode to (each filter's output, per pypdf filter)."""

PDF_STREAM_MAX_DECODING_WORK = 2 * PDF_STREAM_MAX_DECODED_BYTES
"""Most bytes pypdf may feed through one stream's filter chain in total."""

PDF_FLATE_MAX_RECOVERY_INPUT = 256 * 1024
"""Most bytes of a broken flate stream pypdf may retry byte by byte before giving up."""

PDF_OBJECT_STREAMS_MAX_BYTES = 4 * 1024 * 1024
"""Most bytes of object and cross-reference streams one file may make the reader decode."""

PDF_STREAM_OBJECTS_MAX = 100_000
"""Most objects the reader may parse out of object streams in one file."""

PDF_XREF_MAX_ENTRIES = 250_000
"""Most cross-reference stream entries one file may declare (real decks have a few thousand)."""

PDF_FORM_XOBJECT_USES_PER_PAGE = 32
"""Most form XObject invocations extract_text interprets on one page (pypdf's default is
5,000, and it re-interprets the form's whole stream each time); later ones are skipped."""

PDF_TEXT_CONTENT_MAX_BYTES = 2 * 1024 * 1024
"""Most bytes of page content streams extract_text decodes from one file (text-show
operators cost about 4 µs a byte to interpret; 24,000 characters rarely need 1 MB)."""

# The hard bound. The budgets above cannot cover every path through pypdf (it re-parses on
# cache misses, re-scans the file for each undefined reference, re-runs form XObjects and
# ToUnicode CMaps), so every parse runs in a short-lived child process under these limits.

PDF_CHILD_CPU_SECONDS = 10
"""CPU seconds one parse may use; the child is killed by the kernel past it (RLIMIT_CPU)."""

PDF_CHILD_WALL_SECONDS = 20
"""Wall-clock seconds one parse may take; the child's whole process group is killed past it."""

PDF_CHILD_MEMORY_BYTES = 1024 * 1024 * 1024
"""Address space the child may map (RLIMIT_AS); the worker needs about 64 MiB to run."""

PDF_CHILD_MAX_CONCURRENT = 2
"""Most parse children one process runs at once; further callers wait for a slot."""

# Video. Values from docs/superpowers/specs/2026-11-13-training-library-video-design.md.

MAX_VIDEO_BYTES = 500 * 1024 * 1024
"""Largest video a single upload may carry."""

VIDEO_PIECE_BYTES = 8 * 1024 * 1024
"""Size of every upload piece but the last (an S3 part needs at least 5 MiB)."""

MAX_VIDEO_SECONDS = 3600
"""Longest video the library accepts."""

MAX_VIDEO_ITEMS = 50
"""Most videos one company's library may hold."""

MAX_VIDEO_TOTAL_BYTES = 5 * 1024 * 1024 * 1024
"""Most bytes of videos one company's library may hold, counting uploads in progress."""

MAX_UPLOADS_IN_PROGRESS = 2
"""Most video uploads one company may have open at once."""

UPLOAD_ABANDON_SECONDS = 86_400
"""An upload with no new piece for this long is removed by the sweep."""

DAILY_PROCESSING_SECONDS = 18_000
"""Most seconds of video one company may have transcribed per rolling 24 hours."""

AUDIO_CHUNK_SECONDS = 300
"""Length of each audio chunk sent to the speech endpoint."""

ASK_WHOLE_MAX_CHARS = 40_000
"""A transcript up to this long is sent whole to answer a question about it."""

SUMMARY_PART_CHARS = 20_000
"""Most transcript characters in one map step of the summary."""

PIECES_IN_FLIGHT = 4
"""Most upload pieces one gateway process accepts at once."""

PLAYBACK_URL_SECONDS = 900
"""Lifetime of a presigned playback or worker source URL."""

JOB_STALE_SECONDS = 600
"""A claimed job whose heartbeat is older than this is requeued."""

JOB_MAX_ATTEMPTS = 3
"""Attempts a job gets for transient failures before it fails."""

JOB_BACKOFF_SECONDS = 60
"""Wait before a retried job runs again, times the attempt number (60 s, 120 s)."""

SWEEP_EVERY_SECONDS = 3600
"""How often the training worker sweeps abandoned uploads and old usage rows."""

USAGE_KEEP_SECONDS = 172_800
"""Usage rows older than this are deleted by the sweep."""

WINDOW_SECONDS = 60
"""Length of each embedded transcript window."""

ASK_NEAREST_WINDOWS = 8
"""Windows sent to answer a question about a transcript too long to send whole."""

VIDEO_EXTENSIONS: dict[str, VideoContainer] = {
    ".mp4": "mp4",
    ".m4v": "mp4",
    ".mov": "mp4",
    ".webm": "webm",
}
"""Accepted upload extensions and the container each is stored and demuxed as."""

PLAYBACK_MEDIA_TYPE: dict[VideoContainer, VideoMediaType] = {
    "mp4": "video/mp4",
    "webm": "video/webm",
}
"""Media type a stored container plays as (a ``.mov`` plays as ``video/mp4``)."""

# ffmpeg and ffprobe run in the training worker on untrusted video, each as a child under these
# limits (csa/training/media.py). Values from docs/TRAINING_VIDEO_CAPACITY.md: 4 x the worst
# measured CPU, 2 x the worst measured address space (rounded up to 64 MiB), 4 x the worst wall.

FFMPEG_CHILD_CPU_SECONDS = 309
"""CPU seconds one ffmpeg or ffprobe child may use (RLIMIT_CPU; the kernel kills it past it)."""

FFMPEG_CHILD_MEMORY_BYTES = 469762048
"""Address space one ffmpeg or ffprobe child may map (RLIMIT_AS, 448 MiB)."""

FFMPEG_CHILD_WALL_SECONDS = 300
"""Wall-clock seconds one child may take; its whole process group is killed past it."""

PROBE_MAX_OUTPUT_BYTES = 262144
"""Most bytes of stdout read from one child; more refuses the file."""

SILENCE_MAX_DB = -50.0
"""A first audio stream whose peak is at most this (dBFS) is silent: no speech to transcribe."""

PLAYABLE_VIDEO: frozenset[str] = frozenset({"h264", "vp8", "vp9", "av1", "hevc"})
"""Video codecs (ffprobe names) the library accepts; HEVC plays only in some browsers."""

PLAYABLE_AUDIO: frozenset[str] = frozenset({"aac", "mp3", "opus", "vorbis"})
"""Audio codecs (ffprobe names) the library accepts."""
