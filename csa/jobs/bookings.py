"""Scheduled work on bookings: the bot's pending bookings that ran out of time, and reminders.

Reminders are two jobs. `run_reminder_queue` runs per company, as that company, and writes what
is owed: one row in `booking_reminders` per recipient and start time, and for a customer on
Telegram the message itself, in the same transaction. `run_reminder_mail` runs once for everyone
and sends the queued email rows. A row is claimed (`sending`) and committed before anything is
sent, and settled after, so a worker that dies mid-send leaves it `sending` and it is never sent
twice: a lost reminder is the price of never sending one twice.

Logs carry categories only, never an address or a title.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import structlog

from csa.bookings import bot, customer_messages, notify, service, setup
from csa.console.mail_templates import customer_lapsed_mail, reminder_mail
from csa.console.mailer import Mailer, OutgoingMail
from csa.core import db
from csa.jobs.clock import Clock

logger = structlog.get_logger(__name__)

Audience = Literal["customer", "staff", "lapse"]


# --- a reminder row -----------------------------------------------------------------------------


async def _queue_row(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    booking: service.BookingRecord,
    audience: Audience,
    *,
    channel: Literal["telegram", "email"],
    address: str | None,
    sent_at: datetime | None = None,
) -> bool:
    """Make the row. True when it is new or was dropped and is queued again; false when this
    booking, start, audience and address already has one (sent, failed or waiting)."""
    row = await db.q.fetch_one(
        "bookings/reminder_insert.sql",
        {
            "client_id": client_id,
            "booking_id": booking.id,
            "starts_at": booking.starts_at,
            "audience": audience,
            "channel": channel,
            # One spelling of an address, so `A@x.test` and `a@x.test` are one reminder.
            "address": None if address is None else address.lower(),
            "status": "sent" if channel == "telegram" else "queued",
            "sent_at": sent_at,
        },
        conn=conn,
    )
    return row is not None


# --- a pending booking that ran out of time -----------------------------------------------------


async def run_pending_expiry(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Cancel the bot's pending bookings whose time is up, free their holds, and tell the customer.

    A booking lapses when `expires_at` has passed, or when it has already started: a pending booking
    moved earlier keeps the expiry it was made with. Only bookings the bot made with an expiry are
    here, so a pending booking staff made is never touched. Up to `batch_size` rows are claimed with
    `FOR UPDATE SKIP LOCKED`, so a booking a person is confirming at that moment waits for the next
    run, and each is settled in its own savepoint, so one that cannot be settled does not stop the
    rest.

    The hold always lapses. The customer is told only when the company's gates allow an automatic
    message (replies not Off, Customer support active): nothing goes without them. A customer on
    Telegram is told there, in the Inbox thread. One who is not, and has an address, is mailed:
    the row goes in `booking_reminders` as audience `lapse`, in the same savepoint, so the mail job
    sends it once and a worker that dies mid-send does not repeat it.
    """
    blocked, _ = await bot.gate(conn, client_id)
    due = await db.q.fetch_all(
        "bookings/pending_expired.sql",
        {"client_id": client_id, "now": clock.now(), "batch_size": batch_size},
        conn=conn,
    )
    expired = told = mailed = failed = 0
    for row in due:
        try:
            async with conn.transaction():
                found = await service.read_records(conn, client_id, booking_id=row["id"])
                lapsed = await service.expire_for(conn, client_id, found[0])
                if blocked is None:
                    if await customer_messages.lapsed(conn, client_id, lapsed):
                        told += 1
                    elif address := notify.clean_address(notify.customer_address(lapsed)):
                        mailed += await _queue_row(
                            conn, client_id, lapsed, "lapse", channel="email", address=address
                        )
            expired += 1
        except Exception as exc:
            failed += 1
            logger.error(
                "booking_expiry_failed",
                client_id=str(client_id),
                booking_id=str(row["id"]),
                error=type(exc).__name__,
            )
    return {"expired": expired, "told": told, "mailed": mailed, "failed": failed}


# --- queuing reminders --------------------------------------------------------------------------


async def customers_remindable(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> bool:
    """Nothing automatic reaches a customer while replies are Off or Customer support is
    suspended. A company with no Customer support package has no replies setting, so its customers
    are still reminded by email."""
    has_package = await db.q.fetch_one(
        "console/service_package_any_in_status.sql",
        (client_id, [setup.REPLIES_PACKAGE], sorted(setup.REPLIES_STATUSES)),
        conn=conn,
    )
    if has_package is None:
        return True
    blocked, _ = await bot.gate(conn, client_id)
    return blocked is None


async def _remind_customer(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    booking: service.BookingRecord,
    zone: ZoneInfo,
    now: datetime,
) -> tuple[int, int]:
    """Queue the customer's one reminder for this start: `(emails queued, messages written)`.

    A customer on Telegram gets the message, in the Inbox thread as `reviewed` so a person's
    takeover does not hold it. Anyone else with an address gets an email row. A WhatsApp customer
    with no address gets nothing: nothing proactive goes there.
    """
    live = await db.q.fetch_one(
        "bookings/reminder_customer_live.sql",
        {"client_id": client_id, "booking_id": booking.id, "starts_at": booking.starts_at},
        conn=conn,
    )
    if live is not None:
        return 0, 0
    # The key names the start, so a booking moved away and back is not written to twice.
    key = f"booking:{booking.id}:reminder:{int(booking.starts_at.timestamp())}"
    if await customer_messages.telegram_message(
        conn,
        client_id,
        booking,
        key=key,
        body=customer_messages.reminder_text(booking, zone),
        authority="reviewed",
    ):
        await _queue_row(
            conn, client_id, booking, "customer", channel="telegram", address=None, sent_at=now
        )
        return 0, 1
    address = notify.clean_address(notify.customer_address(booking))
    if address is None:
        return 0, 0
    queued = await _queue_row(
        conn, client_id, booking, "customer", channel="email", address=address
    )
    return int(queued), 0


async def _remind(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    booking_id: UUID,
    facts: notify.MailFacts,
    zone: ZoneInfo,
    customers: bool,
    now: datetime,
) -> tuple[int, int]:
    found = await service.read_records(conn, client_id, booking_id=booking_id)
    if not found or found[0].status != "confirmed" or found[0].starts_at <= now:
        return 0, 0
    booking = found[0]
    queued = told = 0
    # A private meeting is for its attendees and guests; its customer hears nothing.
    if customers and booking.customer is not None and not booking.private:
        queued, told = await _remind_customer(conn, client_id, booking, zone, now)
    for one in notify.recipients(booking, facts, customer=False):
        queued += await _queue_row(
            conn, client_id, booking, "staff", channel="email", address=one.address
        )
    return queued, told


async def run_reminder_queue(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Queue the reminders owed for the company's confirmed bookings starting within its reminder
    time. Idempotent: a recipient already queued, sent or failed for a start is left alone, so it
    can run every five minutes. A booking made inside the window is queued on the next run.

    Bookings are read a page of `batch_size` at a time until none is left, each in its own
    savepoint, so one that cannot be queued does not stop the rest. Customers are reminded only
    while the company's gates allow an automatic message (`customers_remindable`); staff and guests
    always are. A pending booking is never reminded, and nothing is queued for a cancelled one.
    """
    now = clock.now()
    row = await db.q.fetch_one("bookings/settings_get.sql", {"client_id": client_id}, conn=conn)
    minutes = (row or setup.DEFAULT_SETTINGS)["reminder_minutes"]
    until = now + timedelta(minutes=minutes)
    facts = await notify.load_facts(conn, client_id)
    zone = ZoneInfo(facts.timezone)
    customers = await customers_remindable(conn, client_id)
    bookings = queued = told = failed = 0
    after: dict[str, Any] = {"after_starts": None, "after_id": None}
    while True:
        page = await db.q.fetch_all(
            "bookings/reminders_due.sql",
            {
                "client_id": client_id,
                "now": now,
                "until": until,
                "batch_size": batch_size,
                **after,
            },
            conn=conn,
        )
        for row in page:
            try:
                async with conn.transaction():
                    mails, messages = await _remind(
                        conn, client_id, row["id"], facts, zone, customers, now
                    )
                queued += mails
                told += messages
                bookings += 1
            except Exception as exc:
                failed += 1
                logger.error(
                    "booking_reminder_queue_failed",
                    client_id=str(client_id),
                    booking_id=str(row["id"]),
                    error=type(exc).__name__,
                )
        if len(page) < batch_size:
            break
        after = {"after_starts": page[-1]["starts_at"], "after_id": page[-1]["id"]}
    return {"bookings": bookings, "queued": queued, "told": told, "failed": failed}


# --- sending the queued mail --------------------------------------------------------------------


@dataclass
class _Company:
    """What the mail for one company needs, read once per run."""

    facts: notify.MailFacts
    customers: bool  # customers may be reminded (see `customers_remindable`)
    replies: bool  # the bot would act on a reply (replies on, Customer support active)


async def _load_company(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> _Company:
    blocked, _ = await bot.gate(conn, client_id)
    return _Company(
        await notify.load_facts(conn, client_id),
        await customers_remindable(conn, client_id),
        blocked is None,
    )


async def _compose(
    conn: psycopg.AsyncConnection[Any],
    row: Mapping[str, Any],
    company: _Company,
    now: datetime,
) -> OutgoingMail | None:
    """The mail this claimed row owes now, or none if it is no longer owed (the row is dropped).

    Everything is read again here, as late as it can be: the booking's status and start, the
    company's gates, and the address (the contact's own today, or the person's on the booking).
    """
    client_id: UUID = row["client_id"]
    found = await service.read_records(conn, client_id, booking_id=row["booking_id"])
    if not found:
        return None
    booking, audience, address = found[0], row["audience"], row["address"]
    facts = company.facts
    if audience == "lapse":
        # The booking was cancelled when it lapsed; it never comes back.
        if booking.status != "cancelled" or booking.customer is None or not company.replies:
            return None
        to = notify.clean_address(notify.customer_address(booking))
        if to is None or to.lower() != address:
            return None
        return customer_lapsed_mail(
            to=to,
            name=booking.customer.full_name,
            brand=facts.brand,
            booking=booking,
            original=await customer_messages.the_original(conn, client_id, booking),
            tz=facts.timezone,
        )
    # A reminder is for a confirmed booking at the start it was queued for, and never after it.
    if booking.status != "confirmed" or booking.starts_at != row["starts_at"]:
        return None
    if booking.starts_at <= now:
        return None
    if audience == "customer":
        if booking.customer is None or booking.private or not company.customers:
            return None
        to = notify.clean_address(notify.customer_address(booking))
        if to is None or to.lower() != address:
            return None
        return reminder_mail(
            to=to,
            name=booking.customer.full_name,
            brand=facts.brand,
            booking=booking,
            tz=facts.timezone,
        )
    for one in notify.recipients(booking, facts, customer=False):
        if one.address.lower() == address:
            return reminder_mail(
                to=one.address, name=one.name, brand=facts.brand, booking=booking, tz=facts.timezone
            )
    return None


async def run_reminder_mail(
    conn: psycopg.AsyncConnection[Any],
    mailer: Mailer | None,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Send the queued email rows of every company; `conn` is the runner's owner connection.

    With no mailer, or mail switched off, nothing is claimed and the rows stay `queued`. Otherwise
    up to `batch_size` are claimed as `sending` in one transaction that commits before anything is
    sent. Each is then checked again (still confirmed at the same start, gates and address as they
    are now), sent outside any transaction, and settled as `sent`, `failed` or `dropped`. A failed
    send is not retried, and a row left `sending` by a dead worker is never sent. Sends run
    together, a few at a time, and the database steps take turns on the one connection.
    """
    stats = {"claimed": 0, "sent": 0, "failed": 0, "dropped": 0}
    if mailer is None or not mailer.enabled:
        return stats
    async with conn.transaction():
        claimed = await db.q.fetch_all(
            "bookings/reminders_claim.sql", {"batch_size": batch_size}, conn=conn
        )
    stats["claimed"] = len(claimed)
    turn = asyncio.Lock()
    gate = asyncio.Semaphore(notify.SIMULTANEOUS)
    companies: dict[UUID, _Company] = {}

    async def settle(row: Mapping[str, Any], status: str, now: datetime) -> None:
        try:
            async with turn, conn.transaction():
                await db.q.execute(
                    "bookings/reminder_settle.sql",
                    {
                        "client_id": row["client_id"],
                        "id": row["id"],
                        "status": status,
                        "sent_at": now if status == "sent" else None,
                    },
                    conn=conn,
                )
        except Exception as exc:
            logger.error("booking_reminder_settle_failed", error=type(exc).__name__)

    async def one(row: Mapping[str, Any]) -> str:
        status = "failed"
        try:
            now = clock.now()
            async with turn, conn.transaction():
                # The owner sees every company; the team read still insists the transaction is
                # this company's, as `csa.core.tenancy.tx` sets it: for this transaction only.
                await conn.execute(
                    "SELECT set_config('app.client_id', %s, true)", (str(row["client_id"]),)
                )
                company = companies.get(row["client_id"])
                if company is None:
                    company = companies[row["client_id"]] = await _load_company(
                        conn, row["client_id"]
                    )
                composed = await _compose(conn, row, company, now)
            if composed is None:
                status = "dropped"
            else:
                async with gate:
                    status = "sent" if await mailer.send(composed) == "sent" else "failed"
        except Exception as exc:
            logger.error("booking_reminder_mail_failed", category=type(exc).__name__)
        await settle(row, status, clock.now())
        return status

    for status in await asyncio.gather(*(one(row) for row in claimed)):
        stats[status] += 1
    return stats
