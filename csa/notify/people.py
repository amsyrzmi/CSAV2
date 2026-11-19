"""Notify people: one console notification per person, and the staff chat when asked (plan 2a,
task 3; design 2026-11-16, sections 7 and 8).

``notify_people`` takes recipients already chosen by the caller (an Inbox reminder handler, later
HR) and writes one ``console_notifications`` row for each who is an enabled member of the company;
anyone else is skipped silently. With ``staff_event`` set it also queues the event for the
company's staff chat through :func:`csa.notify.notifier.notify`. Everything runs on the caller's
transaction and is keyed: a replay with the same ``dedupe_key`` writes nothing, in either place.

The recipient lists (:func:`team_user_ids`, :func:`admin_user_ids`) come from migration 0624's
SECURITY DEFINER functions, because the app role cannot read the console accounts.

This module imports :mod:`csa.notify.notifier` and :mod:`csa.core` only (``pyproject.toml``
contract), so ``csa.reminders`` can use it without reaching the console, the Inbox or the sender.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from csa.core import db
from csa.notify import notifier


@dataclass(frozen=True)
class NotifyResult:
    """Rows newly written: console notifications, staff-chat notifications (one per target), and
    person emails queued in the outbox."""

    console_rows: int
    chat_rows: int
    email_rows: int = 0


async def team_user_ids(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> list[UUID]:
    """Everyone who may use the company's Inbox: enabled tenant admins and staff, by user id.

    Never a read-only member, a disabled membership or account, or a platform admin. The
    connection's ``app.client_id`` must be ``client_id`` (the function refuses another company).
    """
    rows = await db.q.fetch_all("notify/team_user_ids.sql", {"client_id": client_id}, conn=conn)
    return [row["user_id"] for row in rows]


async def admin_user_ids(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> list[UUID]:
    """The company's enabled tenant admins, by user id (same rules as :func:`team_user_ids`)."""
    rows = await db.q.fetch_all("notify/admin_user_ids.sql", {"client_id": client_id}, conn=conn)
    return [row["user_id"] for row in rows]


async def notify_people(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    *,
    recipients: Sequence[UUID],
    kind: str,
    priority: str,
    message_key: str,
    params: dict[str, Any],
    link: dict[str, Any],
    dedupe_key: str,
    staff_event: str | None = None,
    staff_payload: dict[str, object] | None = None,
) -> NotifyResult:
    """Write one console notification per recipient, and the staff chat when ``staff_event`` is set.

    A recipient who is not an enabled member of the company (left, disabled, another company's,
    a platform admin, unknown) gets no row and raises nothing; the others are written. The chat
    goes to every enabled target subscribed to ``staff_event`` at ``priority`` or above, whatever
    the recipients, with ``staff_payload`` (ids and display names only, never message text).
    A person who saved email on, whose kind and priority floor match, also gets one row in the
    email outbox, in the same transaction (``email_rows``).
    Raises ``ValueError`` for an unknown priority before writing anything.
    """
    if priority not in notifier.PRIORITIES:
        raise ValueError(f"unknown priority {priority!r}")
    console_rows = 0
    email_rows = 0
    if recipients:
        written = await db.q.fetch_all(
            "notify/put_console_notification.sql",
            {
                "client_id": client_id,
                "recipients": list(recipients),
                "kind": kind,
                "priority": priority,
                "message_key": message_key,
                "params": Jsonb(params),
                "link": Jsonb(link),
                "dedupe_key": dedupe_key,
            },
            conn=conn,
        )
        console_rows = len(written)
        if written:
            # Only the rows just written (a replay is deduped and returns none), on this connection,
            # so the email is queued or rolled back with the console row.
            queued = await db.q.fetch_all(
                "notify/email_outbox_put.sql",
                {"client_id": client_id, "notification_ids": [row["id"] for row in written]},
                conn=conn,
            )
            email_rows = len(queued)
    chat_rows = 0
    if staff_event is not None:
        chat_rows = await notifier.notify(
            conn,
            client_id=client_id,
            event=staff_event,
            priority=priority,
            payload=staff_payload or {},
            dedupe_key=dedupe_key,
        )
    return NotifyResult(console_rows=console_rows, chat_rows=chat_rows, email_rows=email_rows)
