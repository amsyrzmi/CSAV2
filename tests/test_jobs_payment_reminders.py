"""Payments 1, task 6: the payment reminder queue (Telegram, open WhatsApp chats, email rows).

Real database, synthetic data only. Rows are seeded as the owner; the job runs as the company, on
its own transactions (`own_transactions`), with a fixed clock. 02:00 UTC is 10:00 in Kuala Lumpur.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from csa.core import db
from csa.jobs.clock import FakeClock
from csa.jobs.payment_reminders import (
    DAILY_CAP,
    MAX_PER_RUN,
    QUEUE_STATS,
    QUIET_END,
    QUIET_START,
    run_payment_reminder_queue,
)
from csa.payments import notices
from csa.payments.reference import new_invoice_id, new_reference
from psycopg.types.json import Jsonb
from structlog.testing import capture_logs

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]

NOW = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)  # Friday 10:00 in Kuala Lumpur
DUE = NOW - timedelta(hours=1)  # step 0 is owed, and step -7 before it
ACCOUNT = "512000000034"
BRAND = "Kedai Contoh"
ZERO = dict.fromkeys(QUEUE_STATS, 0)
TRANSFER = {"bank_name": "Bank Contoh", "account_number": ACCOUNT, "account_name": "Kedai Contoh"}
QR = {"duitnow_id": "DN-5120000056", "account_name": "Kedai Contoh Sdn Bhd"}


def body(ref: str, first: str = "is due today.") -> str:
    """The English reminder for a company with only the transfer method."""
    return (
        f"{BRAND}: invoice {ref} for MYR 1,059.90 {first}\n"
        f"Bank transfer: Bank Contoh, account {ACCOUNT}, name Kedai Contoh.\n"
        f"Please put {ref} as the payment reference so we can match your payment.\n"
        "If you've already paid, reply here and tell us when."
    )


def owner(pg_container: object) -> Any:
    return psycopg.AsyncConnection.connect(owner_dsn_for(pg_container), autocommit=True)


async def seed_company(
    conn: psycopg.AsyncConnection[Any],
    *,
    package: str | None = "active",
    payments: bool | None = True,
    support: str | None = "active",
    mode: str | None = None,
    reminder_days: list[int] | None = None,
    methods: tuple[tuple[str, dict[str, Any], bool, int], ...] = (("transfer", TRANSFER, True, 0),),
) -> uuid.UUID:
    """A company: `package` is the subscriptions package status (None: none), `payments` the
    settings switch (None: no settings row), `support` the Customer support package status."""
    client = uuid.uuid4()
    config = Jsonb({} if mode is None else {"policy": {"deployment_mode": mode}})
    await conn.execute(
        "INSERT INTO clients(id,slug,name,brand_name,status,platform_version,config) "
        "VALUES (%s,%s,'Synthetic Reminders Sdn Bhd',%s,'active','p1-test',%s)",
        (client, f"pay-rem-{client}", BRAND, config),
    )
    for key, status in (("subscriptions", package), ("customer_support", support)):
        if status is not None:
            await conn.execute(
                "INSERT INTO console_service_packages(client_id,package,status,activated_at,"
                "reference) VALUES (%s,%s,%s,now(),'p1-test')",
                (client, key, status),
            )
    if payments is not None:
        await conn.execute(
            "INSERT INTO payment_settings (client_id, enabled, reminder_days) VALUES (%s,%s,%s)",
            (client, payments, reminder_days or [-7, 0, 3, 7]),
        )
    for kind, details, active, order in methods:
        await conn.execute(
            "INSERT INTO payment_methods (client_id, kind, label, details, is_active, sort_order)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (client, kind, kind.title(), Jsonb(details), active, order),
        )
    return client


async def contact(
    conn: psycopg.AsyncConnection[Any],
    client: uuid.UUID,
    *,
    email: str | None = None,
    given: str | None = None,
    locale: str = "en",
) -> uuid.UUID:
    person = uuid.uuid4()
    await conn.execute(
        "INSERT INTO contacts (id, client_id, full_name, email, customer_given_email, locale)"
        " VALUES (%s,%s,'Synthetic Customer',%s,%s,%s)",
        (person, client, email, given, locale),
    )
    return person


async def chat(
    conn: psycopg.AsyncConnection[Any],
    client: uuid.UUID,
    person: uuid.UUID,
    *,
    channel: str = "telegram",
    at: datetime = NOW - timedelta(hours=2),
) -> uuid.UUID:
    """The rows a customer's own message leaves: a chat, its route, the message that came in."""
    credential, endpoint, conversation, trace = (uuid.uuid4() for _ in range(4))
    # One active Telegram credential per company (0494): later chats sit on disconnected ones.
    await conn.execute(
        "INSERT INTO channel_credentials (id, client_id, channel, shop_id, disconnected_at)"
        " VALUES (%s, %s, %s, %s, CASE WHEN %s AND EXISTS (SELECT 1 FROM channel_credentials"
        " WHERE client_id = %s AND channel = 'telegram' AND disconnected_at IS NULL)"
        " THEN now() END)",
        (
            credential,
            client,
            channel,
            None if channel == "telegram" else uuid.uuid4().hex,
            channel == "telegram",
            client,
        ),
    )
    await conn.execute(
        "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id, is_enabled)"
        " VALUES (%s, %s, %s, %s, true)",
        (endpoint, client, uuid.uuid4().hex, credential),
    )
    await conn.execute(
        "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread)"
        " VALUES (%s, %s, %s, %s, %s)",
        (conversation, client, person, channel, str(uuid.uuid4().int % 10**9)),
    )
    await conn.execute(
        "INSERT INTO ingress_events (client_id, endpoint_id, contact_id, idempotency_key,"
        " payload_hash, trace_id, raw) VALUES (%s, %s, %s, %s, 'x', %s, '{}'::jsonb)",
        (client, endpoint, person, f"ing:{trace}", trace),
    )
    await conn.execute(
        "INSERT INTO messages (client_id, conversation_id, direction, author, body, redacted_body,"
        " idempotency_key, trace_id, created_at)"
        " VALUES (%s, %s, 'inbound', 'customer', 'hello', 'hello', %s, %s, %s)",
        (client, conversation, f"in:{trace}", trace, at),
    )
    return conversation


async def invoice(
    conn: psycopg.AsyncConnection[Any],
    client: uuid.UUID,
    person: uuid.UUID | None,
    *,
    status: str = "issued",
    due_at: datetime = DUE,
    subscription: uuid.UUID | None = None,
    reference: bool = True,
) -> str:
    invoice_id = new_invoice_id(NOW)
    await conn.execute(
        "INSERT INTO invoices (id, client_id, contact_id, subscription_id, reference, status,"
        " amount, currency, issued_at, due_at, line_items)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,'MYR',%s,%s,%s)",
        (
            invoice_id,
            client,
            person,
            subscription,
            new_reference() if reference else None,
            status,
            Decimal("1059.90"),
            NOW - timedelta(days=10),
            due_at,
            Jsonb([{"description": "Pro Monthly"}]),
        ),
    )
    return invoice_id


async def reference_of(conn: psycopg.AsyncConnection[Any], invoice_id: str) -> str:
    row = await (
        await conn.execute("SELECT reference FROM invoices WHERE id=%s", (invoice_id,))
    ).fetchone()
    assert row is not None
    return str(row[0])


async def reminded_at(conn: psycopg.AsyncConnection[Any], invoice_id: str) -> datetime | None:
    cursor = await conn.execute("SELECT reminded_at FROM invoices WHERE id=%s", (invoice_id,))
    row = await cursor.fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def reminders(conn: psycopg.AsyncConnection[Any], client: uuid.UUID) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT invoice_id, step, channel, address, status, created_at, sent_at"
        " FROM invoice_reminders WHERE client_id=%s ORDER BY invoice_id, step",
        (client,),
    )
    keys = ["invoice_id", "step", "channel", "address", "status", "created_at", "sent_at"]
    return [dict(zip(keys, row, strict=True)) for row in await cursor.fetchall()]


def shape(rows: list[dict[str, Any]]) -> set[tuple[str, int, str, str | None, str]]:
    return {(r["invoice_id"], r["step"], r["channel"], r["address"], r["status"]) for r in rows}


async def outbound(conn: psycopg.AsyncConnection[Any], client: uuid.UUID) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT m.body, m.idempotency_key, m.send_authority, m.conversation_id, m.author,"
        " (SELECT count(*) FROM outbound_deliveries d WHERE d.message_id = m.id) AS deliveries"
        " FROM messages m WHERE m.client_id=%s AND m.direction='outbound' ORDER BY m.created_at",
        (client,),
    )
    keys = ["body", "key", "authority", "conversation", "author", "deliveries"]
    return [dict(zip(keys, row, strict=True)) for row in await cursor.fetchall()]


async def run(
    app_dsn: str, client: uuid.UUID, now: datetime = NOW, **kwargs: Any
) -> dict[str, int]:
    async with db.build_pool(app_dsn, max_size=1, autocommit=True) as pool:
        return await run_payment_reminder_queue(pool, client, FakeClock(now), **kwargs)


# --- constants ----------------------------------------------------------------------------------


async def test_the_constants() -> None:
    assert (QUIET_START.hour, QUIET_START.minute, QUIET_END.hour, QUIET_END.minute) == (21, 0, 9, 0)
    assert DAILY_CAP == 1 and MAX_PER_RUN == 500
    assert QUEUE_STATS == (
        "invoices", "sent", "queued", "no_channel", "skipped", "capped", "failed", "quiet",
    )  # fmt: skip


# --- who is reminded, and where -----------------------------------------------------------------


async def test_a_telegram_customer_gets_one_message_and_a_sent_row(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client, email="also@example.test")
        await conn.execute(
            "INSERT INTO plans(client_id,code,name,price,billing_interval,is_active) "
            "VALUES (%s,'pro','Pro Monthly',1059.90,'month',true)",
            (client,),
        )
        sub = uuid.uuid4()
        await conn.execute(
            "INSERT INTO subscriptions(id,client_id,contact_id,plan,status,price,started_at,"
            "renews_at) VALUES (%s,%s,%s,'pro','active',1059.90,%s,%s)",
            (sub, client, person, NOW - timedelta(days=40), NOW + timedelta(days=20)),
        )
        conversation = await chat(conn, client, person)
        inv = await invoice(conn, client, person, subscription=sub)
        ref = await reference_of(conn, inv)

        assert await run(app_dsn, client) == ZERO | {"invoices": 1, "sent": 1, "skipped": 1}
        (message,) = await outbound(conn, client)
        assert message["body"] == body(ref)
        assert message["key"] == f"invoice:{inv}:reminder:0"
        assert message["authority"] == "reviewed" and message["author"] == "assistant"
        assert message["conversation"] == conversation and message["deliveries"] == 1
        rows = await reminders(conn, client)
        assert shape(rows) == {
            (inv, -7, "none", None, "skipped"),
            (inv, 0, "telegram", None, "sent"),
        }
        sent = next(r for r in rows if r["step"] == 0)
        assert sent["sent_at"] == NOW and sent["created_at"] == NOW
        assert await reminded_at(conn, inv) == NOW

        # A second run, and one later the same day, write nothing more.
        assert await run(app_dsn, client) == ZERO
        assert await run(app_dsn, client, NOW + timedelta(hours=3)) == ZERO
        assert len(await outbound(conn, client)) == 1 and len(await reminders(conn, client)) == 2


async def test_whatsapp_inside_the_window_gets_a_message_in_the_open_chat(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client, email="wa@example.test")
        await chat(conn, client, person, channel="whatsapp", at=NOW - timedelta(hours=30))
        recent = await chat(conn, client, person, channel="whatsapp", at=NOW - timedelta(hours=2))
        inv = await invoice(conn, client, person)

        assert await run(app_dsn, client) == ZERO | {"invoices": 1, "sent": 1, "skipped": 1}
        (message,) = await outbound(conn, client)
        assert message["conversation"] == recent and message["deliveries"] == 1
        assert message["authority"] == "reviewed"
        assert message["body"] == body(await reference_of(conn, inv))
        assert (inv, 0, "whatsapp", None, "sent") in shape(await reminders(conn, client))


async def test_whatsapp_outside_the_window_falls_back_to_email_else_no_channel(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        mailed = await contact(conn, client, email="Siti.Contoh@Example.TEST")
        given = await contact(conn, client, given="given@example.test")
        bare = await contact(conn, client)
        for person in (mailed, given, bare):
            await chat(conn, client, person, channel="whatsapp", at=NOW - timedelta(hours=30))
        inv_mailed = await invoice(conn, client, mailed)
        inv_given = await invoice(conn, client, given)
        inv_bare = await invoice(conn, client, bare)

        assert await run(app_dsn, client) == ZERO | {
            "invoices": 3,
            "queued": 2,
            "no_channel": 1,
            "skipped": 3,
        }
        assert await outbound(conn, client) == []
        rows = [r for r in await reminders(conn, client) if r["step"] == 0]
        assert shape(rows) == {
            (inv_mailed, 0, "email", "siti.contoh@example.test", "queued"),
            (inv_given, 0, "email", "given@example.test", "queued"),
            (inv_bare, 0, "none", None, "no_channel"),
        }
        assert all(r["sent_at"] is None for r in rows)
        assert await reminded_at(conn, inv_mailed) == NOW
        assert await reminded_at(conn, inv_bare) is None


async def test_a_customer_with_no_channel_gets_a_no_channel_row_and_no_message(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client, email="not-an-address")
        inv = await invoice(conn, client, person)

        stats = await run(app_dsn, client)
        assert stats == ZERO | {"invoices": 1, "no_channel": 1, "skipped": 1}
        assert await outbound(conn, client) == []
        assert (inv, 0, "none", None, "no_channel") in shape(await reminders(conn, client))
        assert await reminded_at(conn, inv) is None
        assert await run(app_dsn, client) == ZERO  # the row stands: nothing is retried


async def test_paid_void_and_claimed_invoices_and_those_without_a_contact_get_nothing(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client)
        await chat(conn, client, person)
        for status in ("paid", "void", "proof_submitted", "cash_pending", "draft"):
            await invoice(conn, client, person, status=status)
        await invoice(conn, client, None)  # no contact
        await invoice(conn, client, person, reference=False)  # an old invoice with no reference
        await invoice(conn, client, person, due_at=NOW + timedelta(days=8))  # nothing owed yet

        assert await run(app_dsn, client) == ZERO
        assert await reminders(conn, client) == [] and await outbound(conn, client) == []


async def test_after_downtime_only_the_newest_owed_step_is_sent(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client)
        await chat(conn, client, person)
        inv = await invoice(conn, client, person, status="overdue", due_at=NOW - timedelta(days=4))
        ref = await reference_of(conn, inv)

        assert await run(app_dsn, client) == ZERO | {"invoices": 1, "sent": 1, "skipped": 2}
        assert shape(await reminders(conn, client)) == {
            (inv, -7, "none", None, "skipped"),
            (inv, 0, "none", None, "skipped"),
            (inv, 3, "telegram", None, "sent"),
        }
        (message,) = await outbound(conn, client)
        assert message["key"] == f"invoice:{inv}:reminder:3"
        assert message["body"] == body(ref, "was due on 16 November 2026 and is still unpaid.")


async def test_a_step_zero_reminder_held_to_the_next_local_day_says_it_was_due(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client)
        await chat(conn, client, person)
        # Due 22:30 local on 20 November: step 0 falls in quiet hours and waits for the morning.
        inv = await invoice(conn, client, person, due_at=datetime(2026, 11, 20, 14, 30, tzinfo=UTC))
        ref = await reference_of(conn, inv)

        assert await run(app_dsn, client, datetime(2026, 11, 20, 15, 0, tzinfo=UTC)) == ZERO | {
            "quiet": 1
        }
        morning = datetime(2026, 11, 21, 1, 0, tzinfo=UTC)  # 09:00 local on 21 November
        assert await run(app_dsn, client, morning) == ZERO | {
            "invoices": 1,
            "sent": 1,
            "skipped": 1,
        }
        (message,) = await outbound(conn, client)
        assert message["key"] == f"invoice:{inv}:reminder:0"
        assert message["body"] == body(ref, "was due on 20 November 2026 and is still unpaid.")
        assert "today" not in message["body"]


async def test_a_step_before_the_due_date_names_the_date(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client)
        await chat(conn, client, person)
        # Due 16:30 UTC on 26 November: 00:30 on 27 November in Kuala Lumpur.
        inv = await invoice(conn, client, person, due_at=datetime(2026, 11, 26, 16, 30, tzinfo=UTC))

        assert await run(app_dsn, client) == ZERO | {"invoices": 1, "sent": 1}
        (message,) = await outbound(conn, client)
        assert message["body"] == body(await reference_of(conn, inv), "is due on 27 November 2026.")


async def test_one_reminder_per_contact_per_local_day(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client)
        await chat(conn, client, person)
        # The page is read by due date, then id: the lower id goes first.
        first, second = sorted([await invoice(conn, client, person) for _ in range(2)])

        stats = await run(app_dsn, client)
        assert stats == ZERO | {"invoices": 2, "sent": 1, "skipped": 2, "capped": 1}
        assert len(await outbound(conn, client)) == 1
        # The capped invoice has no row for its step, so a later run tries it again.
        assert {(r["invoice_id"], r["step"]) for r in await reminders(conn, client)} == {
            (first, -7),
            (first, 0),
            (second, -7),
        }
        assert await run(app_dsn, client, NOW + timedelta(hours=10)) == ZERO | {
            "invoices": 1,
            "capped": 1,
        }  # 20:00 local, still the same day

        # The next local day it goes.
        assert await run(app_dsn, client, NOW + timedelta(days=1)) == ZERO | {
            "invoices": 1,
            "sent": 1,
        }
        sent = await outbound(conn, client)
        assert [m["key"] for m in sent] == [
            f"invoice:{first}:reminder:0",
            f"invoice:{second}:reminder:0",
        ]
        # The first said "today"; the one the cap held back a day says it was due yesterday.
        assert "is due today." in sent[0]["body"]
        assert "was due on 20 November 2026 and is still unpaid." in sent[1]["body"]
        assert await reminded_at(conn, second) == NOW + timedelta(days=1)


async def test_skipped_and_no_channel_rows_do_not_use_the_daily_cap(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client)  # no chat, no address
        invoices = [await invoice(conn, client, person) for _ in range(3)]

        assert await run(app_dsn, client) == ZERO | {"invoices": 3, "no_channel": 3, "skipped": 3}
        assert {r["invoice_id"] for r in await reminders(conn, client)} == set(invoices)


@pytest.mark.parametrize(
    "local",
    [datetime(2026, 11, 20, 22, 0), datetime(2026, 11, 20, 8, 59), datetime(2026, 11, 20, 21, 0)],
)
async def test_quiet_hours_do_nothing(pg_container: object, app_dsn: str, local: datetime) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client, email="quiet@example.test")
        await chat(conn, client, person)
        await invoice(conn, client, person, due_at=NOW - timedelta(days=4))
        now = (local - timedelta(hours=8)).replace(tzinfo=UTC)  # Kuala Lumpur is UTC+8

        assert await run(app_dsn, client, now) == ZERO | {"quiet": 1}
        assert await reminders(conn, client) == [] and await outbound(conn, client) == []
        # 09:00 local the same morning (or the next) it goes.
        morning = datetime(2026, 11, 21 if local.hour >= 21 else 20, 1, 0, tzinfo=UTC)
        assert (await run(app_dsn, client, morning))["sent"] == 1


async def test_replies_off_writes_no_chat_message_and_no_email(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, mode="shadow")
        person = await contact(conn, client, email="off@example.test")
        await chat(conn, client, person)
        await chat(conn, client, person, channel="whatsapp")
        inv = await invoice(conn, client, person)

        assert await run(app_dsn, client) == ZERO | {"invoices": 1, "no_channel": 1, "skipped": 1}
        assert await outbound(conn, client) == []
        assert (inv, 0, "none", None, "no_channel") in shape(await reminders(conn, client))


async def test_without_customer_support_no_chat_message_but_the_email_is_queued(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        person = await contact(conn, client, email="mail@example.test")
        await chat(conn, client, person)
        inv = await invoice(conn, client, person)

        assert await run(app_dsn, client) == ZERO | {"invoices": 1, "queued": 1, "skipped": 1}
        assert await outbound(conn, client) == []
        assert (inv, 0, "email", "mail@example.test", "queued") in shape(
            await reminders(conn, client)
        )


@pytest.mark.parametrize(
    ("package", "payments"),
    [("active", False), ("active", None), ("suspended", True), (None, True)],
)
async def test_payments_off_or_package_inactive_does_nothing(
    pg_container: object, app_dsn: str, package: str | None, payments: bool | None
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, package=package, payments=payments)
        person = await contact(conn, client, email="x@example.test")
        await chat(conn, client, person)
        await invoice(conn, client, person)

        assert await run(app_dsn, client) == ZERO
        # Payments off is checked before quiet hours: nothing is said about the hour either.
        assert await run(app_dsn, client, NOW + timedelta(hours=12)) == ZERO
        assert await reminders(conn, client) == [] and await outbound(conn, client) == []


async def test_a_run_touches_only_its_own_company(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        mine = await seed_company(conn)
        other = await seed_company(conn)
        for client in (mine, other):
            person = await contact(conn, client)
            await chat(conn, client, person)
            await invoice(conn, client, person)

        assert (await run(app_dsn, mine))["sent"] == 1
        assert len(await reminders(conn, mine)) == 2 and len(await outbound(conn, mine)) == 1
        assert await reminders(conn, other) == [] and await outbound(conn, other) == []


async def test_a_run_stops_at_max_per_run_and_the_next_continues(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, reminder_days=[0])
        person = await contact(conn, client)
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO invoices (id, client_id, contact_id, reference, status, amount,"
                " currency, issued_at, due_at) VALUES (%s,%s,%s,%s,'issued',10,'MYR',%s,%s)",
                [
                    (new_invoice_id(NOW), client, person, new_reference(), NOW, DUE)
                    for _ in range(600)
                ],
            )

        assert await run(app_dsn, client, batch_size=100) == ZERO | {
            "invoices": 500,
            "no_channel": 500,
        }
        assert await run(app_dsn, client, batch_size=100) == ZERO | {
            "invoices": 100,
            "no_channel": 100,
        }
        assert len(await reminders(conn, client)) == 600


# --- the copy -----------------------------------------------------------------------------------


async def test_a_malay_contact_gets_the_malay_reminder(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        person = await contact(conn, client, locale="ms-MY")
        await chat(conn, client, person)
        inv = await invoice(conn, client, person)
        ref = await reference_of(conn, inv)

        await run(app_dsn, client)
        (message,) = await outbound(conn, client)
        assert message["body"] == (
            f"{BRAND}: invois {ref} untuk MYR 1,059.90 kena bayar hari ini.\n"
            f"Pindahan bank: Bank Contoh, akaun {ACCOUNT}, nama Kedai Contoh.\n"
            f"Letak {ref} sebagai rujukan bayaran ya, senang kami padankan.\n"
            "Kalau dah bayar, balas sini dan bagitahu bila ya."
        )


async def test_only_active_qr_and_transfer_methods_are_listed_in_order(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(
            conn,
            methods=(
                ("transfer", TRANSFER, True, 5),
                ("qr", QR, True, 1),
                ("cash", {}, True, 0),
                ("transfer", TRANSFER | {"account_number": "512000000078"}, False, 2),
            ),
        )
        person = await contact(conn, client)
        await chat(conn, client, person)
        inv = await invoice(conn, client, person)
        ref = await reference_of(conn, inv)

        await run(app_dsn, client)
        (message,) = await outbound(conn, client)
        assert message["body"].split("\n")[1:3] == [
            "DuitNow: DN-5120000056, name Kedai Contoh Sdn Bhd.",
            f"Bank transfer: Bank Contoh, account {ACCOUNT}, name Kedai Contoh.",
        ]
        assert "512000000078" not in message["body"] and "Cash" not in message["body"]
        assert message["body"].count(ref) == 2


async def test_with_no_payable_method_the_reminder_still_names_the_reference(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, methods=(("cash", {}, True, 0),))
        person = await contact(conn, client)
        await chat(conn, client, person)
        inv = await invoice(conn, client, person)
        ref = await reference_of(conn, inv)

        await run(app_dsn, client)
        (message,) = await outbound(conn, client)
        assert message["body"] == (
            f"{BRAND}: invoice {ref} for MYR 1,059.90 is due today.\n"
            f"Please put {ref} as the payment reference so we can match your payment.\n"
            "If you've already paid, reply here and tell us when."
        )


# --- failures -----------------------------------------------------------------------------------


async def test_one_failing_invoice_is_logged_by_class_and_the_rest_go(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn)
        people = [await contact(conn, client) for _ in range(2)]
        for person in people:
            await chat(conn, client, person)
        broken = await invoice(conn, client, people[0])
        fine = await invoice(conn, client, people[1])
        real = notices.chat_message

        async def flaky(*args: Any, **kwargs: Any) -> bool:
            if kwargs["key"].startswith(f"invoice:{broken}:"):
                raise RuntimeError(f"boom {ACCOUNT} siti@example.test")
            return await real(*args, **kwargs)

        monkeypatch.setattr(notices, "chat_message", flaky)
        with capture_logs() as logs:
            stats = await run(app_dsn, client)
        assert stats == ZERO | {"invoices": 1, "sent": 1, "skipped": 1, "failed": 1}
        (failure,) = [e for e in logs if e["event"] == "payment_reminder_queue_failed"]
        assert failure["error_class"] == "RuntimeError"
        assert failure["invoice_id"] == broken and failure["client_id"] == str(client)
        assert ACCOUNT not in json.dumps(logs, default=str)
        assert "siti@example.test" not in json.dumps(logs, default=str)
        # The failed invoice's transaction was undone, its skipped row with it.
        assert {r["invoice_id"] for r in await reminders(conn, client)} == {fine}

        monkeypatch.setattr(notices, "chat_message", real)
        assert (await run(app_dsn, client))["sent"] == 1  # retried on the next run
