"""PDF checks and text extraction in a child process with hard limits.

``check_pdf_isolated`` and ``extract_text_isolated`` mean what ``pdf.check_pdf`` and
``pdf.extract_text`` mean, but the parse runs in a short-lived child (``pdf_worker``): the
in-process budgets cannot cover every path through pypdf, and the gateway's event loop and
thread pool are shared by every tenant. The child gets a CPU limit and an address-space
limit (set by the worker itself before it imports pypdf or reads the file), a wall-clock
limit enforced here by killing its whole process group, an empty environment (the
gateway's holds database and API keys), no stderr, and at most 1 MiB of stdout, which must
be exactly one result in the worker's JSON shape. At most ``PDF_CHILD_MAX_CONCURRENT``
children run at once per process; further callers wait for a slot.

A child that is killed, runs out of time or memory, crashes or answers with anything else
means the file could not be checked in the allowed time: ``PdfRefused("training_pdf_unreadable")``.
A child that cannot be started, or a worker that cannot get ready, is ``PdfGuardError``:
infrastructure, not the file. Logs carry the action, the outcome category, the refusal code
and the elapsed time only: never the file name, its bytes or its text.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
import weakref
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

import csa.training
from csa.training import limits
from csa.training.limits import MAX_PAGES
from csa.training.pdf import PdfInfo, PdfRefused, check_file_name
from csa.training.pdf_worker import EXIT_NOT_READY

logger = structlog.get_logger(__name__)

UNREADABLE = "training_pdf_unreadable"
MAX_CHILD_OUTPUT_BYTES = 1024 * 1024

# The directory holding the ``csa`` package. ``-I`` makes the child ignore PYTHONPATH and
# leave the working directory off sys.path, and the image does not install the project
# (uv sync --no-install-project; it runs from /app), so the bootstrap puts this directory on
# sys.path itself. The interpreter finds the virtualenv's site-packages on its own.
_PACKAGE_ROOT = str(Path(csa.training.__file__).resolve().parents[2])
_BOOTSTRAP = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv.pop(1))\n"
    "try:\n"
    "    from csa.training.pdf_worker import main\n"
    "except BaseException:\n"
    f"    os._exit({EXIT_NOT_READY})\n"
    "sys.exit(main(sys.argv[1:]))\n"
)
# Nothing from the parent: Python needs no variable to start from an absolute path.
_CHILD_ENV: dict[str, str] = {}

_RefusalCode = Literal[
    "training_name_invalid",
    "training_not_pdf",
    "training_pdf_damaged",
    "training_pdf_protected",
    "training_pdf_too_many_pages",
    "training_pdf_active_content",
]


class PdfGuardError(Exception):
    """The PDF checker could not run: infrastructure, not the file's fault."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Checked(_Strict):
    ok: Literal[True]
    page_count: int = Field(ge=1, le=MAX_PAGES)
    landscape: bool


class _Extracted(_Strict):
    ok: Literal[True]
    text: str
    truncated: bool


class _Refused(_Strict):
    ok: Literal[False]
    code: _RefusalCode


_CHECK_OUTPUT: TypeAdapter[_Checked | _Refused] = TypeAdapter(_Checked | _Refused)
_EXTRACT_OUTPUT: TypeAdapter[_Extracted | _Refused] = TypeAdapter(_Extracted | _Refused)


class _Unreadable(Exception):
    """The child did not give a result; ``outcome`` is the log category."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


class _NotStarted(Exception):
    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


async def check_pdf_isolated(data: bytes, file_name: str) -> PdfInfo:
    """``pdf.check_pdf`` in a limited child; the name is checked here, so it never leaves."""
    check_file_name(file_name)
    result = await _run("check", [], data, _CHECK_OUTPUT.validate_json)
    return PdfInfo(page_count=result.page_count, landscape=result.landscape)


async def extract_text_isolated(data: bytes, max_chars: int) -> tuple[str, bool]:
    """``pdf.extract_text`` in a limited child."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")

    def parse(output: bytes) -> _Extracted | _Refused:
        result = _EXTRACT_OUTPUT.validate_json(output)
        if isinstance(result, _Extracted) and len(result.text) > max_chars:
            raise ValueError("more text than asked for")
        return result

    result = await _run("extract", [f"--max-chars={max_chars}"], data, parse)
    return result.text, result.truncated


async def _run[Result: _Strict](
    action: str,
    options: list[str],
    data: bytes,
    parse: Callable[[bytes], Result | _Refused],
) -> Result:
    argv = _worker_command(
        [
            action,
            *options,
            f"--cpu-seconds={limits.PDF_CHILD_CPU_SECONDS}",
            f"--memory-bytes={limits.PDF_CHILD_MEMORY_BYTES}",
        ]
    )
    async with _slots():
        started = time.monotonic()
        outcome, code = "error", None
        try:
            output = await _run_child(argv, data)
            try:
                result = parse(output)
            except ValueError:  # pydantic's ValidationError included
                raise _Unreadable("bad_output") from None
            if isinstance(result, _Refused):
                outcome, code = "refused", result.code
                raise PdfRefused(result.code)
            outcome = "ok"
            return result
        except _Unreadable as unreadable:
            outcome = unreadable.outcome
            raise PdfRefused(UNREADABLE) from None
        except _NotStarted as failure:
            outcome = failure.outcome
            raise PdfGuardError("the PDF checker could not run") from failure.__cause__
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            fields: dict[str, object] = {"action": action, "outcome": outcome}
            if code is not None:
                fields["code"] = code
            elapsed_ms = round((time.monotonic() - started) * 1000)
            logger.info("training_pdf_child", **fields, elapsed_ms=elapsed_ms)


async def _run_child(argv: list[str], data: bytes) -> bytes:
    """The child's stdout, once it has exited 0 within the wall-clock limit."""
    try:
        process = await _spawn(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _NotStarted("start_failed") from exc
    try:
        return await asyncio.wait_for(_exchange(process, data), limits.PDF_CHILD_WALL_SECONDS)
    except TimeoutError:
        raise _Unreadable("timeout") from None
    finally:
        await _stop(process)


async def _spawn(argv: list[str]) -> Process:
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=PIPE,
        stdout=PIPE,
        stderr=DEVNULL,
        env=dict(_CHILD_ENV),
        cwd="/",
        close_fds=True,
        start_new_session=True,  # its own process group, so a kill takes any grandchild too
    )


def _worker_command(args: list[str]) -> list[str]:
    return [sys.executable, "-I", "-B", "-c", _BOOTSTRAP, _PACKAGE_ROOT, *args]


async def _exchange(process: Process, data: bytes) -> bytes:
    stdin, stdout = process.stdin, process.stdout
    assert stdin is not None and stdout is not None
    # Feed and read at once: a child that writes before it reads cannot deadlock us.
    feeder = asyncio.ensure_future(_feed(stdin, data))
    reader = asyncio.ensure_future(_read_capped(stdout))
    try:
        output = await reader
    finally:
        feeder.cancel()
        reader.cancel()
        await asyncio.gather(feeder, reader, return_exceptions=True)
    returncode = await process.wait()
    if returncode == EXIT_NOT_READY:
        raise _NotStarted("not_ready")
    if returncode < 0:
        raise _Unreadable("killed")  # a signal: SIGXCPU or SIGKILL at the CPU limit
    if returncode != 0:
        raise _Unreadable("crashed")
    return output


async def _feed(stdin: asyncio.StreamWriter, data: bytes) -> None:
    try:
        stdin.write(data)
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass  # the child stopped reading; its exit status tells the rest
    finally:
        stdin.close()


async def _read_capped(stdout: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await stdout.read(64 * 1024):
        size += len(chunk)
        if size > MAX_CHILD_OUTPUT_BYTES:
            raise _Unreadable("flood")
        chunks.append(chunk)
    return b"".join(chunks)


async def _stop(process: Process) -> None:
    """Kill the child's process group unless it has been reaped already, then reap it.

    Once reaped its pid may be reused, so the group is only signalled before that.
    ``Process.wait`` also waits for the pipes to close, and a stdout pipe asyncio stopped
    reading (its buffer full, after a flood or an abandoned read) never reaches end-of-file
    by itself: drain it. Every writer was in the killed group, so what is left is at most
    the pipe's capacity.
    """
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.stdout is not None:
        with contextlib.suppress(OSError):
            while await process.stdout.read(64 * 1024):
                pass
    await process.wait()


_slots_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _slots() -> asyncio.Semaphore:
    """This process's child slots (one semaphore per event loop; the gateway runs one)."""
    loop = asyncio.get_running_loop()
    slots = _slots_by_loop.get(loop)
    if slots is None:
        slots = _slots_by_loop[loop] = asyncio.Semaphore(limits.PDF_CHILD_MAX_CONCURRENT)
    return slots
