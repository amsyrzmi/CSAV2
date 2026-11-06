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
from collections.abc import Callable
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
        self, access: SecretStr, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> None: ...

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
) -> str:
    """Patch the event; if Google has none, make it with the stored id. A taken id is either the
    same event (the push and the job raced: patch it) or one deleted in Google (its id stays
    reserved: a new id, once). A deleted calendar is made again, once.
    """
    calendar_id = access.calendar_id
    token = access.token
    for renewed in (False, True):
        try:
            await api.patch_event(token, calendar_id, event_id, body)
            return event_id
        except CalendarError as exc:
            if exc.code != "provider_not_found":
                raise
        try:
            await api.insert_event(token, calendar_id, body, event_id=event_id)
            return event_id
        except CalendarError as exc:
            if exc.code == "event_exists":
                try:
                    await api.patch_event(token, calendar_id, event_id, body)
                    return event_id
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
                await api.insert_event(token, calendar_id, body, event_id=event_id)
                return event_id
            if exc.code != "provider_not_found" or renewed:
                raise
        # Neither the event nor a place to make it: the person may have deleted the calendar.
        if await api.calendar_exists(token, calendar_id):
            raise CalendarError("provider_not_found")
        fresh = await connections.renew_calendar(client_id, person_id)
        calendar_id, token = fresh.calendar_id, fresh.token
    raise CalendarError("provider_not_found")  # unreachable: the loop returns or raises


async def sync_one(
    pool: AsyncConnectionPool,
    connections: CalendarConnections,
    api: EventsApi,
    client_id: UUID,
    booking_id: UUID,
    person_id: UUID,
    *,
    now: datetime,
) -> str:
    """Bring one person's event for one booking in line with the booking as it is now.

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
    assert row is not None
    version = record.version
    event_id: str = row["google_event_id"]
    status = "synced"
    try:
        if plan == "keep":
            status = "synced" if row["synced_version"] is not None else "deleted"
        else:
            access = await connections.access(client_id, person_id)
            if plan == "upsert":
                body = event_body(record, zone=ZoneInfo(facts["timezone"]), brand=facts["brand"])
                event_id = await _upsert(
                    api, connections, client_id, person_id, booking_id, access, event_id, body, pool
                )
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
        logger.warning("booking_calendar_sync_failed", category=exc.code)
        async with tx(pool, str(client_id)) as conn:
            await db.q.execute(
                "bookings/google_event_fail.sql",
                {**ids, "version": version, "error": exc.code[:64]},
                conn=conn,
            )
        return "failed"
    async with tx(pool, str(client_id)) as conn:
        await db.q.execute(
            "bookings/google_event_settle.sql",
            {**ids, "version": version, "status": status, "event_id": event_id},
            conn=conn,
        )
    return status


class CalendarPush:
    """Pushes one booking to every connected person on it, right after it commits."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        connections: CalendarConnections,
        api: EventsApi,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.pool = pool
        self.connections = connections
        self.api = api
        self.now = now

    @property
    def available(self) -> bool:
        return self.connections.available

    async def __call__(self, booking: service.BookingRecord, *, timeout: float) -> None:
        """Never raises: the booking has committed, and a slow or refusing Google only leaves rows
        for the job. Cut off at `timeout` seconds; whatever was not finished is the job's.
        """
        if not self.available:
            return
        try:
            async with tx(self.pool, str(booking.client_id)) as conn:
                rows = await db.q.fetch_all(
                    "bookings/google_connected_attendees.sql",
                    {"client_id": booking.client_id, "booking_id": booking.id},
                    conn=conn,
                )
            if not rows:
                return
            await asyncio.wait_for(
                asyncio.gather(
                    *(
                        sync_one(
                            self.pool,
                            self.connections,
                            self.api,
                            booking.client_id,
                            booking.id,
                            row["person_id"],
                            now=self.now(),
                        )
                        for row in rows
                    )
                ),
                timeout,
            )
        except TimeoutError:
            logger.warning("booking_calendar_push_failed", category="timeout")
        except Exception as exc:
            logger.warning("booking_calendar_push_failed", category=type(exc).__name__)


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
                )
            except Exception as exc:
                logger.error("booking_calendar_reconcile_failed", category=type(exc).__name__)
                return "failed"

    stats["due"] = len(work)
    for outcome in await asyncio.gather(*(one(*item) for item in work)):
        stats[outcome] += 1
    return stats
