"""Structured JSON logging via structlog (LG01 §8.1).

JSON output through an orjson renderer, a ``contextvars`` processor that injects
``trace_id`` / ``client_id`` / ``conversation_id`` / ``channel`` when bound
(absent keys are omitted, not null-spammed), and stdlib ``logging`` routed
through structlog so uvicorn/psycopg logs share the format. ``ENV=dev`` may use
the console renderer for readability; ``ci``/``prod`` always emit JSON.

Secrets are never logged (see §3, §6).
"""  # LG01

from __future__ import annotations

import logging
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from io import TextIOBase
from threading import Condition, Thread
from typing import Any, TextIO

import orjson
import structlog
from structlog.contextvars import merge_contextvars

_CONTEXT_KEYS = ("trace_id", "client_id", "conversation_id", "channel")
_OUTPUT_CAPACITY = 1024
_OUTPUT_STALL_SECONDS = 5.0
_output: LogOutput | None = None


@dataclass(frozen=True)
class _Entry:
    stream: TextIO
    text: str
    created: float


class LogOutput:
    """Bounded output with nonblocking producers and observable sink failures."""

    def __init__(self, capacity: int = _OUTPUT_CAPACITY) -> None:
        self._capacity = capacity
        self._pending: deque[_Entry] = deque()
        self._condition = Condition()
        self._active: _Entry | None = None
        self._failure: str | None = None
        self._dropped = 0
        self._closing = False
        self._thread = Thread(target=self._run, name="csa-log-output", daemon=True)
        self._thread.start()

    def write(self, stream: TextIO, text: str) -> None:
        with self._condition:
            if self._closing or len(self._pending) >= self._capacity:
                self._failure = "closed" if self._closing else "queue_full"
                self._dropped += 1
                return
            self._pending.append(_Entry(stream, text, time.monotonic()))
            self._condition.notify()

    def health(self) -> tuple[bool, str]:
        with self._condition:
            oldest = self._active or (self._pending[0] if self._pending else None)
            reason = self._failure
            if (
                reason is None
                and oldest
                and time.monotonic() - oldest.created > _OUTPUT_STALL_SECONDS
            ):
                reason = "sink_stalled"
            if reason:
                return False, f"logging unavailable: {reason}; dropped_events={self._dropped}"
            return True, "ok"

    def flush(self, timeout: float = 1.0) -> None:
        with self._condition:
            if not self._condition.wait_for(
                lambda: not self._pending and self._active is None, timeout=timeout
            ):
                raise RuntimeError("Logging output did not drain before the deadline")
            if self._failure:
                raise RuntimeError(self.health()[1])

    def close(self, timeout: float = 1.0) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify()
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise RuntimeError("Logging output shutdown timed out")
        if self._failure:
            raise RuntimeError(self.health()[1])

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: bool(self._pending) or self._closing)
                if not self._pending:
                    return
                self._active = entry = self._pending.popleft()
            try:
                entry.stream.write(entry.text)
                entry.stream.flush()
            except Exception as exc:
                # Do not try to report a failed sink through the same failed sink.
                with self._condition:
                    self._failure = f"sink_error:{type(exc).__name__}"
                    self._dropped += 1
            finally:
                with self._condition:
                    self._active = None
                    self._condition.notify_all()


class _QueuedStream(TextIOBase):
    def __init__(self, output: LogOutput, stream: TextIO) -> None:
        self._output = output
        self._stream = stream
        self._parts: list[str] = []

    def write(self, text: str) -> int:
        self._parts.append(text)
        return len(text)

    def flush(self) -> None:
        if self._parts:
            text = "".join(self._parts)
            self._parts.clear()
            self._output.write(self._stream, text)


class _QueuedLogger:
    def __init__(self, output: LogOutput, stream: TextIO) -> None:
        self._output = output
        self._stream = stream

    def msg(self, message: str) -> None:
        self._output.write(self._stream, message + "\n")

    log = debug = info = warn = warning = error = exception = fatal = critical = msg


def logging_health() -> tuple[bool, str]:
    return _output.health() if _output is not None else (True, "ok")


def flush_logging(timeout: float = 1.0) -> None:
    if _output is not None:
        _output.flush(timeout)


def shutdown_logging() -> None:
    global _output
    output, _output = _output, None
    if output is not None:
        output.close()


def _orjson_dumps(obj: Any, default: Any = None, **_: Any) -> str:
    """Serialize a structlog event dict to JSON using orjson."""
    return orjson.dumps(obj, default=default).decode("utf-8")


def bind_context(**kwargs: Any) -> None:
    """Bind context values (trace_id/client_id/conversation_id/channel).

    Only keys in :data:`_CONTEXT_KEYS` with a non-``None`` value are bound so
    absent context is omitted from log lines rather than rendered as ``null``.
    """
    to_bind = {k: v for k, v in kwargs.items() if k in _CONTEXT_KEYS and v is not None}
    if to_bind:
        structlog.contextvars.bind_contextvars(**to_bind)


def clear_context() -> None:
    """Clear all bound context for the current execution context."""
    structlog.contextvars.clear_contextvars()


def _redact_telegram_urls(
    _logger: object, _method: str, event: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    # Telegram embeds bot credentials in both API and download URLs, including HTTP logs.
    for key, value in event.items():
        if isinstance(value, str):
            event[key] = re.sub(
                r"(https?://api\.telegram\.org/(?:file/)?bot)[^/\s\"']+", r"\1[REDACTED]", value
            )
    return event


class _DropConsoleQuery(logging.Filter):
    """Strip the query string from uvicorn access lines for `/console/` paths.

    Console list filters carry personal data (the inbox search text), and uvicorn's access
    log records the full request target. Other paths keep theirs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            target = args[2]
            if target.startswith("/console/") and "?" in target:
                record.args = (*args[:2], target.split("?", 1)[0], *args[3:])
        return True


def configure_logging(env: str = "prod") -> None:
    """Configure structlog + stdlib logging for the process.

    ``env='dev'`` uses the human-readable console renderer; every other value
    emits JSON. Idempotent — safe to call once at process start.
    """
    global _output
    shutdown_logging()
    output = _output = LogOutput()
    stdout = sys.stdout
    shared_processors: list[structlog.typing.Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_telegram_urls,
    ]

    renderer: structlog.typing.Processor
    if env == "dev":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer(serializer=_orjson_dumps)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=lambda *_: _QueuedLogger(output, stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, psycopg, ...) through structlog's config.
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(_QueuedStream(_output, sys.stderr))
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _DropConsoleQuery) for item in access.filters):
        access.addFilter(_DropConsoleQuery())


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
