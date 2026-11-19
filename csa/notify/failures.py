"""Bell notices for notification delivery that stopped working (inbox refinement part 3, design
2026-11-19, section 5.2).

When a staff target's notification goes ``dead`` (Telegram or email), every tenant admin of the
company gets one ``notify.target_failed`` console notification; when a person's own email goes
``dead``, that person gets one ``notify.email_failed``. Each is guarded by a once-per-episode flag
(``staff_notification_targets.failure_notified_at`` and
``console_notification_prefs.failure_notified_at``), set atomically by the UPDATE that decides
whether to notify, so a run of dead rows raises one notice, not one per row. A delivery, a
successful test or an edit (target) and a delivered email or a saved preference (person) clear the
flag, so the next failure is a new episode.

The two kinds are bell only. They are not in :data:`csa.notify.prefs.KINDS`, and the outbox is only
filled for a person's listed kinds, so they never reach an email queue. Params carry ids, the
channel name and a count, never an address or a message.

The ``*_safely`` wrappers are what the senders call: they run the hook in a savepoint on the
sender's own transaction and swallow (and log, ids only) any error, so a failure here never masks
the failure being handled and never rolls back a delivered mark.

This module imports :mod:`csa.notify.people` and :mod:`csa.core` only, like ``csa.notify.people``
itself, so the senders can use it without a cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import structlog

from csa.core import db
from csa.notify.people import admin_user_ids, notify_people

logger = structlog.get_logger(__name__)

TARGET_FAILED = "notify.target_failed"
EMAIL_FAILED = "notify.email_failed"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_LINK: dict[str, Any] = {"route": "notifications"}


def _epoch_micros(moment: datetime) -> int:
    """The flag time as whole microseconds since the epoch (exact, no float)."""
    return (moment - _EPOCH) // (datetime(1970, 1, 1, 0, 0, 0, 1, tzinfo=UTC) - _EPOCH)


async def report_dead_target(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, target_id: UUID
) -> bool:
    """Tell every tenant admin that a target's notification went dead, once per failure episode.

    True when this call set the flag (and wrote the notices); False when the target was already
    flagged or does not exist. The connection must be the company's own (``tx``).
    """
    row = await db.q.fetch_one(
        "notify/target_failure_mark.sql",
        {"client_id": client_id, "target_id": target_id},
        conn=conn,
    )
    if row is None:
        return False
    admins = await admin_user_ids(conn, client_id)
    await notify_people(
        conn,
        client_id,
        recipients=admins,
        kind=TARGET_FAILED,
        priority="high",
        message_key=TARGET_FAILED,
        params={
            "target_id": str(target_id),
            "channel": row["channel"],
            "attempts": row["attempts"],
        },
        link=dict(_LINK),
        dedupe_key=f"{TARGET_FAILED}:{target_id}:{_epoch_micros(row['failure_notified_at'])}",
    )
    return True


async def report_dead_person_email(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, user_id: UUID
) -> bool:
    """Tell a person, and only them, that an email to them went dead, once per failure episode.

    True when this call set the flag (and wrote the notice); False when the person was already
    flagged or has no saved preferences row (nothing is written then).
    """
    row = await db.q.fetch_one(
        "notify/person_failure_mark.sql",
        {"client_id": client_id, "user_id": user_id},
        conn=conn,
    )
    if row is None:
        return False
    await notify_people(
        conn,
        client_id,
        recipients=[user_id],
        kind=EMAIL_FAILED,
        priority="high",
        message_key=EMAIL_FAILED,
        params={},
        link=dict(_LINK),
        dedupe_key=f"{EMAIL_FAILED}:{user_id}:{_epoch_micros(row['failure_notified_at'])}",
    )
    return True


async def clear_target_failure(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, target_id: UUID
) -> None:
    """The target works again (delivered, tested or edited): the next dead row notifies again."""
    await db.q.execute(
        "notify/target_failure_clear.sql",
        {"client_id": client_id, "target_id": target_id},
        conn=conn,
    )


async def clear_person_failure(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, user_id: UUID
) -> None:
    """The person's email works again: the next dead email notifies again."""
    await db.q.execute(
        "notify/person_failure_clear.sql",
        {"client_id": client_id, "user_id": user_id},
        conn=conn,
    )


async def report_dead_target_safely(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, target_id: UUID
) -> None:
    """:func:`report_dead_target` in a savepoint; an error is logged (ids only) and swallowed."""
    try:
        async with conn.transaction():
            await report_dead_target(conn, client_id, target_id)
    except Exception:
        logger.exception("target_failure_report_failed", target_id=str(target_id))


async def report_dead_person_email_safely(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, user_id: UUID
) -> None:
    """:func:`report_dead_person_email` in a savepoint; an error is logged and swallowed."""
    try:
        async with conn.transaction():
            await report_dead_person_email(conn, client_id, user_id)
    except Exception:
        logger.exception("person_failure_report_failed", user_id=str(user_id))


async def clear_target_failure_safely(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, target_id: UUID
) -> None:
    """:func:`clear_target_failure` in a savepoint; an error is logged and swallowed, so a clear
    never undoes the delivered mark it rides on."""
    try:
        async with conn.transaction():
            await clear_target_failure(conn, client_id, target_id)
    except Exception:
        logger.exception("target_failure_clear_failed", target_id=str(target_id))


async def clear_person_failure_safely(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, user_id: UUID
) -> None:
    """:func:`clear_person_failure` in a savepoint; an error is logged and swallowed."""
    try:
        async with conn.transaction():
            await clear_person_failure(conn, client_id, user_id)
    except Exception:
        logger.exception("person_failure_clear_failed", user_id=str(user_id))
