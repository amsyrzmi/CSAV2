"""Synthetic videos for the training tests, made with ffmpeg on the fly (not a test module).

Every file is generated here from ffmpeg's own sources (``testsrc``, ``sine``, ``anullsrc``,
``anoisesrc``) into the test's temporary directory; the repository is public and never holds a
media file. Pictures are 64x48 (unless asked otherwise) at 5 fps, so a minute of video is small and
quick to make.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path
from typing import Literal

AudioKind = Literal["tone", "silence", "noise", "none"]
Container = Literal["mp4", "webm", "mov"]

_AUDIO_SOURCES: dict[str, str] = {
    "tone": "sine=frequency=440:sample_rate=48000:duration={seconds}",
    "silence": "anullsrc=r=48000:cl=mono:d={seconds}",
    "noise": "anoisesrc=d={seconds}:c=pink:r=48000:a=0.1",
}

_VIDEO_OPTIONS: dict[str, list[str]] = {
    "libx264": ["-preset", "ultrafast", "-pix_fmt", "yuv420p"],
    "libx265": ["-preset", "ultrafast", "-x265-params", "log-level=error", "-tag:v", "hvc1"],
    "libvpx-vp9": ["-deadline", "realtime", "-cpu-used", "8", "-b:v", "100k"],
    "mjpeg": ["-pix_fmt", "yuvj420p"],
}

_MUXERS: dict[str, str] = {"mp4": "mp4", "webm": "webm", "mov": "mov"}


def make_video(
    path: Path,
    *,
    seconds: float,
    audio: AudioKind = "tone",
    container: Container = "mp4",
    video_codec: str = "libx264",
    size: tuple[int, int] = (64, 48),
) -> Path:
    """Write a small synthetic video to ``path`` and return it.

    ``webm`` holds VP9 and Opus (the default ``libx264`` becomes ``libvpx-vp9`` there);
    ``mp4`` and ``mov`` hold the given video codec and AAC.
    """
    ffmpeg = _ffmpeg()
    if container == "webm" and video_codec == "libx264":
        video_codec = "libvpx-vp9"
    args = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={size[0]}x{size[1]}:rate=5:duration={seconds}",
    ]
    if audio != "none":
        args += ["-f", "lavfi", "-i", _AUDIO_SOURCES[audio].format(seconds=seconds)]
    args += ["-map", "0:v:0"]
    if audio != "none":
        args += ["-map", "1:a:0", "-c:a", "libopus" if container == "webm" else "aac"]
        args += ["-b:a", "32k", "-t", str(seconds)]
    else:
        args += ["-an"]
    args += ["-c:v", video_codec, *_VIDEO_OPTIONS.get(video_codec, [])]
    args += ["-threads", "1", "-f", _MUXERS[container], str(path)]
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL, timeout=120)
    return path


def cover_art_first(video: Path, path: Path) -> Path:
    """Write ``video`` (an mp4) to ``path`` with a 200x200 JPEG cover that reads as stream 0.

    ffmpeg's mp4 muxer stores the cover (``attached_pic``) in ``moov/udta`` after the tracks, where
    the demuxer numbers it last; moving ``udta`` before the ``trak`` atoms makes it stream 0. The
    media data stays where it is (``moov`` comes after ``mdat``), so no chunk offset changes.
    """
    ffmpeg = _ffmpeg()
    common = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    cover = path.with_name(f"{path.stem}-cover.jpg")
    _run(
        [*common, "-f", "lavfi", "-i", "color=c=red:size=200x200", "-frames:v", "1"]
        + ["-pix_fmt", "yuvj420p", str(cover)]
    )
    muxed = path.with_name(f"{path.stem}-muxed.mp4")
    _run(
        [*common, "-i", str(video), "-i", str(cover), "-map", "0", "-map", "1", "-c", "copy"]
        + ["-disposition:v:1", "attached_pic", "-f", "mp4", str(muxed)]
    )
    atoms = []
    for kind, atom in _atoms(muxed.read_bytes()):
        if kind == b"moov":
            children = sorted(
                _atoms(atom[8:]), key=lambda child: {b"mvhd": 0, b"udta": 1}.get(child[0], 2)
            )
            assert [kind for kind, _ in children][:2] == [b"mvhd", b"udta"]
            atom = atom[:8] + b"".join(child for _, child in children)
        atoms.append((kind, atom))
    assert [kind for kind, _ in atoms].index(b"mdat") < [kind for kind, _ in atoms].index(b"moov")
    path.write_bytes(b"".join(atom for _, atom in atoms))
    return path


def _atoms(data: bytes) -> list[tuple[bytes, bytes]]:
    found: list[tuple[bytes, bytes]] = []
    offset = 0
    while offset < len(data):
        size, kind = struct.unpack(">I4s", data[offset : offset + 8])
        if size < 8:
            raise ValueError("an atom size this helper does not handle")
        found.append((kind, data[offset : offset + size]))
        offset += size
    return found


def _ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not on PATH")
    return ffmpeg


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL, timeout=120)
