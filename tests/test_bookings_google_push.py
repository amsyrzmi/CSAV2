"""A person's Google events follow the bookings they attend: what an event holds, the push, the
reconcile job, and what happens when Google or the grant misbehaves. A fake Google stands in.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from csa.bookings import service
from csa.bookings.google.client import CalendarError, EventResult
from csa.bookings.google.push import (
    CalendarPush,
    run_calendar_reconcile,
    sync_one,
)
from csa.core.tenancy import tx
from csa.jobs.clock import SystemClock
from pydantic import SecretStr

from tests import test_bookings_google_connections as world
from tests.conftest import requires_docker
from tests.test_bookings_setup import Team, make_person
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

credentials_key = world.credentials_key
prepared = world.prepared
team = world.team
person = world.person
connect = world.connect
FakeCalendar = world.FakeCalendar
ZONE = ZoneInfo("Asia/Kuala_Lumpur")


class FakeGoogle(FakeCalendar):
    """Google as far as events go: calendars holding events, ids that stay reserved once deleted."""

    def __init__(self) -> None:
        super().__init__()
        self.calendars: dict[str, dict[str, dict[str, Any]]] = {}
        self.reserved: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str, str]] = []
        self.fail: str | None = None
        self.pause = 0.0

    async def create_calendar(self, access: SecretStr, *, summary: str, time_zone: str) -> str:
        calendar = await super().create_calendar(access, summary=summary, time_zone=time_zone)
        self.calendars[calendar] = {}
        return calendar

    async def calendar_exists(self, access: SecretStr, calendar_id: str) -> bool:
        return calendar_id in self.calendars

    async def _call(self, verb: str, calendar_id: str, event_id: str) -> None:
        self.calls.append((verb, calendar_id, event_id))
        await asyncio.sleep(self.pause)
        if self.fail:
            raise CalendarError(self.fail)

    async def insert_event(
        self,
        access: SecretStr,
        calendar_id: str,
        body: dict[str, Any],
        *,
        meet: bool = False,
        event_id: str | None = None,
    ) -> EventResult:
        event_id = event_id or uuid.uuid4().hex
        await self._call("insert", calendar_id, event_id)
        if calendar_id not in self.calendars:
            raise CalendarError("provider_not_found")
        if event_id in self.calendars[calendar_id] or (calendar_id, event_id) in self.reserved:
            raise CalendarError("event_exists")
        self.calendars[calendar_id][event_id] = dict(body)
        return EventResult(event_id, None)

    async def patch_event(
        self, access: SecretStr, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> None:
        await self._call("patch", calendar_id, event_id)
        events = self.calendars.get(calendar_id, {})
        if event_id not in events:
            raise CalendarError("provider_not_found")
        events[event_id] = {**events[event_id], **body}

    async def delete_event(self, access: SecretStr, calendar_id: str, event_id: str) -> None:
        await self._call("delete", calendar_id, event_id)
        events = self.calendars.get(calendar_id, {})
        if event_id not in events:
            raise CalendarError("provider_not_found")
        del events[event_id]
        self.reserved.add((calendar_id, event_id))

    def events(self) -> list[dict[str, Any]]:
        return [event for calendar in self.calendars.values() for event in calendar.values()]


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def push(sessions: SessionWorld, google: FakeGoogle) -> CalendarPush:
    return CalendarPush(sessions.pool, sessions.app.state.calendar_connections, google)


# --- helpers ---------------------------------------------------------------------------------


def soon(days: int = 3, hour: int = 10) -> datetime:
    day = datetime.now(UTC) + timedelta(days=days)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


async def make_booking(
    team: Team,
    person_ids: list[str],
    *,
    start: datetime | None = None,
    status: str = "confirmed",
    title: str = "Weekly sync",
) -> str:
    """A booking written as the owner, with the people attending."""
    booking, begin = str(uuid.uuid4()), start or soon()
    client = team.w.tenants[0]
    await team.w.q(
        "INSERT INTO bookings (id, client_id, service, title, starts_at, ends_at, status)"
        " VALUES (%s, %s, 'Synthetic', %s, %s, %s, %s)",
        booking,
        client,
        title,
        begin,
        begin + timedelta(minutes=30),
        status,
    )
    for person_id in person_ids:
        await team.w.q(
            "INSERT INTO booking_attendees (client_id, booking_id, person_id) VALUES (%s, %s, %s)",
            client,
            booking,
            person_id,
        )
    return booking


async def change(team: Team, booking: str, **columns: Any) -> int:
    """What staff, the bot or a job does to a booking: new values and a version one higher."""
    sets = ", ".join(f"{name} = %s" for name in columns)
    rows = await team.w.q(
        f"UPDATE bookings SET {sets}, version = version + 1 WHERE id = %s RETURNING version",  # type: ignore[arg-type]
        *columns.values(),
        booking,
    )
    return int(rows[0]["version"])


async def ledger(team: Team, booking: str | None = None) -> list[dict[str, Any]]:
    return await team.w.q(
        "SELECT booking_id::text AS booking, status, synced_version, google_event_id, attempts,"
        " failed_version, last_error FROM booking_calendar_events WHERE client_id = %s"
        " ORDER BY updated_at",
        team.w.tenants[0],
    )


async def sync(
    sessions: SessionWorld,
    push: CalendarPush,
    booking: str,
    person_id: str,
    now: datetime | None = None,
) -> str:
    return await sync_one(
        sessions.pool,
        push.connections,
        push.api,
        uuid.UUID(sessions.tenants[0]),
        uuid.UUID(booking),
        uuid.UUID(person_id),
        now=now or datetime.now(UTC),
    )


async def reconcile(sessions: SessionWorld, push: CalendarPush) -> dict[str, int]:
    import psycopg

    async with await psycopg.AsyncConnection.connect(sessions.owner_dsn, autocommit=True) as owner:
        return await run_calendar_reconcile(owner, sessions.pool, push, SystemClock())


# --- the sync --------------------------------------------------------------------------------


@requires_docker
async def test_a_confirmed_booking_gets_an_event_in_the_persons_calendar(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    assert await sync(sessions, push, booking, person["id"]) == "synced"
    [event] = google.events()
    assert event["summary"] == "Weekly sync" and "attendees" not in event
    [row] = await ledger(team)
    assert row["status"] == "synced" and row["synced_version"] == 1
    assert row["google_event_id"] in google.calendars["calendar-1"]


@requires_docker
async def test_a_move_patches_the_one_event_and_a_cancel_deletes_it(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    await sync(sessions, push, booking, person["id"])
    later = soon(4, 14)
    version = await change(team, booking, starts_at=later, ends_at=later + timedelta(minutes=30))
    assert await sync(sessions, push, booking, person["id"]) == "synced"
    [event] = google.events()
    assert event["start"]["dateTime"] == later.astimezone(ZONE).isoformat()
    assert (await ledger(team))[0]["synced_version"] == version
    await change(team, booking, status="cancelled")
    assert await sync(sessions, push, booking, person["id"]) == "deleted"
    assert google.events() == [] and (await ledger(team))[0]["status"] == "deleted"


@requires_docker
async def test_nothing_is_pushed_for_a_pending_or_ended_booking(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    pending = await make_booking(team, [person["id"]], status="pending")
    ended = await make_booking(team, [person["id"]], start=soon(-3))
    assert await sync(sessions, push, pending, person["id"]) == "skipped"
    assert await sync(sessions, push, ended, person["id"]) == "skipped"
    assert google.events() == [] and await ledger(team) == []


@requires_docker
async def test_the_push_reaches_each_connected_attendee_and_no_one_else(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    other = await make_person(team, "No Google", user_id=team.admin_info["user"]["id"])
    booking = await make_booking(team, [person["id"], other["id"]])
    [record_] = await _records(sessions, booking)
    await push(record_, timeout=5)
    assert len(google.events()) == 1
    [row] = await ledger(team)
    assert row["status"] == "synced"


async def _records(sessions: SessionWorld, booking: str) -> list[service.BookingRecord]:
    async with tx(sessions.pool, sessions.tenants[0]) as conn:
        return await service.read_records(
            conn, uuid.UUID(sessions.tenants[0]), booking_id=uuid.UUID(booking)
        )


@requires_docker
async def test_a_slow_google_never_fails_the_push_and_the_job_finishes_it(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    [record_] = await _records(sessions, booking)
    google.pause = 1.0
    await push(record_, timeout=0.2)  # cut off: it returns, it does not raise
    google.pause = 0.0
    assert (await reconcile(sessions, push))["due"] == 1
    assert len(google.events()) == 1
    assert (await ledger(team))[0]["status"] == "synced"


@requires_docker
async def test_a_down_google_leaves_a_failed_row_and_the_push_returns(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    [record_] = await _records(sessions, booking)
    google.fail = "provider_unreachable"
    await push(record_, timeout=5)
    [row] = await ledger(team)
    assert row["status"] == "failed" and row["last_error"] == "provider_unreachable"
    assert row["attempts"] == 1 and row["failed_version"] == 1


# --- the reconcile job -----------------------------------------------------------------------


@requires_docker
async def test_the_job_makes_an_event_for_a_booking_whose_push_never_ran(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    """A crash after commit, or a booking made before the person connected."""
    await connect(team, google)
    await make_booking(team, [person["id"]])
    assert (await reconcile(sessions, push))["synced"] == 1
    assert len(google.events()) == 1
    assert (await reconcile(sessions, push))["due"] == 0  # and only once


@requires_docker
async def test_the_job_follows_a_cancel_a_lapse_and_a_no_show_the_bot_or_a_job_made(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    cancelled = await make_booking(team, [person["id"]], title="Cancelled")
    lapsed = await make_booking(team, [person["id"]], start=soon(5), title="Lapsed")
    shown = await make_booking(team, [person["id"]], start=soon(6), title="No show")
    await reconcile(sessions, push)
    assert len(google.events()) == 3
    await change(team, cancelled, status="cancelled")  # the bot's cancel
    await change(team, lapsed, status="cancelled")  # a lapse
    await change(team, shown, status="no_show")  # a no-show: the event stays
    stats = await reconcile(sessions, push)
    # The no-show changes nothing in Google, so the job does not even look at it.
    assert stats["due"] == 2 and stats["deleted"] == 2
    assert [e["summary"] for e in google.events()] == ["No show"]


@requires_docker
async def test_an_up_to_date_row_is_left_alone_and_a_stale_one_is_patched(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    await reconcile(sessions, push)
    google.calls.clear()
    assert (await reconcile(sessions, push))["due"] == 0 and google.calls == []
    await change(team, booking, title="Renamed")
    assert (await reconcile(sessions, push))["synced"] == 1
    assert [verb for verb, *_ in google.calls] == ["patch"]
    assert google.events()[0]["summary"] == "Renamed"


@requires_docker
async def test_reconnecting_backfills_without_a_second_event(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    moved = await make_booking(team, [person["id"]], title="Moved")
    kept = await make_booking(team, [person["id"]], start=soon(5), title="Kept")
    await reconcile(sessions, push)
    ids = {row["booking"]: row["google_event_id"] for row in await ledger(team)}
    await team.send(team.staff, "/calendar/disconnect")
    assert (await reconcile(sessions, push))["due"] == 0  # nothing while disconnected
    await change(team, moved, starts_at=soon(7), ends_at=soon(7) + timedelta(minutes=30))
    await connect(team, google, "c2")
    stats = await reconcile(sessions, push)
    assert stats["due"] == 2 and stats["synced"] == 2
    assert len(google.events()) == 2  # patched in place: no duplicates
    assert {row["booking"]: row["google_event_id"] for row in await ledger(team)} == ids
    assert kept in ids


@requires_docker
async def test_a_late_answer_never_records_an_older_version(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    await sync(sessions, push, booking, person["id"])
    version = await change(team, booking, title="Two")
    await sync(sessions, push, booking, person["id"])
    async with tx(sessions.pool, sessions.tenants[0]) as conn:
        from csa.core import db

        await db.q.execute(
            "bookings/google_event_settle.sql",
            {
                "client_id": uuid.UUID(sessions.tenants[0]),
                "booking_id": uuid.UUID(booking),
                "person_id": uuid.UUID(person["id"]),
                "version": version - 1,
                "status": "synced",
                "event_id": "stale",
            },
            conn=conn,
        )
    [row] = await ledger(team)
    assert row["synced_version"] == version and row["google_event_id"] != "stale"


# --- Google and the grant misbehaving --------------------------------------------------------


@requires_docker
async def test_an_event_Google_already_has_is_patched_not_made_twice(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    """A crash after Google made the event, or the push racing the job: a patch, not a second."""
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    await sync(sessions, push, booking, person["id"])
    [row] = await ledger(team)
    await team.w.q("UPDATE booking_calendar_events SET status = 'pending', synced_version = NULL")
    assert await sync(sessions, push, booking, person["id"]) == "synced"
    assert (
        len(google.events()) == 1
        and (await ledger(team))[0]["google_event_id"] == row["google_event_id"]
    )


@requires_docker
async def test_an_event_the_person_deleted_is_made_again_under_a_new_id_once(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    await sync(sessions, push, booking, person["id"])
    [first] = await ledger(team)
    await google.delete_event(SecretStr("x"), "calendar-1", first["google_event_id"])  # in Google
    await change(team, booking, title="Again")
    assert await sync(sessions, push, booking, person["id"]) == "synced"
    [second] = await ledger(team)
    assert second["google_event_id"] != first["google_event_id"] and len(google.events()) == 1
    google.calls.clear()
    await change(team, booking, title="Third")
    await sync(sessions, push, booking, person["id"])
    assert [verb for verb, *_ in google.calls] == ["patch"] and len(google.events()) == 1


@requires_docker
async def test_a_calendar_the_person_deleted_is_made_again_once(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    await sync(sessions, push, booking, person["id"])
    del google.calendars["calendar-1"]  # deleted in Google
    await change(team, booking, title="Again")
    assert await sync(sessions, push, booking, person["id"]) == "synced"
    assert len(google.created) == 2 and len(google.calendars["calendar-2"]) == 1
    stored = await team.w.q("SELECT calendar_id FROM booking_calendar_connections")
    assert stored[0]["calendar_id"] == "calendar-2"


@requires_docker
async def test_a_refused_grant_needs_a_reconnect_and_touches_nothing_else(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    await team.w.q(
        "UPDATE booking_calendar_connections SET token_expires_at = now() - interval '1 minute'"
    )
    google.refresh_error = "invalid_grant"
    assert await sync(sessions, push, booking, person["id"]) == "needs_reconnect"
    status = await team.w.q("SELECT status FROM booking_calendar_connections")
    assert status[0]["status"] == "needs_reconnect"
    [row] = await ledger(team)
    assert row["status"] == "pending" and row["attempts"] == 0 and row["last_error"] is None
    assert (await reconcile(sessions, push))["due"] == 0  # nothing more until they reconnect


@requires_docker
async def test_a_rate_limit_is_tried_five_times_at_one_version_and_again_when_it_changes(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    google.fail = "provider_rate_limited"
    for _ in range(8):
        await reconcile(sessions, push)
    assert len([c for c in google.calls if c[0] == "patch"]) == 5
    [row] = await ledger(team)
    assert (
        row["status"] == "failed"
        and row["attempts"] == 5
        and row["last_error"] == "provider_rate_limited"
    )
    await change(team, booking, title="New version")
    google.fail = None
    assert (await reconcile(sessions, push))["synced"] == 1
