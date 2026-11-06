"""The customer's booking agent: what it books and what it tells the reply (stage 1, PR 2, Task 3).

The rules themselves are in `tests/test_bookings_bot.py`. These tests run the agent as the turn
does (an `AgentContext` with the turn's key) and read the facts the reply is written from.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from csa.agents.base import AgentContext
from csa.agents.staged.models import BookingInput
from csa.agents.staged.service import BookingAgent
from csa.bookings.turn_context import booking_context
from csa.console.mailer import Mailer
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.facts import AgentResult
from csa.contracts.plan import Intent, Plan
from csa.contracts.state import BookingContext, Context
from csa.core import db
from csa.core.tenancy import tx
from csa.graph.policy.handoff import decide_handoff

from tests.conftest import requires_docker
from tests.test_bookings_api import booked, meeting
from tests.test_bookings_bot import ELEVEN, NOW, TEN, TWO, Shop, a_type, pending_change, shop
from tests.test_bookings_mail import SENDER, FakeTransport
from tests.test_bookings_setup import make_person, make_type
from tests.test_coordinator import make_envelope

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]
__all__ = ["shop"]  # the fixture, imported so this module can use it

KEY = "event-1:t0-booking_agent"
TEN_TEXT = "Tuesday 4 March 2031, 10:00 am (2031-03-04 10:00)"
ELEVEN_TEXT = "Tuesday 4 March 2031, 11:00 am (2031-03-04 11:00)"
HOLD = "is not confirmed yet"
ASKED = "Nothing has been cancelled yet."


def context(
    shop: Shop,
    contact: uuid.UUID,
    *,
    key: str = KEY,
    channel: str = "telegram",
    now: datetime = NOW,
    chat: uuid.UUID | None = None,
    trace: uuid.UUID | None = None,
) -> AgentContext:
    return AgentContext(
        client_id=shop.client,
        contact_id=contact,
        trace_id=trace or uuid.uuid4(),
        conversation_id=chat,
        idempotency_key=key,
        channel=channel,
        pool=shop.w.pool,
        pending_actions=PendingActionStore(shop.w.pool),
        now=lambda: now,
    )


async def ask(
    shop: Shop,
    contact: uuid.UUID,
    action: str,
    *,
    key: str = KEY,
    channel: str = "telegram",
    now: datetime = NOW,
    agent: BookingAgent | None = None,
    chat: uuid.UUID | None = None,
    trace: uuid.UUID | None = None,
    **fields: Any,
) -> AgentResult:
    inp = BookingInput.model_validate({"action": action, **fields})
    return await (agent or BookingAgent()).run(
        context(shop, contact, key=key, channel=channel, now=now, chat=chat, trace=trace), inp
    )


async def cancel_it(
    shop: Shop,
    contact: uuid.UUID,
    booking_id: object,
    *,
    key: str = KEY,
    agent: BookingAgent | None = None,
    now: datetime = NOW,
) -> AgentResult:
    """A cancel as a customer makes one: the bot asks, and the next message says yes."""
    asked = await ask(
        shop,
        contact,
        "cancel",
        key=f"{key}:asked",
        agent=agent,
        now=now,
        booking_id=str(booking_id),
    )
    assert "? Reply yes to " in said(asked), said(asked)
    return await ask(
        shop,
        contact,
        "cancel",
        key=key,
        agent=agent,
        now=now,
        booking_id=str(booking_id),
        confirmed=True,
    )


def said(result: AgentResult) -> str:
    assert result.ok, result.error
    return "\n".join(f.text for f in result.facts)


async def make_it(
    shop: Shop,
    contact: uuid.UUID,
    kind: dict[str, Any],
    start: str = "2031-03-04T10:00",
    **extra: Any,
) -> AgentResult:
    return await ask(shop, contact, "book", type_id=kind["id"], starts_at=start, **extra)


# --- what each outcome says ---------------------------------------------------------------------


async def test_a_confirmed_booking_says_what_when_where_and_who(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    result = await make_it(shop, contact, kind)
    assert said(result) == (f"Booking confirmed: Haircut on {TEN_TEXT}, with Person 0, by phone.")
    assert not result.requires_human
    (row,) = await shop.rows()
    assert row["status"] == "confirmed" and row["source"] == "bot"


async def test_a_pending_booking_says_it_is_not_confirmed_yet(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.mode("suggest")
    text = said(await make_it(shop, await shop.customer(), kind))
    assert HOLD in text and "Booking requested" in text
    assert "Booking confirmed" not in text and "confirmed:" not in text
    assert [r["status"] for r in await shop.rows()] == ["pending"]


async def test_a_type_that_needs_confirmation_is_pending_in_auto(shop: Shop) -> None:
    kind = await a_type(shop, needs_confirmation=True)
    text = said(await make_it(shop, await shop.customer(), kind))
    assert HOLD in text and "Booking confirmed" not in text


async def test_the_place_and_link_are_said_for_a_booking_that_stands(shop: Shop) -> None:
    kind = await a_type(
        shop, location_kind="online", meeting_link="https://meet.example.test/room-1"
    )
    text = said(await make_it(shop, await shop.customer(), kind))
    assert "online" in text
    assert text.endswith("Meeting link: https://meet.example.test/room-1")
    place = await a_type(shop, location_kind="in_person", address="12 Jalan Contoh")
    near = await make_it(
        shop, await shop.customer(), place, "2031-03-04T14:00", key="event-2:t0-booking_agent"
    )
    assert "at 12 Jalan Contoh" in said(near)


@pytest.mark.parametrize("how", ["replies_off", "service_off"])
async def test_a_switched_off_bot_claims_nothing(shop: Shop, how: str) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    if how == "replies_off":
        await shop.mode("shadow")
    else:
        await shop.suspend()
    for action, fields in (
        ("book", {"type_id": kind["id"], "starts_at": "2031-03-04T10:00"}),
        ("availability", {"type_id": kind["id"]}),
        ("list", {}),
    ):
        text = said(await ask(shop, contact, action, **fields))
        assert text == "Booking by message is not available at the moment. Nothing was changed."
    for word in ("confirmed", "booked", "cancelled", "moved", "leave"):
        assert word not in text.lower()
    assert await shop.rows() == []


async def test_a_taken_time_says_so_and_offers_other_times(shop: Shop) -> None:
    kind = await a_type(shop)
    await make_it(shop, await shop.customer(), kind)
    text = said(await make_it(shop, await shop.customer(), kind, key="event-2:t0-booking_agent"))
    assert text.startswith("That time was just taken. Other free times for Haircut: ")
    offered = text.removeprefix("That time was just taken. Other free times for Haircut: ")
    assert TEN_TEXT not in offered
    assert 1 <= len(offered.rstrip(".").split("; ")) <= 3
    assert offered.startswith("Tuesday 4 March 2031, 9:00 am (2031-03-04 09:00); ")
    assert len(await shop.rows()) == 1


@pytest.mark.parametrize(
    "start",
    [
        "2031-03-04T10:07",  # off the quarter-hour grid
        "2031-03-04T22:00",  # after closing
        "2031-03-09T10:00",  # a Sunday
        "2031-02-01T10:00",  # in the past
        "2033-03-04T10:00",  # too far ahead
    ],
)
async def test_a_time_that_cannot_be_had_says_the_same_thing_for_every_reason(
    shop: Shop, start: str
) -> None:
    kind = await a_type(shop, max_advance_days=60)
    text = said(await make_it(shop, await shop.customer(), kind, start))
    assert text.startswith("Not available that day. Other free times for Haircut: ")
    assert "taken" not in text
    for reason in ("hours", "closed", "weekend", "notice", "advance", "leave", "grid"):
        assert reason not in text.lower()
    assert await shop.rows() == []


async def test_nothing_free_says_so_without_inventing_a_time(shop: Shop) -> None:
    kind = await a_type(shop, max_advance_days=0)
    text = said(await make_it(shop, await shop.customer(), kind, "2031-03-20T10:00"))
    assert text == "Not available that day. No other free times were found soon."


async def test_not_found_reads_the_same_however_it_arose(shop: Shop) -> None:
    kind = await a_type(shop)
    mine, other = await shop.customer(), await shop.customer()
    theirs = await make_it(shop, other, kind)
    assert theirs.ok
    (row,) = await shop.rows()
    staffer = await make_person(shop.team, "Aina")
    private = await booked(
        shop.team,
        shop.team.admin,
        meeting(staffer, contact_id=str(mine), private=True, start="2031-03-05T10:00:00Z"),
    )
    ids = [uuid.uuid4(), uuid.UUID(row["id"]), uuid.UUID(private["id"])]
    seen = set()
    for number, booking_id in enumerate(ids):
        cases: list[tuple[str, dict[str, Any]]] = [
            ("status", {}),
            ("cancel", {}),
            ("reschedule", {"starts_at": "2031-03-04T14:00"}),
        ]
        for action, fields in cases:
            result = await ask(
                shop,
                mine,
                action,
                key=f"event-{number}{action}:t0-booking_agent",
                booking_id=str(booking_id),
                **fields,
            )
            seen.add(said(result))
    assert seen == {"That could not be found. Nothing was changed."}
    (still,) = [r for r in await shop.rows() if r["id"] == row["id"]]
    assert still["status"] == "confirmed"


async def test_a_service_that_is_not_for_customers_is_not_found_and_not_listed(shop: Shop) -> None:
    people = [await make_person(shop.team, "Aina")]
    internal = await make_type(
        shop.team, name="Staff sync", audience="staff", person_ids=[people[0]["id"]]
    )
    public = await make_type(shop.team, name="Consultation", person_ids=[people[0]["id"]])
    contact = await shop.customer()
    text = said(await make_it(shop, contact, internal))
    assert text.startswith("That could not be found.")
    assert "Consultation" in text and "Staff sync" not in text
    assert (await make_it(shop, contact, public)).ok
    listed = said(await ask(shop, contact, "availability"))
    assert "Staff sync" not in listed


async def test_what_a_booking_write_says_reads_properly_to_a_customer_on_its_own(
    shop: Shop,
) -> None:
    """When a reply cannot be checked, these facts are sent as they stand (see the whole-turn
    tests): none may be a note to the composer, or carry an id."""
    kind = await a_type(shop, max_advance_days=60)
    await shop.w.q(
        "INSERT INTO booking_settings (client_id, customer_limit) VALUES (%s, 1)", shop.tenant
    )
    contact, nameless_one = await shop.customer(), await nameless(shop)
    texts = [
        said(await make_it(shop, contact, {**kind, "id": str(uuid.uuid4())})),  # no such service
        said(await make_it(shop, nameless_one, kind, key="event-2:t0-booking_agent")),
        said(
            await make_it(shop, contact, kind, channel="whatsapp", key="event-3:t0-booking_agent")
        ),
        said(
            await make_it(shop, contact, kind, "2031-03-04T14:00", key="event-4:t0-booking_agent")
        ),
        said(await make_it(shop, await shop.customer(), kind, key="event-5:t0-booking_agent")),
        said(
            await make_it(
                shop, await shop.customer(), kind, "2031-03-09T10:00", key="e6:t0-booking_agent"
            )
        ),
        said(
            await ask(
                shop, contact, "cancel", key="e7:t0-booking_agent", booking_id=str(uuid.uuid4())
            )
        ),
    ]
    assert any("I need your name" in t for t in texts) and any(
        "no email address" in t for t in texts
    )
    assert any("reached the number" in t for t in texts)
    for text in texts:
        lowered = text.lower()
        for note in (
            "service id",
            "booking id",
            "for lookups",
            "never show",
            "the customer",
            "this customer",
            "you may ask",
        ):
            assert note not in lowered, text


async def test_a_staff_made_request_that_was_never_confirmed_reads_as_not_found(
    shop: Shop,
) -> None:
    """The customer was never told of it, so a declined (or cancelled-while-pending) request of
    staff's is not theirs to read about, though its id is known. A confirmed one that is later
    cancelled still reads as cancelled: they were told of it."""
    kind = await a_type(shop, needs_confirmation=True)
    plain = await a_type(shop)
    contact = await shop.customer()
    asked = {"contact_id": str(contact)}
    declined = await booked(
        shop.team,
        shop.team.admin,
        {"type_id": kind["id"], "starts_at": "2031-03-04T10:00:00Z", **asked},
    )
    dropped = await booked(
        shop.team,
        shop.team.admin,
        {"type_id": kind["id"], "starts_at": "2031-03-04T14:00:00Z", **asked},
    )
    told = await booked(
        shop.team,
        shop.team.admin,
        {"type_id": plain["id"], "starts_at": "2031-03-05T10:00:00Z", **asked},
    )
    assert (declined["status"], dropped["status"], told["status"]) == (
        "pending",
        "pending",
        "confirmed",
    )
    for made, how in ((declined, "decline"), (dropped, "cancel"), (told, "cancel")):
        done = await shop.team.send(
            shop.team.admin, f"/bookings/{made['id']}/{how}", {"version": made["version"]}
        )
        assert done.status_code == 200, done.text
    for number, made in enumerate((declined, dropped)):
        key = f"event-{number}:t0-booking_agent"
        text = said(await ask(shop, contact, "status", key=key, booking_id=made["id"]))
        assert text == "That could not be found. Nothing was changed."
    found = said(
        await ask(shop, contact, "status", key="e3:t0-booking_agent", booking_id=told["id"])
    )
    assert found.startswith("Booking cancelled")


async def test_status_marks_a_booking_in_the_past(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    ahead = said(await ask(shop, contact, "status", booking_id=str(made.booking.id)))
    assert not ahead.startswith("This booking is in the past")
    later = NOW + timedelta(days=30)
    past = said(
        await ask(
            shop,
            contact,
            "status",
            booking_id=str(made.booking.id),
            now=later,
            key="e2:t0-booking_agent",
        )
    )
    assert past.startswith("This booking is in the past. Booking confirmed")


async def test_the_limit_is_said_without_a_booking(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.w.q(
        "INSERT INTO booking_settings (client_id, customer_limit) VALUES (%s, 1)", shop.tenant
    )
    contact = await shop.customer()
    await make_it(shop, contact, kind)
    text = said(
        await make_it(shop, contact, kind, "2031-03-04T14:00", key="event-2:t0-booking_agent")
    )
    assert "no new booking was made" in text and "Booking confirmed" not in text


async def test_past_the_cutoff_hands_over_to_a_person(shop: Shop) -> None:
    kind = await a_type(shop, change_cutoff_minutes=120)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    late = TEN - timedelta(minutes=30)
    cases: list[tuple[str, dict[str, Any]]] = [
        ("reschedule", {"starts_at": "2031-03-04T14:00"}),
        ("cancel", {}),
    ]
    for action, fields in cases:
        result = await ask(
            shop,
            contact,
            action,
            key=f"e-{action}:t0-booking_agent",
            now=late,
            booking_id=str(made.booking.id),
            **fields,
        )
        assert result.ok and result.requires_human
        assert result.handoff_reason == "booking_change_past_cutoff"
        assert said(result).endswith("A team member will help.")
        assert "confirmed" not in said(result)


async def test_a_new_time_touching_the_old_one_hands_over_to_a_person(shop: Shop) -> None:
    kind = await a_type(shop, needs_confirmation=True, buffer_minutes=15)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    done = await shop.team.send(
        shop.team.admin, f"/bookings/{made.booking.id}/confirm", {"version": made.booking.version}
    )
    assert done.status_code == 200
    result = await ask(
        shop, contact, "reschedule", booking_id=str(made.booking.id), starts_at="2031-03-04T10:15"
    )
    assert result.requires_human and result.handoff_reason == "booking_change_overlaps_itself"
    assert "team member will help" in said(result)


async def test_a_handoff_is_an_ordinary_handoff(shop: Shop) -> None:
    kind = await a_type(shop, change_cutoff_minutes=120)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    result = await ask(
        shop, contact, "cancel", now=TEN - timedelta(minutes=30), booking_id=str(made.booking.id)
    )
    plan = Plan(
        tasks=[], reply_strategy="auto_reply", intent=Intent.BOOKING, confidence=1, source="llm"
    )
    decision = decide_handoff(plan, [result], context=None, signals=None)
    assert decision is not None and decision.required
    assert decision.reason == "agent_failure"
    fine = await ask(shop, contact, "status", booking_id=str(made.booking.id))
    assert decide_handoff(plan, [fine], context=None, signals=None) is None


# --- changes and cancels ------------------------------------------------------------------------


async def test_a_move_in_place_and_a_cancel_say_what_happened(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    moved = said(
        await ask(
            shop,
            contact,
            "reschedule",
            booking_id=str(made.booking.id),
            starts_at="2031-03-04T11:00",
        )
    )
    assert moved.startswith(f"Booking moved and confirmed: Haircut on {ELEVEN_TEXT}")
    gone = said(await cancel_it(shop, contact, made.booking.id, key="e2:t0-booking_agent"))
    assert gone.startswith(f"Booking cancelled: Haircut on {ELEVEN_TEXT}")
    assert "confirmed" not in gone


async def test_a_pending_request_to_move_keeps_the_real_original_time(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    await shop.mode("suggest")
    text = said(
        await ask(
            shop,
            contact,
            "reschedule",
            booking_id=str(made.booking.id),
            starts_at="2031-03-04T14:00",
        )
    )
    assert text.startswith(
        "Request to move the booking to Tuesday 4 March 2031, 2:00 pm (2031-03-04 14:00) received."
    )
    assert "The new time is not confirmed yet" in text
    assert (
        f"The original booking (Haircut on {TEN_TEXT}, with Person 0, by phone) is still confirmed."
        in text
    )


async def test_a_withdrawn_request_says_the_original_stands_and_a_retry_says_the_same_truth(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    first = said(await cancel_it(shop, contact, asked.booking.id, key="e1:t0-booking_agent"))
    assert first.startswith("The request to move the booking is withdrawn.")
    assert f"Haircut on {TEN_TEXT}" in first and first.endswith("It is still confirmed.")
    assert "cancelled" not in first
    retry = said(
        await ask(
            shop,
            contact,
            "cancel",
            key="e1:t0-booking_agent",
            booking_id=str(asked.booking.id),
            confirmed=True,
        )
    )
    assert "is withdrawn and will not go ahead" in retry
    assert (
        f"The original booking (Haircut on {TEN_TEXT}, with Person 0, by phone) is still confirmed."
        in retry
    )
    assert "Booking confirmed" not in retry and "cancelled:" not in retry
    original = (await shop.rows())[0]
    assert original["status"] == "confirmed"


# --- a cancel is asked first --------------------------------------------------------------------


def question(when: str = TEN_TEXT) -> str:
    return f"Cancel your Haircut on {when}? Reply yes to cancel. {ASKED}"


async def asked_in_chat(
    shop: Shop, kind: dict[str, Any]
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, AgentResult]:
    """A confirmed booking at ten, a chat, and the customer's "cancel it" (message 1) answered with
    the question (message 2). Returns the customer, the chat, the booking and the answer."""
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    chat, trace = await conversation(shop, contact), uuid.uuid4()
    await say(shop, chat, 1, "inbound", "please cancel my haircut", trace=trace)
    result = await ask(
        shop,
        contact,
        "cancel",
        key="m1:t0",
        chat=chat,
        trace=trace,
        booking_id=str(made.booking.id),
    )
    # Sent as the turn's own reply, with no approval (auto mode).
    await say(shop, chat, 2, "outbound", said(result), trace=trace, key=f"{trace}:0")
    return contact, chat, made.booking.id, result


async def reply_to_it(
    shop: Shop,
    contact: uuid.UUID,
    chat: uuid.UUID,
    booking_id: uuid.UUID,
    number: int,
    text: str,
    *,
    now: datetime = NOW,
    key: str | None = None,
    trace: uuid.UUID | None = None,
) -> AgentResult:
    """The customer's next message (`number`), which the planner read as a yes to the question."""
    trace = trace or uuid.uuid4()
    await say(shop, chat, number, "inbound", text, trace=trace, at=now + timedelta(seconds=number))
    return await ask(
        shop,
        contact,
        "cancel",
        key=key or f"m{number}:t0",
        chat=chat,
        trace=trace,
        now=now,
        booking_id=str(booking_id),
        confirmed=True,
    )


async def test_a_cancel_asks_first_and_names_the_booking(shop: Shop) -> None:
    kind = await a_type(shop)
    _, _, booking_id, result = await asked_in_chat(shop, kind)
    assert said(result) == question()
    assert not result.requires_human
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]
    assert await shop.audits("bookings.booking.cancelled") == 0
    asked = [a for a in await shop.actions() if a["action"] == "ask_cancel"]
    assert [(a["turn_key"], a["booking"]) for a in asked] == [("m1:t0", str(booking_id))]


async def test_a_yes_with_no_question_before_it_only_asks(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    eager = await ask(shop, contact, "cancel", booking_id=str(made.booking.id), confirmed=True)
    assert said(eager) == question()
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]


async def test_a_yes_cancels_once_and_a_replayed_yes_does_not_cancel_again(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, chat, booking_id, _ = await asked_in_chat(shop, kind)
    done = await reply_to_it(shop, contact, chat, booking_id, 3, "yes", key="m3:t0")
    assert said(done).startswith(f"Booking cancelled: Haircut on {TEN_TEXT}")
    assert [r["status"] for r in await shop.rows()] == ["cancelled"]
    again = await ask(
        shop, contact, "cancel", key="m3:t0", chat=chat, booking_id=str(booking_id), confirmed=True
    )
    assert said(again) == said(done)
    # A second yes is not an answer to the question, and the booking is gone anyway.
    later = await reply_to_it(shop, contact, chat, booking_id, 4, "yes!")
    assert said(later).startswith("Booking cancelled")
    assert await shop.audits("bookings.booking.cancelled") == 1


async def test_a_malay_yes_is_a_yes(shop: Shop) -> None:
    """The planner judges the yes in any language; the context tells it there is a question, and
    the server needs nothing of the words."""
    kind = await a_type(shop)
    contact, chat, booking_id, _ = await asked_in_chat(shop, kind)
    trace = uuid.uuid4()
    await say(shop, chat, 3, "inbound", "ya, batalkan", trace=trace)
    seen = await build_context(shop, contact, chat, trace)
    assert seen is not None and seen.cancel_asked == booking_id
    done = await ask(
        shop,
        contact,
        "cancel",
        key="m3:t0",
        chat=chat,
        trace=trace,
        booking_id=str(booking_id),
        confirmed=True,
    )
    assert said(done).startswith("Booking cancelled")


@pytest.mark.parametrize(
    ("text", "then"), [("no, keep it", None), ("what time do you open?", "status")]
)
async def test_a_no_or_another_message_drops_the_question(
    shop: Shop, text: str, then: str | None
) -> None:
    kind = await a_type(shop)
    contact, chat, booking_id, _ = await asked_in_chat(shop, kind)
    trace = uuid.uuid4()
    await say(shop, chat, 3, "inbound", text, trace=trace)
    if then is not None:
        await ask(
            shop, contact, then, key="m3:t0", chat=chat, trace=trace, booking_id=str(booking_id)
        )
    # The message after that is no answer to the question: a yes now asks again.
    late_yes = await reply_to_it(shop, contact, chat, booking_id, 5, "yes")
    assert said(late_yes) == question()
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]


async def test_the_context_shows_the_question_only_to_the_next_message(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, chat, booking_id, _ = await asked_in_chat(shop, kind)
    next_trace = uuid.uuid4()
    await say(shop, chat, 3, "inbound", "ok", trace=next_trace)
    first = await build_context(shop, contact, chat, next_trace)
    assert first is not None and first.cancel_asked == booking_id
    after_trace = uuid.uuid4()
    await say(shop, chat, 4, "inbound", "actually, one more thing", trace=after_trace)
    after = await build_context(shop, contact, chat, after_trace)
    assert after is not None and after.cancel_asked is None


@pytest.mark.parametrize("why", ["expired", "stale"])
async def test_an_expired_or_stale_question_asks_again(shop: Shop, why: str) -> None:
    kind = await a_type(shop)
    contact, chat, booking_id, _ = await asked_in_chat(shop, kind)
    when, now = TEN_TEXT, NOW
    if why == "expired":
        now = NOW + timedelta(minutes=31)
    else:  # staff moved it since the question
        (row,) = await shop.w.q("SELECT version FROM bookings WHERE id = %s", str(booking_id))
        moved = await shop.team.send(
            shop.team.admin,
            f"/bookings/{booking_id}",
            {"version": row["version"], "starts_at": "2031-03-04T11:00:00Z"},
            "PATCH",
        )
        assert moved.status_code == 200, moved.text
        when = ELEVEN_TEXT
    third = uuid.uuid4()
    again = await reply_to_it(shop, contact, chat, booking_id, 3, "yes", now=now, trace=third)
    assert said(again) == question(when)
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]
    # The question asked again is sent, and answered by the next message.
    sent = now + timedelta(seconds=4)
    await say(shop, chat, 4, "outbound", said(again), trace=third, key=f"{third}:0", at=sent)
    done = await reply_to_it(shop, contact, chat, booking_id, 5, "yes", now=now)
    assert said(done).startswith(f"Booking cancelled: Haircut on {when}")


async def test_a_yes_to_another_booking_asks_about_that_one(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, chat, _, _ = await asked_in_chat(shop, kind)
    other = await shop.book(contact, kind["id"], ELEVEN)
    assert other.booking is not None
    wrong = await reply_to_it(shop, contact, chat, other.booking.id, 3, "yes")
    assert said(wrong) == question(ELEVEN_TEXT)
    assert {r["status"] for r in await shop.rows()} == {"confirmed"}


# --- a question counts only once the customer was sent it as drafted ----------------------------

FALLBACK = "A team member will follow up with you shortly."


async def held_question(shop: Shop, kind: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID, Any, Any]:
    """The bot's question, held for staff as a draft (Staff approve each reply, or a held auto
    reply): nothing is sent yet. Returns the customer, the chat, the booking and the asking turn."""
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    chat, trace = await conversation(shop, contact), uuid.uuid4()
    await say(shop, chat, 1, "inbound", "please cancel my haircut", trace=trace)
    result = await ask(
        shop,
        contact,
        "cancel",
        key="m1:t0",
        chat=chat,
        trace=trace,
        booking_id=str(made.booking.id),
    )
    assert said(result) == question()
    await shop.w.q(
        "INSERT INTO approvals (client_id, conversation_id, trace_id, draft, reason)"
        " VALUES (%s, %s, %s, %s, 'suggest_mode')",
        shop.tenant,
        str(chat),
        str(trace),
        said(result),
    )
    return contact, chat, made.booking.id, trace


async def settle(
    shop: Shop, chat: uuid.UUID, trace: uuid.UUID, outcome: str, *, at: datetime, text: str
) -> None:
    """Staff decide the held draft, as `approval/service._settle` does: the approval's status, and
    the message that goes out (the draft, the edited text or the fallback) under its own key."""
    (row,) = await shop.w.q(
        "UPDATE approvals SET status = %s, decided_by = 'staff', decided_at = %s, final_text = %s"
        " WHERE trace_id = %s RETURNING id",
        outcome,
        at,
        text,
        str(trace),
    )
    await say(
        shop, chat, 0, "outbound", text, trace=trace, key=f"appr:{row['id']}:{outcome}", at=at
    )


@pytest.mark.parametrize("outcome", ["rejected", "edited", "held"])
async def test_a_question_rejected_edited_or_still_held_is_no_question(
    shop: Shop, outcome: str
) -> None:
    """Staff refused it, replaced it, or have not looked yet: the customer never read it, so their
    "yes" (to whatever they did read) cancels nothing and is asked again."""
    kind = await a_type(shop)
    contact, chat, booking_id, trace = await held_question(shop, kind)
    decided = NOW + timedelta(minutes=5)
    if outcome == "rejected":
        await settle(shop, chat, trace, "rejected", at=decided, text=FALLBACK)
    elif outcome == "edited":
        moved = "Would you rather move it to Thursday? Reply yes."
        await settle(shop, chat, trace, "edited", at=decided, text=moved)
    seen = await build_context(shop, contact, chat)
    assert seen is not None and seen.cancel_asked is None
    yes = await reply_to_it(shop, contact, chat, booking_id, 3, "yes", now=decided)
    assert said(yes) == question()
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]


async def test_a_question_approved_as_drafted_is_answered_by_the_next_yes(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, chat, booking_id, trace = await held_question(shop, kind)
    sent = NOW + timedelta(minutes=5)
    await settle(shop, chat, trace, "approved", at=sent, text=question())
    yes = await reply_to_it(shop, contact, chat, booking_id, 3, "yes", now=sent)
    assert said(yes).startswith(f"Booking cancelled: Haircut on {TEN_TEXT}")


async def test_the_half_hour_runs_from_when_the_question_was_sent(shop: Shop) -> None:
    """Approved 40 minutes after it was drafted, the question can be answered for 30 minutes
    from then, not from the draft."""
    kind = await a_type(shop)
    contact, chat, booking_id, trace = await held_question(shop, kind)
    sent = NOW + timedelta(minutes=40)
    await settle(shop, chat, trace, "approved", at=sent, text=question())
    yes = await reply_to_it(
        shop, contact, chat, booking_id, 3, "yes", now=sent + timedelta(minutes=20)
    )
    assert said(yes).startswith("Booking cancelled")


async def test_a_yes_in_staff_approve_mode_cancels_and_only_the_reply_waits(shop: Shop) -> None:
    """Staff approve each reply: the question went out as drafted, so the customer's yes cancels
    then and there, as a cancel in that mode always has. Only the reply about it is held for staff
    (by the turn's reply gate): the cancel itself sends the customer nothing."""
    kind = await a_type(shop)
    await shop.mode("suggest")
    contact, chat, booking_id, trace = await held_question(shop, kind)
    sent = NOW + timedelta(minutes=5)
    await settle(shop, chat, trace, "approved", at=sent, text=question())
    before = await shop.w.q(
        "SELECT count(*) AS n FROM messages WHERE conversation_id = %s AND direction = 'outbound'",
        str(chat),
    )
    yes = await reply_to_it(shop, contact, chat, booking_id, 3, "yes", now=sent)
    assert said(yes).startswith("Booking cancelled")
    assert [r["status"] for r in await shop.rows()] == ["cancelled"]
    after = await shop.w.q(
        "SELECT count(*) AS n FROM messages WHERE conversation_id = %s AND direction = 'outbound'",
        str(chat),
    )
    assert after == before


async def test_in_staff_approve_mode_the_question_is_asked_and_nothing_is_cancelled(
    shop: Shop,
) -> None:
    """The question is a reply like any other: in Staff approve mode it is the draft staff see."""
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    await shop.mode("suggest")
    result = await ask(shop, contact, "cancel", booking_id=str(made.booking.id))
    assert said(result) == question()
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]
    await shop.mode("shadow")
    off = await ask(shop, contact, "cancel", key="e2:t0", booking_id=str(made.booking.id))
    assert said(off).startswith("Booking by message is not available")
    assert sorted(a["action"] for a in await shop.actions()) == ["ask_cancel", "book"]


# --- a retried message, and one write for each task ---------------------------------------------


async def test_a_retried_booking_answers_with_the_booking_it_made(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    first = said(await make_it(shop, contact, kind))
    again = said(await make_it(shop, contact, kind))
    assert again == first
    assert len(await shop.rows()) == 1


async def test_a_replay_of_a_since_cancelled_booking_does_not_read_as_booked(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await make_it(shop, contact, kind)
    await shop.w.q("UPDATE bookings SET status = 'cancelled' WHERE client_id = %s", shop.tenant)
    text = said(await make_it(shop, contact, kind))
    assert text.startswith("Booking cancelled:") and "Booking confirmed" not in text


async def test_a_replay_of_a_booking_staff_have_not_yet_confirmed_says_so(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await make_it(shop, contact, kind)
    await shop.w.q("UPDATE bookings SET status = 'pending' WHERE client_id = %s", shop.tenant)
    text = said(await make_it(shop, contact, kind))
    assert HOLD in text and "Booking confirmed" not in text


async def test_each_task_makes_one_booking_at_most(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await make_it(shop, contact, kind, key="event-1:t0-booking_agent")
    await make_it(shop, contact, kind, key="event-1:t0-booking_agent")
    await make_it(shop, contact, kind, "2031-03-04T14:00", key="event-1:t1-booking_agent")
    assert len(await shop.rows()) == 2
    assert [a["turn_key"] for a in await shop.actions()] == [
        "event-1:t0-booking_agent",
        "event-1:t1-booking_agent",
    ]


# --- availability -------------------------------------------------------------------------------


async def test_availability_with_no_type_lists_what_can_be_booked(shop: Shop) -> None:
    aina = await make_person(shop.team, "Aina")
    cut = await make_type(shop.team, name="Haircut", person_ids=[aina["id"]])
    sync = await make_type(
        shop.team, name="Consultation", person_ids=[aina["id"]], needs_confirmation=True
    )
    await make_type(shop.team, name="Staff sync", audience="staff", person_ids=[aina["id"]])
    result = await ask(shop, await shop.customer(), "availability")
    assert result.ok
    texts = [f.text for f in result.facts]
    assert len(texts) == 2
    haircut = next(t for t in texts if t.startswith("Can be booked: Haircut"))
    assert f"service id {cut['id']}" in haircut and "30 minutes" in haircut
    consult = next(t for t in texts if "Consultation" in t)
    assert f"service id {sync['id']}" in consult and "team confirms" in consult
    assert "Staff sync" not in said(result)
    assert await shop.rows() == []


async def test_availability_with_nothing_for_customers_says_so(shop: Shop) -> None:
    text = said(await ask(shop, await shop.customer(), "availability"))
    assert text == "Nothing can be booked by message at the moment."


def offered_times(text: str) -> list[str]:
    """The free times in an availability fact, as written."""
    return text.split(": ", 1)[1].rstrip(".").split("; ")


async def test_availability_for_a_service_lists_free_times_in_one_format(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    text = said(await ask(shop, contact, "availability", type_id=kind["id"], day="2031-03-04"))
    assert text.startswith(
        "Free times for Haircut: Tuesday 4 March 2031, 9:00 am (2031-03-04 09:00); "
    )
    # Five times spread over the working day (9:00 to 17:30), not the first hour of it.
    assert [t.split(", ")[1].split(" (")[0] for t in offered_times(text)] == [
        "9:00 am",
        "11:00 am",
        "1:15 pm",
        "3:30 pm",
        "5:30 pm",
    ]
    # A booked time is no longer offered.
    await make_it(shop, await shop.customer(), kind)
    after = said(await ask(shop, contact, "availability", type_id=kind["id"], day="2031-03-04"))
    # Nothing that would run into the booked half hour is offered.
    assert TEN_TEXT not in after and "9:45 am" not in after and "10:15 am" not in after
    assert len(offered_times(after)) == 5
    # With no day, the search starts now (a Monday noon), never in the past.
    today = said(await ask(shop, contact, "availability", type_id=kind["id"]))
    assert today.startswith("Free times for Haircut: Monday 3 March 2031, 1:00 pm ")
    assert "Monday 3 March 2031, 12:" not in today
    # A day with nothing free (Sunday) goes on to the next day that has times.
    sunday = said(await ask(shop, contact, "availability", type_id=kind["id"], day="2031-03-09"))
    assert sunday.startswith("Free times for Haircut: Monday 10 March 2031, 9:00 am ")


@pytest.mark.parametrize(
    ("part", "first", "last", "count"),
    [
        ("morning", "9:00 am", "11:45 am", 5),
        ("afternoon", "12:00 pm", "4:45 pm", 5),
        ("evening", "5:00 pm", "5:30 pm", 3),
    ],
)
async def test_availability_can_be_asked_for_part_of_the_day(
    shop: Shop, part: str, first: str, last: str, count: int
) -> None:
    kind = await a_type(shop)
    text = said(
        await ask(
            shop,
            await shop.customer(),
            "availability",
            type_id=kind["id"],
            day="2031-03-04",
            part_of_day=part,
        )
    )
    assert text.startswith(f"Free {part} times for Haircut: ")
    times_ = [t.split(", ")[1].split(" (")[0] for t in offered_times(text)]
    assert (times_[0], times_[-1], len(times_)) == (first, last, count)


async def test_a_part_of_the_day_with_nothing_free_says_so(shop: Shop) -> None:
    kind = await a_type(shop, max_advance_days=0)
    text = said(
        await ask(
            shop,
            await shop.customer(),
            "availability",
            type_id=kind["id"],
            day="2031-03-04",
            part_of_day="evening",
        )
    )
    assert text == "No free evening times for Haircut were found soon."


async def test_availability_names_a_person_only_when_one_person_can_take_the_slot(
    shop: Shop,
) -> None:
    kind = await a_type(shop, delivery="named")
    named = said(
        await ask(shop, await shop.customer(), "availability", type_id=kind["id"], day="2031-03-04")
    )
    assert "Tuesday 4 March 2031, 9:00 am (2031-03-04 09:00) with Person 0" in named
    several = await a_type(shop, people=2, delivery="named")
    open_ = said(
        await ask(
            shop, await shop.customer(), "availability", type_id=several["id"], day="2031-03-04"
        )
    )
    assert " with " not in open_


async def test_availability_for_an_unknown_service_is_not_found_and_lists_the_real_ones(
    shop: Shop,
) -> None:
    await a_type(shop)
    result = await ask(shop, await shop.customer(), "availability", type_id=str(uuid.uuid4()))
    assert [f.text for f in result.facts][0] == "That could not be found. Nothing was changed."
    assert any(f.text.startswith("Can be booked: Haircut") for f in result.facts)


async def test_no_fact_ever_says_why_someone_is_away(shop: Shop) -> None:
    from tests.test_bookings_api import person_on_leave

    away = await person_on_leave(shop.team)
    kind = await make_type(shop.team, person_ids=[away["id"]])
    contact = await shop.customer()
    texts = [
        said(await ask(shop, contact, "availability", type_id=kind["id"], day="2031-03-04")),
        said(await make_it(shop, contact, kind)),
    ]
    for text in texts:
        assert "leave" not in text.lower() and "away" not in text.lower()


# --- the customer's own details -----------------------------------------------------------------


async def nameless(shop: Shop) -> uuid.UUID:
    contact = uuid.uuid4()
    await shop.w.q(
        "INSERT INTO contacts (id, client_id) VALUES (%s, %s)", str(contact), shop.tenant
    )
    return contact


async def details(shop: Shop, contact: uuid.UUID) -> dict[str, Any]:
    (row,) = await shop.w.q(
        "SELECT full_name, email, customer_given_email FROM contacts WHERE id = %s", str(contact)
    )
    return row


async def test_the_bot_asks_for_a_name_it_does_not_have(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await nameless(shop)
    text = said(await make_it(shop, contact, kind))
    assert "I need your name" in text and "Nothing has been booked yet" in text
    assert await shop.rows() == []
    given = await make_it(shop, contact, kind, customer_name="Aina Binti Test")
    assert said(given).startswith("Booking confirmed")
    assert (await details(shop, contact))["full_name"] == "Aina Binti Test"
    (row,) = await shop.rows()
    assert row["status"] == "confirmed"


async def test_a_name_and_email_fill_only_what_is_empty(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer("Aina Binti Test")
    await shop.w.q("UPDATE contacts SET email = 'aina@example.test' WHERE id = %s", str(contact))
    await make_it(
        shop, contact, kind, customer_name="Someone Else", customer_email="other@example.test"
    )
    assert await details(shop, contact) == {
        "full_name": "Aina Binti Test",
        "email": "aina@example.test",
        "customer_given_email": None,  # an address is on file, so nothing is added beside it
    }


async def test_an_address_a_customer_types_is_kept_apart_from_the_contacts_own_email(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await make_it(shop, contact, kind, customer_email="aina@example.test")
    assert await details(shop, contact) == {
        "full_name": "Synthetic Customer",
        "email": None,
        "customer_given_email": "aina@example.test",
    }


async def test_a_whatsapp_booking_asks_for_an_email(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    result = await make_it(shop, contact, kind, channel="whatsapp")
    texts = [f.text for f in result.facts]
    assert texts[0].startswith("Booking confirmed")
    assert "no email address on file" in texts[1] and "fine to say no" in texts[1]
    assert "reminders" not in texts[1]  # nothing here sends reminders yet (no mailer)
    (row,) = await shop.rows()
    assert row["status"] == "confirmed"  # asking for an email never holds the booking back
    # Telegram is not asked, and an email given is saved and not asked for again.
    other = await shop.customer()
    assert (
        len((await make_it(shop, other, kind, "2031-03-04T14:00", key="e2:t0-booking_agent")).facts)
        == 1
    )
    third = await shop.customer()
    given = await make_it(
        shop,
        third,
        kind,
        "2031-03-04T15:00",
        key="e3:t0-booking_agent",
        channel="whatsapp",
        customer_email="third@example.test",
    )
    assert len(given.facts) == 1
    assert (await details(shop, third))["customer_given_email"] == "third@example.test"


# --- list and status ----------------------------------------------------------------------------


async def test_list_and_status_show_only_the_customers_own_visible_bookings(shop: Shop) -> None:
    aina = await make_person(shop.team, "Aina", email="aina.staff@example.test")
    kind = await make_type(shop.team, name="Haircut", person_ids=[aina["id"]])
    slow = await make_type(
        shop.team, name="Consultation", person_ids=[aina["id"]], needs_confirmation=True
    )
    mine, other = await shop.customer(), await shop.customer()
    await shop.w.q(
        "UPDATE contacts SET phone = '+60123456789', email = 'mine@example.test' WHERE id = %s",
        str(mine),
    )
    staff_confirmed = await booked(
        shop.team,
        shop.team.admin,
        {
            "type_id": kind["id"],
            "starts_at": "2031-03-05T10:00:00Z",
            "contact_id": str(mine),
            "guests": [{"name": "Hidden Guest", "email": "guest@example.test"}],
        },
    )
    staff_pending = await booked(
        shop.team,
        shop.team.admin,
        {"type_id": slow["id"], "starts_at": "2031-03-05T14:00:00Z", "contact_id": str(mine)},
    )
    assert staff_confirmed["status"] == "confirmed" and staff_pending["status"] == "pending"
    private = await booked(
        shop.team,
        shop.team.admin,
        meeting(aina, contact_id=str(mine), private=True, start="2031-03-06T10:00:00Z"),
    )
    others = await booked(
        shop.team,
        shop.team.admin,
        {"type_id": kind["id"], "starts_at": "2031-03-06T14:00:00Z", "contact_id": str(other)},
    )
    bot_made = await make_it(shop, mine, kind)
    assert bot_made.ok
    await shop.mode("suggest")
    await make_it(shop, mine, slow, "2031-03-07T10:00", key="event-9:t0-booking_agent")

    listing = await ask(shop, mine, "list")
    text = said(listing)
    # No booking id in what the customer is told (the context carries the ids the planner needs).
    for booking in (staff_confirmed, staff_pending, private, others):
        assert str(booking["id"]) not in text
    assert "booking id" not in text and "for lookups" not in text
    assert len(listing.facts) == 3  # the bot's confirmed, staff's confirmed, the bot's pending
    assert "Wednesday 5 March 2031, 10:00 am" in text  # staff-made and confirmed: their own
    assert "Wednesday 5 March 2031, 2:00 pm" not in text  # staff-made and pending: not theirs
    assert "Thursday 6 March 2031" not in text  # a private meeting, and another customer's
    assert "Aina" in text
    for secret in (
        "aina.staff@example.test",
        "Hidden Guest",
        "guest@example.test",
        "+60123456789",
        "mine@example.test",
        "Weekly sync",
        "created_by",
    ):
        assert secret not in text
    assert any(HOLD in f.text for f in listing.facts)

    for booking, visible in (
        (staff_confirmed, True),
        (staff_pending, False),
        (private, False),
        (others, False),
    ):
        found = said(await ask(shop, mine, "status", booking_id=booking["id"]))
        assert (found.startswith("Booking confirmed")) is visible, found
        assert ("could not be found" in found) is (not visible)
        for secret in ("Hidden Guest", "guest@example.test", "aina.staff@example.test"):
            assert secret not in found


async def test_list_puts_the_next_booking_first_however_many_old_ones_there_are(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    for n in range(30):
        day = datetime(2030, 1, 1, 10, tzinfo=UTC) + timedelta(days=n)
        await shop.w.q(
            "INSERT INTO bookings (client_id, contact_id, service, status, starts_at, ends_at)"
            " VALUES (%s, %s, 'Old', 'confirmed', %s, %s)",
            shop.tenant,
            str(contact),
            day,
            day + timedelta(minutes=30),
        )
    await make_it(shop, contact, kind)
    await make_it(shop, contact, kind, "2031-03-05T10:00", key="event-2:t0-booking_agent")
    facts = [f.text for f in (await ask(shop, contact, "list")).facts]
    assert len(facts) == 20
    assert TEN_TEXT in facts[0] and "Wednesday 5 March 2031" in facts[1]
    assert not facts[0].startswith("This booking is in the past")
    assert facts[2].startswith("This booking is in the past")
    assert "Saturday" not in facts[0]


async def test_list_with_no_bookings_says_so(shop: Shop) -> None:
    await a_type(shop)
    assert said(await ask(shop, await shop.customer(), "list")) == "The customer has no bookings."


# --- email --------------------------------------------------------------------------------------
#
# Mail goes out after the booking has committed, in the background: the reply never waits for the
# relay. `agent.drain()` waits for the mail still on its way, so a test can look at what arrived.


async def with_mail(shop: Shop) -> tuple[uuid.UUID, FakeTransport, BookingAgent]:
    contact = await shop.customer()
    await shop.w.q("UPDATE contacts SET email = 'aina@example.test' WHERE id = %s", str(contact))
    transport = FakeTransport()
    return contact, transport, BookingAgent(Mailer(transport, SENDER))


async def test_a_confirmed_booking_sends_the_invitation(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, transport, agent = await with_mail(shop)
    text = said(await make_it(shop, contact, kind, agent=agent))
    await agent.drain()
    assert transport.to() == ["aina@example.test"]
    assert "email" not in text  # the reply claims nothing about mail it has not seen arrive
    # A retried message changes nothing, so it sends nothing more.
    await make_it(shop, contact, kind, agent=agent)
    await agent.drain()
    assert len(transport.sent) == 1


async def test_a_move_and_a_cancel_send_their_mail_and_a_hold_sends_none(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, transport, agent = await with_mail(shop)
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    moved = await ask(
        shop,
        contact,
        "reschedule",
        agent=agent,
        booking_id=str(made.booking.id),
        starts_at="2031-03-04T11:00",
    )
    assert said(moved).startswith("Booking moved and confirmed")
    await agent.drain()
    assert len(transport.sent) == 1 and "11:00" in transport.sent[0].text
    gone = await cancel_it(shop, contact, made.booking.id, agent=agent, key="e2:t0-booking_agent")
    assert said(gone).startswith("Booking cancelled")
    await agent.drain()
    # A chat reply cannot remove an event from a calendar, so the cancellation is mailed.
    assert len(transport.sent) == 2
    await shop.mode("suggest")
    held = said(
        await make_it(
            shop, contact, kind, "2031-03-04T14:00", key="e3:t0-booking_agent", agent=agent
        )
    )
    await agent.drain()
    assert HOLD in held and len(transport.sent) == 2


async def test_a_pending_booking_moved_or_cancelled_in_chat_mails_nobody_on_it(shop: Shop) -> None:
    """The people on a booking the bot asked for are invited when staff confirm it, so while it
    waits they hear nothing of it: no "moved", and no cancellation of something never sent."""
    person = await make_person(shop.team, "Aina Own", email="aina.staff@example.test")
    kind = await make_type(shop.team, person_ids=[person["id"]], needs_confirmation=True)
    contact, transport, agent = await with_mail(shop)
    held = said(await make_it(shop, contact, kind, agent=agent))
    assert HOLD in held
    (row,) = await shop.rows()
    moved = await ask(
        shop,
        contact,
        "reschedule",
        key="e2:t0-booking_agent",
        agent=agent,
        booking_id=row["id"],
        starts_at="2031-03-04T11:00",
    )
    assert said(moved).startswith("Booking time changed")
    gone = await cancel_it(shop, contact, row["id"], agent=agent, key="e3:t0-booking_agent")
    assert said(gone).startswith("Booking cancelled")
    await agent.drain()
    assert transport.sent == []


async def test_an_address_the_customer_gave_gets_the_mail_when_the_contact_has_none(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    transport = FakeTransport()
    agent = BookingAgent(Mailer(transport, SENDER))
    contact = await shop.customer()
    await make_it(shop, contact, kind, customer_email="given@example.test", agent=agent)
    await agent.drain()
    assert transport.to() == ["given@example.test"]
    # The contact's own address wins when there is one.
    own, transport2 = await shop.customer(), FakeTransport()
    await shop.w.q("UPDATE contacts SET email = 'own@example.test' WHERE id = %s", str(own))
    agent2 = BookingAgent(Mailer(transport2, SENDER))
    await make_it(
        shop,
        own,
        kind,
        "2031-03-04T14:00",
        key="event-2:t0-booking_agent",
        customer_email="claim@example.test",
        agent=agent2,
    )
    await agent2.drain()
    assert transport2.to() == ["own@example.test"]


async def test_a_mail_failure_does_not_change_the_result(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, transport, agent = await with_mail(shop)
    transport.explode = RuntimeError("relay down")
    text = said(await make_it(shop, contact, kind, agent=agent))
    await agent.drain()  # and nothing is raised from the background
    assert text.startswith("Booking confirmed") and "email" not in text
    (row,) = await shop.rows()
    assert row["status"] == "confirmed"


async def test_with_no_mailer_the_booking_stands_and_no_mail_is_claimed(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, _, _ = await with_mail(shop)
    agent = BookingAgent()
    text = said(await make_it(shop, contact, kind, agent=agent))
    await agent.drain()
    assert text.startswith("Booking confirmed") and "email" not in text


class Slow:
    """A relay that does not answer until it is told to."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.sent: list[Any] = []

    def send(self, mail: Any, sender: str) -> None:
        self.started.set()
        assert self.release.wait(20), "the test never released the relay"
        self.sent.append(mail)


async def test_a_slow_relay_does_not_delay_the_reply_and_the_mail_still_arrives(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    await shop.w.q("UPDATE contacts SET email = 'aina@example.test' WHERE id = %s", str(contact))
    relay = Slow()
    agent = BookingAgent(Mailer(relay, SENDER, limit=30))
    try:
        began = time.monotonic()
        result = await asyncio.wait_for(make_it(shop, contact, kind, agent=agent), 5)
        took = time.monotonic() - began
        assert said(result).startswith("Booking confirmed") and took < 5
        assert await asyncio.to_thread(relay.started.wait, 5)  # the mail is on its way...
        assert relay.sent == []  # ...and has not left, though the reply is here
    finally:
        relay.release.set()
    await agent.drain()
    assert len(relay.sent) == 1 and relay.sent[0].to == "aina@example.test"


async def test_a_mail_is_sent_after_the_booking_has_committed(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from csa.bookings import notify

    kind = await a_type(shop)
    contact, _, agent = await with_mail(shop)
    seen: list[int] = []
    real = notify.notify_after_commit

    async def look(pool: Any, mail: Any, booking: Any, change: Any, **how: Any) -> Any:
        rows = await shop.rows()  # a different connection: it sees only what has committed
        seen.append(len(rows))
        return await real(pool, mail, booking, change, **how)

    monkeypatch.setattr(notify, "notify_after_commit", look)
    await make_it(shop, contact, kind, agent=agent)
    await agent.drain()
    assert seen == [1]


async def test_a_mail_that_raises_is_swallowed_and_logged_by_category(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from csa.bookings import notify
    from structlog.testing import capture_logs

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("aina@example.test is unreachable")

    monkeypatch.setattr(notify, "notify_after_commit", boom)
    kind = await a_type(shop)
    contact, _, agent = await with_mail(shop)
    with capture_logs() as logs:
        text = said(await make_it(shop, contact, kind, agent=agent))
        await agent.drain()
    assert text.startswith("Booking confirmed")
    assert "aina@example.test" not in repr(logs)
    assert any(entry.get("category") == "unexpected" for entry in logs)


# --- saving what the customer says about themselves ----------------------------------------------


async def test_a_whatsapp_customer_can_give_an_email_after_booking(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    booked_ = await make_it(shop, contact, kind, channel="whatsapp")
    ask_text = booked_.facts[1].text
    assert "no email address on file" in ask_text and "fine to say no" in ask_text
    saved = await ask(
        shop,
        contact,
        "save_details",
        key="event-2:t0-booking_agent",
        channel="whatsapp",
        customer_email="aina@example.test",
    )
    # A booking exists, but this agent has no mailer, so nothing claims reminders are live.
    assert said(saved) == "The email address is saved."
    found = await details(shop, contact)
    assert found["customer_given_email"] == "aina@example.test" and found["email"] is None
    (row,) = await shop.rows()
    assert row["status"] == "confirmed"  # saving details books and changes nothing


@pytest.mark.parametrize("booking", [True, False])
@pytest.mark.parametrize("mailer", [True, False])
async def test_the_reminder_sentence_needs_a_mailer_and_names_a_booking_only_if_one_exists(
    shop: Shop, booking: bool, mailer: bool
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    if booking:
        await shop.book(contact, kind["id"])
    agent = BookingAgent(Mailer(FakeTransport(), SENDER) if mailer else None)
    text = said(
        await ask(shop, contact, "save_details", customer_email="aina@example.test", agent=agent)
    )
    if not mailer:
        assert text == "The email address is saved."
    elif booking:
        assert text == (
            "The email address is saved. It will be used for reminders, including for the "
            "booking just made."
        )
    else:
        assert (
            text == "The email address is saved. It will be used for reminders of future bookings."
        )
    assert ("just made" in text) == (booking and mailer)


async def test_a_booking_in_the_past_or_cancelled_is_not_the_booking_just_made(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    await shop.cancel(contact, made.booking.id)
    agent = BookingAgent(Mailer(FakeTransport(), SENDER))
    text = said(
        await ask(shop, contact, "save_details", customer_email="aina@example.test", agent=agent)
    )
    assert "just made" not in text


async def test_save_details_saves_a_name_and_an_email_and_only_into_empty_fields(
    shop: Shop,
) -> None:
    contact = await nameless(shop)
    both = await ask(
        shop,
        contact,
        "save_details",
        customer_name="Aina Binti Test",
        customer_email="a@example.test",
    )
    assert said(both) == "The name is saved. The email address is saved."
    again = await ask(
        shop, contact, "save_details", customer_name="Someone Else", customer_email="b@example.test"
    )
    assert said(again) == ("Nothing was changed: a name and an email address are already on file.")
    assert await details(shop, contact) == {
        "full_name": "Aina Binti Test",
        "email": None,
        "customer_given_email": "a@example.test",
    }


async def test_a_stranger_claiming_a_customers_address_gets_nothing_of_theirs(shop: Shop) -> None:
    """An address typed into a chat is a claim, not an identity (the plan's own rule, ruled)."""
    kind = await a_type(shop)
    owner, stranger = await shop.customer("Real Owner"), await shop.customer("Stranger")
    await shop.w.q("UPDATE contacts SET email = 'taken@example.test' WHERE id = %s", str(owner))
    own = await shop.book(owner, kind["id"], ELEVEN)
    assert own.booking is not None
    chat = await conversation(shop, stranger)
    # The same answer as for any address: nothing says that another customer has it.
    text = said(await ask(shop, stranger, "save_details", customer_email="Taken@Example.test"))
    assert text == "The email address is saved."
    assert (await details(shop, stranger))["email"] is None
    assert (await details(shop, owner))["email"] == "taken@example.test"
    # No walk-in match: the same address still finds the real owner, never the stranger.
    async with tx(shop.w.pool, shop.tenant) as conn:
        match = await db.q.fetch_one(
            "bookings/contact_walk_in_insert.sql",
            {
                "client_id": shop.client,
                "name": "Walk In",
                "phone": None,
                "email": "taken@example.test",
            },
            conn=conn,
        )
        assert match is not None and match["id"] == owner
        # No Inbox `known`: the stranger is not labelled a customer by a claim.
        found = await db.q.fetch_one(
            "inbox/get_conversation_detail.sql",
            {"client_id": shop.client, "conversation_id": chat},
            conn=conn,
        )
    assert found is not None and found["known"] is False
    # And the owner's bookings are not the stranger's to list, move or cancel.
    listed = said(await ask(shop, stranger, "list", key="event-2:t0-booking_agent"))
    assert listed == "The customer has no bookings."
    moved = await ask(
        shop, stranger, "cancel", key="event-3:t0-booking_agent", booking_id=str(own.booking.id)
    )
    assert said(moved) == "That could not be found. Nothing was changed."
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]


@pytest.mark.parametrize("how", ["replies_off", "service_off"])
async def test_save_details_writes_nothing_when_the_bot_is_off(shop: Shop, how: str) -> None:
    contact = await nameless(shop)
    if how == "replies_off":
        await shop.mode("shadow")
    else:
        await shop.suspend()
    text = said(
        await ask(
            shop, contact, "save_details", customer_name="Aina", customer_email="a@example.test"
        )
    )
    assert text == "Booking by message is not available at the moment. Nothing was changed."
    assert await details(shop, contact) == {
        "full_name": None,
        "email": None,
        "customer_given_email": None,
    }


# --- the context the classifier and planner are given ---------------------------------------------


async def conversation(shop: Shop, contact: uuid.UUID) -> uuid.UUID:
    made = uuid.uuid4()
    await shop.w.q(
        "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread)"
        " VALUES (%s, %s, %s, 'telegram', %s)",
        str(made),
        shop.tenant,
        str(contact),
        f"thread-{made}",
    )
    return made


async def say(
    shop: Shop,
    chat: uuid.UUID,
    number: int,
    direction: str,
    body: str,
    *,
    redacted: str | None | bool = True,
    trace: uuid.UUID | None = None,
    key: str | None = None,
    at: datetime | None = None,
) -> None:
    """A stored message; `number` orders them (or `at`); `redacted=True` redacts nothing. `key`
    is the message's idempotency key: `{trace}:0` is a turn's own reply sent without approval."""
    shown = body if redacted is True else redacted
    await shop.w.q(
        "INSERT INTO messages (client_id, conversation_id, direction, author, body, redacted_body,"
        " idempotency_key, trace_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        shop.tenant,
        str(chat),
        direction,
        "contact" if direction == "inbound" else "assistant",
        body,
        shown,
        key or f"msg-{uuid.uuid4()}",
        str(trace) if trace else None,
        at or NOW + timedelta(seconds=number),
    )


async def build_context(
    shop: Shop, contact: uuid.UUID, chat: uuid.UUID, trace: uuid.UUID | None = None
) -> BookingContext | None:
    async with tx(shop.w.pool, shop.tenant) as conn:
        return await booking_context(conn, shop.client, contact, chat, trace or uuid.uuid4(), NOW)


async def test_the_booking_context_holds_today_types_upcoming_and_recent(shop: Shop) -> None:
    kind = await a_type(shop)
    staffer = await make_person(shop.team, "Aina")
    await make_type(shop.team, name="Staff sync", audience="staff", person_ids=[staffer["id"]])
    mine, other = await shop.customer(), await shop.customer()
    own = await shop.book(mine, kind["id"])
    assert own.booking is not None
    await shop.book(other, kind["id"], ELEVEN)
    await booked(
        shop.team,
        shop.team.admin,
        meeting(staffer, contact_id=str(mine), private=True, start="2031-03-06T10:00:00Z"),
    )
    await shop.w.q(
        "INSERT INTO bookings (client_id, contact_id, service, status, starts_at, ends_at)"
        " VALUES (%s, %s, 'Old', 'confirmed', %s, %s)",
        shop.tenant,
        str(mine),
        "2031-02-03T10:00Z",
        "2031-02-03T10:30Z",
    )
    chat = await conversation(shop, mine)
    now_trace = uuid.uuid4()
    for n in range(8):
        await say(shop, chat, n, "inbound" if n % 2 else "outbound", f"message {n}")
    await say(shop, chat, 8, "inbound", "call me on 0123456789", redacted="call me on [phone]")
    await say(shop, chat, 9, "outbound", "secret raw only", redacted=None)
    await say(shop, chat, 10, "inbound", "x" * 500)
    await say(shop, chat, 11, "inbound", "the message being answered now", trace=now_trace)

    ctx = await build_context(shop, mine, chat, now_trace)
    assert ctx is not None
    assert ctx.today == "Monday 2031-03-03" and ctx.timezone == "UTC"
    assert [(t.id, t.name, t.minutes) for t in ctx.types] == [
        (uuid.UUID(kind["id"]), "Haircut", 30)
    ]
    assert [(u.id, u.name, u.when, u.status) for u in ctx.upcoming] == [
        (own.booking.id, "Haircut", TEN_TEXT, "confirmed")
    ]
    assert [(m.author, m.text) for m in ctx.recent] == [
        ("assistant", "message 4"),
        ("customer", "message 5"),
        ("assistant", "message 6"),
        ("customer", "message 7"),
        ("customer", "call me on [phone]"),
        ("customer", "x" * 300),
    ]


async def test_a_company_with_more_than_twenty_services_still_gets_its_context(
    shop: Shop,
) -> None:
    person = await make_person(shop.team, "Aina")
    for n in range(21):
        await make_type(shop.team, name=f"Service {n:02d}", person_ids=[person["id"]])
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    ctx = await build_context(shop, contact, chat)
    assert ctx is not None, "21 services must not switch the context off"
    assert len(ctx.types) == 20 and ctx.more_types is True
    # A shorter list is complete, and says so.
    await shop.w.q(
        "UPDATE booking_types SET is_active = false WHERE client_id = %s AND name = 'Service 20'",
        shop.tenant,
    )
    shorter = await build_context(shop, contact, chat)
    assert shorter is not None and len(shorter.types) == 20 and shorter.more_types is False


async def test_the_booking_context_is_off_where_the_bot_cannot_book(shop: Shop) -> None:
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    assert await build_context(shop, contact, chat) is not None
    assert await build_context(shop, uuid.uuid4(), chat) is None  # no such contact
    await shop.suspend()
    assert await build_context(shop, contact, chat) is None
    await shop.w.q(
        "UPDATE console_service_packages SET status = 'active' WHERE client_id = %s", shop.tenant
    )
    assert await build_context(shop, contact, chat) is not None
    await shop.w.q(
        "UPDATE capability_registry SET client_enabled = false"
        " WHERE client_id = %s AND name = 'booking_agent'",
        shop.tenant,
    )
    assert await build_context(shop, contact, chat) is None


def envelope_for(shop: Shop, contact: uuid.UUID, chat: uuid.UUID) -> Any:
    plain = make_envelope("3 pm please")
    return plain.model_copy(
        update={
            "client_id": shop.client,
            "trace_id": uuid.uuid4(),
            "contact": plain.contact.model_copy(update={"id": contact}),
            "conversation": plain.conversation.model_copy(update={"id": chat}),
        }
    )


async def test_load_context_fills_the_booking_context_and_a_failure_leaves_it_empty(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from csa.turn import deps
    from structlog.testing import capture_logs

    await a_type(shop)
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    envelope = envelope_for(shop, contact, chat)
    real = deps.PostgresTurnDependencies(shop.w.pool)
    filled = await real.load_context(envelope)
    assert filled.booking is not None and filled.booking.timezone == "UTC"

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the database said aina@example.test")

    monkeypatch.setattr(deps, "booking_context", boom)
    with capture_logs() as logs:
        failed = await real.load_context(envelope)
    assert failed.booking is None and failed.active_order_ids == []  # the turn goes on
    assert any(entry.get("category") == "booking_context_failed" for entry in logs)
    assert "aina@example.test" not in repr(logs)


async def test_a_preview_gets_no_booking_context(shop: Shop) -> None:
    from csa.contracts.envelope import Channel
    from csa.turn import deps

    await a_type(shop)
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    real = deps.PostgresTurnDependencies(shop.w.pool)
    envelope = envelope_for(shop, contact, chat)
    assert (await real.load_context(envelope)).booking is not None  # a customer's chat has one
    preview = envelope.model_copy(update={"channel": Channel.WEB})
    assert (await real.load_context(preview)).booking is None  # the Overview preview does not


async def test_a_company_without_bookings_gets_the_context_it_always_had(shop: Shop) -> None:
    from csa.turn import deps

    contact = await shop.customer()
    chat = await conversation(shop, contact)
    await shop.w.q(
        "UPDATE capability_registry SET client_enabled = false"
        " WHERE client_id = %s AND name = 'booking_agent'",
        shop.tenant,
    )
    loaded = await deps.PostgresTurnDependencies(shop.w.pool).load_context(
        envelope_for(shop, contact, chat)
    )
    assert loaded.booking is None
    assert loaded.model_dump(exclude={"booking"}) == Context(
        flags={"human_muted": False}
    ).model_dump(exclude={"booking"})


# --- the agent's declared shape -----------------------------------------------------------------


def test_the_agent_declares_reads_commits_and_one_attempt() -> None:
    agent = BookingAgent()
    assert agent.ACTION_RISK == {
        "availability": "read",
        "list": "read",
        "status": "read",
        "book": "commit",
        "reschedule": "commit",
        "cancel": "commit",
        "save_details": "commit",
    }
    assert (agent.side_effects, agent.max_retries, agent.timeout_ms) == ("irreversible", 0, 10000)
    assert ELEVEN > TEN and TWO > ELEVEN
