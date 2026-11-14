"""Synthetic videos for the training tests, made with ffmpeg on the fly (not a test module).

Every file is generated here from ffmpeg's own sources (``testsrc``, ``sine``, ``anullsrc``,
``anoisesrc``) into the test's temporary directory; the repository is public and never holds a
media file. Pictures are 64x48 at 5 fps, so a minute of video is small and quick to make.
"""

from __future__ import annotations

import shutil
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
) -> Path:
    """Write a small synthetic video to ``path`` and return it.

    ``webm`` holds VP9 and Opus (the default ``libx264`` becomes ``libvpx-vp9`` there);
    ``mp4`` and ``mov`` hold the given video codec and AAC.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not on PATH")
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
        f"testsrc=size=64x48:rate=5:duration={seconds}",
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
