"""Fire the company's due reminders (design 2026-11-16, section 7; plan 2a, task 2, decision 6).

One transaction per reminder, each the company's own on the app pool: the oldest due row is
claimed ``FOR UPDATE SKIP LOCKED``, its kind's handler runs inside a savepoint and its notices are
delivered there, and the decision is written and committed with the claim. Two runs for one
company never fire the same row (the job's advisory lock, and ``SKIP LOCKED`` under it).

A handler or a delivery that raises rolls back only its savepoint: the row records the attempt
and the error's class and is tried again after a backoff (1, then 4 minutes); the third failure
marks it ``fired`` with the error and it never runs again. The other rows of the run go on. Two
failures that would not raise are made to: a database error the handler or a delivery caught
(the savepoint is left aborted) and a hang (each row's handler and deliveries get
``HANDLER_TIMEOUT`` seconds), so neither can keep the row at the head of the queue.

A kind with no handler in this process is not lost: an older worker during a rolling deploy, or
a process that never registered the kind, only pushes it ``UNKNOWN_KIND_RETRY`` ahead with
``last_error = 'unknown kind'`` (no attempt counted), so a worker that knows the kind fires it.

A notification's key is ``<reminder id>:<fire number>`` (a second notice in the same decision
adds ``:<index>``). A worker that dies after delivering and before committing leaves the row
scheduled with the same ``fire_count``, so the replay delivers with the same key and the
notification tables, unique on it, keep one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx
from csa.notify.people import NotifyResult, notify_people
from csa.reminders import registry as kinds
from csa.reminders.registry import Decision, HandlerContext, Notice, Reminder, ReminderRegistry

logger = structlog.get_logger(__name__)

BATCH_SIZE = 500
"""At most this many reminders per company per run."""
MAX_ATTEMPTS = 3
"""The failure that reaches this count is the last: the row is marked ``fired`` and stops."""
HANDLER_TIMEOUT = 20.0
"""Seconds one reminder's handler and deliveries may take before it counts as a failure."""
UNKNOWN_KIND = "unknown kind"
UNKNOWN_KIND_RETRY = timedelta(minutes=15)
"""How far a reminder whose kind this process does not know is pushed ahead."""
STATS = ("fired", "dropped", "failed", "dead", "unknown")

Outcome = Literal["fired", "dropped", "failed", "dead", "unknown"]
Deliver = Callable[[psycopg.AsyncConnection[Any], UUID, Notice, str], Awaitable[Any]]
"""``deliver(conn, client_id, notice, dedupe_key)``: send one notice on the claim's transaction."""


class Clock(Protocol):
    """The job clock (``csa.jobs.clock.Clock``), restated so this package does not load csa.jobs."""

    def now(self) -> datetime: ...


def backoff(attempts: int) -> timedelta:
    """How long after its ``attempts``-th failure a reminder is tried again: 1, 4, 16 minutes."""
    return timedelta(minutes=4 ** (attempts - 1))


def delivery_key(reminder_id: UUID, fire_number: int, index: int = 0) -> str:
    """The notification key of a decision's ``index``-th notice on fire ``fire_number``."""
    key = f"{reminder_id}:{fire_number}"
    return key if index == 0 else f"{key}:{index}"


async def deliver_notice(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, notice: Notice, dedupe_key: str
) -> NotifyResult:
    """The default delivery: ``notify_people`` with the notice, on the claim's transaction.

    The console rows go to the notice's recipients (a non-member is skipped). ``staff_chat``
    also queues the staff chat as ``staff_event`` (the notice's kind when it names none), with the
    notice's ``params`` as the chat payload: ids and display names only, never message text.
    """
    staff_event = (notice.staff_event or notice.kind) if notice.staff_chat else None
    return await notify_people(
        conn,
        client_id,
        recipients=notice.recipients,
        kind=notice.kind,
        priority=notice.priority,
        message_key=notice.message_key,
        params=notice.params,
        link=notice.link,
        dedupe_key=dedupe_key,
        staff_event=staff_event,
        staff_payload=dict(notice.params) if staff_event is not None else None,
    )


class SwallowedDatabaseError(Exception):
    """A handler or a delivery caught a database error and went on: its savepoint is aborted."""


def _raise_if_aborted(conn: psycopg.AsyncConnection[Any]) -> None:
    # Raising sends the savepoint down its ROLLBACK TO path; leaving it would RELEASE an aborted
    # savepoint, which fails, and then the failure could not be recorded on the row either.
    if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
        raise SwallowedDatabaseError


async def fire_due(
    app_pool: AsyncConnectionPool,
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = BATCH_SIZE,
    handlers: ReminderRegistry | None = None,
    deliver: Deliver | None = None,
) -> dict[str, int]:
    """Fire up to ``batch_size`` of the company's due reminders, oldest first, and count them.

    ``fired``: the handler acted (delivered, or asked to fire again). ``dropped``: an empty
    decision. ``failed``: raised (or caught a database error, or hung), will retry. ``dead``:
    failed for the last time. ``unknown``: no handler for the kind here, pushed ahead.
    ``handlers`` defaults to the process registry and ``deliver`` to :func:`deliver_notice`
    (``notify_people``).
    """
    stats: dict[str, int] = dict.fromkeys(STATS, 0)
    now = clock.now()
    table = kinds.registry if handlers is None else handlers
    send = deliver_notice if deliver is None else deliver
    seen: list[UUID] = []  # a row asked to fire again by now is left to the next run
    for _ in range(batch_size):
        async with tx(app_pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "reminders/claim_due.sql",
                {"client_id": client_id, "now": now, "seen": seen},
                conn=conn,
            )
            if row is None:
                break
            seen.append(row["id"])
            outcome = await _fire_one(conn, client_id, row, now, table, send)
        stats[outcome] += 1
    return stats


async def _fire_one(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    row: dict[str, Any],
    now: datetime,
    table: ReminderRegistry,
    send: Deliver,
) -> Outcome:
    reminder = Reminder(
        id=row["id"],
        kind=row["kind"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        due_at=row["due_at"],
        fire_count=row["fire_count"],
        payload=row["payload"],
    )
    ids = {"client_id": client_id, "id": reminder.id, "now": now}
    handler = table.get(reminder.kind)
    if handler is None:
        await db.q.execute(
            "reminders/defer.sql",
            {**ids, "due_at": now + UNKNOWN_KIND_RETRY, "last_error": UNKNOWN_KIND},
            conn=conn,
        )
        logger.warning(
            "reminder_unknown_kind",
            client_id=str(client_id),
            reminder_id=str(reminder.id),
            kind=reminder.kind,
        )
        return "unknown"
    fire_number = reminder.fire_count + 1
    try:
        # TimeoutError is an Exception: a hang is a failure of this row, not of the whole run.
        async with asyncio.timeout(HANDLER_TIMEOUT):
            async with conn.transaction():  # a savepoint: a failure undoes the handler's writes
                decision: Decision = await handler(HandlerContext(conn, client_id, reminder, now))
                _raise_if_aborted(conn)
                for index, notice in enumerate(decision.notices):
                    key = delivery_key(reminder.id, fire_number, index)
                    await send(conn, client_id, notice, key)
                    _raise_if_aborted(conn)
    except Exception as exc:  # one bad reminder must not stop the others
        attempts = int(row["attempts"]) + 1
        dead = attempts >= MAX_ATTEMPTS
        await db.q.execute(
            "reminders/fail.sql",
            {
                **ids,
                "state": "fired" if dead else "scheduled",
                "due_at": reminder.due_at if dead else now + backoff(attempts),
                "attempts": attempts,
                # The class only: a message can carry a name or a customer's words.
                "last_error": type(exc).__name__,
            },
            conn=conn,
        )
        logger.error(
            "reminder_failed",
            client_id=str(client_id),
            reminder_id=str(reminder.id),
            kind=reminder.kind,
            attempts=attempts,
            dead=dead,
            error_class=type(exc).__name__,
        )
        return "dead" if dead else "failed"
    if not decision.notices and decision.next_due_at is None:
        await db.q.execute(
            "reminders/finish.sql",
            {**ids, "state": "done", "due_at": None, "fired": 0, "last_error": None},
            conn=conn,
        )
        return "dropped"
    await db.q.execute(
        "reminders/finish.sql",
        {
            **ids,
            "state": "done" if decision.next_due_at is None else "scheduled",
            "due_at": decision.next_due_at,
            "fired": 1,
            "last_error": None,
        },
        conn=conn,
    )
    return "fired"
