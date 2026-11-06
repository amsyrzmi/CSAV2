"""Disconnect, buyer-data retention and catch-up on a disposable Postgres; synthetic data only.

Every test seeds its own tenant with its own synthetic shop id (migration 0491 allows one active
store per shop). The purge and catch-up jobs run with an injectable clock; disconnects stamp the
database's own clock, so each test's clock starts at the real time and moves forward from there.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from csa.core import db
from csa.core.tenancy import tx
from csa.jobs.clock import FakeClock
from csa.jobs.runner import build_default_registry
from csa.marketplace import retention
from csa.marketplace.disconnect import (
    StoreNotFoundError,
    disconnect_in_transaction,
    disconnect_store,
)
from csa.marketplace.jobs import enqueue_job
from csa.marketplace.retention import (
    BUYER_DATA_DAYS,
    DISCONNECT_PURGE_DAYS,
    run_marketplace_catch_up,
    run_marketplace_purge,
)
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_ecommerce_grants import seed
from tests.test_tiktok_token_refresh import connected

pytest_plugins = ["tests.test_ecommerce_grants"]
pytestmark = [pytest.mark.asyncio]
needs_db = requires_docker

DAY = timedelta(days=1)


@pytest_asyncio.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(app_dsn, max_size=3)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def owner(pg_container: object) -> Any:
    with psycopg.connect(
        owner_dsn_for(pg_container), autocommit=True, row_factory=cast(Any, dict_row)
    ) as conn:
        yield conn


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime.now(UTC) + timedelta(seconds=1))


async def store_of(owner_dsn: str, pool: AsyncConnectionPool) -> tuple[UUID, UUID, str]:
    grant = await connected(owner_dsn, pool)
    binding = grant.context.binding
    return binding.client_id, binding.record_id, binding.shop_id


def add_order(
    owner: Any,
    client: UUID,
    store: UUID | None,
    *,
    status: str,
    updated: datetime | None,
    channel: str = "tiktok_shop",
    ship_to: dict[str, str] | None = None,
    synced: datetime | None = None,
) -> str:
    order_id = f"ord_{uuid4().hex[:16]}"
    owner.execute(
        "INSERT INTO orders(id,client_id,store_id,channel,external_order_id,status,total,"
        "ship_to,provider_updated_at,items,payment_status,synced_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,12.50,%s,%s,%s,'paid',%s)",
        (
            order_id,
            client,
            store,
            channel,
            f"ext-{order_id}",
            status,
            json.dumps(ship_to if ship_to is not None else {"recipient_name": "Synthetic Buyer"}),
            updated,
            json.dumps([{"sku": "SYN-1", "qty": 1}]),
            synced,
        ),
    )
    return order_id


def add_listing(owner: Any, client: UUID, store: UUID) -> str:
    sku = f"sku-{uuid4().hex[:12]}"
    owner.execute(
        "INSERT INTO marketplace_listings(client_id,store_id,channel,external_product_id,"
        "external_sku_id,product_name,price) VALUES (%s,%s,'tiktok_shop',%s,%s,'Synthetic item',9)",
        (client, store, f"prod-{sku}", sku),
    )
    return sku


def order_row(owner: Any, order_id: str) -> dict[str, Any]:
    row = owner.execute("SELECT * FROM orders WHERE id=%s", (order_id,)).fetchone()
    assert row is not None
    return row


def count(owner: Any, table: str, client: UUID, store: UUID | None = None) -> int:
    where = "client_id=%s" + (" AND store_id=%s" if store else "")
    params = (client, store) if store else (client,)
    row = owner.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", params).fetchone()
    return int(row["n"])


def credential(owner: Any, store: UUID) -> dict[str, Any]:
    row = owner.execute("SELECT * FROM channel_credentials WHERE id=%s", (store,)).fetchone()
    assert row is not None
    return row


async def purge(
    pool: AsyncConnectionPool, client: UUID, clock: FakeClock, **kwargs: int
) -> dict[str, int]:
    return await in_tenant(pool, client, run_marketplace_purge, clock, **kwargs)


async def in_tenant(
    pool: AsyncConnectionPool,
    client: UUID,
    job: Callable[..., Awaitable[dict[str, int]]],
    clock: FakeClock,
    **kwargs: int,
) -> dict[str, int]:
    async with tx(pool, str(client)) as conn:
        return await job(conn, client, clock, **kwargs)


def synthetic_store(owner: Any, client: UUID, *, shop: str | None, **columns: Any) -> UUID:
    """A second store row for a tenant; the shop id is unique (0491), or None for pending."""
    store = uuid4()
    owner.execute(
        "INSERT INTO channel_credentials(id,client_id,channel,shop_id) "
        "VALUES (%s,%s,'tiktok_shop',%s)",
        (store, client, shop),
    )
    for column, value in columns.items():
        owner.execute(
            f"UPDATE channel_credentials SET {column}=%s WHERE id=%s",
            (value, store),
        )
    return store


def attempt_for(
    owner: Any,
    client: UUID,
    store: UUID,
    *,
    created: datetime,
    expires: datetime,
    status: str = "pending",
) -> UUID:
    attempt = uuid4()
    context = {
        "binding": {"client_id": str(client), "record_id": str(store), "platform": "tiktok_shop"},
        "app_id": "SYNTH-APP",
        "app_revision": 1,
        "credential_generation": 1,
    }
    owner.execute(
        "INSERT INTO ecommerce_authorization_attempts(id,client_id,context,state_hash,"
        "created_at,expires_at,status,consumed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            attempt,
            client,
            json.dumps(context),
            uuid4().hex + uuid4().hex,
            created,
            expires,
            status,
            created if status == "consumed" else None,
        ),
    )
    return attempt


@needs_db
async def test_disconnect_nulls_tokens_cancels_jobs_and_keeps_orders(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    other_client, other_store, _ = await store_of(migrated_dsn, pool)
    before = credential(owner, store)
    assert before["access_token"] and before["refresh_token"] and before["disconnected_at"] is None
    order = add_order(
        owner,
        client,
        store,
        status="shipped",
        updated=datetime.now(UTC),
        ship_to={"recipient_name": "Synthetic Buyer"},
    )
    add_listing(owner, client, store)
    async with tx(pool, str(client)) as conn:
        await enqueue_job(conn, client_id=client, store_id=store, kind="backfill_orders")
        await enqueue_job(
            conn, client_id=client, store_id=store, kind="order_refresh", target="o-1"
        )
    async with tx(pool, str(other_client)) as conn:
        await enqueue_job(conn, client_id=other_client, store_id=other_store, kind="catch_up")
    grant = owner.execute(
        "SELECT state FROM ecommerce_grants WHERE record_id=%s", (store,)
    ).fetchall()
    assert [row["state"] for row in grant] == ["active"]

    with capture_logs() as logs:
        await disconnect_store(pool, client, store, reason="deauthorized")

    after = credential(owner, store)
    assert after["disconnected_at"] is not None
    assert after["access_token"] is None and after["refresh_token"] is None
    # The 0320 fence bumped the generation once and disconnected the active grant.
    assert after["credential_generation"] == before["credential_generation"] + 1
    states = owner.execute(
        "SELECT state FROM ecommerce_grants WHERE record_id=%s", (store,)
    ).fetchall()
    assert [row["state"] for row in states] == ["disconnected"]
    jobs = owner.execute(
        "SELECT status,last_error_code FROM marketplace_sync_jobs WHERE store_id=%s", (store,)
    ).fetchall()
    assert len(jobs) == 2 and {row["status"] for row in jobs} == {"cancelled"}
    assert {row["last_error_code"] for row in jobs} == {"store_disconnected"}
    # Orders and listings stay as they were, read-only; another tenant's job is untouched.
    assert order_row(owner, order)["ship_to"] == {"recipient_name": "Synthetic Buyer"}
    assert order_row(owner, order)["buyer_cleared_at"] is None
    assert count(owner, "marketplace_listings", client, store) == 1
    assert count(owner, "marketplace_sync_jobs", other_client, other_store) == 1
    assert owner.execute(
        "SELECT status FROM marketplace_sync_jobs WHERE store_id=%s", (other_store,)
    ).fetchone() == {"status": "pending"}
    # The reason is only logged, as a constant (no migration may add a column for it).
    events = [entry for entry in logs if entry.get("event") == "marketplace_store_disconnected"]
    assert len(events) == 1 and events[0]["reason"] == "deauthorized"
    assert "token" not in json.dumps(events, default=str).lower()

    # Idempotent: a second call changes nothing and is not an error.
    await disconnect_store(pool, client, store, reason="admin")
    again = credential(owner, store)
    assert again["disconnected_at"] == after["disconnected_at"]
    assert again["credential_generation"] == after["credential_generation"]


@needs_db
async def test_disconnect_refuses_foreign_missing_and_pending_stores(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    other_client, other_store, _ = await store_of(migrated_dsn, pool)
    pending = synthetic_store(owner, client, shop=None)
    other_before = credential(owner, other_store)
    pending_before = credential(owner, pending)
    for target_client, target in (
        (client, other_store),  # another tenant's store, seen from this tenant
        (client, uuid4()),  # missing
        (client, pending),  # no shop yet: nothing to disconnect
    ):
        with pytest.raises(StoreNotFoundError) as caught:
            await disconnect_store(pool, target_client, target, reason="admin")
        assert caught.value.code == "store_not_found" and str(caught.value) == "store_not_found"
    assert credential(owner, other_store) == other_before
    assert credential(owner, pending) == pending_before
    assert credential(owner, store)["disconnected_at"] is None
    assert other_client != client


@needs_db
async def test_purge_clears_buyer_data_90_days_after_delivery_keeps_totals(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    assert BUYER_DATA_DAYS == 90
    client, store, _ = await store_of(migrated_dsn, pool)
    other_client, other_store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    old = now - (BUYER_DATA_DAYS + 1) * DAY
    delivered = add_order(owner, client, store, status="delivered", updated=old)
    cancelled = add_order(owner, client, store, status="cancelled", updated=old)
    recent = add_order(owner, client, store, status="delivered", updated=now - 89 * DAY)
    shipped = add_order(owner, client, store, status="shipped", updated=old)
    pending = add_order(owner, client, store, status="pending_confirmation", updated=old)
    local = add_order(owner, client, None, status="delivered", updated=old, channel="telegram")
    foreign = add_order(owner, other_client, other_store, status="delivered", updated=old)
    snapshot = {key: order_row(owner, key) for key in (delivered, cancelled, recent, local)}

    result = await purge(pool, client, clock)

    assert result["buyer_data_aged"] == 2
    for key in (delivered, cancelled):
        row = order_row(owner, key)
        assert row["ship_to"] is None and row["buyer_cleared_at"] == now
        # Number, totals, status, dates and items stay.
        for column in ("external_order_id", "total", "status", "placed_at", "items", "currency"):
            assert row[column] == snapshot[key][column]
        assert row["provider_updated_at"] == old
    for key in (recent, shipped, pending, local, foreign):
        row = order_row(owner, key)
        assert row["ship_to"] == {"recipient_name": "Synthetic Buyer"}
        assert row["buyer_cleared_at"] is None
    # A second run finds nothing more.
    again = await purge(pool, client, clock)
    assert again["buyer_data_aged"] == 0
    # Once the 89-day order passes 90 days it is cleared too, from the same clock.
    clock.advance(2 * DAY)
    assert (await purge(pool, client, clock))["buyer_data_aged"] == 1
    assert order_row(owner, recent)["ship_to"] is None


@needs_db
async def test_purge_is_bounded_by_batch_size(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    old = clock.now() - 100 * DAY
    for _ in range(3):
        add_order(owner, client, store, status="delivered", updated=old)
    runs = [(await purge(pool, client, clock, batch_size=2))["buyer_data_aged"] for _ in range(3)]
    assert runs == [2, 1, 0]


@needs_db
async def test_purge_after_30_days_disconnected_deletes_listings_clears_orders(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    assert DISCONNECT_PURGE_DAYS == 30
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    # A: disconnected 31 days ago. B: only 29 days ago. C: still connected (the seeded store).
    a = synthetic_store(owner, client, shop=f"SYN-SHOP-{uuid4()}", disconnected_at=now - 31 * DAY)
    b = synthetic_store(owner, client, shop=f"SYN-SHOP-{uuid4()}", disconnected_at=now - 29 * DAY)
    orders = {
        key: add_order(owner, client, target, status="shipped", updated=now - 5 * DAY)
        for key, target in (("a", a), ("b", b), ("c", store))
    }
    add_listing(owner, client, a)
    add_listing(owner, client, a)
    add_listing(owner, client, b)
    add_listing(owner, client, store)
    kept = order_row(owner, orders["a"])

    result = await purge(pool, client, clock)

    assert result["buyer_data_disconnected"] == 1 and result["listings_deleted"] == 2
    cleared = order_row(owner, orders["a"])
    assert cleared["ship_to"] is None and cleared["buyer_cleared_at"] == now
    for column in ("external_order_id", "total", "status", "items", "provider_updated_at"):
        assert cleared[column] == kept[column]
    assert count(owner, "marketplace_listings", client, a) == 0
    for target, key in ((b, "b"), (store, "c")):
        assert count(owner, "marketplace_listings", client, target) == 1
        assert order_row(owner, orders[key])["ship_to"] is not None
    # Nothing left to do on a second run; B's turn comes when it passes 30 days.
    again = await purge(pool, client, clock)
    assert again["buyer_data_disconnected"] == 0 and again["listings_deleted"] == 0
    clock.advance(2 * DAY)
    third = await purge(pool, client, clock)
    assert third["buyer_data_disconnected"] == 1 and third["listings_deleted"] == 1
    assert count(owner, "marketplace_listings", client, store) == 1


@needs_db
async def test_purge_makes_a_reconnected_store_copy_its_products_again(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    a = synthetic_store(owner, client, shop=f"SYN-SHOP-{uuid4()}", disconnected_at=now - 31 * DAY)
    synced = now - 40 * DAY
    products_part = {"page_token": "tok", "started_at": synced.isoformat()}
    orders_part = {
        "page_token": "otok",
        "updated_from": synced.isoformat(),
        "updated_to": now.isoformat(),
    }
    states = (
        (a, json.dumps({"orders": orders_part, "products": products_part})),
        (store, None),
    )
    for target, cursor in states:
        owner.execute(
            "INSERT INTO marketplace_store_state(store_id,client_id,orders_synced_at,"
            "products_synced_at,catch_up_cursor,backfill_cursor) VALUES (%s,%s,%s,%s,%s,%s)",
            (target, client, synced, synced, synced, cursor),
        )
    add_listing(owner, client, a)
    add_listing(owner, client, store)

    result = await purge(pool, client, clock)

    assert result["listings_deleted"] == 1 and result["products_reset"] == 1

    def state(target: UUID) -> dict[str, Any]:
        row = owner.execute(
            "SELECT * FROM marketplace_store_state WHERE store_id=%s", (target,)
        ).fetchone()
        assert row is not None
        return cast(dict[str, Any], row)

    purged = state(a)
    # Products forget they were copied; orders keep their progress and checkpoint.
    assert purged["products_synced_at"] is None
    assert purged["orders_synced_at"] == synced and purged["catch_up_cursor"] == synced
    checkpoint = json.loads(purged["backfill_cursor"])
    assert checkpoint["products"] is None and checkpoint["orders"] == orders_part
    # A store that kept its listings is not touched.
    assert state(store)["products_synced_at"] == synced
    # Nothing more to reset on a second run.
    assert (await purge(pool, client, clock))["products_reset"] == 0


@needs_db
async def test_reconnect_within_30_days_resumes_without_purge(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    order = add_order(owner, client, store, status="shipped", updated=clock.now())
    add_listing(owner, client, store)
    await disconnect_store(pool, client, store, reason="admin")
    clock.advance(29 * DAY)
    assert (await purge(pool, client, clock))["listings_deleted"] == 0
    # Reconnect (replace_authorized_credential.sql clears disconnected_at with new tokens).
    owner.execute(
        "UPDATE channel_credentials SET access_token='x', refresh_token='y', "
        "disconnected_at=NULL WHERE id=%s",
        (store,),
    )
    clock.advance(40 * DAY)
    result = await purge(pool, client, clock)
    assert result["listings_deleted"] == 0 and result["buyer_data_disconnected"] == 0
    assert count(owner, "marketplace_listings", client, store) == 1
    assert order_row(owner, order)["ship_to"] is not None
    assert credential(owner, store)["disconnected_at"] is None


@needs_db
async def test_abandoned_pending_store_removed_but_one_with_live_attempt_kept(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    old = now - 2 * DAY
    abandoned = synthetic_store(owner, client, shop=None, updated_at=old)
    expired_attempt = synthetic_store(owner, client, shop=None, updated_at=old)
    attempt_for(
        owner,
        client,
        expired_attempt,
        created=old,
        expires=old + timedelta(minutes=10),
    )
    live = synthetic_store(owner, client, shop=None, updated_at=old)
    live_attempt = attempt_for(
        owner, client, live, created=now - timedelta(minutes=1), expires=now + timedelta(minutes=9)
    )
    fresh = synthetic_store(owner, client, shop=None, updated_at=now - timedelta(hours=1))
    gone_but_marked = synthetic_store(owner, client, shop=None, updated_at=old)
    owner.execute(
        "INSERT INTO marketplace_sync_jobs(client_id,store_id,kind) VALUES (%s,%s,'catch_up')",
        (client, abandoned),
    )
    owner.execute(
        "INSERT INTO marketplace_store_state(store_id,client_id) VALUES (%s,%s)",
        (abandoned, client),
    )

    result = await purge(pool, client, clock)

    assert result["pending_stores_deleted"] == 3  # abandoned, expired attempt, gone_but_marked
    ids = {
        row["id"]
        for row in owner.execute(
            "SELECT id FROM channel_credentials WHERE client_id=%s", (client,)
        ).fetchall()
    }
    assert ids == {store, live, fresh}
    assert abandoned not in ids and expired_attempt not in ids and gone_but_marked not in ids
    # Dependent rows went with the store; the live attempt is untouched and still pending.
    assert count(owner, "marketplace_sync_jobs", client, abandoned) == 0
    assert count(owner, "marketplace_store_state", client, abandoned) == 0
    assert owner.execute(
        "SELECT status FROM ecommerce_authorization_attempts WHERE id=%s", (live_attempt,)
    ).fetchone() == {"status": "pending"}
    # Once the live attempt has expired and a day has passed, the store goes too.
    clock.advance(2 * DAY)
    assert (await purge(pool, client, clock))["pending_stores_deleted"] == 2
    assert {
        row["id"]
        for row in owner.execute(
            "SELECT id FROM channel_credentials WHERE client_id=%s", (client,)
        ).fetchall()
    } == {store}


@needs_db
async def test_pending_store_with_a_grant_is_never_deleted(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    """Grants are lifecycle evidence; a row that ever held one stays even if it has no shop."""
    seeded = await seed(migrated_dsn)
    binding = seeded.context.binding
    owner.execute(
        "INSERT INTO ecommerce_grants(id,client_id,record_id,platform,shop_id,app_id,"
        "credential_generation,state,source,scopes,verified_at,expires_at) "
        "VALUES (%s,%s,%s,'tiktok_shop','S','A',1,'disconnected',"
        "'verified_authorization_exchange','{}',now()-interval '3 day',now()-interval '2 day')",
        (uuid4(), binding.client_id, binding.record_id),
    )
    owner.execute(
        "UPDATE channel_credentials SET shop_id=NULL, updated_at=%s WHERE id=%s",
        (clock.now() - 5 * DAY, binding.record_id),
    )
    result = await purge(pool, binding.client_id, clock)
    assert result["pending_stores_deleted"] == 0
    assert credential(owner, binding.record_id)["id"] == binding.record_id


@needs_db
async def test_expired_store_is_disconnected_after_30_days(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    """Rule: a store a refused refresh marked expired (tokens cleared, expires_at set)
    becomes disconnected 30 days after expires_at; a reconnect before then clears the state.
    """
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    expired_long = synthetic_store(
        owner,
        client,
        shop=f"SYN-SHOP-{uuid4()}",
        expires_at=now - 31 * DAY,
    )
    expired_recent = synthetic_store(
        owner,
        client,
        shop=f"SYN-SHOP-{uuid4()}",
        expires_at=now - 29 * DAY,
    )
    # Tokens present with an old expiry is a refresh that keeps failing, not a refusal.
    unrefreshed = synthetic_store(
        owner,
        client,
        shop=f"SYN-SHOP-{uuid4()}",
        access_token="cipher-a",
        refresh_token="cipher-b",
        expires_at=now - 40 * DAY,
    )
    async with tx(pool, str(client)) as conn:
        await enqueue_job(conn, client_id=client, store_id=expired_long, kind="catch_up")

    with capture_logs() as logs:
        result = await purge(pool, client, clock)

    assert result["expired_disconnected"] == 1
    assert credential(owner, expired_long)["disconnected_at"] == now
    assert credential(owner, expired_recent)["disconnected_at"] is None
    assert credential(owner, unrefreshed)["disconnected_at"] is None
    assert credential(owner, store)["disconnected_at"] is None
    assert owner.execute(
        "SELECT status FROM marketplace_sync_jobs WHERE store_id=%s", (expired_long,)
    ).fetchone() == {"status": "cancelled"}
    events = [entry for entry in logs if entry.get("event") == "marketplace_store_disconnected"]
    assert [entry["reason"] for entry in events] == ["expired"]
    # Its own 30 days of retention start from the day it was disconnected.
    add_listing(owner, client, expired_long)
    assert (await purge(pool, client, clock))["listings_deleted"] == 0
    clock.advance(31 * DAY)
    assert (await purge(pool, client, clock))["listings_deleted"] == 1


@needs_db
async def test_catch_up_enqueues_one_job_per_connected_store(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    other_client, other_store, _ = await store_of(migrated_dsn, pool)
    pending = synthetic_store(owner, client, shop=None)

    first = await in_tenant(pool, client, run_marketplace_catch_up, clock)

    assert first == {"stores": 1, "queued": 1, "folded": 0}
    rows = owner.execute(
        "SELECT store_id,kind,target,status FROM marketplace_sync_jobs WHERE client_id=%s",
        (client,),
    ).fetchall()
    assert rows == [{"store_id": store, "kind": "catch_up", "target": None, "status": "pending"}]
    assert pending != store
    # A second run folds into the pending job; the other tenant's store is not touched here.
    second = await in_tenant(pool, client, run_marketplace_catch_up, clock)
    assert second == {"stores": 1, "queued": 0, "folded": 1}
    assert count(owner, "marketplace_sync_jobs", client) == 1
    assert count(owner, "marketplace_sync_jobs", other_client, other_store) == 0
    await in_tenant(pool, other_client, run_marketplace_catch_up, clock)
    assert count(owner, "marketplace_sync_jobs", other_client, other_store) == 1


@needs_db
@pytest.mark.parametrize("how", ["disconnected", "expired"])
async def test_catch_up_skips_stores_that_held_an_active_grant_and_lost_it(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock, how: str
) -> None:
    """Real stores with a bound grant: the control run queues one, then the store is
    disconnected (Disconnect) or expired (a refused refresh), and the filter excludes it."""
    client, store, _ = await store_of(migrated_dsn, pool)
    grants = "SELECT state FROM ecommerce_grants WHERE record_id=%s"
    assert [row["state"] for row in owner.execute(grants, (store,)).fetchall()] == ["active"]
    control = await in_tenant(pool, client, run_marketplace_catch_up, clock)
    assert control == {"stores": 1, "queued": 1, "folded": 0}
    if how == "disconnected":
        await disconnect_store(pool, client, store, reason="admin")
    else:
        owner.execute(
            "UPDATE channel_credentials SET access_token=NULL, refresh_token=NULL, "
            "expires_at=%s WHERE id=%s",
            (clock.now() - DAY, store),
        )
    assert [row["state"] for row in owner.execute(grants, (store,)).fetchall()] == ["disconnected"]
    owner.execute("UPDATE marketplace_sync_jobs SET status='done' WHERE store_id=%s", (store,))

    result = await in_tenant(pool, client, run_marketplace_catch_up, clock)

    assert result == {"stores": 0, "queued": 0, "folded": 0}
    statuses = owner.execute(
        "SELECT status FROM marketplace_sync_jobs WHERE store_id=%s", (store,)
    ).fetchall()
    assert [row["status"] for row in statuses] == ["done"]


@needs_db
async def test_aged_purge_uses_synced_at_when_tiktok_gave_no_update_time(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    old = now - 100 * DAY
    untimed_old = add_order(owner, client, store, status="delivered", updated=None, synced=old)
    untimed_new = add_order(
        owner, client, store, status="cancelled", updated=None, synced=now - 10 * DAY
    )
    no_time = add_order(owner, client, store, status="delivered", updated=None, synced=None)
    timed = add_order(owner, client, store, status="delivered", updated=now - 5 * DAY, synced=old)

    result = await purge(pool, client, clock)

    assert result["buyer_data_aged"] == 1
    assert order_row(owner, untimed_old)["ship_to"] is None
    assert order_row(owner, untimed_old)["buyer_cleared_at"] == now
    # A recent sync, a fresh TikTok update time, or no time at all (nothing to age) keep it.
    for key in (untimed_new, no_time, timed):
        assert order_row(owner, key)["ship_to"] is not None


@needs_db
async def test_retention_boundaries_are_exact(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    second = timedelta(seconds=1)
    on_90 = add_order(owner, client, store, status="delivered", updated=now - 90 * DAY)
    under_90 = add_order(owner, client, store, status="delivered", updated=now - 90 * DAY + second)
    on_30 = synthetic_store(
        owner, client, shop=f"SYN-SHOP-{uuid4()}", disconnected_at=now - 30 * DAY
    )
    under_30 = synthetic_store(
        owner, client, shop=f"SYN-SHOP-{uuid4()}", disconnected_at=now - 30 * DAY + second
    )
    on_30_order = add_order(owner, client, on_30, status="shipped", updated=now)
    under_30_order = add_order(owner, client, under_30, status="shipped", updated=now)
    add_listing(owner, client, on_30)
    add_listing(owner, client, under_30)
    expired_on = synthetic_store(
        owner, client, shop=f"SYN-SHOP-{uuid4()}", expires_at=now - 30 * DAY
    )
    expired_under = synthetic_store(
        owner, client, shop=f"SYN-SHOP-{uuid4()}", expires_at=now - 30 * DAY + second
    )

    result = await purge(pool, client, clock)

    assert result["buyer_data_aged"] == 1 and result["buyer_data_disconnected"] == 1
    assert result["listings_deleted"] == 1 and result["expired_disconnected"] == 1
    assert order_row(owner, on_90)["ship_to"] is None
    assert order_row(owner, under_90)["ship_to"] is not None
    assert order_row(owner, on_30_order)["ship_to"] is None
    assert order_row(owner, under_30_order)["ship_to"] is not None
    assert count(owner, "marketplace_listings", client, on_30) == 0
    assert count(owner, "marketplace_listings", client, under_30) == 1
    assert credential(owner, expired_on)["disconnected_at"] == now
    assert credential(owner, expired_under)["disconnected_at"] is None


@needs_db
async def test_expired_conversion_leaves_a_store_reconnected_before_the_disconnect(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    """The list said expired; a reconnect committed (tokens back) before the disconnect ran."""
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    cutoff = now - 30 * DAY
    owner.execute(
        "UPDATE channel_credentials SET access_token=NULL, refresh_token=NULL, expires_at=%s "
        "WHERE id=%s",
        (now - 31 * DAY, store),
    )
    async with tx(pool, str(client)) as conn:
        await enqueue_job(conn, client_id=client, store_id=store, kind="catch_up")
        listed = await db.q.fetch_all(
            "marketplace/list_expired_stores.sql",
            {"client_id": client, "expired_cutoff": cutoff, "batch_size": 10},
            conn=conn,
        )
    assert [row["id"] for row in listed] == [store]
    # The reconnect lands between the list and the disconnect.
    owner.execute(
        "UPDATE channel_credentials SET access_token='new-a', refresh_token='new-b', "
        "expires_at=%s, disconnected_at=NULL WHERE id=%s",
        (now + DAY, store),
    )
    before = credential(owner, store)

    async with tx(pool, str(client)) as conn:
        done = await disconnect_in_transaction(
            conn, client, store, reason="expired", now=now, expired_before=cutoff
        )

    assert done is False
    assert credential(owner, store) == before
    assert before["access_token"] == "new-a" and before["disconnected_at"] is None
    assert owner.execute(
        "SELECT status FROM marketplace_sync_jobs WHERE store_id=%s", (store,)
    ).fetchone() == {"status": "pending"}
    # Without the guard (an Admin's Disconnect) the same call does disconnect it.
    async with tx(pool, str(client)) as conn:
        assert await disconnect_in_transaction(conn, client, store, reason="admin", now=now)
    assert credential(owner, store)["access_token"] is None


@needs_db
async def test_expired_conversion_skips_a_store_whose_row_is_locked_by_a_reconnect(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    client, store, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    owner.execute(
        "UPDATE channel_credentials SET access_token=NULL, refresh_token=NULL, expires_at=%s "
        "WHERE id=%s",
        (now - 31 * DAY, store),
    )
    with owner.transaction():  # a reconnect in flight holds the row lock
        owner.execute("SELECT id FROM channel_credentials WHERE id=%s FOR UPDATE", (store,))
        skipped = await purge(pool, client, clock)
    assert skipped["expired_disconnected"] == 0
    assert credential(owner, store)["disconnected_at"] is None
    assert (await purge(pool, client, clock))["expired_disconnected"] == 1


@needs_db
async def test_pending_store_with_a_consumed_attempt_is_kept_until_it_expires(
    owner: Any, migrated_dsn: str, pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    """The callback's first transaction consumed the attempt; the exchange is under way."""
    client, _, _ = await store_of(migrated_dsn, pool)
    now = clock.now()
    old = now - 2 * DAY
    mid_flight = synthetic_store(owner, client, shop=None, updated_at=old)
    attempt_for(
        owner,
        client,
        mid_flight,
        created=now - timedelta(minutes=2),
        expires=now + timedelta(minutes=8),
        status="consumed",
    )
    stale = synthetic_store(owner, client, shop=None, updated_at=old)
    attempt_for(
        owner,
        client,
        stale,
        created=old,
        expires=old + timedelta(minutes=10),
        status="consumed",
    )

    result = await purge(pool, client, clock)

    assert result["pending_stores_deleted"] == 1
    assert credential(owner, mid_flight)["id"] == mid_flight
    assert owner.execute("SELECT 1 FROM channel_credentials WHERE id=%s", (stale,)).fetchall() == []
    clock.advance(timedelta(minutes=9))
    assert (await purge(pool, client, clock))["pending_stores_deleted"] == 1


def test_default_registry_has_both_jobs() -> None:
    registry = build_default_registry()
    purge_job = registry.get("marketplace_purge")
    catch_up = registry.get("marketplace_catch_up")
    assert purge_job is not None and catch_up is not None
    assert purge_job.every == timedelta(hours=6) and purge_job.per_client is True
    assert catch_up.every == timedelta(hours=3) and catch_up.per_client is True
    assert purge_job.fn is retention.run_marketplace_purge
    assert catch_up.fn is retention.run_marketplace_catch_up
