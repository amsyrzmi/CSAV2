"""Email with a calendar file after a staff change to a booking (stage 1, PR 1, Task 5)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from csa.bookings.api import build_bookings_router
from csa.console.mailer import Mailer, OutgoingMail
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_bookings_api import TUESDAY, at, book, booked, meeting
from tests.test_bookings_setup import BASE, Team, make_person, make_type
from tests.test_console_sessions import SessionWorld
from tests.test_customer_replies import activate_only

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

SENDER = "FourStacks <no-reply@fourstacks.my>"
BRAND = "Kedai Mawar Synthetic"
ADDRESS = "12 Jalan Contoh, Kuala Lumpur"
# 02:00 UTC is 10:00 in Kuala Lumpur, inside the default working day.
TEN_KL = at(2)


class FakeTransport:
    """Keeps every message; refuses the addresses in `fail` and, with `explode`, all of them."""

    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []
        self.fail: set[str] = set()
        self.explode: Exception | None = None

    def send(self, mail: OutgoingMail, sender: str) -> None:
        if self.explode is not None or mail.to in self.fail:
            raise self.explode or RuntimeError(f"refused {mail.to}")
        self.sent.append(mail)

    def to(self) -> list[str]:
        return sorted(m.to for m in self.sent)

    def for_(self, address: str) -> OutgoingMail:
        (mail,) = [m for m in self.sent if m.to == address]
        return mail


@pytest.fixture(autouse=True)
async def prepared(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_bookings_router())
    for tenant in sessions.tenants:
        await activate_only(sessions, tenant, "bookings")
    await sessions.q(
        "UPDATE clients SET timezone = 'Asia/Kuala_Lumpur', brand_name = %s WHERE id = %s",
        BRAND,
        sessions.tenants[0],
    )


@pytest.fixture
async def team(sessions: SessionWorld) -> Team:
    return await Team(sessions).open()


@pytest.fixture
def relay(sessions: SessionWorld) -> FakeTransport:
    transport = FakeTransport()
    sessions.app.state.mailer = Mailer(transport, SENDER)
    return transport


def calendar_of(mail: OutgoingMail) -> str:
    assert mail.calendar is not None
    return mail.calendar.content


def unfolded(text: str) -> str:
    return re.sub(r"\r\n[ \t]", "", text)


def ics_value(mail: OutgoingMail, name: str) -> list[str]:
    found = []
    for line in unfolded(calendar_of(mail)).split("\r\n"):
        if re.split(r"[:;]", line, maxsplit=1)[0] == name:
            found.append(line.split(":", 1)[1])
    return found


def everything(mail: OutgoingMail) -> str:
    return "\n".join([mail.to, mail.subject, mail.text, mail.html, calendar_of(mail)])


async def send(t: Team, who: Any, path: str, body: Any = None, method: str = "POST") -> Any:
    return await t.send(who, path, body, method)


# --- who gets one ----------------------------------------------------------------------------


async def test_a_customer_staff_and_guests_each_get_one(
    sessions: SessionWorld, team: Team, relay: FakeTransport
) -> None:
    staff_email = team.staff_info["user"]["email"]
    admin_email = team.admin_info["user"]["email"]
    own = await make_person(team, "Aina Own", email="aina@example.test")
    account = await make_person(team, "Staff Account", user_id=team.staff_info["user"]["id"])
    nobody = await make_person(team, "No Address")
    made = await booked(
        team,
        team.admin,
        meeting(
            own,
            account,
            nobody,
            start=TEN_KL,
            location_kind="in_person",
            address=ADDRESS,
            walk_in={"name": "Customer <One>", "email": "customer@example.test"},
            guests=[
                {"name": "Outside Guest", "email": "guest@example.test"},
                {"email": "Guest@Example.test"},  # the same address again: kept once
            ],
        ),
    )
    assert made["email"] == "sent"
    # The Admin who made it is not an attendee, so gets nothing; nobody else does either.
    assert relay.to() == sorted(
        ["aina@example.test", staff_email, "customer@example.test", "guest@example.test"]
    )
    assert admin_email not in relay.to()
    for mail in relay.sent:
        file = unfolded(calendar_of(mail))
        assert ics_value(mail, "METHOD") == ["REQUEST"]
        assert mail.calendar is not None and mail.calendar.method == "REQUEST"
        assert ics_value(mail, "UID") == [f"{made['id']}@fourstacks.my"]
        assert ics_value(mail, "DTSTART") == ["20310304T020000Z"]
        assert ics_value(mail, "ORGANIZER") == ["mailto:no-reply@fourstacks.my"]
        (attendee,) = ics_value(mail, "ATTENDEE")
        assert attendee.endswith(f"mailto:{mail.to}")
        # Each file and message names this recipient and nobody else.
        for other in relay.to():
            if other != mail.to:
                assert other not in everything(mail), (mail.to, other)
        assert BRAND in mail.text and BRAND in mail.html
        assert "4 Mar 2031, 10:00 to 10:30" in mail.text  # the company's own time
        assert "Asia/Kuala_Lumpur" in mail.text
        assert ADDRESS in mail.text
        assert "BEGIN:VEVENT" in file
    # A name with markup is escaped in the HTML, and said plainly in the text.
    customer = relay.for_("customer@example.test")
    assert "Hello Customer <One>," in customer.text
    assert "Customer &lt;One&gt;" in customer.html and "<One>" not in customer.html
    assert "Customer" not in relay.for_("guest@example.test").text
    # One booking, one row, nothing changed by sending.
    rows = await sessions.q("SELECT status, version FROM bookings WHERE id=%s", made["id"])
    assert rows == [{"status": "confirmed", "version": 1}]


async def test_an_attendee_who_made_the_change_gets_one(team: Team, relay: FakeTransport) -> None:
    mine = await make_person(team, "Staff Person", user_id=team.staff_info["user"]["id"])
    made = await booked(team, team.staff, meeting(mine, start=TEN_KL))
    assert made["email"] == "sent"
    assert relay.to() == [team.staff_info["user"]["email"]]


async def test_nobody_without_an_address_gets_one(
    sessions: SessionWorld, team: Team, relay: FakeTransport
) -> None:
    quiet = await make_person(team, "No Address")
    made = await booked(
        team,
        team.staff,
        meeting(quiet, start=TEN_KL, walk_in={"name": "Phone Only", "phone": "0123456789"}),
    )
    assert made["email"] == "none"
    assert relay.sent == []
    # An address that is not one (a contact imported with free text) is no address.
    await sessions.q("UPDATE contacts SET email = 'call me' WHERE full_name = 'Phone Only'")
    moved = await send(
        team, team.staff, f"/bookings/{made['id']}", {"version": 1, "starts_at": at(3)}, "PATCH"
    )
    assert moved.status_code == 200 and moved.json()["email"] == "none"
    assert relay.sent == []


async def test_replies_off_still_emails_on_a_staff_action(
    sessions: SessionWorld, team: Team, relay: FakeTransport
) -> None:
    await sessions.q(
        "UPDATE clients SET config = jsonb_set(coalesce(config, '{}'::jsonb), '{policy}',"
        ' \'{"deployment_mode": "shadow"}\'::jsonb) WHERE id = %s',
        sessions.tenants[0],
    )
    person = await make_person(team, "Aina", email="aina@example.test")
    made = await booked(
        team,
        team.staff,
        meeting(person, start=TEN_KL, walk_in={"name": "Customer", "email": "c@example.test"}),
    )
    assert made["email"] == "sent"
    assert relay.to() == ["aina@example.test", "c@example.test"]


# --- how it can go wrong ---------------------------------------------------------------------


async def test_a_failed_send_keeps_the_booking(
    sessions: SessionWorld, team: Team, relay: FakeTransport
) -> None:
    person = await make_person(team, "Aina", email="aina@example.test")
    other = await make_person(team, "Bakar", email="bakar@example.test")
    relay.fail = {"bakar@example.test"}
    with capture_logs() as logs:
        made = await send(
            team,
            team.staff,
            "/bookings",
            {**meeting(person, other, start=TEN_KL, title="Secret Title Words")},
        )
    assert made.status_code == 201, made.text
    assert made.json()["email"] == "failed"
    assert relay.to() == ["aina@example.test"]  # the one that could go, went
    rows = await sessions.q("SELECT status FROM bookings WHERE id=%s", made.json()["id"])
    assert rows == [{"status": "confirmed"}]
    # The log names a category, never an address or a title.
    text = repr(logs)
    for secret in ("example.test", "Secret Title"):
        assert secret not in text
    # Every sort of relay failure is the same to the caller.
    relay.explode = TimeoutError("relay at bakar@example.test is slow")
    again = await send(team, team.staff, "/bookings", meeting(person, start=at(3)))
    assert again.status_code == 201 and again.json()["email"] == "failed"


@pytest.mark.parametrize("mailer", ["off", "absent"])
async def test_mail_off_keeps_the_booking(sessions: SessionWorld, team: Team, mailer: str) -> None:
    sessions.app.state.mailer = Mailer(None, SENDER) if mailer == "off" else None
    person = await make_person(team, "Aina", email="aina@example.test")
    made = await send(team, team.staff, "/bookings", meeting(person, start=TEN_KL))
    assert made.status_code == 201, made.text
    assert made.json()["email"] == "off"
    rows = await sessions.q("SELECT status FROM bookings WHERE id=%s", made.json()["id"])
    assert rows == [{"status": "confirmed"}]
    # With nobody to tell, it is "none", even with mail off.
    quiet = await make_person(team, "No Address")
    silent = await send(team, team.staff, "/bookings", meeting(quiet, start=at(3)))
    assert silent.json()["email"] == "none"


async def test_a_calendar_read_carries_no_email_word(team: Team, relay: FakeTransport) -> None:
    person = await make_person(team, "Aina", email="aina@example.test")
    await booked(team, team.staff, meeting(person, start=TEN_KL))
    shown = await team.staff.get(
        f"{BASE}/calendar?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    )
    assert [item["email"] for item in shown.json()["items"]] == [None]


# --- moving, cancelling, confirming ----------------------------------------------------------


async def test_moving_updates_and_cancelling_removes_the_invitation(
    team: Team, relay: FakeTransport
) -> None:
    person = await make_person(team, "Aina", email="aina@example.test")
    made = await booked(
        team,
        team.staff,
        meeting(
            person,
            start=TEN_KL,
            walk_in={"name": "Customer", "email": "c@example.test"},
            location_kind="online",
            meeting_link="https://meet.example.test/room-1",
        ),
    )
    first = relay.for_("c@example.test")
    assert first.subject.startswith("Booked: Weekly sync")
    relay.sent.clear()

    moved = await send(
        team, team.staff, f"/bookings/{made['id']}", {"version": 1, "starts_at": at(3)}, "PATCH"
    )
    assert moved.status_code == 200 and moved.json()["email"] == "sent"
    assert relay.to() == ["aina@example.test", "c@example.test"]
    update = relay.for_("c@example.test")
    assert update.subject.startswith("Moved: Weekly sync")
    assert "has moved to a new time" in update.text
    assert ics_value(update, "METHOD") == ["REQUEST"]
    assert ics_value(update, "UID") == ics_value(first, "UID")
    assert ics_value(update, "DTSTART") == ["20310304T030000Z"]
    assert int(ics_value(update, "SEQUENCE")[0]) > int(ics_value(first, "SEQUENCE")[0])
    assert "https://meet.example.test/room-1" in update.text
    assert ics_value(update, "URL") == ["https://meet.example.test/room-1"]
    relay.sent.clear()

    gone = await send(team, team.staff, f"/bookings/{made['id']}/cancel", {"version": 2})
    assert gone.status_code == 200 and gone.json()["email"] == "sent"
    assert relay.to() == ["aina@example.test", "c@example.test"]
    removal = relay.for_("c@example.test")
    assert removal.subject.startswith("Cancelled: Weekly sync")
    assert "has been cancelled" in removal.text
    assert removal.calendar is not None and removal.calendar.method == "CANCEL"
    assert ics_value(removal, "METHOD") == ["CANCEL"]
    assert ics_value(removal, "STATUS") == ["CANCELLED"]
    assert ics_value(removal, "UID") == ics_value(first, "UID")
    assert int(ics_value(removal, "SEQUENCE")[0]) > int(ics_value(update, "SEQUENCE")[0])


async def test_a_pending_booking_tells_the_customer_nothing_until_it_is_confirmed(
    team: Team, relay: FakeTransport
) -> None:
    person = await make_person(team, "Aina", email="aina@example.test")
    kind = await make_type(
        team,
        needs_confirmation=True,
        delivery="named",
        person_ids=[person["id"]],
        location_kind="in_person",
        address=ADDRESS,
    )
    ask = {
        "type_id": kind["id"],
        "people": [person["id"]],
        "walk_in": {"name": "Customer", "email": "c@example.test"},
    }
    pending = await booked(team, team.staff, {**ask, "starts_at": TEN_KL})
    assert pending["status"] == "pending"
    assert relay.to() == ["aina@example.test"]  # the person delivering it, not the customer
    assert ics_value(relay.sent[0], "STATUS") == ["TENTATIVE"]
    assert "waiting for confirmation" in relay.sent[0].text
    relay.sent.clear()

    confirmed = await send(team, team.staff, f"/bookings/{pending['id']}/confirm", {"version": 1})
    assert confirmed.status_code == 200 and confirmed.json()["email"] == "sent"
    assert relay.to() == ["aina@example.test", "c@example.test"]
    for mail in relay.sent:
        assert ics_value(mail, "METHOD") == ["REQUEST"]
        assert ics_value(mail, "STATUS") == ["CONFIRMED"]
        assert ics_value(mail, "SEQUENCE") == ["2"]
        assert ics_value(mail, "UID") == [f"{pending['id']}@fourstacks.my"]
    relay.sent.clear()

    other = await booked(team, team.staff, {**ask, "starts_at": at(4)})
    relay.sent.clear()
    declined = await send(team, team.admin, f"/bookings/{other['id']}/decline")
    assert declined.status_code == 200 and declined.json()["email"] == "sent"
    assert relay.to() == ["aina@example.test", "c@example.test"]
    assert all(m.calendar and m.calendar.method == "CANCEL" for m in relay.sent)


async def test_a_no_show_sends_nothing(
    sessions: SessionWorld, team: Team, relay: FakeTransport
) -> None:
    person = await make_person(team, "Aina", email="aina@example.test")
    past = await booked(team, team.staff, meeting(person, start="2020-01-07T02:00:00Z"))
    relay.sent.clear()
    marked = await send(team, team.staff, f"/bookings/{past['id']}/no-show", {"version": 1})
    assert marked.status_code == 200 and marked.json()["status"] == "no_show"
    assert marked.json()["email"] is None
    assert relay.sent == []


# --- private meetings, and what the words say --------------------------------------------------


async def test_a_private_meeting_mails_only_its_attendees(
    sessions: SessionWorld, team: Team, relay: FakeTransport
) -> None:
    attendee = await make_person(team, "Staff Attendee", user_id=team.staff_info["user"]["id"])
    colleague = await make_person(team, "Colleague", email="colleague@example.test")
    quiet = await make_person(team, "No Address")
    made = await booked(
        team,
        team.staff,
        meeting(
            attendee,
            colleague,
            quiet,
            start=TEN_KL,
            title="Redundancy talks",
            private=True,
            walk_in={"name": "A Customer", "email": "customer@example.test"},
            guests=[{"name": "Adviser", "email": "adviser@example.test"}],
        ),
    )
    attendees = sorted(
        [team.staff_info["user"]["email"], "colleague@example.test", "adviser@example.test"]
    )
    assert relay.to() == attendees
    relay.sent.clear()

    # An Admin who is not part of it moves it, then cancels it. They are not mailed, and neither is
    # anyone who is not an attendee. What they get back stays redacted and says nothing of the mail.
    # With mail on, an invitation is mailed instead of returned, so make the Admin with it off.
    sessions.app.state.mailer = None
    second_admin, second_info = await team.w.member(role="tenant_admin")
    sessions.app.state.mailer = Mailer(relay, SENDER)
    moved = await send(
        team, second_admin, f"/bookings/{made['id']}", {"version": 1, "starts_at": at(3)}, "PATCH"
    )
    assert moved.status_code == 200, moved.text
    assert relay.to() == attendees
    cancelled = await send(team, second_admin, f"/bookings/{made['id']}/cancel")
    assert cancelled.status_code == 200
    assert relay.to() == sorted(attendees * 2)
    assert second_info["user"]["email"] not in "".join(m.to for m in relay.sent)
    for response in (moved, cancelled):
        body = response.json()
        assert body["redacted"] is True and body["title"] == "Busy" and body["email"] is None
        for secret in ("Redundancy", "example.test", "Colleague", "Adviser", "Jalan"):
            assert secret not in response.text
    # The attendees' own mail does say what it is.
    assert all("Redundancy talks" in m.text for m in relay.sent)


async def test_an_in_person_mail_has_map_links(team: Team, relay: FakeTransport) -> None:
    person = await make_person(team, "Aina", email="aina@example.test")
    await booked(
        team,
        team.staff,
        meeting(
            person, start=TEN_KL, location_kind="in_person", address="Kedai A&B, #5 Jalan Ujian"
        ),
    )
    (mail,) = relay.sent
    maps = "https://www.google.com/maps/search/?api=1&query=Kedai%20A%26B%2C%20%235%20Jalan%20Ujian"
    waze = "https://waze.com/ul?q=Kedai%20A%26B%2C%20%235%20Jalan%20Ujian&navigate=yes"
    assert f"Google Maps: {maps}" in mail.text and f"Waze: {waze}" in mail.text
    assert f'href="{maps.replace("&", "&amp;")}"' in mail.html
    assert f'href="{waze.replace("&", "&amp;")}"' in mail.html
    assert "Kedai A&amp;B, #5 Jalan Ujian" in mail.html
    # An online or phone booking has no map links.
    await booked(team, team.staff, meeting(person, start=at(3), location_kind="phone"))
    assert "Maps" not in relay.sent[-1].text and "By phone" in relay.sent[-1].text


async def test_awkward_text_cannot_break_the_message_or_the_file(
    team: Team, relay: FakeTransport
) -> None:
    person = await make_person(team, 'Aina, "The"; Boss', email="aina@example.test")
    title = "<script>x</script>, a; b \\ c & d"
    await booked(
        team,
        team.staff,
        meeting(
            person,
            start=TEN_KL,
            title=title,
            location_kind="in_person",
            address="<b>Unit 1</b>, Jalan A; Blok B",
        ),
    )
    (mail,) = relay.sent
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html
    assert "<b>" not in mail.html
    assert "\n" not in mail.subject and "\r" not in mail.subject
    assert ics_value(mail, "SUMMARY") == ["<script>x</script>\\, a\\; b \\\\ c & d"]
    names = [
        re.split(r"[:;]", line, maxsplit=1)[0] for line in unfolded(calendar_of(mail)).split("\r\n")
    ]
    assert names.count("ATTENDEE") == 1 and names.count("SUMMARY") == 1


async def test_a_refused_booking_sends_nothing(team: Team, relay: FakeTransport) -> None:
    person = await make_person(team, "Aina", email="aina@example.test")
    await booked(team, team.staff, meeting(person, start=TEN_KL))
    relay.sent.clear()
    clash = await book(team, team.staff, meeting(person, start=TEN_KL))
    assert clash.status_code == 409
    assert relay.sent == []
