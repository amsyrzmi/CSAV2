"""The calendar file (iCalendar, RFC 5545) that goes with a booking email.

One file per recipient, with only that recipient as `ATTENDEE`, so a customer never learns a staff
or guest address. `ORGANIZER` is the mailer's sender address. Every text value is escaped and every
control character is removed, so a title, address or name can never add a property.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

if TYPE_CHECKING:
    from csa.bookings.service import BookingRecord

Method = Literal["REQUEST", "CANCEL"]

UID_DOMAIN = "fourstacks.my"
PRODID = "-//FourStacks//Bookings//EN"
FOLD_AT = 75  # octets in a physical line, not counting the CRLF
PHONE_PLACE = "By phone"
_STATUS = {"pending": "TENTATIVE"}


def map_links(address: str) -> tuple[str, str]:
    """The Google Maps and Waze search links for an address."""
    query = quote(address, safe="")
    return (
        f"https://www.google.com/maps/search/?api=1&query={query}",
        f"https://waze.com/ul?q={query}&navigate=yes",
    )


def _plain(value: str) -> str:
    """The value with every control, format and line-separator character turned into a space,
    except a line break, which stays `\\n` for `_text` to escape."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(" " if _odd(ch) and ch != "\n" else ch for ch in value)


def _odd(ch: str) -> bool:
    return unicodedata.category(ch)[0] == "C" or unicodedata.category(ch) in ("Zl", "Zp")


def _text(value: str) -> str:
    """A TEXT value: backslash, semicolon, comma and line breaks escaped."""
    return (
        _plain(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _param(value: str) -> str:
    """A quoted parameter value: no quote, no control character, and none of `:;,` can end it."""
    cleaned = " ".join(_plain(value).replace('"', "").replace("\n", " ").split())
    return f'"{cleaned}"'


def _fold(line: str) -> str:
    """Fold at 75 octets, breaking only between characters. Continuation lines start with a space,
    which counts toward their 75."""
    parts: list[str] = []
    current = ""
    size = 0
    limit = FOLD_AT
    for ch in line:
        width = len(ch.encode())
        if size + width > limit:
            parts.append(current)
            current, size, limit = " ", 1, FOLD_AT
        current += ch
        size += width
    parts.append(current)
    return "\r\n".join(parts)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _mailbox(address: str) -> str:
    """A bare address for `mailto:`. Anything that could end the line or the value is refused."""
    if not address or any(_odd(ch) or ch.isspace() or ch in '<>,;"' for ch in address):
        raise ValueError("not a plain email address")
    return address


def _address_of(sender: str) -> str:
    """The bare address of "Name <address>", so a person's own address is never the organizer."""
    return _mailbox(parseaddr(sender)[1])


def invitation(
    booking: BookingRecord,
    organizer: str,
    method: Method,
    *,
    attendee: str,
    attendee_name: str | None = None,
    stamp: datetime | None = None,
) -> str:
    """The `.ics` text for one recipient: an invitation (`REQUEST`) or its removal (`CANCEL`).

    `UID` is the booking's id, so a later file replaces the earlier one; `SEQUENCE` is the
    booking's `version`, which every move, confirmation and cancellation raises.
    """
    cancelled = method == "CANCEL"
    status = "CANCELLED" if cancelled else _STATUS.get(booking.status, "CONFIRMED")
    link = booking.meeting_link
    if link is not None and not (link.startswith("https://") and link.isprintable()):
        link = None
    place: str | None = None
    notes: list[str] = []
    if booking.location_kind == "in_person" and booking.address:
        place = booking.address
        maps, waze = map_links(booking.address)
        notes = [f"Google Maps: {maps}", f"Waze: {waze}"]
    elif booking.location_kind == "online" and link:
        place = link
        notes = [f"Join: {link}"]
    elif booking.location_kind == "phone":
        place = PHONE_PLACE
    who = "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=FALSE"
    if attendee_name:
        who += f";CN={_param(attendee_name)}"
    content = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{booking.id}@{UID_DOMAIN}",
        f"DTSTAMP:{_utc(stamp or datetime.now(UTC))}",
        f"DTSTART:{_utc(booking.starts_at)}",
        f"DTEND:{_utc(booking.ends_at)}",
        f"SEQUENCE:{booking.version}",
        f"STATUS:{status}",
        "TRANSP:OPAQUE",
        f"SUMMARY:{_text(booking.title or 'Booking')}",
        *([f"LOCATION:{_text(place)}"] if place else []),
        *([f"URL:{link}"] if link and booking.location_kind == "online" else []),
        *([f"DESCRIPTION:{_text(chr(10).join(notes))}"] if notes else []),
        f"ORGANIZER:mailto:{_address_of(organizer)}",
        f"{who}:mailto:{_mailbox(attendee)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "".join(f"{_fold(line)}\r\n" for line in content)
