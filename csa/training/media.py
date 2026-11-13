"""ffprobe and ffmpeg on untrusted video, each in a hardened child (training worker only).

The video file is hostile input to a large C program, so every call runs the tool as a child:
``[sys.executable, "-I", "-B", "-c", _LIMITED_EXEC, ...]`` sets the rlimits (CPU, address space,
file size, open files, no core) and ``nice 10``, then ``execve``s the absolute tool path with an
empty environment. The child is spawned as ``pdf_guard._spawn`` does: ``cwd="/"``, its own session
(so a kill takes any grandchild too), stdin and stderr to /dev/null. Its stdout is capped at
``PROBE_MAX_OUTPUT_BYTES`` and its whole process group is killed past the wall clock.

The input is read only through ``input_args``: the demuxer is forced from the stored container
(``-f mov`` for mp4, ``-f matroska`` for webm), so an HLS playlist or an ffconcat list saved as a
video is parsed as a damaged video, never followed; ``-protocol_whitelist`` allows only ``file``
for a local path and ``https,tls,tcp`` for a presigned S3 URL; mov's data references stay off.
Every audio command takes ``-vn`` and ``-threads 1``.

Outcomes: a file the tool cannot read (it fails, is killed at a limit, floods stdout or answers
with something unusable) is ``MediaRefused("training_video_unreadable")``; a codec outside the
library's sets is ``MediaRefused("training_video_codec")``. ``MediaUnavailable`` is
infrastructure: ``ffmpeg_missing``, ``timeout`` (the wall clock), or ``failed`` (the child could
not start, or a remote source failed, which may be the network). Logs carry the tool, the action,
the outcome, the code, byte counts and elapsed time only: never stderr (it is never read), a
path, a URL or a file name.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from csa.speech import MAX_AUDIO_BYTES
from csa.training import limits
from csa.training.limits import VideoContainer

logger = structlog.get_logger(__name__)

RefusalCode = Literal["training_video_unreadable", "training_video_codec"]
UnavailableCode = Literal["ffmpeg_missing", "timeout", "failed"]
Tool = Literal["ffmpeg", "ffprobe"]

UNREADABLE: RefusalCode = "training_video_unreadable"
CODEC: RefusalCode = "training_video_codec"

AUDIO_SAMPLE_RATE = 16000
AUDIO_BITRATE = "32k"

# The bootstrap's own exit codes, chosen away from ffmpeg's (0, 1, 69, 255 and negative AVERRORs).
EXIT_NOT_READY = 125  # the limits could not be set
EXIT_NO_TOOL = 126  # the tool could not be executed

_CHILD_OPEN_FILES = 64
_CHILD_FILE_BYTES = MAX_AUDIO_BYTES  # the largest file a tool writes is one audio chunk
_MAX_DURATION_MS = 2**31 - 1

_LIMITED_EXEC = (
    "import os, resource, sys\n"
    "try:\n"
    "    cpu, memory, file_bytes, files = (int(value) for value in sys.argv[1:5])\n"
    "    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))\n"
    "    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))\n"
    "    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))\n"
    "    resource.setrlimit(resource.RLIMIT_NOFILE, (files, files))\n"
    "    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
    "    os.nice(10)\n"
    "except BaseException:\n"
    f"    os._exit({EXIT_NOT_READY})\n"
    "try:\n"
    "    os.execve(sys.argv[5], sys.argv[5:], {})\n"
    "except BaseException:\n"
    f"    os._exit({EXIT_NO_TOOL})\n"
)

_DEMUXERS: dict[str, str] = {"mp4": "mov", "webm": "matroska"}
_COMMON = ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-filter_threads", "1"]
_PEAK_KEY = "lavfi.astats.Overall.Peak_level"
# Mono 16 kHz float (what the speech endpoint hears), measured in frames of 2^20 samples (about
# 65 s each) so a whole hour prints under 60 lines; astats never resets, so the last line holds
# the peak over the whole stream.
_LOUDNESS_FILTER = (
    f"aresample={AUDIO_SAMPLE_RATE},aformat=sample_fmts=flt:channel_layouts=mono,"
    "asetnsamples=n=1048576,"
    "astats=metadata=1:reset=0:measure_perchannel=none:measure_overall=Peak_level,"
    f"ametadata=mode=print:key={_PEAK_KEY}:file=-"
)


class MediaRefused(Exception):
    """The video cannot be accepted: unreadable, or a codec outside the library's sets."""

    def __init__(self, code: RefusalCode) -> None:
        super().__init__(code)
        self.code: RefusalCode = code


class MediaUnavailable(Exception):
    """The tool could not do the work: infrastructure, not (necessarily) the file."""

    def __init__(self, code: UnavailableCode) -> None:
        super().__init__(code)
        self.code: UnavailableCode = code


@dataclass(frozen=True)
class Probe:
    duration_ms: int
    video_codec: str | None
    audio_codec: str | None
    has_audio: bool


def input_args(source: str, container: VideoContainer) -> list[str]:
    """ffmpeg/ffprobe input options for a stored video: an absolute path or an https URL."""
    demuxer = _DEMUXERS.get(container)
    if demuxer is None:
        raise ValueError("unknown video container")
    # mov can point at other files (data references); keep that off, as it is by default.
    format_options = ["-enable_drefs", "0"] if demuxer == "mov" else []
    if source.startswith("https://"):
        whitelist, url = "https,tls,tcp", source
    elif source.startswith("/"):
        whitelist, url = "file", f"file:{source}"
    else:
        raise ValueError("a media source is an absolute path or an https URL")
    return ["-protocol_whitelist", whitelist, "-f", demuxer, *format_options, "-i", url]


async def probe(source: str, container: VideoContainer) -> Probe:
    """Duration and the first video and audio streams' codecs (ffprobe; no frame is decoded)."""
    args = [
        "-v",
        "error",
        "-hide_banner",
        "-threads",
        "1",
        *input_args(source, container),
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name:stream_disposition=attached_pic",
    ]
    return await _run("ffprobe", "probe", args, source, _parse_probe)


def check_playable(p: Probe) -> Literal["hevc"] | None:
    """Refuse codecs outside the library's sets; ``"hevc"`` tells the manager it may not play."""
    if p.video_codec not in limits.PLAYABLE_VIDEO:
        raise MediaRefused(CODEC)
    if p.has_audio and p.audio_codec not in limits.PLAYABLE_AUDIO:
        raise MediaRefused(CODEC)
    return "hevc" if p.video_codec == "hevc" else None


async def max_volume_db(source: str, container: VideoContainer) -> float:
    """Peak level (dBFS, ``-inf`` for digital silence) over the whole first audio stream.

    Read from stdout only (``ametadata`` prints ``astats``' running peak), never from stderr.
    """
    args = [
        *_COMMON,
        "-threads",
        "1",
        *input_args(source, container),
        "-t",
        str(limits.MAX_VIDEO_SECONDS),
        "-vn",
        "-sn",
        "-dn",
        "-map",
        "0:a:0",
        "-af",
        _LOUDNESS_FILTER,
        "-threads",
        "1",
        "-f",
        "null",
        "-",
    ]
    return await _run("ffmpeg", "loudness", args, source, _parse_peak)


async def extract_chunk(
    source: str, container: VideoContainer, start_seconds: int, seconds: int, out: Path
) -> int:
    """Write ``seconds`` of the first audio stream from ``start_seconds`` to ``out``.

    16 kHz mono Opus at 32 kbit/s in Ogg, without the source's metadata; returns the bytes written.
    """
    if start_seconds < 0 or seconds < 1:
        raise ValueError("a chunk starts at or after 0 and lasts at least a second")
    if not out.is_absolute():
        raise ValueError("the chunk path must be absolute")
    args = [
        *_COMMON,
        "-threads",
        "1",
        "-ss",
        str(start_seconds),
        *input_args(source, container),
        "-t",
        str(seconds),
        "-vn",
        "-sn",
        "-dn",
        "-map",
        "0:a:0",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-ac",
        "1",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-c:a",
        "libopus",
        "-b:a",
        AUDIO_BITRATE,
        "-threads",
        "1",
        "-f",
        "ogg",
        "-y",
        f"file:{out}",
    ]

    def written(_output: bytes) -> int:
        size = out.stat().st_size
        if size > MAX_AUDIO_BYTES:
            raise ValueError("chunk larger than a speech request may carry")
        return size

    return await _run("ffmpeg", "extract", args, source, written)


# --- output parsing -----------------------------------------------------------------------


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Disposition(_Loose):
    attached_pic: int = 0


class _Stream(_Loose):
    codec_type: str | None = None
    codec_name: str | None = Field(default=None, max_length=32)
    disposition: _Disposition = Field(default_factory=_Disposition)


class _Format(_Loose):
    duration: str | None = Field(default=None, max_length=32)


class _ProbeOutput(_Loose):
    streams: list[_Stream] = Field(default_factory=list[_Stream])
    format: _Format


def _parse_probe(output: bytes) -> Probe:
    parsed = _ProbeOutput.model_validate_json(output)
    if parsed.format.duration is None:
        raise ValueError("no duration")
    seconds = float(parsed.format.duration)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("no usable duration")
    duration_ms = round(seconds * 1000)
    if duration_ms > _MAX_DURATION_MS:
        raise ValueError("duration out of range")
    videos = [
        stream
        for stream in parsed.streams
        if stream.codec_type == "video" and not stream.disposition.attached_pic
    ]
    audios = [stream for stream in parsed.streams if stream.codec_type == "audio"]
    if not videos and not audios:
        raise ValueError("no video or audio stream")
    return Probe(
        duration_ms=duration_ms,
        video_codec=videos[0].codec_name if videos else None,
        audio_codec=audios[0].codec_name if audios else None,
        has_audio=bool(audios),
    )


def _parse_peak(output: bytes) -> float:
    peak = -math.inf  # no audio frame at all: nothing to hear
    for line in output.decode("ascii").splitlines():
        key, _, value = line.partition("=")
        if key == _PEAK_KEY:
            peak = float(value)
    if math.isnan(peak) or peak == math.inf:
        raise ValueError("no usable level")
    return peak


# --- the child ------------------------------------------------------------------------------


class _Flood(Exception):
    pass


def _binary(name: Tool) -> str:
    """The absolute path of the tool on this process's PATH."""
    found = shutil.which(name)
    if found is None:
        raise MediaUnavailable("ffmpeg_missing")
    return os.path.abspath(found)


def _command(tool: str, args: list[str]) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        _LIMITED_EXEC,
        str(limits.FFMPEG_CHILD_CPU_SECONDS),
        str(limits.FFMPEG_CHILD_MEMORY_BYTES),
        str(_CHILD_FILE_BYTES),
        str(_CHILD_OPEN_FILES),
        tool,
        *args,
    ]


async def _run[Result](
    tool: Tool,
    action: str,
    args: list[str],
    source: str,
    parse: Callable[[bytes], Result],
) -> Result:
    started = time.monotonic()
    outcome, code, output_bytes = "error", None, 0
    try:
        try:
            argv = _command(_binary(tool), args)
        except MediaUnavailable as missing:
            outcome, code = "missing", missing.code
            raise
        try:
            process = await _spawn(argv)
        except (OSError, subprocess.SubprocessError):
            outcome, code = "start_failed", "failed"
            raise MediaUnavailable("failed") from None
        try:
            returncode, output = await asyncio.wait_for(
                _collect(process), limits.FFMPEG_CHILD_WALL_SECONDS
            )
        except TimeoutError:
            outcome, code = "timeout", "timeout"
            raise MediaUnavailable("timeout") from None
        except _Flood:
            outcome, code = "flood", UNREADABLE
            raise MediaRefused(UNREADABLE) from None
        finally:
            await _stop(process)
        output_bytes = len(output)
        if returncode == EXIT_NOT_READY:
            outcome, code = "not_ready", "failed"
            raise MediaUnavailable("failed")
        if returncode == EXIT_NO_TOOL:
            outcome, code = "missing", "ffmpeg_missing"
            raise MediaUnavailable("ffmpeg_missing")
        if returncode < 0:
            # A signal: SIGXCPU or SIGKILL at the CPU limit, SIGXFSZ, or a crash.
            outcome, code = "killed", UNREADABLE
            raise MediaRefused(UNREADABLE)
        if returncode != 0:
            outcome = "failed"
            if source.startswith("https://"):
                code = "failed"  # stderr is never read, so a network failure looks the same
                raise MediaUnavailable("failed")
            code = UNREADABLE
            raise MediaRefused(UNREADABLE)
        try:
            result = parse(output)
        except (ValueError, UnicodeDecodeError, ValidationError, OSError):
            outcome, code = "bad_output", UNREADABLE
            raise MediaRefused(UNREADABLE) from None
        outcome = "ok"
        return result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        fields: dict[str, object] = {"tool": tool, "action": action, "outcome": outcome}
        if code is not None:
            fields["code"] = code
        elapsed_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "training_media_child", **fields, output_bytes=output_bytes, elapsed_ms=elapsed_ms
        )


async def _spawn(argv: list[str]) -> Process:
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=DEVNULL,
        stdout=PIPE,
        stderr=DEVNULL,
        env={},
        cwd="/",
        close_fds=True,
        start_new_session=True,  # its own process group, so a kill takes any grandchild too
    )


async def _collect(process: Process) -> tuple[int, bytes]:
    stdout = process.stdout
    assert stdout is not None
    chunks: list[bytes] = []
    size = 0
    while chunk := await stdout.read(64 * 1024):
        size += len(chunk)
        if size > limits.PROBE_MAX_OUTPUT_BYTES:
            raise _Flood
        chunks.append(chunk)
    return await process.wait(), b"".join(chunks)


async def _stop(process: Process) -> None:
    """Kill the child's process group unless it has been reaped already, then reap it.

    As ``pdf_guard._stop``: once reaped its pid may be reused, so the group is only signalled
    before that, and a stdout pipe asyncio stopped reading is drained so ``wait`` can return.
    """
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.stdout is not None:
        with contextlib.suppress(OSError):
            while await process.stdout.read(64 * 1024):
                pass
    await process.wait()
