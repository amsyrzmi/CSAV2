"""What a reminder handler gets and gives back, and the handlers by kind (plan 2a, task 2).

A kind is the name of the handler that fires it (``inbox.overdue``). The fire job claims a due
reminder, finds its kind's handler here and calls it with a :class:`HandlerContext`; the handler
re-checks the subject on the company's transaction and answers with a :class:`Decision`. This
module knows nothing about what a subject is; the packages that own a subject register its kinds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg

Priority = Literal["low", "normal", "high", "urgent"]


@dataclass(frozen=True)
class Notice:
    """One notification to send: the same message to every recipient (ids of console users).

    ``message_key`` and ``params`` are rendered where the notice is shown (the browser for the
    console row, the server for the staff chat), so a notice never carries finished text.
    ``staff_chat`` also sends it to the company staff chat as ``staff_event``.
    """

    recipients: tuple[UUID, ...]
    kind: str
    priority: Priority
    message_key: str
    params: dict[str, Any]
    link: dict[str, Any]
    staff_chat: bool = False
    staff_event: str | None = None


@dataclass(frozen=True)
class Decision:
    """What a handler wants done with the reminder it fired.

    ``notices`` are delivered now. ``next_due_at`` set means fire again then (the row stays
    scheduled and its ``fire_count`` goes up); ``None`` means the reminder is finished (``done``).
    ``next_due_at`` with no notices is a wait: the row moves and ``fire_count`` stays, so the next
    notice is still the fire it would have been. An empty decision, no notices and no
    ``next_due_at``, drops the reminder silently: its subject moved on.
    """

    notices: tuple[Notice, ...] = ()
    next_due_at: datetime | None = None


@dataclass(frozen=True)
class Reminder:
    """The claimed reminder row, as a handler sees it."""

    id: UUID
    kind: str
    subject_type: str
    subject_id: str
    due_at: datetime
    fire_count: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class HandlerContext:
    """A handler's inputs: the company's open transaction, the reminder and the job's time."""

    conn: psycopg.AsyncConnection[Any]
    client_id: UUID
    reminder: Reminder
    now: datetime


ReminderHandler = Callable[[HandlerContext], Awaitable[Decision]]


class ReminderRegistry:
    """Handlers by kind. A kind has one handler: registering it twice is a mistake."""

    def __init__(self) -> None:
        self._handlers: dict[str, ReminderHandler] = {}

    def register(self, kind: str, handler: ReminderHandler) -> None:
        if kind in self._handlers:
            raise ValueError(f"reminder kind already registered: {kind}")
        self._handlers[kind] = handler

    def get(self, kind: str) -> ReminderHandler | None:
        return self._handlers.get(kind)

    def kinds(self) -> list[str]:
        return sorted(self._handlers)


registry = ReminderRegistry()
"""The process's handlers; the fire job uses it unless it is given another."""


def register_kind(kind: str, handler: ReminderHandler) -> None:
    """Register ``handler`` for ``kind`` in the process registry; a duplicate raises ValueError."""
    registry.register(kind, handler)
