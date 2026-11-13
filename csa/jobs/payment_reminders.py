"""The payment_reminder_queue job: reminders for open invoices, on Telegram, in an open WhatsApp
chat, or as an email row for the mail job; else a `no_channel` row for "Needs a nudge".

Steps are days from an invoice's due date (`payment_settings.reminder_days`, before is negative).
A step is owed once `due_at + step days <= now`, and each step is made once: one
`invoice_reminders` row per (invoice, step), `UNIQUE (client_id, invoice_id, step)`, and the chat
message key `invoice:{id}:reminder:{step}`. Only the newest owed step is ever sent; older owed
steps without a row (the worker was down, payments were just switched on) are recorded `skipped`,
never sent late. Nothing goes in quiet hours (21:00 to 09:00 in the company's time zone), and a
contact gets at most `DAILY_CAP` reminders a local day: a reminder over the cap writes no row and
is tried again on a later run, so it goes the next day.

The channel, in order: Telegram, when the company's gates allow an automatic message
(`csa.bookings.bot.gate`); a WhatsApp chat the customer wrote in within 23 hours, under the same
gate; an email row when the contact has an address (`contacts.email`, else the address given in a
chat) and `customers_remindable` holds; else `no_channel`. A chat message is written for the
delivery worker in the invoice's transaction, `reviewed`, as booking reminders are.

Logs carry categories and ids only: never an address, an amount, a name or a message.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool

from csa.bookings import bot
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.bookings import customers_remindable
from csa.jobs.clock import Clock
from csa.jobs.subscriptions import PACKAGE_KEYS, PACKAGE_STATUSES
from csa.payments import notices
from csa.payments.contracts import MethodOut
from csa.payments.copy import locale_of, reminder_text
from csa.payments.invoices import InvoiceRecord
from csa.payments.settings import PAYABLE_KINDS, get_settings, list_methods

logger = structlog.get_logger(__name__)

Conn = psycopg.AsyncConnection[Any]

QUIET_START = time(21, 0)
QUIET_END = time(9, 0)
DAILY_CAP = 1  # reminders sent or queued per contact per local day
MAX_PER_RUN = 500  # invoices worked on per company per run; the next run goes on
QUEUE_STATS: tuple[str, ...] = (
    "invoices",
    "sent",
    "queued",
    "no_channel",
    "skipped",
    "capped",
    "failed",
    "quiet",
)
# What a customer is reminded of: an invoice they can still pay. A claimed one (proof_submitted)
# waits for staff, and cash_pending is paid at a meeting (Payments 2).
REMINDABLE = frozenset({"issued", "overdue"})
_RECORD_FIELDS = tuple(f.name for f in fields(InvoiceRecord))


@dataclass(frozen=True)
class _Company:
    """What every reminder of one run needs, read once."""

    steps: list[int]
    brand: str
    zone: ZoneInfo
    methods: list[MethodOut]  # active QR and transfer methods, in the company's order
    chats: bool  # an automatic message may go (bot.gate): Telegram and open WhatsApp chats
    email: bool  # customers may be mailed (customers_remindable)


class _AlreadyDone(Exception):
    """The step got its row meanwhile: undo what this attempt wrote."""


def is_quiet(now: datetime, zone: ZoneInfo) -> bool:
    """21:00 to 09:00 in the company's time zone."""
    local = now.astimezone(zone).time()
    return local >= QUIET_START or local < QUIET_END


def _local_day(now: datetime, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The company's local day holding `now`, as [start, end)."""
    day = now.astimezone(zone).date()
    start = datetime.combine(day, time(0), tzinfo=zone)
    return start, datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone)


async def _load_company(conn: Conn, client_id: UUID) -> _Company | None:
    """The run's settings and gates; None when the company's payments do nothing (the
    subscriptions package is not active, or payments are not switched on)."""
    package = await db.q.fetch_one(
        "console/service_package_any_in_status.sql",
        (client_id, PACKAGE_KEYS, PACKAGE_STATUSES),
        conn=conn,
    )
    if package is None:
        return None
    settings = await get_settings(conn, client_id)
    if not settings.enabled:
        return None
    # The name customers know the company by and its time zone, as booking reminders read them.
    facts = await db.q.fetch_one("bookings/mail_facts.sql", {"client_id": client_id}, conn=conn)
    if facts is None:
        raise RuntimeError("company not found")
    methods = await list_methods(conn, client_id, active_only=True)
    blocked, _ = await bot.gate(conn, client_id)
    return _Company(
        steps=list(settings.reminder_days),
        brand=facts["brand"],
        zone=ZoneInfo(facts["timezone"]),
        methods=[m for m in methods if m.kind in PAYABLE_KINDS],
        chats=blocked is None,
        email=await customers_remindable(conn, client_id),
    )


async def _row(
    conn: Conn,
    client_id: UUID,
    invoice_id: str,
    step: int,
    now: datetime,
    *,
    channel: str,
    status: str,
    address: str | None = None,
) -> bool:
    """Write the step's row; false when the step already has one."""
    made = await db.q.fetch_one(
        "payments/reminder_insert.sql",
        {
            "client_id": client_id,
            "invoice_id": invoice_id,
            "step": step,
            "channel": channel,
            "address": address,
            "status": status,
            "created_at": now,
            "sent_at": now if status == "sent" else None,
        },
        conn=conn,
    )
    return made is not None


async def _remind(
    conn: Conn,
    client_id: UUID,
    due: dict[str, Any],
    company: _Company,
    now: datetime,
) -> dict[str, int]:
    """One invoice's reminder, in its transaction; returns what it did (counts by QUEUE_STATS)."""
    done: dict[str, int] = {}
    invoice_id: str = due["id"]
    key = {"client_id": client_id, "invoice_id": invoice_id}
    # LOCK ORDER (invoice_lock.sql): the subscription first, then the invoice.
    if due["subscription_id"] is not None:
        await db.q.fetch_one(
            "subscriptions/subscription_lock.sql",
            {"client_id": client_id, "subscription_id": due["subscription_id"]},
            conn=conn,
        )
    locked = await db.q.fetch_one("payments/invoice_lock.sql", key, conn=conn)
    # Paid, voided or claimed since the page was read, or moved off its contact: nothing is owed.
    if locked is None or locked["status"] not in REMINDABLE or locked["contact_id"] is None:
        return done
    contact = await db.q.fetch_one(
        "payments/reminder_contact.sql",
        {"client_id": client_id, "contact_id": locked["contact_id"]},
        conn=conn,
    )
    if contact is None:
        return done
    for step in due["older"]:
        if await _row(conn, client_id, invoice_id, step, now, channel="none", status="skipped"):
            done["skipped"] = done.get("skipped", 0) + 1
    if not due["newest_open"]:
        return done
    day_start, day_end = _local_day(now, company.zone)
    today = await db.q.fetch_one(
        "payments/reminders_today.sql",
        {
            "client_id": client_id,
            "contact_id": contact["id"],
            "day_start": day_start,
            "day_end": day_end,
        },
        conn=conn,
    )
    if today is not None and today["reminders"] >= DAILY_CAP:
        done["capped"] = 1
        return done

    step: int = due["newest"]
    invoice = InvoiceRecord(**{name: locked[name] for name in _RECORD_FIELDS})
    text = reminder_text(
        invoice,
        company.methods,
        step=step,
        brand=company.brand,
        locale=locale_of(contact["locale"]),
        tz=company.zone,
    )
    message_key = f"invoice:{invoice_id}:reminder:{step}"
    try:
        async with conn.transaction():
            address = None
            if company.chats and await notices.chat_message(
                conn, client_id, contact["id"], channel="telegram", key=message_key, body=text
            ):
                channel, status = "telegram", "sent"
            elif company.chats and await notices.chat_message(
                conn,
                client_id,
                contact["id"],
                channel="whatsapp",
                key=message_key,
                body=text,
                now=now,
            ):
                channel, status = "whatsapp", "sent"
            elif company.email and (
                address := notices.reminder_address(
                    contact["email"], contact["customer_given_email"]
                )
            ):
                channel, status = "email", "queued"
            else:
                channel, status = "none", "no_channel"
            made = await _row(
                conn, client_id, invoice_id, step, now,
                channel=channel, status=status, address=address,
            )  # fmt: skip
            if not made:
                raise _AlreadyDone
    except _AlreadyDone:
        return done
    if status in ("sent", "queued"):
        await db.q.execute("payments/invoice_reminded.sql", {**key, "now": now}, conn=conn)
    done[status] = 1
    return done


async def run_payment_reminder_queue(
    app_pool: AsyncConnectionPool,
    client_id: UUID,
    clock: Clock,
    *,
    batch_size: int = 100,
) -> dict[str, int]:
    """Queue the payment reminders owed for one company's open invoices.

    Every transaction is the company's own, on `app_pool` (`Job.own_transactions`). The settings
    and gates are read once; nothing happens unless the subscriptions package is active and
    payments are switched on, and in quiet hours the run returns `quiet: 1` and writes nothing.
    Then invoices owed a step are read a page of `batch_size` at a time (`reminders_due.sql`, by
    due date), up to `MAX_PER_RUN`; each is worked on in its own transaction: the subscription
    and then the invoice are locked and the invoice read again (still issued or overdue, still
    with its contact), older owed steps recorded `skipped`, the daily cap checked, and the newest
    step sent or queued on the first channel that has a route. `invoices.reminded_at` is set when
    a message is written or an email row queued. An invoice that fails is undone, logged
    (`payment_reminder_queue_failed`, the error class only) and counted, and the rest go on;
    the next run tries it again.
    """
    stats: dict[str, int] = dict.fromkeys(QUEUE_STATS, 0)
    now = clock.now()
    async with tx(app_pool, str(client_id)) as conn:
        company = await _load_company(conn, client_id)
    if company is None:
        return stats
    if is_quiet(now, company.zone):
        stats["quiet"] = 1
        return stats
    after: dict[str, Any] = {"after_due": None, "after_id": None}
    seen = 0
    while seen < MAX_PER_RUN:
        limit = min(batch_size, MAX_PER_RUN - seen)
        async with tx(app_pool, str(client_id)) as conn:
            page = await db.q.fetch_all(
                "payments/reminders_due.sql",
                {
                    "client_id": client_id,
                    "now": now,
                    "steps": company.steps,
                    "batch_size": limit,
                    **after,
                },
                conn=conn,
            )
        for due in page:
            seen += 1
            try:
                async with tx(app_pool, str(client_id)) as conn:
                    done = await _remind(conn, client_id, due, company, now)
            except Exception as exc:  # one bad invoice must not stop the others
                stats["failed"] += 1
                # The class only: an error message can carry an address, an amount or a name.
                logger.error(
                    "payment_reminder_queue_failed",
                    client_id=str(client_id),
                    invoice_id=due["id"],
                    error_class=type(exc).__name__,
                )
                continue
            stats["invoices"] += 1
            for name, count in done.items():
                stats[name] += count
        if len(page) < limit:
            break
        after = {"after_due": page[-1]["due_at"], "after_id": page[-1]["id"]}
    return stats
