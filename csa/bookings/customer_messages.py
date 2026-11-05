"""Messages to a booking's customer outside a turn: staff confirm or decline, or the hold lapses.

The first proactive Telegram messages. Nothing here sends: it writes the rows the delivery worker
sends from (a message and its delivery), in the transaction that changes the booking, so a message
and the change it reports stand or fall together. Only Telegram: nothing proactive goes on
WhatsApp, and a customer there is mailed after the commit instead (`notify.py`).

Who stands behind a message decides if delivery holds it while a person has taken over the chat.
A person's own action (confirm, decline) is `human`, and a notice the system sends because time ran
out is `reviewed`, so neither is held back. The caller decides if a message is owed at all: staff
actions always are, even with replies Off, and the expiry job asks `bot.gate` first.

Every text is plain English, built from the booking and nothing the customer typed, and says what
happened and that the customer can reply to book again.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from csa.bookings import service
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


def _what(booking: service.BookingRecord) -> str:
    title = " ".join((booking.title or "").split())
    return f"{title} booking" if title else "booking"


def _text(
    event: Literal["confirmed", "moved", "declined", "expired"],
    booking: service.BookingRecord,
    original: service.BookingRecord | None,
    zone: ZoneInfo,
) -> str:
    """The message. `booking` is the booking the event is about (for a move, the original at its
    new time); `original` is the booking a lapsed or declined request would have moved, if so.
    """
    what = _what(booking)
    when = format_slot(booking.starts_at, zone)
    if event == "confirmed":
        return (
            f"Your {what} is confirmed for {when}. "
            "You can reply here to change it, or to book another time."
        )
    if event == "moved":
        return f"Your {what} has moved to {when}. You can reply here to change it again."
    late = " in time" if event == "expired" else ""
    if original is not None:
        stays = format_slot(original.starts_at, zone)
        return (
            f"Your request to move your {what} to {when} wasn't confirmed{late}. "
            f"Your booking stays on {stays}. Reply here to ask for another time."
        )
    ending = "so that time has been released" if event == "expired" else "so that time isn't booked"
    return (
        f"Your {what} request for {when} wasn't confirmed{late}, {ending}. "
        "Reply here to book again."
    )


async def _the_original(
    conn: Conn, client_id: UUID, booking: service.BookingRecord
) -> service.BookingRecord | None:
    """The booking a request would have moved, when it is still standing."""
    if booking.replaces_booking_id is None:
        return None
    found = await service.read_records(conn, client_id, booking_id=booking.replaces_booking_id)
    return found[0] if found and found[0].status in ("pending", "confirmed") else None


# --- the three moments --------------------------------------------------------------------------


async def confirmed(conn: Conn, client_id: UUID, booking: service.BookingRecord) -> bool:
    """Staff confirmed a booking the bot made, or a request to move one (`booking` is then the
    original, now at the new time). A person's action: it goes even with replies Off."""
    zone = await service.company_zone(conn, client_id)
    event: Literal["confirmed", "moved"] = "moved" if booking.moved_by_request else "confirmed"
    return await telegram_message(
        conn,
        client_id,
        booking,
        key=f"booking:{booking.id}:confirmed:{booking.version}",
        body=_text(event, booking, None, zone),
        authority="human",
    )


async def declined(conn: Conn, client_id: UUID, booking: service.BookingRecord) -> bool:
    """Staff declined a booking the bot made, or a request to move one: the original stands."""
    zone = await service.company_zone(conn, client_id)
    original = await _the_original(conn, client_id, booking)
    return await telegram_message(
        conn,
        client_id,
        booking,
        key=f"booking:{booking.id}:declined",
        body=_text("declined", booking, original, zone),
        authority="human",
    )


async def lapsed(conn: Conn, client_id: UUID, booking: service.BookingRecord) -> bool:
    """A pending booking the bot made ran out of time. The caller has checked the company's gates
    (replies not Off, Customer support active): nothing automatic goes without them."""
    zone = await service.company_zone(conn, client_id)
    original = await _the_original(conn, client_id, booking)
    return await telegram_message(
        conn,
        client_id,
        booking,
        key=f"booking:{booking.id}:expired",
        body=_text("expired", booking, original, zone),
        authority="reviewed",
    )


async def after_staff_action(
    conn: Conn, client_id: UUID, booking: service.BookingRecord, action: str
) -> bool:
    """Write the chat message a staff action owes, in its transaction. True if one is queued.

    Only the customer's own bot bookings owe one, and only on confirm and decline: a booking staff
    made tells the customer nothing until it is confirmed, and every other action is mailed. A
    request to move a booking the customer asked for is the bot's, even when the booking it moves
    was made by staff (a confirmation answers with that booking, `moved_by_request`).
    """
    if not (booking.source == "bot" or booking.moved_by_request):
        return False
    if booking.customer is None or booking.private:
        return False
    if action == "confirmed":
        return await confirmed(conn, client_id, booking)
    if action == "declined":
        return await declined(conn, client_id, booking)
    return False
