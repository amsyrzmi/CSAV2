"""Findings of the whole-branch review of the Google Calendar work, each pinned by a test that
failed before its fix: who may still receive events, a slower write landing last, a recreated
calendar, a grant revoked at Google, and which bookings the job looks at.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from csa.bookings.google.push import CalendarPush

from tests.conftest import requires_docker
from tests.test_bookings_google_push import (
    ZONE,
    FakeGoogle,
    _records,
    booking_row,
    change,
    connect,
    connect_as,
    credentials_key,
    google,
    ledger,
    make_booking,
    make_person,
    meet_push,
    online,
    person,
    prepared,
    push,
    reconcile,
    soon,
    sync,
)
from tests.test_bookings_setup import Team
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

# The fixtures this file shares with the push tests, bound here so pytest finds them.
credentials_key = credentials_key
prepared = prepared
person = person
google = google
push = push
meet_push = meet_push


@pytest.fixture
async def team(sessions: SessionWorld) -> Team:
    """The team of the push tests."""
    return await Team(sessions).open()


# --- who may still receive events ------------------------------------------------------------


@requires_docker
async def test_a_member_who_was_disabled_gets_no_more_events(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    await team.w.q(
        "UPDATE console_memberships SET is_enabled = false WHERE client_id = %s AND user_id = %s",
        team.w.tenants[0],
        team.staff_info["user"]["id"],
    )
    booking = await make_booking(team, [person["id"]])
    [record] = await _records(sessions, booking)
    await push(record, timeout=5)
    assert (await reconcile(sessions, push))["due"] == 0
    assert google.events() == [] and await ledger(team) == []


@requires_docker
async def test_a_person_given_to_another_member_stops_following_the_old_one(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    await team.w.q(
        "UPDATE booking_people SET user_id = %s WHERE id = %s",
        team.admin_info["user"]["id"],
        person["id"],
    )
    booking = await make_booking(team, [person["id"]])
    [record] = await _records(sessions, booking)
    await push(record, timeout=5)
    assert (await reconcile(sessions, push))["due"] == 0 and google.events() == []
    # The new member connects for the same person: no clash with the old connection.
    await connect_as(team, team.admin)
    assert (await reconcile(sessions, push))["due"] == 1
    assert len(google.events()) == 1


@requires_docker
async def test_a_member_given_another_person_connects_for_that_person(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    second = await make_person(team, "Second Person")
    await team.w.q("UPDATE booking_people SET user_id = NULL WHERE id = %s", person["id"])
    await team.w.q(
        "UPDATE booking_people SET user_id = %s WHERE id = %s",
        team.staff_info["user"]["id"],
        second["id"],
    )
    await connect(team, google, code="c3")
    [row] = await team.w.q(
        "SELECT person_id::text AS person FROM booking_calendar_connections WHERE client_id = %s",
        team.w.tenants[0],
    )
    assert row["person"] == second["id"]


# --- a slower write landing last -------------------------------------------------------------


@requires_docker
async def test_a_write_that_lands_after_a_newer_one_leaves_the_row_to_be_pushed_again(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])

    async def newer_push_finishes_first() -> None:
        await change(team, booking, title="Renamed")
        assert await sync(sessions, push, booking, person["id"]) == "synced"

    google.during_insert = newer_push_finishes_first
    assert await sync(sessions, push, booking, person["id"]) == "synced"  # the older, slower one
    google.during_insert = None
    [row] = await ledger(team)
    # Its own answer is refused (a newer version is recorded), but Google may now hold the older
    # content, so the row is not left "synced".
    assert row["status"] == "pending"
    assert (await reconcile(sessions, push))["due"] == 1
    [event] = google.events()
    assert event["summary"] == "Renamed"
    assert (await ledger(team))[0]["status"] == "synced"


@requires_docker
async def test_a_link_written_while_the_booking_moves_does_not_claim_the_new_version(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await online(team, [person["id"]])
    [record] = await _records(sessions, booking)
    later = soon(4, 14)

    async def staff_move_it() -> None:
        await change(team, booking, starts_at=later, ends_at=later + timedelta(minutes=30))

    google.during_insert = staff_move_it
    updated = await meet_push(record, timeout=5)
    google.during_insert = None
    row = await booking_row(team, booking)
    assert row["version"] == 3 and row["meeting_link"] is not None  # the move, then the link
    [entry] = await ledger(team)
    # The event was made for version 1, so the row says so and the job brings it up to date.
    assert entry["synced_version"] == 1
    assert updated.version != 3  # the mail is not told the event holds the new time
    assert (await reconcile(sessions, meet_push))["due"] == 1
    [event] = google.events()
    assert event["start"]["dateTime"] == later.astimezone(ZONE).isoformat()
    assert (await ledger(team))[0]["synced_version"] == 3


# --- a deleted calendar ----------------------------------------------------------------------


@requires_docker
async def test_a_calendar_made_again_is_filled_with_the_persons_other_bookings(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    first = await make_booking(team, [person["id"]])
    second = await make_booking(team, [person["id"]], start=soon(5))
    await sync(sessions, push, first, person["id"])
    await sync(sessions, push, second, person["id"])
    google.calendars.pop("calendar-1")  # the person deleted their Bookings calendar
    await change(team, first, title="Renamed")
    assert await sync(sessions, push, first, person["id"]) == "synced"
    rows = {r["booking"]: r["status"] for r in await ledger(team)}
    assert rows[second] == "pending"  # its event went with the calendar
    assert (await reconcile(sessions, push))["due"] == 1
    assert len(google.events()) == 2


@requires_docker
async def test_two_syncs_for_one_person_make_the_missing_calendar_once(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    first = await make_booking(team, [person["id"]])
    second = await make_booking(team, [person["id"]], start=soon(5))
    await sync(sessions, push, first, person["id"])
    await sync(sessions, push, second, person["id"])
    google.calendars.pop("calendar-1")
    for booking in (first, second):
        await change(team, booking, title="Renamed")
    google.pause = 0.05
    done = await asyncio.gather(
        sync(sessions, push, first, person["id"]), sync(sessions, push, second, person["id"])
    )
    assert done == ["synced", "synced"]
    assert len(google.created) == 2  # the first, and one made again, not two
    assert len(google.calendars) == 1 and len(google.events()) == 2


# --- a grant revoked at Google ---------------------------------------------------------------


@requires_docker
async def test_a_refused_access_token_is_not_a_failed_try_and_is_refreshed_next(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    google.fail = "provider_unauthorized"
    assert await sync(sessions, push, booking, person["id"]) == "failed"
    google.fail = None
    [row] = await ledger(team)
    assert row["attempts"] == 0  # no retries used up while the token was merely stale
    [expiry] = await team.w.q("SELECT token_expires_at FROM booking_calendar_connections")
    assert expiry["token_expires_at"] <= datetime.now(UTC) + timedelta(seconds=1)
    google.refresh_error = "invalid_grant"  # and the refresh finds the grant revoked
    assert await sync(sessions, push, booking, person["id"]) == "needs_reconnect"


@requires_docker
async def test_connecting_again_gives_failed_rows_their_tries_back(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    booking = await make_booking(team, [person["id"]])
    google.fail = "provider_rejected"
    for _ in range(5):
        await sync(sessions, push, booking, person["id"])
    google.fail = None
    assert (await ledger(team))[0]["attempts"] == 5
    assert (await reconcile(sessions, push))["due"] == 0  # used up
    await team.w.q("UPDATE booking_calendar_connections SET status = 'needs_reconnect'")
    await connect(team, google, code="c4")
    assert (await reconcile(sessions, push))["due"] == 1
    assert len(google.events()) == 1


# --- what the job looks at -------------------------------------------------------------------


@requires_docker
async def test_a_booking_that_already_ended_does_not_take_a_place_in_the_batch(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> None:
    await connect(team, google)
    ended = datetime.now(UTC) - timedelta(hours=2)
    await make_booking(team, [person["id"]], start=ended)
    coming = await make_booking(team, [person["id"]])
    result = await reconcile(sessions, push)
    assert result["due"] == 1 and result["synced"] == 1
    assert [r["booking"] for r in await ledger(team)] == [coming]
