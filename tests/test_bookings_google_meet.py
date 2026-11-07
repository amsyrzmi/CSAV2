"""The Meet link on an online booking as the console sees it: that it is missing, and asking again.

The push and the job that make the link are in `test_bookings_google_push.py`; a fake Google stands
in here too.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from csa.bookings.google.push import CalendarPush

from tests.conftest import requires_docker
from tests.test_bookings_google_push import (
    MEET,
    FakeGoogle,
    _records,
    booking_row,
    connect,
    credentials_key,
    google,
    make_booking,
    make_person,
    online,
    person,
    prepared,
    team,
)
from tests.test_bookings_setup import BASE, Team
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

# The fixtures this file shares with the push tests, bound here so pytest finds them.
credentials_key = credentials_key
prepared = prepared
team = team
person = person
google = google


@pytest.fixture
def meet_on(sessions: SessionWorld, google: FakeGoogle) -> Iterator[CalendarPush]:
    """The gateway's push with Meet links on, as `apps.gateway.main` sets it on the app."""
    push = CalendarPush(sessions.pool, sessions.app.state.calendar_connections, google, meet=True)
    sessions.app.state.calendar_push = push
    yield push
    sessions.app.state.calendar_push = None


async def calendar(team: Team, who: httpx.AsyncClient) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    window = {
        "from": (now - timedelta(days=1)).isoformat(),
        "to": (now + timedelta(days=30)).isoformat(),
    }
    found = await who.get(f"{BASE}/calendar", params=window)
    assert found.status_code == 200, found.text
    return found.json()["items"]  # type: ignore[no-any-return]


async def retry(team: Team, who: httpx.AsyncClient, booking: str) -> httpx.Response:
    return await team.send(who, f"/bookings/{booking}/meet")


async def refused_once(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    push: CalendarPush,
) -> str:
    """An online booking whose first try at a link was refused: its event is there, with none."""
    await connect(team, google)
    booking = await online(team, [person["id"]])
    [record] = await _records(sessions, booking)
    google.meet_refused = True
    await push(record, timeout=5)
    google.meet_refused = False
    return booking


@requires_docker
async def test_an_online_booking_without_a_link_is_missing_one_while_meet_links_are_on(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_on: CalendarPush,
) -> None:
    booking = await refused_once(sessions, team, person, google, meet_on)
    [item] = await calendar(team, team.staff)
    assert item["id"] == booking and item["meet_missing"] is True and item["meeting_link"] is None


@requires_docker
async def test_a_booking_with_a_link_or_not_online_is_not_missing_one(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_on: CalendarPush,
) -> None:
    await connect(team, google)
    await online(team, [person["id"]], link="https://zoom.example/j/9")
    await online(team, [person["id"]], kind="phone")
    await online(team, [person["id"]], kind="in_person")
    items = await calendar(team, team.staff)
    assert len(items) == 3 and not [i for i in items if i["meet_missing"]]


@requires_docker
async def test_with_meet_links_off_nothing_is_missing(
    sessions: SessionWorld, team: Team, person: dict[str, Any], google: FakeGoogle
) -> None:
    await connect(team, google)
    await online(team, [person["id"]])
    [item] = await calendar(team, team.staff)
    assert item["meet_missing"] is False


@requires_docker
async def test_asking_again_puts_the_link_on_the_booking_and_answers_with_it(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_on: CalendarPush,
) -> None:
    booking = await refused_once(sessions, team, person, google, meet_on)
    answered = await retry(team, team.staff, booking)
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["meeting_link"] == MEET and body["meet_missing"] is False and body["email"] is None
    assert (await booking_row(team, booking))["meeting_link"] == MEET


@requires_docker
async def test_asking_again_when_google_still_refuses_answers_with_the_booking_unchanged(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_on: CalendarPush,
) -> None:
    booking = await refused_once(sessions, team, person, google, meet_on)
    google.meet_refused = True
    answered = await retry(team, team.staff, booking)
    assert answered.status_code == 200, answered.text
    assert answered.json()["meet_missing"] is True
    assert (await booking_row(team, booking))["meeting_link"] is None


@requires_docker
async def test_only_someone_who_may_change_the_booking_can_ask_again(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_on: CalendarPush,
) -> None:
    booking = await refused_once(sessions, team, person, google, meet_on)
    assert (await retry(team, team.reader, booking)).status_code == 403
    # A booking the staff member neither made nor attends is not theirs to change.
    other = await make_person(team, "Someone Else")
    elsewhere = await make_booking(team, [other["id"]])
    await team.w.q(
        "UPDATE bookings SET location_kind = 'online', created_by = %s WHERE id = %s",
        team.admin_info["user"]["id"],
        elsewhere,
    )
    denied = await retry(team, team.staff, elsewhere)
    assert denied.status_code == 403 and denied.json()["detail"] == "own_bookings_only"
    assert (await booking_row(team, booking))["meeting_link"] is None  # nothing was asked yet


@requires_docker
async def test_asking_again_is_refused_when_there_is_nothing_to_ask_for(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_on: CalendarPush,
) -> None:
    await connect(team, google)
    typed = await online(team, [person["id"]], link="https://zoom.example/j/9")
    refused = await retry(team, team.staff, typed)
    assert refused.status_code == 409 and refused.json()["detail"] == "wrong_status"
    assert google.meet_asked == []


@requires_docker
async def test_asking_again_with_meet_links_off_is_refused(
    sessions: SessionWorld, team: Team, person: dict[str, Any], google: FakeGoogle
) -> None:
    await connect(team, google)
    booking = await online(team, [person["id"]])
    refused = await retry(team, team.staff, booking)
    assert refused.status_code == 409 and refused.json()["detail"] == "calendar_unavailable"


@requires_docker
async def test_asking_again_when_no_one_on_the_booking_has_a_calendar_is_refused(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    meet_on: CalendarPush,
) -> None:
    booking = await online(team, [person["id"]])  # the staff member never connected
    refused = await retry(team, team.staff, booking)
    assert refused.status_code == 409 and refused.json()["detail"] == "not_connected"
