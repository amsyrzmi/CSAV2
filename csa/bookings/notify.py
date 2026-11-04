"""Email with a calendar file when staff make, move or cancel a booking.

Runs after the booking's transaction has committed, so a failed email can never undo a booking.
Who gets one: the customer (when their contact has an address and the booking is not still waiting
for confirmation), each person on the booking with an address (their own, or their account's), and
each outside guest. A private meeting goes to its attendees only. Nobody else is ever mailed, and
each recipient gets their own file naming only themselves, so a customer cannot learn a staff or
guest address.

The result is one word for the whole booking, the worst outcome across recipients. Logs carry the
category only, never an address or a title.
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
SEND_LIMIT = 60.0  # seconds for all of one booking's mail
_EMAIL = TypeAdapter(Email)


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


def recipients(booking: BookingRecord, facts: MailFacts) -> list[Recipient]:
    """Everyone with an address who should hear about this booking, once each."""
    found: dict[str, Recipient] = {}

    def add(address: object, name: str | None) -> None:
        clean = _clean(address) if address else None
        if clean is not None:
            found.setdefault(clean.casefold(), Recipient(clean, name))

    customer = booking.customer
    # A private meeting is for its attendees. A customer hears nothing until staff confirm.
    if customer is not None and not booking.private and booking.status != "pending":
        add(customer.email, customer.full_name)
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
) -> EmailSummary:
    """Send the invitation, the new time or the cancellation; say how it went."""
    people = recipients(booking, facts)
    if not people:
        return "none"
    if mailer is None or not mailer.enabled:
        return "off"
    try:
        ZoneInfo(facts.timezone)
        gate = asyncio.Semaphore(SIMULTANEOUS)
        results = await asyncio.wait_for(
            asyncio.gather(*(_deliver(mailer, booking, change, facts, p, gate) for p in people)),
            SEND_LIMIT,
        )
    except Exception:
        logger.warning("bookings_mail_failed", category="send_error")
        return "failed"
    return "sent" if all(results) else "failed"


async def notify_after_commit(
    pool: AsyncConnectionPool,
    mailer: Mailer | None,
    booking: BookingRecord,
    change: Change,
) -> EmailSummary:
    """Read the company's facts in a new transaction, then notify. Never raises."""
    try:
        async with tx(pool, str(booking.client_id)) as conn:
            facts = await load_facts(conn, booking.client_id)
    except Exception:
        logger.warning("bookings_mail_failed", category="facts_unavailable")
        return "failed"
    return await notify(mailer, booking, change, facts)
