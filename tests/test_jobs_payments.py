"""Payments 1, task 5: the payments_due job (opening and period invoices, overdue, unpaid suspend).

Real database, synthetic data only. The job runs per company inside the runner's tenant
transaction; two runs at once (two real connections) issue one invoice per period, and a second run
changes nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs import payments as payments_job
from csa.jobs.clock import FakeClock
from csa.jobs.payments import NOTHING_DUE, run_payments_due
from csa.jobs.runner import build_default_registry
from csa.jobs.subscriptions import run_subscriptions_due
from csa.payments import invoices
from csa.payments.reference import new_invoice_id, new_reference
from csa.subscriptions.service import record_event
from psycopg import sql
from structlog.testing import capture_logs

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]

ACTOR = "system:payments_due"
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
ZERO = {"opening_issued": 0, "period_issued": 0, "overdue": 0, "suspended": 0, "failed": 0}
HOLD = 0.4  # seconds a racing run keeps its transaction (and locks) open before it commits


async def seed_tenant(
    conn: psycopg.AsyncConnection[Any],
    *,
    package: str | None = "active",
    payments: bool | None = True,
    lead_days: int = 7,
    suspend_after_days: int | None = None,
) -> uuid.UUID:
    """A company with plans: `package` None means no package, `payments` None no settings row."""
    client = uuid.uuid4()
    await conn.execute(
        "INSERT INTO clients(id,slug,name,status,platform_version) "
        "VALUES (%s,%s,'Synthetic payments','active','p1-test')",
        (client, f"pay-due-{client}"),
    )
    if package is not None:
        await conn.execute(
            "INSERT INTO console_service_packages(client_id,package,status,activated_at,reference)"
            " VALUES (%s,'subscriptions',%s,now(),'p1-test')",
            (client, package),
        )
    for code, price in [("basic", 50), ("pro", 80), ("free", 0)]:
        await conn.execute(
            "INSERT INTO plans(client_id,code,name,price,billing_interval,is_active) "
            "VALUES (%s,%s,%s,%s,'month',true)",
            (client, code, code.title(), price),
        )
    if payments is not None:
        await set_payments(
            conn, client, enabled=payments, lead_days=lead_days, suspend=suspend_after_days
        )
    return client


async def set_payments(
    conn: psycopg.AsyncConnection[Any],
    client: uuid.UUID,
    *,
    enabled: bool = True,
    lead_days: int = 7,
    suspend: int | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO payment_settings (client_id, enabled, lead_days, suspend_after_days) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (client_id) DO UPDATE SET enabled = EXCLUDED.enabled, "
        "lead_days = EXCLUDED.lead_days, suspend_after_days = EXCLUDED.suspend_after_days",
        (client, enabled, lead_days, suspend),
    )


async def add_subscription(
    conn: psycopg.AsyncConnection[Any], client: uuid.UUID, **fields: Any
) -> uuid.UUID:
    values: dict[str, Any] = {
        "plan": "basic",
        "status": "active",
        "price": 50,
        "started_at": datetime(2025, 1, 1, tzinfo=UTC),
        "renews_at": NOW + timedelta(days=20),
    } | fields
    sub = uuid.uuid4()
    names = ["id", "client_id", *values]
    await conn.execute(
        sql.SQL("INSERT INTO subscriptions({}) VALUES ({})").format(
            sql.SQL(",").join(map(sql.Identifier, names)),
            sql.SQL(",").join(sql.Placeholder() * len(names)),
        ),
        (sub, client, *values.values()),
    )
    return sub


async def add_invoice(
    conn: psycopg.AsyncConnection[Any],
    client: uuid.UUID,
    sub: uuid.UUID | None,
    status: str,
    *,
    due_at: datetime,
    period_start: datetime | None = None,
) -> str:
    invoice_id = new_invoice_id(NOW)
    await conn.execute(
        "INSERT INTO invoices (id, client_id, subscription_id, reference, status, amount, "
        "currency, issued_at, due_at, period_start, period_end) "
        "VALUES (%s,%s,%s,%s,%s,50,'MYR',%s,%s,%s,%s)",
        (
            invoice_id,
            client,
            sub,
            new_reference(),
            status,
            NOW - timedelta(days=40),
            due_at,
            period_start,
            period_start + timedelta(days=30) if period_start else None,
        ),
    )
    return invoice_id


async def invoices_of(conn: psycopg.AsyncConnection[Any], sub: uuid.UUID) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT id, status, amount, currency, issued_at, due_at, period_start "
        "FROM invoices WHERE subscription_id=%s ORDER BY period_start NULLS FIRST, id",
        (sub,),
    )
    keys = ["id", "status", "amount", "currency", "issued_at", "due_at", "period_start"]
    return [dict(zip(keys, row, strict=True)) for row in await cursor.fetchall()]


async def invoice_status(conn: psycopg.AsyncConnection[Any], invoice_id: str) -> str:
    cursor = await conn.execute("SELECT status FROM invoices WHERE id=%s", (invoice_id,))
    row = await cursor.fetchone()
    assert row is not None
    return str(row[0])


async def count_invoices(conn: psycopg.AsyncConnection[Any], client: uuid.UUID) -> int:
    cursor = await conn.execute("SELECT count(*) FROM invoices WHERE client_id=%s", (client,))
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def fetch_subscription(conn: psycopg.AsyncConnection[Any], sub: uuid.UUID) -> dict[str, Any]:
    cursor = await conn.execute(
        "SELECT status, paused_at, resume_at, pause_reason, version, renews_at "
        "FROM subscriptions WHERE id=%s",
        (sub,),
    )
    row = await cursor.fetchone()
    assert row is not None
    keys = ["status", "paused_at", "resume_at", "pause_reason", "version", "renews_at"]
    return dict(zip(keys, row, strict=True))


async def fetch_events(conn: psycopg.AsyncConnection[Any], sub: uuid.UUID) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT actor, action, from_status, to_status, payload "
        "FROM subscription_events WHERE subscription_id=%s ORDER BY id",
        (sub,),
    )
    keys = ["actor", "action", "from_status", "to_status", "payload"]
    return [dict(zip(keys, row, strict=True)) for row in await cursor.fetchall()]


async def run(
    app_dsn: str,
    client: uuid.UUID,
    now: datetime = NOW,
    *,
    session: uuid.UUID | None = None,
    **kwargs: Any,
) -> dict[str, int]:
    """One run of the job in `session`'s tenant transaction (the company itself by default)."""
    async with db.build_pool(app_dsn, max_size=1, autocommit=True) as pool:
        async with tx(pool, str(session or client)) as conn:
            return await run_payments_due(conn, client, FakeClock(now), **kwargs)


def owner(pg_container: object) -> Any:
    return psycopg.AsyncConnection.connect(owner_dsn_for(pg_container), autocommit=True)


async def test_running_twice_issues_one_invoice_per_period(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, lead_days=3)
        pending = await add_subscription(conn, tenant, status="pending", renews_at=None)
        renews = NOW + timedelta(days=2)
        due = await add_subscription(conn, tenant, price=Decimal("59.90"), renews_at=renews)

        assert await run(app_dsn, tenant) == ZERO | {"opening_issued": 1, "period_issued": 1}
        assert await run(app_dsn, tenant) == ZERO
        assert await run(app_dsn, tenant, NOW + timedelta(days=1)) == ZERO

        (opening,) = await invoices_of(conn, pending)
        assert opening["status"] == "issued" and opening["period_start"] is None
        assert opening["issued_at"] == NOW and opening["due_at"] == NOW + timedelta(days=3)
        (period,) = await invoices_of(conn, due)
        assert period["period_start"] == renews and period["due_at"] == renews
        assert (period["amount"], period["currency"]) == (Decimal("59.90"), "MYR")
        assert await fetch_events(conn, due) == []  # issuing writes no subscription event


async def test_two_concurrent_runs_issue_one_invoice(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, suspend_after_days=7)
        pending = await add_subscription(conn, tenant, status="pending", renews_at=None)
        due = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=4))
        late = await add_subscription(conn, tenant)
        past_due = await add_invoice(
            conn, tenant, late, "issued", due_at=NOW - timedelta(days=1), period_start=NOW
        )
        unpaid = await add_subscription(conn, tenant)
        await add_invoice(
            conn, tenant, unpaid, "overdue", due_at=NOW - timedelta(days=9), period_start=NOW
        )

        async with db.build_pool(app_dsn, max_size=2, autocommit=True) as pool:

            async def job() -> dict[str, int]:
                async with tx(pool, str(tenant)) as tenant_conn:
                    stats = await run_payments_due(tenant_conn, tenant, FakeClock(NOW))
                    await asyncio.sleep(HOLD)  # keep the locks while the other run goes
                    return stats

            results = await asyncio.gather(job(), job())

        totals = {key: sum(r[key] for r in results) for key in ZERO}
        assert totals == ZERO | {
            "opening_issued": 1,
            "period_issued": 1,
            "overdue": 1,
            "suspended": 1,
        }
        assert len(await invoices_of(conn, pending)) == 1
        assert len(await invoices_of(conn, due)) == 1
        assert await invoice_status(conn, past_due) == "overdue"
        assert [e["action"] for e in await fetch_events(conn, unpaid)] == ["suspended_unpaid"]
        assert await run(app_dsn, tenant) == ZERO


async def test_only_periods_starting_within_lead_days_are_invoiced(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn)  # lead_days 7
        edge = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=7))
        eight = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=8))

        assert await run(app_dsn, tenant) == ZERO | {"period_issued": 1}
        assert len(await invoices_of(conn, edge)) == 1
        assert await invoices_of(conn, eight) == []
        # a day later the eight-day period is inside the lead time too
        assert await run(app_dsn, tenant, NOW + timedelta(days=1)) == ZERO | {"period_issued": 1}
        assert len(await invoices_of(conn, eight)) == 1


async def test_a_running_period_gets_no_invoice_when_payments_are_switched_on(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, payments=False)
        middle = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=20))
        # the subscriptions job has not renewed it yet: its next period already started
        lagging = await add_subscription(conn, tenant, renews_at=NOW - timedelta(hours=1))

        assert await run(app_dsn, tenant) == NOTHING_DUE
        await set_payments(conn, tenant)
        assert await run(app_dsn, tenant) == ZERO
        assert await invoices_of(conn, middle) == []
        assert await invoices_of(conn, lagging) == []

        # the next period of the middle one is invoiced once it is within the lead time
        later = NOW + timedelta(days=13, hours=12)
        assert await run(app_dsn, tenant, later) == ZERO | {"period_issued": 1}
        (invoice,) = await invoices_of(conn, middle)
        assert invoice["period_start"] == NOW + timedelta(days=20)
        assert await invoices_of(conn, lagging) == []


async def test_cancelled_paused_and_cancelling_subscriptions_are_never_invoiced(
    pg_container: object, app_dsn: str
) -> None:
    soon = NOW + timedelta(days=3)
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn)
        subs = [
            await add_subscription(conn, tenant, status="cancelled", renews_at=soon),
            await add_subscription(conn, tenant, status="paused", paused_at=NOW, renews_at=soon),
            await add_subscription(conn, tenant, cancel_at_period_end=True, renews_at=soon),
        ]
        # a pending subscription whose opening invoice was voided never gets another one
        voided = await add_subscription(conn, tenant, status="pending", renews_at=None)
        void_id = await add_invoice(conn, tenant, voided, "void", due_at=NOW)
        # a cancelled one never gets an opening invoice either
        subs.append(await add_subscription(conn, tenant, status="cancelled", renews_at=None))

        assert await run(app_dsn, tenant) == ZERO
        for sub in subs:
            assert await invoices_of(conn, sub) == []
        assert [i["id"] for i in await invoices_of(conn, voided)] == [void_id]


async def test_a_pending_subscription_from_before_payments_gets_its_opening_invoice(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, payments=None)
        pending = await add_subscription(
            conn, tenant, status="pending", renews_at=None, started_at=NOW - timedelta(days=3)
        )
        assert await run(app_dsn, tenant) == NOTHING_DUE
        assert await invoices_of(conn, pending) == []

        await set_payments(conn, tenant, lead_days=5)
        assert await run(app_dsn, tenant) == ZERO | {"opening_issued": 1}
        (opening,) = await invoices_of(conn, pending)
        assert opening["due_at"] == NOW + timedelta(days=5)
        assert opening["period_start"] is None


@pytest.mark.parametrize(
    ("package", "payments"),
    [(None, True), ("requested", True), ("suspended", True), ("active", False), ("active", None)],
)
async def test_no_active_package_or_payments_off_does_nothing(
    pg_container: object, app_dsn: str, package: str | None, payments: bool | None
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, package=package, payments=payments)
        if payments is not None:
            await set_payments(conn, tenant, enabled=payments, suspend=7)
        await add_subscription(conn, tenant, status="pending", renews_at=None)
        await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=2))
        unpaid = await add_subscription(conn, tenant)
        stale = await add_invoice(
            conn, tenant, unpaid, "issued", due_at=NOW - timedelta(days=30), period_start=NOW
        )

        result = await run(app_dsn, tenant)
        assert result == NOTHING_DUE
        assert result is not NOTHING_DUE  # a copy; the caller may change it
        assert await count_invoices(conn, tenant) == 1
        assert await invoice_status(conn, stale) == "issued"
        assert (await fetch_subscription(conn, unpaid))["status"] == "active"


async def test_batch_size_limits_each_run_and_the_next_run_continues(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn)
        due = [
            await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=day))
            for day in range(1, 6)
        ]
        pending = [
            await add_subscription(
                conn, tenant, status="pending", renews_at=None, started_at=NOW - timedelta(days=d)
            )
            for d in (3, 2, 1)
        ]

        assert await run(app_dsn, tenant, batch_size=2) == ZERO | {
            "opening_issued": 2,
            "period_issued": 2,
        }
        # oldest first: the two earliest periods and the two oldest approvals
        assert [len(await invoices_of(conn, s)) for s in due] == [1, 1, 0, 0, 0]
        assert [len(await invoices_of(conn, s)) for s in pending] == [1, 1, 0]
        assert await run(app_dsn, tenant, batch_size=2) == ZERO | {
            "opening_issued": 1,
            "period_issued": 2,
        }
        assert await run(app_dsn, tenant, batch_size=2) == ZERO | {"period_issued": 1}
        assert await run(app_dsn, tenant, batch_size=2) == ZERO
        assert [len(await invoices_of(conn, s)) for s in due + pending] == [1] * 8


async def test_rows_that_would_get_no_invoice_never_use_up_the_batch(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn)
        # zero-priced ones come first in each order and would take the only slot every run
        free = await add_subscription(
            conn, tenant, plan="free", price=0, renews_at=NOW + timedelta(days=1)
        )
        free_pending = await add_subscription(
            conn,
            tenant,
            plan="free",
            price=0,
            status="pending",
            renews_at=None,
            started_at=NOW - timedelta(days=5),
        )
        # the plan in effect at renews_at is free, so its next period costs nothing
        to_free = await add_subscription(
            conn,
            tenant,
            renews_at=NOW + timedelta(days=2),
            pending_plan="free",
            pending_effective_at=NOW + timedelta(days=1),
        )
        # free today, but the next period is on the paid plan
        to_paid = await add_subscription(
            conn,
            tenant,
            plan="free",
            price=0,
            renews_at=NOW + timedelta(days=3),
            pending_plan="pro",
            pending_effective_at=NOW + timedelta(days=3),
        )
        paid = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=4))
        paid_pending = await add_subscription(
            conn, tenant, status="pending", renews_at=None, started_at=NOW - timedelta(days=1)
        )

        assert await run(app_dsn, tenant, batch_size=1) == ZERO | {
            "opening_issued": 1,
            "period_issued": 1,
        }
        assert len(await invoices_of(conn, paid_pending)) == 1
        (next_paid,) = await invoices_of(conn, to_paid)
        assert next_paid["amount"] == Decimal("80.00")  # the pro plan in effect at renews_at
        assert await run(app_dsn, tenant, batch_size=1) == ZERO | {"period_issued": 1}
        assert len(await invoices_of(conn, paid)) == 1
        assert await run(app_dsn, tenant, batch_size=1) == ZERO
        for sub in (free, free_pending, to_free):
            assert await invoices_of(conn, sub) == []


async def test_issued_past_due_becomes_overdue_and_claims_stay(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn)
        sub = await add_subscription(conn, tenant)
        late = await add_invoice(
            conn, tenant, sub, "issued", due_at=NOW - timedelta(seconds=1), period_start=NOW
        )
        not_yet = await add_invoice(
            conn, tenant, sub, "issued", due_at=NOW, period_start=NOW + timedelta(days=30)
        )
        claimed = await add_invoice(
            conn,
            tenant,
            sub,
            "proof_submitted",
            due_at=NOW - timedelta(days=5),
            period_start=NOW - timedelta(days=30),
        )
        cash = await add_invoice(
            conn,
            tenant,
            sub,
            "cash_pending",
            due_at=NOW - timedelta(days=5),
            period_start=NOW - timedelta(days=60),
        )
        loose = await add_invoice(conn, tenant, None, "issued", due_at=NOW - timedelta(days=2))

        assert await run(app_dsn, tenant) == ZERO | {"overdue": 2}
        assert await invoice_status(conn, late) == "overdue"
        assert await invoice_status(conn, loose) == "overdue"
        assert await invoice_status(conn, not_yet) == "issued"
        assert await invoice_status(conn, claimed) == "proof_submitted"
        assert await invoice_status(conn, cash) == "cash_pending"
        assert await run(app_dsn, tenant) == ZERO
        assert (await fetch_subscription(conn, sub))["status"] == "active"  # suspend is off


async def test_suspend_is_off_by_default(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn)
        sub = await add_subscription(conn, tenant)
        await add_invoice(
            conn, tenant, sub, "overdue", due_at=NOW - timedelta(days=30), period_start=NOW
        )
        assert await run(app_dsn, tenant) == ZERO
        row = await fetch_subscription(conn, sub)
        assert (row["status"], row["pause_reason"], row["version"]) == ("active", None, 1)
        assert await fetch_events(conn, sub) == []


async def test_suspend_after_seven_days_pauses_once_as_unpaid(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, suspend_after_days=7)
        # resume_at is cleared, so the subscriptions job never resumes an unpaid pause by date
        unpaid = await add_subscription(conn, tenant, resume_at=date(2026, 3, 1))
        older = await add_invoice(
            conn,
            tenant,
            unpaid,
            "overdue",
            due_at=NOW - timedelta(days=12),
            period_start=NOW - timedelta(days=30),
        )
        await add_invoice(
            conn, tenant, unpaid, "overdue", due_at=NOW - timedelta(days=8), period_start=NOW
        )
        recent = await add_subscription(conn, tenant)
        await add_invoice(
            conn, tenant, recent, "overdue", due_at=NOW - timedelta(days=6), period_start=NOW
        )
        # issued and long past due: marked overdue first, then suspended in the same run
        lapsed = await add_subscription(conn, tenant)
        await add_invoice(
            conn, tenant, lapsed, "issued", due_at=NOW - timedelta(days=10), period_start=NOW
        )
        claimed = await add_subscription(conn, tenant)
        await add_invoice(
            conn,
            tenant,
            claimed,
            "proof_submitted",
            due_at=NOW - timedelta(days=30),
            period_start=NOW,
        )
        staff_paused = await add_subscription(
            conn, tenant, status="paused", paused_at=NOW - timedelta(days=2)
        )
        await add_invoice(
            conn, tenant, staff_paused, "overdue", due_at=NOW - timedelta(days=30), period_start=NOW
        )
        # the opening invoice of a pending subscription never suspends anything
        pending = await add_subscription(conn, tenant, status="pending", renews_at=None)
        await add_invoice(conn, tenant, pending, "overdue", due_at=NOW - timedelta(days=30))

        assert await run(app_dsn, tenant) == ZERO | {"overdue": 1, "suspended": 2}

        row = await fetch_subscription(conn, unpaid)
        assert (row["status"], row["pause_reason"], row["paused_at"]) == ("paused", "unpaid", NOW)
        assert (row["resume_at"], row["version"]) == (None, 2)
        (event,) = await fetch_events(conn, unpaid)
        assert (event["actor"], event["action"]) == (ACTOR, "suspended_unpaid")
        assert (event["from_status"], event["to_status"]) == ("active", "paused")
        assert event["payload"] == {"invoice_id": older}  # the longest overdue one
        assert (await fetch_subscription(conn, lapsed))["pause_reason"] == "unpaid"
        for sub in (recent, claimed):
            assert (await fetch_subscription(conn, sub))["status"] == "active"
        kept = await fetch_subscription(conn, staff_paused)
        assert (kept["pause_reason"], kept["version"]) == (None, 1)
        assert (await fetch_subscription(conn, pending))["status"] == "pending"

        assert await run(app_dsn, tenant) == ZERO
        assert len(await fetch_events(conn, unpaid)) == 1
        # the subscriptions job leaves the unpaid pause alone (no resume date)
        async with db.build_pool(app_dsn, max_size=1, autocommit=True) as pool:
            async with tx(pool, str(tenant)) as tenant_conn:
                due = await run_subscriptions_due(tenant_conn, tenant, FakeClock(NOW))
        assert due["resumed"] == 0
        assert (await fetch_subscription(conn, unpaid))["status"] == "paused"


async def staff_resume(app_dsn: str, client: uuid.UUID, sub: uuid.UUID) -> None:
    """Resume `sub` as the executor does: subscription_apply.sql under the lock, then the event."""
    async with db.build_pool(app_dsn, max_size=1, autocommit=True) as pool:
        async with tx(pool, str(client)) as conn:
            key = {"client_id": client, "subscription_id": sub}
            locked = await db.q.fetch_one("subscriptions/subscription_lock.sql", key, conn=conn)
            assert locked is not None
            applied = await db.q.fetch_one(
                "subscriptions/subscription_apply.sql",
                {
                    **key,
                    "version": locked["version"],
                    "action": "resume",
                    "resume_at": None,
                    "renews_at": None,
                    "immediate_plan": False,
                    "scheduled_plan": False,
                    "plan_code": None,
                    "price": None,
                    "currency": None,
                    "target_interval": None,
                    "effective_at": None,
                },
                conn=conn,
            )
            assert applied is not None and applied["status"] == "active"
            await record_event(
                conn,
                client,
                sub,
                actor=str(uuid.uuid4()),
                action="resumed",
                from_status=locked["status"],
                to_status=applied["status"],
                payload={"version": applied["version"]},
            )


async def test_a_staff_resume_of_an_unpaid_pause_is_not_undone_by_the_next_run(
    pg_container: object, app_dsn: str
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, suspend_after_days=7)
        sub = await add_subscription(conn, tenant)
        first = await add_invoice(
            conn, tenant, sub, "overdue", due_at=NOW - timedelta(days=10), period_start=NOW
        )
        assert await run(app_dsn, tenant) == ZERO | {"suspended": 1}

        await staff_resume(app_dsn, tenant, sub)
        row = await fetch_subscription(conn, sub)
        assert (row["status"], row["pause_reason"]) == ("active", None)
        assert await invoice_status(conn, first) == "overdue"  # still unpaid

        # suspension happens at most once per invoice: the same overdue invoice never pauses again
        assert await run(app_dsn, tenant) == ZERO
        assert await run(app_dsn, tenant, NOW + timedelta(days=3)) == ZERO
        assert (await fetch_subscription(conn, sub))["status"] == "active"
        suspensions = [
            e for e in await fetch_events(conn, sub) if e["action"] == "suspended_unpaid"
        ]
        assert [e["payload"] for e in suspensions] == [{"invoice_id": first}]

        # another overdue invoice past the cutoff that has caused no suspension yet still counts
        second = await add_invoice(
            conn,
            tenant,
            sub,
            "overdue",
            due_at=NOW - timedelta(days=8),
            period_start=NOW + timedelta(days=30),
        )
        assert await run(app_dsn, tenant) == ZERO | {"suspended": 1}
        row = await fetch_subscription(conn, sub)
        assert (row["status"], row["pause_reason"]) == ("paused", "unpaid")
        suspensions = [
            e for e in await fetch_events(conn, sub) if e["action"] == "suspended_unpaid"
        ]
        assert [e["payload"] for e in suspensions] == [
            {"invoice_id": first},
            {"invoice_id": second},
        ]


async def test_two_tenants_are_isolated(pg_container: object, app_dsn: str) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn, suspend_after_days=7)
        other = await seed_tenant(conn, suspend_after_days=7)
        mine = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=2))
        theirs = await add_subscription(conn, other, renews_at=NOW + timedelta(days=2))
        their_pending = await add_subscription(conn, other, status="pending", renews_at=None)
        their_late = await add_invoice(
            conn, other, theirs, "issued", due_at=NOW - timedelta(days=10), period_start=NOW
        )

        assert await run(app_dsn, tenant) == ZERO | {"period_issued": 1}
        assert len(await invoices_of(conn, mine)) == 1
        assert [i["id"] for i in await invoices_of(conn, theirs)] == [their_late]
        assert await invoices_of(conn, their_pending) == []
        # the other company's id under this company's session finds nothing and writes nothing
        assert await run(app_dsn, other, session=tenant) == NOTHING_DUE
        assert await invoice_status(conn, their_late) == "issued"
        assert (await fetch_subscription(conn, theirs))["status"] == "active"

        assert await run(app_dsn, other) == ZERO | {
            "opening_issued": 1,
            "period_issued": 1,
            "overdue": 1,
            "suspended": 1,
        }
        assert len(await invoices_of(conn, mine)) == 1
        assert (await fetch_subscription(conn, mine))["status"] == "active"


async def test_a_failed_issue_is_counted_rolled_back_and_logged_by_class_only(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await owner(pg_container) as conn:
        tenant = await seed_tenant(conn)
        broken = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=1))
        fine = await add_subscription(conn, tenant, renews_at=NOW + timedelta(days=2))
        secret = "Aisha Rahman synthetic-aisha@example.test MYR 50.00"

        async def flaky(
            conn: Any, client_id: uuid.UUID, subscription_id: uuid.UUID, *, now: datetime
        ) -> Any:
            issued = await invoices.issue_period_invoice(conn, client_id, subscription_id, now=now)
            if subscription_id == broken:
                raise RuntimeError(secret)  # after the insert: the savepoint must undo it
            return issued

        monkeypatch.setattr(payments_job, "issue_period_invoice", flaky)
        with capture_logs() as logs:
            assert await run(app_dsn, tenant) == ZERO | {"period_issued": 1, "failed": 1}

        assert await invoices_of(conn, broken) == []
        assert len(await invoices_of(conn, fine)) == 1
        (entry,) = [log for log in logs if log["event"] == "payments_issue_failed"]
        assert entry["error_class"] == "RuntimeError"
        assert entry["subscription_id"] == str(broken)
        assert entry["client_id"] == str(tenant)
        assert entry["kind"] == "period"
        allowed = {"event", "log_level", "client_id", "subscription_id", "kind", "error_class"}
        assert set(entry) <= allowed
        assert all(secret not in str(value) for log in logs for value in log.values())

        monkeypatch.undo()
        assert await run(app_dsn, tenant) == ZERO | {"period_issued": 1}
        assert len(await invoices_of(conn, broken)) == 1


async def test_registry_contains_payments_due_every_fifteen_minutes() -> None:
    job = build_default_registry().get("payments_due")
    assert job is not None
    assert job.every == timedelta(minutes=15)
    assert job.per_client is True
    assert job.own_transactions is False
    assert job.fn is run_payments_due
