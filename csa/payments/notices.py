"""Proactive messages to a contact about payments: one chat message, written for delivery.

Nothing here sends. A message is written as the booking notices are (`csa.bookings.
customer_messages.telegram_message`): a `messages` row and its `outbound_deliveries` row, in the
caller's transaction, `reviewed` so a person's takeover of the chat does not hold it back. The
caller decides whether a message is owed at all (the company's gates, quiet hours, the daily cap).

Telegram goes to the contact's latest Telegram chat (`bookings/customer_route.sql`). WhatsApp goes
only into a chat the customer wrote in less than 23 hours ago (`payments/whatsapp_route.sql`):
outside WhatsApp's 24-hour window nothing can be sent without a template.

On Telegram a message may carry a picture (the company's QR, `payments.pictures.qr_picture`): an
image message, written just before its text in the same transaction, as the bot's image replies
are (`csa.turn.deps._insert_outbound`). WhatsApp and email get the text alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.bookings.notify import clean_address
from csa.contracts.facts import PictureRef
from csa.core import db
from csa.media.attachments import image_attachments
from csa.media.service import picture_for

Conn = AsyncConnection[Any]
ChatChannel = Literal["telegram", "whatsapp"]


def reminder_address(email: str | None, given_email: str | None) -> str | None:
    """Where a contact's payment reminder is mailed: `contacts.email`, else the address the
    customer gave in a chat, as booking reminders choose it (`notify.customer_address`), when it
    passes the bookings address check; lower case, the one spelling a row keeps. Else none."""
    address = clean_address(email or given_email)
    return None if address is None else address.lower()


async def chat_message(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    *,
    channel: ChatChannel,
    key: str,
    body: str,
    now: datetime | None = None,
    picture: PictureRef | None = None,
) -> bool:
    """Queue `body` to the contact on `channel`, once per `key`.

    Returns false, writing nothing, when the contact has no route on that channel (no Telegram
    chat; no WhatsApp chat with a message from them within 23 hours of `now`, the database's
    clock when not given); true when the message is queued, and also when this key already
    queued it.

    With a `picture` on Telegram whose subject still has its stored picture, the image message
    (body and redacted body = the caption, nothing else) is written first under `{key}:qr`, then
    the text: the same route, the same `reviewed` authority, a delivery row each, and
    `clock_timestamp()` stamps keep the picture before its text for delivery and the Inbox. The
    picture is written only together with its text: never when this key's text already exists
    (a rerun, or a text written before pictures existed), so it never comes after it. No stored
    picture (removed since it was chosen) means the text alone. WhatsApp ignores `picture`.
    """
    if channel == "telegram":
        route = await db.q.fetch_one(
            "bookings/customer_route.sql",
            {"client_id": client_id, "contact_id": contact_id, "conversation_id": None},
            conn=conn,
        )
    else:
        route = await db.q.fetch_one(
            "payments/whatsapp_route.sql",
            {"client_id": client_id, "contact_id": contact_id, "now": now},
            conn=conn,
        )
    if route is None:
        return False
    if (
        channel == "telegram"
        and picture is not None
        and await db.q.fetch_one("worker/get_message.sql", (client_id, key), conn=conn) is None
    ):
        stored = await picture_for(conn, client_id, picture.kind, picture.subject_key)
        if stored is not None:
            # The caption is code's own (`copy.qr_caption`), so it is its own redacted copy.
            await _write(
                conn, client_id, route, key=f"{key}:qr", body=picture.caption,
                attachments=image_attachments(stored.id),
            )  # fmt: skip
    # The text is the server's own, with no customer words in it, so it is its own redacted copy.
    await _write(conn, client_id, route, key=key, body=body, attachments=[])
    return True


async def _write(
    conn: Conn,
    client_id: UUID,
    route: dict[str, Any],
    *,
    key: str,
    body: str,
    attachments: list[dict[str, str]],
) -> None:
    """One outbound message on `route`, `reviewed` so a person's takeover does not hold it back,
    and its delivery row; nothing when `key` already wrote one."""
    message = await db.q.fetch_one(
        "worker/insert_message.sql",
        (
            client_id,
            route["conversation_id"],
            "outbound",
            "assistant",
            body,
            body,
            Jsonb(attachments),
            bool(attachments),
            key,
            route["trace_id"],
        ),
        conn=conn,
    )
    if message is None:
        return
    await db.q.execute(
        "bookings/message_authority_set.sql",
        {"client_id": client_id, "id": message["id"], "authority": "reviewed"},
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
