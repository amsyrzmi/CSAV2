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
from typing import Any

import orjson
import structlog
from structlog.contextvars import merge_contextvars

_CONTEXT_KEYS = ("trace_id", "client_id", "conversation_id", "channel")


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


def configure_logging(env: str = "prod") -> None:
    """Configure structlog + stdlib logging for the process.

    ``env='dev'`` uses the human-readable console renderer; every other value
    emits JSON. Idempotent — safe to call once at process start.
    """
    shared_processors: list[structlog.typing.Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor
    if env == "dev":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer(serializer=_orjson_dumps)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, psycopg, ...) through structlog's config.
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
