"""The customer's booking rules, with no turn (stage 1, PR 2, Task 2)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from csa.bookings import bot
from csa.bookings.api import build_bookings_router
from csa.core.tenancy import tx

from tests.conftest import requires_docker
from tests.test_bookings_api import TUESDAY, at, booked, meeting, person_on_leave
from tests.test_bookings_setup import Team, make_person, make_type
from tests.test_console_sessions import SessionWorld
from tests.test_customer_replies import activate_only

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

# Monday noon; the working week is the default, Monday to Friday 09:00 to 18:00 in UTC.
NOW = datetime(2031, 3, 3, 12, 0, tzinfo=UTC)
TEN = datetime(2031, 3, 4, 10, 0, tzinfo=UTC)
ELEVEN = datetime(2031, 3, 4, 11, 0, tzinfo=UTC)
WEDNESDAY = date(2031, 3, 5)


class Shop:
    """A company with Customer support on, a team, and customers to book for."""

    def __init__(self, w: SessionWorld, team: Team) -> None:
        self.w = w
        self.team = team
        self.tenant = w.tenants[0]
        self.client = uuid.UUID(self.tenant)

    async def policy(self, value: object) -> None:
        """The company's whole `config.policy`, as stored (a hand edit may be anything)."""
        await self.w.q(
            "UPDATE clients SET config = jsonb_set(coalesce(config, '{}'::jsonb), '{policy}',"
            " %s::jsonb) WHERE id = %s",
            json.dumps(value),
            self.tenant,
        )

    async def mode(self, mode: str | None) -> None:
        await self.policy({} if mode is None else {"deployment_mode": mode})

    async def suspend(self) -> None:
        await self.w.q(
            "UPDATE console_service_packages SET status = 'suspended'"
            " WHERE client_id = %s AND package = 'customer_support'",
            self.tenant,
        )

    async def customer(self, name: str = "Synthetic Customer") -> uuid.UUID:
        contact = uuid.uuid4()
        await self.w.q(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, %s)",
            str(contact),
            self.tenant,
            name,
        )
        return contact

    async def run(self, call: Any, *args: Any, **kwargs: Any) -> bot.BotOutcome:
        async with tx(self.w.pool, self.tenant) as conn:
            return await call(conn, self.client, *args, **kwargs)  # type: ignore[no-any-return]

    async def book(
        self, contact: uuid.UUID, type_id: str, starts_at: datetime = TEN, **extra: Any
    ) -> bot.BotOutcome:
        return await self.run(
            bot.book,
            contact,
            **{
                "type_id": uuid.UUID(type_id),
                "starts_at": starts_at,
                "turn_key": f"turn-{uuid.uuid4()}",
                "conversation_id": None,
                "now": NOW,
                **extra,
            },
        )

    async def change(
        self,
        contact: uuid.UUID,
        booking_id: uuid.UUID,
        starts_at: datetime,
        *,
        now: datetime = NOW,
        turn_key: str | None = None,
    ) -> bot.BotOutcome:
        return await self.run(
            bot.change,
            contact,
            booking_id,
            starts_at=starts_at,
            turn_key=turn_key or f"turn-{uuid.uuid4()}",
            now=now,
        )

    async def cancel(
        self,
        contact: uuid.UUID,
        booking_id: uuid.UUID,
        *,
        now: datetime = NOW,
        turn_key: str | None = None,
    ) -> bot.BotOutcome:
        return await self.run(
            bot.cancel,
            contact,
            booking_id,
            turn_key=turn_key or f"turn-{uuid.uuid4()}",
            now=now,
        )

    async def rows(self) -> list[dict[str, Any]]:
        return await self.w.q(
            "SELECT id::text AS id, status, source, created_by, replaces_booking_id::text AS rep,"
            " expires_at, starts_at FROM bookings WHERE client_id = %s ORDER BY created_at",
            self.tenant,
        )

    async def actions(self) -> list[dict[str, Any]]:
        """What each turn key did, as the replay lookup reads it."""
        return await self.w.q(
            "SELECT turn_key, booking_id::text AS booking, action FROM booking_turn_actions"
            " WHERE client_id = %s ORDER BY turn_key",
            self.tenant,
        )

    async def audits(self, action: str) -> int:
        rows = await self.w.q(
            "SELECT count(*) AS n FROM console_audit WHERE client_id = %s AND action = %s",
            self.tenant,
            action,
        )
        return int(rows[0]["n"])

    async def holds(self, booking_id: object) -> int:
        rows = await self.w.q(
            "SELECT count(*) AS n FROM booking_holds WHERE booking_id = %s", str(booking_id)
        )
        return int(rows[0]["n"])


@pytest.fixture
async def shop(sessions: SessionWorld) -> Shop:
    sessions.app.include_router(build_bookings_router())
    for tenant in sessions.tenants:
        await activate_only(sessions, tenant, "bookings", "customer_support")
        await sessions.q("UPDATE clients SET timezone = 'UTC' WHERE id = %s", tenant)
    made = Shop(sessions, await Team(sessions).open())
    await made.mode("auto")
    return made


async def a_type(shop: Shop, *, people: int = 1, **extra: Any) -> dict[str, Any]:
    """A type for customers, with `people` bookable people to deliver it."""
    team = [await make_person(shop.team, f"Person {n}") for n in range(people)]
    return await make_type(shop.team, person_ids=[p["id"] for p in team], **extra)


# --- the reply mode and the gates ---------------------------------------------------------------


async def test_shadow_writes_nothing_and_says_replies_off(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.mode("shadow")
    contact = await shop.customer()
    outcome = await shop.book(contact, kind["id"])
    assert outcome.code == "replies_off"
    assert outcome.booking is None and outcome.offers == []
    assert await shop.rows() == []


async def test_suggest_holds_the_slot_as_pending_with_an_expiry(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.mode("suggest")
    outcome = await shop.book(await shop.customer(), kind["id"])
    assert outcome.code == "pending"
    assert outcome.booking is not None and outcome.booking.status == "pending"
    (row,) = await shop.rows()
    assert row["status"] == "pending"
    # A day on is after the booking starts, so the hold lapses at the start.
    assert row["expires_at"] == TEN
    assert await shop.holds(row["id"]) == 1


async def test_pending_hours_come_from_the_settings(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.mode("suggest")
    await shop.w.q(
        "INSERT INTO booking_settings (client_id, pending_hours) VALUES (%s, 6)", shop.tenant
    )
    await shop.book(await shop.customer(), kind["id"])
    (row,) = await shop.rows()
    assert row["expires_at"] == NOW + timedelta(hours=6)


async def test_auto_confirms_unless_the_type_needs_confirmation(shop: Shop) -> None:
    plain = await a_type(shop)
    careful = await a_type(shop, name="Consultation", needs_confirmation=True)
    contact = await shop.customer()
    first = await shop.book(contact, plain["id"])
    second = await shop.book(contact, careful["id"], ELEVEN)
    assert (first.code, second.code) == ("confirmed", "pending")
    rows = {r["id"]: r for r in await shop.rows()}
    assert first.booking is not None and second.booking is not None
    assert rows[str(first.booking.id)]["status"] == "confirmed"
    assert rows[str(first.booking.id)]["expires_at"] is None
    assert rows[str(second.booking.id)]["expires_at"] == ELEVEN


async def test_no_policy_means_auto(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.mode(None)
    assert (await shop.book(await shop.customer(), kind["id"])).code == "confirmed"


@pytest.mark.parametrize("state", ["suspended", "requested"])
async def test_customer_support_suspended_writes_nothing(shop: Shop, state: str) -> None:
    kind = await a_type(shop)
    await shop.w.q(
        "UPDATE console_service_packages SET status = %s"
        " WHERE client_id = %s AND package = 'customer_support'",
        state,
        shop.tenant,
    )
    outcome = await shop.book(await shop.customer(), kind["id"])
    assert outcome.code == "service_off" and outcome.booking is None
    assert await shop.rows() == []


async def test_customer_service_and_reply_mode_read_the_company(shop: Shop) -> None:
    async with tx(shop.w.pool, shop.tenant) as conn:
        assert await bot.customer_service_on(conn, shop.client) is True
        assert await bot.reply_mode(conn, shop.client) == "auto"
    await shop.mode("suggest")
    async with tx(shop.w.pool, shop.tenant) as conn:
        assert await bot.reply_mode(conn, shop.client) == "suggest"


async def test_customer_types_are_active_and_for_customers(shop: Shop) -> None:
    await a_type(shop, name="For customers")
    await a_type(shop, name="For both", audience="both")
    await a_type(shop, name="Staff only", audience="staff")
    gone = await a_type(shop, name="Switched off")
    off = await shop.team.send(
        shop.team.admin,
        f"/types/{gone['id']}",
        {"version": gone["version"], "is_active": False},
        "PATCH",
    )
    assert off.status_code == 200, off.text
    async with tx(shop.w.pool, shop.tenant) as conn:
        names = sorted(t.name for t in await bot.customer_types(conn, shop.client))
    assert names == ["For both", "For customers"]


async def test_a_staff_only_type_is_not_found(shop: Shop) -> None:
    kind = await a_type(shop, audience="staff")
    outcome = await shop.book(await shop.customer(), kind["id"])
    assert outcome.code == "not_found"
    assert await shop.rows() == []


# --- one message, one booking -------------------------------------------------------------------


async def test_the_same_turn_key_replays_the_booking(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    first = await shop.book(contact, kind["id"], turn_key="turn-one")
    again = await shop.book(contact, kind["id"], turn_key="turn-one")
    assert first.code == "confirmed" and again.code == "replay"
    assert first.booking is not None and again.booking is not None
    assert again.booking.id == first.booking.id
    (row,) = await shop.rows()
    assert await shop.actions() == [
        {"turn_key": "turn-one", "booking": row["id"], "action": "book"}
    ]
    assert await shop.holds(row["id"]) == 1
    # The slot is held now, yet a different message for it is refused as taken.
    other = await shop.book(await shop.customer(), kind["id"])
    assert other.code == "slot_taken"


async def test_a_turn_key_of_another_customer_is_not_found(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.book(await shop.customer(), kind["id"], turn_key="turn-shared")
    stranger = await shop.book(await shop.customer(), kind["id"], ELEVEN, turn_key="turn-shared")
    assert stranger.code == "not_found" and stranger.booking is None


async def test_two_messages_with_one_key_book_once(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    results = await asyncio.gather(
        shop.book(contact, kind["id"], turn_key="turn-twin"),
        shop.book(contact, kind["id"], turn_key="turn-twin"),
    )
    assert sorted(r.code for r in results) == ["confirmed", "replay"]
    assert len(await shop.rows()) == 1


async def test_two_customers_race_for_the_last_slot(shop: Shop) -> None:
    kind = await a_type(shop)
    one, two = await shop.customer(), await shop.customer()
    results = await asyncio.gather(shop.book(one, kind["id"]), shop.book(two, kind["id"]))
    assert sorted(r.code for r in results) == ["confirmed", "slot_taken"]
    lost = next(r for r in results if r.code == "slot_taken")
    assert lost.booking is None
    assert 1 <= len(lost.offers) <= 3
    assert all(o.starts_at != TEN for o in lost.offers)
    assert len(await shop.rows()) == 1


# --- the rules the bot never overrides ----------------------------------------------------------


async def refused_with_offers(shop: Shop, outcome: bot.BotOutcome, asked: datetime = TEN) -> None:
    assert outcome.code == "not_allowed", outcome
    assert outcome.booking is None
    assert 1 <= len(outcome.offers) <= 3
    assert all(o.starts_at != asked for o in outcome.offers)
    assert all(o.starts_at >= NOW for o in outcome.offers)
    for word in ("leave", "away", "holiday", "closed"):
        assert all(word not in o.label.lower() for o in outcome.offers)
    assert await shop.rows() == []


async def test_outside_hours_is_not_allowed(shop: Shop) -> None:
    kind = await a_type(shop)
    early = datetime(2031, 3, 4, 7, 0, tzinfo=UTC)
    await refused_with_offers(
        shop, await shop.book(await shop.customer(), kind["id"], early), early
    )


async def test_a_closed_day_is_not_allowed(shop: Shop) -> None:
    kind = await a_type(shop)
    closed = await shop.team.send(shop.team.admin, "/closed-days", {"day": TUESDAY})
    assert closed.status_code == 201, closed.text
    await refused_with_offers(shop, await shop.book(await shop.customer(), kind["id"]))


async def test_inside_the_notice_is_not_allowed(shop: Shop) -> None:
    kind = await a_type(shop, min_notice_minutes=240)
    soon = NOW.replace(hour=10, day=4)  # Tuesday 10:00; it is Tuesday 08:00 below
    outcome = await shop.book(
        await shop.customer(), kind["id"], soon, now=soon - timedelta(hours=1)
    )
    assert outcome.code == "not_allowed" and outcome.booking is None
    assert await shop.rows() == []


async def test_beyond_max_advance_days_is_not_allowed(shop: Shop) -> None:
    kind = await a_type(shop, max_advance_days=10)
    far = datetime(2031, 4, 1, 10, 0, tzinfo=UTC)
    await refused_with_offers(shop, await shop.book(await shop.customer(), kind["id"], far), far)


async def test_a_person_on_approved_leave_is_not_allowed(shop: Shop) -> None:
    away = await person_on_leave(shop.team)
    kind = await make_type(shop.team, person_ids=[away["id"]])
    outcome = await shop.book(await shop.customer(), kind["id"])
    await refused_with_offers(shop, outcome)
    assert "leave" not in repr(outcome).lower()


async def test_any_free_person_is_named_only_once_booked(shop: Shop) -> None:
    kind = await a_type(shop, people=2)
    async with tx(shop.w.pool, shop.tenant) as conn:
        offers = await bot.offer(
            conn, shop.client, type_id=uuid.UUID(kind["id"]), day_from=date(2031, 3, 4), now=NOW
        )
    assert offers and all("Person" not in o.label for o in offers)
    outcome = await shop.book(await shop.customer(), kind["id"], offers[0].starts_at)
    assert outcome.code == "confirmed" and outcome.booking is not None
    assert [p.display_name for p in outcome.booking.people][0].startswith("Person")


async def test_a_named_type_names_the_person_in_its_offers(shop: Shop) -> None:
    kind = await a_type(shop, delivery="named")
    async with tx(shop.w.pool, shop.tenant) as conn:
        offers = await bot.offer(
            conn, shop.client, type_id=uuid.UUID(kind["id"]), day_from=date(2031, 3, 4), now=NOW
        )
    assert offers and all(o.label.endswith("with Person 0") for o in offers)
    assert offers[0].label.startswith("Tuesday 4 March 2031, 9:00 am")


async def test_offers_start_from_the_day_and_never_the_past(shop: Shop) -> None:
    kind = await a_type(shop)
    async with tx(shop.w.pool, shop.tenant) as conn:
        later = await bot.offer(
            conn, shop.client, type_id=uuid.UUID(kind["id"]), day_from=WEDNESDAY, now=NOW, limit=2
        )
        past = await bot.offer(
            conn, shop.client, type_id=uuid.UUID(kind["id"]), day_from=date(2031, 3, 1), now=NOW
        )
    assert len(later) == 2 and later[0].starts_at == datetime(2031, 3, 5, 9, 0, tzinfo=UTC)
    assert past[0].starts_at >= NOW + timedelta(minutes=60)


async def test_the_customer_limit_refuses_more(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.w.q(
        "INSERT INTO booking_settings (client_id, customer_limit) VALUES (%s, 2)", shop.tenant
    )
    contact = await shop.customer()
    first = await shop.book(contact, kind["id"], TEN)
    await shop.book(contact, kind["id"], ELEVEN)
    third = await shop.book(contact, kind["id"], datetime(2031, 3, 4, 14, 0, tzinfo=UTC))
    assert third.code == "limit_reached" and third.booking is None
    assert len(await shop.rows()) == 2
    # Someone else is not held to this customer's count, and cancelling frees a place.
    assert (
        await shop.book(await shop.customer(), kind["id"], datetime(2031, 3, 4, 15, 0, tzinfo=UTC))
    ).code == "confirmed"
    assert first.booking is not None
    assert (await shop.cancel(contact, first.booking.id)).code == "cancelled"
    again = await shop.book(contact, kind["id"], datetime(2031, 3, 4, 14, 0, tzinfo=UTC))
    assert again.code == "confirmed"


async def confirm(shop: Shop, booking: Any) -> None:
    """Staff confirm a pending booking, so a change has something confirmed to protect."""
    done = await shop.team.send(
        shop.team.admin, f"/bookings/{booking.id}/confirm", {"version": booking.version}
    )
    assert done.status_code == 200, done.text


async def test_a_pending_booking_moves_in_place_even_in_suggest(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.mode("suggest")
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None and made.code == "pending"
    moved = await shop.change(contact, made.booking.id, ELEVEN)
    assert moved.code == "moved" and moved.booking is not None
    assert moved.booking.id == made.booking.id and moved.booking.status == "pending"
    (row,) = await shop.rows()
    assert row["starts_at"] == ELEVEN and row["expires_at"] == TEN  # the expiry is kept


# --- change -------------------------------------------------------------------------------------


async def test_change_in_auto_moves_in_place(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    moved = await shop.change(contact, made.booking.id, ELEVEN)
    assert moved.code == "moved" and moved.booking is not None
    assert moved.booking.id == made.booking.id
    assert moved.booking.starts_at == ELEVEN and moved.booking.version == made.booking.version + 1
    (row,) = await shop.rows()
    assert row["starts_at"] == ELEVEN and row["status"] == "confirmed"
    assert await shop.holds(row["id"]) == 1
    # The old slot is free again.
    assert (await shop.book(await shop.customer(), kind["id"], TEN)).code == "confirmed"


async def test_change_to_a_taken_time_is_refused_with_offers(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    mine = await shop.book(contact, kind["id"])
    await shop.book(await shop.customer(), kind["id"], ELEVEN)
    assert mine.booking is not None
    refused = await shop.change(contact, mine.booking.id, ELEVEN)
    assert refused.code == "slot_taken" and refused.offers
    assert all(o.starts_at != ELEVEN for o in refused.offers)
    (first, _) = await shop.rows()
    assert first["starts_at"] == TEN


async def test_change_that_needs_confirmation_holds_a_second_pending_booking(shop: Shop) -> None:
    kind = await a_type(shop, needs_confirmation=True)
    contact = await shop.customer()
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None and old.code == "pending"
    # Staff confirm the first booking, so a change has something to protect.
    confirmed = await shop.team.send(
        shop.team.admin, f"/bookings/{old.booking.id}/confirm", {"version": old.booking.version}
    )
    assert confirmed.status_code == 200, confirmed.text
    asked = await shop.change(contact, old.booking.id, ELEVEN, turn_key="turn-move")
    assert asked.code == "change_pending" and asked.booking is not None
    assert asked.booking.id != old.booking.id and asked.booking.status == "pending"
    assert asked.booking.replaces_booking_id == old.booking.id
    assert old.booking.replaces_booking_id is None
    old_row, new_row = await shop.rows()
    assert old_row["status"] == "confirmed" and old_row["starts_at"] == TEN
    assert new_row["rep"] == str(old.booking.id) and new_row["starts_at"] == ELEVEN
    assert new_row["source"] == "bot" and new_row["expires_at"] == ELEVEN
    assert await shop.holds(old_row["id"]) == 1 and await shop.holds(new_row["id"]) == 1
    # The same message again is a replay, not a second request.
    again = await shop.change(contact, old.booking.id, ELEVEN, turn_key="turn-move")
    assert again.code == "replay" and again.booking is not None
    assert again.booking.id == asked.booking.id and len(await shop.rows()) == 2
    # A newer request replaces the earlier one.
    third = await shop.change(contact, old.booking.id, datetime(2031, 3, 4, 14, 0, tzinfo=UTC))
    assert third.code == "change_pending"
    states = [(r["status"], r["rep"] is not None) for r in await shop.rows()]
    assert states == [("confirmed", False), ("cancelled", True), ("pending", True)]


async def test_change_in_suggest_is_pending_even_without_confirmation(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None and old.code == "confirmed"
    await shop.mode("suggest")
    asked = await shop.change(contact, old.booking.id, ELEVEN)
    assert asked.code == "change_pending"
    assert [r["status"] for r in await shop.rows()] == ["confirmed", "pending"]


async def test_a_new_time_overlapping_the_old_slot_is_overlaps_itself(shop: Shop) -> None:
    kind = await a_type(shop, needs_confirmation=True, buffer_minutes=15)
    contact = await shop.customer()
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None
    await confirm(shop, old.booking)
    # 10:00 to 10:30, and a 15 minute buffer holds the person until 10:45.
    for start in (
        datetime(2031, 3, 4, 10, 15, tzinfo=UTC),
        datetime(2031, 3, 4, 10, 30, tzinfo=UTC),
        datetime(2031, 3, 4, 9, 45, tzinfo=UTC),
    ):
        outcome = await shop.change(contact, old.booking.id, start)
        assert outcome.code == "overlaps_itself", start
        assert outcome.booking is None
    assert len(await shop.rows()) == 1
    clear = await shop.change(contact, old.booking.id, datetime(2031, 3, 4, 10, 45, tzinfo=UTC))
    assert clear.code == "change_pending"


async def test_past_the_change_cutoff_is_refused(shop: Shop) -> None:
    kind = await a_type(shop, change_cutoff_minutes=120)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    late = TEN - timedelta(minutes=90)
    assert (await shop.change(contact, made.booking.id, ELEVEN, now=late)).code == "past_cutoff"
    assert (await shop.cancel(contact, made.booking.id, now=late)).code == "past_cutoff"
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]
    early = TEN - timedelta(minutes=121)
    assert (await shop.change(contact, made.booking.id, ELEVEN, now=early)).code == "moved"


# --- cancel -------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["auto", "suggest"])
async def test_cancel_frees_the_slot(shop: Shop, mode: str) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    await shop.mode(mode)
    cancelled = await shop.cancel(contact, made.booking.id)
    assert cancelled.code == "cancelled" and cancelled.booking is not None
    assert cancelled.booking.status == "cancelled"
    (row,) = await shop.rows()
    assert row["status"] == "cancelled" and await shop.holds(row["id"]) == 0
    rebooked = await shop.book(await shop.customer(), kind["id"])
    assert rebooked.code == ("pending" if mode == "suggest" else "confirmed")
    # Cancelling it again tells the truth and writes nothing.
    again = await shop.cancel(contact, made.booking.id)
    assert again.code == "replay" and again.booking is not None
    assert again.booking.status == "cancelled"


async def test_cancel_in_shadow_says_replies_off(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    await shop.mode("shadow")
    assert (await shop.cancel(contact, made.booking.id)).code == "replies_off"
    assert (await shop.change(contact, made.booking.id, ELEVEN)).code == "replies_off"
    (row,) = await shop.rows()
    assert row["status"] == "confirmed" and await shop.holds(row["id"]) == 1


async def test_cancelling_a_booking_cancels_its_pending_change(shop: Shop) -> None:
    kind = await a_type(shop, needs_confirmation=True)
    contact = await shop.customer()
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None
    await shop.team.send(
        shop.team.admin, f"/bookings/{old.booking.id}/confirm", {"version": old.booking.version}
    )
    asked = await shop.change(contact, old.booking.id, ELEVEN)
    assert asked.code == "change_pending"
    assert (await shop.cancel(contact, old.booking.id)).code == "cancelled"
    assert [r["status"] for r in await shop.rows()] == ["cancelled", "cancelled"]
    holds = [await shop.holds(r["id"]) for r in await shop.rows()]
    assert holds == [0, 0]


# --- whose booking it is ------------------------------------------------------------------------


async def test_another_customers_booking_is_not_found(shop: Shop) -> None:
    kind = await a_type(shop)
    owner, stranger = await shop.customer(), await shop.customer()
    made = await shop.book(owner, kind["id"])
    assert made.booking is not None
    missing = uuid.uuid4()
    for who, target in ((stranger, made.booking.id), (owner, missing)):
        changed = await shop.change(who, target, ELEVEN)
        cancelled = await shop.cancel(who, target)
        assert changed == bot.BotOutcome("not_found")
        assert cancelled == bot.BotOutcome("not_found")
    (row,) = await shop.rows()
    assert row["status"] == "confirmed" and row["starts_at"] == TEN


async def test_a_private_meeting_and_a_staff_pending_booking_are_not_found(shop: Shop) -> None:
    kind = await a_type(shop, needs_confirmation=True)
    contact = await shop.customer()
    person = await make_person(shop.team, "Staff Person")
    private = await booked(
        shop.team,
        shop.team.admin,
        meeting(person, start=at(14), private=True, contact_id=str(contact)),
    )
    pending = await booked(
        shop.team,
        shop.team.admin,
        {
            "type_id": kind["id"],
            "starts_at": at(15),
            "contact_id": str(contact),
            "people": [kind["person_ids"][0]],
        },
    )
    assert pending["status"] == "pending"
    for booking_id in (private["id"], pending["id"]):
        target = uuid.UUID(booking_id)
        assert (await shop.change(contact, target, ELEVEN)).code == "not_found"
        assert (await shop.cancel(contact, target)).code == "not_found"
    states = {r["id"]: r["status"] for r in await shop.rows()}
    assert states[private["id"]] != "cancelled" and states[pending["id"]] == "pending"


async def test_a_staff_confirmed_booking_is_the_customers_own(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    staff_made = await booked(
        shop.team,
        shop.team.staff,
        {
            "type_id": kind["id"],
            "starts_at": at(14),
            "contact_id": str(contact),
            "people": [kind["person_ids"][0]],
        },
    )
    assert staff_made["status"] == "confirmed"
    assert (await shop.cancel(contact, uuid.UUID(staff_made["id"]))).code == "cancelled"


# --- what is recorded ---------------------------------------------------------------------------


async def test_bot_bookings_record_the_bot_and_audit_the_id_and_action_only(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    conversation = uuid.uuid4()
    outcome = await shop.book(contact, kind["id"], conversation_id=conversation)
    assert outcome.booking is not None
    (row,) = await shop.rows()
    assert row["source"] == "bot" and row["created_by"] is None
    stored = await shop.w.q(
        "SELECT conversation_id::text AS c FROM bookings WHERE client_id = %s", shop.tenant
    )
    assert stored[0]["c"] == str(conversation)
    audits = await shop.w.q(
        "SELECT actor_id, target_id, details FROM console_audit"
        " WHERE client_id = %s AND action = 'bookings.booking.created'",
        shop.tenant,
    )
    assert [(a["actor_id"], a["target_id"], a["details"]) for a in audits] == [
        (None, str(outcome.booking.id), {})
    ]
    moved = await shop.change(contact, outcome.booking.id, ELEVEN)
    assert moved.code == "moved"
    later = await shop.w.q(
        "SELECT actor_id, details FROM console_audit"
        " WHERE client_id = %s AND action = 'bookings.booking.moved'",
        shop.tenant,
    )
    assert [(a["actor_id"], a["details"]) for a in later] == [(None, {})]


async def test_staff_bookings_still_record_staff(shop: Shop) -> None:
    person = await make_person(shop.team)
    made = await booked(shop.team, shop.team.staff, meeting(person))
    stored = await shop.w.q(
        "SELECT source, created_by::text AS by, expires_at, replaces_booking_id"
        " FROM bookings WHERE id = %s",
        made["id"],
    )
    assert stored[0]["source"] == "staff" and stored[0]["by"] is not None
    assert stored[0]["expires_at"] is None and stored[0]["replaces_booking_id"] is None
    assert await shop.actions() == []


async def test_an_unknown_type_is_not_found_and_a_setup_gap_is_not_allowed(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    assert (await shop.book(contact, str(uuid.uuid4()))).code == "not_found"
    away = await shop.team.send(
        shop.team.admin,
        f"/people/{kind['person_ids'][0]}",
        {"version": 1, "is_active": False},
        "PATCH",
    )
    assert away.status_code == 200, away.text
    # Nobody left to deliver the type: a rule answer, never a raised error.
    outcome = await shop.book(contact, kind["id"])
    assert outcome.code == "not_allowed" and outcome.booking is None
    assert await shop.rows() == []


# --- fix round 1: what a customer is told, and what the server checks ----------------------------

TWO = datetime(2031, 3, 4, 14, 0, tzinfo=UTC)
THREE = datetime(2031, 3, 4, 15, 0, tzinfo=UTC)


async def pending_change(
    shop: Shop, kind: dict[str, Any], contact: uuid.UUID, starts_at: datetime = ELEVEN
) -> tuple[bot.BotOutcome, bot.BotOutcome]:
    """A confirmed booking at ten, and a pending request to move it (made in `suggest`), with the
    company back in `auto` so the next change would move it in place.
    """
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None and old.code == "confirmed"
    await shop.mode("suggest")
    asked = await shop.change(contact, old.booking.id, starts_at)
    assert asked.code == "change_pending", asked
    await shop.mode("auto")
    return old, asked


# I1: an in-place change deals with the pending change it finds.


@pytest.mark.parametrize(
    ("asked_time", "code"), [(THREE, "moved"), (ELEVEN, "moved"), (TEN, "change_withdrawn")]
)
async def test_an_in_place_change_settles_an_earlier_pending_change(
    shop: Shop, asked_time: datetime, code: str
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    outcome = await shop.change(contact, old.booking.id, asked_time)
    assert outcome.code == code, outcome
    assert outcome.booking is not None and outcome.booking.id == old.booking.id
    original, request = await shop.rows()
    assert original["starts_at"] == asked_time and original["status"] == "confirmed"
    assert request["status"] == "cancelled" and await shop.holds(request["id"]) == 0
    assert await shop.holds(original["id"]) == 1


async def test_a_refused_in_place_change_keeps_the_earlier_request(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, _ = await pending_change(shop, kind, contact)
    assert old.booking is not None
    await shop.book(await shop.customer(), kind["id"], TWO)
    refused = await shop.change(contact, old.booking.id, TWO)
    assert refused.code == "slot_taken"
    original, request, _ = await shop.rows()
    assert (original["status"], original["starts_at"]) == ("confirmed", TEN)
    assert request["status"] == "pending" and await shop.holds(request["id"]) == 1


# I8: a refusal after the earlier request was dropped puts it back.


async def test_a_refused_change_request_keeps_the_earlier_one(shop: Shop) -> None:
    kind = await a_type(shop, needs_confirmation=True)
    contact = await shop.customer()
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None
    await confirm(shop, old.booking)
    first = await shop.change(contact, old.booking.id, ELEVEN)
    assert first.code == "change_pending"
    await shop.book(await shop.customer(), kind["id"], TWO)
    refused = await shop.change(contact, old.booking.id, TWO)
    assert refused.code == "slot_taken" and refused.offers
    original, request, _ = await shop.rows()
    assert original["status"] == "confirmed"
    assert request["status"] == "pending" and request["starts_at"] == ELEVEN
    assert await shop.holds(request["id"]) == 1
    assert await shop.audits("bookings.booking.cancelled") == 0
    assert len(await shop.actions()) == 3  # the two bookings and the one request


# I2: a name only when one person can take the slot.


async def test_a_named_offer_names_a_person_only_when_one_can_take_it(shop: Shop) -> None:
    kind = await a_type(shop, people=2, delivery="named")
    busy = kind["person_ids"][0]
    await booked(
        shop.team,
        shop.team.admin,
        {"title": "Away", "minutes": 60, "starts_at": at(9), "people": [busy]},
    )
    async with tx(shop.w.pool, shop.tenant) as conn:
        offers = await bot.offer(
            conn,
            shop.client,
            type_id=uuid.UUID(kind["id"]),
            day_from=date(2031, 3, 4),
            now=NOW,
            limit=6,
        )
        people = {str(p.id): p.display_name for p in await bot.setup.list_people(conn, shop.client)}
    first, last = offers[0], offers[-1]
    assert first.starts_at == datetime(2031, 3, 4, 9, 0, tzinfo=UTC)
    free = [name for person, name in people.items() if person != busy]
    assert first.label.endswith(f"with {free[0]}")
    assert last.starts_at >= datetime(2031, 3, 4, 10, 0, tzinfo=UTC)
    assert "with" not in last.label


# I3: offers after a refused change come from the booking's own people.


async def test_offers_after_a_refused_change_can_be_taken(shop: Shop) -> None:
    kind = await a_type(shop, people=2)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"], TWO)
    assert made.booking is not None
    mine = made.booking.people[0].person_id
    await booked(
        shop.team,
        shop.team.admin,
        {"title": "Away", "minutes": 180, "starts_at": at(9), "people": [str(mine)]},
    )
    refused = await shop.change(contact, made.booking.id, datetime(2031, 3, 4, 9, 0, tzinfo=UTC))
    assert refused.code == "slot_taken" and refused.offers
    assert all(o.starts_at >= datetime(2031, 3, 4, 12, 0, tzinfo=UTC) for o in refused.offers)
    taken = await shop.change(contact, made.booking.id, refused.offers[0].starts_at)
    assert taken.code == "moved", taken


# I4 and I5: the server checks every time.


@pytest.mark.parametrize(
    "starts_at",
    [
        datetime(2031, 3, 4, 10, 7, tzinfo=UTC),
        datetime(2031, 3, 4, 10, 0, 30, tzinfo=UTC),
        datetime(2031, 3, 4, 10, 0),
        datetime(2200, 3, 4, 10, 0, tzinfo=UTC),
    ],
    ids=["off the grid", "seconds", "no offset", "absurd year"],
)
async def test_a_time_the_calendar_never_offers_is_not_allowed(
    shop: Shop, starts_at: datetime
) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"], TWO)
    assert made.booking is not None
    booking = await shop.book(await shop.customer(), kind["id"], starts_at)
    assert booking.code == "not_allowed" and booking.booking is None and booking.offers
    changed = await shop.change(contact, made.booking.id, starts_at)
    assert changed.code == "not_allowed" and changed.booking is None and changed.offers
    (row,) = await shop.rows()
    assert (row["status"], row["starts_at"]) == ("confirmed", TWO)


async def test_a_naive_time_is_never_read_in_the_hosts_zone(shop: Shop) -> None:
    await shop.w.q("UPDATE clients SET timezone = 'Asia/Kuala_Lumpur' WHERE id = %s", shop.tenant)
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"], datetime(2031, 3, 4, 2, 0, tzinfo=UTC))
    assert made.booking is not None
    naive = await shop.change(contact, made.booking.id, datetime(2031, 3, 4, 3, 0))
    assert naive.code == "not_allowed"
    (row,) = await shop.rows()
    assert row["starts_at"] == datetime(2031, 3, 4, 2, 0, tzinfo=UTC)


# I6: every bot write remembers its key.


async def test_a_late_retry_of_a_change_does_not_undo_a_later_one(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    assert (await shop.change(contact, made.booking.id, ELEVEN, turn_key="k1")).code == "moved"
    assert (await shop.change(contact, made.booking.id, TWO, turn_key="k2")).code == "moved"
    again = await shop.change(contact, made.booking.id, ELEVEN, turn_key="k1")
    assert again.code == "replay" and again.booking is not None
    assert again.booking.starts_at == TWO
    (row,) = await shop.rows()
    assert row["starts_at"] == TWO


async def test_a_key_answers_only_for_what_it_did(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    first = await shop.book(contact, kind["id"], TEN)
    second = await shop.book(contact, kind["id"], ELEVEN)
    assert first.booking is not None and second.booking is not None
    assert (await shop.cancel(contact, first.booking.id, turn_key="k")).code == "cancelled"
    # The same message again, even re-planned onto another booking, cancels nothing more.
    replay = await shop.cancel(contact, first.booking.id, turn_key="k")
    assert replay.code == "replay" and replay.booking is not None
    assert replay.booking.status == "cancelled"
    other = await shop.cancel(contact, second.booking.id, turn_key="k")
    assert other == bot.BotOutcome("not_found")
    changed = await shop.change(contact, second.booking.id, TWO, turn_key="k")
    assert changed == bot.BotOutcome("not_found")
    rebooked = await shop.book(contact, kind["id"], TWO, turn_key="k")
    assert rebooked == bot.BotOutcome("not_found")
    assert [r["status"] for r in await shop.rows()] == ["cancelled", "confirmed"]


async def test_a_replay_of_a_since_cancelled_booking_says_so(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    first = await shop.book(contact, kind["id"], turn_key="k")
    assert first.booking is not None
    await shop.cancel(contact, first.booking.id)
    again = await shop.book(contact, kind["id"], turn_key="k")
    assert again.code == "replay" and again.booking is not None
    assert again.booking.id == first.booking.id and again.booking.status == "cancelled"
    assert len(await shop.rows()) == 1


# I7: a pending change is not the booking.


async def test_cancelling_a_pending_change_withdraws_it_and_the_booking_stands(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    outcome = await shop.cancel(contact, asked.booking.id)
    assert outcome.code == "change_withdrawn"
    assert outcome.booking is not None and outcome.booking.id == old.booking.id
    assert outcome.booking.status == "confirmed" and outcome.booking.starts_at == TEN
    original, request = await shop.rows()
    assert original["status"] == "confirmed" and await shop.holds(original["id"]) == 1
    assert request["status"] == "cancelled" and await shop.holds(request["id"]) == 0


async def test_changing_a_pending_change_replaces_that_request(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    await shop.mode("suggest")
    newer = await shop.change(contact, asked.booking.id, TWO)
    assert newer.code == "change_pending" and newer.booking is not None
    assert newer.booking.replaces_booking_id == old.booking.id
    assert [(r["status"], r["starts_at"]) for r in await shop.rows()] == [
        ("confirmed", TEN),
        ("cancelled", ELEVEN),
        ("pending", TWO),
    ]


# M4: a pending hold never outlives its start, and a repeat does not extend it.


async def test_a_pending_expiry_never_outlasts_the_start(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.mode("suggest")
    soon = TEN - timedelta(hours=3)
    await shop.book(await shop.customer(), kind["id"], now=soon)
    (row,) = await shop.rows()
    assert row["expires_at"] == TEN


async def test_repeating_a_change_request_keeps_the_earliest_expiry(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    old = await shop.book(contact, kind["id"])
    assert old.booking is not None
    await shop.mode("suggest")
    wednesday = datetime(2031, 3, 5, 15, 0, tzinfo=UTC)
    first = await shop.change(contact, old.booking.id, wednesday)
    again = await shop.change(contact, old.booking.id, wednesday, now=NOW + timedelta(hours=5))
    assert (first.code, again.code) == ("change_pending", "change_pending")
    _, dropped, kept = await shop.rows()
    assert dropped["status"] == "cancelled"
    assert kept["expires_at"] == NOW + timedelta(hours=24)
    other = await shop.change(contact, old.booking.id, TWO, now=NOW + timedelta(hours=6))
    assert other.code == "change_pending"
    assert (await shop.rows())[-1]["expires_at"] == TWO  # a day on is after it starts


# M5 and the limit.


async def test_the_limit_ignores_pending_changes_past_bookings_and_private_meetings(
    shop: Shop,
) -> None:
    kind = await a_type(shop)
    await shop.w.q(
        "INSERT INTO booking_settings (client_id, customer_limit) VALUES (%s, 2)", shop.tenant
    )
    contact = await shop.customer()
    for private, day in ((False, "2031-02-03"), (True, "2031-03-20")):
        await shop.w.q(
            "INSERT INTO bookings (client_id, contact_id, service, status, starts_at, ends_at,"
            " private) VALUES (%s, %s, 'Old', 'confirmed', %s, %s, %s)",
            shop.tenant,
            str(contact),
            f"{day}T10:00Z",
            f"{day}T10:30Z",
            private,
        )
    old, _ = await pending_change(shop, kind, contact)
    assert old.booking is not None
    second = await shop.book(contact, kind["id"], TWO)
    assert second.code == "confirmed", second
    third = await shop.book(contact, kind["id"], THREE)
    assert third.code == "limit_reached"


# M2: the reply mode.


@pytest.mark.parametrize(
    ("policy", "mode"),
    [
        ({}, "auto"),
        ({"auto_send": {"default": True}}, "auto"),
        ({"deployment_mode": "suggest"}, "suggest"),
        ({"deployment_mode": "paused"}, "shadow"),
        ({"deployment_mode": None}, "shadow"),
        ({"deployment_mode": 3}, "shadow"),
        ([1], "shadow"),
        ("auto", "shadow"),
    ],
)
async def test_only_a_missing_key_or_an_empty_policy_is_auto(
    shop: Shop, policy: object, mode: str
) -> None:
    await shop.policy(policy)
    async with tx(shop.w.pool, shop.tenant) as conn:
        assert await bot.reply_mode(conn, shop.client) == mode


async def test_an_unknown_mode_writes_nothing(shop: Shop) -> None:
    kind = await a_type(shop)
    await shop.policy({"deployment_mode": "paused"})
    outcome = await shop.book(await shop.customer(), kind["id"])
    assert outcome.code == "replies_off" and await shop.rows() == []


# M1: gaps the review found.


async def test_customer_support_suspended_stops_change_and_cancel(shop: Shop) -> None:
    kind = await a_type(shop)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    await shop.suspend()
    assert (await shop.change(contact, made.booking.id, ELEVEN)).code == "service_off"
    assert (await shop.cancel(contact, made.booking.id)).code == "service_off"
    (row,) = await shop.rows()
    assert (row["status"], row["starts_at"]) == ("confirmed", TEN)


async def test_change_obeys_max_advance_and_hours(shop: Shop) -> None:
    kind = await a_type(shop, max_advance_days=10)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    for starts_at in (
        datetime(2031, 4, 1, 10, 0, tzinfo=UTC),
        datetime(2031, 3, 4, 7, 0, tzinfo=UTC),
    ):
        outcome = await shop.change(contact, made.booking.id, starts_at)
        assert outcome.code == "not_allowed" and outcome.booking is None and outcome.offers
        assert all(o.starts_at != starts_at for o in outcome.offers)
    (row,) = await shop.rows()
    assert row["starts_at"] == TEN


async def test_a_company_in_another_zone_is_judged_there(shop: Shop) -> None:
    await shop.w.q("UPDATE clients SET timezone = 'Asia/Kuala_Lumpur' WHERE id = %s", shop.tenant)
    kind = await a_type(shop)
    contact = await shop.customer()
    # 02:00 UTC is 10:00 in Kuala Lumpur; 10:00 UTC is 18:00 there, after closing.
    late = await shop.book(contact, kind["id"], TEN)
    assert late.code == "not_allowed" and late.offers
    assert late.offers[0].starts_at == datetime(2031, 3, 4, 1, 0, tzinfo=UTC)
    assert late.offers[0].label == "Tuesday 4 March 2031, 9:00 am"
    morning = await shop.book(contact, kind["id"], datetime(2031, 3, 4, 2, 0, tzinfo=UTC))
    assert morning.code == "confirmed"


@pytest.mark.parametrize(
    ("minutes", "code"), [(121, "moved"), (120, "moved"), (119, "past_cutoff")]
)
async def test_the_cutoff_is_exact(shop: Shop, minutes: int, code: str) -> None:
    kind = await a_type(shop, change_cutoff_minutes=120)
    contact = await shop.customer()
    made = await shop.book(contact, kind["id"])
    assert made.booking is not None
    at_cutoff = TEN - timedelta(minutes=minutes)
    assert (await shop.change(contact, made.booking.id, ELEVEN, now=at_cutoff)).code == code


# One customer, one message at a time: a change to a request and a cancel of its booking lock the
# same two rows, in opposite orders, unless the customer's lock comes first.


async def test_two_messages_from_one_customer_do_not_deadlock(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from psycopg.errors import DeadlockDetected

    kind = await a_type(shop)
    contact = await shop.customer()
    old, asked = await pending_change(shop, kind, contact)
    assert old.booking is not None and asked.booking is not None
    # Make the two meet: each waits, after its first row lock, for the other to have taken its own.
    real = bot._own
    first_seen: set[asyncio.Task[Any] | None] = set()
    both = asyncio.Event()

    async def meeting_point(*args: Any, **kwargs: Any) -> Any:
        row = await real(*args, **kwargs)
        me = asyncio.current_task()
        if me not in first_seen:
            first_seen.add(me)
            if len(first_seen) == 2:
                both.set()
            else:
                try:
                    await asyncio.wait_for(both.wait(), 1.5)
                except TimeoutError:
                    pass  # the other message is queued behind this one's lock: as intended
        return row

    monkeypatch.setattr(bot, "_own", meeting_point)
    results = await asyncio.gather(
        shop.change(contact, asked.booking.id, TWO),
        shop.cancel(contact, old.booking.id),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, DeadlockDetected)], results
    assert all(isinstance(r, bot.BotOutcome) for r in results), results
