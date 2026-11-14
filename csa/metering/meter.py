"""The usage meter: records one ledger row per model call, and never breaks the call.

Provider clients call ``require_context`` before any request (so an unbound call fails fast,
before a paid HTTP call) and ``record`` after a response. ``LedgerMeter.record`` writes in its own
short transaction, each attempt bounded by ``WRITE_ATTEMPT_TIMEOUT_S``, retries once, and on a
second failure logs the loss and returns: a ledger write failing (or stalling on a busy pool) must
not fail or stall the customer's turn. The error log carries what is needed to rebuild the row
(identifiers and counts), never prompt or reply text and never the exception message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx
from csa.metering.context import MeterContext, current_context

__all__ = [
    "LEDGER_PROVIDERS",
    "WRITE_ATTEMPT_TIMEOUT_S",
    "GenerationMirror",
    "LedgerMeter",
    "NullMeter",
    "Usage",
    "UsageMeter",
]

logger = structlog.get_logger(__name__)

_WRITE_ATTEMPTS = 2

# The bound on one write attempt, pool wait included. The pool's own wait (30 s) would otherwise
# let two attempts hold a user turn for about a minute, inside the caller's own deadline.
WRITE_ATTEMPT_TIMEOUT_S: float = 2.0


# The providers the clients record in the ledger (``Usage.provider``: csa/decide/groq.py and
# csa/decide/gemini.py). A model price may only be added for one of these.
LEDGER_PROVIDERS: frozenset[str] = frozenset({"groq", "gemini"})


@dataclass(frozen=True)
class Usage:
    """What one model call used, as the provider reported it."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    schema_name: str


class GenerationMirror(Protocol):
    """A second sink for each call (the trace store). Non-blocking, and never raises."""

    def submit(self, ctx: MeterContext, usage: Usage) -> None:
        """Hand the call to the mirror."""
        ...


class UsageMeter(Protocol):
    """What a provider client needs from a meter."""

    def require_context(self) -> None:
        """Raise ``MeteringContextMissing`` unless a context is bound."""
        ...

    async def record(self, usage: Usage) -> None:
        """Book one call to the bound context."""
        ...


class NullMeter:
    """A meter that records nothing. For evals only, never a runtime process."""

    def require_context(self) -> None:
        """Nothing to require."""

    async def record(self, usage: Usage) -> None:
        """Nothing to record."""


class LedgerMeter:
    """Writes each call to ``ai_usage_events`` and offers it to the optional mirror."""

    def __init__(self, pool: AsyncConnectionPool, mirror: GenerationMirror | None = None) -> None:
        """Write through ``pool`` (the app role); ``mirror`` is optional."""
        self._pool = pool
        self._mirror = mirror

    def require_context(self) -> None:
        """Raise ``MeteringContextMissing`` unless a context is bound."""
        current_context()

    async def record(self, usage: Usage) -> None:
        """Insert one row; a write failure is logged and swallowed, a missing context raises."""
        ctx = current_context()
        await self._write(ctx, usage)
        self._mirror_submit(ctx, usage)

    async def _write(self, ctx: MeterContext, usage: Usage) -> None:
        params = {
            "client_id": ctx.client_id,
            "trace_id": ctx.trace_id,
            "conversation_id": ctx.conversation_id,
            "job": ctx.job,
            "provider": usage.provider,
            "model": usage.model,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "schema_name": usage.schema_name,
        }
        last_error: Exception | None = None
        for _ in range(_WRITE_ATTEMPTS):
            try:
                # Its own transaction on its own connection: the caller's transaction (if any)
                # rolling back must not take the ledger row with it. The attempt's own timeout
                # raises TimeoutError here; a caller's outer deadline cancels this task instead
                # (CancelledError, not caught), so the caller's timeout still fires.
                async with asyncio.timeout(WRITE_ATTEMPT_TIMEOUT_S):
                    async with tx(self._pool, str(ctx.client_id)) as conn:
                        await db.q.execute("billing/usage_insert.sql", params, conn=conn)
                return
            except (psycopg.Error, TimeoutError) as exc:
                last_error = exc
        logger.error(
            "ai_usage_write_failed",
            client_id=str(ctx.client_id),
            trace_id=str(ctx.trace_id),
            conversation_id=None if ctx.conversation_id is None else str(ctx.conversation_id),
            job=ctx.job,
            provider=usage.provider,
            model=usage.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            schema_name=usage.schema_name,
            error_type=type(last_error).__name__,
        )

    def _mirror_submit(self, ctx: MeterContext, usage: Usage) -> None:
        if self._mirror is None:
            return
        try:
            self._mirror.submit(ctx, usage)
        except Exception as exc:  # the mirror is best-effort; it must never fail a call
            logger.warning(
                "ai_usage_mirror_failed",
                client_id=str(ctx.client_id),
                trace_id=str(ctx.trace_id),
                error_type=type(exc).__name__,
            )
