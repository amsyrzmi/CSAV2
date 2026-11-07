"""Making a person's Google events match the bookings they attend.

The booking is the only source of what an event should hold; the ledger (`booking_calendar_events`)
only records what Google last got, as the booking's `version`. Every sync reads the booking and the
person's connection just before it calls Google, never inside a transaction, and records only the
version it read. Two things run it: `CalendarPush`, right after a staff or assistant booking
commits, and `run_calendar_reconcile`, a job that brings in line everything else (the bot's moves
and cancels, a lapse, a no-show, a crash after commit, a person who connected with bookings
already made).

An event is made with an id of our own, stored in the ledger row before the call. A crash after
Google made it, or the push racing the job, is then a patch and never a second event. Logs carry
categories only, never a title, an address or an event id.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from csa.bookings import ics, service
from csa.bookings.google.client import CalendarError, EventResult
from csa.bookings.google.connections import CalendarAccess, CalendarConnections
from csa.core import db
from csa.core.tenancy import tx

if TYPE_CHECKING:
    from csa.jobs.clock import Clock  # the jobs package imports this module

logger = structlog.get_logger(__name__)

SIMULTANEOUS = 4
Plan = Literal["upsert", "remove", "keep"]
STATS = ("due", "synced", "deleted", "failed", "skipped", "needs_reconnect")


class EventsApi(Protocol):
    """The part of `CalendarClient` the sync needs."""

    async def create_calendar(self, access: SecretStr, *, summary: str, time_zone: str) -> str: ...

    async def calendar_exists(self, access: SecretStr, calendar_id: str) -> bool: ...

    async def insert_event(
        self,
        access: SecretStr,
        calendar_id: str,
        body: dict[str, Any],
        *,
        meet: bool = False,
        event_id: str | None = None,
    ) -> EventResult: ...

    async def patch_event(
        self,
        access: SecretStr,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
        *,
        meet: bool = False,
    ) -> str | None: ...

    async def delete_event(self, access: SecretStr, calendar_id: str, event_id: str) -> None: ...


def _where(record: service.BookingRecord) -> tuple[str | None, list[str]]:
    """The place an event shows and the lines under it, as the calendar file words them."""
    link = record.meeting_link
    if link is not None and not (link.startswith("https://") and link.isprintable()):
        link = None
    if record.location_kind == "in_person" and record.address:
        maps, waze = ics.map_links(record.address)
        return record.address, [f"Google Maps: {maps}", f"Waze: {waze}"]
    if record.location_kind == "online" and link:
        return link, [f"Join: {link}"]
    if record.location_kind == "phone":
        return ics.PHONE_PLACE, []
    return None, []


def event_body(record: service.BookingRecord, *, zone: ZoneInfo, brand: str) -> dict[str, Any]:
    """What an event holds: the title, the times in the company's zone, the place or link, the names
    of the people and guests attending, and the customer's name. Never a phone number, an address
    or an email, and never a note: the customer's name is the only thing about them.
    """
    place, notes = _where(record)
    names = [p.display_name for p in record.people] + [g.name for g in record.guests if g.name]
    lines = list(notes)
    if names:
        lines.append("With: " + ", ".join(names))
    if record.customer is not None and record.customer.full_name:
        lines.append(f"Customer: {record.customer.full_name}")
    lines.append(f"From the {brand} Bookings page.")
    body: dict[str, Any] = {
        "summary": record.title or "Booking",
        "description": "\n".join(lines),
        "start": {"dateTime": record.starts_at.astimezone(zone).isoformat(), "timeZone": zone.key},
        "end": {"dateTime": record.ends_at.astimezone(zone).isoformat(), "timeZone": zone.key},
        "transparency": "opaque",
        # An event the person deleted stays in Google as cancelled, and a patch only brings it
        # back if it says it is confirmed.
        "status": "confirmed",
    }
    if place:
        body["location"] = place
    return body


def plan_for(record: service.BookingRecord, now: datetime) -> Plan:
    """What the booking calls for: an event, none (remove any), or to leave things as they are (a
    booking that has ended, or a no-show: never pushed now, and an event already there stays).
    """
    if record.status == "confirmed" and record.replaces_booking_id is None:
        return "upsert" if record.ends_at > now else "keep"
    if record.status == "no_show":
        return "keep"
    return "remove"


async def _upsert(
    api: EventsApi,
    connections: CalendarConnections,
    client_id: UUID,
    person_id: UUID,
    booking_id: UUID,
    access: CalendarAccess,
    event_id: str,
    body: dict[str, Any],
    pool: AsyncConnectionPool,
    *,
    meet: bool = False,
) -> tuple[str, str | None]:
    """Patch the event; if Google has none, make it with the stored id. A taken id is either the
    same event (the push and the job raced: patch it) or one deleted in Google (its id stays
    reserved: a new id, once). A deleted calendar is made again, once. With `meet`, the write
    asks for a Meet link, and is made again without when Google refuses one (the event is then
    made with no link). Returns the event's id and the link Google gave, if any.
    """
    calendar_id = access.calendar_id
    token = access.token

    async def patch() -> str | None:
        return await _with_meet(
            meet, lambda ask: api.patch_event(token, calendar_id, event_id, body, meet=ask)
        )

    async def insert() -> str | None:
        async def make(ask: bool) -> str | None:
            made = await api.insert_event(token, calendar_id, body, meet=ask, event_id=event_id)
            return made.meet_link

        return await _with_meet(meet, make)

    for renewed in (False, True):
        try:
            return event_id, await patch()
        except CalendarError as exc:
            if exc.code != "provider_not_found":
                raise
        try:
            return event_id, await insert()
        except CalendarError as exc:
            if exc.code == "event_exists":
                try:
                    return event_id, await patch()
                except CalendarError as again:
                    if again.code != "provider_not_found":
                        raise
                event_id = uuid.uuid4().hex
                async with tx(pool, str(client_id)) as conn:
                    await db.q.execute(
                        "bookings/google_event_id_set.sql",
                        {
                            "client_id": client_id,
                            "booking_id": booking_id,
                            "person_id": person_id,
                            "event_id": event_id,
                        },
                        conn=conn,
                    )
                return event_id, await insert()
            if exc.code != "provider_not_found" or renewed:
                raise
        # Neither the event nor a place to make it: the person may have deleted the calendar.
        if await api.calendar_exists(token, calendar_id):
            raise CalendarError("provider_not_found")
        fresh = await connections.renew_calendar(client_id, person_id, calendar_id)
        calendar_id, token = fresh.calendar_id, fresh.token
    raise CalendarError("provider_not_found")  # unreachable: the loop returns or raises


async def _with_meet(meet: bool, call: Callable[[bool], Awaitable[str | None]]) -> str | None:
    """Make `call` asking for a Meet link; if Google refuses the request that asks, make it again
    without (the whole request was refused, so nothing was made) and say there is no link.
    """
    if meet:
        try:
            return await call(True)
        except CalendarError as exc:
            if exc.code != "meet_unavailable":
                raise
    await call(False)
    return None


def wants_meet(record: service.BookingRecord, now: datetime) -> bool:
    """A confirmed online booking, still to come, with no link at all (not even one staff typed)."""
    return (
        record.location_kind == "online"
        and not record.meeting_link
        and plan_for(record, now) == "upsert"
    )


async def meet_organizer(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, booking_id: UUID
) -> UUID | None:
    """The one connected attendee who asks Google for the booking's Meet link."""
    row = await db.q.fetch_one(
        "bookings/google_meet_organizer.sql",
        {"client_id": client_id, "booking_id": booking_id},
        conn=conn,
    )
    return None if row is None else row["person_id"]


async def sync_one(
    pool: AsyncConnectionPool,
    connections: CalendarConnections,
    api: EventsApi,
    client_id: UUID,
    booking_id: UUID,
    person_id: UUID,
    *,
    now: datetime,
    meet: bool = False,
) -> str:
    """Bring one person's event for one booking in line with the booking as it is now. With `meet`,
    the booking's organizer (see `meet_organizer`) also asks Google for the Meet link a confirmed
    online booking has none of, and the link is kept on the booking if it still has none.

    Returns how it ended: `synced`, `deleted`, `skipped` (nothing to do), `failed` (retried by
    the job, up to five times at one version) or `needs_reconnect` (Google refused the person's
    grant; nothing is tried again until they connect).
    """
    ids = {"client_id": client_id, "booking_id": booking_id, "person_id": person_id}
    async with tx(pool, str(client_id)) as conn:
        records = await service.read_records(conn, client_id, booking_id=booking_id)
        facts = await db.q.fetch_one("bookings/mail_facts.sql", {"client_id": client_id}, conn=conn)
        row = await db.q.fetch_one("bookings/google_event_get.sql", ids, conn=conn)
        if not records or facts is None or (row is not None and row["status"] == "dropped"):
            return "skipped"
        record = records[0]
        plan = plan_for(record, now)
        if plan != "upsert" and row is None:
            return "skipped"
        if plan == "upsert":
            row = await db.q.fetch_one(
                "bookings/google_event_ensure.sql", {**ids, "event_id": uuid.uuid4().hex}, conn=conn
            )
        ask = (
            meet
            and wants_meet(record, now)
            and await meet_organizer(conn, client_id, booking_id) == person_id
        )
    assert row is not None
    version = record.version
    event_id: str = row["google_event_id"]
    status = "synced"
    access: CalendarAccess | None = None
    try:
        if plan == "keep":
            status = "synced" if row["synced_version"] is not None else "deleted"
        else:
            access = await connections.access(client_id, person_id)
            if plan == "upsert":
                body = event_body(record, zone=ZoneInfo(facts["timezone"]), brand=facts["brand"])
                event_id, link = await _upsert(
                    api,
                    connections,
                    client_id,
                    person_id,
                    booking_id,
                    access,
                    event_id,
                    body,
                    pool,
                    meet=ask,
                )
                if ask:
                    if link is None:
                        # The event is there without one: tried again later, up to five times.
                        raise CalendarError("meet_unavailable")
                    written = await _keep_link(pool, client_id, booking_id, link)
                    if written == version + 1:  # our write alone moved it
                        version = written
            else:
                status = "deleted"
                try:
                    await api.delete_event(access.token, access.calendar_id, event_id)
                except CalendarError as exc:
                    if exc.code != "provider_not_found":
                        raise
    except CalendarError as exc:
        if exc.code in {"invalid_grant", "not_connected"}:
            return "needs_reconnect"
        if exc.code == "provider_unauthorized" and access is not None and not access.refreshed:
            # Probably a token Google revoked early: refresh it next time, which finds out whether
            # the grant is gone. Not counted, or five of the job's runs would use the tries up.
            await connections.expire_token(client_id, person_id)
            return "failed"
        logger.warning("booking_calendar_sync_failed", category=exc.code)
        async with tx(pool, str(client_id)) as conn:
            await db.q.execute(
                "bookings/google_event_fail.sql",
                {**ids, "version": version, "error": exc.code[:64]},
                conn=conn,
            )
        return "failed"
    async with tx(pool, str(client_id)) as conn:
        settled = await db.q.fetch_one(
            "bookings/google_event_settle.sql",
            {**ids, "version": version, "status": status, "event_id": event_id},
            conn=conn,
        )
        if settled is None:
            # A newer version is recorded, but this write may have reached Google after it.
            await db.q.execute(
                "bookings/google_event_stale.sql", {**ids, "version": version}, conn=conn
            )
    return status


async def _keep_link(
    pool: AsyncConnectionPool, client_id: UUID, booking_id: UUID, link: str
) -> int | None:
    """Keep the link Google made on the booking if it still has none: the version it is now at, or
    None when staff typed one (or the booking changed) first. Its own transaction, after the
    call.
    """
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one(
            "bookings/google_meet_link_set.sql",
            {"client_id": client_id, "booking_id": booking_id, "link": link},
            conn=conn,
        )
    return None if row is None else int(row["version"])


class CalendarPush:
    """Pushes one booking to every connected person on it, right after it commits."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        connections: CalendarConnections,
        api: EventsApi,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        meet: bool = False,
    ) -> None:
        self.pool = pool
        self.connections = connections
        self.api = api
        self.now = now
        self.meet = meet  # whether a confirmed online booking with no link gets a Meet link

    @property
    def available(self) -> bool:
        return self.connections.available

    async def __call__(
        self, booking: service.BookingRecord, *, timeout: float
    ) -> service.BookingRecord:
        """Never raises: the booking has committed, and a slow or refusing Google only leaves rows
        for the job. Cut off at `timeout` seconds; whatever was not finished is the job's.
        Returns the booking as it is now: with a Meet link made here, the link and the version it
        moved to, so the mail that follows says what the calendar says.
        """
        if not self.available:
            return booking
        organizer: UUID | None = None
        try:
            async with tx(self.pool, str(booking.client_id)) as conn:
                rows = await db.q.fetch_all(
                    "bookings/google_connected_attendees.sql",
                    {"client_id": booking.client_id, "booking_id": booking.id},
                    conn=conn,
                )
                if self.meet and wants_meet(booking, self.now()):
                    organizer = await meet_organizer(conn, booking.client_id, booking.id)
            if not rows:
                return booking
            await asyncio.wait_for(
                self._sync_all(booking, [row["person_id"] for row in rows], organizer), timeout
            )
        except TimeoutError:
            logger.warning("booking_calendar_push_failed", category="timeout")
        except Exception as exc:
            logger.warning("booking_calendar_push_failed", category=type(exc).__name__)
        return await self._refreshed(booking) if organizer is not None else booking

    async def _sync_all(
        self, booking: service.BookingRecord, people: list[UUID], organizer: UUID | None
    ) -> None:
        """The organizer first when a link is wanted, so everyone else's event is made with it."""
        if organizer is not None:
            await self._sync(booking, organizer)
        await asyncio.gather(*(self._sync(booking, p) for p in people if p != organizer))

    async def _sync(self, booking: service.BookingRecord, person_id: UUID) -> str:
        return await sync_one(
            self.pool,
            self.connections,
            self.api,
            booking.client_id,
            booking.id,
            person_id,
            now=self.now(),
            meet=self.meet,
        )

    async def _refreshed(self, booking: service.BookingRecord) -> service.BookingRecord:
        """The booking with the link and version it has now, or as it was if it cannot be read."""
        try:
            async with tx(self.pool, str(booking.client_id)) as conn:
                [now] = await service.read_records(conn, booking.client_id, booking_id=booking.id)
        except Exception as exc:
            logger.warning("booking_calendar_push_failed", category=type(exc).__name__)
            return booking
        # The version only when the link alone moved it: otherwise the booking changed under us.
        moved = now.version == booking.version + 1 and now.meeting_link != booking.meeting_link
        if not moved:  # the booking changed under us: it is as it was handed in
            return booking
        return replace(booking, meeting_link=now.meeting_link, version=now.version)

    async def ensure_meet(self, booking: service.BookingRecord) -> service.BookingRecord:
        """Ask again for a booking's Meet link (the retry staff press): the organizer's event, now.
        Raises `CalendarError` with `not_connected` when no one on it has a calendar connected.
        """
        async with tx(self.pool, str(booking.client_id)) as conn:
            organizer = await meet_organizer(conn, booking.client_id, booking.id)
        if organizer is None:
            raise CalendarError("not_connected")
        await self._sync(booking, organizer)
        return await self._refreshed(booking)


async def run_calendar_reconcile(
    conn: psycopg.AsyncConnection[Any],
    app_pool: AsyncConnectionPool,
    push: CalendarPush | None,
    clock: Clock,
    *,
    batch_size: int = 50,
) -> dict[str, int]:
    """Once for every company with someone connected: the owner connection only lists them, and each
    booking is then synced through its own company's transaction, as `csa_app`. Companies share
    the batch, so one with a long backlog cannot starve the rest.
    """
    stats: dict[str, int] = dict.fromkeys(STATS, 0)
    if push is None or not push.available:
        return stats
    async with conn.transaction():
        companies = await db.q.fetch_all("bookings/google_reconcile_companies.sql", conn=conn)
    if not companies:
        return stats
    share = max(1, math.ceil(batch_size / len(companies)))
    work: list[tuple[UUID, UUID, UUID]] = []
    for company in companies:
        async with tx(app_pool, str(company["client_id"])) as scoped:
            rows = await db.q.fetch_all(
                "bookings/google_reconcile_due.sql",
                {"client_id": company["client_id"], "batch_size": share},
                conn=scoped,
            )
        work += [(company["client_id"], r["booking_id"], r["person_id"]) for r in rows]
    gate = asyncio.Semaphore(SIMULTANEOUS)

    async def one(client_id: UUID, booking_id: UUID, person_id: UUID) -> str:
        async with gate:
            try:
                return await sync_one(
                    app_pool,
                    push.connections,
                    push.api,
                    client_id,
                    booking_id,
                    person_id,
                    now=clock.now(),
                    meet=push.meet,
                )
            except Exception as exc:
                logger.error("booking_calendar_reconcile_failed", category=type(exc).__name__)
                return "failed"

    stats["due"] = len(work)
    for outcome in await asyncio.gather(*(one(*item) for item in work)):
        stats[outcome] += 1
    return stats
