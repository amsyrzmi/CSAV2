"""Mail to people whose Google Calendar already holds a booking, and the push that comes first.

A person whose event is on their calendar gets the words and no calendar file (it would add a
second event); everyone else is mailed as before. A fake Google and a fake relay stand in.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from csa.bookings import notify
from csa.bookings.api import build_bookings_router
from csa.bookings.google.api import build_calendar_router
from csa.bookings.google.connections import CalendarConnections
from csa.bookings.google.push import CalendarPush
from csa.console.mailer import Mailer

from tests import test_bookings_google_connections as world
from tests.conftest import REPO_ROOT, requires_docker
from tests.test_bookings_api import book, booked, meeting
from tests.test_bookings_google_push import FakeGoogle
from tests.test_bookings_mail import BRAND, SENDER, TEN_KL, FakeTransport
from tests.test_bookings_setup import Team
from tests.test_console_sessions import SessionWorld
from tests.test_customer_replies import activate_only

pytestmark = [requires_docker]
pytest_plugins = ["tests.test_console_sessions"]

credentials_key = world.credentials_key
team = world.team
person = world.person
connect = world.connect
GUEST = "guest@example.test"


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def relay(sessions: SessionWorld) -> FakeTransport:
    transport = FakeTransport()
    sessions.app.state.mailer = Mailer(transport, SENDER)
    return transport


@pytest.fixture(autouse=True)
async def prepared(sessions: SessionWorld, google: FakeGoogle) -> None:
    sessions.app.include_router(build_bookings_router())
    sessions.app.include_router(build_calendar_router())
    connections = CalendarConnections(sessions.pool, google, enabled=True)
    sessions.app.state.calendar_connections = connections
    sessions.app.state.calendar_push = CalendarPush(sessions.pool, connections, google)
    for tenant in sessions.tenants:
        await activate_only(sessions, tenant, "bookings")
    await sessions.q(
        "UPDATE clients SET timezone = 'Asia/Kuala_Lumpur', brand_name = %s WHERE id = %s",
        BRAND,
        sessions.tenants[0],
    )


def staff_address(team: Team) -> str:
    return str(team.staff_info["user"]["email"])


async def make(team: Team, person: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body = meeting(person, start=TEN_KL, guests=[{"name": "Guest", "email": GUEST}], **extra)
    return await booked(team, team.admin, body)


async def later(team: Team, made: dict[str, Any], hours: int = 2) -> Any:
    """Move a booking `hours` later, as an Admin."""
    from datetime import datetime

    start = datetime.fromisoformat(made["starts_at"]) + timedelta(hours=hours)
    body = {"version": made["version"], "starts_at": start.isoformat(), "acknowledged": []}
    return await team.send(team.admin, f"/bookings/{made['id']}", body, method="PATCH")


async def test_a_person_whose_google_calendar_holds_it_is_mailed_no_file(
    team: Team, person: dict[str, Any], google: FakeGoogle, relay: FakeTransport
) -> None:
    await connect(team, google)
    await make(team, person)
    mine = relay.for_(staff_address(team))
    assert mine.calendar is None
    assert "Google Calendar" in mine.text and "attached" not in mine.text
    guest = relay.for_(GUEST).calendar
    assert guest is not None and guest.method == "REQUEST"
    assert len(google.events()) == 1


async def test_a_person_whose_event_did_not_make_it_is_mailed_the_file_as_before(
    team: Team, person: dict[str, Any], google: FakeGoogle, relay: FakeTransport
) -> None:
    await connect(team, google)
    google.fail = "provider_unreachable"
    made = await book(team, team.admin, meeting(person, start=TEN_KL))
    assert made.status_code == 201, made.text  # a down Google never fails a booking
    mail = relay.for_(staff_address(team))
    assert mail.calendar is not None and mail.calendar.method == "REQUEST"
    assert google.events() == []


async def test_nothing_changes_for_a_person_who_has_not_connected(
    team: Team, person: dict[str, Any], google: FakeGoogle, relay: FakeTransport
) -> None:
    await make(team, person)
    assert relay.for_(staff_address(team)).calendar is not None and google.events() == []


async def test_with_no_push_everything_is_as_it_was(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    relay: FakeTransport,
) -> None:
    await connect(team, google)
    sessions.app.state.calendar_push = None
    await make(team, person)
    assert relay.for_(staff_address(team)).calendar is not None and google.events() == []


async def test_a_move_patches_the_event_and_mails_no_file(
    team: Team, person: dict[str, Any], google: FakeGoogle, relay: FakeTransport
) -> None:
    await connect(team, google)
    made = await make(team, person)
    relay.sent.clear()
    moved = await later(team, made)
    assert moved.status_code == 200, moved.text
    mine = relay.for_(staff_address(team))
    assert mine.calendar is None and "Google Calendar" in mine.text
    assert relay.for_(GUEST).calendar is not None  # guests are unchanged
    [event] = google.events()
    assert event["start"]["dateTime"].startswith(moved.json()["starts_at"][:10])


async def test_a_cancel_sends_the_cancel_file_only_to_who_was_ever_sent_a_file(
    team: Team, person: dict[str, Any], google: FakeGoogle, relay: FakeTransport
) -> None:
    await connect(team, google)
    free = await make(team, person)  # their invitation had no file
    relay.sent.clear()
    gone = await team.send(
        team.admin, f"/bookings/{free['id']}/cancel", {"version": free["version"]}
    )
    assert gone.status_code == 200, gone.text
    mine = relay.for_(staff_address(team))
    assert mine.calendar is None and "removed from your Google Calendar" in mine.text
    guest = relay.for_(GUEST).calendar
    assert guest is not None and guest.method == "CANCEL"
    assert google.events() == []

    google.fail = "provider_unreachable"  # this one was invited with a file
    sent = await booked(team, team.admin, meeting(person, start=TEN_KL, title="Second"))
    assert relay.sent[-1].calendar is not None
    google.fail = None
    relay.sent.clear()
    await team.send(team.admin, f"/bookings/{sent['id']}/cancel", {"version": sent["version"]})
    mail = relay.for_(staff_address(team))
    assert mail.calendar is not None and mail.calendar.method == "CANCEL"


async def test_a_slow_google_holds_the_mail_no_longer_than_the_timeout(
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    relay: FakeTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await connect(team, google)
    monkeypatch.setattr(notify, "CALENDAR_PUSH_TIMEOUT", 0.2)
    google.pause = 2.0
    made = await book(team, team.admin, meeting(person, start=TEN_KL))
    assert made.status_code == 201, made.text
    mail = relay.for_(staff_address(team))
    assert mail.calendar is not None and mail.calendar.method == "REQUEST"


def test_the_assistant_books_through_the_same_after_commit_hook() -> None:
    source = Path(REPO_ROOT / "csa" / "bookings" / "executor.py").read_text(encoding="utf-8")
    assert "bookings_api.after_commit(" in source
    assert "notify_after_commit(" not in source


async def test_a_meet_link_made_before_the_mail_is_in_it_and_in_the_answer(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    relay: FakeTransport,
) -> None:
    await connect(team, google)
    sessions.app.state.calendar_push = CalendarPush(
        sessions.pool, sessions.app.state.calendar_connections, google, meet=True
    )
    body = meeting(
        person, start=TEN_KL, location_kind="online", guests=[{"name": "G", "email": GUEST}]
    )
    made = await booked(team, team.staff, body)
    link = "https://meet.example/abc-defg-hij"
    # What the answer says is what the booking is now, so the page does not offer a retry.
    assert made["meeting_link"] == link and made["meet_missing"] is False and made["version"] == 2
    mine = relay.for_(staff_address(team))
    assert link in mine.text and mine.calendar is None  # the link is there, and so is their event
    guest = relay.for_(GUEST)
    assert link in guest.text and guest.calendar is not None


async def test_a_refused_meet_link_sends_the_mail_and_answers_without_one(
    sessions: SessionWorld,
    team: Team,
    person: dict[str, Any],
    google: FakeGoogle,
    relay: FakeTransport,
) -> None:
    await connect(team, google)
    sessions.app.state.calendar_push = CalendarPush(
        sessions.pool, sessions.app.state.calendar_connections, google, meet=True
    )
    google.meet_refused = True
    made = await booked(team, team.staff, meeting(person, start=TEN_KL, location_kind="online"))
    assert made["meeting_link"] is None and made["meet_missing"] is True and made["version"] == 1
    assert relay.for_(staff_address(team)).calendar is not None  # their event is not yet synced


async def test_a_cancel_still_carries_the_file_to_someone_sent_one_before_a_later_move(
    team: Team, person: dict[str, Any], google: FakeGoogle, relay: FakeTransport
) -> None:
    await connect(team, google)
    google.fail = "provider_unreachable"  # the invitation went with a file
    made = await booked(team, team.admin, meeting(person, start=TEN_KL, title="Third"))
    assert relay.for_(staff_address(team)).calendar is not None
    google.fail = None
    relay.sent.clear()
    moved = await later(team, made)  # this time their event is synced, so no file
    assert moved.status_code == 200, moved.text
    assert relay.for_(staff_address(team)).calendar is None
    relay.sent.clear()
    version = moved.json()["version"]
    await team.send(team.admin, f"/bookings/{made['id']}/cancel", {"version": version})
    mail = relay.for_(staff_address(team))
    # The first invitation's file may have put an event on their calendar that we cannot remove.
    assert mail.calendar is not None and mail.calendar.method == "CANCEL"
