"""Child process that parses one PDF under hard limits; ``pdf_guard`` runs it.

    python -m csa.training.pdf_worker check|extract [--name NAME] [--max-chars N]

It reads the PDF from stdin and writes one JSON object to stdout:
``{"ok":true,"page_count":..,"landscape":..}`` (check), ``{"ok":true,"text":..,"truncated":..}``
(extract) or ``{"ok":false,"code":"training_..."}`` (a refusal). Before it imports pypdf or
reads the file it lowers its own limits: CPU seconds (the kernel sends SIGXCPU at the soft
limit and SIGKILL a second later), address space, no core files, few open descriptors.

Nothing reaches stderr: fds 1 and 2 point at /dev/null before anything else runs, so
neither a traceback nor pypdf's warnings (which quote the file's objects) can carry the
file's content anywhere, and only the result is written, to a private copy of stdout.

Exit codes: 0 for any result, accepted or refused; ``EXIT_NOT_READY`` when the arguments,
the limits or the imports failed before the file was read (infrastructure, not the file);
``EXIT_OUT_OF_MEMORY`` when the parse hit the memory limit; ``EXIT_FAILED`` for any other
error in the parse.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import resource
import sys
from collections.abc import Callable
from typing import Any

from csa.training import limits

EXIT_NOT_READY = 70
EXIT_FAILED = 2
EXIT_OUT_OF_MEMORY = 3

MAX_OPEN_FILES = 32
"""Imports need a handful of descriptors at once; parsing needs none."""

DEFAULT_NAME = "upload.pdf"
"""The name ``check`` passes to ``check_pdf`` when none is given. The guard checks the real
name in process (``pdf.check_file_name``), so it never has to appear in the child's argv."""

Result = dict[str, Any]


def _check(data: bytes, args: argparse.Namespace) -> Result:
    from csa.training.pdf import check_pdf

    info = check_pdf(data, args.name)
    return {"ok": True, "page_count": info.page_count, "landscape": info.landscape}


def _extract(data: bytes, args: argparse.Namespace) -> Result:
    from csa.training.pdf import extract_text

    text, truncated = extract_text(data, args.max_chars)
    return {"ok": True, "text": text, "truncated": truncated}


_ACTIONS: dict[str, Callable[[bytes, argparse.Namespace], Result]] = {
    "check": _check,
    "extract": _extract,
}


def main(argv: list[str]) -> int:
    result_fd = _silence_standard_streams()
    try:
        args = _parser().parse_args(argv)
        apply_limits(args.cpu_seconds, args.memory_bytes)
        logging.disable(logging.CRITICAL)
        from csa.training.pdf import PdfRefused
    except BaseException:  # argparse exits; a limit or an import failed
        return EXIT_NOT_READY
    try:
        data = sys.stdin.buffer.read()
        try:
            result = _ACTIONS[args.action](data, args)
        except PdfRefused as refused:
            result = {"ok": False, "code": refused.code}
        payload = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
        _write_all(result_fd, payload.encode("ascii") + b"\n")
    except MemoryError:
        return EXIT_OUT_OF_MEMORY
    except BaseException:
        return EXIT_FAILED
    return 0


def apply_limits(cpu_seconds: int, memory_bytes: int) -> None:
    """Lower this process's limits to the values given, never above those in ``limits``."""
    cpu = max(1, min(cpu_seconds, limits.PDF_CHILD_CPU_SECONDS))
    memory = max(1, min(memory_bytes, limits.PDF_CHILD_MEMORY_BYTES))
    _lower(resource.RLIMIT_CORE, 0, 0)
    _lower(resource.RLIMIT_NOFILE, MAX_OPEN_FILES, MAX_OPEN_FILES)
    _lower(resource.RLIMIT_AS, memory, memory)
    _lower(resource.RLIMIT_CPU, cpu, cpu + 1)


def _lower(which: int, soft: int, hard: int) -> None:
    _, current_hard = resource.getrlimit(which)
    if current_hard != resource.RLIM_INFINITY:
        hard = min(hard, current_hard)
    resource.setrlimit(which, (min(soft, hard), hard))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m csa.training.pdf_worker")
    parser.add_argument("action", choices=sorted(_ACTIONS))
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--max-chars", type=int, default=limits.SUMMARY_MAX_CHARS)
    parser.add_argument("--cpu-seconds", type=int, default=limits.PDF_CHILD_CPU_SECONDS)
    parser.add_argument("--memory-bytes", type=int, default=limits.PDF_CHILD_MEMORY_BYTES)
    return parser


def _silence_standard_streams() -> int:
    """Point fds 1 and 2 at /dev/null; return a private copy of the real stdout."""
    result_fd = os.dup(1)
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 1)
    os.dup2(null, 2)
    os.close(null)
    return result_fd


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
