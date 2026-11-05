"""Pending bookings from the bot: confirm, decline or lapse, and how the customer hears.

The first proactive Telegram messages: written as rows in the transaction that changes the booking,
sent by the delivery worker. Email follows after the commit, as it does for every staff action.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from apps.delivery.main import DeliveryWorker
from csa.agents.staged.service import BookingAgent
from csa.bookings import customer_messages, service
from csa.bookings.contracts import BookingIn
from csa.console.mailer import Mailer
from csa.core import crypto, db
from csa.core.tenancy import tx
from csa.jobs.bookings import run_pending_expiry
from csa.jobs.clock import FakeClock
from csa.jobs.runner import JobRunner, build_default_registry
from csa.notify.sender import format_notification

from tests.conftest import requires_docker
from tests.test_booking_agent import ask, said
from tests.test_bookings_api import TUESDAY, at, booked, meeting
from tests.test_bookings_bot import ELEVEN, NOW, TEN, THREE, TWO, Shop, a_type, pending_change, shop
from tests.test_bookings_mail import SENDER, FakeTransport, calendar_of, ics_value, unfolded
from tests.test_bookings_setup import BASE, make_person, make_type

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]
__all__ = ["shop"]  # the fixture, imported so this module can use it

TEN_TEXT = "Tuesday 4 March 2031, 10:00 am (2031-03-04 10:00)"
ELEVEN_TEXT = "Tuesday 4 March 2031, 11:00 am (2031-03-04 11:00)"
TEN_TEXT_MAIL = "4 Mar 2031, 10:00"  # as a mail writes it, in the company's zone
LAPSED = TEN + timedelta(hours=1)  # after a booking at ten has started
EARLIER = datetime(2026, 1, 1, tzinfo=UTC)  # before the real "now" the database writes with


# --- a company with Telegram and WhatsApp chats ---------------------------------------------------


@dataclass
class Chat:
    conversation: uuid.UUID
    trace: uuid.UUID
    endpoint: uuid.UUID


async def chat(
    shop: Shop,
    contact: uuid.UUID,
    *,
    channel: str = "telegram",
    enabled: bool = True,
    at_time: datetime = EARLIER,
) -> Chat:
    """The rows a customer's own message leaves: a chat, its route, and the message that came in."""
    credential, endpoint, conversation, trace = (uuid.uuid4() for _ in range(4))
    w, tenant = shop.w, shop.tenant
    await w.q(
        "INSERT INTO channel_credentials (id, client_id, channel, shop_id) VALUES (%s, %s, %s, %s)",
        str(credential),
        tenant,
        channel,
        None if channel == "telegram" else uuid.uuid4().hex,
    )
    await w.q(
        "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id, is_enabled)"
        " VALUES (%s, %s, %s, %s, %s)",
        str(endpoint),
        tenant,
        uuid.uuid4().hex,
        str(credential),
        enabled,
    )
    await w.q(
        "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread)"
        " VALUES (%s, %s, %s, %s, %s)",
        str(conversation),
        tenant,
        str(contact),
        channel,
        str(uuid.uuid4().int % 10**9),
    )
    await w.q(
        "INSERT INTO ingress_events (client_id, endpoint_id, contact_id, idempotency_key,"
        " payload_hash, trace_id, raw) VALUES (%s, %s, %s, %s, 'x', %s, '{}'::jsonb)",
        tenant,
        str(endpoint),
        str(contact),
        f"ing:{trace}",
        str(trace),
    )
    await w.q(
        "INSERT INTO messages (client_id, conversation_id, direction, author, body, redacted_body,"
        " idempotency_key, trace_id, created_at)"
        " VALUES (%s, %s, 'inbound', 'customer', 'hello', 'hello', %s, %s, %s)",
        tenant,
        str(conversation),
        f"in:{trace}",
        str(trace),
        at_time,
    )
    return Chat(conversation, trace, endpoint)


async def outbound(shop: Shop) -> list[dict[str, Any]]:
    """Every message written for a customer, with its delivery row."""
    return await shop.w.q(
        "SELECT m.body, m.idempotency_key AS key, m.send_authority AS authority,"
        " m.conversation_id::text AS conversation, m.trace_id::text AS trace,"
        " d.endpoint_id::text AS endpoint, d.trace_id::text AS delivery_trace, d.status"
        " FROM messages m LEFT JOIN outbound_deliveries d ON d.message_id = m.id"
        " WHERE m.client_id = %s AND m.direction = 'outbound' ORDER BY m.created_at, m.id",
        shop.tenant,
    )


async def staff(shop: Shop, booking_id: Any, action: str, version: int | None = None) -> Any:
    body = None if version is None else {"version": version}
    return await shop.team.send(shop.team.admin, f"/bookings/{booking_id}/{action}", body)


@pytest.fixture
def relay(shop: Shop) -> FakeTransport:
    transport = FakeTransport()
    shop.w.app.state.mailer = Mailer(transport, SENDER)
    return transport


async def pending_booking(
    shop: Shop, contact: uuid.UUID, kind: dict[str, Any], **extra: Any
) -> service.BookingRecord:
    """A booking the bot held as pending (Staff approve each reply)."""
    await shop.mode("suggest")
    made = await shop.book(contact, kind["id"], **extra)
    assert made.code == "pending" and made.booking is not None
    return made.booking


async def holds_of(shop: Shop, booking_id: Any) -> list[str]:
    rows = await shop.w.q(
        "SELECT lower(during)::text AS a FROM booking_holds WHERE booking_id = %s",
        str(booking_id),
    )
    return [r["a"] for r in rows]


async def row_of(shop: Shop, booking_id: Any) -> dict[str, Any]:
    (row,) = await shop.w.q(
        "SELECT status, version, starts_at, replaces_booking_id::text AS rep FROM bookings"
        " WHERE id = %s",
        str(booking_id),
    )
    return row


async def run_expiry(shop: Shop, when: datetime) -> dict[str, Any]:
    async with tx(shop.w.pool, shop.tenant) as conn:
        return await run_pending_expiry(conn, shop.client, FakeClock(when), batch_size=100)


# --- the message itself ---------------------------------------------------------------------------


async def test_a_telegram_message_appears_in_the_inbox_thread_and_is_delivered_by_the_real_sql(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    route = await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind, conversation_id=route.conversation)
    async with tx(shop.w.pool, shop.tenant) as conn:
        sent = await customer_messages.telegram_message(
            conn, shop.client, booking, key="booking:one", body="Hello customer", authority="human"
        )
    assert sent is True
    (message,) = await outbound(shop)
    assert message["conversation"] == str(route.conversation)
    # The trace is one the chat's own message came in on, so delivery's route check accepts it.
    assert message["trace"] == message["delivery_trace"] == str(route.trace)
    assert message["endpoint"] == str(route.endpoint) and message["status"] == "pending"
    # The inbox thread lists it, as it lists an approved reply.
    async with tx(shop.w.pool, shop.tenant) as conn:
        thread = await db.q.fetch_all(
            "inbox/list_messages.sql", (shop.client, route.conversation, None, None, 50), conn=conn
        )
    assert [(m["direction"], m["body"]) for m in thread] == [
        ("outbound", "Hello customer"),
        ("inbound", "hello"),
    ]
    # The delivery worker's own queries find the route and the message.
    (delivery,) = await shop.w.q(
        "UPDATE outbound_deliveries SET status = 'delivering' WHERE client_id = %s RETURNING id",
        shop.tenant,
    )
    async with tx(shop.w.pool, shop.tenant) as conn:
        found = await db.q.fetch_one(
            "delivery/check_route.sql", (shop.client, route.trace, route.endpoint), conn=conn
        )
        loaded = await db.q.fetch_one(
            "delivery/get_message.sql", (delivery["id"], shop.client), conn=conn
        )
    assert found is not None and loaded is not None
    assert loaded["body"] == "Hello customer" and loaded["channel"] == "telegram"
    assert loaded["send_authority"] == "human"


async def test_a_message_is_written_once_per_key(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind)
    results = []
    for _ in range(2):
        async with tx(shop.w.pool, shop.tenant) as conn:
            results.append(
                await customer_messages.telegram_message(
                    conn, shop.client, booking, key="booking:k", body="Once", authority="reviewed"
                )
            )
    assert results == [True, True]
    assert len(await outbound(shop)) == 1
    rows = await shop.w.q(
        "SELECT count(*) AS n FROM outbound_deliveries WHERE client_id = %s", shop.tenant
    )
    assert rows[0]["n"] == 1


async def test_with_no_telegram_route_nothing_is_written(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    other = await shop.customer("Another Customer")
    await chat(shop, other)  # somebody else's chat is never used
    booking = await pending_booking(shop, contact, kind)
    whatsapp = await chat(shop, contact, channel="whatsapp")  # and nothing goes on WhatsApp
    async with tx(shop.w.pool, shop.tenant) as conn:
        assert not await customer_messages.telegram_message(
            conn, shop.client, booking, key="booking:a", body="x", authority="human"
        )
        with_chat = service.BookingRecord(
            **{**booking.__dict__, "conversation_id": whatsapp.conversation}
        )
        assert not await customer_messages.telegram_message(
            conn, shop.client, with_chat, key="booking:b", body="x", authority="human"
        )
    assert await outbound(shop) == []


async def test_a_disabled_endpoint_is_no_route(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact, enabled=False)
    booking = await pending_booking(shop, contact, kind)
    async with tx(shop.w.pool, shop.tenant) as conn:
        assert not await customer_messages.telegram_message(
            conn, shop.client, booking, key="booking:a", body="x", authority="human"
        )


async def test_the_booking_own_chat_comes_first_then_the_latest(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old = await chat(shop, contact, at_time=EARLIER - timedelta(days=2))
    newer = await chat(shop, contact, at_time=EARLIER - timedelta(days=1))
    booking = await pending_booking(shop, contact, kind)
    async with tx(shop.w.pool, shop.tenant) as conn:
        await customer_messages.telegram_message(
            conn, shop.client, booking, key="booking:latest", body="x", authority="human"
        )
        own = service.BookingRecord(**{**booking.__dict__, "conversation_id": old.conversation})
        await customer_messages.telegram_message(
            conn, shop.client, own, key="booking:own", body="y", authority="human"
        )
    where = {m["key"]: m["conversation"] for m in await outbound(shop)}
    assert where == {
        "booking:latest": str(newer.conversation),
        "booking:own": str(old.conversation),
    }


async def deliver_all(shop: Shop) -> list[dict[str, Any]]:
    """Run the real delivery worker over the outbox, with Telegram faked at the HTTP layer."""
    sent: list[dict[str, Any]] = []

    def send(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 88}})

    owner = db.build_pool(shop.w.owner_dsn, max_size=2)
    await owner.open(wait=True)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            worker = DeliveryWorker(owner, shop.w.pool, client)
            while await worker.process_once():
                pass
    finally:
        await owner.close()
    return sent


async def with_token(shop: Shop, endpoint: uuid.UUID) -> None:
    (row,) = await shop.w.q(
        "SELECT credential_id::text AS id FROM webhook_endpoints WHERE id = %s", str(endpoint)
    )
    token = crypto.encrypt(
        "1:TOKEN", crypto.credential_aad("channel_credentials", "access_token", row["id"])
    )
    await shop.w.q(
        "UPDATE channel_credentials SET access_token = %s WHERE id = %s", token, row["id"]
    )


async def test_a_staff_message_and_an_expiry_notice_are_really_sent_while_a_person_has_taken_over(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_worker import _configure_test_settings

    _configure_test_settings(monkeypatch, app_dsn="postgresql://x", owner_dsn=shop.w.owner_dsn)
    kind = await a_type(shop)
    contact = await shop.customer()
    route = await chat(shop, contact)
    await with_token(shop, route.endpoint)
    confirm_me = await pending_booking(shop, contact, kind, conversation_id=route.conversation)
    await pending_booking(shop, contact, kind, conversation_id=route.conversation, starts_at=TWO)
    # The trace already carried the bot's own delivered reply, as it does in real life.
    (reply,) = await shop.w.q(
        "INSERT INTO messages (client_id, conversation_id, direction, author, body, redacted_body,"
        " idempotency_key, trace_id)"
        " VALUES (%s, %s, 'outbound', 'assistant', 'held', 'held', %s, %s) RETURNING id",
        shop.tenant,
        str(route.conversation),
        f"reply:{route.trace}",
        str(route.trace),
    )
    await shop.w.q(
        "INSERT INTO outbound_deliveries (client_id, endpoint_id, trace_id, conversation_id,"
        " message_id, status) VALUES (%s, %s, %s, %s, %s, 'delivered')",
        shop.tenant,
        str(route.endpoint),
        str(route.trace),
        str(route.conversation),
        str(reply["id"]),
    )
    # A person has taken over, and automation on old traces is suppressed.
    await shop.w.q(
        "UPDATE conversations SET owned_by = 'human_support', ai_suppressed_before = now(),"
        " ai_muted_until = NULL WHERE id = %s",
        str(route.conversation),
    )
    await shop.mode("shadow")
    done = await staff(shop, confirm_me.id, "confirm", confirm_me.version)
    assert done.status_code == 200 and done.json()["customer_told"] is True
    sent = await deliver_all(shop)
    assert len(sent) == 1 and "confirmed" in sent[0]["text"] and TEN_TEXT in sent[0]["text"]
    await shop.mode("suggest")
    assert (await run_expiry(shop, TWO + timedelta(minutes=1)))["told"] == 1
    sent = await deliver_all(shop)
    assert len(sent) == 1 and "in time" in sent[0]["text"]
    rows = await shop.w.q(
        "SELECT d.status, m.send_authority AS authority FROM outbound_deliveries d"
        " JOIN messages m ON m.id = d.message_id WHERE d.client_id = %s"
        " AND m.idempotency_key LIKE 'booking:%%' ORDER BY d.created_at",
        shop.tenant,
    )
    assert rows == [
        {"status": "delivered", "authority": "human"},
        {"status": "delivered", "authority": "reviewed"},
    ]


# --- staff confirm ------------------------------------------------------------------------------


async def test_confirming_a_bot_booking_tells_the_customer_even_with_replies_off(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind)
    await shop.mode("shadow")
    done = await staff(shop, booking.id, "confirm", booking.version)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "confirmed" and done.json()["customer_told"] is True
    assert done.json()["source"] == "bot" and done.json()["replaces_booking_id"] is None
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{booking.id}:confirmed:{booking.version + 1}"
    assert message["authority"] == "human" and message["status"] == "pending"
    body = message["body"]
    # Replies are Off, so the bot would not act on a reply: nothing invites one.
    assert "confirmed" in body and TEN_TEXT in body and "reply" not in body.lower()


async def test_confirming_for_a_whatsapp_customer_mails_them_and_writes_no_message(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact, channel="whatsapp")
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))
    booking = await pending_booking(shop, contact, kind)
    done = await staff(shop, booking.id, "confirm", booking.version)
    assert done.json()["customer_told"] is True and done.json()["email"] == "sent"
    assert relay.to() == ["c@example.test"]
    assert ics_value(relay.sent[0], "METHOD") == ["REQUEST"]
    assert await outbound(shop) == []


async def test_a_customer_who_cannot_be_reached_is_not_told(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    booking = await pending_booking(shop, contact, kind)
    done = await staff(shop, booking.id, "confirm", booking.version)
    assert done.status_code == 200
    assert done.json()["customer_told"] is False
    assert await outbound(shop) == []


async def test_telegram_and_an_address_get_the_message_and_the_calendar_file_once(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))
    booking = await pending_booking(shop, contact, kind)
    done = await staff(shop, booking.id, "confirm", booking.version)
    assert done.json()["customer_told"] is True
    assert len(await outbound(shop)) == 1
    (mail,) = relay.sent  # the confirmation carries the calendar file
    assert mail.to == "c@example.test" and ics_value(mail, "STATUS") == ["CONFIRMED"]


async def test_an_address_the_customer_typed_in_the_chat_is_used(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await shop.w.q(
        "UPDATE contacts SET customer_given_email = 'typed@example.test' WHERE id = %s",
        str(contact),
    )
    booking = await pending_booking(shop, contact, kind)
    done = await staff(shop, booking.id, "confirm", booking.version)
    assert done.json()["customer_told"] is True and relay.to() == ["typed@example.test"]


async def test_a_staff_made_pending_booking_still_tells_the_customer_nothing(
    shop: Shop, relay: FakeTransport
) -> None:
    person = await make_person(shop.team, "Aina", email="aina@example.test")
    kind = await make_type(
        shop.team, needs_confirmation=True, delivery="named", person_ids=[person["id"]]
    )
    ask_for = {
        "type_id": kind["id"],
        "people": [person["id"]],
        "walk_in": {"name": "Walk In", "email": "walk@example.test"},
    }
    made = await booked(shop.team, shop.team.staff, {**ask_for, "starts_at": at(10)})
    relay.sent.clear()
    gone = await staff(shop, made["id"], "decline")
    assert gone.status_code == 200 and gone.json()["customer_told"] is None
    assert relay.to() == ["aina@example.test"]  # the person delivering it, as before
    assert await outbound(shop) == []


# --- staff decline ------------------------------------------------------------------------------


async def test_declining_a_bot_booking_tells_the_customer_it_was_not_confirmed(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))
    booking = await pending_booking(shop, contact, kind)
    await shop.mode("shadow")
    gone = await staff(shop, booking.id, "decline", booking.version)
    assert gone.status_code == 200 and gone.json()["status"] == "cancelled"
    assert gone.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{booking.id}:declined" and message["authority"] == "human"
    assert "wasn't confirmed" in message["body"] and TEN_TEXT in message["body"]
    assert "reply" not in message["body"].lower()  # replies are Off
    assert relay.sent == []  # the chat message is the only word about it
    assert await holds_of(shop, booking.id) == []


async def test_declining_for_a_whatsapp_customer_mails_them(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact, channel="whatsapp")
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))
    booking = await pending_booking(shop, contact, kind)
    gone = await staff(shop, booking.id, "decline")
    assert gone.json()["customer_told"] is True and relay.to() == ["c@example.test"]
    # They never had the event, so the mail says it was not confirmed, with no calendar file.
    (mail,) = relay.sent
    assert mail.calendar is None
    assert "wasn't confirmed" in mail.text and TEN_TEXT_MAIL in mail.text
    assert "calendar" not in mail.text.lower() and "cancelled" not in mail.text.lower()
    assert "cancelled" not in mail.subject.lower()
    no_mail = await pending_booking(shop, await shop.customer("Phone Only"), kind, starts_at=TWO)
    quiet = await staff(shop, no_mail.id, "decline")
    assert quiet.json()["customer_told"] is False


async def test_a_customers_own_cancel_of_a_pending_booking_is_not_announced(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind)
    cancelled = await shop.cancel(contact, booking.id)
    assert cancelled.code == "cancelled"
    assert await outbound(shop) == [] and relay.sent == []


# --- the pending change -------------------------------------------------------------------------


async def test_confirming_a_pending_change_moves_the_original_and_frees_the_old_slot(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    original, request = old.booking, asked.booking
    done = await staff(shop, request.id, "confirm", request.version)
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["id"] == str(original.id) and out["status"] == "confirmed"
    assert out["starts_at"].startswith("2031-03-04T11:00") and out["customer_told"] is True
    assert out["version"] > original.version and out["replaces_booking_id"] is None
    # The request is closed and holds nothing; the original holds the new time and only that.
    assert (await row_of(shop, request.id))["status"] == "cancelled"
    assert await holds_of(shop, request.id) == []
    assert await holds_of(shop, original.id) == ["2031-03-04 11:00:00+00"]
    shown = await shop.team.admin.get(
        f"{BASE}/calendar?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    )
    assert [b["id"] for b in shown.json()["items"]] == [str(original.id)]
    # The ten o'clock slot is free again, for someone else.
    other = await shop.customer("Someone Else")
    assert (await shop.book(other, kind["id"], TEN)).code == "confirmed"
    # The same event: the original's UID, a higher SEQUENCE, and a message that says it moved.
    (mail,) = relay.sent
    assert ics_value(mail, "UID") == [f"{original.id}@fourstacks.my"]
    assert int(ics_value(mail, "SEQUENCE")[0]) > original.version
    assert "DTSTART:20310304T110000Z" in unfolded(calendar_of(mail))
    (message,) = await outbound(shop)
    assert "moved" in message["body"] and ELEVEN_TEXT in message["body"]
    assert message["key"] == f"booking:{original.id}:confirmed:{out['version']}"


async def test_a_request_to_move_a_booking_staff_made_still_tells_the_customer(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    made = await booked(
        shop.team,
        shop.team.admin,
        {"type_id": kind["id"], "starts_at": TEN.isoformat(), "contact_id": str(contact)},
    )
    assert made["source"] == "staff"
    await shop.mode("suggest")
    asked = await shop.change(contact, uuid.UUID(made["id"]), ELEVEN)
    assert asked.code == "change_pending" and asked.booking is not None
    assert asked.booking.source == "bot"
    done = await staff(shop, asked.booking.id, "confirm", asked.booking.version)
    assert done.status_code == 200 and done.json()["id"] == made["id"]
    assert done.json()["source"] == "staff" and done.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert "moved" in message["body"] and ELEVEN_TEXT in message["body"]


@pytest.mark.parametrize("how", ["decline", "expire"])
async def test_a_pending_change_that_is_declined_or_lapses_leaves_the_original_standing(
    shop: Shop, relay: FakeTransport, how: str
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    before = await row_of(shop, old.booking.id)
    held = await holds_of(shop, old.booking.id)
    if how == "decline":
        gone = await staff(shop, asked.booking.id, "decline", asked.booking.version)
        assert gone.status_code == 200 and gone.json()["customer_told"] is True
        assert gone.json()["replaces_booking_id"] == str(old.booking.id)
        assert relay.sent == []  # nothing about a cancelled booking: the original stands
    else:
        assert (await run_expiry(shop, LAPSED))["expired"] == 1
    assert await row_of(shop, old.booking.id) == before  # not even a version
    assert await holds_of(shop, old.booking.id) == held
    assert (await row_of(shop, asked.booking.id))["status"] == "cancelled"
    assert await holds_of(shop, asked.booking.id) == []
    (message,) = await outbound(shop)
    assert "stays on" in message["body"] and TEN_TEXT in message["body"]
    assert ELEVEN_TEXT in message["body"] and "reply" in message["body"].lower()
    assert "cancelled" not in message["body"].lower()


async def test_confirming_a_request_locks_the_original_first(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    async with await psycopg.AsyncConnection.connect(shop.w.owner_dsn) as holder:
        await holder.execute(
            "SELECT 1 FROM bookings WHERE id = %s FOR UPDATE", (str(old.booking.id),)
        )
        waiting = asyncio.create_task(
            staff(shop, asked.booking.id, "confirm", asked.booking.version)
        )
        await asyncio.sleep(1.5)
        assert not waiting.done()  # queued behind the original's lock
        async with await psycopg.AsyncConnection.connect(shop.w.owner_dsn) as probe:
            # The request is not locked yet, so the bot (original, then request) cannot deadlock.
            await probe.execute(
                "SELECT 1 FROM bookings WHERE id = %s FOR UPDATE NOWAIT", (str(asked.booking.id),)
            )
        await holder.rollback()
    assert (await waiting).status_code == 200


async def test_a_staff_cancel_of_the_original_withdraws_its_pending_change(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    gone = await staff(shop, old.booking.id, "cancel", old.booking.version)
    assert gone.status_code == 200
    assert (await row_of(shop, old.booking.id))["status"] == "cancelled"
    assert (await row_of(shop, asked.booking.id))["status"] == "cancelled"
    assert await holds_of(shop, asked.booking.id) == [] == await holds_of(shop, old.booking.id)


async def test_a_staff_move_of_the_original_withdraws_its_pending_change(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    moved = await shop.team.send(
        shop.team.admin,
        f"/bookings/{old.booking.id}",
        {"version": old.booking.version, "starts_at": TWO.isoformat()},
        "PATCH",
    )
    assert moved.status_code == 200, moved.text
    assert (await row_of(shop, asked.booking.id))["status"] == "cancelled"
    assert await holds_of(shop, asked.booking.id) == []
    assert await holds_of(shop, old.booking.id) == ["2031-03-04 14:00:00+00"]


async def test_a_failed_staff_move_leaves_the_pending_change_alone(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    # Eight in the evening is outside working hours and needs acknowledging: the move is refused
    # after the request was withdrawn, so the whole move rolls back with it.
    refused = await shop.team.send(
        shop.team.admin,
        f"/bookings/{old.booking.id}",
        {"version": old.booking.version, "starts_at": "2031-03-04T20:00:00Z"},
        "PATCH",
    )
    assert refused.status_code == 409 and refused.json()["detail"] == "needs_acknowledging"
    assert (await row_of(shop, asked.booking.id))["status"] == "pending"
    assert await holds_of(shop, asked.booking.id) == ["2031-03-04 11:00:00+00"]


async def test_declining_a_pending_original_withdraws_its_pending_change(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    original = await pending_booking(shop, contact, kind)
    async with tx(shop.w.pool, shop.tenant) as conn:
        request = await service.create_for(
            conn,
            shop.client,
            service.BOT,
            BookingIn(
                type_id=uuid.UUID(kind["id"]),
                starts_at=ELEVEN,
                contact_id=contact,
                people=[p.person_id for p in original.people],
            ),
            now=NOW,
            force_pending=True,
            expires_at=ELEVEN,
            replaces_booking_id=original.id,
        )
    gone = await staff(shop, original.id, "decline", original.version)
    assert gone.status_code == 200
    assert (await row_of(shop, request.id))["status"] == "cancelled"
    assert await holds_of(shop, request.id) == []


# --- expiry -------------------------------------------------------------------------------------


async def test_a_lapsed_pending_booking_is_cancelled_and_the_customer_told(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind)
    fresh = await run_expiry(shop, TEN - timedelta(minutes=1))
    assert fresh["expired"] == 0 and (await row_of(shop, booking.id))["status"] == "pending"
    stats = await run_expiry(shop, TEN)
    assert stats["expired"] == 1
    assert (await row_of(shop, booking.id))["status"] == "cancelled"
    assert await holds_of(shop, booking.id) == []
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{booking.id}:expired" and message["authority"] == "reviewed"
    assert "in time" in message["body"] and TEN_TEXT in message["body"]
    assert "reply" in message["body"].lower() and "book" in message["body"]
    again = await run_expiry(shop, TEN + timedelta(hours=1))
    assert again["expired"] == 0 and len(await outbound(shop)) == 1
    audits = await shop.w.q(
        "SELECT action FROM console_audit WHERE target_id = %s ORDER BY created_at",
        str(booking.id),
    )
    assert "bookings.booking.expired" in [a["action"] for a in audits]
    # The freed time can be booked.
    other = await shop.customer("Someone Else")
    await shop.mode("auto")
    assert (await shop.book(other, kind["id"], TEN)).code == "confirmed"


@pytest.mark.parametrize("gate", ["replies_off", "service_off"])
async def test_an_expiry_sends_nothing_when_replies_are_off_or_support_is_not_active(
    shop: Shop, gate: str
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind)
    if gate == "replies_off":
        await shop.mode("shadow")
    else:
        await shop.suspend()
    stats = await run_expiry(shop, TEN)
    # The hold still lapses, so a slot is not kept for ever; nobody is messaged.
    assert stats["expired"] == 1 and (await row_of(shop, booking.id))["status"] == "cancelled"
    assert await holds_of(shop, booking.id) == []
    assert await outbound(shop) == []


async def test_a_pending_booking_that_has_started_lapses_even_if_its_expiry_is_later(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind)
    # Moved earlier by staff, a pending booking keeps the expiry it was made with.
    await shop.w.q(
        "UPDATE bookings SET expires_at = %s WHERE id = %s",
        TEN + timedelta(days=2),
        str(booking.id),
    )
    assert (await run_expiry(shop, TEN - timedelta(hours=1)))["expired"] == 0
    assert (await run_expiry(shop, TEN + timedelta(minutes=1)))["expired"] == 1
    assert (await row_of(shop, booking.id))["status"] == "cancelled"


async def test_the_expiry_touches_only_bot_bookings_with_an_expiry(shop: Shop) -> None:
    person = await make_person(shop.team, "Aina")
    kind = await make_type(
        shop.team, needs_confirmation=True, delivery="named", person_ids=[person["id"]]
    )
    made = await booked(
        shop.team,
        shop.team.staff,
        {
            "type_id": kind["id"],
            "people": [person["id"]],
            "starts_at": at(10),
            "walk_in": {"name": "Walk In", "phone": "0123456789"},
        },
    )
    assert made["status"] == "pending" and made["source"] == "staff"
    confirmed = await booked(shop.team, shop.team.admin, meeting(person, start=at(14)))
    stats = await run_expiry(shop, datetime(2032, 1, 1, tzinfo=UTC))
    assert stats["expired"] == 0
    assert (await row_of(shop, made["id"]))["status"] == "pending"
    assert (await row_of(shop, confirmed["id"]))["status"] == "confirmed"


async def test_the_job_is_registered_every_five_minutes_and_skips_onboarding_companies(
    shop: Shop,
) -> None:
    job = build_default_registry().get("booking_pending_expiry")
    assert job is not None and job.per_client and job.every == timedelta(minutes=5)
    assert job.fn is run_pending_expiry
    kind = await a_type(shop)
    booking = await pending_booking(shop, await shop.customer(), kind)
    owner_pool = db.build_pool(shop.w.owner_dsn, max_size=2)
    await owner_pool.open(wait=True)
    try:
        runner = JobRunner(
            owner_pool, shop.w.pool, lock_conninfo=shop.w.owner_dsn, clock=FakeClock(LAPSED)
        )
        await shop.w.q("UPDATE clients SET status = 'onboarding' WHERE id = %s", shop.tenant)
        assert await runner.run_job_once("booking_pending_expiry") is True
        assert (await row_of(shop, booking.id))["status"] == "pending"
        await shop.w.q("UPDATE clients SET status = 'active' WHERE id = %s", shop.tenant)
        assert await runner.run_job_once("booking_pending_expiry") is True
        assert (await row_of(shop, booking.id))["status"] == "cancelled"
        runs = await shop.w.q(
            "SELECT stats FROM job_runs WHERE job = 'booking_pending_expiry' AND client_id = %s",
            shop.tenant,
        )
        assert runs[-1]["stats"]["expired"] == 1
    finally:
        await owner_pool.close()


# --- staff, and the bot's own cancel ------------------------------------------------------------


async def test_staff_are_told_of_a_pending_booking_with_no_customer_details(shop: Shop) -> None:
    await shop.w.q(
        "INSERT INTO staff_notification_targets (client_id, channel, address)"
        " VALUES (%s, 'telegram', '999')",
        shop.tenant,
    )
    kind = await a_type(shop)
    contact = await shop.customer("Distinct Customer Name")
    await shop.w.q(
        "UPDATE contacts SET phone = '+60123456789', email = 'c@example.test' WHERE id = %s",
        str(contact),
    )
    booking = await pending_booking(shop, contact, kind)
    (note,) = await shop.w.q(
        "SELECT event, priority, payload FROM staff_notifications WHERE client_id = %s",
        shop.tenant,
    )
    assert note["event"] == "booking_pending"
    assert note["payload"] == {"booking_id": str(booking.id)}
    text = format_notification(note["event"], note["priority"], note["payload"])
    for private in ("Distinct", "0123456789", "c@example.test", "Haircut"):
        assert private not in json.dumps(note["payload"]) + text
    assert "Booking" in text
    # A booking that stands asks nobody to do anything.
    await shop.mode("auto")
    assert (await shop.book(contact, kind["id"], ELEVEN)).code == "confirmed"
    rows = await shop.w.q("SELECT count(*) AS n FROM staff_notifications")
    assert rows[0]["n"] == 1


async def test_a_pending_change_also_tells_staff(shop: Shop) -> None:
    await shop.w.q(
        "INSERT INTO staff_notification_targets (client_id, channel, address)"
        " VALUES (%s, 'telegram', '999')",
        shop.tenant,
    )
    kind = await a_type(shop)
    old, asked = await pending_change(shop, kind, await shop.customer())
    assert asked.booking is not None
    rows = await shop.w.q(
        "SELECT payload FROM staff_notifications WHERE client_id = %s", shop.tenant
    )
    assert [r["payload"]["booking_id"] for r in rows] == [str(asked.booking.id)]


async def test_a_cancel_by_the_bot_removes_the_event_from_everyones_calendar(
    shop: Shop,
) -> None:
    aina = await make_person(shop.team, "Aina", email="aina.staff@example.test")
    kind = await make_type(shop.team, person_ids=[aina["id"]])
    contact = await shop.customer()
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))
    transport = FakeTransport()
    agent = BookingAgent(Mailer(transport, SENDER))
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    gone = await ask(
        shop,
        contact,
        "cancel",
        agent=agent,
        key="e9:t0-booking_agent",
        booking_id=str(made.booking.id),
    )
    assert said(gone).startswith("Booking cancelled")
    await agent.drain()
    # A chat reply cannot take an event out of a calendar, so the customer who was sent an
    # invitation gets the cancellation too.
    assert transport.to() == ["aina.staff@example.test", "c@example.test"]
    assert all(m.calendar is not None and m.calendar.method == "CANCEL" for m in transport.sent)


async def test_a_cancel_by_the_bot_of_a_never_confirmed_booking_mails_the_customer_nothing(
    shop: Shop,
) -> None:
    aina = await make_person(shop.team, "Aina", email="aina.staff@example.test")
    kind = await make_type(shop.team, person_ids=[aina["id"]])
    contact = await shop.customer()
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))
    transport = FakeTransport()
    agent = BookingAgent(Mailer(transport, SENDER))
    held = await pending_booking(shop, contact, kind)
    gone = await ask(
        shop, contact, "cancel", agent=agent, key="e9:t0-booking_agent", booking_id=str(held.id)
    )
    assert said(gone).startswith("Booking cancelled")
    await agent.drain()
    assert "c@example.test" not in transport.to()  # they never had an invitation to remove


async def test_the_api_says_who_made_a_booking_and_what_it_replaces(shop: Shop) -> None:
    kind = await a_type(shop)
    old, asked = await pending_change(shop, kind, await shop.customer())
    assert old.booking is not None and asked.booking is not None
    shown = await shop.team.admin.get(
        f"{BASE}/calendar?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    )
    assert isinstance(shown, httpx.Response) and shown.status_code == 200
    by_id = {b["id"]: b for b in shown.json()["items"]}
    assert by_id[str(old.booking.id)]["source"] == "bot"
    assert by_id[str(old.booking.id)]["replaces_booking_id"] is None
    assert by_id[str(asked.booking.id)]["replaces_booking_id"] == str(old.booking.id)


# --- fix round 1 ----------------------------------------------------------------------------------


async def patch_time(shop: Shop, booking: Any, when: datetime) -> Any:
    return await shop.team.send(
        shop.team.admin,
        f"/bookings/{booking.id}",
        {"version": booking.version, "starts_at": when.isoformat()},
        "PATCH",
    )


async def chat_with_address(shop: Shop, contact: uuid.UUID, channel: str = "telegram") -> None:
    await chat(shop, contact, channel=channel)
    await shop.w.q("UPDATE contacts SET email = 'c@example.test' WHERE id = %s", str(contact))


# I1: a staff cancel of a pending bot row is a decline for the customer.


async def test_a_staff_cancel_of_a_pending_bot_booking_tells_the_customer_like_a_decline(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await pending_booking(shop, contact, kind)
    gone = await staff(shop, booking.id, "cancel", booking.version)
    assert gone.status_code == 200 and gone.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{booking.id}:declined" and message["authority"] == "human"
    assert "wasn't confirmed" in message["body"] and TEN_TEXT in message["body"]


async def test_a_staff_cancel_of_a_pending_request_tells_the_customer_it_stands(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    gone = await staff(shop, asked.booking.id, "cancel", asked.booking.version)
    assert gone.status_code == 200 and gone.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{asked.booking.id}:declined"
    assert "stays on" in message["body"] and TEN_TEXT in message["body"]


async def test_a_staff_cancel_of_a_pending_booking_staff_made_tells_nobody(
    shop: Shop, relay: FakeTransport
) -> None:
    person = await make_person(shop.team, "Aina")
    kind = await make_type(
        shop.team, needs_confirmation=True, delivery="named", person_ids=[person["id"]]
    )
    made = await booked(
        shop.team,
        shop.team.admin,
        {
            "type_id": kind["id"],
            "people": [person["id"]],
            "starts_at": at(10),
            "walk_in": {"name": "Walk In", "email": "walk@example.test"},
        },
    )
    gone = await staff(shop, made["id"], "cancel")
    assert gone.json()["customer_told"] is None and relay.sent == []
    assert await outbound(shop) == []


# I2: a stale retry after staff confirmed a move says what is true.


async def test_a_retried_change_and_a_late_cancel_after_staff_confirmed_say_it_stands(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None
    await shop.mode("suggest")
    asked = await shop.change(contact, old.booking.id, ELEVEN, turn_key="retry-me")
    assert asked.code == "change_pending" and asked.booking is not None
    done = await staff(shop, asked.booking.id, "confirm", asked.booking.version)
    assert done.status_code == 200
    before = (await shop.rows(), await shop.audits("bookings.booking.cancelled"))
    again = await shop.change(contact, old.booking.id, ELEVEN, turn_key="retry-me")
    late = await shop.cancel(contact, asked.booking.id)
    for outcome in (again, late):
        assert outcome.code == "replay" and outcome.booking is not None
        assert outcome.booking.id == old.booking.id
        assert outcome.booking.status == "confirmed" and outcome.booking.starts_at == ELEVEN
        assert outcome.booking.replaces_booking_id is None
    assert (await shop.rows(), await shop.audits("bookings.booking.cancelled")) == before


async def test_the_reply_facts_never_say_a_confirmed_move_was_withdrawn(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    await shop.mode("suggest")
    first = await ask(
        shop,
        contact,
        "reschedule",
        key="e7:t0-booking_agent",
        booking_id=str(made.booking.id),
        starts_at="2031-03-04T11:00",
    )
    assert "Request to move" in said(first)
    (request,) = await shop.w.q(
        "SELECT id::text AS id, version FROM bookings WHERE replaces_booking_id = %s",
        str(made.booking.id),
    )
    assert (await staff(shop, request["id"], "confirm", request["version"])).status_code == 200
    retry = said(
        await ask(
            shop,
            contact,
            "reschedule",
            key="e7:t0-booking_agent",
            booking_id=str(made.booking.id),
            starts_at="2031-03-04T11:00",
        )
    )
    late = said(
        await ask(shop, contact, "cancel", key="e8:t0-booking_agent", booking_id=request["id"])
    )
    for text in (retry, late):
        assert "withdrawn" not in text and "will not go ahead" not in text
        assert text.startswith("Booking confirmed") and ELEVEN_TEXT in text


# I3 and I4: the mail a customer on WhatsApp gets.


async def test_a_declined_request_mails_a_customer_without_telegram_that_it_was_not_accepted(
    shop: Shop, relay: FakeTransport
) -> None:
    aina = await make_person(shop.team, "Aina", email="aina.staff@example.test")
    kind = await make_type(shop.team, person_ids=[aina["id"]])
    contact = await shop.customer()
    await chat_with_address(shop, contact, "whatsapp")
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    for how in ("decline", "cancel"):
        relay.sent.clear()
        if how == "cancel":  # a second request, ended with Cancel instead
            await shop.mode("suggest")
            asked = await shop.change(contact, old.booking.id, ELEVEN)
            assert asked.booking is not None
        gone = await staff(shop, asked.booking.id, how, asked.booking.version)
        assert gone.status_code == 200 and gone.json()["customer_told"] is True, gone.text
        assert gone.json()["email"] == "sent"
        (mail,) = relay.sent  # only the customer: the people on the booking had nothing sent
        assert mail.to == "c@example.test" and mail.calendar is None
        assert "wasn't accepted" in mail.text and "4 Mar 2031, 10:00" in mail.text
        assert "calendar" not in mail.text.lower()
    assert (await row_of(shop, old.booking.id))["status"] == "confirmed"


async def test_a_decline_mails_the_customer_plain_text_and_the_people_the_cancellation(
    shop: Shop, relay: FakeTransport
) -> None:
    aina = await make_person(shop.team, "Aina", email="aina.staff@example.test")
    kind = await make_type(shop.team, person_ids=[aina["id"]])
    contact = await shop.customer()
    await chat_with_address(shop, contact, "whatsapp")
    booking = await pending_booking(shop, contact, kind)
    gone = await staff(shop, booking.id, "decline")
    assert gone.json()["customer_told"] is True
    customer = relay.for_("c@example.test")
    person = relay.for_("aina.staff@example.test")
    assert customer.calendar is None and "wasn't confirmed" in customer.text
    assert person.calendar is not None and person.calendar.method == "CANCEL"


# M4: no invitation to reply that the bot would not act on.


@pytest.mark.parametrize("closed", ["replies_off", "service_off"])
async def test_a_message_does_not_invite_a_reply_the_bot_would_not_act_on(
    shop: Shop, closed: str
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    first = await pending_booking(shop, contact, kind)
    second = await pending_booking(shop, contact, kind, starts_at=TWO)
    done = await staff(shop, first.id, "confirm", first.version)
    assert done.json()["customer_told"] is True
    assert "reply" in (await outbound(shop))[0]["body"].lower()  # the same text, replies on
    if closed == "replies_off":
        await shop.mode("shadow")
    else:
        await shop.suspend()
    gone = await staff(shop, second.id, "decline", second.version)
    assert gone.json()["customer_told"] is True
    message = (await outbound(shop))[1]
    assert "wasn't confirmed" in message["body"] and "reply" not in message["body"].lower()


# M1: the bot locks the original before the request, as staff do.


async def test_the_bot_locks_the_original_before_the_request_it_was_given(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    async with await psycopg.AsyncConnection.connect(shop.w.owner_dsn) as holder:
        await holder.execute(
            "SELECT 1 FROM bookings WHERE id = %s FOR UPDATE", (str(old.booking.id),)
        )
        waiting = asyncio.create_task(shop.change(contact, asked.booking.id, THREE))
        await asyncio.sleep(1.5)
        assert not waiting.done()  # queued behind the original's lock
        async with await psycopg.AsyncConnection.connect(shop.w.owner_dsn) as probe:
            # The request is not locked yet, so a staff confirm (original, then request) is safe.
            await probe.execute(
                "SELECT 1 FROM bookings WHERE id = %s FOR UPDATE NOWAIT", (str(asked.booking.id),)
            )
        await holder.rollback()
    assert (await waiting).code == "moved"


# M8: staff move or cancel a confirmed booking, and a customer with a Telegram route is told.


async def test_a_staff_move_of_a_confirmed_booking_tells_a_customer_with_telegram(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat_with_address(shop, contact)
    made = (await shop.book(contact, kind["id"])).booking
    assert made is not None
    await shop.mode("shadow")  # a person's action goes even with replies Off
    moved = await patch_time(shop, made, TWO)
    assert moved.status_code == 200 and moved.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{made.id}:moved:{moved.json()['version']}"
    assert message["authority"] == "human"
    assert "moved" in message["body"] and "2:00 pm" in message["body"]
    assert "reply" not in message["body"].lower()
    (mail,) = relay.sent  # the email carries the calendar update, so both go
    assert mail.calendar is not None and mail.calendar.method == "REQUEST"


async def test_a_staff_cancel_of_a_confirmed_booking_tells_a_customer_with_telegram(
    shop: Shop, relay: FakeTransport
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat_with_address(shop, contact)
    made = (await shop.book(contact, kind["id"])).booking
    assert made is not None
    gone = await staff(shop, made.id, "cancel", made.version)
    assert gone.status_code == 200 and gone.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{made.id}:cancelled" and message["authority"] == "human"
    assert "cancelled" in message["body"] and TEN_TEXT in message["body"]
    (mail,) = relay.sent
    assert mail.calendar is not None and mail.calendar.method == "CANCEL"


async def test_a_staff_made_confirmed_booking_tells_its_customer_too(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    made = await booked(
        shop.team,
        shop.team.admin,
        {"type_id": kind["id"], "starts_at": TEN.isoformat(), "contact_id": str(contact)},
    )
    gone = await staff(shop, made["id"], "cancel")
    assert gone.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert message["key"] == f"booking:{made['id']}:cancelled"


async def test_a_withdrawn_request_is_mentioned_in_the_message(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None
    gone = await staff(shop, old.booking.id, "cancel", old.booking.version)
    assert gone.json()["customer_told"] is True
    (message,) = await outbound(shop)
    assert "cancelled" in message["body"] and "request" in message["body"]
    assert "withdrawn" in message["body"]


async def test_no_message_without_a_route_for_a_pending_booking_or_a_private_meeting(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = (await shop.book(contact, kind["id"])).booking  # no chat at all
    assert made is not None
    gone = await staff(shop, made.id, "cancel", made.version)
    assert gone.json()["customer_told"] is False and await outbound(shop) == []
    # A pending booking was never confirmed, so moving it tells the customer nothing.
    await chat(shop, contact)
    held = await pending_booking(shop, contact, kind, starts_at=TWO)
    moved = await patch_time(shop, held, THREE)
    assert moved.status_code == 200 and await outbound(shop) == []
    # A private meeting is for its attendees only.
    person = await make_person(shop.team, "Aina")
    secret = await booked(
        shop.team,
        shop.team.admin,
        {**meeting(person, start=at(9)), "private": True, "contact_id": str(contact)},
    )
    cancelled = await staff(shop, secret["id"], "cancel")
    assert cancelled.status_code == 200 and await outbound(shop) == []
