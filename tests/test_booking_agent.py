"""The customer's booking agent: what it books and what it tells the reply (stage 1, PR 2, Task 3).

The rules themselves are in `tests/test_bookings_bot.py`. These tests run the agent as the turn
does (an `AgentContext` with the turn's key) and read the facts the reply is written from.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from csa.agents.base import AgentContext
from csa.agents.staged.models import BookingInput
from csa.agents.staged.service import BookingAgent
from csa.console.mailer import Mailer
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.facts import AgentResult
from csa.contracts.plan import Intent, Plan
from csa.graph.policy.handoff import decide_handoff

from tests.conftest import requires_docker
from tests.test_bookings_api import booked, meeting
from tests.test_bookings_bot import ELEVEN, NOW, TEN, TWO, Shop, a_type, pending_change, shop
from tests.test_bookings_mail import SENDER, FakeTransport
from tests.test_bookings_setup import make_person, make_type

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]
__all__ = ["shop"]  # the fixture, imported so this module can use it

KEY = "event-1:t0-booking_agent"
TEN_TEXT = "Tuesday 4 March 2031, 10:00 am"
ELEVEN_TEXT = "Tuesday 4 March 2031, 11:00 am"
HOLD = "is not confirmed yet"


def context(
    shop: Shop,
    contact: uuid.UUID,
    *,
    key: str = KEY,
    channel: str = "telegram",
    now: datetime = NOW,
) -> AgentContext:
    return AgentContext(
        client_id=shop.client,
        contact_id=contact,
        trace_id=uuid.uuid4(),
        conversation_id=None,
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
    mailer: Mailer | None = None,
    **fields: Any,
) -> AgentResult:
    inp = BookingInput.model_validate({"action": action, **fields})
    return await BookingAgent(mailer).run(
        context(shop, contact, key=key, channel=channel, now=now), inp
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
    assert offered.startswith("Tuesday 4 March 2031, 9:00 am; ")
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
    gone = said(
        await ask(
            shop, contact, "cancel", key="e2:t0-booking_agent", booking_id=str(made.booking.id)
        )
    )
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
    assert text.startswith("Request to move the booking to Tuesday 4 March 2031, 2:00 pm received.")
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
    first = said(
        await ask(
            shop, contact, "cancel", key="e1:t0-booking_agent", booking_id=str(asked.booking.id)
        )
    )
    assert first.startswith("The request to move the booking is withdrawn.")
    assert f"Haircut on {TEN_TEXT}" in first and first.endswith("It is still confirmed.")
    assert "cancelled" not in first
    retry = said(
        await ask(
            shop, contact, "cancel", key="e1:t0-booking_agent", booking_id=str(asked.booking.id)
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


async def test_availability_for_a_service_lists_free_times_in_one_format(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    text = said(await ask(shop, contact, "availability", type_id=kind["id"], day="2031-03-04"))
    assert text.startswith("Free times for Haircut: Tuesday 4 March 2031, 9:00 am; ")
    assert text.endswith(f"{TEN_TEXT}.") and text.count(";") == 4
    # A booked time is no longer offered.
    await make_it(shop, await shop.customer(), kind)
    after = said(await ask(shop, contact, "availability", type_id=kind["id"], day="2031-03-04"))
    # Nothing that would run into the booked half hour is offered.
    assert TEN_TEXT not in after and "9:45 am" not in after
    assert "Tuesday 4 March 2031, 10:30 am" in after
    # With no day, the search starts now (a Monday noon), never in the past.
    today = said(await ask(shop, contact, "availability", type_id=kind["id"]))
    assert today.startswith("Free times for Haircut: Monday 3 March 2031, ")
    assert "Monday 3 March 2031, 12:" not in today and "am" not in today.split(";")[0]


async def test_availability_names_a_person_only_when_one_person_can_take_the_slot(
    shop: Shop,
) -> None:
    kind = await a_type(shop, delivery="named")
    named = said(
        await ask(shop, await shop.customer(), "availability", type_id=kind["id"], day="2031-03-04")
    )
    assert f"{TEN_TEXT} with Person 0" in named
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
    (row,) = await shop.w.q("SELECT full_name, email FROM contacts WHERE id = %s", str(contact))
    return row


async def test_the_bot_asks_for_a_name_it_does_not_have(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await nameless(shop)
    text = said(await make_it(shop, contact, kind))
    assert "Ask the customer for their name" in text and "Nothing has been booked yet" in text
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
    }


async def test_an_email_another_customer_has_is_ignored_without_a_word(shop: Shop) -> None:
    kind = await a_type(shop)
    first, second = await shop.customer(), await shop.customer()
    await shop.w.q("UPDATE contacts SET email = 'taken@example.test' WHERE id = %s", str(first))
    result = await make_it(
        shop, second, kind, channel="whatsapp", customer_email="Taken@Example.test"
    )
    text = said(result)
    assert text.startswith("Booking confirmed") and "taken@example.test" not in text.lower()
    assert (await details(shop, second))["email"] is None


async def test_a_whatsapp_booking_asks_for_an_email(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    result = await make_it(shop, contact, kind, channel="whatsapp")
    texts = [f.text for f in result.facts]
    assert texts[0].startswith("Booking confirmed")
    assert "no email address on file" in texts[1] and "fine if they say no" in texts[1]
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
    assert (await details(shop, third))["email"] == "third@example.test"


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
    assert str(staff_confirmed["id"]) in text  # staff-made and confirmed: the customer's own
    assert str(staff_pending["id"]) not in text  # staff-made and pending: not theirs to see
    assert str(private["id"]) not in text and str(others["id"]) not in text
    assert (
        text.count("booking id") == 3
    )  # the bot's confirmed, staff's confirmed, the bot's pending
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


async def with_mail(shop: Shop) -> tuple[uuid.UUID, FakeTransport, Mailer]:
    contact = await shop.customer()
    await shop.w.q("UPDATE contacts SET email = 'aina@example.test' WHERE id = %s", str(contact))
    transport = FakeTransport()
    return contact, transport, Mailer(transport, SENDER)


async def test_a_confirmed_booking_sends_the_invitation(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, transport, mailer = await with_mail(shop)
    text = said(await make_it(shop, contact, kind, mailer=mailer))
    assert transport.to() == ["aina@example.test"]
    assert text.endswith("A confirmation email was sent to the customer.")
    # A retried message changes nothing, so it sends nothing more.
    again = said(await make_it(shop, contact, kind, mailer=mailer))
    assert len(transport.sent) == 1 and "email was sent" not in again


async def test_a_move_and_a_cancel_send_their_mail_and_a_hold_sends_none(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, transport, mailer = await with_mail(shop)
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    moved = said(
        await ask(
            shop,
            contact,
            "reschedule",
            mailer=mailer,
            booking_id=str(made.booking.id),
            starts_at="2031-03-04T11:00",
        )
    )
    assert moved.endswith("An email with the new time was sent to the customer.")
    gone = said(
        await ask(
            shop,
            contact,
            "cancel",
            mailer=mailer,
            key="e2:t0-booking_agent",
            booking_id=str(made.booking.id),
        )
    )
    assert gone.endswith("A cancellation email was sent to the customer.")
    assert len(transport.sent) == 2
    await shop.mode("suggest")
    held = said(
        await make_it(
            shop, contact, kind, "2031-03-04T14:00", key="e3:t0-booking_agent", mailer=mailer
        )
    )
    assert HOLD in held and "email was sent" not in held and len(transport.sent) == 2


async def test_a_mail_failure_does_not_change_the_result(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, transport, mailer = await with_mail(shop)
    transport.explode = RuntimeError("relay down")
    text = said(await make_it(shop, contact, kind, mailer=mailer))
    assert text.startswith("Booking confirmed") and "email was sent" not in text
    (row,) = await shop.rows()
    assert row["status"] == "confirmed"


async def test_with_no_mailer_the_booking_stands_and_no_mail_is_claimed(shop: Shop) -> None:
    kind = await a_type(shop)
    contact, _, _ = await with_mail(shop)
    text = said(await make_it(shop, contact, kind))
    assert text.startswith("Booking confirmed") and "email" not in text


async def test_a_mail_is_sent_after_the_booking_has_committed(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from csa.bookings import notify

    kind = await a_type(shop)
    contact, _, mailer = await with_mail(shop)
    seen: list[int] = []
    real = notify.notify_after_commit

    async def look(pool: Any, mail: Any, booking: Any, change: Any) -> Any:
        rows = await shop.rows()  # a different connection: it sees only what has committed
        seen.append(len(rows))
        return await real(pool, mail, booking, change)

    monkeypatch.setattr(notify, "notify_after_commit", look)
    await make_it(shop, contact, kind, mailer=mailer)
    assert seen == [1]


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
    }
    assert (agent.side_effects, agent.max_retries, agent.timeout_ms) == ("irreversible", 0, 25000)
    assert ELEVEN > TEN and TWO > ELEVEN
