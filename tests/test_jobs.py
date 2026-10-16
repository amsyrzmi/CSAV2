"""Tests for LG10 scheduled jobs, remade expiry sweep, and stock reconciliation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import FakeClock
from csa.jobs.orders import run_order_draft_expiry, run_orphan_draft_expiry
from csa.jobs.reapers import run_mute_expiry
from csa.jobs.reconcile import run_reservation_reconcile
from csa.jobs.registry import Job, JobRegistry
from csa.jobs.runner import JobRunner

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_worker import _configure_test_settings

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.usefixtures("lg_setup_dsn")]


async def test_mute_expiry_batch_isolation_locks_and_idempotency(
    pg_container: object, app_dsn: str
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    tenant, other, contact = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    expired, locked, future, indefinite, ai_owned, foreign = [uuid.uuid4() for _ in range(6)]
    async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
        for client in (tenant, other):
            await conn.execute(
                "INSERT INTO clients(id,slug,name,status,platform_version) "
                "VALUES (%s,%s,'Synthetic mute','active','lg10-test')",
                (client, f"mute-{client}"),
            )
        await conn.execute(
            "INSERT INTO contacts(id,client_id,full_name) VALUES (%s,%s,'Synthetic')",
            (contact, tenant),
        )
        for ident, client, owner, mute in [
            (expired, tenant, "human_support", datetime.now(UTC) - timedelta(minutes=10)),
            (locked, tenant, "human_support", datetime.now(UTC) - timedelta(minutes=5)),
            (future, tenant, "human_support", datetime.now(UTC) + timedelta(hours=1)),
            (indefinite, tenant, "human_support", None),
            (ai_owned, tenant, "ai", datetime.now(UTC) - timedelta(minutes=1)),
            (foreign, other, "human_support", datetime.now(UTC) - timedelta(minutes=10)),
        ]:
            await conn.execute(
                "INSERT INTO conversations(id,client_id,contact_id,channel,channel_thread,"
                "status,owned_by,ai_muted_until) VALUES (%s,%s,%s,'telegram',%s,"
                "'escalated',%s,%s)",
                (ident, client, contact, str(ident), owner, mute),
            )
    async with db.build_pool(app_dsn, max_size=2, autocommit=True) as pool:
        async with await psycopg.AsyncConnection.connect(owner_dsn) as blocker:
            await blocker.execute("SELECT id FROM conversations WHERE id=%s FOR UPDATE", (locked,))
            async with tx(pool, str(tenant)) as conn:
                assert await run_mute_expiry(conn, tenant, FakeClock(), batch_size=1) == {
                    "released_count": 1
                }
            async with tx(pool, str(tenant)) as conn:
                assert await run_mute_expiry(conn, tenant, FakeClock()) == {"released_count": 0}
        async with tx(pool, str(tenant)) as conn:
            assert await run_mute_expiry(conn, tenant, FakeClock()) == {"released_count": 1}
        async with tx(pool, str(tenant)) as conn:
            assert await run_mute_expiry(conn, tenant, FakeClock()) == {"released_count": 0}
        async with tx(pool, str(tenant)) as conn:
            assert await run_mute_expiry(conn, other, FakeClock()) == {"released_count": 0}
    async with await psycopg.AsyncConnection.connect(owner_dsn) as conn:
        cursor = await conn.execute(
            "SELECT id,status,owned_by,ai_muted_until FROM conversations WHERE id=ANY(%s)",
            ([expired, locked, future, indefinite, ai_owned, foreign],),
        )
        rows = {row[0]: row[1:] for row in await cursor.fetchall()}
    assert rows[expired] == rows[locked] == ("open", "ai", None)
    assert rows[future][0:2] == rows[indefinite][0:2] == ("escalated", "human_support")
    assert rows[ai_owned][0:2] == ("escalated", "ai")
    assert rows[foreign][0:2] == ("escalated", "human_support")


@pytest.mark.parametrize("per_client", [True, False])
async def test_job_failures_do_not_report_success_or_refresh_health(
    pg_container: object, app_dsn: str, per_client: bool
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    clock = FakeClock()
    failing = True
    visited: list[uuid.UUID | None] = []
    clients = [uuid.uuid4(), uuid.uuid4()]

    async def job_fn(conn, *args, batch_size):
        client = args[0] if per_client else None
        visited.append(client)
        if failing and (not per_client or client == clients[0]):
            raise RuntimeError("synthetic scheduled failure")
        return {"checked": True}

    async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True) as conn:
        for client in clients:
            await conn.execute(
                "INSERT INTO clients(id,slug,name,status,platform_version) "
                "VALUES (%s,%s,'Synthetic job','active','lg10-test')",
                (client, f"job-{client}"),
            )
    registry = JobRegistry()
    registry.register(Job("synthetic_failure", timedelta(minutes=1), job_fn, per_client=per_client))
    async with (
        db.build_pool(owner_dsn, max_size=2, autocommit=True) as owner_pool,
        db.build_pool(app_dsn, max_size=2, autocommit=True) as app_pool,
    ):
        runner = JobRunner(
            owner_pool, app_pool, lock_conninfo=owner_dsn, registry=registry, clock=clock
        )
        assert await runner.run_job_once("synthetic_failure") is False
        if per_client:
            assert all(client in visited for client in clients)
        for _ in range(4):
            clock.advance(timedelta(minutes=1))
            assert await runner.run_job_once("synthetic_failure") is False
        assert not runner.is_healthy("synthetic_failure")
        failing = False
        assert await runner.run_job_once("synthetic_failure") is True
        assert runner.is_healthy("synthetic_failure")
        clock.advance(timedelta(minutes=4))
        failing = True
        assert await runner.run_job_once("synthetic_failure") is False
        assert not runner.is_healthy("synthetic_failure")
    async with await psycopg.AsyncConnection.connect(owner_dsn) as conn:
        cursor = await conn.execute(
            "SELECT ok,error FROM job_runs WHERE job='synthetic_failure' ORDER BY id"
        )
        rows = await cursor.fetchall()
    assert any(not ok and "synthetic scheduled failure" in error for ok, error in rows)
    assert any(ok for ok, _ in rows)


async def test_order_draft_expiry_per_product_aggregation(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that expired draft reservations are released aggregated per product without leak."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    p1 = uuid.uuid4()
    p2 = uuid.uuid4()
    ord1 = f"ORD-EXP-{uuid.uuid4().hex[:6]}"
    ord2 = f"ORD-EXP-{uuid.uuid4().hex[:6]}"

    # Setup tenant and products with initial stock and reservations
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version) "
            "VALUES (%s, %s, %s, 'active', 'lg10-test')",
            (client_id, f"client-{client_id.hex[:8]}", "Client Expiry"),
        )
        conn.execute(
            "INSERT INTO products (id, client_id, sku, name, price, stock_qty, reserved_qty) "
            "VALUES (%s, %s, 'SKU1', 'Product 1', 10.00, 100, 15), "
            "       (%s, %s, 'SKU2', 'Product 2', 20.00, 100, 5)",
            (p1, client_id, p2, client_id),
        )
        # ord1 has TWO lines of SKU1 (5 + 5 = 10)
        conn.execute(
            "INSERT INTO orders (id, client_id, status, total) VALUES (%s, %s, 'draft', 100.00)",
            (ord1, client_id),
        )
        conn.execute(
            "INSERT INTO order_items "
            "(id, client_id, order_id, product_id, sku, name_snapshot, qty, unit_price) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'SKU1', 'Product 1', 5, 10.00), "
            "       (gen_random_uuid(), %s, %s, %s, 'SKU1', 'Product 1', 5, 10.00)",
            (client_id, ord1, p1, client_id, ord1, p1),
        )
        # ord2 also has one line of SKU1 (5) and one line of SKU2 (5)
        conn.execute(
            "INSERT INTO orders (id, client_id, status, total) VALUES (%s, %s, 'draft', 100.00)",
            (ord2, client_id),
        )
        conn.execute(
            "INSERT INTO order_items "
            "(id, client_id, order_id, product_id, sku, name_snapshot, qty, unit_price) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'SKU1', 'Product 1', 5, 10.00), "
            "       (gen_random_uuid(), %s, %s, %s, 'SKU2', 'Product 2', 5, 20.00)",
            (client_id, ord2, p1, client_id, ord2, p2),
        )
        contact_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Expiry Contact')",
            (contact_id, client_id),
        )
        # Create expired confirmations for both orders
        expired_at = datetime.now(UTC) - timedelta(minutes=5)
        conn.execute(
            "INSERT INTO order_confirmations "
            "(id, client_id, contact_id, order_id, token, state, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'pending', %s), "
            "       (gen_random_uuid(), %s, %s, %s, %s, 'pending', %s)",
            (
                client_id,
                contact_id,
                ord1,
                f"tok-{ord1}",
                expired_at,
                client_id,
                contact_id,
                ord2,
                f"tok-{ord2}",
                expired_at,
            ),
        )

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    clock = FakeClock()
    try:
        async with tx(pool, str(client_id)) as conn:
            stats = await run_order_draft_expiry(conn, client_id, clock, batch_size=100)

        assert stats["confirmations"] == 2
        assert stats["orders_cancelled"] == 2
        assert stats["products_released"] == 2  # Both p1 and p2 released

        # Verify that reserved_qty on both products was released by the exact combined sum
        with psycopg.connect(owner_dsn) as conn:
            row_p1 = conn.execute("SELECT reserved_qty FROM products WHERE id=%s", (p1,)).fetchone()
            row_p2 = conn.execute("SELECT reserved_qty FROM products WHERE id=%s", (p2,)).fetchone()
            row_o1 = conn.execute("SELECT status FROM orders WHERE id=%s", (ord1,)).fetchone()
            row_o2 = conn.execute("SELECT status FROM orders WHERE id=%s", (ord2,)).fetchone()
            events = conn.execute(
                "SELECT event, actor FROM order_events WHERE client_id=%s ORDER BY created_at",
                (client_id,),
            ).fetchall()

        assert row_p1 is not None and row_p2 is not None
        assert row_o1 is not None and row_o2 is not None
        # SKU1 originally had 15 reserved; 10 from ord1 and 5 from ord2 -> 0 remaining
        assert row_p1[0] == 0
        # SKU2 originally had 5 reserved; 5 from ord2 -> 0 remaining
        assert row_p2[0] == 0
        assert row_o1[0] == "cancelled"
        assert row_o2[0] == "cancelled"
        assert len(events) == 2
        assert all(e[0] == "expired" and e[1] == "system" for e in events)

        # Idempotency check: running again changes nothing
        async with tx(pool, str(client_id)) as conn:
            repeat_stats = await run_order_draft_expiry(conn, client_id, clock, batch_size=100)
        assert repeat_stats["confirmations"] == 0
        assert repeat_stats["orders_cancelled"] == 0
    finally:
        await pool.close()


async def test_orphan_draft_expiry(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that abandoned drafts older than 2x TTL are cancelled and released."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    p1 = uuid.uuid4()
    ord1 = f"ORD-ORPHAN-{uuid.uuid4().hex[:6]}"

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version) "
            "VALUES (%s, %s, %s, 'active', 'lg10-test')",
            (client_id, f"client-{client_id.hex[:8]}", "Client Orphan"),
        )
        conn.execute(
            "INSERT INTO products (id, client_id, sku, name, price, stock_qty, reserved_qty) "
            "VALUES (%s, %s, 'SKU-ORP', 'Product Orp', 10.00, 100, 3)",
            (p1, client_id),
        )
        conn.execute(
            "INSERT INTO orders (id, client_id, status, total, created_at) "
            "VALUES (%s, %s, 'draft', 30.00, now() - interval '1 hour')",
            (ord1, client_id),
        )
        conn.execute(
            "INSERT INTO order_items "
            "(id, client_id, order_id, product_id, sku, name_snapshot, qty, unit_price) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'SKU-ORP', 'Product Orp', 3, 10.00)",
            (client_id, ord1, p1),
        )

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    clock = FakeClock()
    try:
        async with tx(pool, str(client_id)) as conn:
            stats = await run_orphan_draft_expiry(conn, client_id, clock, ttl=timedelta(minutes=15))

        assert stats["orders_cancelled"] == 1
        assert stats["products_released"] == 1

        with psycopg.connect(owner_dsn) as conn:
            row_p1 = conn.execute("SELECT reserved_qty FROM products WHERE id=%s", (p1,)).fetchone()
            row_ord = conn.execute("SELECT status FROM orders WHERE id=%s", (ord1,)).fetchone()
        assert row_p1 is not None and row_ord is not None
        assert row_p1[0] == 0
        assert row_ord[0] == "cancelled"
    finally:
        await pool.close()


async def test_reservation_reconcile_and_repair(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that reservation reconciliation reports drift accurately and repair fixes it."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    p1 = uuid.uuid4()
    ord1 = f"ORD-REC-{uuid.uuid4().hex[:6]}"

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version) "
            "VALUES (%s, %s, %s, 'active', 'lg10-test')",
            (client_id, f"client-{client_id.hex[:8]}", "Client Reconcile"),
        )
        # Seed intentional drift: reserved_qty is 10, but only 4 is active in order_items
        conn.execute(
            "INSERT INTO products (id, client_id, sku, name, price, stock_qty, reserved_qty) "
            "VALUES (%s, %s, 'SKU-DRIFT', 'Drifting Product', 10.00, 100, 10)",
            (p1, client_id),
        )
        conn.execute(
            "INSERT INTO orders (id, client_id, status, total) VALUES (%s, %s, 'draft', 40.00)",
            (ord1, client_id),
        )
        conn.execute(
            "INSERT INTO order_items "
            "(id, client_id, order_id, product_id, sku, name_snapshot, qty, unit_price) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'SKU-DRIFT', 'Drifting Product', 4, 10.00)",
            (client_id, ord1, p1),
        )

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    clock = FakeClock()
    try:
        # 1. Report mode: detect drift
        async with tx(pool, str(client_id)) as conn:
            report = await run_reservation_reconcile(conn, client_id, clock, repair=False)

        assert report["mode"] == "report"
        assert report["drift_count"] == 1
        d = report["drifted"][0]
        assert d["sku"] == "SKU-DRIFT"
        assert d["actual"] == 10
        assert d["expected"] == 4
        assert d["drift"] == 6

        # 2. Repair mode: align reserved_qty to expected
        async with tx(pool, str(client_id)) as conn:
            repair = await run_reservation_reconcile(conn, client_id, clock, repair=True)

        assert repair["mode"] == "repair"
        assert repair["drift_count"] == 1
        assert repair["repaired"][0]["old_reserved"] == 10
        assert repair["repaired"][0]["new_reserved"] == 4

        # 3. Post-repair verification: zero drift
        async with tx(pool, str(client_id)) as conn:
            clean = await run_reservation_reconcile(conn, client_id, clock, repair=False)
        assert clean["drift_count"] == 0
    finally:
        await pool.close()


async def test_job_runner_advisory_lock_and_run_log(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that JobRunner respects distributed advisory lock and writes to job_runs."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version) "
            "VALUES (%s, %s, %s, 'active', 'lg10-test')",
            (client_id, f"client-{client_id.hex[:8]}", "Client Runner"),
        )

    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)

    clock = FakeClock()
    runner = JobRunner(
        owner_pool,
        app_pool,
        lock_conninfo=owner_dsn,
        clock=clock,
    )

    try:
        # Run order_draft_expiry via the runner
        ran = await runner.run_job_once("order_draft_expiry")
        assert ran is True

        # Check job_runs table
        with psycopg.connect(owner_dsn) as conn:
            runs = conn.execute(
                "SELECT job, client_id, ok, stats FROM job_runs WHERE client_id=%s ORDER BY id",
                (client_id,),
            ).fetchall()
        assert len(runs) >= 1
        assert runs[0][0] == "order_draft_expiry"
        assert runs[0][1] == client_id
        assert runs[0][2] is True

        # Lock contention simulation: hold the advisory lock in an external connection
        lock_conn = await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True)
        try:
            await lock_conn.execute(
                "SELECT pg_advisory_lock(hashtextextended('job:order_draft_expiry', 0))"
            )
            # Runner should gracefully skip the tick and return False
            ran_again = await runner.run_job_once("order_draft_expiry")
            assert ran_again is False
        finally:
            await lock_conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended('job:order_draft_expiry', 0))"
            )
            await lock_conn.close()
    finally:
        await owner_pool.close()
        await app_pool.close()


async def test_job_runner_due_and_heartbeat_health(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that JobRunner tracks due intervals and heartbeat health."""
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version) "
            "VALUES (%s, %s, %s, 'active', 'lg10-test')",
            (client_id, f"client-{client_id.hex[:8]}", "Client Heartbeat"),
        )

    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)

    clock = FakeClock()
    runner = JobRunner(
        owner_pool,
        app_pool,
        lock_conninfo=owner_dsn,
        clock=clock,
    )

    try:
        # Initially healthy
        assert runner.is_healthy("order_draft_expiry") is True

        # Run due jobs at t=0
        results = await runner.run_due_jobs()
        assert "order_draft_expiry" in results
        assert results["order_draft_expiry"] is True

        # Immediately after, not due again
        results2 = await runner.run_due_jobs()
        assert "order_draft_expiry" not in results2

        # Advance clock by 2 minutes (> 1 min every) -> due again
        clock.advance(timedelta(minutes=2))
        assert runner.is_healthy("order_draft_expiry") is True
        results3 = await runner.run_due_jobs()
        assert "order_draft_expiry" in results3

        # Advance clock past 3x interval (4 minutes) without running -> unhealthy
        clock.advance(timedelta(minutes=4))
        assert runner.is_healthy("order_draft_expiry") is False
    finally:
        await owner_pool.close()
        await app_pool.close()


async def test_property_stock_reservation_integrity(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Property test: random order/confirmation/expiry sequences maintain zero stock drift."""
    import random

    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)

    client_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    products = [uuid.uuid4() for _ in range(3)]

    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, status, platform_version) "
            "VALUES (%s, %s, %s, 'active', 'lg10-property')",
            (client_id, f"client-{client_id.hex[:8]}", "Client Property"),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Property Contact')",
            (contact_id, client_id),
        )
        for idx, pid in enumerate(products):
            conn.execute(
                "INSERT INTO products (id, client_id, sku, name, price, stock_qty, reserved_qty) "
                "VALUES (%s, %s, %s, %s, 10.00, 1000, 0)",
                (pid, client_id, f"SKU-{idx}", f"Product {idx}"),
            )

    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    clock = FakeClock()

    rng = random.Random(42)
    active_orders: list[str] = []

    try:
        # Run 25 random cycles of order creation, item addition, confirmation, and expiry
        for step in range(25):
            action = rng.choice(["create", "expire"])
            if action == "create" or not active_orders:
                ord_id = f"ORD-PROP-{step}-{uuid.uuid4().hex[:4]}"
                qty = rng.randint(1, 5)
                chosen_p = rng.choice(products)
                async with tx(pool, str(client_id)) as conn:
                    await conn.execute(
                        "INSERT INTO orders (id, client_id, status, total) "
                        "VALUES (%s, %s, 'draft', 10.00)",
                        (ord_id, client_id),
                    )
                    await conn.execute(
                        "INSERT INTO order_items "
                        "(id, client_id, order_id, product_id, sku, name_snapshot, "
                        "qty, unit_price) "
                        "VALUES (gen_random_uuid(), %s, %s, %s, 'SKU', 'Product', %s, 10.00)",
                        (client_id, ord_id, chosen_p, qty),
                    )
                    await conn.execute(
                        "UPDATE products SET reserved_qty = reserved_qty + %s WHERE id = %s",
                        (qty, chosen_p),
                    )
                    await conn.execute(
                        "INSERT INTO order_confirmations "
                        "(id, client_id, contact_id, order_id, token, state, expires_at) "
                        "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'pending', %s)",
                        (
                            client_id,
                            contact_id,
                            ord_id,
                            f"tok-{ord_id}",
                            clock.now() + timedelta(minutes=5),
                        ),
                    )
                active_orders.append(ord_id)
            else:
                # Expire by advancing time and running the expiry sweep
                clock.advance(timedelta(minutes=10))
                async with tx(pool, str(client_id)) as conn:
                    await run_order_draft_expiry(conn, client_id, clock)
                active_orders.clear()

            # Invariant check: reservation_reconcile must ALWAYS report 0 drift at every step!
            async with tx(pool, str(client_id)) as conn:
                recon = await run_reservation_reconcile(conn, client_id, clock)
            assert recon["drift_count"] == 0, f"drift detected at step {step}: {recon}"
    finally:
        await pool.close()
