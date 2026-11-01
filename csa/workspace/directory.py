"""Basic company directory search, the caller's own manager and the caller's own team.

Only name, job title and work email are ever returned; HR grants govern everything else.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection

from csa.core import db
from csa.workspace.contracts import MyPeopleOut, PersonOut, TeamReason

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


MAX_WORDS = 5  # bounds the SQL; later words are ignored, so a long query still only narrows
_WORD_SPLIT = re.compile(r"[\s,]+")


def _words(query: str) -> list[str]:
    """The first words of the query, split on whitespace and commas."""
    return [word for word in _WORD_SPLIT.split(query.strip()) if word][:MAX_WORDS]


def _title_start(word: str) -> str:
    """A Postgres regex for ``word`` at the start of a word (``\\m``), the word taken literally."""
    return r"\m" + re.escape(word)


async def search_people(conn: AsyncConnection[Any], client_id: UUID, query: str) -> list[PersonOut]:
    """Every word, in any order: in the name or work email, or starting a word of the job title.
    Name and email matches come first."""
    words = _words(query)
    if not words:
        return []
    patterns = [_like(word) for word in words]
    titles = [_title_start(word) for word in words]
    rows = await db.q.fetch_all(
        "workspace/people_search.sql", (client_id, patterns, titles, patterns), conn=conn
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


MAX_TEAM = 20  # one email reaches at most 20 addresses


@dataclass(frozen=True)
class MyTeam:
    """The caller's active direct reports: with a work email, and without one (left out)."""

    reports: list[PersonOut]
    skipped: list[PersonOut]
    reason: TeamReason | None


async def my_team(conn: AsyncConnection[Any], client_id: UUID, user_id: UUID) -> MyTeam:
    """Only for holders of the approves-team switch; the switch is checked before any read."""
    await conn.execute("SELECT set_config('app.hr_user_id', %s, true)", (str(user_id),))
    switch = await db.q.fetch_one("workspace/team_switch.sql", (client_id, user_id), conn=conn)
    if switch is None or not switch["approves_team"]:
        return MyTeam([], [], "not_team_approver")
    me = await db.q.fetch_one("workspace/people_me.sql", (client_id,), conn=conn)
    if me is None:
        return MyTeam([], [], "no_employee_link")
    rows = await db.q.fetch_all("workspace/team_reports.sql", (client_id,), conn=conn)
    # 0484 can list a self-managed caller as their own report; never email yourself as "team".
    people = [
        PersonOut(
            employee_id=row["employee_id"],
            display_name=row["display_name"],
            job_title=None,
            work_email=row["work_email"],
        )
        for row in rows
        if row["employee_id"] != me["employee_id"]
    ]
    reports = [person for person in people if person.work_email]
    if not people:
        return MyTeam([], [], "no_reports")
    if not reports:
        return MyTeam([], [], "no_work_email")
    if len(reports) > MAX_TEAM:
        return MyTeam([], [], "team_too_large")
    return MyTeam(reports, [person for person in people if not person.work_email], None)
