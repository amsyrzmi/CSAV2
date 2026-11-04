"""Booking, moving and cancelling, and the calendar read (stage 1, PR 1, Task 4)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from csa.bookings import service
from csa.bookings.api import build_bookings_router
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_bookings_setup import BASE, Team, make_person, make_resource, make_type
from tests.test_console_sessions import SessionWorld
from tests.test_customer_replies import activate_only

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TUESDAY = "2031-03-04"
SATURDAY = "2031-03-08"
TEN = f"{TUESDAY}T10:00:00Z"


@pytest.fixture(autouse=True)
async def prepared(sessions: SessionWorld) -> None:
    """Bookings on in both companies, and their time zone UTC so the tests can say 10:00."""
    sessions.app.include_router(build_bookings_router())
    for tenant in sessions.tenants:
        await activate_only(sessions, tenant, "bookings")
        await sessions.q("UPDATE clients SET timezone = 'UTC' WHERE id = %s", tenant)


@pytest.fixture
async def team(sessions: SessionWorld) -> Team:
    return await Team(sessions).open()


def soon_tuesday() -> str:
    """A Tuesday two to three weeks from today, for the tests that depend on the real clock."""
    day = date.today() + timedelta(days=14)
    return (day + timedelta(days=(1 - day.weekday()) % 7)).isoformat()


def at(hour: int, minute: int = 0, day: str = TUESDAY) -> str:
    return f"{day}T{hour:02d}:{minute:02d}:00Z"


def meeting(*people: dict[str, Any], start: str = TEN, minutes: int = 30, **extra: Any) -> Any:
    return {
        "title": "Weekly sync",
        "minutes": minutes,
        "starts_at": start,
        "people": [p["id"] for p in people],
        **extra,
    }


async def book(t: Team, who: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    return await t.send(who, "/bookings", body)


async def booked(t: Team, who: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    made = await book(t, who, body)
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


async def holds(w: SessionWorld, booking_id: str) -> list[tuple[str, str]]:
    rows = await w.q(
        "SELECT subject_id::text AS s, lower(during)::text AS a, upper(during)::text AS b"
        " FROM booking_holds WHERE booking_id = %s ORDER BY subject_id",
        booking_id,
    )
    return [(r["s"], f"{r['a']}/{r['b']}") for r in rows]


async def approve_leave(w: SessionWorld, employee: str, first: str, last: str) -> None:
    """An approved leave case, as an owner-only fixture (the HR triggers want an audit trail)."""
    async with await psycopg.AsyncConnection.connect(w.owner_dsn, autocommit=True) as conn:
        await conn.execute("SET session_replication_role = replica")
        await conn.execute(
            "INSERT INTO hr_cases (client_id, id, domain, employee_id, created_by, payload,"
            " status, version, updated_at) VALUES (%s,%s,'leave',%s,%s,%s,'approved',0,now())",
            (
                w.tenants[0],
                str(uuid.uuid4()),
                employee,
                w.admin_id,
                Jsonb(
                    {
                        "domain": "leave",
                        "starts_on": first,
                        "ends_on": last,
                        "leave_type": "annual",
                        "reason": "synthetic private reason",
                    }
                ),
            ),
        )


async def person_on_leave(t: Team, first: str = TUESDAY, last: str = TUESDAY) -> dict[str, Any]:
    employee = str(uuid.uuid4())
    await t.w.q(
        "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s,%s,'Synthetic')",
        t.w.tenants[0],
        employee,
    )
    person = await make_person(t, "Away Person", employee_id=employee)
    await approve_leave(t.w, employee, first, last)
    return person


def no_leave_words(response: httpx.Response) -> None:
    """Nothing in a response says leave: not its type, not its reason, not the word itself."""
    text = response.text.lower()
    for word in ("annual", "reason", "leave_type", "starts_on", "on_leave", "leave"):
        assert word not in text, response.text


# --- no double booking ---------------------------------------------------------------------


async def test_two_simultaneous_requests_for_the_last_slot(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    results = await asyncio.gather(
        book(team, team.admin, meeting(person)), book(team, team.staff, meeting(person))
    )
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
    lost = next(r for r in results if r.status_code == 409)
    assert lost.json() == {"detail": "slot_taken", "message": "That time was just taken."}
    rows = await sessions.q(
        "SELECT count(*) AS n FROM bookings WHERE client_id=%s", sessions.tenants[0]
    )
    assert rows[0]["n"] == 1


async def test_people_asked_for_in_opposite_orders_cannot_deadlock(
    sessions: SessionWorld, team: Team
) -> None:
    a, b = await make_person(team, "Aina"), await make_person(team, "Bakar")
    results = await asyncio.gather(
        *[
            book(team, team.admin, meeting(*(pair if n % 2 else pair[::-1])))
            for n, pair in enumerate([(a, b)] * 6)
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409, 409], [r.text for r in results]
    assert {r.json()["detail"] for r in results if r.status_code == 409} == {"slot_taken"}


async def test_any_free_person_gets_the_least_busy(sessions: SessionWorld, team: Team) -> None:
    busy, light = await make_person(team, "Aina"), await make_person(team, "Bakar")
    kind = await make_type(team, delivery="any", person_ids=[busy["id"], light["id"]])
    for hour in (9, 12):  # Aina has two bookings that day, Bakar none yet
        await booked(team, team.admin, meeting(busy, start=at(hour)))
    await booked(team, team.admin, meeting(light, start=at(13)))
    first = await booked(team, team.staff, {"type_id": kind["id"], "starts_at": at(15)})
    assert [p["id"] for p in first["people"]] == [light["id"]]
    # Bakar is now taken at 11:00 too, so Aina gets that one even though she is busier.
    await booked(team, team.admin, meeting(light, start=at(11)))
    second = await booked(team, team.staff, {"type_id": kind["id"], "starts_at": at(11)})
    assert [p["id"] for p in second["people"]] == [busy["id"]]
    # Nobody is free: the time was just taken.
    await booked(team, team.admin, meeting(busy, start=at(16)))
    await booked(team, team.admin, meeting(light, start=at(16)))
    full = await book(team, team.staff, {"type_id": kind["id"], "starts_at": at(16)})
    assert full.status_code == 409 and full.json()["detail"] == "slot_taken"


async def test_several_people_all_hold_the_time(sessions: SessionWorld, team: Team) -> None:
    a, b = await make_person(team, "Aina"), await make_person(team, "Bakar")
    kind = await make_type(team, delivery="several", person_ids=[a["id"], b["id"]])
    made = await booked(team, team.staff, {"type_id": kind["id"], "starts_at": TEN})
    assert {p["id"] for p in made["people"]} == {a["id"], b["id"]}
    assert len(await holds(sessions, made["id"])) == 2
    for who in (a, b):
        clash = await book(team, team.admin, meeting(who, start=at(10, 15)))
        assert clash.status_code == 409 and clash.json()["detail"] == "slot_taken"


async def test_a_room_is_held(sessions: SessionWorld, team: Team) -> None:
    a, b = await make_person(team, "Aina"), await make_person(team, "Bakar")
    one, two = await make_resource(team, "Room 1"), await make_resource(team, "Room 2")
    specific = await make_type(
        team, name="Fitting", resource_rule="specific", resource_ids=[one["id"]]
    )
    made = await booked(
        team, team.staff, {"type_id": specific["id"], "starts_at": TEN, "people": [a["id"]]}
    )
    assert [r["id"] for r in made["resources"]] == [one["id"]]
    assert len(await holds(sessions, made["id"])) == 2  # the person and the room
    same_room = await book(
        team, team.staff, {"type_id": specific["id"], "starts_at": TEN, "people": [b["id"]]}
    )
    assert same_room.status_code == 409 and same_room.json()["detail"] == "slot_taken"
    # A meeting can ask for a room by itself, and an "any room" type picks the free one.
    other = await book(team, team.admin, meeting(b, start=TEN, resources=[one["id"]]))
    assert other.status_code == 409
    anyroom = await make_type(
        team, name="Class", resource_rule="any", resource_ids=[one["id"], two["id"]]
    )
    picked = await booked(
        team, team.staff, {"type_id": anyroom["id"], "starts_at": TEN, "people": [b["id"]]}
    )
    assert [r["id"] for r in picked["resources"]] == [two["id"]]


async def test_the_hold_covers_the_buffer(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    kind = await make_type(team, buffer_minutes=15, delivery="named", person_ids=[person["id"]])
    made = await booked(
        team, team.staff, {"type_id": kind["id"], "starts_at": TEN, "people": [person["id"]]}
    )
    assert made["ends_at"].startswith(f"{TUESDAY}T10:30")
    assert (await holds(sessions, made["id"]))[0][1].endswith("10:45:00+00")
    inside = await book(team, team.admin, meeting(person, start=at(10, 30)))
    assert inside.status_code == 409 and inside.json()["detail"] == "slot_taken"
    assert (await book(team, team.admin, meeting(person, start=at(10, 45)))).status_code == 201


async def test_a_request_must_make_sense(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    kind = await make_type(team)
    inactive = await make_person(team, "Gone")
    await team.send(
        team.admin, f"/people/{inactive['id']}", {"version": 1, "is_active": False}, "PATCH"
    )
    for body in (
        {**meeting(person), "minutes": 0},
        {**meeting(person), "title": None},
        {**meeting(person), "minutes": None},
        {**meeting(person), "type_id": kind["id"]},
        {"type_id": kind["id"], "starts_at": "2031-03-04T10:00:00"},  # no time zone
        {**meeting(person), "walk_in": {"name": "Nobody"}},  # a walk-in needs a way to reach them
        {
            **meeting(person),
            "contact_id": str(uuid.uuid4()),
            "walk_in": {"name": "X", "phone": "123"},
        },
        {**meeting(person), "guests": [{"email": "a\nb@example.test"}]},
        {**meeting(person), "acknowledged": ["not_a_finding"]},
    ):
        assert (await book(team, team.admin, body)).status_code == 422, body
    for body, code in (
        (meeting(), "nobody_to_book"),
        (meeting(inactive), "person_inactive"),
        ({"type_id": str(uuid.uuid4()), "starts_at": TEN}, "type_not_found"),
        (
            {"type_id": kind["id"], "starts_at": TEN, "contact_id": str(uuid.uuid4())},
            "contact_not_found",
        ),
        (meeting(person, resources=[str(uuid.uuid4())]), "resource_not_found"),
    ):
        refused = await book(team, team.admin, body)
        assert refused.status_code == 422, (body, refused.text)
        if isinstance(code, str):
            assert refused.json()["detail"] == code
    named = await make_type(team, name="Named", delivery="named", person_ids=[person["id"]])
    asked = await book(team, team.admin, {"type_id": named["id"], "starts_at": TEN})
    assert asked.status_code == 422 and asked.json()["detail"] == "people_required"
    private_type = await book(
        team, team.admin, {"type_id": kind["id"], "starts_at": TEN, "private": True}
    )
    assert private_type.json()["detail"] == "private_meetings_only"
    assert service.window_end(datetime(2031, 1, 1, tzinfo=UTC), 30) > datetime(
        2031, 1, 1, tzinfo=UTC
    )
    with pytest.raises(Exception, match="times_invalid"):
        service.window_end(datetime(2031, 1, 1, tzinfo=UTC), 0)


# --- the rules --------------------------------------------------------------------------------


async def test_staff_outside_hours_needs_acknowledging_then_books(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    late = meeting(person, start=at(20))
    asked = await book(team, team.staff, late)
    assert asked.status_code == 409
    assert asked.json() == {
        "detail": "needs_acknowledging",
        "message": "Outside working hours.",
        "findings": ["outside_hours"],
    }
    nothing = await sessions.q(
        "SELECT count(*) AS n FROM bookings WHERE client_id=%s", sessions.tenants[0]
    )
    assert nothing[0]["n"] == 0
    made = await book(team, team.staff, {**late, "acknowledged": ["outside_hours"]})
    assert made.status_code == 201, made.text
    # The default week has no weekend, so a Saturday needs the same acknowledgement.
    weekend = meeting(person, start=at(10, day=SATURDAY))
    assert (await book(team, team.staff, weekend)).json()["findings"] == ["outside_hours"]
    assert (
        await book(team, team.admin, {**weekend, "acknowledged": ["outside_hours"]})
    ).status_code == 201


async def test_a_closed_day_needs_acknowledging(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    await team.send(team.admin, "/closed-days", {"day": TUESDAY})
    asked = await book(team, team.staff, meeting(person))
    assert asked.status_code == 409
    assert asked.json()["findings"] == ["closed_day"]
    assert asked.json()["message"] == "This is a closed day."
    both = await book(team, team.staff, meeting(person, start=at(20)))
    assert both.json()["findings"] == ["closed_day", "outside_hours"]
    assert both.json()["message"] == "This is a closed day. Outside working hours."
    done = await book(team, team.staff, {**meeting(person), "acknowledged": ["closed_day"]})
    assert done.status_code == 201


async def test_a_person_with_their_own_hours_is_checked_against_them(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    own = {
        "subject_kind": "person",
        "subject_id": person["id"],
        "periods": [{"weekday": 1, "opens": "12:00", "closes": "14:00"}],
    }
    assert (await team.send(team.admin, "/hours", own, "PUT")).status_code == 200
    assert (await book(team, team.staff, meeting(person))).json()["findings"] == ["outside_hours"]
    assert (await book(team, team.staff, meeting(person, start=at(12)))).status_code == 201


async def link_to_hr(t: Team, user_id: str) -> str:
    """An HR person linked to an account after the booking person was made, as an owner fixture."""
    employee = str(uuid.uuid4())
    await t.w.q(
        "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s,%s,'Synthetic')",
        t.w.tenants[0],
        employee,
    )
    await t.w.q(
        "INSERT INTO hr_employee_accounts (client_id, user_id, employee_id) VALUES (%s,%s,%s)",
        t.w.tenants[0],
        user_id,
        employee,
    )
    return employee


async def test_leave_follows_an_account_linked_to_hr_after_the_person_was_added(
    sessions: SessionWorld, team: Team
) -> None:
    day = soon_tuesday()
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    person = await make_person(team, "Late Link", user_id=team.staff_info["user"]["id"])
    assert person["employee_id"] is None
    kind = await make_type(team, delivery="named", person_ids=[person["id"]], max_advance_days=60)
    before = await booked(team, team.staff, meeting(person, start=at(10, day=next_day)))
    employee = await link_to_hr(team, team.staff_info["user"]["id"])
    await approve_leave(sessions, employee, day, day)
    # Not offered, whatever the stored column says.
    window = f"from={day}T00:00:00Z&to={next_day}T23:59:00Z"
    got = await team.staff.get(
        f"{BASE}/availability?type_id={kind['id']}&people={person['id']}&{window}"
    )
    assert {s["starts_at"][:10] for s in got.json()} == {next_day}
    no_leave_words(got)
    # Staff are refused, and nothing says why.
    asked = await book(team, team.staff, meeting(person, start=at(10, day=day)))
    assert asked.status_code == 409
    assert asked.json()["message"] == "Not available that day."
    assert asked.json()["findings"] == ["unavailable"]
    no_leave_words(asked)
    # A booking already there on a leave day shows the flag.
    await approve_leave(sessions, employee, next_day, next_day)
    window = f"?from={next_day}T00:00:00Z&to={next_day}T23:59:00Z"
    shown = await team.reader.get(f"{BASE}/calendar{window}")
    assert {b["id"]: b["conflict"] for b in shown.json()["items"]}[before["id"]] == "unavailable"
    no_leave_words(shown)


async def test_an_account_person_without_an_hr_link_books_normally(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team, "No HR", user_id=team.staff_info["user"]["id"])
    made = await booked(team, team.staff, meeting(person))
    assert made["conflict"] is None
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    shown = await team.reader.get(f"{BASE}/calendar{window}")
    assert [b["conflict"] for b in shown.json()["items"]] == [None]


async def test_staff_cannot_book_a_person_on_leave(sessions: SessionWorld, team: Team) -> None:
    person = await person_on_leave(team)
    asked = await book(team, team.staff, meeting(person))
    assert asked.status_code == 409
    assert asked.json() == {
        "detail": "refused",
        "message": "Not available that day.",
        "findings": ["unavailable"],
    }
    no_leave_words(asked)
    acknowledged = await book(
        team, team.staff, {**meeting(person), "acknowledged": ["unavailable"]}
    )
    assert acknowledged.status_code == 409 and acknowledged.json()["detail"] == "refused"
    no_leave_words(acknowledged)
    # The day after is fine.
    assert (
        await book(team, team.staff, meeting(person, start=at(10, day="2031-03-05")))
    ).status_code == 201


async def test_an_admin_can_book_a_person_on_leave_after_acknowledging(
    sessions: SessionWorld, team: Team
) -> None:
    person = await person_on_leave(team)
    asked = await book(team, team.admin, meeting(person))
    assert asked.status_code == 409
    assert asked.json() == {
        "detail": "needs_acknowledging",
        "message": "Not available that day.",
        "findings": ["unavailable"],
    }
    no_leave_words(asked)
    made = await book(team, team.admin, {**meeting(person), "acknowledged": ["unavailable"]})
    assert made.status_code == 201, made.text
    no_leave_words(made)
    assert made.json()["conflict"] == "unavailable"
    # A platform admin who chose the company acts as an Admin.
    other = await make_person(team, "Other")
    chosen = await sessions.mutate(
        sessions.admin,
        f"{BASE}/bookings?client_id={sessions.tenants[0]}",
        meeting(other, start=at(20)),
    )
    assert chosen.status_code == 409 and chosen.json()["findings"] == ["outside_hours"]


async def test_leave_approved_after_a_booking_flags_the_conflict(
    sessions: SessionWorld, team: Team
) -> None:
    employee = str(uuid.uuid4())
    await sessions.q(
        "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s,%s,'Synthetic')",
        sessions.tenants[0],
        employee,
    )
    person = await make_person(team, "Linked", employee_id=employee)
    free = await make_person(team, "Unlinked")
    made = await booked(team, team.staff, meeting(person))
    other = await booked(team, team.staff, meeting(free))
    assert made["conflict"] is None
    await approve_leave(sessions, employee, TUESDAY, TUESDAY)
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    shown = await team.reader.get(f"{BASE}/calendar{window}")
    flagged = {b["id"]: b for b in shown.json()["items"]}
    assert flagged[made["id"]]["conflict"] == "unavailable"
    assert flagged[made["id"]]["status"] == "confirmed"  # nothing is cancelled for them
    assert flagged[other["id"]]["conflict"] is None
    no_leave_words(shown)
    assert "annual" not in json.dumps(flagged)


async def test_short_notice_is_allowed_for_staff(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    kind = await make_type(
        team, delivery="named", person_ids=[person["id"]], min_notice_minutes=525600
    )
    soon = datetime.now(UTC) + timedelta(days=3)
    while soon.weekday() > 4:
        soon += timedelta(days=1)
    start = soon.replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
    body = {"type_id": kind["id"], "starts_at": start, "people": [person["id"]]}
    for who in (team.staff, team.admin):
        made = await book(team, who, {**body, "starts_at": start})
        assert made.status_code == 201, made.text
        start = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()


# --- moving, cancelling and the rest ----------------------------------------------------------


async def test_a_move_keeps_the_booking_and_replaces_its_holds(
    sessions: SessionWorld, team: Team
) -> None:
    person, room = await make_person(team), await make_resource(team)
    made = await booked(team, team.staff, meeting(person, resources=[room["id"]]))
    moved = await team.send(
        team.staff,
        f"/bookings/{made['id']}",
        {"version": made["version"], "starts_at": at(14)},
        "PATCH",
    )
    assert moved.status_code == 200, moved.text
    out = moved.json()
    assert out["id"] == made["id"] and out["version"] == made["version"] + 1
    assert out["starts_at"].startswith(f"{TUESDAY}T14:00") and out["ends_at"].startswith(
        f"{TUESDAY}T14:30"
    )
    assert {p["id"] for p in out["people"]} == {person["id"]}
    assert [r["id"] for r in out["resources"]] == [room["id"]]
    held = await holds(sessions, made["id"])
    assert len(held) == 2 and all(span.startswith(f"{TUESDAY} 14:00") for _, span in held)
    # The old time is free again, and the next move needs the new version.
    assert (await book(team, team.admin, meeting(person))).status_code == 201
    old = await team.send(
        team.staff,
        f"/bookings/{made['id']}",
        {"version": made["version"], "starts_at": at(15)},
        "PATCH",
    )
    assert old.status_code == 409
    assert old.json() == {"detail": "stale", "message": "This booking was changed by someone else."}


async def test_a_failed_move_leaves_the_booking_and_holds_unchanged(
    sessions: SessionWorld, team: Team
) -> None:
    person, room = await make_person(team), await make_resource(team)
    first = await booked(team, team.staff, meeting(person, start=at(10)))
    second = await booked(team, team.staff, meeting(person, start=at(11), resources=[room["id"]]))
    before = await sessions.q("SELECT * FROM bookings WHERE id = %s", second["id"])
    held = await holds(sessions, second["id"])
    # Into the first booking's time: the person is taken, so the whole move fails.
    clash = await team.send(
        team.staff, f"/bookings/{second['id']}", {"version": 1, "starts_at": at(10, 15)}, "PATCH"
    )
    assert clash.status_code == 409 and clash.json()["detail"] == "slot_taken"
    # Into a time that needs acknowledging.
    late = await team.send(
        team.staff, f"/bookings/{second['id']}", {"version": 1, "starts_at": at(21)}, "PATCH"
    )
    assert late.status_code == 409 and late.json()["detail"] == "needs_acknowledging"
    assert await sessions.q("SELECT * FROM bookings WHERE id = %s", second["id"]) == before
    assert await holds(sessions, second["id"]) == held
    assert len(await holds(sessions, first["id"])) == 1
    # A move that fails halfway, with the room taken by someone else, also changes nothing.
    other = await make_person(team, "Bakar")
    await booked(team, team.admin, meeting(other, start=at(16), resources=[room["id"]]))
    halfway = await team.send(
        team.staff, f"/bookings/{second['id']}", {"version": 1, "starts_at": at(16)}, "PATCH"
    )
    assert halfway.status_code == 409 and halfway.json()["detail"] == "slot_taken"
    assert await sessions.q("SELECT * FROM bookings WHERE id = %s", second["id"]) == before
    assert await holds(sessions, second["id"]) == held


async def test_a_move_to_a_closed_day_or_leave_follows_the_same_rules(
    sessions: SessionWorld, team: Team
) -> None:
    person = await person_on_leave(team, "2031-03-05", "2031-03-05")
    made = await booked(team, team.staff, meeting(person))
    day = {"version": 1, "starts_at": at(10, day="2031-03-05")}
    staff = await team.send(team.staff, f"/bookings/{made['id']}", day, "PATCH")
    assert staff.status_code == 409 and staff.json()["detail"] == "refused"
    admin = await team.send(team.admin, f"/bookings/{made['id']}", day, "PATCH")
    assert admin.status_code == 409 and admin.json()["detail"] == "needs_acknowledging"
    assert staff.json()["findings"] == admin.json()["findings"] == ["unavailable"]
    no_leave_words(staff)
    no_leave_words(admin)
    done = await team.send(
        team.admin, f"/bookings/{made['id']}", {**day, "acknowledged": ["unavailable"]}, "PATCH"
    )
    assert done.status_code == 200 and done.json()["conflict"] == "unavailable"
    no_leave_words(done)


async def test_cancelling_frees_the_slot(sessions: SessionWorld, team: Team) -> None:
    person, room = await make_person(team), await make_resource(team)
    made = await booked(team, team.staff, meeting(person, resources=[room["id"]]))
    cancelled = await team.send(team.staff, f"/bookings/{made['id']}/cancel", {"version": 1})
    assert cancelled.status_code == 200, cancelled.text
    out = cancelled.json()
    assert out["status"] == "cancelled" and out["version"] == 2
    assert [r["id"] for r in out["resources"]] == [room["id"]]
    assert await holds(sessions, made["id"]) == []
    again = await booked(team, team.admin, meeting(person, resources=[room["id"]]))
    assert again["id"] != made["id"]
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    listed = [b["id"] for b in (await team.reader.get(f"{BASE}/calendar{window}")).json()["items"]]
    assert listed == [again["id"]]
    twice = await team.send(team.staff, f"/bookings/{made['id']}/cancel")
    assert twice.status_code == 409 and twice.json()["detail"] == "wrong_status"
    gone = await team.send(
        team.staff, f"/bookings/{made['id']}", {"version": 2, "starts_at": at(15)}, "PATCH"
    )
    assert gone.status_code == 409 and gone.json()["detail"] == "wrong_status"


async def test_no_show_keeps_the_record(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    past = await booked(team, team.staff, meeting(person, start="2020-01-07T10:00:00Z"))
    future = await booked(team, team.staff, meeting(person))
    early = await team.send(team.staff, f"/bookings/{future['id']}/no-show")
    assert early.status_code == 409 and early.json()["detail"] == "not_started"
    marked = await team.send(team.staff, f"/bookings/{past['id']}/no-show", {"version": 1})
    assert marked.status_code == 200 and marked.json()["status"] == "no_show"
    kept = await sessions.q("SELECT status, cancelled_at FROM bookings WHERE id=%s", past["id"])
    assert kept == [{"status": "no_show", "cancelled_at": None}]
    assert len(await holds(sessions, past["id"])) == 1
    shown = await team.reader.get(
        f"{BASE}/calendar?from=2020-01-07T00:00:00Z&to=2020-01-08T00:00:00Z"
    )
    assert [(b["id"], b["status"]) for b in shown.json()["items"]] == [(past["id"], "no_show")]
    again = await team.send(team.staff, f"/bookings/{past['id']}/no-show")
    assert again.status_code == 409 and again.json()["detail"] == "wrong_status"


async def test_confirm_and_decline_a_pending_booking(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    kind = await make_type(
        team, needs_confirmation=True, delivery="named", person_ids=[person["id"]]
    )
    ask = {"type_id": kind["id"], "people": [person["id"]]}
    pending = await booked(team, team.staff, {**ask, "starts_at": TEN})
    assert pending["status"] == "pending"
    # A pending booking holds the slot.
    clash = await book(team, team.admin, meeting(person, start=at(10, 15)))
    assert clash.status_code == 409
    assert (await team.send(team.staff, f"/bookings/{pending['id']}/no-show")).status_code == 409
    confirmed = await team.send(team.staff, f"/bookings/{pending['id']}/confirm", {"version": 1})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["version"] == 2
    again = await team.send(team.staff, f"/bookings/{pending['id']}/confirm")
    assert again.status_code == 409 and again.json()["detail"] == "wrong_status"

    other = await booked(team, team.staff, {**ask, "starts_at": at(14)})
    declined = await team.send(team.admin, f"/bookings/{other['id']}/decline")
    assert declined.status_code == 200 and declined.json()["status"] == "cancelled"
    assert await holds(sessions, other["id"]) == []
    assert (await book(team, team.admin, meeting(person, start=at(14)))).status_code == 201
    undeclinable = await team.send(team.admin, f"/bookings/{pending['id']}/decline")
    assert undeclinable.status_code == 409
    # A meeting is never pending; only a type that needs confirmation makes a pending booking.
    assert (await booked(team, team.staff, meeting(person, start=at(16))))["status"] == "confirmed"


async def test_a_deactivated_person_keeps_bookings_but_gets_no_new_ones(
    sessions: SessionWorld, team: Team
) -> None:
    gone, stays = await make_person(team, "Aina"), await make_person(team, "Bakar")
    kind = await make_type(team, delivery="any", person_ids=[gone["id"], stays["id"]])
    old = await booked(team, team.staff, meeting(gone))
    off = await team.send(
        team.admin, f"/people/{gone['id']}", {"version": 1, "is_active": False}, "PATCH"
    )
    assert off.status_code == 200
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    shown = (await team.staff.get(f"{BASE}/calendar{window}")).json()["items"]
    assert shown[0]["id"] == old["id"]
    assert shown[0]["people"] == [{"id": gone["id"], "display_name": "Aina", "is_active": False}]
    refused = await book(team, team.staff, meeting(gone, start=at(14)))
    assert refused.status_code == 422 and refused.json()["detail"] == "person_inactive"
    # "Any free person" never picks them, even when they are the free one.
    await booked(team, team.admin, meeting(stays))
    taken = await book(team, team.staff, {"type_id": kind["id"], "starts_at": TEN})
    assert taken.status_code == 409 and taken.json()["detail"] == "slot_taken"
    slots = await team.staff.get(
        f"{BASE}/availability?type_id={kind['id']}&from={TUESDAY}T09:00:00Z&to={TUESDAY}T10:00:00Z"
    )
    assert all(gone["id"] not in s["people"] for s in slots.json())
    # The existing booking can still be moved and cancelled.
    moved = await team.staff.patch(
        f"{BASE}/bookings/{old['id']}",
        json={"version": 1, "starts_at": at(15)},
        headers=team.w.headers(team.staff),
    )
    assert moved.status_code == 200
    assert (await team.send(team.staff, f"/bookings/{old['id']}/cancel")).status_code == 200


# --- customers and guests ---------------------------------------------------------------------


async def test_a_walk_in_creates_a_contact_from_an_empty_list(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    kind = await make_type(team)
    assert (await team.staff.get(f"{BASE}/customers")).json() == []
    walk_in = {"name": "Walk In", "phone": "+60 12-000 0000", "email": "walk.in@example.test"}
    made = await booked(
        team,
        team.staff,
        {"type_id": kind["id"], "starts_at": TEN, "people": [person["id"]], "walk_in": walk_in},
    )
    customer = made["customer"]
    assert customer["full_name"] == "Walk In" and customer["phone"] == "+60 12-000 0000"
    rows = await sessions.q(
        "SELECT contact_kind, full_name, client_id::text AS c FROM contacts WHERE id=%s",
        customer["id"],
    )
    assert rows == [{"contact_kind": "customer", "full_name": "Walk In", "c": sessions.tenants[0]}]
    found = (await team.staff.get(f"{BASE}/customers?q=walk")).json()
    assert [c["id"] for c in found] == [customer["id"]]
    by_phone = (await team.staff.get(f"{BASE}/customers?q=0120000000")).json()
    assert [c["id"] for c in by_phone] == [customer["id"]]
    # The next booking can pick that customer.
    again = await booked(
        team,
        team.staff,
        {
            "type_id": kind["id"],
            "starts_at": at(14),
            "people": [person["id"]],
            "contact_id": customer["id"],
        },
    )
    assert again["customer"]["id"] == customer["id"]
    # The same walk-in typed in again (same email) is the same customer, not a second contact.
    repeat = await booked(
        team,
        team.staff,
        {
            "type_id": kind["id"],
            "starts_at": at(15),
            "people": [person["id"]],
            "walk_in": {"name": "Walk In Again", "email": "walk.in@example.test"},
        },
    )
    assert repeat["customer"]["id"] == customer["id"]
    count = await sessions.q(
        "SELECT count(*) AS n FROM contacts WHERE client_id=%s", sessions.tenants[0]
    )
    assert count[0]["n"] == 1
    assert (await team.other.get(f"{BASE}/customers")).json() == []


async def test_an_outside_guest_is_kept_with_the_meeting(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    guests = [
        {"name": "Supplier", "email": "supplier@example.test"},
        {"email": "Supplier@example.test"},  # the same address twice is kept once
        {"name": "Auditor", "email": "auditor@example.test"},
    ]
    made = await booked(team, team.staff, meeting(person, guests=guests))
    assert sorted(g["email"] for g in made["guests"]) == [
        "auditor@example.test",
        "supplier@example.test",
    ]
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    shown = (await team.admin.get(f"{BASE}/calendar{window}")).json()["items"]
    assert len(shown[0]["guests"]) == 2
    rows = await sessions.q(
        "SELECT count(*) AS n FROM booking_attendees WHERE booking_id=%s AND person_id IS NULL",
        made["id"],
    )
    assert rows[0]["n"] == 2


# --- who may change what ----------------------------------------------------------------------


async def test_staff_change_their_own_or_attended_bookings_only(
    sessions: SessionWorld, team: Team
) -> None:
    mine = await make_person(team, "Staff Person", user_id=team.staff_info["user"]["id"])
    theirs = await make_person(team, "Someone Else")
    created = await booked(team, team.staff, meeting(theirs, start=at(10)))
    attended = await booked(team, team.admin, meeting(mine, start=at(11)))
    foreign = await booked(team, team.admin, meeting(theirs, start=at(12)))
    move = lambda b: team.send(  # noqa: E731
        team.staff, f"/bookings/{b['id']}", {"version": 1, "starts_at": at(15)}, "PATCH"
    )
    assert (await move(foreign)).status_code == 403
    for action in ("cancel", "confirm", "decline", "no-show"):
        refused = await team.send(team.staff, f"/bookings/{foreign['id']}/{action}")
        assert refused.status_code == 403, action
    assert (await team.send(team.staff, f"/bookings/{foreign['id']}/cancel")).json()[
        "detail"
    ] == "own_bookings_only"
    assert (await move(created)).status_code == 200
    cancel = await team.send(team.staff, f"/bookings/{attended['id']}/cancel")
    assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
    # Nothing happened to the one that was not theirs.
    still = await sessions.q("SELECT status, version FROM bookings WHERE id=%s", foreign["id"])
    assert still == [{"status": "confirmed", "version": 1}]
    # An Admin changes any booking.
    assert (await team.send(team.admin, f"/bookings/{foreign['id']}/cancel")).status_code == 200
    # Read-only members book and change nothing.
    other = await booked(team, team.admin, meeting(theirs, start=at(13)))
    assert (await book(team, team.reader, meeting(theirs, start=at(14)))).status_code == 403
    for action in ("cancel", "confirm", "decline", "no-show"):
        denied = await team.send(team.reader, f"/bookings/{other['id']}/{action}")
        assert denied.status_code == 403, action
    moved = await team.send(
        team.reader, f"/bookings/{other['id']}", {"version": 1, "starts_at": at(15)}, "PATCH"
    )
    assert moved.status_code == 403


async def test_can_change_says_what_each_viewer_may_do_and_the_server_agrees(
    sessions: SessionWorld, team: Team
) -> None:
    """`can_change` is computed by the rule `_for_change` enforces, so the buttons never lie."""
    w = sessions
    mine = await make_person(team, "Staff Person", user_id=team.staff_info["user"]["id"])
    theirs = await make_person(team, "Someone Else")
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"

    async def seen(who: httpx.AsyncClient, extra: str = "") -> dict[str, dict[str, Any]]:
        read = await who.get(f"{BASE}/calendar{window}{extra}")
        assert read.status_code == 200, read.text
        return {b["id"]: b for b in read.json()["items"]}

    # Staff created it for someone else and are not attending it.
    created = await booked(team, team.staff, meeting(theirs, start=at(9)))
    assert created["can_change"] is True
    # An Admin made it for someone else; staff neither made it nor attend it.
    foreign = await booked(team, team.admin, meeting(theirs, start=at(10)))
    assert foreign["can_change"] is True
    # An Admin made it and a linked staff person attends it.
    attended = await booked(team, team.admin, meeting(mine, start=at(11)))
    # A private meeting the staff member is not part of, and one they are.
    hidden = await booked(team, team.admin, meeting(theirs, start=at(12), private=True))
    shared = await booked(team, team.admin, meeting(mine, start=at(13), private=True))

    staff = await seen(team.staff)
    assert {k: v["can_change"] for k, v in staff.items()} == {
        created["id"]: True,
        foreign["id"]: False,
        attended["id"]: True,
        hidden["id"]: False,
        shared["id"]: True,
    }
    assert staff[hidden["id"]]["redacted"] is True
    # Admins and platform admins may change everything, a redacted private meeting included.
    platform = await seen(w.admin, f"&client_id={w.tenants[0]}")
    assert platform[hidden["id"]]["redacted"] is True
    for admin in (await seen(team.admin), platform):
        assert {v["can_change"] for v in admin.values()} == {True}
    # Read-only members may change nothing.
    assert {v["can_change"] for v in (await seen(team.reader)).values()} == {False}
    # The server agrees, both ways.
    refused = await team.send(team.staff, f"/bookings/{foreign['id']}/cancel")
    assert refused.status_code == 403 and refused.json()["detail"] == "own_bookings_only"
    for allowed in (created, attended, shared):
        done = await team.send(team.staff, f"/bookings/{allowed['id']}/cancel")
        assert done.status_code == 200, done.text
        assert done.json()["can_change"] is True
    denied = await team.send(team.reader, f"/bookings/{foreign['id']}/cancel")
    assert denied.status_code == 403


async def test_another_company_cannot_read_or_change_a_booking(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    made = await booked(team, team.admin, meeting(person))
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    assert (await team.other.get(f"{BASE}/calendar{window}")).json() == {"items": [], "busy": []}
    assert (
        await team.other.get(f"{BASE}/calendar{window}&client_id={sessions.tenants[0]}")
    ).status_code == 403
    for action in ("cancel", "confirm", "decline", "no-show"):
        gone = await team.send(team.other, f"/bookings/{made['id']}/{action}")
        assert gone.status_code == 404 and gone.json()["detail"] == "booking_not_found", action
    moved = await team.send(
        team.other, f"/bookings/{made['id']}", {"version": 1, "starts_at": at(15)}, "PATCH"
    )
    assert moved.status_code == 404
    # Another company's people, rooms, types and contacts cannot be used.
    elsewhere_person = (await team.send(team.other, "/people", {"display_name": "Other"})).json()
    elsewhere_room = (await team.send(team.other, "/resources", {"name": "Their room"})).json()
    for body, code in (
        (meeting(elsewhere_person), "person_not_found"),
        (meeting(person, resources=[elsewhere_room["id"]]), "resource_not_found"),
    ):
        refused = await book(team, team.admin, body)
        assert refused.status_code == 422 and refused.json()["detail"] == code
    other_kind = (
        await team.send(
            team.other,
            "/types",
            {"name": "Theirs", "duration_minutes": 30, "location_kind": "phone"},
        )
    ).json()
    refused = await book(team, team.admin, {"type_id": other_kind["id"], "starts_at": TEN})
    assert refused.json()["detail"] == "type_not_found"
    their_contact = await sessions.q(
        "INSERT INTO contacts (client_id, full_name) VALUES (%s, 'Elsewhere')"
        " RETURNING id::text AS id",
        sessions.tenants[1],
    )
    refused = await book(
        team, team.admin, meeting(person, start=at(14), contact_id=their_contact[0]["id"])
    )
    assert refused.json()["detail"] == "contact_not_found"
    still = await sessions.q("SELECT status FROM bookings WHERE id=%s", made["id"])
    assert still == [{"status": "confirmed"}]


# --- privacy ----------------------------------------------------------------------------------


async def test_a_private_meeting_shows_busy_to_everyone_but_attendees(
    sessions: SessionWorld, team: Team
) -> None:
    w = sessions
    second_admin, _ = await w.member(role="tenant_admin")
    attendee = await make_person(team, "Staff Attendee", user_id=team.staff_info["user"]["id"])
    hidden = await make_person(team, "Quiet Colleague")
    room = await make_resource(team, "Secret Room")
    made = await booked(
        team,
        team.admin,
        meeting(
            hidden,
            attendee,
            title="Redundancy talks",
            private=True,
            resources=[room["id"]],
            guests=[{"name": "Adviser", "email": "adviser@example.test"}],
        ),
    )
    await w.q(
        "UPDATE bookings SET meeting_link = 'https://meet.example.test/secret-room' WHERE id=%s",
        made["id"],
    )
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    secrets = (
        "Redundancy",
        "Quiet Colleague",
        "Staff Attendee",
        "Secret Room",
        "adviser@",
        "secret-room",
        "Weekly",
    )
    platform = lambda: w.admin.get(  # noqa: E731
        f"{BASE}/calendar{window}&client_id={w.tenants[0]}"
    )
    for read in (
        team.reader.get(f"{BASE}/calendar{window}"),
        second_admin.get(f"{BASE}/calendar{window}"),
        platform(),
    ):
        shown = await read
        assert shown.status_code == 200, shown.text
        assert shown.json()["busy"] == []
        assert [b["id"] for b in shown.json()["items"]] == [made["id"]]
        item = shown.json()["items"][0]
        assert item["title"] == "Busy" and item["redacted"] is True and item["private"] is True
        assert item["people"] == [] and item["guests"] == [] and item["resources"] == []
        assert item["meeting_link"] is None and item["customer"] is None and item["type_id"] is None
        assert item["address"] is None and item["location_kind"] is None
        assert item["starts_at"].startswith(f"{TUESDAY}T10:00")
        for secret in secrets:
            assert secret not in shown.text
    # The creator and an attendee see everything.
    for who in (team.admin, team.staff):
        full = (await who.get(f"{BASE}/calendar{window}")).json()["items"][0]
        assert full["title"] == "Redundancy talks" and full["redacted"] is False
        assert len(full["people"]) == 2 and full["meeting_link"].endswith("secret-room")
    # An Admin who is not part of it can still move and cancel it, and the answer stays redacted.
    moved = await team.send(
        w.admin,
        f"/bookings/{made['id']}?client_id={w.tenants[0]}",
        {"version": 1, "starts_at": at(14)},
        "PATCH",
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["title"] == "Busy" and moved.json()["redacted"] is True
    for secret in secrets:
        assert secret not in moved.text
    moved_again = await w.mutate(
        second_admin,
        f"{BASE}/bookings/{made['id']}",
        {"version": 2, "starts_at": at(15)},
        method="PATCH",
    )
    assert moved_again.status_code == 200 and moved_again.json()["redacted"] is True
    # Refusals do not name the meeting or its people.
    other = await booked(team, team.admin, meeting(hidden, start=at(11)))
    clash = await w.mutate(
        second_admin,
        f"{BASE}/bookings/{other['id']}",
        {"version": 1, "starts_at": at(15)},
        method="PATCH",
    )
    assert clash.status_code == 409
    for secret in secrets:
        assert secret not in clash.text
    cancelled = await w.mutate(second_admin, f"{BASE}/bookings/{made['id']}/cancel", {"version": 3})
    assert cancelled.status_code == 200
    assert cancelled.json()["title"] == "Busy" and cancelled.json()["status"] == "cancelled"
    for secret in secrets:
        assert secret not in cancelled.text
    # Staff who are not part of it can do neither.
    mine = await booked(team, team.admin, meeting(hidden, start=at(16), private=True))
    assert (await team.send(team.staff, f"/bookings/{mine['id']}/cancel")).status_code == 403


async def test_read_only_sees_no_customer_details(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    kind = await make_type(team)
    made = await booked(
        team,
        team.admin,
        {
            "type_id": kind["id"],
            "starts_at": TEN,
            "people": [person["id"]],
            "walk_in": {"name": "Walk In", "phone": "0123456789", "email": "walk.in@example.test"},
            "guests": [{"name": "Friend", "email": "friend@example.test"}],
        },
    )
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    reader = await team.reader.get(f"{BASE}/calendar{window}")
    assert reader.status_code == 200
    item = reader.json()["items"][0]
    assert item["id"] == made["id"] and item["customer"] is None and item["guests"] == []
    for secret in ("Walk In", "0123456789", "walk.in@", "friend@", "Friend"):
        assert secret not in reader.text
    assert item["title"] == "Haircut" and item["people"][0]["id"] == person["id"]
    for who in (team.admin, team.staff):
        full = (await who.get(f"{BASE}/calendar{window}")).json()["items"][0]
        assert full["customer"]["full_name"] == "Walk In" and len(full["guests"]) == 1
    # Customer search follows the Inbox: staff and Admins only.
    assert (await team.reader.get(f"{BASE}/customers")).status_code == 403
    assert (await team.staff.get(f"{BASE}/customers")).status_code == 200
    chosen = await sessions.admin.get(f"{BASE}/customers?client_id={sessions.tenants[0]}")
    assert chosen.status_code == 200 and len(chosen.json()) == 1


async def test_a_filtered_calendar_gives_a_private_meeting_no_id(
    sessions: SessionWorld, team: Team
) -> None:
    """Filtering by person or room must not let anyone rebuild who is in a private meeting."""
    w = sessions
    second_admin, _ = await w.member(role="tenant_admin")
    aina, bakar = await make_person(team, "Aina"), await make_person(team, "Bakar")
    room = await make_resource(team, "Board Room")
    private = await booked(
        team,
        team.admin,
        meeting(aina, bakar, resources=[room["id"]], private=True, title="Redundancy talks"),
    )
    ordinary = await booked(team, team.admin, meeting(aina, start=at(14), title="Open sync"))
    window = f"?from={TUESDAY}T00:00:00Z&to=2031-03-05T00:00:00Z"
    ids: list[str] = []
    for query, expected in (
        (f"&person={aina['id']}", [ordinary["id"]]),
        (f"&person={bakar['id']}", []),
        (f"&resource={room['id']}", []),
    ):
        for viewer in (second_admin, team.reader, team.staff):
            shown = await viewer.get(f"{BASE}/calendar{window}{query}")
            assert shown.status_code == 200, shown.text
            body = shown.json()
            assert [b["id"] for b in body["items"]] == expected
            assert private["id"] not in shown.text
            assert len(body["busy"]) == 1
            assert set(body["busy"][0]) == {"starts_at", "ends_at", "label"}
            assert body["busy"][0]["label"] == "Busy"
            assert body["busy"][0]["starts_at"].startswith(f"{TUESDAY}T10:00")
            ids += [b["id"] for b in body["items"]]
            for secret in ("Redundancy", "Aina", "Bakar", "Board Room"):
                assert secret not in json.dumps(body["busy"])
    assert private["id"] not in ids
    # The platform admin who chose the company reads the same way.
    chosen = await w.admin.get(
        f"{BASE}/calendar{window}&client_id={w.tenants[0]}&person={bakar['id']}"
    )
    assert chosen.json()["items"] == [] and len(chosen.json()["busy"]) == 1
    # The creator is part of the meeting, so for them it is an item and not a busy time.
    mine = (await team.admin.get(f"{BASE}/calendar{window}&person={bakar['id']}")).json()
    assert [b["id"] for b in mine["items"]] == [private["id"]] and mine["busy"] == []
    # An unfiltered read keeps the id, so an Admin can still find it to move or cancel.
    plain = (await second_admin.get(f"{BASE}/calendar{window}")).json()
    assert private["id"] in [b["id"] for b in plain["items"]] and plain["busy"] == []


# --- availability ----------------------------------------------------------------------------


async def test_availability_by_type_and_by_people(sessions: SessionWorld, team: Team) -> None:
    a, b = await make_person(team, "Aina"), await make_person(team, "Bakar")
    kind = await make_type(
        team, delivery="any", person_ids=[a["id"], b["id"]], buffer_minutes=30, max_advance_days=60
    )
    day = soon_tuesday()
    await booked(team, team.admin, meeting(a, start=at(9, day=day), minutes=60))
    window = f"from={day}T09:00:00Z&to={day}T11:00:00Z"
    slots = (await team.reader.get(f"{BASE}/availability?type_id={kind['id']}&{window}")).json()
    assert all(s["ends_at"] > s["starts_at"] for s in slots)
    by_start = {s["starts_at"][11:16]: s["people"] for s in slots}
    # Aina is held from 09:00 to 10:00, so only Bakar is offered at 09:00; both are at 10:00.
    assert by_start["09:00"] == [b["id"]]
    assert sorted(by_start["10:00"]) == sorted([a["id"], b["id"]])
    asked = await team.reader.get(
        f"{BASE}/availability?people={a['id']}&minutes=60&from={day}T09:00:00Z&to={day}T13:00:00Z"
    )
    starts = [s["starts_at"][11:16] for s in asked.json()]
    assert starts[0] == "10:00" and "09:00" not in starts
    far = f"{TUESDAY}T09:00:00Z"
    for bad in (
        f"type_id={kind['id']}&from={day}T09:00:00Z&to=2031-05-01T00:00:00Z",
        f"type_id={kind['id']}&from={day}T09:00:00Z&to={day}T08:00:00Z",
        f"from={far}&to={TUESDAY}T10:00:00Z",
        f"people={uuid.uuid4()}&minutes=30&from={far}&to={TUESDAY}T10:00:00Z",
    ):
        assert (await team.reader.get(f"{BASE}/availability?{bad}")).status_code == 422, bad


async def test_availability_leaves_out_a_person_on_leave_and_keeps_the_notice(
    sessions: SessionWorld, team: Team
) -> None:
    day = soon_tuesday()
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    person = await person_on_leave(team, day, day)
    kind = await make_type(team, delivery="named", person_ids=[person["id"]], max_advance_days=60)
    window = f"from={day}T00:00:00Z&to={next_day}T23:59:00Z"
    got = await team.staff.get(
        f"{BASE}/availability?type_id={kind['id']}&people={person['id']}&{window}"
    )
    assert {s["starts_at"][:10] for s in got.json()} == {next_day}  # the leave day is never offered
    no_leave_words(got)
    soon = await make_type(team, name="Far", max_advance_days=0)
    none = await team.staff.get(f"{BASE}/availability?type_id={soon['id']}&{window}")
    assert none.json() == []


async def test_an_any_room_type_with_every_room_switched_off_books_nothing(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team, "Aina")
    room = await make_resource(team, "Room A")
    kind = await make_type(
        team,
        delivery="any",
        person_ids=[person["id"]],
        resource_rule="any",
        resource_ids=[room["id"]],
        max_advance_days=60,
    )
    off = await team.send(
        team.admin, f"/resources/{room['id']}", {"version": 1, "is_active": False}, "PATCH"
    )
    assert off.status_code == 200, off.text
    refused = await book(team, team.staff, {"type_id": kind["id"], "starts_at": TEN})
    assert refused.status_code == 422 and refused.json()["detail"] == "no_room_available"
    assert await sessions.q("SELECT count(*) AS n FROM bookings") == [{"n": 0}]
    soon = soon_tuesday()
    slots = await team.staff.get(
        f"{BASE}/availability?type_id={kind['id']}&from={soon}T09:00:00Z&to={soon}T17:00:00Z"
    )
    assert slots.status_code == 200 and slots.json() == []
    # Naming a live room by hand is still a booking.
    other = await make_resource(team, "Room B")
    named = await book(
        team, team.staff, {"type_id": kind["id"], "starts_at": TEN, "resources": [other["id"]]}
    )
    assert named.status_code == 201, named.text


async def test_availability_never_offers_a_time_that_has_passed(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team, "Aina")
    now = datetime.now(UTC)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    window = (
        f"from={(now - timedelta(days=3)).strftime(stamp)}"
        f"&to={(now + timedelta(days=1)).strftime(stamp)}"
    )
    got = await team.staff.get(
        f"{BASE}/availability?people={person['id']}&minutes=30&{window}&limit=500"
    )
    assert got.status_code == 200, got.text
    assert all(datetime.fromisoformat(s["starts_at"]) >= now for s in got.json())
    kind = await make_type(
        team, delivery="named", person_ids=[person["id"]], min_notice_minutes=0, max_advance_days=60
    )
    got = await team.staff.get(
        f"{BASE}/availability?type_id={kind['id']}&people={person['id']}&{window}&limit=500"
    )
    assert got.status_code == 200, got.text
    assert all(datetime.fromisoformat(s["starts_at"]) >= now for s in got.json())
    # A booking in the past is still allowed for staff to write.
    past = await book(team, team.staff, meeting(person, start="2020-01-07T10:00:00Z"))
    assert past.status_code == 201, past.text


# --- audit ------------------------------------------------------------------------------------


async def test_every_booking_change_writes_an_audit_row(sessions: SessionWorld, team: Team) -> None:
    w = sessions
    person = await make_person(team)
    kind = await make_type(team, needs_confirmation=True)
    walk_in = {"name": "Walk In", "phone": "0123456789", "email": "walk.in@example.test"}
    asked = {"type_id": kind["id"], "people": [person["id"]], "walk_in": walk_in}
    one = await booked(team, team.staff, {**asked, "starts_at": TEN})
    two = await booked(team, team.staff, {**asked, "starts_at": at(11)})
    three = await booked(team, team.staff, meeting(person, start="2020-01-07T10:00:00Z"))
    await team.send(team.staff, f"/bookings/{one['id']}/confirm")
    await team.send(
        team.staff, f"/bookings/{one['id']}", {"version": 2, "starts_at": at(13)}, "PATCH"
    )
    await team.send(team.staff, f"/bookings/{one['id']}/cancel")
    await team.send(team.staff, f"/bookings/{two['id']}/decline")
    await team.send(team.staff, f"/bookings/{three['id']}/no-show")
    # Refused things leave no row.
    await team.send(team.staff, f"/bookings/{one['id']}/cancel")
    await book(team, team.staff, meeting(person, start=at(20)))
    await team.send(team.reader, f"/bookings/{two['id']}/cancel")

    rows = await w.q(
        "SELECT action, target_id, details FROM console_audit"
        " WHERE client_id=%s AND action LIKE 'bookings.booking.%%' ORDER BY created_at, id",
        w.tenants[0],
    )
    assert [r["action"][len("bookings.booking.") :] for r in rows] == [
        "created", "created", "created", "confirmed", "moved", "cancelled", "declined", "no_show",
    ]  # fmt: skip
    assert [r["target_id"] for r in rows[:3]] == [one["id"], two["id"], three["id"]]
    for row in rows:
        assert row["details"] == {}
    dump = json.dumps(rows, default=str)
    for secret in ("Walk In", "0123456789", "walk.in@", "Weekly sync"):
        assert secret not in dump
    assert (
        await w.q(
            "SELECT count(*) AS n FROM console_audit"
            " WHERE client_id=%s AND action LIKE 'bookings.booking.%%'",
            w.tenants[1],
        )
    )[0]["n"] == 0


# --- fix round 1 ------------------------------------------------------------------------------


async def test_availability_stops_at_the_limit_and_keeps_the_event_loop_free(
    sessions: SessionWorld, team: Team, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = await make_person(team)
    seen: list[Any] = []
    real = service.free_slots

    def slow(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("limit"))
        time.sleep(0.8)  # a long scan: it must not hold up everything else the gateway serves
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "free_slots", slow)
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = loop.time()
            gaps.append(now - last)
            last = now

    watching = asyncio.create_task(ticker())
    asked = await team.reader.get(
        f"{BASE}/availability?people={person['id']}&minutes=30&limit=3"
        f"&from={TUESDAY}T00:00:00Z&to=2031-03-06T00:00:00Z"
    )
    stop.set()
    await watching
    assert asked.status_code == 200, asked.text
    assert len(asked.json()) == 3 and seen == [3]
    assert max(gaps) < 0.4, max(gaps)


async def test_an_any_type_with_nobody_active_books_nobody(
    sessions: SessionWorld, team: Team
) -> None:
    only = await make_person(team, "Aina")
    room = await make_resource(team)
    plain = await make_type(team, name="Plain", delivery="any", person_ids=[only["id"]])
    roomed = await make_type(
        team,
        name="Roomed",
        delivery="any",
        person_ids=[only["id"]],
        resource_rule="any",
        resource_ids=[room["id"]],
    )
    # A booking made while Aina was active can lose her hold; moving it must not book nobody.
    held = await booked(team, team.staff, {"type_id": roomed["id"], "starts_at": at(9)})
    await sessions.q(
        "DELETE FROM booking_holds WHERE booking_id=%s AND subject_kind='person'", held["id"]
    )
    off = await team.send(
        team.admin, f"/people/{only['id']}", {"version": 1, "is_active": False}, "PATCH"
    )
    assert off.status_code == 200
    window = f"from={TUESDAY}T09:00:00Z&to={TUESDAY}T17:00:00Z"
    for kind in (plain, roomed):
        refused = await book(team, team.staff, {"type_id": kind["id"], "starts_at": at(13)})
        assert refused.status_code == 422, (kind["name"], refused.text)
        assert refused.json()["detail"] == "nobody_to_book"
        slots = await team.staff.get(f"{BASE}/availability?type_id={kind['id']}&{window}")
        assert slots.status_code == 200 and slots.json() == [], kind["name"]
    moved = await team.send(
        team.staff, f"/bookings/{held['id']}", {"version": 1, "starts_at": at(15)}, "PATCH"
    )
    assert moved.status_code == 422 and moved.json()["detail"] == "nobody_to_book"
    unchanged = await sessions.q(
        "SELECT starts_at::text AS s FROM bookings WHERE id=%s", held["id"]
    )
    assert unchanged[0]["s"].startswith(f"{TUESDAY} 09:00")
    # Asking for times with no one and nothing named is a mistake, not an empty calendar.
    nobody = await team.staff.get(f"{BASE}/availability?minutes=30&{window}")
    assert nobody.status_code == 422, nobody.text
    assert nobody.json()["detail"] == "people_or_resources_required"


async def test_extreme_and_naive_times_are_refused_not_crashed(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    made = await booked(team, team.staff, meeting(person))
    for bad in (
        "9999-12-31T23:59:00Z",
        "0001-01-01T00:00:00+08:00",
        "1999-12-31T23:59:00Z",
        "2101-01-01T00:00:00Z",
        "2031-03-04T10:00:00",
    ):
        posted = await book(team, team.staff, meeting(person, start=bad))
        assert posted.status_code == 422, (bad, posted.text)
        moved = await team.send(
            team.staff, f"/bookings/{made['id']}", {"version": 1, "starts_at": bad}, "PATCH"
        )
        assert moved.status_code == 422, (bad, moved.text)
        for path, extra in (
            ("/availability", {"people": person["id"], "minutes": 30}),
            ("/calendar", {}),
        ):
            for window in (
                {"from": bad, "to": f"{TUESDAY}T12:00:00Z"},
                {"from": f"{TUESDAY}T09:00:00Z", "to": bad},
            ):
                read = await team.staff.get(f"{BASE}{path}", params={**extra, **window})
                assert read.status_code == 422, (path, bad, read.text)
    wide = await team.staff.get(
        f"{BASE}/calendar", params={"from": f"{TUESDAY}T00:00:00Z", "to": "2031-06-04T00:00:00Z"}
    )
    assert wide.status_code == 422 and wide.json()["detail"] == "window_too_large"
    rows = await sessions.q(
        "SELECT count(*) AS n FROM bookings WHERE client_id=%s", sessions.tenants[0]
    )
    assert rows[0]["n"] == 1


async def test_a_meeting_can_carry_a_place_or_an_online_link(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    kind = await make_type(team)
    online = await booked(
        team,
        team.staff,
        meeting(person, location_kind="online", meeting_link="https://meet.example.test/room-1"),
    )
    assert online["location_kind"] == "online"
    assert online["meeting_link"] == "https://meet.example.test/room-1"
    here = await booked(
        team,
        team.staff,
        meeting(person, start=at(12), location_kind="in_person", address="Level 2, Room 3"),
    )
    assert here["location_kind"] == "in_person" and here["address"] == "Level 2, Room 3"
    saved = await sessions.q(
        "SELECT location_kind, address, meeting_link FROM bookings WHERE id=%s", online["id"]
    )
    assert saved == [
        {
            "location_kind": "online",
            "address": None,
            "meeting_link": "https://meet.example.test/room-1",
        }
    ]
    for extra in (
        {"meeting_link": "http://meet.example.test/room-1"},
        {"meeting_link": "https://meet.example.test/a\nb"},
        {"meeting_link": "javascript:alert(1)"},
        {"address": "Level 2\nRoom 3"},
        {"location_kind": "teleport"},
    ):
        refused = await book(team, team.staff, {**meeting(person, start=at(14)), **extra})
        assert refused.status_code == 422, (extra, refused.text)
    # With a type, the type decides the place.
    for extra in (
        {"location_kind": "online"},
        {"address": "Somewhere else"},
        {"meeting_link": "https://meet.example.test/other"},
    ):
        refused = await book(
            team,
            team.staff,
            {"type_id": kind["id"], "starts_at": at(15), "people": [person["id"]], **extra},
        )
        assert refused.status_code == 422, (extra, refused.text)
    rows = await sessions.q(
        "SELECT count(*) AS n FROM bookings WHERE client_id=%s", sessions.tenants[0]
    )
    assert rows[0]["n"] == 2
