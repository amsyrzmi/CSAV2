"""Booking, moving and cancelling, and the calendar read.

Every write runs inside the caller's tenant transaction and goes in this order: lock the people and
rooms involved, load the calendar, ask `findings_for` and `decide`, then write the booking and its
holds. The database has the last word on overlap: the exclusion constraint on `booking_holds`
refuses a second booking of the same person or room, and that (or a deadlock between two requests
for the same slot) is answered as 409 `slot_taken`.

Every read goes through `read_records` and `to_out`. `to_out` is the only place that decides what a
viewer may see, so a new read cannot forget to hide a private meeting or a customer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from psycopg import AsyncConnection, errors

from csa.bookings import setup
from csa.bookings.availability import (
    DEFAULT_COMPANY_HOURS,
    Busy,
    Calendar,
    Hours,
    free_slots,
    on_leave,
    pick_person,
    touched_dates,
)
from csa.bookings.contracts import (
    BookingActionIn,
    BookingBusy,
    BookingCalendarOut,
    BookingCustomerOut,
    BookingFinding,
    BookingGuestOut,
    BookingIn,
    BookingMoveIn,
    BookingOut,
    BookingPersonRef,
    BookingResourceRef,
    BookingSlotOut,
    BookingTypeOut,
)
from csa.bookings.rules import Actor, Finding, decide, findings_for
from csa.console.contracts import ConsoleActor, Role
from csa.console.kb_uploads import audit
from csa.core import db

Conn = AsyncConnection[Any]

SLOT_TAKEN = "That time was just taken."
NOT_AVAILABLE = "Not available that day."
SENTENCES: dict[str, str] = {
    "outside_hours": "Outside working hours.",
    "closed_day": "This is a closed day.",
    "on_leave": NOT_AVAILABLE,  # never says why
    "short_notice": "Shorter notice than this booking type allows.",
}
BUSY = "Busy"
# `rules.py` calls a person's leave `on_leave`. A client only ever hears "unavailable": nothing it
# sees may say why someone is away.
TO_WIRE: dict[str, str] = {"on_leave": "unavailable"}
FROM_WIRE: dict[str, str] = {wire: inside for inside, wire in TO_WIRE.items()}


def on_the_wire(findings: Iterable[str]) -> list[str]:
    return sorted(TO_WIRE.get(f, f) for f in findings)


class Refusal(HTTPException):
    """A 409 that carries more than a code: a sentence to show and, sometimes, the findings.

    The route class in `api.py` sends `body`; anywhere else it is a plain `HTTPException` whose
    detail is the code, as in the setup routes.
    """

    def __init__(self, status: int, code: str, message: str | None = None, **extra: object) -> None:
        super().__init__(status, code)
        self.body: dict[str, object] = {
            "detail": code,
            **({"message": message} if message else {}),
            **extra,
        }


def slot_taken() -> Refusal:
    return Refusal(409, "slot_taken", SLOT_TAKEN)


def stale() -> Refusal:
    return Refusal(409, "stale", "This booking was changed by someone else.")


# --- who is acting, and who is looking -------------------------------------------------------


def acting_as(actor: ConsoleActor) -> Actor:
    """The owner's table has three actors. Admin and platform admin are "admin"; staff are "staff".
    The bot is not reachable from the console. Read-only members change nothing.
    """
    if actor.platform_admin or actor.role in (Role.PLATFORM_ADMIN, Role.TENANT_ADMIN):
        return "admin"
    if actor.role == Role.TENANT_STAFF:
        return "staff"
    raise HTTPException(403, "bookings changes require staff or an administrator")


@dataclass(frozen=True)
class Viewer:
    """Who is reading: their account, and whether they may see customer details."""

    user_id: UUID
    read_only: bool
    admin: bool = False


def viewer_of(actor: ConsoleActor) -> Viewer:
    read_only = not actor.platform_admin and actor.role == Role.READ_ONLY
    admin = actor.platform_admin or actor.role in (Role.PLATFORM_ADMIN, Role.TENANT_ADMIN)
    return Viewer(actor.user_id, read_only=read_only, admin=admin)


# --- the booking as the server knows it, and as one viewer may see it -------------------------


@dataclass(frozen=True)
class Attendee:
    """A bookable person on a booking, with the account and address mail will need."""

    person_id: UUID
    display_name: str
    is_active: bool
    user_id: UUID | None
    email: str | None
    employee_id: UUID | None


@dataclass(frozen=True)
class BookingRecord:
    """Everything about a booking, unredacted. Only `to_out` turns it into something to show."""

    id: UUID
    client_id: UUID
    status: str
    kind: str
    title: str | None
    private: bool
    starts_at: datetime
    ends_at: datetime
    version: int
    type_id: UUID | None
    created_by: UUID | None
    location_kind: str | None
    address: str | None
    meeting_link: str | None
    customer: BookingCustomerOut | None
    people: tuple[Attendee, ...]
    resources: tuple[BookingResourceRef, ...]
    guests: tuple[BookingGuestOut, ...]
    conflict: bool
    # The status before the change this record came back from, so mail can tell a booking that was
    # never confirmed from one that was. Null on a plain read.
    previous_status: str | None = None


def involved(record: BookingRecord, user_id: UUID) -> bool:
    """The creator, or someone whose account is linked to a person on the booking."""
    return record.created_by == user_id or any(p.user_id == user_id for p in record.people)


def hidden_from(record: BookingRecord, viewer: Viewer) -> bool:
    """A private meeting the viewer neither made nor attends."""
    return record.private and not involved(record, viewer.user_id)


def may_change(record: BookingRecord, viewer: Viewer) -> bool:
    """The rule `_for_change` enforces: an Admin changes any booking (a private meeting they are not
    part of included), staff change what they created or attend, read-only members change nothing.
    """
    if viewer.admin:
        return True
    return not viewer.read_only and involved(record, viewer.user_id)


def to_out(record: BookingRecord, viewer: Viewer) -> BookingOut:
    """The one function every read goes through.

    A private meeting is shown as "Busy" with only its times, status and version to everyone but the
    creator and the attendees, including Admins and platform admins. A read-only member never gets a
    customer or an outside guest.
    """
    if hidden_from(record, viewer):
        return BookingOut(
            id=record.id,
            status=record.status,  # type: ignore[arg-type]
            kind="meeting",
            title=BUSY,
            private=True,
            redacted=True,
            starts_at=record.starts_at,
            ends_at=record.ends_at,
            version=record.version,
            type_id=None,
            customer=None,
            people=[],
            resources=[],
            guests=[],
            location_kind=None,
            address=None,
            meeting_link=None,
            conflict=None,
            can_change=may_change(record, viewer),
        )
    return BookingOut(
        id=record.id,
        status=record.status,  # type: ignore[arg-type]
        kind=record.kind,  # type: ignore[arg-type]
        title=record.title,
        private=record.private,
        redacted=False,
        starts_at=record.starts_at,
        ends_at=record.ends_at,
        version=record.version,
        type_id=record.type_id,
        customer=None if viewer.read_only else record.customer,
        people=[
            BookingPersonRef(id=p.person_id, display_name=p.display_name, is_active=p.is_active)
            for p in record.people
        ],
        resources=list(record.resources),
        guests=[] if viewer.read_only else list(record.guests),
        location_kind=record.location_kind,  # type: ignore[arg-type]
        address=record.address,
        meeting_link=record.meeting_link,
        conflict="unavailable" if record.conflict else None,
        can_change=may_change(record, viewer),
    )


def calendar_out(
    records: Iterable[BookingRecord], viewer: Viewer, *, filtered: bool
) -> BookingCalendarOut:
    """The calendar as this viewer may see it.

    A private meeting the viewer is not part of is a redacted item, with its id so an Admin can
    move or cancel it. On a read filtered by person or room it is a bare busy time instead, with no
    id: with one, asking about each person in turn would list everyone who is in it.
    """
    items: list[BookingOut] = []
    busy: list[BookingBusy] = []
    for record in records:
        if filtered and hidden_from(record, viewer):
            busy.append(BookingBusy(starts_at=record.starts_at, ends_at=record.ends_at, label=BUSY))
        else:
            items.append(to_out(record, viewer))
    busy.sort(key=lambda b: (b.starts_at, b.ends_at))
    return BookingCalendarOut(items=items, busy=busy)


# --- loading ---------------------------------------------------------------------------------


async def company_zone(conn: Conn, client_id: UUID) -> ZoneInfo:
    row = await db.q.fetch_one("bookings/timezone_get.sql", {"client_id": client_id}, conn=conn)
    if row is None:
        raise RuntimeError(f"company {client_id} has no clients row")
    return ZoneInfo(row["timezone"])


def default_hours() -> dict[int, list[tuple[time, time]]]:
    """A fresh copy of the default week, so the shared default is never changed."""
    return {day: list(spans) for day, spans in DEFAULT_COMPANY_HOURS.items()}


async def _leave_by_person(
    conn: Conn,
    client_id: UUID,
    people: Iterable[tuple[UUID, UUID | None, UUID | None]],
    days: list[date],
) -> dict[UUID, list[tuple[date, date]]]:
    """Approved leave over `days`, dates only, keyed by booking person.

    `people` holds (person, account, stored HR person). Leave belongs to an HR employee. A person
    with an account is looked up live through the account's HR link, so a link made after the
    person was added still counts; the stored `employee_id` counts as well. A person with neither
    has no leave, and a company without the HR package has no link rows.
    """
    listed = list(people)
    linked: dict[UUID, UUID] = {}
    if any(user_id is not None for _, user_id, _ in listed):
        for member in await setup.team_members(conn, client_id):
            if member["employee_id"] is not None:
                linked[member["user_id"]] = member["employee_id"]
    by_employee: dict[UUID, set[UUID]] = {}
    for person_id, user_id, stored in listed:
        for employee_id in (stored, linked.get(user_id) if user_id is not None else None):
            if employee_id is not None:
                by_employee.setdefault(employee_id, set()).add(person_id)
    if not by_employee:
        return {}
    rows = await db.q.fetch_all(
        "bookings/leave_days.sql",
        {"client_id": client_id, "employees": list(by_employee), "from": days[0], "to": days[-1]},
        conn=conn,
    )
    leave: dict[UUID, list[tuple[date, date]]] = {}
    for row in rows:
        for person_id in by_employee[row["employee_id"]]:
            leave.setdefault(person_id, []).append((row["starts_on"], row["ends_on"]))
    return leave


async def load_calendar(
    conn: Conn,
    client_id: UUID,
    start: datetime,
    end: datetime,
    *,
    except_booking: UUID | None = None,
) -> Calendar:
    """Hours, closed days, leave and holds for [start, end). The holds of `except_booking` are left
    out, so a booking being moved does not collide with itself.
    """
    zone = await company_zone(conn, client_id)
    days = touched_dates(zone, start, end)
    people = await setup.list_people(conn, client_id)
    company: dict[int, list[tuple[time, time]]] = {}
    overrides: dict[UUID, dict[int, list[tuple[time, time]]]] = {}
    for row in await db.q.fetch_all("bookings/hours_all.sql", {"client_id": client_id}, conn=conn):
        if row["subject_kind"] == "company":
            week = company
        else:
            week = overrides.setdefault(row["subject_id"], {})
        week.setdefault(row["weekday"], []).append((row["opens"], row["closes"]))
    closed = await db.q.fetch_all(
        "bookings/closed_days_list.sql",
        {"client_id": client_id, "from": days[0], "to": days[-1]},
        conn=conn,
    )
    busy = await db.q.fetch_all(
        "bookings/holds_busy.sql",
        {"client_id": client_id, "from": start, "to": end, "except": except_booking},
        conn=conn,
    )
    hours: Hours = company or default_hours()
    leave = await _leave_by_person(
        conn, client_id, [(p.id, p.user_id, p.employee_id) for p in people], days
    )
    return Calendar(
        timezone=zone,
        company_hours=hours,
        overrides=overrides,
        closed_days=frozenset(row["day"] for row in closed),
        leave=leave,
        busy=[Busy(r["subject_kind"], r["subject_id"], r["start"], r["end"]) for r in busy],
    )


async def read_records(
    conn: Conn,
    client_id: UUID,
    *,
    booking_id: UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    person: UUID | None = None,
    resource: UUID | None = None,
) -> list[BookingRecord]:
    """One booking (any status), or the bookings in a window that are not cancelled."""
    rows = await db.q.fetch_all(
        "bookings/bookings_read.sql",
        {
            "client_id": client_id,
            "id": booking_id,
            "from": start,
            "to": end,
            "person": person,
            "resource": resource,
        },
        conn=conn,
    )
    if not rows:
        return []
    params = {"client_id": client_id, "ids": [row["id"] for row in rows]}
    attendees = await db.q.fetch_all("bookings/attendees_read.sql", params, conn=conn)
    rooms = await db.q.fetch_all("bookings/resource_holds_read.sql", params, conn=conn)

    people: dict[UUID, list[Attendee]] = {}
    guests: dict[UUID, list[BookingGuestOut]] = {}
    for a in attendees:
        if a["person_id"] is None:
            guests.setdefault(a["booking_id"], []).append(
                BookingGuestOut(name=a["guest_name"], email=a["guest_email"])
            )
        else:
            people.setdefault(a["booking_id"], []).append(
                Attendee(
                    a["person_id"],
                    a["display_name"],
                    a["is_active"],
                    a["user_id"],
                    a["email"],
                    a["employee_id"],
                )
            )
    resources: dict[UUID, list[BookingResourceRef]] = {}
    for r in rooms:
        resources.setdefault(r["booking_id"], []).append(
            BookingResourceRef(id=r["id"], name=r["name"], is_active=r["is_active"])
        )
    conflicts = await _conflicts(conn, client_id, rows, people)
    return [
        BookingRecord(
            id=row["id"],
            client_id=client_id,
            status=row["status"],
            kind=row["kind"],
            title=row["title"],
            private=row["private"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            version=row["version"],
            type_id=row["type_id"],
            created_by=row["created_by"],
            location_kind=row["location_kind"],
            address=row["address"],
            meeting_link=row["meeting_link"],
            customer=(
                BookingCustomerOut(
                    id=row["contact_id"],
                    full_name=row["customer_name"],
                    phone=row["customer_phone"],
                    email=row["customer_email"],
                )
                if row["contact_id"] is not None
                else None
            ),
            people=tuple(people.get(row["id"], ())),
            resources=tuple(resources.get(row["id"], ())),
            guests=tuple(guests.get(row["id"], ())),
            conflict=row["id"] in conflicts,
        )
        for row in rows
    ]


async def _conflicts(
    conn: Conn, client_id: UUID, rows: list[dict[str, Any]], people: dict[UUID, list[Attendee]]
) -> set[UUID]:
    """Live bookings with a person who now has approved leave over them. Computed on every read, so
    nothing is stored and no reason is ever looked at.
    """
    live = [r for r in rows if r["status"] in ("pending", "confirmed")]
    linked = [
        (a.person_id, a.user_id, a.employee_id) for r in live for a in people.get(r["id"], ())
    ]
    if not any(user or employee for _, user, employee in linked):
        return set()
    zone = await company_zone(conn, client_id)
    days = touched_dates(zone, min(r["starts_at"] for r in live), max(r["ends_at"] for r in live))
    leave = await _leave_by_person(conn, client_id, linked, days)
    calendar = Calendar(zone, {}, {}, frozenset(), leave, [])
    return {
        r["id"]
        for r in live
        if any(
            on_leave(calendar, a.person_id, r["starts_at"], r["ends_at"])
            for a in people.get(r["id"], ())
        )
    }


# --- checks shared by booking and moving -----------------------------------------------------


def window_end(start: datetime, minutes: int) -> datetime:
    """The end of a booking, refusing one that does not end after it starts.

    `findings_for` cannot handle a window that ends at or before its start, so this runs first.
    """
    end = start + timedelta(minutes=minutes)
    if end <= start:
        raise HTTPException(422, "times_invalid")
    return end


def check_window(start: datetime, end: datetime, days: int) -> None:
    """A read window that runs forward and is not longer than `days`."""
    if end <= start:
        raise HTTPException(422, "window_invalid")
    if end - start > timedelta(days=days):
        raise HTTPException(422, "window_too_large")


def _taken(
    calendar: Calendar, subjects: Iterable[tuple[str, UUID]], start: datetime, hold_end: datetime
) -> bool:
    mine = set(subjects)
    return any(
        (b.subject_kind, b.subject_id) in mine and b.start < hold_end and start < b.end
        for b in calendar.busy
    )


def _check_time(
    calendar: Calendar,
    acting: Actor,
    *,
    start: datetime,
    end: datetime,
    hold_end: datetime,
    people: Sequence[UUID],
    resources: Sequence[UUID],
    min_notice: timedelta,
    now: datetime,
    acknowledged: Iterable[BookingFinding],
) -> None:
    """Refuse a taken time, then apply the owner's table to what is left.

    Nothing here names a person, a title or a reason: leave is only ever "Not available that day."
    and the code "unavailable".
    """
    subjects = [("person", p) for p in people] + [("resource", r) for r in resources]
    if _taken(calendar, subjects, start, hold_end):
        raise slot_taken()
    found = findings_for(calendar, start, end, people, resources, min_notice, now)
    agreed: set[Finding] = {FROM_WIRE.get(f, f) for f in acknowledged}  # type: ignore[misc]
    decision = decide(acting, found, agreed)
    if decision.refused:
        raise Refusal(409, "refused", NOT_AVAILABLE, findings=on_the_wire(decision.refused))
    if decision.needs_acknowledging:
        needs = sorted(decision.needs_acknowledging)
        raise Refusal(
            409,
            "needs_acknowledging",
            " ".join(SENTENCES[f] for f in needs),
            findings=on_the_wire(needs),
        )


async def _insert_holds(
    conn: Conn,
    client_id: UUID,
    booking_id: UUID,
    subjects: Iterable[tuple[str, UUID]],
    start: datetime,
    hold_end: datetime,
) -> None:
    """One hold per person and room. The database refuses an overlap, for everyone."""
    try:
        for kind, subject_id in subjects:
            await db.q.execute(
                "bookings/hold_insert.sql",
                {
                    "client_id": client_id,
                    "booking_id": booking_id,
                    "subject_kind": kind,
                    "subject_id": subject_id,
                    "starts_at": start,
                    "ends_at": hold_end,
                },
                conn=conn,
            )
    except (errors.ExclusionViolation, errors.DeadlockDetected) as exc:
        raise slot_taken() from exc


def _known(rows: Sequence[Any]) -> dict[UUID, bool]:
    """Everyone or everything in the company, by id, with whether it is active."""
    return {row.id: row.is_active for row in rows}


def _check_subjects(kind: str, ids: Sequence[UUID], known: dict[UUID, bool]) -> None:
    """Each id belongs to this company (the holds table has no foreign key to prove it) and is
    active, because this is for a new booking or a new availability question.
    """
    for subject_id in ids:
        if subject_id not in known:
            raise HTTPException(422, f"{kind}_not_found")
        if not known[subject_id]:
            raise HTTPException(422, f"{kind}_inactive")


@dataclass(frozen=True)
class Crew:
    """Who and what a booking needs: some required, and some to be picked from a pool."""

    people: list[UUID]
    person_pool: list[UUID]
    resources: list[UUID]
    resource_pool: list[UUID]


def crew_for(
    booking_type: BookingTypeOut | None,
    people: Sequence[UUID],
    resources: Sequence[UUID],
    active_people: Sequence[UUID],
    active_resources: Sequence[UUID],
) -> Crew:
    """People and rooms asked for are required. What is not asked for follows the type: "several"
    needs all its people, "any" picks one of them (or of everyone, if the type names nobody), and a
    "specific" room is required while an "any" room is picked.
    """
    crew = Crew(list(people), [], list(resources), [])
    if booking_type is None:
        return crew
    if not crew.people:
        if booking_type.delivery == "several":
            crew.people.extend(booking_type.person_ids)
        else:
            pool = booking_type.person_ids or list(active_people)
            crew.person_pool.extend(p for p in pool if p in active_people)
    if not crew.resources:
        if booking_type.resource_rule == "specific":
            crew.resources.extend(booking_type.resource_ids)
        elif booking_type.resource_rule == "any":
            crew.resource_pool.extend(r for r in booking_type.resource_ids if r in active_resources)
    return crew


async def _pick(
    conn: Conn,
    client_id: UUID,
    calendar: Calendar,
    kind: str,
    pool: Sequence[UUID],
    *,
    start: datetime,
    end: datetime,
    hold_end: datetime,
    min_notice: timedelta,
    now: datetime,
) -> UUID:
    """The free person (or room) with the fewest problems with this time, then the fewest bookings
    that day. Someone already held for the time is never picked.
    """
    free = [c for c in pool if not _taken(calendar, [(kind, c)], start, hold_end)]
    if not free:
        raise slot_taken()

    def problems(candidate: UUID) -> int:
        who = [candidate] if kind == "person" else []
        what = [candidate] if kind == "resource" else []
        return len(findings_for(calendar, start, end, who, what, min_notice, now))

    fewest = min(problems(c) for c in free)
    best = [c for c in free if problems(c) == fewest]
    zone = calendar.timezone
    day = start.astimezone(zone).date()
    first = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    last = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    rows = await db.q.fetch_all(
        "bookings/holds_day_counts.sql",
        {"client_id": client_id, "kind": kind, "ids": best, "from": first, "to": last},
        conn=conn,
    )
    return pick_person(best, {row["subject_id"]: row["n"] for row in rows})


# --- writing ---------------------------------------------------------------------------------


async def _type_for(conn: Conn, client_id: UUID, type_id: UUID) -> BookingTypeOut:
    found = await setup.list_types(conn, client_id, type_id)
    if not found:
        raise HTTPException(422, "type_not_found")
    if not found[0].is_active:
        raise HTTPException(422, "type_inactive")
    return found[0]


async def _customer(conn: Conn, client_id: UUID, data: BookingIn) -> UUID | None:
    """The existing contact, or a new one made from the walk-in's name and phone or email."""
    if data.contact_id is not None:
        row = await db.q.fetch_one(
            "bookings/contact_get.sql", {"client_id": client_id, "id": data.contact_id}, conn=conn
        )
        if row is None:
            raise HTTPException(422, "contact_not_found")
        return data.contact_id
    if data.walk_in is None:
        return None
    row = await db.q.fetch_one(
        "bookings/contact_walk_in_insert.sql",
        {"client_id": client_id, **data.walk_in.model_dump()},
        conn=conn,
    )
    if row is None:
        raise RuntimeError("contact_walk_in_insert.sql returned no row")
    return row["id"]  # type: ignore[no-any-return]


async def create_booking(
    conn: Conn,
    client_id: UUID,
    actor: ConsoleActor,
    data: BookingIn,
    *,
    now: datetime | None = None,
) -> BookingRecord:
    """Book a type, or an internal meeting, for these people and rooms.

    The caller sends the result through `to_out`, and runs the mail step after the commit.
    """
    acting = acting_as(actor)
    now = now or datetime.now(UTC)
    booking_type = await _type_for(conn, client_id, data.type_id) if data.type_id else None
    if data.private and booking_type is not None:
        raise HTTPException(422, "private_meetings_only")
    minutes = booking_type.duration_minutes if booking_type else data.minutes
    if minutes is None:
        raise HTTPException(422, "times_invalid")
    start = data.starts_at.astimezone(UTC)
    end = window_end(start, minutes)
    hold_end = end + timedelta(minutes=booking_type.buffer_minutes if booking_type else 0)
    min_notice = timedelta(minutes=booking_type.min_notice_minutes if booking_type else 0)

    everyone = await setup.list_people(conn, client_id)
    rooms = await setup.list_resources(conn, client_id)
    given_people = list(dict.fromkeys(data.people))
    given_rooms = list(dict.fromkeys(data.resources))
    if booking_type and booking_type.delivery == "named" and not given_people:
        raise HTTPException(422, "people_required")
    crew = crew_for(
        booking_type,
        given_people,
        given_rooms,
        [p.id for p in everyone if p.is_active],
        [r.id for r in rooms if r.is_active],
    )
    if booking_type is not None and not (crew.people or crew.person_pool):
        raise HTTPException(422, "nobody_to_book")  # every type needs someone to deliver it
    _check_subjects("person", crew.people, _known(everyone))
    _check_subjects("resource", crew.resources, _known(rooms))
    if not (crew.people or crew.person_pool or crew.resources or crew.resource_pool):
        raise HTTPException(422, "nobody_to_book")

    await setup.lock_subjects(
        conn,
        client_id,
        [("person", p) for p in (*crew.people, *crew.person_pool)]
        + [("resource", r) for r in (*crew.resources, *crew.resource_pool)],
    )
    calendar = await load_calendar(conn, client_id, start, hold_end)
    if crew.person_pool:
        crew.people.append(
            await _pick(
                conn, client_id, calendar, "person", crew.person_pool,
                start=start, end=end, hold_end=hold_end, min_notice=min_notice, now=now,
            )
        )  # fmt: skip
    if crew.resource_pool:
        crew.resources.append(
            await _pick(
                conn, client_id, calendar, "resource", crew.resource_pool,
                start=start, end=end, hold_end=hold_end, min_notice=min_notice, now=now,
            )
        )  # fmt: skip
    _check_time(
        calendar,
        acting,
        start=start,
        end=end,
        hold_end=hold_end,
        people=crew.people,
        resources=crew.resources,
        min_notice=min_notice,
        now=now,
        acknowledged=data.acknowledged,
    )

    row = await db.q.fetch_one(
        "bookings/booking_insert.sql",
        {
            "client_id": client_id,
            "contact_id": await _customer(conn, client_id, data),
            "service": booking_type.name if booking_type else None,
            "status": "pending"
            if booking_type and booking_type.needs_confirmation
            else "confirmed",
            "starts_at": start,
            "ends_at": end,
            "type_id": data.type_id,
            "title": None if booking_type else data.title,
            "kind": "appointment" if booking_type else "meeting",
            "created_by": actor.user_id,
            "private": data.private,
            # The place is copied from the type, so editing the type later moves no booking. A
            # meeting with no type brings its own.
            "location_kind": booking_type.location_kind if booking_type else data.location_kind,
            "address": booking_type.address if booking_type else data.address,
            "meeting_link": booking_type.meeting_link if booking_type else data.meeting_link,
        },
        conn=conn,
    )
    if row is None:
        raise RuntimeError("booking_insert.sql returned no row")
    booking_id: UUID = row["id"]
    for person_id in crew.people:
        await db.q.execute(
            "bookings/attendee_person_insert.sql",
            {"client_id": client_id, "booking_id": booking_id, "person_id": person_id},
            conn=conn,
        )
    first_of_each: dict[str, Any] = {}
    for g in data.guests:
        first_of_each.setdefault(g.email.lower(), g)  # one address listed twice is kept once
    for guest in first_of_each.values():
        await db.q.execute(
            "bookings/attendee_guest_insert.sql",
            {"client_id": client_id, "booking_id": booking_id, **guest.model_dump()},
            conn=conn,
        )
    await _insert_holds(
        conn,
        client_id,
        booking_id,
        [("person", p) for p in crew.people] + [("resource", r) for r in crew.resources],
        start,
        hold_end,
    )
    await audit(conn, actor, client_id, "bookings.booking.created", str(booking_id))
    return (await read_records(conn, client_id, booking_id=booking_id))[0]


async def _for_change(
    conn: Conn, client_id: UUID, actor: ConsoleActor, booking_id: UUID, version: int | None
) -> BookingRecord:
    """Lock a booking and check this person may change it, at this version.

    Staff may change what they created or attend. An Admin may change any booking, a private meeting
    included. Another company's booking is simply not found.
    """
    acting = acting_as(actor)
    locked = await db.q.fetch_one(
        "bookings/booking_lock.sql", {"client_id": client_id, "id": booking_id}, conn=conn
    )
    if locked is None:
        raise HTTPException(404, "booking_not_found")
    record = (await read_records(conn, client_id, booking_id=booking_id))[0]
    if acting == "staff" and not involved(record, actor.user_id):
        raise HTTPException(403, "own_bookings_only")
    if version is not None and record.version != version:
        raise stale()
    return record


def _live(record: BookingRecord) -> None:
    if record.status not in ("pending", "confirmed"):
        raise HTTPException(409, "wrong_status")


async def move_booking(
    conn: Conn,
    client_id: UUID,
    actor: ConsoleActor,
    booking_id: UUID,
    data: BookingMoveIn,
    *,
    now: datetime | None = None,
) -> BookingRecord:
    """Move a booking to a new start. It keeps its id, length, people and rooms; the version goes up
    and the holds are replaced in the same transaction, so a refusal leaves everything as it was.
    The new time meets the same rules as a new booking.
    """
    acting = acting_as(actor)
    now = now or datetime.now(UTC)
    record = await _for_change(conn, client_id, actor, booking_id, data.version)
    _live(record)
    booking_type = None
    if record.type_id is not None:
        booking_type = next(iter(await setup.list_types(conn, client_id, record.type_id)), None)
    start = data.starts_at.astimezone(UTC)
    end = window_end(start, int((record.ends_at - record.starts_at).total_seconds() // 60))
    hold_end = end + timedelta(minutes=booking_type.buffer_minutes if booking_type else 0)
    min_notice = timedelta(minutes=booking_type.min_notice_minutes if booking_type else 0)
    held = await db.q.fetch_all(
        "bookings/holds_for_booking.sql",
        {"client_id": client_id, "booking_id": booking_id},
        conn=conn,
    )
    people = [h["subject_id"] for h in held if h["subject_kind"] == "person"]
    rooms = [h["subject_id"] for h in held if h["subject_kind"] == "resource"]
    if record.type_id is not None and not people:
        raise HTTPException(422, "nobody_to_book")  # a booking of a type needs someone
    subjects = [("person", p) for p in people] + [("resource", r) for r in rooms]
    await setup.lock_subjects(conn, client_id, subjects)
    calendar = await load_calendar(conn, client_id, start, hold_end, except_booking=booking_id)
    _check_time(
        calendar,
        acting,
        start=start,
        end=end,
        hold_end=hold_end,
        people=people,
        resources=rooms,
        min_notice=min_notice,
        now=now,
        acknowledged=data.acknowledged,
    )
    await db.q.execute(
        "bookings/holds_delete.sql", {"client_id": client_id, "booking_id": booking_id}, conn=conn
    )
    await _insert_holds(conn, client_id, booking_id, subjects, start, hold_end)
    await db.q.execute(
        "bookings/booking_times_set.sql",
        {"client_id": client_id, "id": booking_id, "starts_at": start, "ends_at": end},
        conn=conn,
    )
    await audit(conn, actor, client_id, "bookings.booking.moved", str(booking_id))
    return (await read_records(conn, client_id, booking_id=booking_id))[0]


async def _finish(
    conn: Conn,
    client_id: UUID,
    actor: ConsoleActor,
    record: BookingRecord,
    status: str,
    action: str,
    *,
    free: bool,
) -> BookingRecord:
    """Set the new status, delete the holds when the time is freed, and write the audit row."""
    if free:
        await db.q.execute(
            "bookings/holds_delete.sql",
            {"client_id": client_id, "booking_id": record.id},
            conn=conn,
        )
    await db.q.execute(
        "bookings/booking_status_set.sql",
        {"client_id": client_id, "id": record.id, "status": status},
        conn=conn,
    )
    await audit(conn, actor, client_id, f"bookings.booking.{action}", str(record.id))
    if free:
        # The rooms live on the holds, which are gone now: keep them on the returned record.
        return replace(
            record,
            status=status,
            version=record.version + 1,
            conflict=False,
            previous_status=record.status,
        )
    fresh = (await read_records(conn, client_id, booking_id=record.id))[0]
    return replace(fresh, previous_status=record.status)


async def cancel_booking(
    conn: Conn, client_id: UUID, actor: ConsoleActor, booking_id: UUID, data: BookingActionIn
) -> BookingRecord:
    """Cancel a pending or confirmed booking; its holds are deleted, so the time is free again."""
    record = await _for_change(conn, client_id, actor, booking_id, data.version)
    _live(record)
    return await _finish(conn, client_id, actor, record, "cancelled", "cancelled", free=True)


async def decline_booking(
    conn: Conn, client_id: UUID, actor: ConsoleActor, booking_id: UUID, data: BookingActionIn
) -> BookingRecord:
    """Decline a pending booking: it is cancelled and frees its holds, like a cancel."""
    record = await _for_change(conn, client_id, actor, booking_id, data.version)
    if record.status != "pending":
        raise HTTPException(409, "wrong_status")
    return await _finish(conn, client_id, actor, record, "cancelled", "declined", free=True)


async def confirm_booking(
    conn: Conn, client_id: UUID, actor: ConsoleActor, booking_id: UUID, data: BookingActionIn
) -> BookingRecord:
    """Confirm a pending booking. It already holds its time."""
    record = await _for_change(conn, client_id, actor, booking_id, data.version)
    if record.status != "pending":
        raise HTTPException(409, "wrong_status")
    return await _finish(conn, client_id, actor, record, "confirmed", "confirmed", free=False)


async def mark_no_show(
    conn: Conn,
    client_id: UUID,
    actor: ConsoleActor,
    booking_id: UUID,
    data: BookingActionIn,
    *,
    now: datetime | None = None,
) -> BookingRecord:
    """Mark a confirmed booking that has started as a no-show. The record and its holds stay."""
    record = await _for_change(conn, client_id, actor, booking_id, data.version)
    if record.status != "confirmed":
        raise HTTPException(409, "wrong_status")
    if record.starts_at > (now or datetime.now(UTC)):
        raise HTTPException(409, "not_started")
    return await _finish(conn, client_id, actor, record, "no_show", "no_show", free=False)


# --- availability ----------------------------------------------------------------------------


async def availability(
    conn: Conn,
    client_id: UUID,
    *,
    start: datetime,
    end: datetime,
    type_id: UUID | None,
    people: Sequence[UUID],
    resources: Sequence[UUID],
    minutes: int | None,
    limit: int,
    now: datetime | None = None,
) -> list[BookingSlotOut]:
    """Free times for a type (its length, buffer, notice and how far ahead), or for these people and
    rooms and `minutes`. People and rooms asked for are required; otherwise the type decides.
    """
    check_window(start, end, 31)
    now = now or datetime.now(UTC)
    booking_type = await _type_for(conn, client_id, type_id) if type_id else None
    if booking_type is None and minutes is None:
        raise HTTPException(422, "minutes_required")
    everyone = await setup.list_people(conn, client_id)
    rooms = await setup.list_resources(conn, client_id)
    crew = crew_for(
        booking_type,
        list(dict.fromkeys(people)),
        list(dict.fromkeys(resources)),
        [p.id for p in everyone if p.is_active],
        [r.id for r in rooms if r.is_active],
    )
    _check_subjects("person", crew.people, _known(everyone))
    _check_subjects("resource", crew.resources, _known(rooms))
    if booking_type is None and not (crew.people or crew.resources):
        raise HTTPException(422, "people_or_resources_required")
    if booking_type is not None and not (crew.people or crew.person_pool):
        return []  # nobody is left to deliver it
    buffer = timedelta(minutes=booking_type.buffer_minutes if booking_type else 0)
    start_from, until = start, end
    if booking_type:
        start_from = max(start, now + timedelta(minutes=booking_type.min_notice_minutes))
        until = min(end, now + timedelta(days=booking_type.max_advance_days))
    if until <= start_from:
        return []
    calendar = await load_calendar(conn, client_id, start_from, until + buffer)
    duration = timedelta(minutes=booking_type.duration_minutes if booking_type else minutes or 0)
    # The scan is plain computation and can take seconds over a month with many people. It runs in a
    # thread so the gateway keeps serving everyone else, and stops once it has `limit` slots.
    slots = await asyncio.to_thread(
        partial(
            free_slots,
            calendar,
            required_people=crew.people,
            any_of_people=crew.person_pool,
            required_resources=crew.resources,
            any_of_resources=crew.resource_pool,
            duration=duration,
            buffer=buffer,
            start_from=start_from,
            until=until,
            limit=limit,
        )
    )
    return [
        BookingSlotOut(
            starts_at=s.start, ends_at=s.end, people=list(s.people), resources=list(s.resources)
        )
        for s in slots
    ]
