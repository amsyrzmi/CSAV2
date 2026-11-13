"""Payments 1, task 7: the mail job that sends queued payment reminder email rows.

Real database, synthetic data only, a fake relay. Rows are seeded as the owner; the job lists
companies on an owner connection and claims, checks and settles each row as the company, on the
app pool, with a fixed clock. 02:00 UTC is 10:00 in Kuala Lumpur.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from csa.console.mail_templates import payment_reminder_mail
from csa.console.mailer import Mailer
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs import payment_reminders
from csa.jobs.bookings import LOST_AFTER, MAIL_STATS
from csa.jobs.clock import FakeClock
from csa.jobs.payment_reminders import (
    STALE_AFTER,
    run_payment_reminder_mail,
    run_payment_reminder_queue,
)
from structlog.testing import capture_logs

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_bookings_mail import SENDER, FakeTransport
from tests.test_jobs_payment_reminders import (
    ACCOUNT,
    BRAND,
    DUE,
    NOW,
    contact,
    invoice,
    owner,
    reference_of,
    seed_company,
)

pytestmark = [requires_docker, pytest.mark.asyncio]

ZERO = dict.fromkeys(MAIL_STATS, 0)
STAFF_NOTE = "STAFF-ONLY claim note 77"
VOID_REASON = "STAFF-ONLY void reason 88"


@pytest.fixture(autouse=True)
async def _no_mail_left_from_other_tests(pg_container: object, app_dsn: str) -> None:
    """The database is shared: a queued email row another test left would be sent here too."""
    async with await owner(pg_container) as conn:
        await conn.execute(
            "UPDATE invoice_reminders SET status = 'dropped'"
            " WHERE channel = 'email' AND status IN ('queued', 'sending')"
        )


async def send(
    pg_container: object,
    app_dsn: str,
    relay: FakeTransport | None,
    now: datetime = NOW,
    *,
    mailer: Mailer | None = None,
    batch_size: int = 100,
) -> dict[str, int]:
    if mailer is None and relay is not None:
        mailer = Mailer(relay, SENDER)
    async with db.build_pool(app_dsn, max_size=2, autocommit=True) as pool:
        async with await psycopg.AsyncConnection.connect(owner_dsn_for(pg_container)) as conn:
            return await run_payment_reminder_mail(
                conn, pool, mailer, FakeClock(now), batch_size=batch_size
            )


async def queued(
    conn: psycopg.AsyncConnection[Any],
    client: uuid.UUID,
    invoice_id: str,
    address: str,
    *,
    step: int = 0,
    status: str = "queued",
    created_at: datetime = NOW,
) -> uuid.UUID:
    """An email row as the queue job writes it."""
    cursor = await conn.execute(
        "INSERT INTO invoice_reminders (client_id, invoice_id, step, channel, address, status,"
        " created_at) VALUES (%s,%s,%s,'email',%s,%s,%s) RETURNING id",
        (client, invoice_id, step, address, status, created_at),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def a_customer(
    conn: psycopg.AsyncConnection[Any],
    client: uuid.UUID,
    address: str,
    *,
    locale: str = "en",
    due_at: datetime = DUE,
) -> tuple[str, uuid.UUID]:
    """A contact with an address, an issued invoice for them and its queued email row."""
    person = await contact(conn, client, email=address, locale=locale)
    inv = await invoice(conn, client, person, due_at=due_at)
    return inv, await queued(conn, client, inv, address.lower())


async def status_of(conn: psycopg.AsyncConnection[Any], row_id: uuid.UUID) -> tuple[str, Any]:
    cursor = await conn.execute(
        "SELECT status, sent_at FROM invoice_reminders WHERE id = %s", (row_id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    return row[0], row[1]


# --- the template -------------------------------------------------------------------------------


async def test_the_mail_is_the_reminder_text_with_a_greeting_and_the_html_is_escaped() -> None:
    text = "Kedai <b>&: invoice ABCD-EFG for MYR 1.00 is due today.\nIf you've already paid."
    mail = payment_reminder_mail(
        to="pay@example.test",
        name="Aminah <Synthetic>",
        brand="Kedai <b>&",
        subject_ref="ABCD-EFG",
        text=text,
    )
    assert mail.to == "pay@example.test" and mail.calendar is None
    assert mail.subject == "Kedai <b>&: invoice ABCD-EFG"
    assert mail.text == (
        "Hello Aminah <Synthetic>,\n\n"
        "Kedai <b>&: invoice ABCD-EFG for MYR 1.00 is due today.\n\n"
        "If you've already paid.\n\n"
        "Kedai <b>&\n"
    )
    assert "<b>&" not in mail.html and "Kedai &lt;b&gt;&amp;" in mail.html
    assert "<p>Hello Aminah &lt;Synthetic&gt;,</p>" in mail.html
    assert "<p>If you&#x27;ve already paid.</p>" in mail.html
    assert '<html lang="en">' in mail.html


async def test_the_malay_mail_and_a_subject_kept_to_one_line() -> None:
    mail = payment_reminder_mail(
        to="pay@example.test",
        name=None,
        brand="Kedai\nContoh",
        subject_ref="ABCD-EFG",
        text="Kedai Contoh: invois ABCD-EFG.",
        locale="ms",
    )
    assert mail.subject == "Kedai Contoh: invois ABCD-EFG"
    assert mail.text.startswith("Hai,\n\n")
    assert '<html lang="ms">' in mail.html


# --- sending ------------------------------------------------------------------------------------


async def test_a_queued_row_is_sent_once_and_settled_sent(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        inv, row = await a_customer(conn, client, "Pay.One@Example.test")
        await conn.execute(
            "UPDATE invoices SET claim_note = %s, void_reason = %s WHERE id = %s",
            (STAFF_NOTE, VOID_REASON, inv),
        )
        ref = await reference_of(conn, inv)
        relay = FakeTransport()

        assert await send(pg_container, app_dsn, relay) == ZERO | {"claimed": 1, "sent": 1}
        (mail,) = relay.sent
        # Sent as the contact spells it, as booking reminders are; the row keeps it lower case.
        assert mail.to == "Pay.One@Example.test"
        assert mail.subject == f"{BRAND}: invoice {ref}"
        assert mail.text == (
            "Hello Synthetic Customer,\n\n"
            f"{BRAND}: invoice {ref} for MYR 1,059.90 is due today.\n\n"
            f"Bank transfer: Bank Contoh, account {ACCOUNT}, name Kedai Contoh.\n\n"
            f"Please put {ref} as the payment reference so we can match your payment.\n\n"
            "If you've already paid, please let us know when.\n\n"
            f"{BRAND}\n"
        )
        assert ref in mail.html and ACCOUNT in mail.html
        # A reply to an email goes to a sender nobody reads, so it never says "reply here".
        assert "reply" not in mail.text.lower() and "reply" not in mail.html.lower()
        for secret in (STAFF_NOTE, VOID_REASON):
            assert secret not in mail.text and secret not in mail.html
        assert await status_of(conn, row) == ("sent", NOW)

        # Never twice: later runs find nothing to send.
        assert await send(pg_container, app_dsn, relay) == ZERO
        assert await send(pg_container, app_dsn, relay, NOW + timedelta(hours=2)) == ZERO
        assert len(relay.sent) == 1


async def test_the_queue_job_then_the_mail_job_reach_the_customer(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        person = await contact(conn, client, given="Given@Example.test")
        inv = await invoice(conn, client, person)
        async with db.build_pool(app_dsn, max_size=1, autocommit=True) as pool:
            stats = await run_payment_reminder_queue(pool, client, FakeClock(NOW))
        assert stats["queued"] == 1
        relay = FakeTransport()

        assert (await send(pg_container, app_dsn, relay))["sent"] == 1
        (mail,) = relay.sent
        assert mail.to == "Given@Example.test"
        assert await reference_of(conn, inv) in mail.text


async def test_a_reminder_queued_yesterday_and_sent_today_says_it_was_due(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        inv, row = await a_customer(conn, client, "late@example.test")
        relay = FakeTransport()

        assert (await send(pg_container, app_dsn, relay, NOW + timedelta(days=1)))["sent"] == 1
        (mail,) = relay.sent
        ref = await reference_of(conn, inv)
        assert f"invoice {ref} for MYR 1,059.90 was due on 20 November 2026" in mail.text
        assert "today" not in mail.text
        assert (await status_of(conn, row))[0] == "sent"


async def test_a_malay_contact_gets_the_malay_mail(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        inv, _ = await a_customer(conn, client, "melayu@example.test", locale="ms-MY")
        ref = await reference_of(conn, inv)
        relay = FakeTransport()

        assert (await send(pg_container, app_dsn, relay))["sent"] == 1
        (mail,) = relay.sent
        assert mail.subject == f"{BRAND}: invois {ref}"
        assert mail.text.startswith("Hai Synthetic Customer,\n\n")
        assert f"{BRAND}: invois {ref} untuk MYR 1,059.90 kena bayar hari ini." in mail.text
        assert f"Pindahan bank: Bank Contoh, akaun {ACCOUNT}" in mail.text
        assert mail.text.endswith("Kalau dah bayar, maklumkan kami bila ya.\n\n" + BRAND + "\n")
        assert "balas" not in mail.text.lower()


# --- dropped at send time -----------------------------------------------------------------------


@pytest.mark.parametrize("status", ["paid", "void", "proof_submitted", "cash_pending", "draft"])
async def test_an_invoice_no_longer_open_is_dropped_and_not_mailed(
    pg_container: object, app_dsn: str, status: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        inv, row = await a_customer(conn, client, "drop@example.test")
        await conn.execute("UPDATE invoices SET status = %s WHERE id = %s", (status, inv))
        relay = FakeTransport()

        assert await send(pg_container, app_dsn, relay) == ZERO | {"claimed": 1, "dropped": 1}
        assert relay.sent == []
        assert await status_of(conn, row) == ("dropped", None)


async def test_an_overdue_invoice_is_still_mailed(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        inv, row = await a_customer(conn, client, "overdue@example.test")
        await conn.execute("UPDATE invoices SET status = 'overdue' WHERE id = %s", (inv,))
        relay = FakeTransport()

        assert (await send(pg_container, app_dsn, relay))["sent"] == 1
        assert (await status_of(conn, row))[0] == "sent"


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE contacts SET email = 'other@example.test' WHERE id = %s",
        "UPDATE contacts SET email = NULL WHERE id = %s",
        "UPDATE contacts SET email = 'not an address' WHERE id = %s",
    ],
)
async def test_a_changed_address_is_dropped(
    pg_container: object, app_dsn: str, change: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        inv, row = await a_customer(conn, client, "first@example.test")
        cursor = await conn.execute("SELECT contact_id FROM invoices WHERE id = %s", (inv,))
        found = await cursor.fetchone()
        assert found is not None
        await conn.execute(change, (found[0],))
        relay = FakeTransport()

        assert await send(pg_container, app_dsn, relay) == ZERO | {"claimed": 1, "dropped": 1}
        assert relay.sent == [] and (await status_of(conn, row))[0] == "dropped"


async def test_the_given_address_still_counts_when_the_contact_has_no_email(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        person = await contact(conn, client, given="Typed@Example.test")
        inv = await invoice(conn, client, person)
        row = await queued(conn, client, inv, "typed@example.test")
        relay = FakeTransport()

        assert (await send(pg_container, app_dsn, relay))["sent"] == 1
        assert relay.to() == ["Typed@Example.test"]
        assert (await status_of(conn, row))[0] == "sent"


async def test_an_invoice_moved_off_its_contact_is_dropped(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        inv, row = await a_customer(conn, client, "moved@example.test")
        await conn.execute("UPDATE invoices SET contact_id = NULL WHERE id = %s", (inv,))
        relay = FakeTransport()

        assert (await send(pg_container, app_dsn, relay))["dropped"] == 1
        assert relay.sent == [] and (await status_of(conn, row))[0] == "dropped"


async def test_replies_off_drops_the_row(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        # Customer support active with replies Off: customers_remindable is false.
        client = await seed_company(conn, mode="shadow")
        _, row = await a_customer(conn, client, "off@example.test")
        relay = FakeTransport()

        assert await send(pg_container, app_dsn, relay) == ZERO | {"claimed": 1, "dropped": 1}
        assert relay.sent == [] and (await status_of(conn, row))[0] == "dropped"


# --- failures, lost rows and mail off -----------------------------------------------------------


async def test_a_failed_send_is_failed_logged_by_class_and_not_retried(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        _, row = await a_customer(conn, client, "refused@example.test")
        relay = FakeTransport()
        relay.explode = RuntimeError("relay said no to refused@example.test")

        with capture_logs() as logs:
            stats = await send(pg_container, app_dsn, relay)
        assert stats == ZERO | {"claimed": 1, "failed": 1}
        assert (await status_of(conn, row)) == ("failed", None)
        assert "refused@example.test" not in repr(logs)

        relay.explode = None
        assert await send(pg_container, app_dsn, relay) == ZERO
        assert relay.sent == []


async def test_a_compose_error_fails_the_row_and_logs_only_the_class(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        _, row = await a_customer(conn, client, "boom@example.test")
        real = db.q.fetch_one

        async def broken(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "payments/reminder_contact.sql":
                raise ValueError("synthetic failure for boom@example.test")
            return await real(name, *args, **kwargs)

        monkeypatch.setattr(db.q, "fetch_one", broken)
        relay = FakeTransport()
        with capture_logs() as logs:
            stats = await send(pg_container, app_dsn, relay)
        assert stats == ZERO | {"claimed": 1, "failed": 1}
        assert relay.sent == [] and (await status_of(conn, row))[0] == "failed"
        assert ("payment_reminder_mail_failed", "ValueError") in [
            (e["event"], e.get("category")) for e in logs
        ]
        assert "boom@example.test" not in repr(logs)


async def test_a_row_left_sending_is_failed_after_lost_after_and_counted_lost(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        person = await contact(conn, client, email="lost@example.test")
        inv = await invoice(conn, client, person)
        row = await queued(conn, client, inv, "lost@example.test", status="sending", created_at=NOW)
        relay = FakeTransport()

        # Not yet: a row claimed less than LOST_AFTER ago may still be in flight.
        assert await send(pg_container, app_dsn, relay, NOW + timedelta(minutes=30)) == ZERO
        assert (await status_of(conn, row))[0] == "sending"

        later = NOW + LOST_AFTER + timedelta(minutes=1)
        assert await send(pg_container, app_dsn, relay, later) == ZERO | {"lost": 1}
        assert relay.sent == [] and (await status_of(conn, row)) == ("failed", None)
        assert await send(pg_container, app_dsn, relay, later) == ZERO


async def test_with_mail_off_nothing_is_touched(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        _, row = await a_customer(conn, client, "wait@example.test")
        person = await contact(conn, client, email="old@example.test")
        old = await queued(
            conn,
            client,
            await invoice(conn, client, person),
            "old@example.test",
            status="sending",
            created_at=NOW - timedelta(hours=5),
        )

        assert await send(pg_container, app_dsn, None) == ZERO
        assert await send(pg_container, app_dsn, None, mailer=Mailer(None, SENDER)) == ZERO
        assert (await status_of(conn, row))[0] == "queued"
        assert (await status_of(conn, old))[0] == "sending"

        relay = FakeTransport()
        assert await send(pg_container, app_dsn, relay) == ZERO | {
            "claimed": 1,
            "sent": 1,
            "lost": 1,
        }


# --- which companies, and in what order ---------------------------------------------------------


async def test_companies_take_turns_within_a_batch(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        busy = await seed_company(conn, support=None)
        quiet = await seed_company(conn, support=None)
        for n in range(3):
            await a_customer(conn, busy, f"busy{n}@example.test")
        await a_customer(conn, quiet, "single@example.test")
        relay = FakeTransport()

        assert (await send(pg_container, app_dsn, relay, batch_size=2))["sent"] == 2
        # One from each company, though the busy one has three waiting (they tie on age).
        busy_sent = [to for to in relay.to() if to.startswith("busy")]
        assert len(busy_sent) == 1 and "single@example.test" in relay.to()
        assert (await send(pg_container, app_dsn, relay, batch_size=2))["sent"] == 2
        assert (await send(pg_container, app_dsn, relay, batch_size=2))["sent"] == 0
        assert len(relay.sent) == 4


@pytest.mark.parametrize(
    ("client_status", "package", "payments"),
    [
        ("onboarding", "active", True),
        ("active", "active", False),
        ("active", "suspended", True),
        ("active", None, True),
    ],
)
async def test_a_company_not_live_or_with_payments_off_is_skipped_and_its_rows_wait(
    pg_container: object,
    app_dsn: str,
    client_status: str,
    package: str | None,
    payments: bool,
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None, package=package, payments=payments)
        _, row = await a_customer(conn, client, "waiting@example.test")
        await conn.execute("UPDATE clients SET status = %s WHERE id = %s", (client_status, client))
        relay = FakeTransport()

        assert await send(pg_container, app_dsn, relay) == ZERO
        assert relay.sent == [] and (await status_of(conn, row))[0] == "queued"


async def test_nothing_is_mailed_in_quiet_hours_and_it_goes_in_the_morning(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        _, row = await a_customer(conn, client, "night@example.test")
        relay = FakeTransport()
        night = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)  # 22:30 in Kuala Lumpur

        assert await send(pg_container, app_dsn, relay, night) == ZERO
        assert (await status_of(conn, row))[0] == "queued"
        morning = datetime(2026, 11, 21, 1, 0, tzinfo=UTC)  # 09:00
        assert (await send(pg_container, app_dsn, relay, morning))["sent"] == 1


async def test_a_claim_that_fails_leaves_the_row_queued_for_the_next_run(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        _, first = await a_customer(conn, client, "claim1@example.test")
        _, second = await a_customer(conn, client, "claim2@example.test")
        real = db.q.fetch_one
        calls: list[str] = []

        async def flaky(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "payments/reminder_claim_one.sql":
                calls.append(name)
                if len(calls) == 1:
                    raise psycopg.OperationalError("synthetic claim failure")
            return await real(name, *args, **kwargs)

        monkeypatch.setattr(db.q, "fetch_one", flaky)
        relay = FakeTransport()
        stats = await send(pg_container, app_dsn, relay)
        assert stats == ZERO | {"claimed": 1, "sent": 1, "errors": 1}
        statuses = sorted([(await status_of(conn, r))[0] for r in (first, second)])
        assert statuses == ["queued", "sent"]
        assert (await send(pg_container, app_dsn, relay))["sent"] == 1
        assert len(relay.sent) == 2


@pytest.mark.parametrize(("package", "payments"), [("active", False), ("suspended", True)])
async def test_compose_drops_a_row_whose_company_switched_payments_off(
    pg_container: object, app_dsn: str, package: str, payments: bool
) -> None:
    """The listing skips such a company; a switch flipped between the listing and the claim is
    still caught by the check at claim time."""
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None, package=package, payments=payments)
        inv, row = await a_customer(conn, client, "flip@example.test")
        claimed = {
            "client_id": client,
            "id": row,
            "invoice_id": inv,
            "step": 0,
            "address": "flip@example.test",
            "created_at": NOW,
        }
    async with db.build_pool(app_dsn, max_size=1, autocommit=True) as pool:
        async with tx(pool, str(client)) as scoped:
            assert await payment_reminders._compose(scoped, claimed, NOW) is None
        # With payments on again the same row is owed.
        async with await owner(pg_container) as conn:
            await conn.execute(
                "UPDATE payment_settings SET enabled = true WHERE client_id = %s", (client,)
            )
            await conn.execute(
                "UPDATE console_service_packages SET status = 'active' WHERE client_id = %s",
                (client,),
            )
        async with tx(pool, str(client)) as scoped:
            assert await payment_reminders._compose(scoped, claimed, NOW) is not None


async def test_a_row_queued_more_than_stale_after_ago_is_dropped_never_sent(
    pg_container: object, app_dsn: str
) -> None:
    assert STALE_AFTER == timedelta(hours=48)
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        person = await contact(conn, client, email="stale@example.test")
        inv = await invoice(conn, client, person)
        stale = await queued(
            conn, client, inv, "stale@example.test", created_at=NOW - timedelta(hours=49)
        )
        relay = FakeTransport()

        assert await send(pg_container, app_dsn, relay) == ZERO | {"claimed": 1, "dropped": 1}
        assert relay.sent == [] and await status_of(conn, stale) == ("dropped", None)


async def test_a_row_held_twenty_hours_by_quiet_hours_is_still_sent(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        client = await seed_company(conn, support=None)
        person = await contact(conn, client, email="held@example.test")
        inv = await invoice(conn, client, person)
        held = await queued(
            conn, client, inv, "held@example.test", created_at=NOW - timedelta(hours=20)
        )
        relay = FakeTransport()

        assert await send(pg_container, app_dsn, relay) == ZERO | {"claimed": 1, "sent": 1}
        assert len(relay.sent) == 1 and (await status_of(conn, held))[0] == "sent"
