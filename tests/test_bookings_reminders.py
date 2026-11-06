"""Reminders before a booking, on Telegram and by email, and the mail for a lapsed bot booking."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from csa.console.mailer import Mailer, OutgoingMail
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.bookings import run_pending_expiry, run_reminder_mail, run_reminder_queue
from csa.jobs.clock import FakeClock
from csa.jobs.runner import JobRunner, build_default_registry
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_bookings_api import at, booked, meeting
from tests.test_bookings_bot import TEN, Shop, shop
from tests.test_bookings_customer_messages import chat, outbound, pending_booking, run_expiry
from tests.test_bookings_mail import SENDER, FakeTransport
from tests.test_bookings_setup import make_person, make_type

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]
__all__ = ["shop"]  # the fixture, imported so this module can use it

ADDRESS = "12 Jalan Contoh, Kuala Lumpur"
TEN_TEXT = "Tuesday 4 March 2031, 10:00 am (2031-03-04 10:00)"
ZONED_TEXT = "Tuesday 4 March 2031, 5:00 pm (2031-03-04 17:00)"
EARLY = TEN - timedelta(hours=2)  # inside the default 24 hour window
CUSTOMER = "customer@example.test"
STAFF = "aina@example.test"
GUEST = "guest@example.test"


# --- helpers ------------------------------------------------------------------------------------


async def queue(shop: Shop, when: datetime = EARLY) -> dict[str, Any]:
    async with tx(shop.w.pool, shop.tenant) as conn:
        return await run_reminder_queue(conn, shop.client, FakeClock(when), batch_size=100)


async def mail(
    shop: Shop, transport: FakeTransport | None, when: datetime = EARLY, *, batch_size: int = 100
) -> dict[str, Any]:
    mailer = None if transport is None else Mailer(transport, SENDER)
    async with await psycopg.AsyncConnection.connect(shop.w.owner_dsn) as conn:
        return await run_reminder_mail(
            conn, shop.w.pool, mailer, FakeClock(when), batch_size=batch_size
        )


async def remind(shop: Shop, transport: FakeTransport, when: datetime = EARLY) -> None:
    """One run of both jobs, as the worker does every five minutes."""
    await queue(shop, when)
    await mail(shop, transport, when)


async def rows(shop: Shop) -> list[dict[str, Any]]:
    return await shop.w.q(
        "SELECT audience, channel, address, status, starts_at FROM booking_reminders"
        " WHERE client_id = %s ORDER BY audience, channel, address, starts_at",
        shop.tenant,
    )


def statuses(found: list[dict[str, Any]]) -> dict[tuple[str, str | None], str]:
    return {(r["audience"], r["address"]): r["status"] for r in found}


async def reminders_in_chat(shop: Shop) -> list[dict[str, Any]]:
    return [m for m in await outbound(shop) if ":reminder:" in m["key"]]


async def a_person_and_type(shop: Shop, **extra: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    person = await make_person(shop.team, "Aina Own", email=STAFF)
    kind = await make_type(
        shop.team, delivery="named", person_ids=[person["id"]], **{"name": "Haircut", **extra}
    )
    return person, kind


async def bot_booking(shop: Shop, contact: uuid.UUID, kind: dict[str, Any]) -> Any:
    await shop.mode("auto")
    made = await shop.book(contact, kind["id"], TEN)
    assert made.code == "confirmed" and made.booking is not None
    return made.booking


async def with_email(shop: Shop, contact: uuid.UUID, address: str = CUSTOMER) -> None:
    await shop.w.q("UPDATE contacts SET email = %s WHERE id = %s", address, str(contact))


async def staff_meeting(shop: Shop, **extra: Any) -> dict[str, Any]:
    """A confirmed meeting at ten: a staff attendee, an outside guest and a customer by email."""
    person = await make_person(shop.team, "Aina Own", email=STAFF)
    return await booked(
        shop.team,
        shop.team.admin,
        meeting(
            person,
            start=at(10),
            walk_in={"name": "Walk In", "email": CUSTOMER},
            guests=[{"name": "Outside Guest", "email": GUEST}],
            **extra,
        ),
    )


async def move(shop: Shop, made: dict[str, Any], hour: int) -> dict[str, Any]:
    done = await shop.team.send(
        shop.team.admin,
        f"/bookings/{made['id']}",
        {"version": made["version"], "starts_at": at(hour)},
        "PATCH",
    )
    assert done.status_code == 200, done.text
    return done.json()  # type: ignore[no-any-return]


# --- who is reminded ----------------------------------------------------------------------------


async def test_a_telegram_customer_gets_one_message_in_the_inbox_thread_and_no_email(
    shop: Shop,
) -> None:
    _, kind = await a_person_and_type(shop, location_kind="in_person", address=ADDRESS)
    contact = await shop.customer()
    await with_email(shop, contact)
    route = await chat(shop, contact)
    booking = await bot_booking(shop, contact, kind)
    relay = FakeTransport()
    await remind(shop, relay)
    await remind(shop, relay, EARLY + timedelta(minutes=5))
    (message,) = await reminders_in_chat(shop)
    assert message["conversation"] == str(route.conversation)
    assert message["key"] == f"booking:{booking.id}:reminder:{int(TEN.timestamp())}"
    assert message["authority"] == "reviewed" and message["status"] == "pending"
    assert TEN_TEXT in message["body"] and "Haircut" in message["body"]
    assert ADDRESS in message["body"]
    assert "google.com/maps" in message["body"] and "waze.com" in message["body"]
    # The customer is reminded on Telegram only; the staff member is still mailed.
    assert relay.to() == [STAFF]


async def test_a_customer_with_only_an_email_gets_one_email(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer("Email Only")
    await with_email(shop, contact)
    await bot_booking(shop, contact, kind)
    relay = FakeTransport()
    await remind(shop, relay)
    assert relay.to() == sorted([CUSTOMER, STAFF])
    mail_to_customer = relay.for_(CUSTOMER)
    assert mail_to_customer.subject.startswith("Reminder: Haircut")
    assert "4 Mar 2031, 10:00" in mail_to_customer.text
    assert "Hello Email Only," in mail_to_customer.text
    assert mail_to_customer.calendar is None
    assert await reminders_in_chat(shop) == []


async def test_staff_attendees_and_outside_guests_get_email(shop: Shop) -> None:
    await staff_meeting(shop)
    relay = FakeTransport()
    await remind(shop, relay)
    assert relay.to() == sorted([CUSTOMER, STAFF, GUEST])
    assert "Walk In" not in relay.for_(GUEST).text  # a guest learns nothing about the customer
    found = statuses(await rows(shop))
    assert found == {
        ("customer", CUSTOMER): "sent",
        ("staff", STAFF): "sent",
        ("staff", GUEST): "sent",
    }


async def test_each_reminder_goes_once(shop: Shop) -> None:
    await staff_meeting(shop)
    relay = FakeTransport()
    await remind(shop, relay)
    await remind(shop, relay, EARLY + timedelta(minutes=5))
    await remind(shop, relay, EARLY + timedelta(minutes=10))
    assert relay.to() == sorted([CUSTOMER, STAFF, GUEST])
    assert len(await rows(shop)) == 3


async def test_a_pending_booking_gets_no_reminder(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await chat(shop, contact)
    await pending_booking(shop, contact, kind)
    relay = FakeTransport()
    await remind(shop, relay)
    assert await rows(shop) == [] and relay.sent == []
    assert await reminders_in_chat(shop) == []


async def test_a_booking_that_has_started_or_is_far_off_gets_no_reminder(shop: Shop) -> None:
    await staff_meeting(shop)
    relay = FakeTransport()
    await remind(shop, relay, TEN - timedelta(hours=25))
    await remind(shop, relay, TEN + timedelta(minutes=1))
    assert await rows(shop) == [] and relay.sent == []


async def test_a_booking_made_inside_the_reminder_window_is_reminded_on_the_next_run(
    shop: Shop,
) -> None:
    await staff_meeting(shop)
    relay = FakeTransport()
    # Made two hours ahead with a 24 hour reminder: the time to remind has already gone by.
    await remind(shop, relay, TEN - timedelta(minutes=2 * 60))
    assert relay.to() == sorted([CUSTOMER, STAFF, GUEST])


async def test_the_company_chooses_how_early(shop: Shop) -> None:
    await staff_meeting(shop)
    await shop.w.q(
        "INSERT INTO booking_settings (client_id, reminder_minutes) VALUES (%s, 60)", shop.tenant
    )
    relay = FakeTransport()
    await remind(shop, relay, TEN - timedelta(minutes=61))
    assert relay.sent == []
    await remind(shop, relay, TEN - timedelta(minutes=60))
    assert len(relay.sent) == 3


async def test_a_private_meeting_reminds_its_attendees_and_guests_never_the_customer(
    shop: Shop,
) -> None:
    await staff_meeting(shop, private=True)
    relay = FakeTransport()
    await remind(shop, relay)
    assert relay.to() == sorted([STAFF, GUEST])
    assert all(r["audience"] == "staff" for r in await rows(shop))


async def test_a_whatsapp_customer_with_no_email_gets_no_reminder_and_no_message(
    shop: Shop,
) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await chat(shop, contact, channel="whatsapp")
    await bot_booking(shop, contact, kind)
    relay = FakeTransport()
    await remind(shop, relay)
    assert relay.to() == [STAFF]  # only the staff member
    assert await reminders_in_chat(shop) == []
    assert [r["audience"] for r in await rows(shop)] == ["staff"]


async def test_a_whatsapp_customer_with_an_email_gets_email_only(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await chat(shop, contact, channel="whatsapp")
    await bot_booking(shop, contact, kind)
    relay = FakeTransport()
    await remind(shop, relay)
    assert CUSTOMER in relay.to()
    assert await reminders_in_chat(shop) == []


async def test_an_address_the_customer_gave_in_chat_is_used(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await shop.w.q(
        "UPDATE contacts SET customer_given_email = 'given@example.test' WHERE id = %s",
        str(contact),
    )
    await bot_booking(shop, contact, kind)
    relay = FakeTransport()
    await remind(shop, relay)
    assert "given@example.test" in relay.to()


# --- the company's gates ------------------------------------------------------------------------


@pytest.mark.parametrize("gate", ["replies_off", "service_off"])
async def test_no_customer_reminder_while_replies_are_off_or_support_is_suspended(
    shop: Shop, gate: str
) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await chat(shop, contact)
    await bot_booking(shop, contact, kind)
    if gate == "replies_off":
        await shop.mode("shadow")
    else:
        await shop.suspend()
    relay = FakeTransport()
    await remind(shop, relay)
    # Staff still get theirs; the customer gets nothing, on any channel.
    assert relay.to() == [STAFF]
    assert await reminders_in_chat(shop) == []
    assert [r["audience"] for r in await rows(shop)] == ["staff"]


async def test_replies_turned_off_between_queuing_and_sending_holds_the_customer_mail(
    shop: Shop,
) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await bot_booking(shop, contact, kind)
    await queue(shop)
    await shop.mode("shadow")
    relay = FakeTransport()
    await mail(shop, relay)
    assert relay.to() == [STAFF]
    assert statuses(await rows(shop))[("customer", CUSTOMER)] == "dropped"


async def test_a_bookings_only_company_reminds_its_customers_by_email(shop: Shop) -> None:
    # No Customer support package, so no replies setting: nothing holds the customer's reminder.
    await shop.w.q(
        "DELETE FROM console_service_packages"
        " WHERE client_id = %s AND package = 'customer_support'",
        shop.tenant,
    )
    await staff_meeting(shop)
    relay = FakeTransport()
    await remind(shop, relay)
    assert relay.to() == sorted([CUSTOMER, STAFF, GUEST])


# --- moved and cancelled ------------------------------------------------------------------------


async def test_a_moved_booking_gets_a_new_reminder_and_no_stale_one(shop: Shop) -> None:
    made = await staff_meeting(shop)
    await queue(shop)  # queued for ten, not sent yet
    moved = await move(shop, made, 11)
    relay = FakeTransport()
    await mail(shop, relay)
    assert relay.sent == []  # the ten o'clock reminders are stale
    assert {r["status"] for r in await rows(shop)} == {"dropped"}
    await remind(shop, relay)
    assert relay.to() == sorted([CUSTOMER, STAFF, GUEST])
    for sent in relay.sent:
        assert "11:00" in sent.text and "10:00" not in sent.text
    assert moved["version"] == 2


async def test_a_booking_moved_away_and_back_is_queued_again_but_a_sent_one_is_not_repeated(
    shop: Shop,
) -> None:
    made = await staff_meeting(shop)
    await queue(shop)
    away = await move(shop, made, 11)
    relay = FakeTransport()
    await mail(shop, relay)  # drops the ten o'clock rows
    assert {r["status"] for r in await rows(shop)} == {"dropped"}
    back = await move(shop, away, 10)
    await remind(shop, relay)  # back at ten: the dropped rows are queued again, and sent
    assert relay.to() == sorted([CUSTOMER, STAFF, GUEST])
    assert {r["status"] for r in await rows(shop)} == {"sent"}
    assert len(await rows(shop)) == 3
    # Sent already: away and back again reminds nobody a second time.
    again = await move(shop, await move(shop, back, 11), 10)
    assert again["version"] == 5
    await remind(shop, relay, EARLY + timedelta(minutes=5))
    assert len(relay.sent) == 3


async def test_a_cancelled_booking_gets_none_including_between_queuing_and_sending(
    shop: Shop,
) -> None:
    made = await staff_meeting(shop)
    await queue(shop)
    gone = await shop.team.send(
        shop.team.admin, f"/bookings/{made['id']}/cancel", {"version": made["version"]}
    )
    assert gone.status_code == 200, gone.text
    relay = FakeTransport()
    await mail(shop, relay)
    assert relay.sent == []
    assert {r["status"] for r in await rows(shop)} == {"dropped"}
    # And a cancelled booking is never queued in the first place.
    await queue(shop, EARLY + timedelta(minutes=5))
    assert len(await rows(shop)) == 3


async def test_a_telegram_reminder_for_a_cancelled_booking_is_never_written(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await chat(shop, contact)
    booking = await bot_booking(shop, contact, kind)
    await shop.cancel(contact, booking.id)
    await queue(shop)
    assert await reminders_in_chat(shop) == []


# --- the address is read when the mail is sent ---------------------------------------------------


async def test_a_removed_address_drops_the_row(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await bot_booking(shop, contact, kind)
    await queue(shop)
    await shop.w.q("UPDATE contacts SET email = NULL WHERE id = %s", str(contact))
    relay = FakeTransport()
    await mail(shop, relay)
    assert relay.to() == [STAFF]
    assert statuses(await rows(shop))[("customer", CUSTOMER)] == "dropped"


async def test_an_address_saved_after_queuing_is_used(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await bot_booking(shop, contact, kind)
    relay = FakeTransport()
    await remind(shop, relay)
    assert relay.to() == [STAFF]  # nothing to send the customer yet
    await with_email(shop, contact)
    await remind(shop, relay, EARLY + timedelta(minutes=5))
    assert relay.to() == sorted([STAFF, CUSTOMER])


async def test_a_changed_address_drops_the_old_row_and_the_new_one_is_sent(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await bot_booking(shop, contact, kind)
    await queue(shop)
    await with_email(shop, contact, "new@example.test")
    relay = FakeTransport()
    await mail(shop, relay)
    assert "new@example.test" not in relay.to() and CUSTOMER not in relay.to()
    await remind(shop, relay, EARLY + timedelta(minutes=5))
    assert relay.to() == sorted([STAFF, "new@example.test"])


async def test_two_spellings_of_one_address_are_one_reminder(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact, "Customer@Example.test")
    await bot_booking(shop, contact, kind)
    await queue(shop)
    await with_email(shop, contact, "customer@example.TEST")
    await queue(shop, EARLY + timedelta(minutes=5))
    customers = [r for r in await rows(shop) if r["audience"] == "customer"]
    assert [r["address"] for r in customers] == [CUSTOMER]
    relay = FakeTransport()
    await mail(shop, relay)
    assert sorted(relay.to()) == sorted([STAFF, "customer@example.TEST"])


# --- how the mail goes --------------------------------------------------------------------------


async def test_a_mail_failure_is_recorded_and_not_retried_and_logs_no_address(
    shop: Shop,
) -> None:
    await staff_meeting(shop)
    relay = FakeTransport()
    relay.fail = {CUSTOMER}
    with capture_logs() as logs:
        await remind(shop, relay)
        relay.fail = set()
        await remind(shop, relay, EARLY + timedelta(minutes=5))
    assert relay.to() == sorted([STAFF, GUEST])
    assert statuses(await rows(shop))[("customer", CUSTOMER)] == "failed"
    shown = repr(logs)
    for secret in (CUSTOMER, STAFF, GUEST, "Walk In", "Weekly sync"):
        assert secret not in shown


async def test_no_mailer_or_mail_switched_off_leaves_rows_queued(shop: Shop) -> None:
    await staff_meeting(shop)
    await queue(shop)
    assert (await mail(shop, None))["sent"] == 0
    async with await psycopg.AsyncConnection.connect(shop.w.owner_dsn) as conn:
        stats = await run_reminder_mail(
            conn, shop.w.pool, Mailer(None, SENDER), FakeClock(EARLY), batch_size=10
        )
    assert stats["sent"] == 0
    assert {r["status"] for r in await rows(shop)} == {"queued"}
    relay = FakeTransport()
    await mail(shop, relay)
    assert len(relay.sent) == 3  # and they go once mail is on


async def test_a_row_claimed_by_a_worker_that_died_is_never_sent_again(shop: Shop) -> None:
    await staff_meeting(shop)
    await queue(shop)
    await shop.w.q("UPDATE booking_reminders SET status = 'sending'")
    relay = FakeTransport()
    await mail(shop, relay)
    assert relay.sent == []
    assert {r["status"] for r in await rows(shop)} == {"sending"}


async def test_the_batch_size_limits_one_run_and_the_rest_wait_for_the_next(shop: Shop) -> None:
    await staff_meeting(shop)
    await queue(shop)
    relay = FakeTransport()
    assert (await mail(shop, relay, batch_size=2))["sent"] == 2
    assert (await mail(shop, relay, batch_size=2))["sent"] == 1
    assert (await mail(shop, relay, batch_size=2))["sent"] == 0


async def test_reminders_state_the_local_time_and_the_place_or_the_link(shop: Shop) -> None:
    await shop.w.q("UPDATE clients SET timezone = 'Asia/Jakarta' WHERE id = %s", shop.tenant)
    _, kind = await a_person_and_type(
        shop, location_kind="online", meeting_link="https://meet.example.test/room"
    )
    contact = await shop.customer()
    await chat(shop, contact)
    await bot_booking(shop, contact, kind)
    await queue(shop)
    (message,) = await reminders_in_chat(shop)
    assert ZONED_TEXT in message["body"]
    assert "Link: https://meet.example.test/room" in message["body"]
    relay = FakeTransport()
    await mail(shop, relay)
    staff = relay.for_(STAFF)
    assert "4 Mar 2031, 17:00" in staff.text and "https://meet.example.test/room" in staff.text
    assert "Asia/Jakarta" in staff.text


# --- the runner ---------------------------------------------------------------------------------


async def test_the_jobs_are_registered_every_five_minutes_and_onboarding_companies_are_skipped(
    shop: Shop,
) -> None:
    transport = FakeTransport()
    registry = build_default_registry(Mailer(transport, SENDER), shop.w.pool)
    queue_job = registry.get("booking_reminder_queue")
    mail_job = registry.get("booking_reminder_mail")
    assert queue_job is not None and queue_job.per_client
    assert queue_job.every == timedelta(minutes=5) and queue_job.fn is run_reminder_queue
    assert mail_job is not None and not mail_job.per_client
    assert mail_job.every == timedelta(minutes=5)
    assert build_default_registry().get("booking_reminder_mail") is not None
    await staff_meeting(shop)
    owner_pool = db.build_pool(shop.w.owner_dsn, max_size=2)
    await owner_pool.open(wait=True)
    try:
        runner = JobRunner(
            owner_pool,
            shop.w.pool,
            lock_conninfo=shop.w.owner_dsn,
            registry=registry,
            clock=FakeClock(EARLY),
        )
        await shop.w.q("UPDATE clients SET status = 'onboarding' WHERE id = %s", shop.tenant)
        assert await runner.run_job_once("booking_reminder_queue") is True
        assert await rows(shop) == []
        await shop.w.q("UPDATE clients SET status = 'active' WHERE id = %s", shop.tenant)
        assert await runner.run_job_once("booking_reminder_queue") is True
        assert len(await rows(shop)) == 3
        assert await runner.run_job_once("booking_reminder_mail") is True
        assert transport.to() == sorted([CUSTOMER, STAFF, GUEST])
        runs = await shop.w.q(
            "SELECT stats FROM job_runs WHERE job = 'booking_reminder_mail' AND client_id IS NULL"
        )
        assert runs[-1]["stats"]["sent"] == 3
    finally:
        await owner_pool.close()


# --- a lapsed bot booking, by email -------------------------------------------------------------


async def test_a_lapsed_booking_emails_a_customer_with_no_telegram(shop: Shop) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer("Email Only")
    await with_email(shop, contact)
    await pending_booking(shop, contact, kind)
    relay = FakeTransport()
    stats = await run_expiry(shop, TEN)
    assert stats["expired"] == 1 and stats["told"] == 0 and stats["mailed"] == 1
    await mail(shop, relay, TEN)
    (lapse,) = relay.sent
    assert lapse.to == CUSTOMER and lapse.calendar is None
    assert "wasn't confirmed in time" in lapse.text and "4 Mar 2031, 10:00" in lapse.text
    assert "Hello Email Only," in lapse.text
    assert "ask for another time" in lapse.text  # the bot would answer a reply
    # Once: the expiry job finds nothing more, and a second mail run has nothing to send.
    assert (await run_expiry(shop, TEN + timedelta(hours=1)))["mailed"] == 0
    await mail(shop, relay, TEN + timedelta(hours=1))
    assert len(relay.sent) == 1
    assert [(r["audience"], r["status"]) for r in await rows(shop)] == [("lapse", "sent")]
    assert await outbound(shop) == []


async def test_a_lapsed_booking_tells_a_telegram_customer_there_and_does_not_email(
    shop: Shop,
) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await chat(shop, contact)
    await pending_booking(shop, contact, kind)
    stats = await run_expiry(shop, TEN)
    assert stats["told"] == 1 and stats["mailed"] == 0
    relay = FakeTransport()
    await mail(shop, relay, TEN)
    assert relay.sent == [] and await rows(shop) == []


@pytest.mark.parametrize("gate", ["replies_off", "service_off"])
async def test_a_lapse_is_not_mailed_when_replies_are_off_or_support_is_not_active(
    shop: Shop, gate: str
) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await pending_booking(shop, contact, kind)
    if gate == "replies_off":
        await shop.mode("shadow")
    else:
        await shop.suspend()
    stats = await run_expiry(shop, TEN)
    assert stats["expired"] == 1 and stats["mailed"] == 0
    relay = FakeTransport()
    await mail(shop, relay, TEN)
    assert relay.sent == [] and await rows(shop) == []


async def test_a_lapse_mail_waits_for_no_gate_change_and_a_removed_address_drops_it(
    shop: Shop,
) -> None:
    _, kind = await a_person_and_type(shop)
    contact = await shop.customer()
    await with_email(shop, contact)
    await pending_booking(shop, contact, kind)
    await run_expiry(shop, TEN)
    await shop.w.q("UPDATE contacts SET email = NULL WHERE id = %s", str(contact))
    relay = FakeTransport()
    await mail(shop, relay, TEN)
    assert relay.sent == []
    assert [(r["audience"], r["status"]) for r in await rows(shop)] == [("lapse", "dropped")]


async def test_the_expiry_job_still_runs_the_same_as_before_with_no_customer(shop: Shop) -> None:
    async with tx(shop.w.pool, shop.tenant) as conn:
        clock = FakeClock(datetime(2032, 1, 1, tzinfo=UTC))
        stats = await run_pending_expiry(conn, shop.client, clock)
    assert stats == {"expired": 0, "told": 0, "mailed": 0, "failed": 0}


# --- each company's mail is read and settled as that company --------------------------------------


class Hooked(FakeTransport):
    """A relay that runs `hook` (blocking) the first time it is asked to send."""

    def __init__(self, hook: Callable[[], None]) -> None:
        super().__init__()
        self.hook = hook
        self.fired = False

    def send(self, mail: OutgoingMail, sender: str) -> None:
        if not self.fired:
            self.fired = True
            self.hook()
        super().send(mail, sender)


def owner_exec(shop: Shop, statement: str, *params: Any) -> None:
    with psycopg.connect(shop.w.owner_dsn, autocommit=True) as conn:
        conn.execute(statement, params)  # type: ignore[arg-type]


async def test_two_companies_in_one_run_each_get_their_own_brand_zone_and_rows(
    shop: Shop,
) -> None:
    other = shop.w.tenants[1]
    await staff_meeting(shop)
    elsewhere = await shop.team.send(
        shop.team.other, "/people", {"display_name": "Other Person", "email": "other@example.test"}
    )
    assert elsewhere.status_code == 201, elsewhere.text
    there = meeting(elsewhere.json(), start=at(10), guests=[{"email": "other-guest@example.test"}])
    await booked(shop.team, shop.team.other, there)
    await shop.w.q(
        "UPDATE clients SET timezone = 'Asia/Jakarta', brand_name = 'Mawar' WHERE id = %s",
        shop.tenant,
    )
    await shop.w.q(
        "UPDATE clients SET timezone = 'Asia/Tokyo', brand_name = 'Cekap' WHERE id = %s", other
    )
    await queue(shop)
    async with tx(shop.w.pool, other) as conn:
        await run_reminder_queue(conn, uuid.UUID(other), FakeClock(EARLY), batch_size=100)
    # A company's own transaction sees its own rows only, and cannot claim another's.
    ours = await shop.w.q("SELECT id FROM booking_reminders WHERE client_id = %s", shop.tenant)
    async with tx(shop.w.pool, other) as conn:
        visible = await db.q.fetch_all(
            "bookings/reminders_queued.sql",
            {"client_id": shop.client, "batch_size": 10},
            conn=conn,
        )
        stolen = await db.q.fetch_one(
            "bookings/reminder_claim_one.sql",
            {"client_id": shop.client, "id": ours[0]["id"]},
            conn=conn,
        )
    assert visible == [] and stolen is None
    relay = FakeTransport()
    stats = await mail(shop, relay)
    assert stats["sent"] == 5 and stats["failed"] == 0
    first = {CUSTOMER, STAFF, GUEST}
    second = {"other@example.test", "other-guest@example.test"}
    assert {m.to for m in relay.sent} == first | second
    for sent in relay.sent:
        if sent.to in first:
            assert "Mawar" in sent.text and "4 Mar 2031, 17:00" in sent.text
            assert "Cekap" not in sent.text and "Asia/Jakarta" in sent.text
        else:
            assert "Cekap" in sent.text and "4 Mar 2031, 19:00" in sent.text
            assert "Mawar" not in sent.text and "Asia/Tokyo" in sent.text
    assert {r["status"] for r in await rows(shop)} == {"sent"}


# --- the checks are made when a row's turn to send comes ------------------------------------------


async def test_a_cancel_that_arrives_while_a_row_waits_for_a_send_slot_is_seen(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("csa.bookings.notify.SIMULTANEOUS", 1)
    first = await make_person(shop.team, "First Person", email="first@example.test")
    second = await make_person(shop.team, "Second Person", email="second@example.test")
    await booked(shop.team, shop.team.admin, meeting(first, start=at(10)))
    await queue(shop)  # the first booking's row is queued before the second's exists
    two = await booked(shop.team, shop.team.admin, meeting(second, start=at(11)))
    await queue(shop)
    cancel = "UPDATE bookings SET status = 'cancelled' WHERE id = %s"
    relay = Hooked(lambda: owner_exec(shop, cancel, two["id"]))
    stats = await mail(shop, relay)
    # One slot: the second row waits while the first is being sent, and the cancel lands then.
    assert relay.to() == ["first@example.test"]
    assert stats["sent"] == 1 and stats["dropped"] == 1
    assert statuses(await rows(shop)) == {
        ("staff", "first@example.test"): "sent",
        ("staff", "second@example.test"): "dropped",
    }


async def test_replies_turned_off_while_a_row_waits_for_a_send_slot_is_seen(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("csa.bookings.notify.SIMULTANEOUS", 1)
    person = await make_person(shop.team, "Aina Plain")
    kind = await make_type(shop.team, delivery="named", person_ids=[person["id"]])
    first, second = await shop.customer("First"), await shop.customer("Second")
    await with_email(shop, first, "first@example.test")
    await with_email(shop, second, "second@example.test")
    await shop.mode("auto")
    assert (await shop.book(first, kind["id"], TEN)).code == "confirmed"
    await queue(shop)
    assert (await shop.book(second, kind["id"], TEN + timedelta(minutes=30))).code == "confirmed"
    await queue(shop)
    off = (
        "UPDATE clients SET config = jsonb_set(coalesce(config, '{}'::jsonb), '{policy}',"
        ' \'{"deployment_mode": "shadow"}\'::jsonb) WHERE id = %s'
    )
    relay = Hooked(lambda: owner_exec(shop, off, shop.tenant))
    stats = await mail(shop, relay)
    assert relay.to() == ["first@example.test"]
    assert stats["sent"] == 1 and stats["dropped"] == 1
    assert statuses(await rows(shop)) == {
        ("customer", "first@example.test"): "sent",
        ("customer", "second@example.test"): "dropped",
    }


async def test_a_timeout_leaves_only_the_row_being_sent_not_the_ones_still_waiting(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("csa.bookings.notify.SIMULTANEOUS", 1)
    await staff_meeting(shop)
    await queue(shop)
    sending = asyncio.Event()
    mailer = Mailer(FakeTransport(), SENDER)

    async def stuck(message: OutgoingMail) -> Any:
        sending.set()
        await asyncio.sleep(30)

    mailer.send = stuck  # type: ignore[method-assign]
    async with await psycopg.AsyncConnection.connect(shop.w.owner_dsn) as conn:
        task = asyncio.create_task(
            run_reminder_mail(conn, shop.w.pool, mailer, FakeClock(EARLY), batch_size=100)
        )
        await sending.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert sorted(r["status"] for r in await rows(shop)) == ["queued", "queued", "sending"]
