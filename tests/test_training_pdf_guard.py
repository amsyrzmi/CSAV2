"""Isolated PDF parsing for the training library (csa.training.pdf_guard and pdf_worker).

Each parse runs in a short-lived child process with CPU, memory and wall-clock limits.
These tests run the real worker for the parity and CPU-bomb cases, and small stand-in
child scripts (``_fake_worker``) for the kill, flood, environment and concurrency cases.
Every PDF is synthetic and built here; no fixtures on disk. After every test no process
started by the guard, nor any member of its process group, may still be running.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from csa.training import limits, pdf_guard
from csa.training.pdf import PdfInfo, PdfRefused, check_pdf, extract_text
from csa.training.pdf_guard import PdfGuardError, check_pdf_isolated, extract_text_isolated
from structlog.testing import capture_logs

from tests.test_training_pdf import WIDESCREEN, bad_files, make_pdf, raw_pdf

PACKAGE_ROOT = str(Path(pdf_guard.__file__).resolve().parents[2])
CHECK_RESULT = '{"ok":true,"page_count":1,"landscape":false}'


def test_limit_constants() -> None:
    assert limits.PDF_CHILD_CPU_SECONDS == 10
    assert limits.PDF_CHILD_WALL_SECONDS == 20
    assert limits.PDF_CHILD_MEMORY_BYTES == 1024 * 1024 * 1024
    assert limits.PDF_CHILD_MAX_CONCURRENT == 2


# --- process bookkeeping --------------------------------------------------------------


def _proc_stat(pid: int) -> tuple[str, int] | None:
    """(state, process group) of ``pid``, or None when it no longer exists."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = raw[raw.rindex(")") + 2 :].split()
    return fields[0], int(fields[2])


def _running_in_group(pgid: int) -> list[int]:
    """Live (non-zombie) processes whose process group is ``pgid``."""
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        stat = _proc_stat(int(entry.name))
        if stat is not None and stat[1] == pgid and stat[0] != "Z":
            members.append(int(entry.name))
    return members


def _settle(condition: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _running(pid: int) -> bool:
    stat = _proc_stat(pid)
    return stat is not None and stat[0] != "Z"


@pytest.fixture(autouse=True)
def spawned(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """The pid of every child the guard starts; none may outlive the test."""
    pids: list[int] = []
    original = pdf_guard._spawn

    async def recording(argv: list[str]) -> asyncio.subprocess.Process:
        process = await original(argv)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(pdf_guard, "_spawn", recording)
    yield pids
    for pid in pids:
        # The direct child is reaped (gone, not a zombie) and its whole group is dead.
        assert _proc_stat(pid) is None, f"child {pid} was not reaped"
        assert _settle(lambda pid=pid: not _running_in_group(pid)), f"group {pid} still running"


def _fake_worker(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Run ``body`` (Python source) as the child instead of the real worker."""

    def command(args: list[str]) -> list[str]:
        return [sys.executable, "-I", "-c", body, *args]

    monkeypatch.setattr(pdf_guard, "_worker_command", command)


def _worker_with_action(
    monkeypatch: pytest.MonkeyPatch, action_source: str, name: str = "check"
) -> None:
    """The real worker (limits, stdin, output) with its ``name`` action replaced."""
    body = (
        "import sys, resource\n"
        f"sys.path.append({PACKAGE_ROOT!r})\n"
        "from csa.training import pdf_worker\n"
        f"{action_source}\n"
        f"pdf_worker._ACTIONS[{name!r}] = action\n"
        "sys.exit(pdf_worker.main(sys.argv[1:]))\n"
    )
    _fake_worker(monkeypatch, body)


async def _unreadable(call: Any) -> None:
    with pytest.raises(PdfRefused) as caught:
        await call
    assert caught.value.code == "training_pdf_unreadable"


# --- parity with the in-process functions -----------------------------------------------


def _check_cases() -> list[tuple[str, str, bytes]]:
    minimal = raw_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        ]
    )
    good = [
        ("widescreen", "Induction deck.pdf", make_pdf(3, size=WIDESCREEN)),
        ("portrait", "Latihan keselamatan çé 你好.PDF", make_pdf(2)),
        ("raw", "raw.pdf", minimal),
    ]
    return good + [(label, name, data) for label, name, data, _code in bad_files()]


def _outcome(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except PdfRefused as refused:
        return ("refused", refused.code)


async def _outcome_async(call: Any) -> Any:
    try:
        return await call
    except PdfRefused as refused:
        return ("refused", refused.code)


@pytest.mark.parametrize(
    ("file_name", "data"),
    [pytest.param(name, data, id=label) for label, name, data in _check_cases()],
)
async def test_check_isolated_matches_in_process_for_good_and_bad_files(
    file_name: str, data: bytes
) -> None:
    expected = _outcome(lambda: check_pdf(data, file_name))
    assert await _outcome_async(check_pdf_isolated(data, file_name)) == expected


def _texts(count: int) -> list[str]:
    return [f"Page {number:03d} " + "safety briefing words " * 4 for number in range(1, count + 1)]


@pytest.mark.parametrize(
    ("data", "max_chars"),
    [
        pytest.param(make_pdf(3, texts=_texts(3)), 24_000, id="whole"),
        pytest.param(make_pdf(30, texts=_texts(30)), 1000, id="cut"),
        pytest.param(make_pdf(4), 24_000, id="scan"),
        pytest.param(b"%PDF-1.7\n" + bytes(range(256)) * 8, 24_000, id="damaged"),
        pytest.param(b"not a pdf", 24_000, id="no-header"),
        pytest.param(make_pdf(301), 24_000, id="too-many-pages"),
    ],
)
async def test_extract_isolated_matches_in_process_text_and_truncation(
    data: bytes, max_chars: int
) -> None:
    expected = _outcome(lambda: extract_text(data, max_chars))
    assert await _outcome_async(extract_text_isolated(data, max_chars)) == expected


async def test_extract_isolated_turns_a_lone_surrogate_into_a_replacement_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pypdf decodes ToUnicode entries with "surrogatepass", so a broken font map can give a lone
    # surrogate, which the parent's JSON parser refuses. The worker sends U+FFFD in its place and
    # keeps a valid pair, so the text still arrives as text, not as training_pdf_unreadable.
    _worker_with_action(
        monkeypatch,
        "def action(data, args):\n"
        "    return {'ok': True, 'text': 'a\\ud800b\\udc00c\\ud83d\\ude00', 'truncated': False}",
        "extract",
    )
    assert await extract_text_isolated(make_pdf(), 100) == ("a\ufffdb\ufffdc\U0001f600", False)


async def test_extract_isolated_rejects_a_non_positive_cap_without_a_child(
    spawned: list[int],
) -> None:
    with pytest.raises(ValueError):
        await extract_text_isolated(make_pdf(), 0)
    assert spawned == []


async def test_bad_name_is_refused_without_a_child(spawned: list[int]) -> None:
    for name in ("a/b.pdf", "nul\x00.pdf", "lone \ud800.pdf", "slides.pptx", "x" * 300 + ".pdf"):
        assert await _outcome_async(check_pdf_isolated(make_pdf(), name)) == _outcome(
            lambda name=name: check_pdf(make_pdf(), name)
        )
    assert spawned == []


# --- hard limits ------------------------------------------------------------------------


def _undefined_reference_bomb(references: int = 1000, pad: int = 1_000_000) -> bytes:
    """Each /Kids reference to the undefined object 9 makes pypdf regex-scan the whole
    file again: about 13 ms of CPU per reference per MB, so this costs about 13 s."""
    kids = b" ".join([b"3 0 R"] + [b"9 0 R"] * references)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    out = bytearray(b"%PDF-1.7\n%" + b"a" * pad + b"\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 4\n0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


async def test_cpu_bomb_is_killed_and_reported_unreadable(
    monkeypatch: pytest.MonkeyPatch, spawned: list[int]
) -> None:
    monkeypatch.setattr(limits, "PDF_CHILD_CPU_SECONDS", 1)
    monkeypatch.setattr(limits, "PDF_CHILD_WALL_SECONDS", 3)
    started = time.monotonic()
    with capture_logs() as logs:
        await _unreadable(check_pdf_isolated(_undefined_reference_bomb(), "bomb.pdf"))
    elapsed = time.monotonic() - started
    # Stopped by the CPU limit (a signal), before the wall clock ran out.
    assert [entry["outcome"] for entry in logs] == ["killed"]
    assert 0.9 < elapsed < 6.0
    assert len(spawned) == 1
    assert _proc_stat(spawned[0]) is None


async def test_wall_timeout_kills_the_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawned: list[int]
) -> None:
    monkeypatch.setattr(limits, "PDF_CHILD_WALL_SECONDS", 1)
    grandchild_file = tmp_path / "grandchild"
    _fake_worker(monkeypatch, _sleeper_with_grandchild(grandchild_file))
    started = time.monotonic()
    with capture_logs() as logs:
        await _unreadable(check_pdf_isolated(make_pdf(), "slow.pdf"))
    assert time.monotonic() - started < 3.0
    assert [entry["outcome"] for entry in logs] == ["timeout"]
    grandchild = int(grandchild_file.read_text())
    assert _settle(lambda: not _running(grandchild))
    assert _proc_stat(spawned[0]) is None


def _sleeper_with_grandchild(pid_file: Path) -> str:
    """A child that starts a grandchild (same process group), records its pid, and hangs."""
    return (
        "import subprocess, sys, time\n"
        "grandchild = subprocess.Popen(\n"
        "    [sys.executable, '-I', '-c', 'import time; time.sleep(60)']\n"
        ")\n"
        f"open({str(pid_file)!r}, 'w').write(str(grandchild.pid))\n"
        "time.sleep(60)\n"
    )


async def test_memory_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    lowered = 256 * 1024 * 1024
    monkeypatch.setattr(limits, "PDF_CHILD_MEMORY_BYTES", lowered)
    # The replaced action reports success unless the child really runs under the lowered
    # limit; under it, allocating twice the limit fails.
    _worker_with_action(
        monkeypatch,
        "def action(data, args):\n"
        f"    if resource.getrlimit(resource.RLIMIT_AS) != ({lowered}, {lowered}):\n"
        "        return {'ok': True, 'page_count': 1, 'landscape': False}\n"
        f"    return {{'ok': True, 'blob': len(bytearray({2 * lowered}))}}\n",
    )
    with capture_logs() as logs:
        await _unreadable(check_pdf_isolated(make_pdf(), "big.pdf"))
    assert [entry["outcome"] for entry in logs] == ["crashed"]


async def test_worker_applies_cpu_core_and_file_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(limits, "PDF_CHILD_CPU_SECONDS", 4)
    report = tmp_path / "limits.json"
    _worker_with_action(
        monkeypatch,
        "import json\n"
        "def action(data, args):\n"
        "    names = ('RLIMIT_CPU', 'RLIMIT_AS', 'RLIMIT_CORE', 'RLIMIT_NOFILE')\n"
        "    seen = {n: resource.getrlimit(getattr(resource, n)) for n in names}\n"
        f"    open({str(report)!r}, 'w').write(json.dumps(seen))\n"
        "    return {'ok': True, 'page_count': 1, 'landscape': False}\n",
    )
    assert await check_pdf_isolated(make_pdf(), "a.pdf") == PdfInfo(page_count=1, landscape=False)
    seen = json.loads(report.read_text())
    assert seen["RLIMIT_CPU"] == [4, 5]
    assert seen["RLIMIT_AS"] == [limits.PDF_CHILD_MEMORY_BYTES] * 2
    assert seen["RLIMIT_CORE"] == [0, 0]
    assert seen["RLIMIT_NOFILE"][1] <= 64


async def test_cancelling_the_caller_kills_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawned: list[int]
) -> None:
    grandchild_file = tmp_path / "grandchild"
    _fake_worker(monkeypatch, _sleeper_with_grandchild(grandchild_file))
    task = asyncio.create_task(check_pdf_isolated(make_pdf(), "slow.pdf"))
    deadline = time.monotonic() + 5
    while not grandchild_file.exists() or not grandchild_file.read_text():
        assert time.monotonic() < deadline, "child never started"
        await asyncio.sleep(0.02)
    child = spawned[0]
    assert _running(child)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _proc_stat(child) is None
    grandchild = int(grandchild_file.read_text())
    assert _settle(lambda: not _running(grandchild))


async def test_concurrency_is_bounded(monkeypatch: pytest.MonkeyPatch, spawned: list[int]) -> None:
    _fake_worker(
        monkeypatch,
        f"import sys, time\nsys.stdin.buffer.read()\ntime.sleep(0.4)\nprint({CHECK_RESULT!r})\n",
    )
    calls = limits.PDF_CHILD_MAX_CONCURRENT + 2
    most = 0
    done = False

    async def sample() -> None:
        nonlocal most
        while not done:
            most = max(most, sum(1 for pid in spawned if _running(pid)))
            await asyncio.sleep(0.01)

    sampler = asyncio.create_task(sample())
    data = make_pdf()
    results = await asyncio.gather(*(check_pdf_isolated(data, "a.pdf") for _ in range(calls)))
    done = True
    await sampler
    # Every caller waited for a slot and none failed; never more than the limit at once.
    assert results == [PdfInfo(page_count=1, landscape=False)] * calls
    assert len(spawned) == calls
    assert most == limits.PDF_CHILD_MAX_CONCURRENT


async def test_child_environment_is_empty_of_parent_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CSA_SYNTHETIC_SECRET", "synthetic-value-not-a-real-key")
    report = tmp_path / "environ.json"
    _fake_worker(
        monkeypatch,
        "import json, os, sys\n"
        f"open({str(report)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
        f"sys.stdin.buffer.read()\nprint({CHECK_RESULT!r})\n",
    )
    await check_pdf_isolated(make_pdf(), "a.pdf")
    seen = json.loads(report.read_text())
    assert "CSA_SYNTHETIC_SECRET" not in seen
    assert "synthetic-value-not-a-real-key" not in json.dumps(seen)
    # Nothing from the parent at all: Python itself may add LC_CTYPE (C locale coercion).
    assert set(seen) <= {"LC_CTYPE"}


async def test_real_worker_runs_with_the_empty_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # The parent's environment must not be what makes the worker importable.
    for name in list(os.environ):
        monkeypatch.delenv(name)
    assert await check_pdf_isolated(make_pdf(), "a.pdf") == PdfInfo(page_count=1, landscape=False)


def _prints(payload: str) -> str:
    return f"print({payload!r})"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_prints("hello, not json"), id="not-json"),
        pytest.param("", id="no-output"),
        pytest.param("import sys\nsys.stdout.write('a' * (2 * 1024 * 1024))", id="flood"),
        pytest.param(
            "import sys, time\n"
            "sys.stdout.write('a' * (2 * 1024 * 1024))\n"
            "sys.stdout.flush()\n"
            "time.sleep(60)",
            id="flood-then-hang",
        ),
        pytest.param(
            _prints('{"ok":true,"page_count":1,"landscape":false,"extra":1}'), id="extra-key"
        ),
        pytest.param(_prints('{"ok":true,"page_count":"1","landscape":false}'), id="str-int"),
        pytest.param(_prints('{"ok":true,"page_count":true,"landscape":false}'), id="bool-int"),
        pytest.param(_prints('{"ok":true,"page_count":0,"landscape":false}'), id="zero-pages"),
        pytest.param(_prints('{"ok":true,"page_count":1}'), id="missing-key"),
        pytest.param(_prints('{"ok":false,"code":"training_made_up"}'), id="unknown-code"),
        pytest.param(_prints('{"ok":true,"text":"x","truncated":false}'), id="wrong-shape"),
        pytest.param(_prints(CHECK_RESULT * 2), id="two-objects"),
        pytest.param(f"import sys\n{_prints(CHECK_RESULT)}\nsys.exit(1)", id="result-then-exit-1"),
        pytest.param("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)", id="killed"),
    ],
)
async def test_garbage_from_the_child_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    monkeypatch.setattr(limits, "PDF_CHILD_WALL_SECONDS", 3)
    _fake_worker(monkeypatch, body)
    started = time.monotonic()
    await _unreadable(check_pdf_isolated(make_pdf(), "a.pdf"))
    assert time.monotonic() - started < 2.5  # a flood is cut off, not waited out


async def test_text_longer_than_the_cap_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_worker(monkeypatch, _prints('{"ok":true,"text":"abcdef","truncated":true}'))
    await _unreadable(extract_text_isolated(make_pdf(), 5))


async def test_failure_to_start_the_child_is_a_guard_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = str(tmp_path / "no-such-python")
    monkeypatch.setattr(pdf_guard, "_worker_command", lambda args: [missing, *args])
    with capture_logs() as logs:
        with pytest.raises(PdfGuardError):
            await check_pdf_isolated(make_pdf(), "a.pdf")
    assert [entry["outcome"] for entry in logs] == ["start_failed"]


async def test_worker_that_cannot_get_ready_is_a_guard_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The interpreter started but the worker could not import or apply its limits:
    # infrastructure, not the file's fault.
    from csa.training.pdf_worker import EXIT_NOT_READY

    _fake_worker(monkeypatch, f"import os\nos._exit({EXIT_NOT_READY})")
    with pytest.raises(PdfGuardError):
        await check_pdf_isolated(make_pdf(), "a.pdf")


async def test_nothing_sensitive_in_logs() -> None:
    name = "Rahsia Syarikat MARKER-NAME.pdf"
    text_marker = "MARKERTEXTQZX"
    data = make_pdf(2, texts=[f"{text_marker} one", f"{text_marker} two"])
    with capture_logs() as logs:
        await check_pdf_isolated(data, name)
        assert (await extract_text_isolated(data, 24_000))[0].startswith(text_marker)
        await _outcome_async(check_pdf_isolated(b"%PDF-1.7\n" + bytes(range(256)), name))
    assert [entry["outcome"] for entry in logs] == ["ok", "ok", "refused"]
    for entry in logs:
        assert set(entry) <= {"event", "log_level", "action", "outcome", "code", "elapsed_ms"}
    dumped = repr(logs)
    assert "MARKER" not in dumped
    assert "Rahsia" not in dumped
    assert "%PDF" not in dumped
