"""Subscriptions tab: the due-changes worker job (B1, task 5)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import FakeClock
from csa.jobs.runner import build_default_registry
from csa.jobs.subscriptions import run_subscriptions_due
from psycopg import sql

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.usefixtures("lg_setup_dsn")]

ACTOR = "system:subscriptions_due"
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
ZERO = {"cancelled": 0, "plan_changed": 0, "resumed": 0, "renewed": 0, "pending_skipped": 0}


def at(text: str) -> datetime:
    """A timestamp written in the default tenant calendar (Asia/Kuala_Lumpur, UTC+8)."""
    return datetime.fromisoformat(f"{text}+08:00")


async def seed_tenants(conn: psycopg.AsyncConnection[Any]) -> tuple[uuid.UUID, uuid.UUID]:
    tenant, other = uuid.uuid4(), uuid.uuid4()
    for client in (tenant, other):
        await conn.execute(
            "INSERT INTO clients(id,slug,name,status,platform_version) "
            "VALUES (%s,%s,'Synthetic subscriptions','active','b1-test')",
            (client, f"subs-due-{client}"),
        )
        # The job only runs for a tenant whose subscriptions package is active.
        await conn.execute(
            "INSERT INTO console_service_packages(client_id,package,status,activated_at,reference)"
            " VALUES (%s,'subscriptions','active',now(),'b1-test')",
            (client,),
        )
        for code, price, interval, active in [
            ("basic", 50, "month", True),
            ("pro", 80, "month", True),
            ("annual", 500, "year", True),
            ("retired", 30, "month", False),
        ]:
            await conn.execute(
                "INSERT INTO plans(client_id,code,name,price,billing_interval,is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (client, code, code.title(), price, interval, active),
            )
    return tenant, other


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


async def fetch_subscription(conn: psycopg.AsyncConnection[Any], sub: uuid.UUID) -> dict[str, Any]:
    cursor = await conn.execute(
        "SELECT plan,status,price,currency,renews_at,paused_at,resume_at,cancelled_at,"
        "pending_plan,pending_effective_at,cancel_at_period_end,version,renewal_anchor "
        "FROM subscriptions WHERE id=%s",
        (sub,),
    )
    row = await cursor.fetchone()
    assert row is not None
    keys = (
        "plan status price currency renews_at paused_at resume_at cancelled_at pending_plan "
        "pending_effective_at cancel_at_period_end version renewal_anchor"
    ).split()
    return dict(zip(keys, row, strict=True))


async def fetch_events(conn: psycopg.AsyncConnection[Any], sub: uuid.UUID) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT actor,action,from_status,to_status,payload,proposal_id "
        "FROM subscription_events WHERE subscription_id=%s ORDER BY id",
        (sub,),
    )
    keys = ["actor", "action", "from_status", "to_status", "payload", "proposal_id"]
    return [dict(zip(keys, row, strict=True)) for row in await cursor.fetchall()]


async def run(
    app_dsn: str, client: uuid.UUID, now: datetime = NOW, **kwargs: Any
) -> dict[str, Any]:
    async with db.build_pool(app_dsn, max_size=1, autocommit=True) as pool:
        async with tx(pool, str(client)) as conn:
            return await run_subscriptions_due(conn, client, FakeClock(now), **kwargs)


async def test_cancel_at_period_end_wins_over_renewal_at_the_same_instant(
    pg_container: object, app_dsn: str
) -> None:
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        leaving = await add_subscription(
            conn, tenant, cancel_at_period_end=True, renews_at=NOW, version=4
        )
        staying = await add_subscription(conn, tenant, renews_at=NOW)
        later = await add_subscription(
            conn, tenant, cancel_at_period_end=True, renews_at=NOW + timedelta(seconds=1)
        )

        assert await run(app_dsn, tenant) == ZERO | {"cancelled": 1, "renewed": 1}

        gone = await fetch_subscription(conn, leaving)
        assert gone["status"] == "cancelled"
        assert gone["cancelled_at"] == NOW
        assert gone["cancel_at_period_end"] is False
        assert gone["renews_at"] == NOW  # not advanced
        assert gone["version"] == 5
        events = await fetch_events(conn, leaving)
        assert [(e["actor"], e["action"], e["from_status"], e["to_status"]) for e in events] == [
            (ACTOR, "cancelled_at_period_end", "active", "cancelled")
        ]
        assert events[0]["proposal_id"] is None
        renewed = await fetch_subscription(conn, staying)
        assert renewed["status"] == "active" and renewed["version"] == 2
        assert renewed["renews_at"] == at("2026-04-10 20:00")
        assert [e["action"] for e in await fetch_events(conn, staying)] == ["renewed"]
        untouched = await fetch_subscription(conn, later)
        assert untouched["status"] == "active" and untouched["version"] == 1
        assert await fetch_events(conn, later) == []


async def test_pending_plan_applies_at_plan_price_or_is_skipped_when_archived(
    pg_container: object, app_dsn: str
) -> None:
    due = NOW - timedelta(hours=1)
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        # the plan's price moved from 80 after the change was scheduled; today's price applies
        await conn.execute("UPDATE plans SET price=95 WHERE client_id=%s AND code='pro'", (tenant,))
        changing = await add_subscription(
            conn, tenant, pending_plan="pro", pending_effective_at=due
        )
        archived = await add_subscription(
            conn, tenant, pending_plan="retired", pending_effective_at=due
        )
        missing = await add_subscription(
            conn, tenant, pending_plan="ghost", pending_effective_at=due
        )
        cancelled = await add_subscription(
            conn,
            tenant,
            status="cancelled",
            pending_plan="pro",
            pending_effective_at=due,
            renews_at=None,
        )
        future = await add_subscription(
            conn, tenant, pending_plan="pro", pending_effective_at=NOW + timedelta(days=1)
        )

        assert await run(app_dsn, tenant) == ZERO | {"plan_changed": 1, "pending_skipped": 3}

        done = await fetch_subscription(conn, changing)
        assert (done["plan"], done["price"], done["pending_plan"], done["version"]) == (
            "pro",
            95,
            None,
            2,
        )
        assert done["pending_effective_at"] is None and done["status"] == "active"
        (event,) = await fetch_events(conn, changing)
        assert (event["actor"], event["action"]) == (ACTOR, "plan_changed")
        assert (event["from_status"], event["to_status"]) == ("active", "active")
        assert event["payload"] == {
            "old_plan": "basic",
            "new_plan": "pro",
            "old_price": 50,
            "new_price": 95,
        }
        for sub, plan, status in [
            (archived, "retired", "active"),
            (missing, "ghost", "active"),
            (cancelled, "pro", "cancelled"),
        ]:
            row = await fetch_subscription(conn, sub)
            assert (row["plan"], row["price"], row["status"]) == ("basic", 50, status)
            assert row["pending_plan"] is None and row["pending_effective_at"] is None
            assert row["version"] == 2
            (skipped,) = await fetch_events(conn, sub)
            assert (skipped["actor"], skipped["action"]) == (ACTOR, "pending_change_skipped")
            assert (skipped["from_status"], skipped["to_status"]) == (status, status)
            assert skipped["payload"] == {"plan": plan}
        waiting = await fetch_subscription(conn, future)
        assert waiting["pending_plan"] == "pro" and waiting["version"] == 1
        assert await fetch_events(conn, future) == []


async def test_resume_date_reactivates_and_repairs_past_renewal(
    pg_container: object, app_dsn: str
) -> None:
    # 20:00 UTC on the 10th is already the 11th for a Kuala Lumpur tenant
    now = datetime(2026, 3, 10, 20, 0, tzinfo=UTC)
    paused = {"status": "paused", "paused_at": now - timedelta(days=30)}
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        lapsed = await add_subscription(
            conn, tenant, **paused, resume_at=date(2026, 3, 11), renews_at=at("2026-02-01 09:00")
        )
        yearly = await add_subscription(
            conn,
            tenant,
            **paused,
            plan="annual",
            resume_at=date(2026, 3, 1),
            renews_at=at("2026-02-01 09:00"),
        )
        current = await add_subscription(
            conn, tenant, **paused, resume_at=date(2026, 3, 11), renews_at=at("2026-03-25 09:00")
        )
        tomorrow = await add_subscription(
            conn, tenant, **paused, resume_at=date(2026, 3, 12), renews_at=at("2026-02-01 09:00")
        )
        open_ended = await add_subscription(conn, tenant, **paused, renews_at=at("2026-02-01"))

        assert await run(app_dsn, tenant, now) == ZERO | {"resumed": 3}

        row = await fetch_subscription(conn, lapsed)
        assert row["status"] == "active" and row["version"] == 2
        assert row["paused_at"] is None and row["resume_at"] is None
        # one month after now on the tenant calendar: 11 Mar 04:00 local -> 11 Apr 04:00 local
        assert row["renews_at"] == at("2026-04-11 04:00")
        (event,) = await fetch_events(conn, lapsed)
        assert (event["actor"], event["action"]) == (ACTOR, "resumed")
        assert (event["from_status"], event["to_status"]) == ("paused", "active")
        assert (await fetch_subscription(conn, yearly))["renews_at"] == at("2027-03-11 04:00")
        kept = await fetch_subscription(conn, current)
        assert kept["status"] == "active" and kept["renews_at"] == at("2026-03-25 09:00")
        for sub in (tomorrow, open_ended):
            row = await fetch_subscription(conn, sub)
            assert row["status"] == "paused" and row["version"] == 1
            assert await fetch_events(conn, sub) == []
        # a second pass finds nothing: the repaired renewal is in the future
        assert await run(app_dsn, tenant, now) == ZERO


@pytest.mark.parametrize(
    ("plan", "renews_at", "expected"),
    [
        ("basic", "2026-01-31 10:00", "2026-02-28 10:00"),
        ("basic", "2028-01-31 10:00", "2028-02-29 10:00"),
        ("annual", "2028-02-29 10:00", "2029-02-28 10:00"),
        ("ghost", "2026-01-31 10:00", "2026-02-28 10:00"),
        # 28 Feb 17:00 UTC is already 1 Mar for the tenant: renews on its 1 Apr, not UTC's 28 Mar
        ("basic", "2026-03-01 01:00", "2026-04-01 01:00"),
    ],
)
async def test_renewal_advances_by_interval_month_and_year(
    pg_container: object, app_dsn: str, plan: str, renews_at: str, expected: str
) -> None:
    now = datetime.fromisoformat(renews_at + "+08:00") + timedelta(hours=1)
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        sub = await add_subscription(conn, tenant, plan=plan, renews_at=at(renews_at))

        assert await run(app_dsn, tenant, now) == ZERO | {"renewed": 1}

        row = await fetch_subscription(conn, sub)
        assert row["renews_at"] == at(expected)
        assert (row["status"], row["price"], row["version"]) == ("active", 50, 2)
        (event,) = await fetch_events(conn, sub)
        assert (event["actor"], event["action"]) == (ACTOR, "renewed")
        assert (event["from_status"], event["to_status"]) == ("active", "active")
        assert datetime.fromisoformat(event["payload"]["renews_at"]) == at(expected)
        assert datetime.fromisoformat(event["payload"]["previous_renews_at"]) == at(renews_at)


async def test_renewal_catch_up_advances_one_interval_per_run(
    pg_container: object, app_dsn: str
) -> None:
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        sub = await add_subscription(conn, tenant, renews_at=at("2025-10-31 10:00"))

        assert await run(app_dsn, tenant) == ZERO | {"renewed": 1}
        first = await fetch_subscription(conn, sub)
        assert first["renews_at"] == at("2025-11-30 10:00")
        assert first["renewal_anchor"] == at("2025-10-31 10:00")
        assert len(await fetch_events(conn, sub)) == 1

        assert await run(app_dsn, tenant) == ZERO | {"renewed": 1}
        second = await fetch_subscription(conn, sub)
        # the second step stays on the cycle: 31 Dec, not 30 Dec
        assert second["renews_at"] == at("2025-12-31 10:00")
        assert second["renewal_anchor"] == at("2025-10-31 10:00")
        assert second["version"] == 3


async def test_null_anchor_renews_from_renews_at_then_keeps_the_day(
    pg_container: object, app_dsn: str
) -> None:
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        sub = await add_subscription(conn, tenant, renews_at=at("2026-01-31 10:00"))

        assert await run(app_dsn, tenant) == ZERO | {"renewed": 1}
        first = await fetch_subscription(conn, sub)
        assert first["renews_at"] == at("2026-02-28 10:00")
        assert first["renewal_anchor"] == at("2026-01-31 10:00")

        assert await run(app_dsn, tenant) == ZERO | {"renewed": 1}
        second = await fetch_subscription(conn, sub)
        assert second["renews_at"] == at("2026-03-31 10:00")
        assert second["renewal_anchor"] == at("2026-01-31 10:00")


async def _assert_anchored_renewal(
    pg_container: object, app_dsn: str, anchor: str, renews_at: str, expected: str
) -> None:
    now = at(renews_at) + timedelta(hours=1)
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        sub = await add_subscription(
            conn, tenant, renews_at=at(renews_at), renewal_anchor=at(anchor)
        )

        assert await run(app_dsn, tenant, now) == ZERO | {"renewed": 1}

        row = await fetch_subscription(conn, sub)
        assert row["renews_at"] == at(expected)
        assert row["renewal_anchor"] == at(anchor)
        (event,) = await fetch_events(conn, sub)
        assert datetime.fromisoformat(event["payload"]["renews_at"]) == at(expected)
        assert datetime.fromisoformat(event["payload"]["previous_renews_at"]) == at(renews_at)


async def test_anchored_renewal_returns_to_the_day(pg_container: object, app_dsn: str) -> None:
    # a short month got the renewal to the 30th; the cycle's day is the 31st
    await _assert_anchored_renewal(
        pg_container, app_dsn, "2026-10-31 10:00", "2026-11-30 10:00", "2026-12-31 10:00"
    )


async def test_extended_anchor_is_followed(pg_container: object, app_dsn: str) -> None:
    # an extension moved the cycle to the chosen date
    await _assert_anchored_renewal(
        pg_container, app_dsn, "2026-12-15 10:00", "2026-12-15 10:00", "2027-01-15 10:00"
    )


async def test_job_lapsed_resume_restarts_the_cycle(pg_container: object, app_dsn: str) -> None:
    # 04:00 UTC on 31 Jan is 12:00 on the 31st in Kuala Lumpur
    now = datetime(2026, 1, 31, 4, 0, tzinfo=UTC)
    paused = {
        "status": "paused",
        "paused_at": now - timedelta(days=30),
        "resume_at": date(2026, 1, 31),
        "renewal_anchor": at("2025-12-15 09:00"),
    }
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        lapsed = await add_subscription(conn, tenant, **paused, renews_at=at("2026-01-01 09:00"))
        no_renewal = await add_subscription(conn, tenant, **paused, renews_at=None)
        current = await add_subscription(conn, tenant, **paused, renews_at=at("2026-02-15 09:00"))

        assert await run(app_dsn, tenant, now) == ZERO | {"resumed": 3}

        for sub in (lapsed, no_renewal):
            row = await fetch_subscription(conn, sub)
            assert row["renewal_anchor"] == now
            # one month after the resume on the tenant calendar, clamped: 31 Jan -> 28 Feb
            assert row["renews_at"] == at("2026-02-28 12:00")
        kept = await fetch_subscription(conn, current)
        assert kept["renews_at"] == at("2026-02-15 09:00")
        assert kept["renewal_anchor"] == at("2025-12-15 09:00")


async def test_batch_size_isolation_and_idempotency(pg_container: object, app_dsn: str) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
        tenant, other = await seed_tenants(conn)
        locked = await add_subscription(conn, tenant, renews_at=NOW - timedelta(days=3))
        second = await add_subscription(conn, tenant, renews_at=NOW - timedelta(days=2))
        third = await add_subscription(conn, tenant, renews_at=NOW - timedelta(days=1))
        foreign = await add_subscription(conn, other, renews_at=NOW - timedelta(days=1))
        async with db.build_pool(app_dsn, max_size=2, autocommit=True) as pool:

            async def job(client: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
                async with tx(pool, str(tenant)) as tenant_conn:
                    return await run_subscriptions_due(
                        tenant_conn, client, FakeClock(NOW), **kwargs
                    )

            async with await psycopg.AsyncConnection.connect(owner_dsn) as blocker:
                await blocker.execute(
                    "SELECT id FROM subscriptions WHERE id=%s FOR UPDATE", (locked,)
                )
                # batch_size=1 takes one row; the locked (oldest) row is skipped, not waited for
                assert await job(tenant, batch_size=1) == ZERO | {"renewed": 1}
                assert (await fetch_subscription(conn, second))["version"] == 2
                assert (await fetch_subscription(conn, third))["version"] == 1
                assert await job(tenant) == ZERO | {"renewed": 1}
                assert await job(tenant) == ZERO
            assert await job(tenant) == ZERO | {"renewed": 1}  # the released row
            assert await job(tenant) == ZERO
            # another tenant's id under this tenant's session finds nothing
            assert await job(other) == ZERO

    async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
        for sub in (locked, second, third):
            assert (await fetch_subscription(conn, sub))["version"] == 2
            assert len(await fetch_events(conn, sub)) == 1
        untouched = await fetch_subscription(conn, foreign)
        assert untouched["version"] == 1 and untouched["renews_at"] == NOW - timedelta(days=1)
        assert await fetch_events(conn, foreign) == []


async def test_registry_contains_subscriptions_due_every_five_minutes() -> None:
    job = build_default_registry().get("subscriptions_due")
    assert job is not None
    assert job.every == timedelta(minutes=5)
    assert job.per_client is True
    assert job.fn is run_subscriptions_due


async def test_period_end_cancel_clears_a_pending_plan_change(
    pg_container: object, app_dsn: str
) -> None:
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        later = NOW + timedelta(days=3)
        leaving = await add_subscription(
            conn,
            tenant,
            cancel_at_period_end=True,
            renews_at=NOW - timedelta(minutes=1),
            pending_plan="pro",
            pending_effective_at=later,
        )
        plain = await add_subscription(
            conn, tenant, cancel_at_period_end=True, renews_at=NOW - timedelta(minutes=1)
        )

        assert await run(app_dsn, tenant) == ZERO | {"cancelled": 2}

        gone = await fetch_subscription(conn, leaving)
        assert gone["status"] == "cancelled" and gone["plan"] == "basic"
        assert gone["pending_plan"] is None and gone["pending_effective_at"] is None
        (event,) = await fetch_events(conn, leaving)
        assert event["action"] == "cancelled_at_period_end"
        assert event["payload"] == {"cleared_pending_plan": "pro"}
        (event,) = await fetch_events(conn, plain)
        assert event["payload"] == {"cleared_pending_plan": None}
        # The cleared change is not picked up later as a skipped one.
        assert await run(app_dsn, tenant, later + timedelta(hours=1)) == ZERO
        assert len(await fetch_events(conn, leaving)) == 1


async def test_resume_with_no_renewal_date_starts_a_period(
    pg_container: object, app_dsn: str
) -> None:
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        sub = await add_subscription(
            conn,
            tenant,
            status="paused",
            paused_at=NOW - timedelta(days=5),
            resume_at=date(2026, 3, 10),
            renews_at=None,
        )

        assert await run(app_dsn, tenant) == ZERO | {"resumed": 1}

        row = await fetch_subscription(conn, sub)
        assert row["status"] == "active" and row["version"] == 2
        # 12:00 UTC on 10 Mar is 20:00 in Kuala Lumpur; one month later on that calendar
        assert row["renews_at"] == at("2026-04-10 20:00")
        (event,) = await fetch_events(conn, sub)
        assert event["action"] == "resumed"
        assert datetime.fromisoformat(event["payload"]["renews_at"]) == at("2026-04-10 20:00")


@pytest.mark.parametrize("package", [None, "requested", "suspended"])
async def test_tenant_without_an_active_package_is_left_alone(
    pg_container: object, app_dsn: str, package: str | None
) -> None:
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, other = await seed_tenants(conn)
        if package is None:
            await conn.execute("DELETE FROM console_service_packages WHERE client_id=%s", (tenant,))
        else:
            await conn.execute(
                "UPDATE console_service_packages SET status=%s WHERE client_id=%s",
                (package, tenant),
            )
        due = NOW - timedelta(hours=1)
        subs = [
            await add_subscription(conn, tenant, cancel_at_period_end=True, renews_at=due),
            await add_subscription(conn, tenant, pending_plan="pro", pending_effective_at=due),
            await add_subscription(
                conn, tenant, status="paused", resume_at=date(2026, 3, 1), renews_at=due
            ),
            await add_subscription(conn, tenant, renews_at=due),
        ]
        active_tenant = await add_subscription(conn, other, renews_at=due)

        assert await run(app_dsn, tenant) == ZERO

        for sub in subs:
            assert (await fetch_subscription(conn, sub))["version"] == 1
            assert await fetch_events(conn, sub) == []
        assert await run(app_dsn, other) == ZERO | {"renewed": 1}
        assert (await fetch_subscription(conn, active_tenant))["version"] == 2


async def test_scheduled_interval_change_resets_the_anchor(
    pg_container: object, app_dsn: str
) -> None:
    due = NOW - timedelta(hours=1)
    old_anchor = at("2026-01-15 10:00")
    renews_at = at("2026-03-20 10:00")
    async with await psycopg.AsyncConnection.connect(
        owner_dsn_for(pg_container), autocommit=True
    ) as conn:
        tenant, _ = await seed_tenants(conn)
        fields = {"pending_effective_at": due, "renews_at": renews_at, "renewal_anchor": old_anchor}
        to_yearly = await add_subscription(conn, tenant, pending_plan="annual", **fields)
        to_monthly = await add_subscription(
            conn, tenant, plan="annual", price=500, pending_plan="basic", **fields
        )
        same_interval = await add_subscription(conn, tenant, pending_plan="pro", **fields)
        skipped = await add_subscription(conn, tenant, pending_plan="ghost", **fields)
        no_renewal = await add_subscription(
            conn, tenant, pending_plan="annual", **(fields | {"renews_at": None})
        )

        assert await run(app_dsn, tenant) == ZERO | {"plan_changed": 4, "pending_skipped": 1}

        for sub in (to_yearly, to_monthly):
            row = await fetch_subscription(conn, sub)
            assert row["renewal_anchor"] == renews_at
            assert row["renews_at"] == renews_at
        for sub, plan in [(same_interval, "pro"), (skipped, "basic")]:
            row = await fetch_subscription(conn, sub)
            assert row["plan"] == plan
            assert row["renewal_anchor"] == old_anchor
            assert row["renews_at"] == renews_at
        row = await fetch_subscription(conn, no_renewal)
        assert row["plan"] == "annual"
        assert row["renewal_anchor"] is None and row["renews_at"] is None
