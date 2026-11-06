"""Email with a calendar file when staff make, move or cancel a booking.

Runs after the booking's transaction has committed, so a failed email can never undo a booking.
Who gets one: the customer (when their contact has an address and the booking is not still waiting
for confirmation), each person on the booking with an address (their own, or their account's), and
each outside guest. A private meeting goes to its attendees only. Nobody else is ever mailed, and
each recipient gets their own file naming only themselves, so a customer cannot learn a staff or
guest address.

The result is one word for the whole booking, the worst outcome across recipients. It reports on
whoever was to be mailed: "none" if that is nobody. It also says whether the customer was told:
true if they were mailed or already have a chat message about it (`by_chat`, written in the
booking's own transaction by `customer_messages.py`), false if the booking has a customer who was
not told (no address and no chat, mail off or failed, a private meeting), and null when there is no
customer or none is owed a message yet. A booking the customer's own bot made differs: staff
declining it tells the customer (they asked), by a plain "not confirmed" mail with no calendar
file, because they were never sent an invitation; so does staff moving it before confirming. A
pending request to move a booking, and a booking the bot asked for until staff confirm it, go to
the customer only: nobody on it was ever invited. Logs carry the category only, never an address or
a title.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter, ValidationError

from csa.bookings import ics, setup
from csa.bookings.contracts import Email, EmailSummary
from csa.bookings.service import BookingRecord, Conn
from csa.console.mail_templates import (
    booking_mail,
    customer_declined_mail,
    customer_request_moved_mail,
)
from csa.console.mailer import CalendarFile, Mailer
from csa.core import db
from csa.core.tenancy import tx

if TYPE_CHECKING:
    from csa.bookings.google.push import CalendarPush

logger = structlog.get_logger(__name__)

# "declined" is a cancellation for the people on the booking and a plain "not confirmed" for a
# customer who was never sent an invitation.
Change = Literal["created", "moved", "cancelled", "declined"]

SIMULTANEOUS = 8  # sends at once, so a slow relay holds up a booking with many guests the least
# Seconds the staff member waits for all of one booking's mail, under any proxy's read timeout.
# Past it, sends not yet started are dropped; one already in flight cannot be taken back.
SEND_LIMIT = 15.0
# How long a staff or assistant booking waits for Google before its mail goes; the job finishes it.
CALENDAR_PUSH_TIMEOUT = 8.0
_EMAIL = TypeAdapter(Email)


@dataclass(frozen=True)
class Outcome:
    """How the mail went for the whole booking, and whether its customer got a message."""

    email: EmailSummary
    customer_told: bool | None
    # The booking as it is once the Google push has run (it may have a Meet link and a newer
    # version); None when the push changed nothing or did not run.
    booking: BookingRecord | None = None


@dataclass(frozen=True)
class MailFacts:
    """What the message needs that the booking does not carry."""

    brand: str
    timezone: str
    account_emails: Mapping[UUID, str]  # console account id to its address
    # People whose Google event already holds this booking: their mail carries no calendar file.
    calendar_people: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class Recipient:
    address: str
    name: str | None
    customer: bool = False
    on_google: bool = False  # their Google event holds the booking: no calendar file


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


def clean_address(address: object) -> str | None:
    """The address if it passes the check an address typed into bookings passes; else none."""
    return _clean(address) if address else None


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


def bot_request(booking: BookingRecord) -> bool:
    """A booking the customer's own bot asked for that was never confirmed: still pending, or
    cancelled, declined or lapsed while it was. Nobody but the customer was ever told of it."""
    return booking.source == "bot" and never_confirmed(booking)


def plain_for_customer(booking: BookingRecord, change: Change) -> bool:
    """The customer's mail is plain text with no calendar file: they were never sent an
    invitation. A decline of what their bot asked for, or staff moving it before confirming."""
    return change == "declined" or (change == "moved" and bot_request(booking))


def owes_customer(booking: BookingRecord) -> bool:
    """The booking has a customer, and they are owed a message about this change."""
    return booking.customer is not None and not never_confirmed(booking)


def customer_owed(booking: BookingRecord, owed: bool | None) -> bool:
    """Whether the customer is owed a message, when the caller knows better than `owes_customer`.

    `owed` is the caller's word: true for a declined request the customer's own bot made (they
    asked, so they are told), false for a change the customer made themselves in chat (their own
    message to the bot covers them). None leaves it to the booking's status.
    """
    if owed is None:
        return owes_customer(booking)
    return booking.customer is not None and owed


def _told(owed: bool, by_chat: bool, reached: bool = False) -> bool | None:
    """The answer for `customer_told`: true if a chat message was written or the mail arrived,
    false if the customer was owed one and got none, null if none was owed."""
    if by_chat:
        return True
    return reached if owed else None


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


def recipients(
    booking: BookingRecord, facts: MailFacts, *, customer: bool | None = None
) -> list[Recipient]:
    """Everyone with an address who should hear about this booking, once each. `customer` says
    whether the customer is one of them; by default they are unless it was never confirmed. A
    pending request to move a booking goes to its customer only: nothing was ever sent about it to
    anyone else."""
    found: dict[str, Recipient] = {}

    def add(address: object, name: str | None, *, who: bool = False, google: bool = False) -> None:
        clean = _clean(address) if address else None
        if clean is not None:
            found.setdefault(clean.casefold(), Recipient(clean, name, who, google))

    asker = booking.customer
    mail_customer = not never_confirmed(booking) if customer is None else customer
    # A private meeting is for its attendees. A customer hears nothing until staff confirm, and
    # nothing about a booking that was cancelled without ever being confirmed, unless they asked
    # for it (the caller says so).
    if asker is not None and not booking.private and mail_customer:
        add(customer_address(booking), asker.full_name, who=True)
    # The people on a booking the customer's own bot asked for are invited only once staff confirm
    # it, so while it was never confirmed they get nothing: no "moved", no cancellation of nothing.
    if booking.replaces_booking_id is not None or bot_request(booking):
        return list(found.values())
    for person in booking.people:
        own = _clean(person.email) if person.email else None
        account = facts.account_emails.get(person.user_id) if person.user_id else None
        on_google = person.person_id in facts.calendar_people
        add(own or account, person.display_name, google=on_google)
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
    original: BookingRecord | None,
    invite: bool,
) -> bool:
    try:
        if change == "moved" and one.customer and bot_request(booking):
            # Staff moved what the customer's bot asked for, before confirming it: still a request.
            mail = customer_request_moved_mail(
                to=one.address, name=one.name, brand=facts.brand, booking=booking, tz=facts.timezone
            )
        elif change == "declined" and one.customer:
            # Never sent an invitation, so no calendar file and no talk of cancelling.
            mail = customer_declined_mail(
                to=one.address,
                name=one.name,
                brand=facts.brand,
                booking=booking,
                original=original,
                tz=facts.timezone,
                invite=invite,
            )
        else:
            shown: Literal["created", "moved", "cancelled"] = (
                "cancelled" if change == "declined" else change
            )
            method: Literal["REQUEST", "CANCEL"] = "CANCEL" if shown == "cancelled" else "REQUEST"
            file = (
                None
                if one.on_google
                else CalendarFile(
                    method,
                    ics.invitation(
                        booking, mailer.sender, method, attendee=one.address, attendee_name=one.name
                    ),
                )
            )
            mail = booking_mail(
                to=one.address,
                name=one.name,
                brand=facts.brand,
                booking=booking,
                change=shown,
                tz=facts.timezone,
                calendar=file,
            )
        async with gate:
            return await mailer.send(mail) == "sent"
    except Exception:
        logger.warning("bookings_mail_failed", category="build_error")
        return False


async def notify(
    mailer: Mailer | None,
    booking: BookingRecord,
    change: Change,
    facts: MailFacts,
    *,
    owed: bool | None = None,
    by_chat: bool = False,
    original: BookingRecord | None = None,
    invite: bool = True,
) -> Outcome:
    """Send the invitation, the new time or the cancellation; say how it went.

    `owed` and `by_chat` come from a booking the customer's own bot made, or one staff changed.
    `owed` overrides who is owed a message (see `customer_owed`). `by_chat` says the customer
    already has a chat message about this change: they are then mailed only when the mail carries
    something the chat cannot, a calendar file, so never for a decline (they were never sent an
    invitation). `original` is the booking a declined request would have moved. `invite` is
    whether a declined customer's mail may invite another request (the bot would act on it).
    """
    is_owed = customer_owed(booking, owed)
    mail_customer = is_owed and not (by_chat and plain_for_customer(booking, change))
    people = recipients(booking, facts, customer=mail_customer)
    if not people:
        return Outcome("none", _told(is_owed, by_chat))
    if mailer is None or not mailer.enabled:
        return Outcome("off", _told(is_owed, by_chat))
    try:
        ZoneInfo(facts.timezone)
        gate = asyncio.Semaphore(SIMULTANEOUS)
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    _deliver(mailer, booking, change, facts, p, gate, original, invite)
                    for p in people
                )
            ),
            SEND_LIMIT,
        )
    except Exception:
        logger.warning("bookings_mail_failed", category="send_error")
        return Outcome("failed", _told(is_owed, by_chat))
    reached = mail_customer and customer_reached(booking, people, list(results))
    return Outcome("sent" if all(results) else "failed", _told(is_owed, by_chat, reached))


async def notify_after_commit(
    pool: AsyncConnectionPool,
    mailer: Mailer | None,
    booking: BookingRecord,
    change: Change,
    *,
    owed: bool | None = None,
    by_chat: bool = False,
    original: BookingRecord | None = None,
    invite: bool = True,
    calendar: CalendarPush | None = None,
) -> Outcome:
    """Read the company's facts in a new transaction, then notify.

    Never raises: the booking has committed, so an error here must not become a 500 that a retry
    would turn into "That time was just taken." Anything that goes wrong is "failed", logged by
    category only.
    """
    unknown = _told(customer_owed(booking, owed), by_chat)
    sent = booking  # as it was handed in: the push may return a newer one
    try:
        try:
            async with tx(pool, str(booking.client_id)) as conn:
                facts = await load_facts(conn, booking.client_id)
        except Exception:
            logger.warning("bookings_mail_failed", category="facts_unavailable")
            return Outcome("failed", unknown)
        if calendar is not None and calendar.available and change != "declined":
            # The people's Google events first, so mail to one whose calendar now holds the
            # booking carries no file (it would add a second event). The push never raises.
            booking = await calendar(booking, timeout=CALENDAR_PUSH_TIMEOUT) or booking
            facts = replace(facts, calendar_people=await _calendar_people(pool, booking, change))
        outcome = await notify(
            mailer,
            booking,
            change,
            facts,
            owed=owed,
            by_chat=by_chat,
            original=original,
            invite=invite,
        )
        return replace(outcome, booking=booking if booking is not sent else None)
    except Exception:
        logger.warning("bookings_mail_failed", category="unexpected")
        return Outcome("failed", unknown)


async def _calendar_people(
    pool: AsyncConnectionPool, booking: BookingRecord, change: Change
) -> frozenset[UUID]:
    """The people whose mail carries no calendar file, once the push has run.

    For a new booking or a move: those whose Google event now holds this version of it (a file
    would add a second event). Who was left without one is stored, so a cancellation can tell
    who was ever sent a file. For a cancellation: those who never were, since there is no event
    of ours for the file to remove. Never raises: on a failure everyone gets the file, as before.
    """
    ids = {"client_id": booking.client_id, "booking_id": booking.id}
    try:
        async with tx(pool, str(booking.client_id)) as conn:
            rows = await db.q.fetch_all("bookings/google_ledger_people.sql", ids, conn=conn)
            if change == "cancelled":
                return frozenset(r["person_id"] for r in rows if r["file_free"])
            free = frozenset(
                r["person_id"]
                for r in rows
                if r["status"] == "synced" and r["synced_version"] == booking.version
            )
            await db.q.execute(
                "bookings/google_event_file_mark.sql", {**ids, "omitted": list(free)}, conn=conn
            )
            return free
    except Exception:
        logger.warning("bookings_mail_failed", category="calendar_people")
        return frozenset()
