"""Scheduled work on bookings: the bot's pending bookings that ran out of time, and reminders.

Reminders are two jobs. `run_reminder_queue` runs per company, as that company, and writes what
is owed: one row in `booking_reminders` per recipient and start time, and for a customer on
Telegram the message itself, in the same transaction. `run_reminder_mail` runs once for everyone
and sends the queued email rows: the owner connection only lists which companies have some, and
each row is then claimed, checked and settled in its own company's transaction, as `csa_app`. A row
is claimed (`sending`) only when it has a send slot and committed before it is sent, so a worker
that dies mid-send leaves that row `sending` and it is never sent twice: a lost reminder is the
price of never sending one twice.

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
from psycopg_pool import AsyncConnectionPool

from csa.bookings import bot, customer_messages, notify, service, setup
from csa.console.mail_templates import customer_lapsed_mail, reminder_mail
from csa.console.mailer import Mailer, OutgoingMail
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import Clock

logger = structlog.get_logger(__name__)

Audience = Literal["customer", "staff", "lapse"]
# A lapse notice not sent within a day of the lapse is dropped, never sent late: by then "your
# request wasn't confirmed in time" is old news, and the customer may well have asked again.
LAPSE_NOTICE_LIFE = timedelta(hours=24)


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
    at: datetime | None = None,
) -> bool:
    """Make the row. True when it is new or was dropped and is queued again; false when this
    booking, start, audience and address already has one (sent, failed or waiting). `at` is when
    it was owed, by the job's clock (a lapse notice's age is measured from it); else now."""
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
            "created_at": at,
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
    sends it once and a worker that dies mid-send does not repeat it. One the mail job has not sent
    within `LAPSE_NOTICE_LIFE` is dropped instead.
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
                            conn,
                            client_id,
                            lapsed,
                            "lapse",
                            channel="email",
                            address=address,
                            at=clock.now(),
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
    are still reminded, by email only (`telegram_reminders`)."""
    has_package = await db.q.fetch_one(
        "console/service_package_any_in_status.sql",
        (client_id, [setup.REPLIES_PACKAGE], sorted(setup.REPLIES_STATUSES)),
        conn=conn,
    )
    if has_package is None:
        return True
    blocked, _ = await bot.gate(conn, client_id)
    return blocked is None


async def telegram_reminders(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> bool:
    """A customer may be reminded on Telegram only where Customer support is active and replies
    are not Off: that is the channel the company switched on. Without the package (none, or one
    still being asked for) a chat surviving from earlier is not a channel anyone chose."""
    blocked, _ = await bot.gate(conn, client_id)
    return blocked is None


async def _remind_customer(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    booking: service.BookingRecord,
    zone: ZoneInfo,
    now: datetime,
    telegram: bool,
) -> tuple[int, int]:
    """Queue the customer's one reminder for this start: `(emails queued, messages written)`.

    A customer on Telegram gets the message, in the Inbox thread as `reviewed` so a person's
    takeover does not hold it, when `telegram` allows it (`telegram_reminders`). Anyone else with
    an address gets an email row. A WhatsApp customer with no address gets nothing: nothing
    proactive goes there.
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
    if telegram and await customer_messages.telegram_message(
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
    telegram: bool,
    now: datetime,
) -> tuple[int, int]:
    """One booking's reminders. The booking is locked `FOR SHARE` before it is read, until the
    company's run commits: a move or cancel already holding it is waited for and then read as it
    stands, and one that comes after waits, so its own message follows the reminder. A reminder
    for the old time can never land after the "moved" message."""
    locked = await db.q.fetch_one(
        "bookings/booking_share_lock.sql", {"client_id": client_id, "id": booking_id}, conn=conn
    )
    if locked is None:
        return 0, 0
    found = await service.read_records(conn, client_id, booking_id=booking_id)
    if not found:
        return 0, 0
    booking = found[0]
    if booking.status != "confirmed" or booking.starts_at <= now or booking.late_for_reminder:
        return 0, 0
    queued = told = 0
    # A private meeting is for its attendees and guests; its customer hears nothing.
    if customers and booking.customer is not None and not booking.private:
        queued, told = await _remind_customer(conn, client_id, booking, zone, now, telegram)
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
    can run every five minutes. A booking whose current start was set after its reminder was due
    (made or moved inside the window) gets none, for anyone: its confirmation is enough
    (`service.late_for_reminder`).

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
    telegram = await telegram_reminders(conn, client_id)
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
                        conn, client_id, row["id"], facts, zone, customers, telegram, now
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
        # The booking was cancelled when it lapsed; it never comes back. A notice that waited
        # longer than `LAPSE_NOTICE_LIFE` (mail was off, say) would arrive as news it no longer is.
        if now - row["created_at"] > LAPSE_NOTICE_LIFE:
            return None
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
    # A reminder is for a confirmed booking at the start it was queued for, and never after it. A
    # booking moved away and back after its reminder was due gets none (`late_for_reminder`).
    if booking.status != "confirmed" or booking.starts_at != row["starts_at"]:
        return None
    if booking.late_for_reminder:
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


async def _settle(
    conn: psycopg.AsyncConnection[Any], row: Mapping[str, Any], status: str, now: datetime
) -> None:
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


async def _try_one(
    app_pool: AsyncConnectionPool, mailer: Mailer, clock: Clock, client_id: UUID, row_id: UUID
) -> str | None:
    """Claim one row, check it, send it, settle it. Returns how it ended, or none if another run
    had taken it. The caller holds a send slot, so every check is as close to the send as it can be.

    The claim and the checks are one short transaction in the company's own tenant scope; it
    commits before the send, so a row that is `sending` is never taken again, and the send itself
    runs with no transaction open. A row dropped or failed while composing is settled in that same
    transaction.
    """
    async with tx(app_pool, str(client_id)) as conn:
        row = await db.q.fetch_one(
            "bookings/reminder_claim_one.sql", {"client_id": client_id, "id": row_id}, conn=conn
        )
        if row is None:
            return None
        try:
            async with conn.transaction():
                composed = await _compose(
                    conn, row, await _load_company(conn, client_id), clock.now()
                )
        except Exception as exc:
            logger.error("booking_reminder_mail_failed", category=type(exc).__name__)
            await _settle(conn, row, "failed", clock.now())
            return "failed"
        if composed is None:
            await _settle(conn, row, "dropped", clock.now())
            return "dropped"
    try:
        status = "sent" if await mailer.send(composed) == "sent" else "failed"
    except Exception as exc:
        logger.error("booking_reminder_mail_failed", category=type(exc).__name__)
        status = "failed"
    try:
        async with tx(app_pool, str(client_id)) as conn:
            await _settle(conn, row, status, clock.now())
    except Exception as exc:
        logger.error("booking_reminder_settle_failed", category=type(exc).__name__)
    return status


async def run_reminder_mail(
    conn: psycopg.AsyncConnection[Any],
    app_pool: AsyncConnectionPool,
    mailer: Mailer | None,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Send the queued email rows of every company. `conn` is the runner's owner connection and is
    used only to list which companies have queued rows; everything else, for each company, runs
    through that company's own transaction on `app_pool` (as `csa_app`, row security on).

    With no mailer, or mail switched off, nothing is touched and the rows stay `queued`. Otherwise
    up to `batch_size` rows are worked on, a few at a time (`notify.SIMULTANEOUS`). A row is claimed
    as `sending` only when it has a send slot, in a short transaction that also checks it again
    (still confirmed at the same start and not begun, the company's gates, the address as it is
    now, the person still on the booking) and commits before the send. It is then settled `sent`,
    `failed` or `dropped`. A failed send is not retried. A row left `sending` by a dead worker is
    never sent, and a run cut short by its timeout leaves only the rows being sent that moment:
    rows still waiting for a slot are still `queued`.
    """
    stats = {"claimed": 0, "sent": 0, "failed": 0, "dropped": 0}
    if mailer is None or not mailer.enabled:
        return stats
    async with conn.transaction():
        companies = await db.q.fetch_all("bookings/reminders_companies.sql", conn=conn)
    work: list[tuple[UUID, UUID]] = []
    for company in companies:
        if len(work) >= batch_size:
            break
        async with tx(app_pool, str(company["client_id"])) as scoped:
            queued = await db.q.fetch_all(
                "bookings/reminders_queued.sql",
                {"client_id": company["client_id"], "batch_size": batch_size - len(work)},
                conn=scoped,
            )
        work += [(company["client_id"], row["id"]) for row in queued]
    gate = asyncio.Semaphore(notify.SIMULTANEOUS)

    async def one(client_id: UUID, row_id: UUID) -> str | None:
        async with gate:
            return await _try_one(app_pool, mailer, clock, client_id, row_id)

    for status in await asyncio.gather(*(one(client_id, row_id) for client_id, row_id in work)):
        if status is not None:
            stats["claimed"] += 1
            stats[status] += 1
    return stats
