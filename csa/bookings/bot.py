"""The customer's booking rules, on top of the staff core.

Every function runs on the caller's tenant connection and answers with a `BotOutcome`: a code the
bot can say something true about, the booking that now exists (when one does) and fresh times when
the one asked for could not be had. Nothing here raises an HTTP error at the agent, and nothing says
why a time was refused.

The order matters. The company's gates come first (Customer support on, replies not Off), so a
company with replies Off writes nothing. Then the message's own key: a retried message finds the
booking it already made and answers `replay`, before any lock or time check, because that booking's
own hold now blocks its slot. Only then the rules, and every write sits in a savepoint, so a refusal
leaves nothing half written on the connection the caller goes on to commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from csa.bookings import service, setup
from csa.bookings.contracts import BookingIn, BookingTypeOut
from csa.core import db

Conn = service.Conn
ReplyMode = Literal["shadow", "suggest", "auto"]
Code = Literal[
    "confirmed",
    "pending",
    "moved",
    "change_pending",
    "cancelled",
    "replay",
    "replies_off",
    "service_off",
    "slot_taken",
    "not_allowed",
    "limit_reached",
    "past_cutoff",
    "not_found",
    "overlaps_itself",
]

REPLY_MODES: tuple[ReplyMode, ...] = ("shadow", "suggest", "auto")
# `Policy.deployment_mode` (csa/graph/policy/gate.py) is "auto" until a company sets it. That model
# lives behind the LangGraph boundary, so the default is repeated here.
DEFAULT_REPLY_MODE: ReplyMode = "auto"
CUSTOMER_AUDIENCES = ("customers", "both")
LIVE = ("pending", "confirmed")
# How far from the asked day to look for other times, and how many to show.
OFFER_WINDOW = timedelta(days=14)
FRESH_OFFERS = 3


@dataclass(frozen=True)
class Offer:
    starts_at: datetime
    ends_at: datetime
    label: str


@dataclass(frozen=True)
class BotOutcome:
    code: Code
    booking: service.BookingRecord | None = None
    offers: list[Offer] = field(default_factory=list)


# --- the company's gates -----------------------------------------------------------------------


async def reply_mode(conn: Conn, client_id: UUID) -> ReplyMode:
    """The company's reply mode. A company that never chose has `auto`; a value this code does not
    know is treated as Off, so a surprise never writes anything.
    """
    row = await db.q.fetch_one("turn/get_client_policy.sql", (client_id,), conn=conn)
    if row is None:
        return "shadow"
    policy = row["policy"]
    mode = policy.get("deployment_mode", DEFAULT_REPLY_MODE) if isinstance(policy, dict) else None
    if mode is None:
        return DEFAULT_REPLY_MODE
    return mode if mode in REPLY_MODES else "shadow"


async def customer_service_on(conn: Conn, client_id: UUID) -> bool:
    """Customer support is active (a suspended company's bot does nothing)."""
    row = await db.q.fetch_one(
        "console/service_package_any_in_status.sql",
        (client_id, [setup.REPLIES_PACKAGE], ["active"]),
        conn=conn,
    )
    return row is not None


async def _gate(conn: Conn, client_id: UUID) -> tuple[Code | None, ReplyMode]:
    if not await customer_service_on(conn, client_id):
        return "service_off", "shadow"
    mode = await reply_mode(conn, client_id)
    return ("replies_off" if mode == "shadow" else None), mode


# --- what a customer may be offered ------------------------------------------------------------


def _for_customers(booking_type: BookingTypeOut) -> bool:
    return booking_type.is_active and booking_type.audience in CUSTOMER_AUDIENCES


async def customer_types(conn: Conn, client_id: UUID) -> list[BookingTypeOut]:
    """Active types a customer may book: for customers, or for both."""
    return [t for t in await setup.list_types(conn, client_id) if _for_customers(t)]


async def _customer_type(conn: Conn, client_id: UUID, type_id: UUID) -> BookingTypeOut | None:
    found = await setup.list_types(conn, client_id, type_id)
    return found[0] if found and _for_customers(found[0]) else None


def format_slot(starts_at: datetime, zone: ZoneInfo) -> str:
    """A time as a plain local time, such as "Tuesday 10 November 2026, 3:00 pm"."""
    local = starts_at.astimezone(zone)
    hour = local.hour % 12 or 12
    meridiem = "am" if local.hour < 12 else "pm"
    return f"{local:%A} {local.day} {local:%B %Y}, {hour}:{local.minute:02d} {meridiem}"


async def offer(
    conn: Conn, client_id: UUID, *, type_id: UUID, day_from: date, now: datetime, limit: int = 5
) -> list[Offer]:
    """The next free times for a type a customer may book, from `day_from` (a day in the company's
    zone) or from now, whichever is later. A person is named only for a type that names its people;
    for "any free person" the name comes once the booking exists.
    """
    booking_type = await _customer_type(conn, client_id, type_id)
    if booking_type is None:
        return []
    zone = await service.company_zone(conn, client_id)
    start = max(now, datetime.combine(day_from, time.min, tzinfo=zone))
    try:
        slots = await service.availability(
            conn,
            client_id,
            start=start,
            end=start + OFFER_WINDOW,
            type_id=type_id,
            people=[],
            resources=[],
            minutes=None,
            limit=limit,
            now=now,
        )
    except HTTPException:
        return []
    names = (
        {p.id: p.display_name for p in await setup.list_people(conn, client_id)}
        if booking_type.delivery == "named"
        else {}
    )
    return [
        Offer(s.starts_at, s.ends_at, _label(s.starts_at, zone, s.people, names)) for s in slots
    ]


def _label(starts_at: datetime, zone: ZoneInfo, people: list[UUID], names: dict[UUID, str]) -> str:
    label = format_slot(starts_at, zone)
    named = [names[p] for p in people[:1] if p in names]
    return f"{label} with {named[0]}" if named else label


async def _fresh(
    conn: Conn, client_id: UUID, type_id: UUID, refused: datetime, now: datetime
) -> list[Offer]:
    """Up to three other times for the same type, near the one that was refused."""
    zone = await service.company_zone(conn, client_id)
    day = max(refused.astimezone(zone).date(), now.astimezone(zone).date())
    found = await offer(conn, client_id, type_id=type_id, day_from=day, now=now, limit=4)
    if not [o for o in found if o.starts_at != refused]:
        found = await offer(
            conn, client_id, type_id=type_id, day_from=now.astimezone(zone).date(), now=now, limit=4
        )
    return [o for o in found if o.starts_at != refused][:FRESH_OFFERS]


# --- turning the core's refusals into outcomes ---------------------------------------------------


def _code_for(exc: HTTPException) -> Code:
    """A refusal from the core as a code. A taken time is `slot_taken`; a missing or switched-off
    type is `not_found`; every rule or setup problem besides is `not_allowed`.
    """
    if isinstance(exc, service.Refusal):
        return "slot_taken" if exc.body["detail"] == "slot_taken" else "not_allowed"
    if exc.detail in ("type_not_found", "type_inactive"):
        return "not_found"
    return "not_allowed"


async def _refusal(
    conn: Conn,
    client_id: UUID,
    exc: HTTPException,
    type_id: UUID | None,
    asked: datetime,
    now: datetime,
) -> BotOutcome:
    code = _code_for(exc)
    offers: list[Offer] = []
    if code in ("slot_taken", "not_allowed") and type_id is not None:
        offers = await _fresh(conn, client_id, type_id, asked, now)
    return BotOutcome(code, None, offers)


# --- settings and what a customer already holds --------------------------------------------------


async def _settings(conn: Conn, client_id: UUID) -> dict[str, Any]:
    row = await db.q.fetch_one("bookings/settings_get.sql", {"client_id": client_id}, conn=conn)
    return dict(row or setup.DEFAULT_SETTINGS)


async def _replayed(
    conn: Conn, client_id: UUID, contact_id: UUID, turn_key: str
) -> BotOutcome | None:
    """Serialise this message, then answer with the booking it already made, if any."""
    await setup.lock(conn, "turn", client_id, turn_key)
    row = await db.q.fetch_one(
        "bookings/booking_by_turn_key.sql",
        {"client_id": client_id, "turn_key": turn_key},
        conn=conn,
    )
    if row is None:
        return None
    if row["contact_id"] != contact_id:
        return BotOutcome("not_found")
    return BotOutcome("replay", await _record(conn, client_id, row["id"]))


async def _record(conn: Conn, client_id: UUID, booking_id: UUID) -> service.BookingRecord:
    return (await service.read_records(conn, client_id, booking_id=booking_id))[0]


async def _own(
    conn: Conn, client_id: UUID, contact_id: UUID, booking_id: UUID
) -> dict[str, Any] | None:
    """The booking, locked, when it is this customer's to act on; None for anything else."""
    return await db.q.fetch_one(
        "bookings/booking_own_lock.sql",
        {"client_id": client_id, "id": booking_id, "contact_id": contact_id},
        conn=conn,
    )


# --- book --------------------------------------------------------------------------------------


async def book(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    *,
    type_id: UUID,
    starts_at: datetime,
    turn_key: str,
    conversation_id: UUID | None,
    now: datetime,
) -> BotOutcome:
    """Book a time for this customer, obeying the reply mode.

    `suggest` holds the slot as `pending`; `auto` confirms it unless the type needs confirmation. A
    message that already booked answers `replay`.
    """
    blocked, mode = await _gate(conn, client_id)
    if blocked is not None:
        return BotOutcome(blocked)
    replayed = await _replayed(conn, client_id, contact_id, turn_key)
    if replayed is not None:
        return replayed
    booking_type = await _customer_type(conn, client_id, type_id)
    if booking_type is None:
        return BotOutcome("not_found")
    settings = await _settings(conn, client_id)
    await setup.lock(conn, "customer", client_id, contact_id)  # one customer, one limit check
    held = await db.q.fetch_one(
        "bookings/customer_upcoming_count.sql",
        {"client_id": client_id, "contact_id": contact_id, "now": now},
        conn=conn,
    )
    if held is not None and held["n"] >= settings["customer_limit"]:
        return BotOutcome("limit_reached")
    pending = mode == "suggest" or booking_type.needs_confirmation
    try:
        async with conn.transaction():
            record = await service.create_for(
                conn,
                client_id,
                service.BOT,
                BookingIn(type_id=type_id, starts_at=starts_at, contact_id=contact_id),
                now=now,
                force_pending=pending,
                turn_key=turn_key,
                conversation_id=conversation_id,
                expires_at=now + timedelta(hours=settings["pending_hours"]) if pending else None,
            )
    except HTTPException as exc:
        return await _refusal(conn, client_id, exc, type_id, starts_at, now)
    return BotOutcome("pending" if record.status == "pending" else "confirmed", record)


# --- change and cancel -------------------------------------------------------------------------


def _past_cutoff(
    record: service.BookingRecord, booking_type: BookingTypeOut, now: datetime
) -> bool:
    return record.starts_at - now < timedelta(minutes=booking_type.change_cutoff_minutes)


async def change(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    booking_id: UUID,
    *,
    starts_at: datetime,
    turn_key: str,
    now: datetime,
) -> BotOutcome:
    """Move this customer's booking to a new time.

    In `auto`, for a type that needs no confirmation, the booking moves in place. Otherwise the new
    time is held as a second, pending booking that replaces the first, and the first stays as it
    is until staff confirm.
    """
    blocked, mode = await _gate(conn, client_id)
    if blocked is not None:
        return BotOutcome(blocked)
    replayed = await _replayed(conn, client_id, contact_id, turn_key)
    if replayed is not None:
        return replayed
    own = await _own(conn, client_id, contact_id, booking_id)
    if own is None or own["replaces_booking_id"] is not None or own["status"] not in LIVE:
        return BotOutcome("not_found")
    record = await _record(conn, client_id, booking_id)
    booking_type = await _customer_type(conn, client_id, record.type_id) if record.type_id else None
    if booking_type is None:
        return BotOutcome("not_found")
    in_place = record.status == "pending" or (
        mode == "auto" and not booking_type.needs_confirmation
    )
    if in_place and record.starts_at == starts_at:
        return BotOutcome("replay", record)  # it is already at that time (a retried change)
    if _past_cutoff(record, booking_type, now):
        return BotOutcome("past_cutoff")
    try:
        if in_place:
            async with conn.transaction():
                moved = await service.move_to(
                    conn, client_id, service.BOT, record, starts_at, now=now
                )
            return BotOutcome("moved", moved)
        return await _ask_to_move(
            conn, client_id, contact_id, record, booking_type, starts_at, turn_key, now
        )
    except HTTPException as exc:
        return await _refusal(conn, client_id, exc, record.type_id, starts_at, now)


async def _ask_to_move(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    record: service.BookingRecord,
    booking_type: BookingTypeOut,
    starts_at: datetime,
    turn_key: str,
    now: datetime,
) -> BotOutcome:
    """Hold the new time as a pending booking that replaces `record`, which stays confirmed."""
    minutes = int((record.ends_at - record.starts_at).total_seconds() // 60)
    end = service.window_end(starts_at.astimezone(UTC), minutes)
    hold_end = end + timedelta(minutes=booking_type.buffer_minutes)
    # The database refuses a second hold overlapping the first booking's own, whatever its status,
    # so a time that touches the old slot (or its buffer) cannot be held as a change.
    touching = await db.q.fetch_one(
        "bookings/holds_overlap_booking.sql",
        {"client_id": client_id, "booking_id": record.id, "from": starts_at, "to": hold_end},
        conn=conn,
    )
    if touching is not None:
        return BotOutcome("overlaps_itself")
    settings = await _settings(conn, client_id)
    async with conn.transaction():
        await _drop_pending_changes(conn, client_id, record.id)
        asked = await service.create_for(
            conn,
            client_id,
            service.BOT,
            BookingIn(
                type_id=record.type_id,
                starts_at=starts_at,
                contact_id=contact_id,
                people=[p.person_id for p in record.people],
                resources=[r.id for r in record.resources],
            ),
            now=now,
            force_pending=True,
            turn_key=turn_key,
            expires_at=now + timedelta(hours=settings["pending_hours"]),
            replaces_booking_id=record.id,
        )
    return BotOutcome("change_pending", asked)


async def _drop_pending_changes(conn: Conn, client_id: UUID, booking_id: UUID) -> None:
    """Cancel the pending changes that ask to move this booking, so the latest request is the only
    one and cancelling the booking leaves none behind.
    """
    rows = await db.q.fetch_all(
        "bookings/booking_pending_changes.sql",
        {"client_id": client_id, "id": booking_id},
        conn=conn,
    )
    for row in rows:
        await service.cancel_for(
            conn, client_id, service.BOT, await _record(conn, client_id, row["id"])
        )


async def cancel(
    conn: Conn, client_id: UUID, contact_id: UUID, booking_id: UUID, *, turn_key: str, now: datetime
) -> BotOutcome:
    """Cancel this customer's booking and free its time. A booking already cancelled answers
    `replay`, so a retried message still hears the truth.
    """
    blocked, _ = await _gate(conn, client_id)
    if blocked is not None:
        return BotOutcome(blocked)
    # Nothing is stored against a cancel, so the key only queues two deliveries of one message.
    await setup.lock(conn, "turn", client_id, turn_key)
    own = await _own(conn, client_id, contact_id, booking_id)
    if own is None:
        return BotOutcome("not_found")
    record = await _record(conn, client_id, booking_id)
    if record.status == "cancelled":
        return BotOutcome("replay", record)
    if record.status not in LIVE:
        return BotOutcome("not_found")
    found = await setup.list_types(conn, client_id, record.type_id) if record.type_id else []
    if not found:
        return BotOutcome("not_found")
    if _past_cutoff(record, found[0], now):
        return BotOutcome("past_cutoff")
    try:
        async with conn.transaction():
            await _drop_pending_changes(conn, client_id, booking_id)
            cancelled = await service.cancel_for(conn, client_id, service.BOT, record)
    except HTTPException as exc:
        return BotOutcome(_code_for(exc))
    return BotOutcome("cancelled", cancelled)
