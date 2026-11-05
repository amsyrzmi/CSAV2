"""Messages to a booking's customer outside a turn: a staff action on it, or the hold lapses.

The first proactive Telegram messages. Nothing here sends: it writes the rows the delivery worker
sends from (a message and its delivery), in the transaction that changes the booking, so a message
and the change it reports stand or fall together. Only Telegram: nothing proactive goes on
WhatsApp, and a customer there is mailed after the commit instead (`notify.py`).

Who stands behind a message decides if delivery holds it while a person has taken over the chat.
A person's own action (confirm, decline, move, cancel) is `human`, and a notice the system sends
because time ran out is `reviewed`, so neither is held back. The caller decides if a message is owed
at all: staff actions always are, even with replies Off, and the expiry job asks `bot.gate` first.

Every text is plain English, built from the booking and nothing the customer typed, and says what
happened. It adds that the customer can reply only while the bot would act on a reply.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from csa.bookings import bot, service
from csa.bookings.bot import format_slot
from csa.core import db

Authority = Literal["reviewed", "human"]
Conn = service.Conn


async def telegram_message(
    conn: Conn,
    client_id: UUID,
    booking: service.BookingRecord,
    *,
    key: str,
    body: str,
    authority: Authority,
) -> bool:
    """Queue `body` to the booking's customer on Telegram, once per `key`.

    The route is the booking's own chat, or else the customer's latest Telegram chat, on a trace
    that chat's own message came in on (see `customer_route.sql`). Returns false, writing nothing,
    when there is no such route; true when the message is queued, and also when this key already
    queued it.
    """
    if booking.customer is None:
        return False
    route = await db.q.fetch_one(
        "bookings/customer_route.sql",
        {
            "client_id": client_id,
            "contact_id": booking.customer.id,
            "conversation_id": booking.conversation_id,
        },
        conn=conn,
    )
    if route is None:
        return False
    # The text is the server's own, with no customer words in it, so it is its own redacted copy:
    # a redactor would take a date for a phone number and spoil what the inbox shows.
    message = await db.q.fetch_one(
        "worker/insert_message.sql",
        (
            client_id,
            route["conversation_id"],
            "outbound",
            "assistant",
            body,
            body,
            Jsonb([]),
            False,
            key,
            route["trace_id"],
        ),
        conn=conn,
    )
    if message is None:
        return True
    await db.q.execute(
        "bookings/message_authority_set.sql",
        {"client_id": client_id, "id": message["id"], "authority": authority},
        conn=conn,
    )
    await db.q.fetch_one(
        "worker/insert_delivery.sql",
        (
            client_id,
            route["endpoint_id"],
            route["trace_id"],
            route["conversation_id"],
            message["id"],
        ),
        conn=conn,
    )
    return True


# --- what each message says ---------------------------------------------------------------------

Event = Literal["confirmed", "moved", "cancelled", "declined", "expired"]


def _what(booking: service.BookingRecord) -> str:
    title = " ".join((booking.title or "").split())
    return f"{title} booking" if title else "booking"


def _text(
    event: Event,
    booking: service.BookingRecord,
    original: service.BookingRecord | None,
    zone: ZoneInfo,
    *,
    reply: bool,
) -> str:
    """The message. `booking` is the booking the event is about (for a move, the original at its
    new time); `original` is the booking a lapsed or declined request would have moved, if so.
    `reply` says the bot would act on a reply (replies on, Customer support active): only then does
    the text invite one.
    """
    what = _what(booking)
    when = format_slot(booking.starts_at, zone)

    def ending(invitation: str) -> str:
        return f" {invitation}" if reply else ""

    if event == "confirmed":
        return f"Your {what} is confirmed for {when}." + ending(
            "You can reply here to change it, or to book another time."
        )
    if event == "moved":
        extra = (
            " Your earlier request to move it has been withdrawn."
            if booking.withdrew_change
            else ""
        )
        return f"Your {what} has moved to {when}.{extra}" + ending(
            "You can reply here to change it again."
        )
    if event == "cancelled":
        extra = (
            " Your request to move it has been withdrawn too." if booking.withdrew_change else ""
        )
        return f"Your {what} for {when} has been cancelled.{extra}" + ending(
            "You can reply here to book another time."
        )
    late = " in time" if event == "expired" else ""
    if original is not None:
        stays = format_slot(original.starts_at, zone)
        return (
            f"Your request to move your {what} to {when} wasn't confirmed{late}. "
            f"Your booking stays on {stays}."
        ) + ending("Reply here to ask for another time.")
    why = "so that time has been released" if event == "expired" else "so that time isn't booked"
    return f"Your {what} request for {when} wasn't confirmed{late}, {why}." + ending(
        "Reply here to book again."
    )


async def the_original(
    conn: Conn, client_id: UUID, booking: service.BookingRecord
) -> service.BookingRecord | None:
    """The booking a request would have moved, when it is still standing."""
    if booking.replaces_booking_id is None:
        return None
    found = await service.read_records(conn, client_id, booking_id=booking.replaces_booking_id)
    return found[0] if found and found[0].status in ("pending", "confirmed") else None


async def can_reply(conn: Conn, client_id: UUID) -> bool:
    """The bot would act on a reply: replies are not Off and Customer support is active."""
    blocked, _ = await bot.gate(conn, client_id)
    return blocked is None


# --- the moments --------------------------------------------------------------------------------


async def _send(
    conn: Conn,
    client_id: UUID,
    booking: service.BookingRecord,
    event: Event,
    key: str,
    *,
    authority: Authority,
    reply: bool,
) -> bool:
    zone = await service.company_zone(conn, client_id)
    original = (
        await the_original(conn, client_id, booking) if event in ("declined", "expired") else None
    )
    return await telegram_message(
        conn,
        client_id,
        booking,
        key=key,
        body=_text(event, booking, original, zone, reply=reply),
        authority=authority,
    )


async def lapsed(conn: Conn, client_id: UUID, booking: service.BookingRecord) -> bool:
    """A pending booking the bot made ran out of time. The caller has checked the company's gates
    (replies not Off, Customer support active): nothing automatic goes without them."""
    return await _send(
        conn, client_id, booking, "expired", f"booking:{booking.id}:expired",
        authority="reviewed", reply=True,
    )  # fmt: skip


def is_over(booking: service.BookingRecord, change: str) -> bool:
    """A staff cancel or decline of a booking whose time has already gone. Clearing an old booking
    from the calendar is housekeeping: nobody is owed a message or a mail about a time that has
    passed, the people on it included."""
    return change in ("cancelled", "declined") and booking.ends_at <= datetime.now(UTC)


def is_declined(booking: service.BookingRecord, change: str) -> bool:
    """Staff said no to a booking the customer's own bot asked for (or a request to move one): a
    Decline, or a Cancel of a booking that was still pending. From the customer's side they are
    the same thing."""
    if booking.source != "bot":
        return False
    return change == "declined" or (change == "cancelled" and booking.previous_status == "pending")


async def after_staff_action(
    conn: Conn, client_id: UUID, booking: service.BookingRecord, change: str
) -> bool:
    """Write the chat message a staff action owes, in its transaction. True if one is queued.

    A person's action goes even with replies Off, and only the text changes (no invitation to
    reply the bot would not answer):
    - confirming a booking the bot made, or a request to move one (`moved_by_request`, even when
      the booking it moves was made by staff);
    - declining or cancelling a pending booking the bot made, or a request to move one;
    - moving or cancelling a confirmed booking, whoever made it.
    A booking staff made tells the customer nothing until it is confirmed, a private meeting never,
    and the bot's own cancel needs no message (the customer asked it in chat).
    """
    if booking.customer is None or booking.private or is_over(booking, change):
        return False
    reply = await can_reply(conn, client_id)
    if change == "confirmed" and (booking.source == "bot" or booking.moved_by_request):
        event: Event = "moved" if booking.moved_by_request else "confirmed"
        key = f"booking:{booking.id}:confirmed:{booking.version}"
    elif is_declined(booking, change):
        event, key = "declined", f"booking:{booking.id}:declined"
    elif change == "moved" and booking.status == "confirmed":
        event, key = "moved", f"booking:{booking.id}:moved:{booking.version}"
    elif change == "cancelled" and booking.previous_status == "confirmed":
        event, key = "cancelled", f"booking:{booking.id}:cancelled"
    else:
        return False
    return await _send(conn, client_id, booking, event, key, authority="human", reply=reply)
