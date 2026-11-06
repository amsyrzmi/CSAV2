"""Bookings setup: people, rooms, booking types, hours, closed days and blocks.

Every function runs inside the caller's tenant transaction and writes one audit row per change.
Refusals are `HTTPException`s with a short code: 404 for a missing row, 409 for a stale version,
a taken slot or a duplicate, and 422 for anything the caller sent that cannot be stored.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection, errors
from pydantic import TypeAdapter, ValidationError

from csa.bookings.availability import DEFAULT_COMPANY_HOURS
from csa.bookings.contracts import (
    BookingBlockIn,
    BookingBlockOut,
    BookingClosedDayIn,
    BookingClosedDayOut,
    BookingHoursIn,
    BookingHoursOut,
    BookingPeriod,
    BookingPersonIn,
    BookingPersonOut,
    BookingPersonPatch,
    BookingResourceIn,
    BookingResourceOut,
    BookingResourcePatch,
    BookingSettingsIn,
    BookingSettingsOut,
    BookingTypeIn,
    BookingTypeOut,
    BookingTypePatch,
    Email,
    HoursKind,
)
from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.core import db
from csa.staff.ownership import replies_off

Conn = AsyncConnection[Any]

# What "Create starter types" makes for a company that has none: for customers, delivered by
# any free person, over the phone until the company says where it meets.
STARTERS: tuple[tuple[str, int], ...] = (("Appointment", 30), ("Consultation", 60))


def refuse(status: int, code: str) -> HTTPException:
    """The refusal every setup function raises: a status and a short, stable code."""
    return HTTPException(status, code)


def check_version(current: int, given: int) -> None:
    """A change made from an older read of the row is refused, not merged."""
    if current != given:
        raise refuse(409, "version_conflict")


async def lock(conn: Conn, *parts: object) -> None:
    """Hold a transaction-long advisory lock named by `parts`."""
    key = ":".join(["booking", *map(str, parts)])
    await db.q.execute("bookings/advisory_lock.sql", {"key": key}, conn=conn)


async def lock_subjects(conn: Conn, client_id: UUID, subjects: Iterable[tuple[str, UUID]]) -> None:
    """Lock each person or room before holds are inserted for it, always in the same order.

    Two requests that insert holds for the same subjects at once could otherwise deadlock on the
    overlap rule. Sorting makes them queue, and the second then meets the first one's hold.
    """
    for kind, subject_id in sorted({(k, str(i)) for k, i in subjects}):
        await lock(conn, "hold", client_id, kind, subject_id)


# --- people ----------------------------------------------------------------------------------


def person_out(row: dict[str, Any]) -> BookingPersonOut:
    """A people row as the API shows it."""
    return BookingPersonOut.model_validate(
        {**row, "linked_to_hr": row.get("employee_id") is not None}
    )


async def list_people(conn: Conn, client_id: UUID) -> list[BookingPersonOut]:
    """Everyone who can be booked, active or not."""
    rows = await db.q.fetch_all("bookings/people_list.sql", {"client_id": client_id}, conn=conn)
    return [person_out(row) for row in rows]


async def people_for(conn: Conn, client_id: UUID, *, admin: bool) -> list[BookingPersonOut]:
    """The list as one viewer may see it. Everyone learns whether a person is linked to HR (by the
    stored id or by their account's link), but only an Admin is sent the address and the HR id.
    """
    people = await list_people(conn, client_id)
    linked: set[UUID] = set()
    if any(p.user_id is not None for p in people):
        linked = {m["user_id"] for m in await team_members(conn, client_id) if m["employee_id"]}
    shown = []
    for person in people:
        changes: dict[str, Any] = {
            "linked_to_hr": person.employee_id is not None or person.user_id in linked
        }
        if not admin:
            changes |= {"email": None, "employee_id": None}
        shown.append(person.model_copy(update=changes))
    return shown


async def team_members(conn: Conn, client_id: UUID) -> list[dict[str, Any]]:
    """The company's enabled console members, each with the HR person linked to the account."""
    return await db.q.fetch_all("bookings/team_members.sql", {"client_id": client_id}, conn=conn)


async def _write_person(conn: Conn, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        row = await db.q.fetch_one(sql, params, conn=conn)
    except errors.UniqueViolation as exc:
        raise refuse(409, "person_exists") from exc
    except errors.ForeignKeyViolation as exc:
        # The foreign keys check the company: a console member of this company, an HR person of
        # this company. The calling role cannot read either table, so the database answers.
        name = exc.diag.constraint_name or ""
        raise refuse(
            422, "employee_not_found" if "employee" in name else "member_not_found"
        ) from exc
    if row is None:
        raise refuse(404, "person_not_found")
    return row


async def _require_member(conn: Conn, client_id: UUID, user_id: UUID | None) -> None:
    if user_id is None:
        return
    if user_id not in {m["user_id"] for m in await team_members(conn, client_id)}:
        raise refuse(422, "member_not_found")


async def create_person(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingPersonIn
) -> BookingPersonOut:
    """Add a person; a linked account must be an enabled member of this company."""
    await _require_member(conn, client_id, data.user_id)
    row = await _write_person(
        conn, "bookings/person_insert.sql", {"client_id": client_id, **data.model_dump()}
    )
    await audit(conn, actor, client_id, "bookings.person.created", str(row["id"]))
    return person_out(row)


async def update_person(
    conn: Conn, client_id: UUID, actor: ConsoleActor, person_id: UUID, patch: BookingPersonPatch
) -> BookingPersonOut:
    """Change a person; only the fields sent change."""
    current = await db.q.fetch_one(
        "bookings/person_get.sql", {"client_id": client_id, "id": person_id}, conn=conn
    )
    if current is None:
        raise refuse(404, "person_not_found")
    check_version(current["version"], patch.version)
    changes = patch.changes()
    if changes.get("user_id") not in (None, current["user_id"]):
        await _require_member(conn, client_id, changes["user_id"])  # type: ignore[arg-type]
    merged = {**current, **changes, "client_id": client_id}
    row = await _write_person(conn, "bookings/person_update.sql", merged)
    await audit(conn, actor, client_id, "bookings.person.updated", str(person_id))
    return person_out(row)


_EMAIL = TypeAdapter(Email)


def _safe_email(address: object) -> str | None:
    """The address if it passes the same check as one typed in; otherwise none is stored."""
    try:
        return _EMAIL.validate_python(address)
    except ValidationError:
        return None


def _display_name(member: dict[str, Any]) -> str:
    name = " ".join(str(member["display_name"] or "").split())[:120]
    return name or str(member["email"])


async def add_team(conn: Conn, client_id: UUID, actor: ConsoleActor) -> list[BookingPersonOut]:
    """Add each enabled member not yet listed, with their HR person when they have one.

    The member list and the HR link come from one narrow database function, so a company without
    the HR package, and an Admin with no HR grant, both work.
    """
    await lock(conn, "people", client_id)
    rows = await db.q.fetch_all("bookings/people_user_ids.sql", {"client_id": client_id}, conn=conn)
    listed = {row["user_id"] for row in rows}
    added: list[BookingPersonOut] = []
    for member in await team_members(conn, client_id):
        if member["user_id"] in listed:
            continue
        row = await _write_person(
            conn,
            "bookings/person_insert.sql",
            {
                "client_id": client_id,
                "display_name": _display_name(member),
                "user_id": member["user_id"],
                "employee_id": member["employee_id"],
                "email": _safe_email(member["email"]),
            },
        )
        await audit(conn, actor, client_id, "bookings.person.created", str(row["id"]))
        added.append(person_out(row))
    return added


# --- rooms and shared things -----------------------------------------------------------------


async def list_resources(conn: Conn, client_id: UUID) -> list[BookingResourceOut]:
    """Every room or shared thing, active or not."""
    rows = await db.q.fetch_all("bookings/resources_list.sql", {"client_id": client_id}, conn=conn)
    return [BookingResourceOut.model_validate(row) for row in rows]


async def create_resource(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingResourceIn
) -> BookingResourceOut:
    """Add a room or shared thing."""
    row = await db.q.fetch_one(
        "bookings/resource_insert.sql", {"client_id": client_id, **data.model_dump()}, conn=conn
    )
    if row is None:
        raise RuntimeError("resource_insert.sql returned no row")
    await audit(conn, actor, client_id, "bookings.resource.created", str(row["id"]))
    return BookingResourceOut.model_validate(row)


async def update_resource(
    conn: Conn, client_id: UUID, actor: ConsoleActor, resource_id: UUID, patch: BookingResourcePatch
) -> BookingResourceOut:
    """Change a room or shared thing; only the fields sent change."""
    current = await db.q.fetch_one(
        "bookings/resource_get.sql", {"client_id": client_id, "id": resource_id}, conn=conn
    )
    if current is None:
        raise refuse(404, "resource_not_found")
    check_version(current["version"], patch.version)
    row = await db.q.fetch_one(
        "bookings/resource_update.sql",
        {**current, **patch.changes(), "client_id": client_id},
        conn=conn,
    )
    if row is None:
        raise RuntimeError("resource_update.sql returned no row")
    await audit(conn, actor, client_id, "bookings.resource.updated", str(resource_id))
    return BookingResourceOut.model_validate(row)


# --- booking types ---------------------------------------------------------------------------


async def list_types(
    conn: Conn, client_id: UUID, type_id: UUID | None = None
) -> list[BookingTypeOut]:
    """Booking types, or the one type named, each with its people and rooms."""
    rows = await db.q.fetch_all(
        "bookings/types_list.sql", {"client_id": client_id, "id": type_id}, conn=conn
    )
    return [BookingTypeOut.model_validate(row) for row in rows]


def type_problem(data: BookingTypeIn) -> str | None:
    """What a type is missing for the rules it names, or None when it holds together."""
    if data.delivery in ("named", "several") and not data.person_ids:
        return "type_needs_people"
    if data.resource_rule in ("specific", "any") and not data.resource_ids:
        return "type_needs_resources"
    if data.location_kind == "in_person" and not data.address:
        return "type_needs_address"
    if data.location_kind == "online" and not data.meeting_link:
        return "type_needs_link"
    return None


def _tidy(data: BookingTypeIn) -> BookingTypeIn:
    """Keep only what the chosen rules use, so a stale address or link is never stored."""
    return data.model_copy(
        update={
            "address": data.address if data.location_kind == "in_person" else None,
            "meeting_link": data.meeting_link if data.location_kind == "online" else None,
            "person_ids": list(dict.fromkeys(data.person_ids)),
            "resource_ids": (
                list(dict.fromkeys(data.resource_ids)) if data.resource_rule != "none" else []
            ),
        }
    )


async def _check_type(conn: Conn, client_id: UUID, data: BookingTypeIn) -> BookingTypeIn:
    data = _tidy(data)
    problem = type_problem(data)
    if problem is not None:
        raise refuse(422, problem)
    for sql, ids, code in (
        ("bookings/people_exist.sql", data.person_ids, "person_not_found"),
        ("bookings/resources_exist.sql", data.resource_ids, "resource_not_found"),
    ):
        if ids:
            found = await db.q.fetch_all(sql, {"client_id": client_id, "ids": ids}, conn=conn)
            if len(found) != len(ids):
                raise refuse(422, code)
    return data


async def _store_links(conn: Conn, client_id: UUID, type_id: UUID, data: BookingTypeIn) -> None:
    for table, ids in (("people", data.person_ids), ("resources", data.resource_ids)):
        params = {"client_id": client_id, "type_id": type_id}
        await db.q.execute(f"bookings/type_{table}_delete.sql", params, conn=conn)
        if ids:
            await db.q.execute(
                f"bookings/type_{table}_insert.sql", {**params, "ids": ids}, conn=conn
            )


async def _insert_type(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingTypeIn
) -> BookingTypeOut:
    data = await _check_type(conn, client_id, data)
    row = await db.q.fetch_one(
        "bookings/type_insert.sql", {"client_id": client_id, **data.model_dump()}, conn=conn
    )
    if row is None:
        raise RuntimeError("type_insert.sql returned no row")
    await _store_links(conn, client_id, row["id"], data)
    await audit(conn, actor, client_id, "bookings.type.created", str(row["id"]))
    return (await list_types(conn, client_id, row["id"]))[0]


async def create_type(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingTypeIn
) -> BookingTypeOut:
    """Add a booking type, after checking it has the people, rooms, address or link it names."""
    return await _insert_type(conn, client_id, actor, data)


async def update_type(
    conn: Conn, client_id: UUID, actor: ConsoleActor, type_id: UUID, patch: BookingTypePatch
) -> BookingTypeOut:
    """Change a booking type; the result is checked as a whole, as on creation."""
    locked = await db.q.fetch_one(
        "bookings/type_lock.sql", {"client_id": client_id, "id": type_id}, conn=conn
    )
    if locked is None:
        raise refuse(404, "type_not_found")
    check_version(locked["version"], patch.version)
    current = (await list_types(conn, client_id, type_id))[0].model_dump()
    merged = {**current, **patch.changes()}
    is_active = merged.pop("is_active")
    try:
        data = BookingTypeIn.model_validate({k: merged[k] for k in BookingTypeIn.model_fields})
    except ValidationError as exc:
        raise refuse(422, "type_invalid") from exc
    data = await _check_type(conn, client_id, data)
    await db.q.execute(
        "bookings/type_update.sql",
        {**data.model_dump(), "client_id": client_id, "id": type_id, "is_active": is_active},
        conn=conn,
    )
    await _store_links(conn, client_id, type_id, data)
    await audit(conn, actor, client_id, "bookings.type.updated", str(type_id))
    return (await list_types(conn, client_id, type_id))[0]


async def create_starters(
    conn: Conn, client_id: UUID, actor: ConsoleActor
) -> tuple[list[BookingTypeOut], bool]:
    """Create the starter types if the company has no type at all.

    Returns the company's types and whether anything was created, so a second call does nothing.
    """
    await lock(conn, "starters", client_id)
    existing = await list_types(conn, client_id)
    if existing:
        return existing, False
    for name, minutes in STARTERS:
        await _insert_type(
            conn,
            client_id,
            actor,
            BookingTypeIn(name=name, duration_minutes=minutes, location_kind="phone"),
        )
    return await list_types(conn, client_id), True


# --- hours -----------------------------------------------------------------------------------


def default_periods() -> list[BookingPeriod]:
    """A fresh copy of the default week, so the shared default can never be changed."""
    return [
        BookingPeriod(weekday=day, opens=f"{opens:%H:%M}", closes=f"{closes:%H:%M}")
        for day, spans in sorted(DEFAULT_COMPANY_HOURS.items())
        for opens, closes in spans
    ]


async def _check_subject(
    conn: Conn, client_id: UUID, kind: HoursKind, subject_id: UUID | None
) -> None:
    """A company has no subject id; a person or room must belong to this company."""
    if kind == "company":
        if subject_id is not None:
            raise refuse(422, "subject_invalid")
        return
    if subject_id is None:
        raise refuse(422, "subject_not_found")
    row = await db.q.fetch_one(
        "bookings/subject_exists.sql",
        {"client_id": client_id, "kind": kind, "id": subject_id},
        conn=conn,
    )
    if row is None or not row["found"]:
        raise refuse(422, "subject_not_found")


async def get_hours(
    conn: Conn, client_id: UUID, kind: HoursKind, subject_id: UUID | None
) -> BookingHoursOut:
    """Stored hours for the company, a person or a room.

    A company with none stored has the default week. A person or room with none stored has an
    empty list and follows the company's hours.
    """
    await _check_subject(conn, client_id, kind, subject_id)
    rows = await db.q.fetch_all(
        "bookings/hours_list.sql",
        {"client_id": client_id, "kind": kind, "subject_id": subject_id},
        conn=conn,
    )
    periods = [BookingPeriod.model_validate(row) for row in rows]
    if not periods and kind == "company":
        periods = default_periods()
    return BookingHoursOut(
        subject_kind=kind, subject_id=subject_id, is_default=not rows, periods=periods
    )


def check_periods(periods: list[BookingPeriod]) -> None:
    """Each period opens before it closes, and none overlaps another on the same day.

    A period that runs past midnight is two days' hours, not one: it is refused here.
    """
    for period in periods:
        if period.opens >= period.closes:
            raise refuse(422, "hours_invalid")
    ordered = sorted(periods, key=lambda p: (p.weekday, p.opens))
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if earlier.weekday == later.weekday and earlier.closes > later.opens:
            raise refuse(422, "hours_overlap")


async def put_hours(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingHoursIn
) -> BookingHoursOut:
    """Replace the hours of the company, a person or a room as a whole."""
    await _check_subject(conn, client_id, data.subject_kind, data.subject_id)
    check_periods(data.periods)
    await lock(conn, "hours", client_id, data.subject_kind, data.subject_id)
    where = {"client_id": client_id, "kind": data.subject_kind, "subject_id": data.subject_id}
    await db.q.execute("bookings/hours_delete.sql", where, conn=conn)
    for period in data.periods:
        await db.q.execute(
            "bookings/hours_insert.sql",
            {
                **where,
                "weekday": period.weekday,
                "opens": time.fromisoformat(period.opens),
                "closes": time.fromisoformat(period.closes),
            },
            conn=conn,
        )
    await audit(
        conn, actor, client_id, "bookings.hours.replaced", str(data.subject_id or "company")
    )
    return await get_hours(conn, client_id, data.subject_kind, data.subject_id)


# --- closed days -----------------------------------------------------------------------------


async def list_closed_days(
    conn: Conn, client_id: UUID, start: date | None, end: date | None
) -> list[BookingClosedDayOut]:
    """Closed days in date order, optionally within a range."""
    rows = await db.q.fetch_all(
        "bookings/closed_days_list.sql",
        {"client_id": client_id, "from": start, "to": end},
        conn=conn,
    )
    return [BookingClosedDayOut.model_validate(row) for row in rows]


async def add_closed_day(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingClosedDayIn
) -> BookingClosedDayOut:
    """Close a day; closing it again only changes the note."""
    row = await db.q.fetch_one(
        "bookings/closed_day_upsert.sql", {"client_id": client_id, **data.model_dump()}, conn=conn
    )
    if row is None:
        raise RuntimeError("closed_day_upsert.sql returned no row")
    await audit(conn, actor, client_id, "bookings.closed_day.added", data.day.isoformat())
    return BookingClosedDayOut.model_validate(row)


async def remove_closed_day(conn: Conn, client_id: UUID, actor: ConsoleActor, day: date) -> None:
    """Open a closed day again."""
    row = await db.q.fetch_one(
        "bookings/closed_day_delete.sql", {"client_id": client_id, "day": day}, conn=conn
    )
    if row is None:
        raise refuse(404, "closed_day_not_found")
    await audit(conn, actor, client_id, "bookings.closed_day.removed", day.isoformat())


# --- settings --------------------------------------------------------------------------------

# What a company that has never saved its settings gets (the table's own defaults).
DEFAULT_SETTINGS = {
    "reminder_minutes": 1440,
    "pending_hours": 24,
    "customer_limit": 5,
    "version": 0,
}
# A company has a replies setting only with Customer support, which a suspended one still has.
REPLIES_PACKAGE = "customer_support"
REPLIES_STATUSES = ("active", "suspended")


async def customer_replies_off(conn: Conn, client_id: UUID) -> bool:
    """Customers get nothing automatic: Customer support is active with its mode Off (`shadow`),
    or it is suspended, whatever its mode (the bot, reminders and notices all stop then).

    A company without the package has no replies setting, so it is never "off": its email
    reminders to customers still go.
    """

    async def package_is(*statuses: str) -> bool:
        row = await db.q.fetch_one(
            "console/service_package_any_in_status.sql",
            (client_id, [REPLIES_PACKAGE], list(statuses)),
            conn=conn,
        )
        return row is not None

    if await package_is("suspended"):
        return True
    return await package_is("active") and await replies_off(conn, client_id)


async def get_settings(conn: Conn, client_id: UUID) -> BookingSettingsOut:
    """The stored settings, or the defaults with version 0, and whether replies are Off."""
    row = await db.q.fetch_one("bookings/settings_get.sql", {"client_id": client_id}, conn=conn)
    return BookingSettingsOut(
        **(row or DEFAULT_SETTINGS), replies_off=await customer_replies_off(conn, client_id)
    )


async def put_settings(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingSettingsIn
) -> BookingSettingsOut:
    """Replace the settings. The first save makes the row; a save from an older read is 409."""
    await lock(conn, "settings", client_id)
    current = await db.q.fetch_one("bookings/settings_get.sql", {"client_id": client_id}, conn=conn)
    check_version(current["version"] if current else 0, data.version)
    values = data.model_dump(exclude={"version"}) | {"client_id": client_id}
    sql = "bookings/settings_update.sql" if current else "bookings/settings_insert.sql"
    await db.q.fetch_one(sql, values, conn=conn)
    await audit(conn, actor, client_id, "bookings.settings.updated", str(client_id))
    return await get_settings(conn, client_id)


# --- blocks ----------------------------------------------------------------------------------


async def list_blocks(
    conn: Conn, client_id: UUID, start: datetime | None, end: datetime | None
) -> list[BookingBlockOut]:
    """Blocks that end after `start` (default: now) and begin before `end`, earliest first."""
    rows = await db.q.fetch_all(
        "bookings/blocks_list.sql", {"client_id": client_id, "from": start, "to": end}, conn=conn
    )
    return [BookingBlockOut.model_validate(row) for row in rows]


async def create_block(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: BookingBlockIn
) -> BookingBlockOut:
    """Block a person or room for a period, writing its hold in the same transaction.

    A block over an existing booking, or over another block, is refused as `slot_taken`.
    """
    if data.ends_at <= data.starts_at:
        raise refuse(422, "block_times_invalid")
    await _check_subject(conn, client_id, data.subject_kind, data.subject_id)
    await lock_subjects(conn, client_id, [(data.subject_kind, data.subject_id)])
    try:
        row = await db.q.fetch_one(
            "bookings/block_insert.sql",
            {"client_id": client_id, "created_by": actor.user_id, **data.model_dump()},
            conn=conn,
        )
        if row is None:
            raise RuntimeError("block_insert.sql returned no row")
        await db.q.execute(
            "bookings/block_hold_insert.sql",
            {"client_id": client_id, "block_id": row["id"], **data.model_dump()},
            conn=conn,
        )
    except (errors.ExclusionViolation, errors.DeadlockDetected) as exc:
        raise refuse(409, "slot_taken") from exc
    await audit(conn, actor, client_id, "bookings.block.created", str(row["id"]))
    return BookingBlockOut.model_validate(row)


async def remove_block(conn: Conn, client_id: UUID, actor: ConsoleActor, block_id: UUID) -> None:
    """Lift a block; its hold goes with it."""
    row = await db.q.fetch_one(
        "bookings/block_delete.sql", {"client_id": client_id, "id": block_id}, conn=conn
    )
    if row is None:
        raise refuse(404, "block_not_found")
    await audit(conn, actor, client_id, "bookings.block.removed", str(block_id))
