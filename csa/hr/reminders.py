"""HR task reminders (spec 5.4 and section 8): the one place that writes them, and the worker round.

`emit` is the seam with the Inbox session's generic reminder layer: every task reminder is written
through it, and nothing else in the HR code knows how a reminder is delivered. Today it writes one
`hr_notifications` row (console only: Home lists it); when the generic layer is merged, `emit`
forwards to it instead. The database decides what is due and for whom (0635 `hr_tasks_due`: the
assignee's console account, else one row per HR team member) and refuses a write that is not the
system's (`hr_task_reminder_put`).
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

import structlog
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.core.db import q
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

ReminderEvent = Literal["task_assigned", "task_due_soon", "task_due", "task_overdue"]

# The plain line Home shows for each reminder, before the step's title.
LINES: dict[str, str] = {
    "task_assigned": "New task",
    "task_due_soon": "Due soon",
    "task_due": "Due today",
    "task_overdue": "Overdue",
}


async def emit(
    conn: AsyncConnection,
    client_id: UUID,
    event: ReminderEvent,
    recipient_user_id: UUID,
    dedupe_key: str,
    due_on: date | None,
    subject: str,
    *,
    resource_id: UUID,
) -> bool:
    """Write one reminder for a console user; False when (company, recipient, key) already exists.

    `conn` is a system transaction of `client_id` (`tx`, no HR user). `resource_id` is the task the
    reminder is about. The database refuses another company, an HR session, a kind that is not a
    task reminder, an empty key or subject, a task of another company and a recipient who is not
    an enabled member.
    """
    row = await q.fetch_one(
        "hr/reminder_emit.sql",
        (client_id, recipient_user_id, event, resource_id, dedupe_key, due_on, subject),
        conn=conn,
    )
    return bool(row["written"])


def reminder_line(event: str, title: str | None) -> str:
    """The plain line of a reminder: "Due today: <step title>"."""
    return f"{LINES[event]}: {title or 'a task'}"


async def hr_tasks_round(
    app_pool: AsyncConnectionPool, *, today: date | None = None
) -> tuple[int, int]:
    """Write the task reminders due once: (reminders seen, reminders written).

    `today` defaults to today in Kuala Lumpur (the database's clock). Each company runs in its
    own tenant transaction, so one failing company is logged by id and the others still run. Logs
    carry counts and ids only, never names or titles.
    """
    async with app_pool.connection() as conn:
        async with conn.transaction():
            tenants = await q.fetch_all("hr/tasks_due_tenants.sql", (today,), conn=conn)
    seen = written = failed = 0
    for tenant in tenants:
        client_id = tenant["client_id"]
        try:
            async with tx(app_pool, str(client_id)) as conn:
                due = await q.fetch_all("hr/tasks_due.sql", (client_id, today), conn=conn)
                count = 0
                for item in due:
                    count += await emit(
                        conn,
                        client_id,
                        item["kind"],
                        item["recipient_id"],
                        item["reminder_key"],
                        item["due_on"],
                        reminder_line(item["kind"], item["title"]),
                        resource_id=item["task_id"],
                    )
        except Exception as exc:
            failed += 1
            logger.warning(
                "hr_tasks_tenant_failed", client_id=str(client_id), error_type=type(exc).__name__
            )
            continue
        seen += len(due)
        written += count
    logger.info("hr_tasks_round", tenants=len(tenants), seen=seen, written=written, failed=failed)
    return seen, written
