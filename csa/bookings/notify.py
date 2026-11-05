"""Email with a calendar file when staff make, move or cancel a booking.

Runs after the booking's transaction has committed, so a failed email can never undo a booking.
Who gets one: the customer (when their contact has an address and the booking is not still waiting
for confirmation), each person on the booking with an address (their own, or their account's), and
each outside guest. A private meeting goes to its attendees only. Nobody else is ever mailed, and
each recipient gets their own file naming only themselves, so a customer cannot learn a staff or
guest address.

The result is one word for the whole booking, the worst outcome across recipients. It reports on
whoever was to be mailed: "none" if that is nobody. It also says whether the customer was told:
true if they were mailed, false if the booking has a customer who was not (no address, mail off or
failed, a private meeting), and null when there is no customer or none is owed a message yet. Logs
carry the category only, never an address or a title.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter, ValidationError

from csa.bookings import ics, setup
from csa.bookings.contracts import Email, EmailSummary
from csa.bookings.service import BookingRecord, Conn
from csa.console.mail_templates import booking_mail
from csa.console.mailer import CalendarFile, Mailer
from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

Change = Literal["created", "moved", "cancelled"]

SIMULTANEOUS = 8  # sends at once, so a slow relay holds up a booking with many guests the least
# Seconds the staff member waits for all of one booking's mail, under any proxy's read timeout.
# Past it, sends not yet started are dropped; one already in flight cannot be taken back.
SEND_LIMIT = 15.0
_EMAIL = TypeAdapter(Email)


@dataclass(frozen=True)
class Outcome:
    """How the mail went for the whole booking, and whether its customer got a message."""

    email: EmailSummary
    customer_told: bool | None


@dataclass(frozen=True)
class MailFacts:
    """What the message needs that the booking does not carry."""

    brand: str
    timezone: str
    account_emails: Mapping[UUID, str]  # console account id to its address


@dataclass(frozen=True)
class Recipient:
    address: str
    name: str | None


async def load_facts(conn: Conn, client_id: UUID) -> MailFacts:
    row = await db.q.fetch_one("bookings/mail_facts.sql", {"client_id": client_id}, conn=conn)
    if row is None:
        raise RuntimeError("company not found")
    members = await setup.team_members(conn, client_id)
    return MailFacts(
        brand=row["brand"],
        timezone=row["timezone"],
        account_emails={m["user_id"]: m["email"] for m in members if m["email"]},
    )


def _clean(address: object) -> str | None:
    """The address if it passes the check an address typed into bookings passes; else none."""
    try:
        return _EMAIL.validate_python(address)
    except ValidationError:
        return None


def customer_address(booking: BookingRecord) -> str | None:
    """Where the customer's own message goes: the contact's email, else the address they gave in a
    chat (never checked, so it is only ever a fallback and never identity)."""
    customer = booking.customer
    if customer is None:
        return None
    return customer.email or customer.given_email


def never_confirmed(booking: BookingRecord) -> bool:
    """Still pending, or cancelled or declined while it was: the customer was never told of it."""
    return booking.status == "pending" or (
        booking.status == "cancelled" and booking.previous_status == "pending"
    )


def owes_customer(booking: BookingRecord) -> bool:
    """The booking has a customer, and they are owed a message about this change."""
    return booking.customer is not None and not never_confirmed(booking)


def customer_reached(booking: BookingRecord, people: list[Recipient], results: list[bool]) -> bool:
    """Whether the customer's own message went. A private meeting never mails its customer."""
    typed = customer_address(booking)
    address = _clean(typed) if typed else None
    if address is None or booking.private:
        return False
    wanted = address.casefold()
    return any(
        ok for one, ok in zip(people, results, strict=True) if one.address.casefold() == wanted
    )


def recipients(booking: BookingRecord, facts: MailFacts) -> list[Recipient]:
    """Everyone with an address who should hear about this booking, once each."""
    found: dict[str, Recipient] = {}

    def add(address: object, name: str | None) -> None:
        clean = _clean(address) if address else None
        if clean is not None:
            found.setdefault(clean.casefold(), Recipient(clean, name))

    customer = booking.customer
    # A private meeting is for its attendees. A customer hears nothing until staff confirm, and
    # nothing about a booking that was cancelled without ever being confirmed.
    if customer is not None and not booking.private and not never_confirmed(booking):
        add(customer_address(booking), customer.full_name)
    for person in booking.people:
        own = _clean(person.email) if person.email else None
        account = facts.account_emails.get(person.user_id) if person.user_id else None
        add(own or account, person.display_name)
    for guest in booking.guests:
        add(guest.email, guest.name)
    return list(found.values())


async def _deliver(
    mailer: Mailer,
    booking: BookingRecord,
    change: Change,
    facts: MailFacts,
    one: Recipient,
    gate: asyncio.Semaphore,
) -> bool:
    try:
        method: Literal["REQUEST", "CANCEL"] = "CANCEL" if change == "cancelled" else "REQUEST"
        file = CalendarFile(
            method,
            ics.invitation(
                booking, mailer.sender, method, attendee=one.address, attendee_name=one.name
            ),
        )
        mail = booking_mail(
            to=one.address,
            name=one.name,
            brand=facts.brand,
            booking=booking,
            change=change,
            tz=facts.timezone,
            calendar=file,
        )
        async with gate:
            return await mailer.send(mail) == "sent"
    except Exception:
        logger.warning("bookings_mail_failed", category="build_error")
        return False


async def notify(
    mailer: Mailer | None, booking: BookingRecord, change: Change, facts: MailFacts
) -> Outcome:
    """Send the invitation, the new time or the cancellation; say how it went."""
    owed = owes_customer(booking)
    people = recipients(booking, facts)
    if not people:
        return Outcome("none", False if owed else None)
    if mailer is None or not mailer.enabled:
        return Outcome("off", False if owed else None)
    try:
        ZoneInfo(facts.timezone)
        gate = asyncio.Semaphore(SIMULTANEOUS)
        results = await asyncio.wait_for(
            asyncio.gather(*(_deliver(mailer, booking, change, facts, p, gate) for p in people)),
            SEND_LIMIT,
        )
    except Exception:
        logger.warning("bookings_mail_failed", category="send_error")
        return Outcome("failed", False if owed else None)
    told = customer_reached(booking, people, list(results)) if owed else None
    return Outcome("sent" if all(results) else "failed", told)


async def notify_after_commit(
    pool: AsyncConnectionPool,
    mailer: Mailer | None,
    booking: BookingRecord,
    change: Change,
) -> Outcome:
    """Read the company's facts in a new transaction, then notify.

    Never raises: the booking has committed, so an error here must not become a 500 that a retry
    would turn into "That time was just taken." Anything that goes wrong is "failed", logged by
    category only.
    """
    try:
        try:
            async with tx(pool, str(booking.client_id)) as conn:
                facts = await load_facts(conn, booking.client_id)
        except Exception:
            logger.warning("bookings_mail_failed", category="facts_unavailable")
            return Outcome("failed", False if owes_customer(booking) else None)
        return await notify(mailer, booking, change, facts)
    except Exception:
        logger.warning("bookings_mail_failed", category="unexpected")
        return Outcome("failed", False if owes_customer(booking) else None)
