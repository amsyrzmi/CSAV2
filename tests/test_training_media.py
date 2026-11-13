"""The hardened ffprobe and ffmpeg children for video audio (csa.training.media).

The video file is untrusted input. Every tool runs as a child with an empty environment, no
stdin, rlimits, ``nice 10``, stderr to /dev/null, a capped stdout and a wall clock that kills its
whole process group. These tests run the real ffmpeg on synthetic media made here
(``tests/training_media.py``; never a committed file) and small shell stand-ins for the tool
(``_fake_tool``) for the environment, limit, timeout and outcome cases. After every test no
child, nor any member of its process group, may still be running.
"""

from __future__ import annotations

import contextlib
import datetime
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from csa.speech import MAX_AUDIO_BYTES
from csa.training import limits, media
from csa.training.limits import VideoContainer
from csa.training.media import (
    MediaRefused,
    MediaUnavailable,
    Probe,
    check_playable,
    extract_chunk,
    input_args,
    max_volume_db,
    probe,
)
from structlog.testing import capture_logs

from tests.conftest import REPO_ROOT, requires_ffmpeg
from tests.test_training_pdf_guard import _proc_stat, _running, _running_in_group, _settle
from tests.training_media import make_video

LOG_KEYS = {"event", "log_level", "tool", "action", "outcome", "code", "elapsed_ms", "output_bytes"}


def test_limit_constants() -> None:
    assert limits.FFMPEG_CHILD_CPU_SECONDS == 309
    assert limits.FFMPEG_CHILD_MEMORY_BYTES == 469762048
    assert limits.FFMPEG_CHILD_WALL_SECONDS == 300
    assert limits.PROBE_MAX_OUTPUT_BYTES == 262144
    assert limits.SILENCE_MAX_DB == -50.0
    assert limits.PLAYABLE_VIDEO == {"h264", "vp8", "vp9", "av1", "hevc"}
    assert limits.PLAYABLE_AUDIO == {"aac", "mp3", "opus", "vorbis"}


# --- process bookkeeping --------------------------------------------------------------


@pytest.fixture(autouse=True)
def spawned(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """The pid of every child started; none may outlive the test."""
    pids: list[int] = []
    original = media._spawn

    async def recording(argv: list[str]) -> object:
        process = await original(argv)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(media, "_spawn", recording)
    yield pids
    for pid in pids:
        assert _proc_stat(pid) is None, f"child {pid} was not reaped"
        assert _settle(lambda pid=pid: not _running_in_group(pid)), f"group {pid} still running"


def _tool(name: str) -> str:
    found = shutil.which(name)
    assert found is not None, f"{name} is not on PATH"
    return found


def _fake_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> Path:
    """Run ``body`` (a /bin/sh script) wherever ffmpeg or ffprobe would run."""
    script = tmp_path / "fake-tool"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    monkeypatch.setattr(media, "_binary", lambda name: str(script))
    return script


def _junk(path: Path) -> Path:
    path.write_bytes(os.urandom(4096))
    return path


def _stand_in_ca_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A CA file that exists, for tests that never reach a TLS handshake."""
    ca_file = tmp_path / "stand-in-ca.pem"
    ca_file.write_text("")
    monkeypatch.setattr(media, "_CA_FILE", str(ca_file))
    return ca_file


# --- pure ---------------------------------------------------------------------------------


def _contains(haystack: list[str], needle: list[str]) -> bool:
    return any(haystack[i : i + len(needle)] == needle for i in range(len(haystack)))


def test_s3_sources_whitelist_https_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ca_file = _stand_in_ca_file(monkeypatch, tmp_path)
    url = "https://bucket.s3.example/training/a.mp4?X-Amz-Signature=abc"
    remote = input_args(url, "mp4")
    assert _contains(remote, ["-protocol_whitelist", "https,tls,tcp"])
    assert _contains(remote, ["-f", "mov"])
    # The query string is a credential: the certificate is checked against a CA bundle.
    assert _contains(remote, ["-tls_verify", "1"])
    assert _contains(remote, ["-ca_file", str(ca_file)])
    assert remote.index("-ca_file") < remote.index("-i")
    assert remote[-2:] == ["-i", url]
    webm = input_args(url, "webm")
    assert _contains(webm, ["-f", "matroska"])

    local = input_args("/srv/training/a.mp4", "mp4")
    assert "-tls_verify" not in local
    assert _contains(local, ["-protocol_whitelist", "file"])
    assert _contains(local, ["-f", "mov"])
    assert local[-2:] == ["-i", "file:/srv/training/a.mp4"]
    for args in (remote, webm, local):
        # The forced demuxer and the whitelist are input options: before -i.
        assert args.index("-f") < args.index("-i")
        assert args.index("-protocol_whitelist") < args.index("-i")

    for bad in (
        "http://bucket.s3.example/a.mp4",
        "a.mp4",
        "file:/srv/a.mp4",
        "concat:/a.mp4|/b.mp4",
        "subfile,,start,0,end,0,,:/a.mp4",
        "",
    ):
        with pytest.raises(ValueError):
            input_args(bad, "mp4")
    with pytest.raises(ValueError):
        input_args("/srv/a.mov", "mov")  # type: ignore[arg-type]


def test_the_ca_bundle_is_the_runtime_images(monkeypatch: pytest.MonkeyPatch) -> None:
    # python:3.12-slim-bookworm's bundle; the static ffmpeg's built-in default path does not exist
    # on Debian and the empty environment hides SSL_CERT_FILE.
    assert media._CA_FILE == "/etc/ssl/certs/ca-certificates.crt"


async def test_a_missing_ca_bundle_is_unavailable_never_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawned: list[int]
) -> None:
    monkeypatch.setattr(media, "_CA_FILE", str(tmp_path / "no-such-bundle.pem"))
    with pytest.raises(MediaUnavailable) as caught:
        await probe("https://bucket.example/a.mp4?X-Amz-Signature=s", "mp4")
    assert caught.value.code == "failed"
    assert spawned == []
    assert input_args("/srv/a.mp4", "mp4")  # a local file needs no bundle


def test_check_playable_without_a_file() -> None:
    assert check_playable(Probe(1000, "h264", "aac", True)) is None
    assert check_playable(Probe(1000, "vp9", None, False)) is None
    assert check_playable(Probe(1000, "hevc", "opus", True)) == "hevc"
    for bad in (
        Probe(1000, "mjpeg", "aac", True),
        Probe(1000, None, "aac", True),  # audio only
        Probe(1000, "h264", "pcm_s16le", True),
        Probe(1000, "h264", None, True),  # an audio stream of no known codec
    ):
        with pytest.raises(MediaRefused) as caught:
            check_playable(bad)
        assert caught.value.code == "training_video_codec"


def test_ci_installs_the_pinned_ffmpeg_and_requires_it() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps: list[dict[str, object]] = workflow["jobs"]["tests"]["steps"]
    installs = [step for step in steps if "docker create" in str(step.get("run", ""))]
    assert len(installs) == 1
    install = installs[0]
    run = str(install["run"])
    image_refs = re.findall(r"[\w./-]+@sha256:[0-9a-f]{64}", run + repr(install.get("env", {})))
    assert image_refs, "ffmpeg is not installed from an image pinned by digest"
    assert "docker cp" in run
    assert ":/ffmpeg" in run and ":/ffprobe" in run
    assert "GITHUB_PATH" in run
    # The same image Task 1 measured and chose.
    capacity = (REPO_ROOT / "docs" / "TRAINING_VIDEO_CAPACITY.md").read_text()
    chosen = re.search(r"FFMPEG_IMAGE=(\S+)", capacity)
    assert chosen is not None
    assert set(image_refs) == {chosen.group(1)}

    pytest_step = next(step for step in steps if step.get("name") == "Pytest")
    env = pytest_step["env"]
    assert isinstance(env, dict)
    assert env["CSA_REQUIRE_FFMPEG"] == "1"
    assert steps.index(install) < steps.index(pytest_step)


def test_dockerfile_and_ci_pin_the_same_ffmpeg() -> None:
    dockerfile = (REPO_ROOT / "deploy" / "Dockerfile").read_text()
    # Only the runtime stage ships: the copy must be there, not in the build stage.
    runtime = dockerfile.split(" AS runtime", 1)[1]
    copies = re.findall(
        r"^COPY --from=(\S+@sha256:[0-9a-f]{64}) /ffmpeg /ffprobe /usr/local/bin/$",
        runtime,
        flags=re.MULTILINE,
    )
    assert len(copies) == 1, "the runtime stage does not copy ffmpeg and ffprobe by digest"
    assert runtime.index("COPY --from=" + copies[0]) < runtime.index("USER 1000:1000")
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps: list[dict[str, object]] = workflow["jobs"]["tests"]["steps"]
    install = next(step for step in steps if "docker create" in str(step.get("run", "")))
    env = install["env"]
    assert isinstance(env, dict)
    assert env["FFMPEG_IMAGE"] == copies[0]
    # Every reference to that image in either file is the same digest-pinned one.
    references = re.findall(r"mwader/static-ffmpeg[^\s\"']*", dockerfile + yaml.safe_dump(env))
    assert set(references) == {copies[0]}


async def test_missing_tool_is_unavailable_without_a_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawned: list[int]
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(MediaUnavailable) as caught:
        await probe(str(_junk(tmp_path / "a.mp4")), "mp4")
    assert caught.value.code == "ffmpeg_missing"
    assert spawned == []


# --- real ffmpeg ------------------------------------------------------------------------


@requires_ffmpeg
async def test_probe_reads_duration_codecs_and_audio(tmp_path: Path) -> None:
    mp4 = make_video(tmp_path / "a.mp4", seconds=3)
    found = await probe(str(mp4), "mp4")
    assert abs(found.duration_ms - 3000) <= 150
    assert (found.video_codec, found.audio_codec, found.has_audio) == ("h264", "aac", True)

    webm = make_video(tmp_path / "a.webm", seconds=3, container="webm")
    found = await probe(str(webm), "webm")
    assert abs(found.duration_ms - 3000) <= 150
    assert (found.video_codec, found.audio_codec, found.has_audio) == ("vp9", "opus", True)


@requires_ffmpeg
async def test_probe_without_an_audio_stream(tmp_path: Path) -> None:
    video = make_video(tmp_path / "a.mp4", seconds=2, audio="none")
    found = await probe(str(video), "mp4")
    assert abs(found.duration_ms - 2000) <= 150
    assert (found.video_codec, found.audio_codec, found.has_audio) == ("h264", None, False)
    assert check_playable(found) is None


@requires_ffmpeg
async def test_unreadable_file_is_refused(tmp_path: Path) -> None:
    junk = _junk(tmp_path / "junk.mp4")
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    webm = make_video(tmp_path / "real.webm", seconds=1, container="webm")
    out = tmp_path / "out.ogg"
    cases: list[tuple[Path, VideoContainer]] = [
        (junk, "mp4"),
        (junk, "webm"),
        (empty, "mp4"),
        (webm, "mp4"),
    ]
    for source, container in cases:
        for call in (
            probe(str(source), container),
            max_volume_db(str(source), container),
            extract_chunk(str(source), container, 0, 5, out),
        ):
            with pytest.raises(MediaRefused) as caught:
                await call
            assert caught.value.code == "training_video_unreadable"


@requires_ffmpeg
async def test_codec_policy(tmp_path: Path) -> None:
    mjpeg = make_video(tmp_path / "a.mov", seconds=1, container="mov", video_codec="mjpeg")
    found = await probe(str(mjpeg), "mp4")  # a .mov is stored and demuxed as mp4
    assert found.video_codec == "mjpeg"
    with pytest.raises(MediaRefused) as caught:
        check_playable(found)
    assert caught.value.code == "training_video_codec"

    hevc = make_video(tmp_path / "h.mp4", seconds=1, video_codec="libx265")
    found = await probe(str(hevc), "mp4")
    assert found.video_codec == "hevc"
    assert check_playable(found) == "hevc"


class _OpenWatch:
    """A FIFO at ``path`` that notices whether anything opens it for reading."""

    def __init__(self, path: Path) -> None:
        self.path = path
        os.mkfifo(path)
        self.arm()

    def arm(self) -> None:
        self.opened = threading.Event()
        self._thread = threading.Thread(target=self._wait, daemon=True)
        self._thread.start()

    def _wait(self) -> None:
        descriptor = os.open(self.path, os.O_WRONLY)  # returns once a reader opens it
        self.opened.set()
        os.close(descriptor)

    def was_opened(self) -> bool:
        """Whether a reader opened it so far; then release the waiting thread."""
        self._thread.join(timeout=1.0)  # a real reader woke it well before now
        opened = self.opened.is_set()
        if not opened:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
            self._thread.join(timeout=5.0)
            os.close(descriptor)
        assert not self._thread.is_alive()
        return opened


def _connections(listener: socket.socket) -> int:
    listener.setblocking(False)
    count = 0
    while True:
        try:
            connection, _address = listener.accept()
        except BlockingIOError:
            return count
        connection.close()
        count += 1


@requires_ffmpeg
async def test_playlists_and_concat_lists_never_read_outside_the_source(tmp_path: Path) -> None:
    # The harness itself notices a read and a connection.
    control = _OpenWatch(tmp_path / "control")
    os.close(os.open(control.path, os.O_RDONLY))
    assert control.was_opened()
    with socket.create_server(("127.0.0.1", 0)) as probe_listener:
        socket.create_connection(probe_listener.getsockname()).close()
        assert _connections(probe_listener) == 1

    marker = _OpenWatch(tmp_path / "marker.ts")
    with socket.create_server(("127.0.0.1", 0), backlog=64) as listener:
        port = listener.getsockname()[1]
        remote = f"http://127.0.0.1:{port}"
        hls = tmp_path / "playlist.mp4"
        hls.write_text(
            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n"
            f"#EXTINF:10.0,\nfile:{marker.path}\n#EXTINF:10.0,\n{marker.path}\n"
            f"#EXTINF:10.0,\n{remote}/segment.ts\n#EXT-X-ENDLIST\n"
        )
        concat = tmp_path / "list.mp4"
        concat.write_text(
            f"ffconcat version 1.0\nfile '{marker.path}'\nfile 'file:{marker.path}'\n"
            f"file '{remote}/clip.mp4'\n"
        )
        # A relative entry next to the list passes the concat demuxer's own safe=1 check, so
        # only the forced demuxer keeps this one from being followed.
        relative = tmp_path / "relative.mp4"
        relative.write_text(f"ffconcat version 1.0\nfile '{marker.path.name}'\n")
        out = tmp_path / "out.ogg"
        for source in (hls, concat, relative):
            for container in ("mp4", "webm"):
                for call in (
                    probe(str(source), container),
                    max_volume_db(str(source), container),
                    extract_chunk(str(source), container, 0, 5, out),
                ):
                    with pytest.raises(MediaRefused) as caught:
                        await call
                    assert caught.value.code == "training_video_unreadable"
        assert _connections(listener) == 0
    assert not marker.was_opened()

    # Control: the same relative list, read without a forced demuxer, is followed to the marker.
    marker.arm()
    subprocess.run(
        [_tool("ffprobe"), "-v", "error", str(relative)],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )
    assert marker.was_opened()


def _playlist(segment_url: str) -> str:
    return (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n"
        f"#EXTINF:10.0,\n{segment_url}\n#EXT-X-ENDLIST\n"
    )


def _served(body: str) -> bytes:
    data = body.encode()
    head = (
        "HTTP/1.1 200 OK\r\nContent-Type: application/vnd.apple.mpegurl\r\n"
        f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + data


@requires_ffmpeg
async def test_the_protocol_whitelist_keeps_a_followed_playlist_off_the_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """As if the forced container had become hls: only the whitelist stands in the way."""
    monkeypatch.setattr(limits, "FFMPEG_CHILD_WALL_SECONDS", 15)
    monkeypatch.setitem(media._DEMUXERS, "mp4", "hls")
    ca_file, ca = _test_ca(tmp_path)
    monkeypatch.setattr(media, "_CA_FILE", str(ca_file))
    cert_file, key_file = _issue(
        tmp_path, "playlist-host", [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))], ca
    )
    with socket.create_server(("127.0.0.1", 0), backlog=64) as listener:
        segment = f"http://127.0.0.1:{listener.getsockname()[1]}/segment.ts"
        answer = _served(_playlist(segment))

        # Control: the same presigned-style source read without our whitelist (https's default
        # lets a playlist reach plain http) connects out.
        server = _TlsServer(cert_file, key_file, answer)
        subprocess.run(
            [_tool("ffprobe"), "-v", "error", "-rw_timeout", "1000000"]
            + ["-tls_verify", "1", "-ca_file", str(ca_file), "-f", "hls"]
            + ["-i", f"https://127.0.0.1:{server.port}/playlist.m3u8?sig=abc"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
        )
        server.close()
        assert server.received.startswith(b"GET /playlist.m3u8")
        assert _connections(listener) >= 1

        # The production arguments: the playlist is fetched, its http segment never is.
        server = _TlsServer(cert_file, key_file, answer)
        with pytest.raises(MediaUnavailable):
            await probe(f"https://127.0.0.1:{server.port}/playlist.m3u8?sig=abc", "mp4")
        server.close()
        assert server.received.startswith(b"GET /playlist.m3u8")
        assert _connections(listener) == 0

        # A local playlist: ffmpeg's file protocol already refuses nested http by default; with a
        # whitelist that allows http it connects, with ours it does not.
        playlist = tmp_path / "playlist.mp4"
        playlist.write_text(_playlist(segment))
        subprocess.run(
            [_tool("ffprobe"), "-v", "error", "-rw_timeout", "1000000"]
            + ["-protocol_whitelist", "file,http,tcp", "-f", "hls", "-i", f"file:{playlist}"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
        )
        assert _connections(listener) >= 1
        with pytest.raises(MediaRefused) as caught:
            await probe(str(playlist), "mp4")
        assert caught.value.code == "training_video_unreadable"
        assert _connections(listener) == 0


async def test_child_gets_an_empty_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CSA_SYNTHETIC_SECRET", "synthetic-value-not-a-real-key")
    report = tmp_path / "environ"
    # /proc/<pid>/environ is exactly what the tool was started with (the shell adds its own
    # variables later, in memory only).
    _fake_tool(monkeypatch, tmp_path, f"{_tool('cat')} /proc/$$/environ > {report}")
    with pytest.raises(MediaRefused):
        await probe(str(_junk(tmp_path / "a.mp4")), "mp4")
    assert report.read_bytes() == b""


async def test_child_runs_under_the_limits_niced_without_stdin_or_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "report"
    readlink = _tool("readlink")
    _fake_tool(
        monkeypatch,
        tmp_path,
        f"{_tool('cat')} /proc/self/limits > {report}.limits\n"
        f"{_tool('cat')} /proc/$$/stat > {report}.stat\n"
        f"{_tool('cat')} /proc/$$/status > {report}.status\n"
        f"{readlink} /proc/$$/fd/0 > {report}.stdin\n"
        f"{readlink} /proc/$$/fd/2 > {report}.stderr\n"
        f"{readlink} /proc/$$/cwd > {report}.cwd\n"
        f"{readlink} /proc/$$/exe > {report}.exe\n",
    )
    # No level printed at all reads as digital silence.
    assert await max_volume_db(str(_junk(tmp_path / "a.mp4")), "mp4") == float("-inf")

    seen: dict[str, tuple[str, str]] = {}
    for line in Path(f"{report}.limits").read_text().splitlines()[1:]:
        found = re.match(r"(Max [a-z ]+?)\s{2,}(\S+)\s+(\S+)", line)
        assert found is not None
        seen[found.group(1)] = (found.group(2), found.group(3))
    cpu = limits.FFMPEG_CHILD_CPU_SECONDS
    assert seen["Max cpu time"] == (str(cpu), str(cpu + 1))
    memory = str(limits.FFMPEG_CHILD_MEMORY_BYTES)
    assert seen["Max address space"] == (memory, memory)
    assert seen["Max file size"] == (str(MAX_AUDIO_BYTES), str(MAX_AUDIO_BYTES))
    assert seen["Max core file size"] == ("0", "0")
    assert int(seen["Max open files"][1]) <= 64

    stat = Path(f"{report}.stat").read_text()
    fields = stat[stat.rindex(")") + 2 :].split()
    assert int(fields[16]) == min(os.nice(0) + 10, 19)  # field 19 of /proc/<pid>/stat: nice
    assert int(fields[3]) == int(fields[2]) == int(stat.split()[0])  # its own session and group
    assert Path(f"{report}.stdin").read_text().strip() == "/dev/null"
    assert Path(f"{report}.stderr").read_text().strip() == "/dev/null"
    assert Path(f"{report}.cwd").read_text().strip() == "/"
    # Python ignores SIGPIPE and SIGXFSZ; the tool must not inherit that (an oversize file must
    # end it with SIGXFSZ, not a write error it may ignore).
    status = Path(f"{report}.status").read_text()
    ignored = re.search(r"^SigIgn:\s*([0-9a-f]+)$", status, re.MULTILINE)
    assert ignored is not None
    # Only these two: pytest itself may run with others ignored (SIGHUP under nohup).
    assert int(ignored.group(1), 16) & (1 << (signal.SIGPIPE - 1)) == 0
    assert int(ignored.group(1), 16) & (1 << (signal.SIGXFSZ - 1)) == 0
    # The bootstrap replaced itself with the tool: no Python process stays in between.
    assert Path(f"{report}.exe").read_text().strip() == os.path.realpath("/bin/sh")


async def test_timeout_kills_the_whole_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawned: list[int]
) -> None:
    monkeypatch.setattr(limits, "FFMPEG_CHILD_WALL_SECONDS", 1)
    pid_file = tmp_path / "grandchild"
    sleep = _tool("sleep")
    _fake_tool(monkeypatch, tmp_path, f"{sleep} 60 &\necho $! > {pid_file}\n{sleep} 60")
    started = time.monotonic()
    with capture_logs() as logs:
        with pytest.raises(MediaUnavailable) as caught:
            await probe(str(_junk(tmp_path / "a.mp4")), "mp4")
    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 4.0
    assert [entry["outcome"] for entry in logs] == ["timeout"]
    grandchild = int(pid_file.read_text())
    assert _settle(lambda: not _running(grandchild))
    assert len(spawned) == 1
    assert _proc_stat(spawned[0]) is None


async def test_stderr_never_reaches_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stand_in_ca_file(monkeypatch, tmp_path)
    _fake_tool(monkeypatch, tmp_path, "echo MARKER-STDERR-QZX >&2\nexit 1")
    source = _junk(tmp_path / "Rahsia MARKER-NAME.mp4")
    with capture_logs() as logs:
        for call in (
            probe(str(source), "mp4"),
            max_volume_db(str(source), "mp4"),
            extract_chunk(str(source), "mp4", 0, 5, tmp_path / "out.ogg"),
        ):
            with pytest.raises(MediaRefused) as caught:
                await call
            assert caught.value.code == "training_video_unreadable"
            assert "MARKER" not in str(caught.value)
        with pytest.raises(MediaUnavailable):  # a remote source failing may be the network
            await probe("https://bucket.example/MARKER-URL.mp4?X-Amz-Signature=s", "mp4")
    assert len(logs) == 4
    for entry in logs:
        assert set(entry) <= LOG_KEYS
    dumped = repr(logs)
    assert "MARKER" not in dumped
    assert "Rahsia" not in dumped
    assert str(tmp_path) not in dumped


@pytest.mark.parametrize(
    ("body", "source", "outcome", "error", "code"),
    [
        pytest.param(
            "exit 1", "local", "failed", MediaRefused, "training_video_unreadable", id="exit-local"
        ),
        pytest.param("exit 1", "https", "failed", MediaUnavailable, "failed", id="exit-https"),
        pytest.param(
            "kill -KILL $$",
            "local",
            "killed",
            MediaRefused,
            "training_video_unreadable",
            id="killed",
        ),
        pytest.param(
            "kill -KILL $$",
            "https",
            "killed",
            MediaRefused,
            "training_video_unreadable",
            id="killed-https",
        ),
        pytest.param(
            "/usr/bin/head -c 400000 /dev/zero",
            "local",
            "flood",
            MediaRefused,
            "training_video_unreadable",
            id="flood",
        ),
        pytest.param(
            f"exec /usr/bin/head -c {MAX_AUDIO_BYTES + 1} /dev/zero > ${{0%/*}}/big",
            "local",
            "killed",
            MediaRefused,
            "training_video_unreadable",
            id="file-size-limit",
        ),
        pytest.param(
            "echo 'not json'",
            "local",
            "bad_output",
            MediaRefused,
            "training_video_unreadable",
            id="garbage",
        ),
        pytest.param(
            'echo \'{"streams": [], "format": {"duration": "3.0"}}\'',
            "local",
            "bad_output",
            MediaRefused,
            "training_video_unreadable",
            id="no-streams",
        ),
        pytest.param(
            'echo \'{"streams": [{"codec_type": "video", "codec_name": "h264"}], "format": {}}\'',
            "local",
            "bad_output",
            MediaRefused,
            "training_video_unreadable",
            id="no-duration",
        ),
    ],
)
async def test_child_outcomes_map_to_codes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: str,
    source: str,
    outcome: str,
    error: type[MediaRefused] | type[MediaUnavailable],
    code: str,
) -> None:
    monkeypatch.setattr(limits, "FFMPEG_CHILD_WALL_SECONDS", 10)
    _stand_in_ca_file(monkeypatch, tmp_path)
    _fake_tool(monkeypatch, tmp_path, body)
    where = str(_junk(tmp_path / "a.mp4")) if source == "local" else "https://bucket.example/a.mp4"
    with capture_logs() as logs:
        with pytest.raises(error) as caught:
            await probe(where, "mp4")
    assert caught.value.code == code
    assert [entry["outcome"] for entry in logs] == [outcome]


async def test_a_good_probe_answer_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    answer = (
        '{"streams": [{"codec_type": "video", "codec_name": "mjpeg",'
        ' "disposition": {"attached_pic": 1}}, {"codec_type": "audio", "codec_name": "mp3"}],'
        ' "format": {"duration": "12.3456"}}'
    )
    _fake_tool(monkeypatch, tmp_path, f"echo '{answer}'")
    # Cover art is not a video stream.
    assert await probe(str(_junk(tmp_path / "a.mp4")), "mp4") == Probe(12346, None, "mp3", True)


@requires_ffmpeg
async def test_extract_chunk_is_16k_mono_opus_under_the_request_cap(tmp_path: Path) -> None:
    video = make_video(tmp_path / "a.mp4", seconds=60)
    out = tmp_path / "chunk.ogg"
    written = await extract_chunk(str(video), "mp4", 0, 30, out)
    assert written == out.stat().st_size
    assert 0 < written < MAX_AUDIO_BYTES

    found = _ffprobe_audio(out)
    assert found["codec_name"] == "opus"
    assert found["channels"] == 1
    assert abs(float(found["duration"]) - 30) <= 0.5
    # ffprobe reports Opus at its 48 kHz decode rate; the stream's own header (OpusHead) holds
    # the rate it was encoded from.
    data = out.read_bytes()
    head = data.index(b"OpusHead")
    assert data[head + 9] == 1
    assert struct.unpack("<I", data[head + 12 : head + 16])[0] == 16000
    assert b"OggS" == data[:4]

    # A later chunk starts where it is asked to and stops at the end of the video.
    tail = tmp_path / "tail.ogg"
    assert await extract_chunk(str(video), "mp4", 50, 30, tail) == tail.stat().st_size
    assert abs(float(_ffprobe_audio(tail)["duration"]) - 10) <= 0.5


def _ffprobe_audio(path: Path) -> dict[str, Any]:
    output = subprocess.run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "stream=codec_name,channels:format=duration",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    parsed = json.loads(output)
    (stream,) = parsed["streams"]
    return {**stream, "duration": parsed["format"]["duration"]}


@requires_ffmpeg
async def test_max_volume_tells_silence_from_tone(tmp_path: Path) -> None:
    silence = make_video(tmp_path / "silence.mp4", seconds=3, audio="silence")
    tone = make_video(tmp_path / "tone.mp4", seconds=3)
    noise = make_video(tmp_path / "noise.webm", seconds=3, audio="noise", container="webm")
    quiet = await max_volume_db(str(silence), "mp4")
    loud = await max_volume_db(str(tone), "mp4")
    assert quiet <= limits.SILENCE_MAX_DB < loud
    assert await max_volume_db(str(noise), "webm") > limits.SILENCE_MAX_DB

    # The whole first audio stream counts, not its first frame: 70 s of silence with a tone in
    # its last 3 s (one measuring frame holds about 65 s of 16 kHz audio).
    late = tmp_path / "late.mp4"
    subprocess.run(
        [
            _tool("ffmpeg"),
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x48:rate=1:duration=70",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=70,volume=volume=0:enable='lt(t,67)'",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            "-threads",
            "1",
            str(late),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert await max_volume_db(str(late), "mp4") > limits.SILENCE_MAX_DB


# --- TLS for presigned URLs -------------------------------------------------------------------


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _issue(
    directory: Path,
    label: str,
    names: list[x509.GeneralName],
    issuer: tuple[ec.EllipticCurvePrivateKey, x509.Name] | None = None,
    *,
    ca: bool = False,
) -> tuple[Path, Path]:
    """A throwaway certificate and key, made here (never committed); self-signed by default."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = _name(label)
    signer_key, signer_name = issuer if issuer is not None else (key, subject)
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(signer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    certificate = builder.sign(signer_key, hashes.SHA256())
    cert_file = directory / f"{label}.pem"
    key_file = directory / f"{label}.key"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


def _test_ca(directory: Path) -> tuple[Path, tuple[ec.EllipticCurvePrivateKey, x509.Name]]:
    cert_file, key_file = _issue(directory, "test-ca", [], ca=True)
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return cert_file, (key, _name("test-ca"))


class _TlsServer:
    """Accepts one TLS connection on 127.0.0.1, records what arrives after the handshake and
    sends ``answer`` back, if any."""

    def __init__(self, cert_file: Path, key_file: Path, answer: bytes = b"") -> None:
        self._answer = answer
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(cert_file, key_file)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(30)
        self.port: int = self._listener.getsockname()[1]
        self.connected = False
        self.handshake_failed = False
        self.received = b""
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _address = self._listener.accept()
        except OSError:
            return
        self.connected = True
        with connection:
            try:
                tls = self._context.wrap_socket(connection, server_side=True)
            except (ssl.SSLError, OSError):
                self.handshake_failed = True
                return
            with tls:
                tls.settimeout(10)
                with contextlib.suppress(OSError):
                    self.received = tls.recv(4096)
                    if self._answer:
                        tls.sendall(self._answer)

    def close(self) -> None:
        self._thread.join(timeout=30)
        self._listener.close()
        assert not self._thread.is_alive()


async def _probe_through(server: _TlsServer, host: str) -> None:
    try:
        with pytest.raises(MediaUnavailable) as caught:
            await probe(f"https://{host}:{server.port}/x?sig=abc", "mp4")
        assert caught.value.code == "failed"
    finally:
        server.close()


@requires_ffmpeg
async def test_a_self_signed_server_never_sees_the_presigned_request(tmp_path: Path) -> None:
    if not Path(media._CA_FILE).is_file():
        pytest.skip("this host has no system CA bundle at the runtime image's path")
    cert_file, key_file = _issue(
        tmp_path, "self-signed", [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    )
    server = _TlsServer(cert_file, key_file)
    await _probe_through(server, "127.0.0.1")  # the production CA bundle
    assert server.connected
    assert server.handshake_failed
    assert server.received == b""


@requires_ffmpeg
async def test_a_trusted_certificate_for_the_host_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Control for the hostname test below: the test CA is used and a matching certificate passes.
    ca_file, ca = _test_ca(tmp_path)
    monkeypatch.setattr(media, "_CA_FILE", str(ca_file))
    cert_file, key_file = _issue(tmp_path, "localhost", [x509.DNSName("localhost")], ca)
    server = _TlsServer(cert_file, key_file)
    await _probe_through(server, "localhost")
    assert server.received.startswith(b"GET /x?sig=abc HTTP/1.1\r\n")


@requires_ffmpeg
@pytest.mark.xfail(
    strict=True,
    reason="ffmpeg 7.1's OpenSSL backend checks the chain but not the hostname (also with "
    "-verifyhost); see csa/training/media.py and the Task 13 storage smoke",
)
async def test_a_trusted_certificate_for_another_host_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ca_file, ca = _test_ca(tmp_path)
    monkeypatch.setattr(media, "_CA_FILE", str(ca_file))
    cert_file, key_file = _issue(tmp_path, "other", [x509.DNSName("other.example")], ca)
    server = _TlsServer(cert_file, key_file)
    await _probe_through(server, "localhost")
    assert server.received == b""


# --- WebM without a duration (browser recordings) ---------------------------------------------


def _streamed_webm(path: Path, seconds: int) -> Path:
    """A VP9/Opus WebM muxed to a pipe, as MediaRecorder writes it: no Duration, no Cues."""
    with path.open("wb") as out:
        subprocess.run(
            [
                _tool("ffmpeg"),
                "-nostdin",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size=64x48:rate=5:duration={seconds}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:sample_rate=48000:duration={seconds}",
                "-c:v",
                "libvpx-vp9",
                "-deadline",
                "realtime",
                "-cpu-used",
                "8",
                "-b:v",
                "100k",
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                "-threads",
                "1",
                "-f",
                "webm",
                "pipe:1",
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=out,
            timeout=120,
        )
    return path


@requires_ffmpeg
async def test_webm_without_a_duration_is_measured_by_demuxing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    webm = _streamed_webm(tmp_path / "recorded.webm", 6)
    shown = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"]
        + [str(webm)],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    assert shown.strip() == b"N/A"  # the precondition: ffprobe alone cannot tell

    commands: list[list[str]] = []
    original_command = media._command

    def recording(tool: str, args: list[str]) -> list[str]:
        commands.append(args)
        return original_command(tool, args)

    monkeypatch.setattr(media, "_command", recording)
    with capture_logs() as logs:
        found = await probe(str(webm), "webm")
    assert _contains(commands[-1], ["-stats_period", "1"])  # progress once a second
    assert abs(found.duration_ms - 6000) <= 150
    assert (found.video_codec, found.audio_codec, found.has_audio) == ("vp9", "opus", True)
    assert [(entry["action"], entry["outcome"]) for entry in logs] == [
        ("probe", "ok"),
        ("duration", "ok"),
    ]

    # The demux pass stops just past the longest accepted video, so the caller still sees "too
    # long" without reading a whole hostile file.
    monkeypatch.setattr(limits, "MAX_VIDEO_SECONDS", 2)
    longer = await probe(str(webm), "webm")
    assert 2000 < longer.duration_ms <= 3200

    # Random bytes stored as webm are still unreadable.
    with pytest.raises(MediaRefused) as caught:
        await probe(str(_junk(tmp_path / "junk.webm")), "webm")
    assert caught.value.code == "training_video_unreadable"
