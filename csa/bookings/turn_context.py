"""What the classifier and planner are told about bookings, so "3 pm please" can become a booking.

A customer's second message ("3 pm please") names no service, day or booking. The planner sees only
the current message, so for a company whose customers can book it is also given: today's local date,
the company's zone, the services a customer may book and this customer's own upcoming bookings (each
with its id), the last few messages of the conversation, and the booking the last reply asked the
customer to confirm cancelling, when this message is their first since. Ids are only ever taken
from here, and the server still checks every time and slot, so nothing in it is authority.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from csa.bookings import bot, service, setup
from csa.contracts.state import (
    RECENT_CHARS,
    RECENT_MESSAGES,
    BookingContext,
    BookingRef,
    BookingType,
    RecentMessage,
)
from csa.core import db

UPCOMING = 10
MAX_TYPES = 20  # the contract's limit (`BookingContext.types`)


async def booking_context(
    conn: service.Conn,
    client_id: UUID,
    contact_id: UUID,
    conversation_id: UUID,
    trace_id: UUID,
    now: datetime,
) -> BookingContext | None:
    """The context, or None when the bot could not book for this customer anyway: the booking
    agent is off, the contact is unknown, or Customer support is not active.
    """
    if not await db.q.fetch_one(
        "agents/preparation_enabled.sql", (client_id, "booking_agent"), conn=conn
    ):
        return None
    if not await db.q.fetch_one(
        "agents/preparation_contact.sql", (client_id, contact_id), conn=conn
    ):
        return None
    if not await bot.customer_service_on(conn, client_id):
        return None
    zone = await service.company_zone(conn, client_id)
    local = now.astimezone(zone)
    names = {t.id: t.name for t in await setup.list_types(conn, client_id)}
    offered = await bot.customer_types(conn, client_id)
    # The contract holds at most 20. More must not switch the whole context off: the planner is
    # told the list is partial, and can ask `availability` for the rest.
    types = [
        BookingType(id=t.id, name=t.name, minutes=t.duration_minutes) for t in offered[:MAX_TYPES]
    ]
    # The same read as the customer's booking list: their own, never private, pending only if
    # the bot made it. Upcoming comes first there, so the next booking is never cut off.
    rows = await db.q.fetch_all(
        "agents/booking_list.sql",
        {"client_id": client_id, "contact_id": contact_id, "now": now},
        conn=conn,
    )
    upcoming = []
    for row in rows:
        if row["ends_at"] < now or len(upcoming) == UPCOMING:
            continue
        name = names.get(row["type_id"], "Booking")
        if row["replaces_booking_id"] is not None:
            name += " (a request to move another booking)"
        upcoming.append(
            BookingRef(
                id=row["id"],
                name=name,
                when=bot.format_slot(row["starts_at"], zone),
                status=row["status"],
            )
        )
    messages = await db.q.fetch_all(
        "turn/booking_recent_messages.sql",
        {
            "client_id": client_id,
            "conversation_id": conversation_id,
            "trace_id": trace_id,
            "limit": RECENT_MESSAGES,
        },
        conn=conn,
    )
    recent = [
        RecentMessage(
            author="customer" if m["direction"] == "inbound" else "assistant",
            text=m["redacted_body"].strip()[:RECENT_CHARS],
        )
        for m in messages
    ]
    # Shown even when it has run out or the booking changed since: the agent then asks again,
    # rather than the planner reading a "yes" as nothing at all.
    asked = await bot.cancel_question(conn, client_id, contact_id, conversation_id, trace_id)
    return BookingContext(
        today=f"{local:%A} {local.date().isoformat()}",
        timezone=str(zone.key),
        types=types,
        more_types=len(offered) > MAX_TYPES,
        upcoming=upcoming,
        recent=recent,
        cancel_asked=asked["booking_id"] if asked else None,
    )
