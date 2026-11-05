"""The calendar file for a booking: structure, escaping, folding, and what it never says."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from csa.bookings import ics
from csa.bookings.contracts import BookingCustomerOut, BookingGuestOut
from csa.bookings.service import Attendee, BookingRecord

BOOKING_ID = UUID("11111111-2222-3333-4444-555555555555")
CLIENT_ID = UUID("99999999-2222-3333-4444-555555555555")
ORGANIZER = "FourStacks <no-reply@fourstacks.my>"
ADDRESS = "12 Jalan Contoh, Kuala Lumpur"


def record(**changes: object) -> BookingRecord:
    fields: dict[str, object] = {
        "id": BOOKING_ID,
        "client_id": CLIENT_ID,
        "status": "confirmed",
        "kind": "appointment",
        "title": "Haircut",
        "private": False,
        "starts_at": datetime(2031, 3, 4, 10, 0, tzinfo=UTC),
        "ends_at": datetime(2031, 3, 4, 10, 30, tzinfo=UTC),
        "version": 1,
        "type_id": None,
        "created_by": None,
        "location_kind": "in_person",
        "address": ADDRESS,
        "meeting_link": None,
        "customer": BookingCustomerOut(
            id=UUID(int=7), full_name="Synthetic Customer", phone=None, email="c@example.test"
        ),
        "people": (Attendee(UUID(int=1), "Aina", True, None, "aina@example.test", None),),
        "resources": (),
        "guests": (BookingGuestOut(name="Guest", email="guest@example.test"),),
        "conflict": False,
    }
    return BookingRecord(**{**fields, **changes})  # type: ignore[arg-type]


def invite(booking: BookingRecord | None = None, method: str = "REQUEST", **extra: object) -> str:
    return ics.invitation(
        booking or record(),
        ORGANIZER,
        method,  # type: ignore[arg-type]
        attendee="c@example.test",
        **extra,  # type: ignore[arg-type]
    )


def lines(text: str) -> list[str]:
    """The unfolded content lines. Every physical line must end in CRLF."""
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")
    assert "\r" not in text.replace("\r\n", "")
    return re.sub(r"\r\n[ \t]", "", text).split("\r\n")[:-1]


def name_of(line: str) -> str:
    return re.split(r"[:;]", line, maxsplit=1)[0]


def prop(text: str, name: str) -> list[str]:
    """Each property called `name`: the value after the colon that ends its name and parameters."""
    found = []
    for line in lines(text):
        if name_of(line) == name:
            found.append(line.split(":", 1)[1])
    return found


def unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def test_a_request_is_a_valid_event_in_utc() -> None:
    text = invite(record(version=3))
    assert lines(text)[:2] == ["BEGIN:VCALENDAR", "VERSION:2.0"]
    assert lines(text)[-1] == "END:VCALENDAR"
    assert prop(text, "METHOD") == ["REQUEST"]
    assert prop(text, "UID") == [f"{BOOKING_ID}@fourstacks.my"]
    assert prop(text, "SEQUENCE") == ["3"]
    assert prop(text, "DTSTART") == ["20310304T100000Z"]
    assert prop(text, "DTEND") == ["20310304T103000Z"]
    assert re.fullmatch(r"\d{8}T\d{6}Z", prop(text, "DTSTAMP")[0])
    assert prop(text, "STATUS") == ["CONFIRMED"]
    assert prop(text, "SUMMARY") == ["Haircut"]


def test_a_time_with_an_offset_is_written_in_utc() -> None:
    kl = timezone(timedelta(hours=8))
    text = invite(
        record(
            starts_at=datetime(2031, 3, 4, 18, 0, tzinfo=kl),
            ends_at=datetime(2031, 3, 4, 19, 0, tzinfo=kl),
        )
    )
    assert prop(text, "DTSTART") == ["20310304T100000Z"]
    assert prop(text, "DTEND") == ["20310304T110000Z"]


def test_a_pending_booking_is_tentative() -> None:
    assert prop(invite(record(status="pending")), "STATUS") == ["TENTATIVE"]


def test_long_lines_are_folded_at_75_octets_without_splitting_a_character() -> None:
    long_title = "Pertemuan semula é中文 " * 20
    text = invite(record(title=long_title, address="Jalan " + "panjang, " * 40))
    physical = text.split("\r\n")[:-1]
    assert all(len(line.encode()) <= 75 for line in physical), [
        len(line.encode()) for line in physical
    ]
    assert any(line.startswith(" ") for line in physical)
    # Unfolding gives back the whole title: nothing was cut and no character was split.
    assert unescape(prop(text, "SUMMARY")[0]) == long_title
    text.encode().decode("utf-8")


def test_awkward_text_stays_valid() -> None:
    nasty = "A, B; C \\ D\nE\r\nATTENDEE:mailto:evil@example.test\r\nX-EVIL:1"
    text = ics.invitation(
        record(title=nasty, address=nasty, guests=(BookingGuestOut(name=nasty, email="g@e.test"),)),
        ORGANIZER,
        "REQUEST",
        attendee="g@example.test",
        attendee_name=nasty,
    )
    names = [name_of(line) for line in lines(text)]
    # Nothing was added: one ATTENDEE, no X-EVIL, and every line is a property we write.
    assert names.count("ATTENDEE") == 1
    known = {
        "BEGIN", "END", "VERSION", "PRODID", "CALSCALE", "METHOD", "UID", "DTSTAMP", "DTSTART",
        "DTEND", "SEQUENCE", "STATUS", "SUMMARY", "LOCATION", "DESCRIPTION", "ORGANIZER",
        "ATTENDEE", "TRANSP", "URL",
    }  # fmt: skip
    assert set(names) <= known, set(names) - known
    for field in ("SUMMARY", "LOCATION"):
        value = prop(text, field)[0]
        assert "\\n" in value and "\\," in value and "\\;" in value and "\\\\" in value
    assert prop(text, "ATTENDEE")[0].endswith("mailto:g@example.test")
    # The name sits inside one quoted parameter; outside it there is exactly one address.
    attendee = lines(text)[[name_of(x) for x in lines(text)].index("ATTENDEE")]
    assert attendee.count('"') == 2
    assert re.sub(r'"[^"]*"', "", attendee).count("mailto:") == 1


def test_the_file_names_only_the_recipient_and_the_senders_address() -> None:
    text = invite()
    assert prop(text, "ORGANIZER") == ["mailto:no-reply@fourstacks.my"]
    attendees = prop(text, "ATTENDEE")
    assert len(attendees) == 1 and attendees[0].endswith("mailto:c@example.test")
    for other in ("aina@example.test", "guest@example.test", "Aina", "Guest", "Synthetic"):
        assert other not in text


def test_a_cancel_keeps_the_uid_and_raises_the_sequence() -> None:
    first = invite(record(version=1))
    gone = invite(record(version=3, status="cancelled"), "CANCEL")
    assert prop(gone, "METHOD") == ["CANCEL"]
    assert prop(gone, "STATUS") == ["CANCELLED"]
    assert prop(gone, "UID") == prop(first, "UID")
    assert int(prop(gone, "SEQUENCE")[0]) > int(prop(first, "SEQUENCE")[0])


def test_an_online_type_carries_the_link_and_an_in_person_type_the_address() -> None:
    online = invite(
        record(location_kind="online", address=None, meeting_link="https://meet.example.test/r-1")
    )
    assert prop(online, "URL") == ["https://meet.example.test/r-1"]
    assert "https://meet.example.test/r-1" in prop(online, "LOCATION")[0]
    here = invite()
    assert unescape(prop(here, "LOCATION")[0]) == ADDRESS
    assert prop(here, "URL") == []
    maps, waze = ics.map_links(ADDRESS)
    description = unescape(prop(here, "DESCRIPTION")[0])
    assert maps in description and waze in description


def test_a_phone_booking_has_no_place() -> None:
    text = invite(record(location_kind="phone", address=None))
    assert prop(text, "URL") == []
    assert unescape(prop(text, "LOCATION")[0]) == "By phone"


def test_a_link_that_is_not_https_is_left_out() -> None:
    text = invite(record(location_kind="online", address=None, meeting_link="javascript:alert(1)"))
    assert "javascript" not in text


def test_map_links_encode_the_address() -> None:
    place = "Kedai A&B, #5 Jalan é ?x=1"
    maps, waze = ics.map_links(place)
    google = urlparse(maps)
    assert (google.scheme, google.netloc, google.path) == (
        "https",
        "www.google.com",
        "/maps/search/",
    )
    assert parse_qs(google.query) == {"api": ["1"], "query": [place]}
    w = urlparse(waze)
    assert (w.scheme, w.netloc, w.path) == ("https", "waze.com", "/ul")
    assert parse_qs(w.query)["q"] == [place]
    for link in (maps, waze):
        assert " " not in link and "\n" not in link
