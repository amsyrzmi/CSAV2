"""The metering context: whose work a model call is, and which job it belongs to.

An entry point (a customer turn, an assistant request, an email job) binds a ``MeterContext``
with ``meter_context`` before any model call. The provider clients read it through the meter, so
every call is booked to a company without the call sites passing identifiers around. The
binding is a ``ContextVar``: it follows the running task, nests, and is restored on exit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

__all__ = [
    "JOB_AGENT",
    "JOB_LABELS",
    "Job",
    "MeterContext",
    "MeteringContextMissing",
    "current_context",
    "meter_context",
]

# The closed set of jobs that make model calls. The same list is the CHECK in migration 0570.
Job = Literal[
    "customer_turn",
    "workspace_assistant",
    "hr_assistant",
    "email_analysis",
    "email_reply",
    "email_compose",
    "training_summary",
]

JOB_LABELS: dict[Job, str] = {
    "customer_turn": "Customer reply",
    "workspace_assistant": "Assistant request",
    "hr_assistant": "HR assistant",
    "email_analysis": "Email summary",
    "email_reply": "Email reply draft",
    "email_compose": "Email compose",
    "training_summary": "Training summary",
}

# The agent each job's cost is shown under.
JOB_AGENT: dict[Job, str] = {
    "customer_turn": "support",
    "workspace_assistant": "assistant",
    "hr_assistant": "hr",
    "email_analysis": "email",
    "email_reply": "email",
    "email_compose": "email",
    "training_summary": "training",
}


class MeteringContextMissing(RuntimeError):
    """A model call was about to run with no company bound to it."""


@dataclass(frozen=True)
class MeterContext:
    """One piece of work: the company, the trace it belongs to and the job kind."""

    client_id: UUID
    trace_id: UUID
    job: Job
    conversation_id: UUID | None = None


_CURRENT: ContextVar[MeterContext | None] = ContextVar("csa_meter_context", default=None)


@contextmanager
def meter_context(ctx: MeterContext) -> Iterator[None]:
    """Bind ``ctx`` for the body; the previous binding (or none) is restored on exit."""
    token = _CURRENT.set(ctx)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_context() -> MeterContext:
    """The bound context, or ``MeteringContextMissing`` when no entry point bound one."""
    ctx = _CURRENT.get()
    if ctx is None:
        raise MeteringContextMissing("no metering context is bound to this call")
    return ctx
