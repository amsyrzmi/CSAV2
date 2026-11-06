"""Invitation, reset and HR-owner email copy: plain text plus a minimal HTML part.

Copy follows docs/WRITING_GUIDE.md. Every value is escaped in the HTML part, and the
subject is kept to one line so a business name can never add a header.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from csa.bookings.ics import map_links
from csa.console.mailer import CalendarFile, OutgoingMail

if TYPE_CHECKING:
    from csa.bookings.service import BookingRecord

# Password resets have no tenant, so their times use the console's own zone.
RESET_TIMEZONE = "Asia/Kuala_Lumpur"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_BODY = (
    "margin:0;padding:24px;font-family:Arial,Helvetica,sans-serif;font-size:15px;"
    "line-height:1.5;color:#1f2933;"
)
_BUTTON = (
    "display:inline-block;padding:10px 18px;background:#1f2933;color:#ffffff;"
    "text-decoration:none;border-radius:6px;"
)


def when(moment: datetime, tz: str) -> str:
    """Format as in the writing guide, for example ``27 Oct 2026, 09:15``."""
    local = moment.astimezone(ZoneInfo(tz))
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}, {local:%H:%M}"


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _html(paragraphs: list[str]) -> str:
    body = "".join(f"<p>{paragraph}</p>\n" for paragraph in paragraphs)
    return f'<!DOCTYPE html>\n<html lang="en">\n<body style="{_BODY}">\n{body}</body>\n</html>\n'


def _link_paragraphs(link: str, action: str) -> list[str]:
    href = escape(link)
    return [
        f'<a href="{href}" style="{_BUTTON}">{action}</a>',
        f"If the button does not work, copy this link into your browser:<br>{href}",
    ]


def invitation_mail(
    *,
    to: str,
    invitee_name: str,
    inviter_name: str,
    business_name: str,
    link: str,
    expires_at: datetime,
    tz: str,
) -> OutgoingMail:
    expiry = when(expires_at, tz)
    joined = f"{inviter_name} has invited you to join {business_name} on FourStacks."
    expires = f"The link expires on {expiry}. After that, ask {inviter_name} for a new invitation."
    unexpected = (
        "If you did not expect this invitation, you can ignore this email. "
        "Nothing changes until you open the link and set a password."
    )
    text = (
        f"Hello {invitee_name},\n\n{joined}\n\n"
        f"Open this link to set your password and sign in:\n{link}\n\n"
        f"{expires}\n\n{unexpected}\n\nFourStacks\n"
    )
    html = _html(
        [
            f"Hello {escape(invitee_name)},",
            f"{escape(inviter_name)} has invited you to join "
            f"<strong>{escape(business_name)}</strong> on FourStacks.",
            *_link_paragraphs(link, "Accept invitation"),
            escape(expires),
            escape(unexpected),
            "FourStacks",
        ]
    )
    subject = f"You're invited to {_one_line(business_name)} on FourStacks"
    return OutgoingMail(to=to, subject=subject, text=text, html=html)


def reset_mail(*, to: str, name: str, link: str, expires_at: datetime) -> OutgoingMail:
    started = "A FourStacks administrator has started a password reset for your account."
    expires = f"The link expires on {when(expires_at, RESET_TIMEZONE)}."
    unexpected = (
        "If you did not expect this email, you can ignore it. "
        "Your password stays the same until you choose a new one."
    )
    text = (
        f"Hello {name},\n\n{started}\n\n"
        f"Open this link to choose a new password:\n{link}\n\n"
        f"{expires}\n\n{unexpected}\n\nFourStacks\n"
    )
    html = _html(
        [
            f"Hello {escape(name)},",
            started,
            *_link_paragraphs(link, "Choose a new password"),
            expires,
            unexpected,
            "FourStacks",
        ]
    )
    return OutgoingMail(to=to, subject="Reset your FourStacks password", text=text, html=html)


def owner_moved_mail(
    *,
    to: str,
    name: str,
    business_name: str,
    actor_name: str,
    old_owner_name: str | None,
    new_owner_name: str,
    moved_at: datetime,
    tz: str,
) -> OutgoingMail:
    """No link and no HR data: this only says who holds the title now."""
    moved = (
        f"{actor_name} made {new_owner_name} the HR owner of {business_name} "
        f"on {when(moved_at, tz)}"
    )
    change = f"{moved}." if old_owner_name is None else f"{moved}, replacing {old_owner_name}."
    power = "The HR owner controls who has HR access and can see sensitive employee details."
    text = f"Hello {name},\n\n{change}\n\n{power}\n\nFourStacks\n"
    html = _html([f"Hello {escape(name)},", escape(change), power, "FourStacks"])
    subject = f"HR owner changed for {_one_line(business_name)}"
    return OutgoingMail(to=to, subject=subject, text=text, html=html)


BookingChange = Literal["created", "moved", "cancelled"]
_BOOKING_SUBJECT = {"created": "Booked", "moved": "Moved", "cancelled": "Cancelled"}


def _span(start: datetime, end: datetime, tz: str) -> str:
    """``4 Mar 2031, 10:00 to 10:30`` and, across midnight, the end's date too."""
    zone = ZoneInfo(tz)
    first, last = start.astimezone(zone), end.astimezone(zone)
    until = f"{last:%H:%M}" if first.date() == last.date() else when(end, tz)
    return f"{when(start, tz)} to {until}"


def _place_rows(booking: BookingRecord) -> list[tuple[str, str, str | None]]:
    """Where it happens: the address with Maps and Waze links, the meeting link, or the phone."""
    if booking.location_kind == "in_person" and booking.address:
        maps, waze = map_links(booking.address)
        return [("Where", booking.address, None), ("Google Maps", maps, maps), ("Waze", waze, waze)]
    if booking.location_kind == "online" and booking.meeting_link:
        link = booking.meeting_link
        return [("Link", link, link if link.startswith("https://") else None)]
    if booking.location_kind == "phone":
        return [("Where", "By phone", None)]
    return []


def booking_mail(
    *,
    to: str,
    name: str | None,
    brand: str,
    booking: BookingRecord,
    change: BookingChange,
    tz: str,
    calendar: CalendarFile,
) -> OutgoingMail:
    """A booking made, moved or cancelled by staff, with the calendar file attached.

    The place and the Maps and Waze links come from the booking itself, and nothing here says who
    else is on it.
    """
    title = " ".join((booking.title or "Booking").split())
    pending = booking.status == "pending" and change != "cancelled"
    headline = {
        "created": f"{title} with {brand} is booked.",
        "moved": f"{title} with {brand} has moved to a new time.",
        "cancelled": f"{title} with {brand} has been cancelled.",
    }[change]
    if pending:
        headline = f"{title} with {brand} is waiting for confirmation."
    rows: list[tuple[str, str, str | None]] = [
        ("What", title, None),
        ("When", f"{_span(booking.starts_at, booking.ends_at, tz)} ({tz})", None),
        *_place_rows(booking),
    ]
    footer = (
        "Open the attached calendar file to remove it from your calendar."
        if change == "cancelled"
        else "Open the attached calendar file to add it to your calendar."
    )
    hello = f"Hello {name}," if name else "Hello,"
    text = (
        f"{hello}\n\n{headline}\n\n"
        + "".join(f"{label}: {value}\n" for label, value, _ in rows)
        + f"\n{footer}\n\n{brand}\n"
    )
    details = "<br>\n".join(
        f"<strong>{label}:</strong> "
        + (f'<a href="{escape(href)}">{escape(value)}</a>' if href else escape(value))
        for label, value, href in rows
    )
    html = _html([escape(hello), escape(headline), details, escape(footer), escape(brand)])
    subject_title = _BOOKING_SUBJECT[change] if not pending else "Waiting for confirmation"
    subject = f"{subject_title}: {title} on {when(booking.starts_at, tz)} ({_one_line(brand)})"
    return OutgoingMail(to=to, subject=subject, text=text, html=html, calendar=calendar)


def customer_declined_mail(
    *,
    to: str,
    name: str | None,
    brand: str,
    booking: BookingRecord,
    original: BookingRecord | None,
    tz: str,
    invite: bool = True,
) -> OutgoingMail:
    """Plain text, with no calendar file, for a customer whose own bot asked for a booking, or for
    a move of one, that staff did not accept.

    They were never sent an invitation, so there is nothing to remove from a calendar and the
    word "cancelled" would be wrong. With the booking a request would have moved, it says that one
    stands, at its real time. It invites another request only when `invite` says the bot would act
    on one (replies on, Customer support active).
    """
    title = " ".join((booking.title or "Booking").split())
    new = when(booking.starts_at, tz)
    if original is not None:
        headline = (
            f"Your request to move {title} with {brand} to {new} wasn't accepted. "
            f"Your booking stays on {when(original.starts_at, tz)}."
        )
        subject = f"Request not accepted: {title} ({_one_line(brand)})"
    else:
        headline = (
            f"Your request for {title} with {brand} on {new} wasn't confirmed, "
            "so that time isn't booked."
        )
        subject = f"Not confirmed: {title} on {new} ({_one_line(brand)})"
    hello = f"Hello {name}," if name else "Hello,"
    footer = ["You can ask for another time whenever you like."] if invite else []
    text = "\n\n".join([hello, headline, *footer, brand]) + "\n"
    html = _html([escape(hello), escape(headline), *map(escape, footer), escape(brand)])
    return OutgoingMail(to=to, subject=subject, text=text, html=html)


def customer_request_moved_mail(
    *, to: str, name: str | None, brand: str, booking: BookingRecord, tz: str
) -> OutgoingMail:
    """Plain text, with no calendar file, for a customer whose own bot asked for a booking (or a
    move of one) that staff then moved before confirming it.

    They were never sent an invitation, so there is no event to update, and it is still a request:
    the mail says so. A later "wasn't confirmed" then names the time they were told of.
    """
    title = " ".join((booking.title or "Booking").split())
    new = when(booking.starts_at, tz)
    if booking.replaces_booking_id is not None:
        headline = (
            f"Your request to move {title} with {brand} now asks for {new}. It isn't confirmed yet."
        )
    else:
        headline = (
            f"Your request for {title} with {brand} has moved to {new}. It isn't confirmed yet."
        )
    subject = f"Request moved: {title} to {new} ({_one_line(brand)})"
    hello = f"Hello {name}," if name else "Hello,"
    text = "\n\n".join([hello, headline, brand]) + "\n"
    html = _html([escape(hello), escape(headline), escape(brand)])
    return OutgoingMail(to=to, subject=subject, text=text, html=html)


def reminder_mail(
    *, to: str, name: str | None, brand: str, booking: BookingRecord, tz: str
) -> OutgoingMail:
    """A reminder that a booking is coming up: what, when and where, as plain text with no
    calendar file (the one sent when it was made is already in the calendar).

    The same for the customer, the people on the booking and its guests, and it names nobody else.
    """
    title = " ".join((booking.title or "Booking").split())
    headline = f"A reminder: {title} with {brand} is coming up."
    rows: list[tuple[str, str, str | None]] = [
        ("What", title, None),
        ("When", f"{_span(booking.starts_at, booking.ends_at, tz)} ({tz})", None),
        *_place_rows(booking),
    ]
    hello = f"Hello {name}," if name else "Hello,"
    text = (
        f"{hello}\n\n{headline}\n\n"
        + "".join(f"{label}: {value}\n" for label, value, _ in rows)
        + f"\n{brand}\n"
    )
    details = "<br>\n".join(
        f"<strong>{label}:</strong> "
        + (f'<a href="{escape(href)}">{escape(value)}</a>' if href else escape(value))
        for label, value, href in rows
    )
    html = _html([escape(hello), escape(headline), details, escape(brand)])
    subject = f"Reminder: {title} on {when(booking.starts_at, tz)} ({_one_line(brand)})"
    return OutgoingMail(to=to, subject=subject, text=text, html=html)


def customer_lapsed_mail(
    *,
    to: str,
    name: str | None,
    brand: str,
    booking: BookingRecord,
    original: BookingRecord | None,
    tz: str,
    invite: bool = True,
) -> OutgoingMail:
    """Plain text for a customer whose own bot's pending booking, or request to move one, ran out
    of time before staff answered. Like the decline, it sends no calendar file and never says
    "cancelled": they were never sent an invitation. It says the time was released, or that the
    booking a request would have moved stays where it was, and invites another request only when
    `invite` says the bot would act on one.
    """
    title = " ".join((booking.title or "Booking").split())
    new = when(booking.starts_at, tz)
    if original is not None:
        headline = (
            f"Your request to move {title} with {brand} to {new} wasn't confirmed in time. "
            f"Your booking stays on {when(original.starts_at, tz)}."
        )
        subject = f"Request not confirmed in time: {title} ({_one_line(brand)})"
    else:
        headline = (
            f"Your request for {title} with {brand} on {new} wasn't confirmed in time, "
            "so that time has been released."
        )
        subject = f"Not confirmed in time: {title} on {new} ({_one_line(brand)})"
    hello = f"Hello {name}," if name else "Hello,"
    footer = ["You can ask for another time whenever you like."] if invite else []
    text = "\n\n".join([hello, headline, *footer, brand]) + "\n"
    html = _html([escape(hello), escape(headline), *map(escape, footer), escape(brand)])
    return OutgoingMail(to=to, subject=subject, text=text, html=html)
