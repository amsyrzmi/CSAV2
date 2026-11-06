"""Local order flows never touch synced TikTok orders (spec 6.3, last bullet).

Database tests on a disposable migrated Postgres; every order, shop and customer is synthetic.
A synced order is a marketplace row with a provider order id; the local flows (draft expiry, the
24 hour approval expiry, reservation reconcile, the order agent's confirm, cancel and status, and
staff approval) must read and write only local orders.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any, cast

import psycopg
import pytest
import pytest_asyncio
from csa.contracts.plan import Task
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import FakeClock
from csa.jobs.orders import run_order_draft_expiry, run_orphan_draft_expiry
from csa.jobs.reconcile import run_reservation_reconcile
from csa.marketplace.store import upsert_order
from psycopg.rows import dict_row

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_marketplace_store import order_for
from tests.test_order_agent import World, _approve, _seed_target

pytestmark = [requires_docker, pytest.mark.asyncio]

LONG_AGO = timedelta(days=3)
THIRTY_HOURS = timedelta(hours=30)


@pytest_asyncio.fixture
async def world(migrated_dsn: str, pg_container: object, app_dsn: str) -> AsyncIterator[World]:
    pool = db.build_pool(app_dsn, max_size=8)
    await pool.open(wait=True)
    try:
        yield World(owner_dsn_for(pg_container), pool)
    finally:
        await pool.close()


@pytest.fixture
def owner(pg_container: object) -> Iterator[Any]:
    with psycopg.connect(
        owner_dsn_for(pg_container), autocommit=True, row_factory=cast(Any, dict_row)
    ) as conn:
        yield conn


class Shop:
    """One synthetic TikTok store in the world's tenant; the shop id is unique per tenant."""

    def __init__(self, world: World, owner: Any) -> None:
        self.world = world
        self.owner = owner
        self.store = uuid.uuid4()
        self.shop = f"SYNTH-SHOP-{self.store}"
        owner.execute(
            "INSERT INTO channel_credentials(id,client_id,channel,shop_id) "
            "VALUES (%s,%s,'tiktok_shop',%s)",
            (self.store, world.client_id, self.shop),
        )

    async def sync(
        self,
        *,
        tiktok_status: str,
        local_status: str,
        age: timedelta,
        linked: bool = False,
    ) -> str:
        """Land one synced order through the real mapping and upsert, then age it.

        ``local_status`` overrides the mapped status (a synthetic draft is not something sync
        produces, but the draft jobs must still leave it alone). ``linked`` gives the row a
        contact, which sync never does today; it proves the guards do not lean on a NULL contact.
        """
        external = f"SYNTH-{uuid.uuid4().hex[:12]}"
        order = order_for(
            self.world.client_id, self.store, self.shop, external, status=tiktok_status
        )
        async with tx(self.world.pool, str(self.world.client_id)) as conn:
            assert await upsert_order(conn, order) == "inserted"
        self.owner.execute(
            "UPDATE orders SET status=%s, created_at=now()-%s, placed_at=now()-%s, "
            "updated_at=now()-%s, contact_id=%s WHERE client_id=%s AND external_order_id=%s",
            (
                local_status,
                age,
                age,
                age,
                self.world.contact_id if linked else None,
                self.world.client_id,
                external,
            ),
        )
        return order.id


def local_order(
    world: World,
    order_id: str,
    *,
    status: str,
    age: timedelta,
    qty: int,
    channel: str = "telegram",
    sku: str = "A1",
    confirmation: bool = False,
) -> str:
    """A local order with one reserved line, aged, optionally with a lapsed pending confirmation."""
    order_id = f"{order_id}-{uuid.uuid4().hex[:6]}"  # order ids are global across tenants
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        product = conn.execute(
            "SELECT id FROM products WHERE client_id=%s AND sku=%s", (world.client_id, sku)
        ).fetchone()
        assert product is not None
        conn.execute(
            "INSERT INTO orders (id, client_id, contact_id, status, total, channel, "
            "created_at, placed_at, updated_at) "
            "VALUES (%s,%s,%s,%s,10.00,%s,now()-%s,now()-%s,now()-%s)",
            (order_id, world.client_id, world.contact_id, status, channel, age, age, age),
        )
        conn.execute(
            "INSERT INTO order_items "
            "(id, client_id, order_id, product_id, sku, name_snapshot, qty, unit_price) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, 10.00)",
            (world.client_id, order_id, product[0], sku, f"Product {sku}", qty),
        )
        conn.execute(
            "UPDATE products SET reserved_qty = reserved_qty + %s WHERE id=%s",
            (qty, product[0]),
        )
        if confirmation:
            conn.execute(
                "INSERT INTO order_confirmations "
                "(id, client_id, contact_id, order_id, token, state, expires_at) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'pending', now() - interval '5 min')",
                (world.client_id, world.contact_id, order_id, f"tok-{order_id}"),
            )
    return order_id


def add_expired_confirmation(owner: Any, world: World, order_id: str) -> None:
    owner.execute(
        "INSERT INTO order_confirmations "
        "(id, client_id, contact_id, order_id, token, state, expires_at) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'pending', now() - interval '5 min')",
        (world.client_id, world.contact_id, order_id, f"tok-{order_id}"),
    )


def frozen(owner: Any, order_id: str) -> dict[str, Any]:
    """Everything stored about an order: the row, its lines, its events and its confirmations."""
    row = owner.execute("SELECT row_to_json(o)::text AS r FROM orders o WHERE id=%s", (order_id,))
    order = row.fetchone()
    assert order is not None
    lines = owner.execute(
        "SELECT row_to_json(i)::text AS r FROM order_items i WHERE order_id=%s ORDER BY id",
        (order_id,),
    ).fetchall()
    events = owner.execute(
        "SELECT event FROM order_events WHERE order_id=%s ORDER BY id", (order_id,)
    ).fetchall()
    confirmations = owner.execute(
        "SELECT state FROM order_confirmations WHERE order_id=%s", (order_id,)
    ).fetchall()
    return {
        "order": order["r"],
        "lines": [line["r"] for line in lines],
        "events": [e["event"] for e in events],
        "confirmations": [c["state"] for c in confirmations],
    }


def status_of(owner: Any, order_id: str) -> str:
    row = owner.execute("SELECT status FROM orders WHERE id=%s", (order_id,)).fetchone()
    assert row is not None
    return cast(str, row["status"])


def event_names(owner: Any, order_id: str) -> list[str]:
    rows = owner.execute("SELECT event FROM order_events WHERE order_id=%s", (order_id,))
    return [r["event"] for r in rows.fetchall()]


async def test_draft_expiry_jobs_leave_tiktok_orders_alone(world: World, owner: Any) -> None:
    shop = Shop(world, owner)
    # Local orders that must expire: one draft with a lapsed confirmation, one orphan draft.
    with_confirmation = local_order(
        world, "LOC-CONF", status="draft", age=timedelta(hours=2), qty=2, confirmation=True
    )
    orphan = local_order(world, "LOC-ORPHAN", status="draft", age=timedelta(hours=2), qty=1)
    # Synced orders in the same states, well past both windows.
    tt_draft_conf = await shop.sync(
        tiktok_status="UNPAID", local_status="draft", age=timedelta(hours=2)
    )
    add_expired_confirmation(owner, world, tt_draft_conf)
    tt_draft_orphan = await shop.sync(
        tiktok_status="UNPAID", local_status="draft", age=timedelta(hours=2)
    )
    tt_unpaid = await shop.sync(
        tiktok_status="UNPAID", local_status="pending_confirmation", age=LONG_AGO
    )
    synced = [tt_draft_conf, tt_draft_orphan, tt_unpaid]
    before = {oid: frozen(owner, oid) for oid in synced}
    assert world.reserved("A1") == 3

    clock = FakeClock()
    async with tx(world.pool, str(world.client_id)) as conn:
        confirmations = await run_order_draft_expiry(conn, world.client_id, clock)
    async with tx(world.pool, str(world.client_id)) as conn:
        orphans = await run_orphan_draft_expiry(conn, world.client_id, clock)

    # Only the two local drafts were expired and their reservations released.
    assert confirmations["confirmations"] == 1
    assert confirmations["orders_cancelled"] == 1
    assert orphans["orders_cancelled"] == 1
    assert status_of(owner, with_confirmation) == "cancelled"
    assert status_of(owner, orphan) == "cancelled"
    assert world.reserved("A1") == 0
    # Every synced order is byte for byte what sync stored, including its confirmation row.
    assert {oid: frozen(owner, oid) for oid in synced} == before
    assert before[tt_draft_conf]["confirmations"] == ["pending"]


async def test_expire_drafts_function_leaves_tiktok_orders_alone(world: World, owner: Any) -> None:
    shop = Shop(world, owner)
    local = local_order(
        world, "LOC-FN", status="draft", age=timedelta(hours=2), qty=1, confirmation=True
    )
    tt_draft = await shop.sync(tiktok_status="UNPAID", local_status="draft", age=timedelta(hours=2))
    add_expired_confirmation(owner, world, tt_draft)
    before = frozen(owner, tt_draft)

    async with tx(world.pool, str(world.client_id)) as conn:
        cursor = await conn.execute("SELECT * FROM csa_expire_drafts()")
        row = await cursor.fetchone()
    assert row == {"expired_confirmations": 1, "cancelled_orders": 1}
    assert status_of(owner, local) == "cancelled"
    assert frozen(owner, tt_draft) == before


async def test_pending_approvals_expiry_leaves_tiktok_orders_alone(
    world: World, owner: Any
) -> None:
    shop = Shop(world, owner)
    local = local_order(world, "LOC-APPR", status="pending_confirmation", age=THIRTY_HOURS, qty=2)
    # An order taken on a TikTok chat is local (no provider order id) and still expires.
    chat = local_order(
        world,
        "LOC-CHAT",
        status="pending_confirmation",
        age=THIRTY_HOURS,
        qty=1,
        channel="tiktok_shop",
        sku="B2",  # one product per order: the baseline release updates a product row once per run
    )
    # An unpaid TikTok order maps to pending_confirmation, so the 24 hour expiry would take it.
    unpaid = await shop.sync(
        tiktok_status="UNPAID", local_status="pending_confirmation", age=THIRTY_HOURS
    )
    unpaid_linked = await shop.sync(
        tiktok_status="UNPAID", local_status="pending_confirmation", age=THIRTY_HOURS, linked=True
    )
    synced = [unpaid, unpaid_linked]
    before = {oid: frozen(owner, oid) for oid in synced}
    assert (world.reserved("A1"), world.reserved("B2")) == (2, 1)

    async with tx(world.pool, str(world.client_id)) as conn:
        cursor = await conn.execute("SELECT order_id FROM csa_expire_pending_approvals(24)")
        expired = {r["order_id"] for r in await cursor.fetchall()}

    assert expired == {local, chat}
    assert status_of(owner, local) == status_of(owner, chat) == "cancelled"
    assert event_names(owner, local) == ["cancelled"]
    assert (world.reserved("A1"), world.reserved("B2")) == (0, 0)
    assert {oid: frozen(owner, oid) for oid in synced} == before
    assert all(status_of(owner, oid) == "pending_confirmation" for oid in synced)


async def test_reservation_reconcile_ignores_tiktok_orders(world: World, owner: Any) -> None:
    shop = Shop(world, owner)
    # Synced orders in both holding statuses carry lines with no local product.
    tt_pending = await shop.sync(
        tiktok_status="UNPAID", local_status="pending_confirmation", age=LONG_AGO
    )
    tt_draft = await shop.sync(tiktok_status="UNPAID", local_status="draft", age=LONG_AGO)
    synced = [tt_pending, tt_draft]
    lines = owner.execute(
        "SELECT count(*) AS n, count(product_id) AS linked "
        "FROM order_items WHERE order_id = ANY(%s)",
        (synced,),
    ).fetchone()
    assert lines is not None and lines["n"] == 2 and lines["linked"] == 0
    before = {oid: frozen(owner, oid) for oid in synced}

    clock = FakeClock()
    async with tx(world.pool, str(world.client_id)) as conn:
        clean = await run_reservation_reconcile(conn, world.client_id, clock)
    assert clean["drift_count"] == 0

    # Real drift is still reported against local orders only: the synced lines add nothing.
    owner.execute(
        "UPDATE products SET reserved_qty = 3 WHERE client_id=%s AND sku='A1'", (world.client_id,)
    )
    async with tx(world.pool, str(world.client_id)) as conn:
        report = await run_reservation_reconcile(conn, world.client_id, clock)
    assert [(d["sku"], d["actual"], d["expected"]) for d in report["drifted"]] == [("A1", 3, 0)]
    async with tx(world.pool, str(world.client_id)) as conn:
        repair = await run_reservation_reconcile(conn, world.client_id, clock, repair=True)
    assert [(r["sku"], r["new_reserved"]) for r in repair["repaired"]] == [("A1", 0)]
    assert world.reserved("A1") == 0
    assert {oid: frozen(owner, oid) for oid in synced} == before


@pytest.mark.parametrize("linked", [False, True])
async def test_order_agent_status_never_returns_tiktok_orders(
    world: World, owner: Any, linked: bool
) -> None:
    """A customer typing a TikTok order number finds nothing, even if linked to them."""
    shop = Shop(world, owner)
    mine = local_order(world, "LOC-MINE", status="pending_confirmation", age=LONG_AGO, qty=1)
    tt_order = await shop.sync(
        tiktok_status="UNPAID",
        local_status="pending_confirmation",
        age=LONG_AGO,
        linked=linked,
    )

    typed = await world.runner.execute(
        Task(
            task_id="s1",
            agent="order_agent",
            input={"action": "status", "order_id": tt_order},
        ),
        world.ctx("status-typed"),
    )
    assert typed.ok
    assert [f.id for f in typed.data.facts] == ["no_orders"]
    assert tt_order not in repr(typed.data.facts)

    listing = await world.runner.execute(
        Task(task_id="s2", agent="order_agent", input={"action": "status"}),
        world.ctx("status-list"),
    )
    assert listing.ok
    assert [f.id for f in listing.data.facts] == [f"order_{mine}"]


@pytest.mark.parametrize("linked", [False, True])
async def test_order_confirm_and_cancel_refuse_tiktok_orders(
    world: World, owner: Any, linked: bool
) -> None:
    """Confirm, cancel, approve and reject say not found for a synced order and change nothing."""
    shop = Shop(world, owner)
    target = _seed_target(world)
    tt_order = await shop.sync(
        tiktok_status="UNPAID",
        local_status="pending_confirmation",
        age=LONG_AGO,
        linked=linked,
    )
    before = frozen(owner, tt_order)

    for action in ("confirm", "cancel"):
        callback = await world.store.issue(
            client_id=world.client_id,
            conversation_id=world.conversation_id,
            contact_id=world.contact_id,
            agent="order_agent",
            action=action,
            payload={"order_id": tt_order},
            ttl=timedelta(minutes=30),
        )
        result = await world.tap(callback, action, tt_order)
        assert result.error is not None and result.error.code == "order_not_found", action
        assert frozen(owner, tt_order) == before

    for n, action in enumerate(("approve", "reject")):
        result = await _approve(world, action, tt_order, target, f"staff-{n}")
        assert result.error is not None and result.error.code == "order_not_found", action
        assert frozen(owner, tt_order) == before
    assert status_of(owner, tt_order) == "pending_confirmation"
    assert event_names(owner, tt_order) == []
