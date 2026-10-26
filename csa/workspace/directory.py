"""Basic company directory search and the caller's own manager lookup.

Only name, job title and work email are ever returned; HR grants govern everything else.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection

from csa.core import db
from csa.workspace.contracts import MyPeopleOut, PersonOut

# Best-effort throttle: in-memory, per process, keyed by login session id. It resets on restart
# and is not shared across workers, so it bounds scraping rather than guaranteeing a quota.
LOOKUPS_PER_MINUTE = 60
_WINDOW_SECONDS = 60.0
_lookups: dict[UUID, deque[float]] = {}


def clear_throttle() -> None:
    _lookups.clear()


def throttle_lookup(session_id: UUID, *, now: float | None = None) -> None:
    """Raise 429 once a login session exceeds the per-minute lookup budget."""
    moment = time.monotonic() if now is None else now
    window = _lookups.setdefault(session_id, deque())
    while window and moment - window[0] >= _WINDOW_SECONDS:
        window.popleft()
    if len(window) >= LOOKUPS_PER_MINUTE:
        raise HTTPException(429, "people_lookup_rate_limited")
    window.append(moment)


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_people(conn: AsyncConnection[Any], client_id: UUID, query: str) -> list[PersonOut]:
    pattern = _like(query.strip())
    rows = await db.q.fetch_all(
        "workspace/people_search.sql", (client_id, pattern, pattern), conn=conn
    )
    return [PersonOut.model_validate(row) for row in rows]


async def my_people(conn: AsyncConnection[Any], client_id: UUID, user_id: UUID) -> MyPeopleOut:
    # The view's row filter is the caller's identity, set transaction-locally for this read only.
    await conn.execute("SELECT set_config('app.hr_user_id', %s, true)", (str(user_id),))
    row = await db.q.fetch_one("workspace/people_me.sql", (client_id,), conn=conn)
    if row is None:
        return MyPeopleOut(employee_id=None, manager=None, reason="no_employee_link")
    if row["manager_employee_id"] is None:
        return MyPeopleOut(employee_id=row["employee_id"], manager=None, reason="no_manager")
    manager = PersonOut(
        employee_id=row["manager_employee_id"],
        display_name=row["manager_display_name"],
        job_title=row["manager_job_title"],
        work_email=row["manager_work_email"],
    )
    return MyPeopleOut(employee_id=row["employee_id"], manager=manager, reason=None)
