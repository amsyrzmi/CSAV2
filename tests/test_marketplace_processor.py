"""The marketplace sync processor end to end: mocked TikTok, disposable Postgres, synthetic data.

Every TikTok call goes through the fenced reader (authorized shops, then the read), on the
processor's own transport factory, which these tests replace with ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from csa.agents.ecommerce import refresh as refresh_module
from csa.agents.ecommerce import token_refresh
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.core import db
from csa.core.tenancy import tx
from csa.marketplace import jobs
from csa.marketplace import processor as processor_module
from csa.marketplace.jobs import enqueue_job
from csa.marketplace.processor import (
    REMOVED_PRODUCT_STATUS,
    MarketplaceSyncProcessor,
    SyncConfig,
    marketplace_sync_loop,
)
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_ecommerce_storage import decrypt, encrypt
from tests.test_tiktok_product_reads import product_payload, sku_payload
from tests.test_tiktok_snapshots import extended_order
from tests.test_tiktok_token_refresh import (
    APP_KEY,
    APP_SECRET,
    CIPHER,
    NEW_ACCESS,
    OLD_ACCESS,
    OLD_REFRESH,
    connected,
    respond,
    token_payload,
)

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]

PRIVATE_TEXT = "SYNTHETIC-PRIVATE-PROVIDER-TEXT"
CONFIG = SyncConfig(
    max_attempts=2,
    stale_after_seconds=900.0,
    page_size=2,
    page_delay_seconds=0.0,
    retry_backoff_seconds=60.0,
)


@pytest_asyncio.fixture
async def owner_pool(migrated_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(migrated_dsn, max_size=2)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


class Clock:
    """A settable clock; it starts at the real time so the database's own now() agrees."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC) + timedelta(seconds=1)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def any_app_loader(pool: AsyncConnectionPool):
    """Serves every synthetic app row (each seeded tenant has its own app id)."""

    async def load_app(app_id: str) -> TikTokAppMaterial | None:
        async with pool.connection() as conn:
            row = await db.q.fetch_one("ecommerce/get_app.sql", (app_id,), conn=conn)
        if row is None or row["state"] != "active":
            return None
        return TikTokAppMaterial(
            app_id=row["app_id"],
            revision=row["revision"],
            app_key=SecretStr(APP_KEY),
            app_secret=SecretStr(APP_SECRET),
            enabled=True,
        )

    return load_app


def make_processor(
    owner_pool: AsyncConnectionPool,
    pool: AsyncConnectionPool,
    clock: Clock,
    config: SyncConfig = CONFIG,
) -> MarketplaceSyncProcessor:
    return MarketplaceSyncProcessor(
        owner_pool,
        pool,
        load_app=any_app_loader(pool),
        encrypt=encrypt,
        decrypt=decrypt,
        config=config,
        clock=clock,
    )


class FakeTikTok:
    """Mock TikTok Shop for one shop: authorized shops, order and product reads, all recorded.

    ``orders`` and ``products`` map a page token (None for the first page) to
    ``(items, next_token)``. ``fail`` maps a (path kind, page token or id) to a response factory.
    """

    def __init__(self, shop_id: str) -> None:
        self.shop_id = shop_id
        self.orders: dict[str | None, tuple[list[dict], str | None]] = {None: ([], None)}
        self.products: dict[str | None, tuple[list[dict], str | None]] = {None: ([], None)}
        self.order_details: dict[str, dict] = {}
        self.product_details: dict[str, dict] = {}
        self.fail: dict[tuple[str, str | None], Callable[[], Any]] = {}
        self.requests: list[tuple[str, str | None, dict]] = []

    def calls(self, kind: str) -> list[tuple[str | None, dict]]:
        return [(key, body) for k, key, body in self.requests if k == kind]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/authorization/202309/shops":
            self.requests.append(("shops", None, {}))
            return respond({"code": 0, "data": {"shops": [{"id": self.shop_id, "cipher": CIPHER}]}})
        assert request.headers["x-tts-access-token"] == OLD_ACCESS
        assert request.url.params["shop_cipher"] == CIPHER
        if path == "/order/202309/orders/search":
            kind, key = "order_search", request.url.params.get("page_token")
        elif path == "/order/202309/orders":
            kind, key = "order_detail", request.url.params["ids"]
        elif path == "/product/202309/products/search":
            kind, key = "product_search", request.url.params.get("page_token")
        elif path.startswith("/product/202309/products/"):
            kind, key = "product_detail", path.rsplit("/", 1)[1]
        else:  # pragma: no cover - a request the sync must never make
            raise AssertionError(f"unexpected TikTok path {path}")
        self.requests.append((kind, key, body))
        failure = self.fail.get((kind, key))
        if failure is not None:
            return failure()
        if kind == "order_search":
            items, token = self.orders[key]
            return respond({"code": 0, "data": {"orders": items, "next_page_token": token or ""}})
        if kind == "product_search":
            items, token = self.products[key]
            return respond({"code": 0, "data": {"products": items, "next_page_token": token or ""}})
        if kind == "order_detail":
            return respond({"code": 0, "data": {"orders": [self.order_details[key]]}})
        return respond({"code": 0, "data": self.product_details[key]})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeTikTok:
        monkeypatch.setattr(
            processor_module, "_new_read_transport", lambda: httpx.MockTransport(self.handler)
        )
        return self


def server_error() -> httpx.Response:
    return respond({"code": 1, "message": PRIVATE_TEXT}, status_code=500)


def order(order_id: str, *, update_time: int = 1_762_003_600, **changes: Any) -> dict:
    payload = extended_order(order_id)
    payload["update_time"] = update_time
    payload.update(changes)
    return payload


def catalogue_product(product_id: str) -> dict:
    """A product with its own SKU ids (TikTok SKU ids are unique; listings are keyed on them)."""
    payload = product_payload(product_id)
    payload["skus"] = [
        sku_payload(f"{product_id}-SKU-1"),
        sku_payload(f"{product_id}-SKU-2", quantities=(0,), seller_sku=None),
    ]
    return payload


async def store_for(migrated_dsn: str, pool: AsyncConnectionPool):
    """A connected store (active grant with product scope, stored binding) of a new tenant."""
    grant = await connected(migrated_dsn, pool, access_in=timedelta(days=5))
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE clients SET status='active' WHERE id=%s", (grant.context.binding.client_id,)
        )
    return grant.context.binding


async def only_active(migrated_dsn: str, *clients: UUID) -> None:
    """The module's database is shared: run_once must only see this test's tenants."""
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE clients SET status = "
            "CASE WHEN id = ANY(%s) THEN 'active' ELSE 'onboarding' END",
            (list(clients),),
        )


async def enqueue(pool, binding, kind, target=None) -> bool:
    async with tx(pool, str(binding.client_id)) as conn:
        return await enqueue_job(
            conn, client_id=binding.client_id, store_id=binding.record_id, kind=kind, target=target
        )


async def fetch(migrated_dsn: str, sql: str, *params: Any) -> list[dict]:
    async with await psycopg.AsyncConnection.connect(
        migrated_dsn, row_factory=cast(Any, dict_row)
    ) as conn:
        return cast(list[dict], await (await conn.execute(cast(Any, sql), params)).fetchall())


async def job_rows(migrated_dsn: str, store: UUID) -> list[dict]:
    return await fetch(
        migrated_dsn,
        "SELECT kind, target, status, attempts, last_error_code FROM marketplace_sync_jobs "
        "WHERE store_id=%s ORDER BY created_at, kind",
        store,
    )


async def state_row(migrated_dsn: str, store: UUID) -> dict | None:
    rows = await fetch(
        migrated_dsn, "SELECT * FROM marketplace_store_state WHERE store_id=%s", store
    )
    return rows[0] if rows else None


async def order_ids(migrated_dsn: str, client: UUID) -> list[str]:
    rows = await fetch(
        migrated_dsn,
        "SELECT external_order_id FROM orders WHERE client_id=%s ORDER BY external_order_id",
        client,
    )
    return [r["external_order_id"] for r in rows]


async def run_until_idle(
    processor: MarketplaceSyncProcessor, limit: int = 20, *, clock: Clock | None = None
) -> int:
    """Run rounds until no job is handled; with ``clock``, each round is a second later."""
    handled = 0
    for _ in range(limit):
        count = await processor.run_once()
        if clock is not None:
            clock.advance(1)
        if count == 0:
            return handled
        handled += count
    raise AssertionError("the queue never went idle")


async def test_backfill_pages_and_resumes_from_checkpoint_after_crash(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.orders = {
        None: ([order("SYNTH-O1"), order("SYNTH-O2")], "SYNTH-PAGE-2"),
        "SYNTH-PAGE-2": ([order("SYNTH-O3"), order("SYNTH-O4")], "SYNTH-PAGE-3"),
        "SYNTH-PAGE-3": ([order("SYNTH-O5")], None),
    }
    clock = Clock()
    started = clock.now
    processor = make_processor(owner_pool, pool, clock)
    assert await enqueue(pool, binding, "backfill_orders") is True

    # The worker dies while reading page 2: the job stays claimed, page 1 is checkpointed.
    def crash() -> httpx.Response:
        raise asyncio.CancelledError

    tiktok.fail[("order_search", "SYNTH-PAGE-2")] = crash
    with pytest.raises(asyncio.CancelledError):
        await processor.run_once()
    assert await order_ids(migrated_dsn, binding.client_id) == ["SYNTH-O1", "SYNTH-O2"]
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    checkpoint = json.loads(state["backfill_cursor"])
    assert checkpoint["orders"]["page_token"] == "SYNTH-PAGE-2"
    assert state["orders_synced_at"] is None and state["catch_up_cursor"] is None
    assert [r["status"] for r in await job_rows(migrated_dsn, binding.record_id)] == ["claimed"]

    # Not stale yet: nothing runs. Once stale, the job resumes at page 2 with the same window.
    del tiktok.fail[("order_search", "SYNTH-PAGE-2")]
    before = len(tiktok.requests)
    clock.advance(60)
    assert await processor.run_once() == 0
    assert len(tiktok.requests) == before
    clock.advance(CONFIG.stale_after_seconds)
    assert await processor.run_once() == 1

    searches = tiktok.calls("order_search")
    assert [token for token, _ in searches] == [
        None,
        "SYNTH-PAGE-2",
        "SYNTH-PAGE-2",
        "SYNTH-PAGE-3",
    ]
    windows = {(body["update_time_ge"], body["update_time_lt"]) for _, body in searches}
    assert windows == {
        (
            int((started - timedelta(days=CONFIG.backfill_days)).timestamp()),
            int(started.timestamp()),
        )
    }
    # Each page is its own verified read: authorized shops before every search.
    assert [k for k, _, _ in tiktok.requests].count("shops") == len(searches)
    assert await order_ids(migrated_dsn, binding.client_id) == [f"SYNTH-O{i}" for i in range(1, 6)]
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    assert json.loads(state["backfill_cursor"])["orders"] is None
    assert state["orders_synced_at"] == clock.now
    # Catch-up later reads from where the backfill's window ended.
    assert state["catch_up_cursor"] == started
    assert state["products_synced_at"] is None
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert (job["status"], job["attempts"]) == ("done", 2)


async def test_order_refresh_upserts_one_order(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.order_details["SYNTH-R1"] = order("SYNTH-R1", status="IN_TRANSIT")
    processor = make_processor(owner_pool, pool, Clock())
    assert await enqueue(pool, binding, "order_refresh", "SYNTH-R1") is True

    assert await processor.run_once() == 1

    assert [k for k, _, _ in tiktok.requests] == ["shops", "order_detail"]
    (row,) = await fetch(
        migrated_dsn,
        "SELECT external_order_id, status, provider_status, store_id, synced_at FROM orders "
        "WHERE client_id=%s",
        binding.client_id,
    )
    assert (row["external_order_id"], row["status"], row["provider_status"]) == (
        "SYNTH-R1",
        "shipped",
        "IN_TRANSIT",
    )
    assert row["store_id"] == binding.record_id and row["synced_at"] is not None
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert job["status"] == "done" and job["last_error_code"] is None
    # A single-order refresh is not a store-wide sync.
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is None or state["orders_synced_at"] is None


async def add_staff_target(migrated_dsn: str, client: UUID) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "INSERT INTO staff_notification_targets(client_id,channel,address) "
            "VALUES (%s,'telegram','SYNTH-STAFF-CHAT')",
            (client,),
        )


async def notes_for(migrated_dsn: str, client: UUID) -> list[dict]:
    return await fetch(
        migrated_dsn, "SELECT event, payload FROM staff_notifications WHERE client_id=%s", client
    )


async def run_attempts(processor: MarketplaceSyncProcessor, clock: Clock) -> None:
    """Enough rounds, past every backoff, for each queued job to use up its attempts."""
    for _ in range(CONFIG.max_attempts):
        await run_until_idle(processor, clock=clock)
        clock.advance(CONFIG.max_backoff_seconds + 1)
    await run_until_idle(processor, clock=clock)


async def test_failed_job_notifies_once_per_store(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    await add_staff_target(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.fail[("order_search", None)] = server_error
    tiktok.fail[("product_search", None)] = server_error
    await enqueue(pool, binding, "backfill_orders")
    await enqueue(pool, binding, "backfill_products")
    clock = Clock()
    processor = make_processor(owner_pool, pool, clock)

    with capture_logs() as logs:
        await run_attempts(processor, clock)

    rows = await job_rows(migrated_dsn, binding.record_id)
    assert [(r["status"], r["attempts"], r["last_error_code"]) for r in rows] == [
        ("failed", CONFIG.max_attempts, "http_error")
    ] * 2
    # One signed request per attempt: never retried inside an attempt.
    assert len(tiktok.calls("order_search")) == CONFIG.max_attempts
    assert len(tiktok.calls("product_search")) == CONFIG.max_attempts
    notes = await notes_for(migrated_dsn, binding.client_id)
    assert len(notes) == 1
    text = json.dumps(notes[0]["payload"])
    for private in (binding.shop_id, PRIVATE_TEXT, "http_error"):
        assert private not in text
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and state["problem_notified_at"] is not None
    assert state["last_error_code"] == "http_error"
    logged = repr(logs)
    for secret in (binding.shop_id, PRIVATE_TEXT, OLD_ACCESS, CIPHER, APP_SECRET):
        assert secret not in logged

    # A single-order refresh succeeding is not a store-wide sync: the episode stays open.
    tiktok.order_details["SYNTH-OK"] = order("SYNTH-OK")
    await enqueue(pool, binding, "order_refresh", "SYNTH-OK")
    await run_until_idle(processor, clock=clock)
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and state["problem_notified_at"] is not None

    # One side synced is not enough while the other still fails: the episode stays open.
    del tiktok.fail[("order_search", None)]
    await enqueue(pool, binding, "backfill_orders")
    await run_until_idle(processor, clock=clock)
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and state["problem_notified_at"] is not None
    # Both sides synced: the episode ends, and the next failure episode notifies again, once.
    del tiktok.fail[("product_search", None)]
    await enqueue(pool, binding, "backfill_products")
    await run_until_idle(processor, clock=clock)
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    assert state["problem_notified_at"] is None and state["last_error_code"] is None
    tiktok.fail[("product_search", None)] = server_error
    await enqueue(pool, binding, "backfill_products")
    await run_attempts(processor, clock)
    assert len(await notes_for(migrated_dsn, binding.client_id)) == 2


async def test_refresh_kind_failures_never_notify(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    await add_staff_target(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.fail[("order_detail", "SYNTH-F1")] = server_error
    tiktok.fail[("product_detail", "SYNTH-F2")] = server_error
    await enqueue(pool, binding, "order_refresh", "SYNTH-F1")
    await enqueue(pool, binding, "product_refresh", "SYNTH-F2")
    clock = Clock()
    processor = make_processor(owner_pool, pool, clock)

    with capture_logs() as logs:
        await run_attempts(processor, clock)

    rows = await job_rows(migrated_dsn, binding.record_id)
    assert [(r["status"], r["last_error_code"]) for r in rows] == [("failed", "http_error")] * 2
    assert await notes_for(migrated_dsn, binding.client_id) == []
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is None or state["problem_notified_at"] is None
    failures = [e for e in logs if e.get("event") == "marketplace_sync_job_failed"]
    assert {e["code"] for e in failures} == {"http_error"}
    assert "SYNTH-F1" not in repr(logs) and "SYNTH-F2" not in repr(logs)


async def test_catch_up_that_only_queues_a_failing_backfill_notifies_once(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    await add_staff_target(migrated_dsn, binding.client_id)
    clock = Clock()
    # Products are synced; the orders backfill never finished, so there is no cursor yet.
    async with tx(pool, str(binding.client_id)) as conn:
        await jobs.set_store_state(
            conn, binding.client_id, binding.record_id, now=clock.now, products_synced_at=clock.now
        )
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.fail[("order_search", None)] = server_error
    processor = make_processor(owner_pool, pool, clock)
    await enqueue(pool, binding, "backfill_orders")
    await run_attempts(processor, clock)
    assert len(await notes_for(migrated_dsn, binding.client_id)) == 1

    # Every 3 hours: catch-up queues the backfill again, which fails again.
    for _ in range(2):
        await enqueue(pool, binding, "catch_up")
        await run_until_idle(processor, clock=clock)
        state = await state_row(migrated_dsn, binding.record_id)
        assert state is not None and state["problem_notified_at"] is not None
        await run_attempts(processor, clock)
    assert len(await notes_for(migrated_dsn, binding.client_id)) == 1
    kinds = [(r["kind"], r["status"]) for r in await job_rows(migrated_dsn, binding.record_id)]
    assert kinds.count(("backfill_orders", "failed")) == 3
    assert kinds.count(("catch_up", "done")) == 2

    # Once the backfill completes, the episode ends.
    del tiktok.fail[("order_search", None)]
    await enqueue(pool, binding, "backfill_orders")
    await run_until_idle(processor, clock=clock)
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and state["problem_notified_at"] is None


async def test_failed_backfill_drops_its_checkpoint(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.orders = {
        None: ([order("SYNTH-K1")], "SYNTH-POISON"),
        "SYNTH-POISON": ([order("SYNTH-K2")], None),
    }
    tiktok.fail[("order_search", "SYNTH-POISON")] = server_error
    clock = Clock()
    processor = make_processor(owner_pool, pool, clock)
    await enqueue(pool, binding, "backfill_orders")
    # While attempts remain, the job resumes at the checkpoint.
    await run_until_idle(processor, clock=clock)
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    assert json.loads(state["backfill_cursor"])["orders"]["page_token"] == "SYNTH-POISON"
    clock.advance(CONFIG.max_backoff_seconds + 1)
    await run_until_idle(processor, clock=clock)
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert job["status"] == "failed"
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and json.loads(state["backfill_cursor"])["orders"] is None

    # The next backfill starts clean instead of resuming the failing page.
    del tiktok.fail[("order_search", "SYNTH-POISON")]
    before = len(tiktok.calls("order_search"))
    await enqueue(pool, binding, "backfill_orders")
    await run_until_idle(processor, clock=clock)
    assert [token for token, _ in tiktok.calls("order_search")[before:]] == [None, "SYNTH-POISON"]
    assert await order_ids(migrated_dsn, binding.client_id) == ["SYNTH-K1", "SYNTH-K2"]


async def test_backfill_at_the_page_cap_hands_back_without_spending_an_attempt(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.orders = {
        None: ([order("SYNTH-G1")], "SYNTH-G-2"),
        "SYNTH-G-2": ([order("SYNTH-G2")], "SYNTH-G-3"),
        "SYNTH-G-3": ([order("SYNTH-G3")], None),
    }
    config = dataclasses.replace(CONFIG, max_pages_per_attempt=2)
    clock = Clock()
    processor = make_processor(owner_pool, pool, clock, config)
    await enqueue(pool, binding, "backfill_orders")

    assert await processor.run_once() == 1
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert (job["status"], job["attempts"], job["last_error_code"]) == ("pending", 0, None)
    assert await order_ids(migrated_dsn, binding.client_id) == ["SYNTH-G1", "SYNTH-G2"]
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    assert json.loads(state["backfill_cursor"])["orders"]["page_token"] == "SYNTH-G-3"

    # Due at once: the next round resumes at the checkpoint and finishes.
    assert await processor.run_once() == 1
    assert [t for t, _ in tiktok.calls("order_search")] == [None, "SYNTH-G-2", "SYNTH-G-3"]
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert (job["status"], job["attempts"]) == ("done", 1)
    assert await order_ids(migrated_dsn, binding.client_id) == [
        "SYNTH-G1",
        "SYNTH-G2",
        "SYNTH-G3",
    ]


@pytest.mark.parametrize("kind", ["backfill_orders", "catch_up"])
async def test_page_token_cycle_fails_the_job(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    clock = Clock()
    if kind == "catch_up":
        async with tx(pool, str(binding.client_id)) as conn:
            await jobs.set_store_state(
                conn,
                binding.client_id,
                binding.record_id,
                now=clock.now,
                catch_up_cursor=clock.now - timedelta(hours=3),
                orders_synced_at=clock.now,
                products_synced_at=clock.now,
            )
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    # A, B, A: each token differs from the one sent, so only the seen-set catches it.
    tiktok.orders = {
        None: ([order("SYNTH-Y1")], "SYNTH-A"),
        "SYNTH-A": ([order("SYNTH-Y2")], "SYNTH-B"),
        "SYNTH-B": ([order("SYNTH-Y3")], "SYNTH-A"),
    }
    processor = make_processor(owner_pool, pool, clock)
    await enqueue(pool, binding, kind)

    assert await processor.run_once() == 1

    assert [t for t, _ in tiktok.calls("order_search")] == [None, "SYNTH-A", "SYNTH-B"]
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert (job["status"], job["attempts"], job["last_error_code"]) == (
        "pending",
        1,
        "page_token_cycle",
    )
    # The page that closed the cycle was not stored.
    assert await order_ids(migrated_dsn, binding.client_id) == ["SYNTH-Y1", "SYNTH-Y2"]


async def test_catch_up_at_the_page_cap_fails_and_keeps_its_cursor(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    clock = Clock()
    cursor = clock.now - timedelta(hours=3)
    async with tx(pool, str(binding.client_id)) as conn:
        await jobs.set_store_state(
            conn,
            binding.client_id,
            binding.record_id,
            now=clock.now,
            catch_up_cursor=cursor,
            orders_synced_at=clock.now,
            products_synced_at=clock.now,
        )
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.orders = {
        None: ([order("SYNTH-Z1")], "SYNTH-Z-2"),
        "SYNTH-Z-2": ([order("SYNTH-Z2")], "SYNTH-Z-3"),
        "SYNTH-Z-3": ([order("SYNTH-Z3")], None),
    }
    config = dataclasses.replace(CONFIG, max_pages_per_attempt=2)
    processor = make_processor(owner_pool, pool, clock, config)
    await enqueue(pool, binding, "catch_up")

    assert await processor.run_once() == 1

    assert len(tiktok.calls("order_search")) == 2
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert (job["status"], job["attempts"], job["last_error_code"]) == (
        "pending",
        1,
        "page_cap_reached",
    )
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and state["catch_up_cursor"] == cursor


async def test_expired_store_cancels_pending_jobs(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    await enqueue(pool, binding, "backfill_orders")
    await enqueue(pool, binding, "order_refresh", "SYNTH-E1")
    await enqueue(pool, binding, "product_refresh", "SYNTH-E2")
    # What a refused refresh does (mark_store_expired.sql): tokens cleared, expiry now.
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET access_token=NULL, refresh_token=NULL, "
            "expires_at=now() WHERE id=%s",
            (binding.record_id,),
        )
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)

    assert await run_until_idle(make_processor(owner_pool, pool, Clock())) == 1

    assert tiktok.requests == []
    rows = await job_rows(migrated_dsn, binding.record_id)
    assert {(r["status"], r["last_error_code"]) for r in rows} == {("cancelled", "store_expired")}
    assert len(rows) == 3


@pytest.mark.parametrize("case", ["pending", "disconnected"])
async def test_processor_makes_no_request_for_pending_or_disconnected_store(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    store = binding.record_id
    if case == "pending":
        # A first connect that has not come back from TikTok yet: no shop.
        store = uuid4()
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute(
                "INSERT INTO channel_credentials(id,client_id,channel) "
                "VALUES (%s,%s,'tiktok_shop')",
                (store, binding.client_id),
            )
    target = binding.model_copy(update={"record_id": store})
    await enqueue(pool, target, "backfill_orders")
    await enqueue(pool, target, "catch_up")
    if case == "disconnected":
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute(
                "UPDATE channel_credentials SET disconnected_at=now(), access_token=NULL, "
                "refresh_token=NULL WHERE id=%s",
                (store,),
            )
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)

    await run_until_idle(make_processor(owner_pool, pool, Clock()))

    assert tiktok.requests == []
    rows = await job_rows(migrated_dsn, store)
    assert {(r["status"], r["last_error_code"]) for r in rows} == {("cancelled", f"store_{case}")}


@pytest.mark.parametrize(
    ("case", "change"),
    [
        ("disconnected", "disconnected_at=now(), access_token=NULL, refresh_token=NULL"),
        # What a refused refresh does between two pages (mark_store_expired.sql).
        ("expired", "access_token=NULL, refresh_token=NULL, expires_at=now()"),
    ],
)
async def test_store_lost_during_a_backfill_stops_it_without_writing(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    change: str,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.orders = {
        None: ([order("SYNTH-D1")], "SYNTH-D-PAGE-2"),
        "SYNTH-D-PAGE-2": ([order("SYNTH-D2")], None),
    }
    await enqueue(pool, binding, "backfill_orders")
    await enqueue(pool, binding, "order_refresh", "SYNTH-D9")
    real_read = processor_module.read_verified
    reads = 0

    async def read_then_disconnect(*args: Any, **kwargs: Any):
        nonlocal reads
        result = await real_read(*args, **kwargs)
        reads += 1
        if reads == 2:  # page 2 was read, then the store was lost before it was stored
            async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
                await conn.execute(
                    f"UPDATE channel_credentials SET {change} WHERE id=%s",  # type: ignore[arg-type]
                    (binding.record_id,),
                )
        return result

    monkeypatch.setattr(processor_module, "read_verified", read_then_disconnect)

    await run_until_idle(make_processor(owner_pool, pool, Clock()))

    assert await order_ids(migrated_dsn, binding.client_id) == ["SYNTH-D1"]
    rows = await job_rows(migrated_dsn, binding.record_id)
    # The gate reports what the store became, not always "disconnected".
    assert [(r["kind"], r["status"], r["last_error_code"]) for r in rows] == [
        ("backfill_orders", "cancelled", f"store_{case}"),
        ("order_refresh", "cancelled", f"store_{case}"),
    ]
    assert tiktok.calls("order_detail") == []


async def test_listing_sync_leaves_local_stock_untouched(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    product_id = uuid4()
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "INSERT INTO products(id,client_id,sku,name,price,stock_qty,reserved_qty) "
            "VALUES (%s,%s,'MUG-BLK-M','Local mug',9.90,11,2)",
            (product_id, binding.client_id),
        )
    local_before = await fetch(migrated_dsn, "SELECT * FROM products WHERE id=%s", product_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.products = {
        None: ([catalogue_product("SYNTH-P1"), catalogue_product("SYNTH-P2")], "SYNTH-P-PAGE-2"),
        "SYNTH-P-PAGE-2": ([catalogue_product("SYNTH-P3")], None),
    }
    await enqueue(pool, binding, "backfill_products")
    clock = Clock()

    assert await run_until_idle(make_processor(owner_pool, pool, clock)) == 1

    listings = await fetch(
        migrated_dsn,
        "SELECT external_product_id, external_sku_id, seller_sku, stock_qty FROM "
        "marketplace_listings WHERE client_id=%s ORDER BY external_product_id, external_sku_id",
        binding.client_id,
    )
    assert len(listings) == 3 * 2
    assert {r["external_product_id"] for r in listings} == {"SYNTH-P1", "SYNTH-P2", "SYNTH-P3"}
    assert await fetch(migrated_dsn, "SELECT * FROM products WHERE id=%s", product_id) == (
        local_before
    )
    # Nothing local was written: no order lines, no stock movement (reserved_qty is on products).
    assert (
        await fetch(
            migrated_dsn, "SELECT id FROM order_items WHERE client_id=%s", binding.client_id
        )
        == []
    )
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    assert state["products_synced_at"] == clock.now
    assert json.loads(state["backfill_cursor"])["products"] is None
    assert [b == {} for _, b in tiktok.calls("product_search")] == [True, True]


async def test_product_refresh_marks_skus_the_provider_no_longer_lists(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    first = product_payload("SYNTH-PR")
    tiktok.product_details["SYNTH-PR"] = first
    processor = make_processor(owner_pool, pool, Clock())
    await enqueue(pool, binding, "product_refresh", "SYNTH-PR")
    await run_until_idle(processor)

    # The seller removed one variant and restocked the other.
    second = product_payload("SYNTH-PR")
    second["update_time"] = first["update_time"] + 60
    second["skus"] = [sku_payload("SYNTH-SKU-1", quantities=(9,))]
    tiktok.product_details["SYNTH-PR"] = second
    await enqueue(pool, binding, "product_refresh", "SYNTH-PR")
    await run_until_idle(processor)

    rows = await fetch(
        migrated_dsn,
        "SELECT external_sku_id, product_status, stock_qty FROM marketplace_listings "
        "WHERE client_id=%s ORDER BY external_sku_id",
        binding.client_id,
    )
    assert [(r["external_sku_id"], r["product_status"], r["stock_qty"]) for r in rows] == [
        ("SYNTH-SKU-1", "ACTIVATE", 9),
        ("SYNTH-SKU-2", REMOVED_PRODUCT_STATUS, 0),
    ]
    assert REMOVED_PRODUCT_STATUS == "DELETED"
    assert [r["status"] for r in await job_rows(migrated_dsn, binding.record_id)] == [
        "done",
        "done",
    ]


async def test_unparsable_page_fails_the_job_without_partial_writes(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    broken = order("SYNTH-BAD")
    del broken["payment"]
    tiktok.orders = {None: ([order("SYNTH-GOOD"), broken], None)}
    await enqueue(pool, binding, "backfill_orders")
    clock = Clock()
    processor = make_processor(owner_pool, pool, clock)

    assert await processor.run_once() == 1

    assert await order_ids(migrated_dsn, binding.client_id) == []
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert (job["status"], job["attempts"], job["last_error_code"]) == (
        "pending",
        1,
        "invalid_response",
    )
    # The backoff holds the job back; the loop itself carries on.
    assert await processor.run_once() == 0


async def test_catch_up_without_a_cursor_queues_the_backfills(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    await enqueue(pool, binding, "catch_up")

    assert await make_processor(owner_pool, pool, Clock()).run_once() == 1

    assert tiktok.requests == []
    rows = await job_rows(migrated_dsn, binding.record_id)
    assert sorted((r["kind"], r["status"]) for r in rows) == [
        ("backfill_orders", "pending"),
        ("backfill_products", "pending"),
        ("catch_up", "done"),
    ]


async def test_catch_up_queues_only_the_products_backfill_when_products_were_forgotten(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the 30-day purge a reconnected store has orders progress but no products progress."""
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    clock = Clock()
    synced = clock.now - timedelta(hours=4)
    async with tx(pool, str(binding.client_id)) as conn:
        await jobs.set_store_state(
            conn,
            binding.client_id,
            binding.record_id,
            now=clock.now,
            catch_up_cursor=clock.now - timedelta(hours=3),
            orders_synced_at=synced,
            products_synced_at=None,
        )
    FakeTikTok(binding.shop_id).install(monkeypatch)
    await enqueue(pool, binding, "catch_up")

    assert await make_processor(owner_pool, pool, clock).run_once() == 1

    rows = await job_rows(migrated_dsn, binding.record_id)
    assert sorted((r["kind"], r["status"]) for r in rows) == [
        ("backfill_products", "pending"),
        ("catch_up", "done"),
    ]


async def test_catch_up_reads_since_the_cursor_and_advances_it_only_on_success(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    clock = Clock()
    cursor = clock.now - timedelta(hours=3)
    synced = clock.now - timedelta(hours=4)
    async with tx(pool, str(binding.client_id)) as conn:
        await jobs.set_store_state(
            conn,
            binding.client_id,
            binding.record_id,
            now=clock.now,
            catch_up_cursor=cursor,
            orders_synced_at=synced,
            products_synced_at=synced,
        )
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.orders = {None: ([order("SYNTH-C1")], None)}
    tiktok.products = {None: ([product_payload("SYNTH-CP")], None)}
    tiktok.fail[("product_search", None)] = server_error
    await enqueue(pool, binding, "catch_up")
    processor = make_processor(owner_pool, pool, clock)

    assert await processor.run_once() == 1
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and state["catch_up_cursor"] == cursor
    assert state["orders_synced_at"] == synced

    del tiktok.fail[("product_search", None)]
    clock.advance(CONFIG.max_backoff_seconds + 1)
    assert await processor.run_once() == 1
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    assert state["catch_up_cursor"] == clock.now
    assert state["orders_synced_at"] == clock.now == state["products_synced_at"]
    assert await order_ids(migrated_dsn, binding.client_id) == ["SYNTH-C1"]
    # Both attempts read the same start; the window ends when each attempt ran.
    first_try, second_try = (body for _, body in tiktok.calls("order_search"))
    overlap = processor_module.CATCH_UP_OVERLAP
    assert first_try["update_time_ge"] == second_try["update_time_ge"]
    assert second_try["update_time_ge"] == int((cursor - overlap).timestamp())
    assert second_try["update_time_lt"] == int(clock.now.timestamp())
    assert first_try["update_time_lt"] < second_try["update_time_lt"]
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert job["status"] == "done"


async def test_run_once_handles_one_job_per_tenant(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = await store_for(migrated_dsn, pool)
    second = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, first.client_id, second.client_id)
    for binding in (first, second):
        for target in ("SYNTH-T1", "SYNTH-T2"):
            await enqueue(pool, binding, "order_refresh", target)
    details: list[str] = []

    def transport() -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/authorization/202309/shops":
                shops = [{"id": b.shop_id, "cipher": CIPHER} for b in (first, second)]
                return respond({"code": 0, "data": {"shops": shops}})
            details.append(request.url.params["ids"])
            return respond({"code": 0, "data": {"orders": [order(request.url.params["ids"])]}})

        return httpx.MockTransport(handler)

    monkeypatch.setattr(processor_module, "_new_read_transport", transport)
    processor = make_processor(owner_pool, pool, Clock())

    assert await processor.run_once() == 2
    for binding in (first, second):
        statuses = sorted(r["status"] for r in await job_rows(migrated_dsn, binding.record_id))
        assert statuses == ["done", "pending"]
    assert await processor.run_once() == 2
    assert await processor.run_once() == 0
    assert sorted(details) == ["SYNTH-T1", "SYNTH-T1", "SYNTH-T2", "SYNTH-T2"]


async def test_refresh_due_catches_failures_per_store_and_caps_concurrency(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Four tenants with one store each, all due (the access token lapses within the hour).
    stores = [
        (await connected(migrated_dsn, pool, access_in=timedelta(hours=1))).context.binding
        for _ in range(4)
    ]
    await only_active(migrated_dsn, *(s.client_id for s in stores))
    failing = stores[1].shop_id
    in_flight = peak = 0
    refresh_requests = 0

    def refresh_transport() -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak, refresh_requests
            refresh_requests += 1
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.05)
            finally:
                in_flight -= 1
            return respond(token_payload())

        return httpx.MockTransport(handler)

    def shop_transport() -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-tts-access-token"] == NEW_ACCESS
            shops = [{"id": s.shop_id, "cipher": CIPHER} for s in stores]
            return respond({"code": 0, "data": {"shops": shops}})

        return httpx.MockTransport(handler)

    monkeypatch.setattr(token_refresh, "_new_transport", refresh_transport)
    monkeypatch.setattr(refresh_module, "_new_shop_transport", shop_transport)
    # One store's shop check is lost (not a mismatch): refresh_store raises a safe error.
    real_shop_check = refresh_module._shop_still_matches

    async def shop_check(app, access_token, shop_id, shop_cipher):  # type: ignore[no-untyped-def]
        if shop_id == failing:
            raise refresh_module.TikTokAuthorizationError("authorized_shop_verification_failed")
        return await real_shop_check(app, access_token, shop_id, shop_cipher)

    monkeypatch.setattr(refresh_module, "_shop_still_matches", shop_check)
    # The refresh records a grant verified "now", so it runs on the real clock, as in production.
    processor = MarketplaceSyncProcessor(
        owner_pool,
        pool,
        load_app=any_app_loader(pool),
        encrypt=encrypt,
        decrypt=decrypt,
        config=CONFIG,
    )

    with capture_logs() as logs:
        refreshed = await processor.refresh_due()

    assert refreshed == 3
    assert refresh_requests == 4
    assert peak == SyncConfig().refresh_concurrency == 2
    failures = [e for e in logs if e.get("event") == "marketplace_refresh_failed"]
    assert [e["code"] for e in failures] == ["authorized_shop_verification_failed"]
    logged = repr(logs)
    for secret in (OLD_REFRESH, NEW_ACCESS, CIPHER, APP_SECRET, PRIVATE_TEXT, failing):
        assert secret not in logged
    rows = await fetch(
        migrated_dsn,
        "SELECT shop_id, credential_generation FROM channel_credentials WHERE id = ANY(%s)",
        [s.record_id for s in stores],
    )
    generations = {r["shop_id"]: r["credential_generation"] for r in rows}
    # The lost one keeps its credential untouched and is retried on the next round.
    assert generations.pop(failing) == 2
    assert sorted(generations.values()) == [3, 3, 3]


async def test_sync_loop_survives_errors_and_stops_on_cancel() -> None:
    calls: list[str] = []

    class Flaky:
        async def refresh_due(self) -> int:
            calls.append("refresh")
            raise RuntimeError("synthetic")

        async def run_once(self) -> int:
            calls.append("run")
            if calls.count("run") >= 3:
                raise asyncio.CancelledError
            raise RuntimeError("synthetic")

    with pytest.raises(asyncio.CancelledError):
        await marketplace_sync_loop(Flaky(), poll_seconds=0, refresh_seconds=3600)  # type: ignore[arg-type]
    assert calls == ["refresh", "run", "run", "run"]


async def test_job_abandoned_by_crashes_fails_and_notifies_once(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "INSERT INTO staff_notification_targets(client_id,channel,address) "
            "VALUES (%s,'telegram','SYNTH-STAFF-CHAT')",
            (binding.client_id,),
        )
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)

    def crash() -> httpx.Response:
        raise asyncio.CancelledError

    # Page 1 is stored and checkpointed; the worker dies on page 2 on every attempt.
    tiktok.orders = {None: ([order("SYNTH-CR1")], "SYNTH-CR-2")}
    tiktok.fail[("order_search", "SYNTH-CR-2")] = crash
    await enqueue(pool, binding, "backfill_orders")
    clock = Clock()
    processor = make_processor(owner_pool, pool, clock)
    for _ in range(CONFIG.max_attempts):
        with pytest.raises(asyncio.CancelledError):
            await processor.run_once()
        clock.advance(CONFIG.stale_after_seconds + 1)

    # The next round finds the claim stale with no attempts left: failed, one notification.
    assert await processor.run_once() == 0
    (job,) = await job_rows(migrated_dsn, binding.record_id)
    assert (job["status"], job["last_error_code"]) == ("failed", "claim_abandoned")
    notes = await fetch(
        migrated_dsn,
        "SELECT payload FROM staff_notifications WHERE client_id=%s",
        binding.client_id,
    )
    assert [n["payload"] for n in notes] == [{"text": processor_module.PROBLEM_TEXT}]
    # Attempt 1 read page 1 and 2; each later attempt resumed at page 2.
    assert [t for t, _ in tiktok.calls("order_search")] == [None] + ["SYNTH-CR-2"] * (
        CONFIG.max_attempts
    )
    # The abandoned backfill dropped its checkpoint: the next one starts at page 1.
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and json.loads(state["backfill_cursor"])["orders"] is None
    assert state["last_error_code"] == "claim_abandoned"


async def test_one_side_failing_with_a_cursor_set_notifies_once_across_catch_ups(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    await add_staff_target(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    poisoned = True

    async def handler(request: httpx.Request) -> httpx.Response:
        # Only the 90-day orders backfill fails; catch-up searches (a few hours) succeed.
        if poisoned and request.url.path == "/order/202309/orders/search":
            body = json.loads(request.content)
            if body["update_time_lt"] - body["update_time_ge"] > 86_400:
                tiktok.requests.append(("order_backfill_failed", None, body))
                return server_error()
        return await tiktok.handler(request)

    monkeypatch.setattr(
        processor_module, "_new_read_transport", lambda: httpx.MockTransport(handler)
    )
    clock = Clock()
    processor = make_processor(owner_pool, pool, clock)

    # The products backfill completes through the real writer: the store now has a cursor.
    await enqueue(pool, binding, "backfill_products")
    await run_until_idle(processor, clock=clock)
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None
    assert state["catch_up_cursor"] is not None and state["products_synced_at"] is not None
    assert state["orders_synced_at"] is None

    await enqueue(pool, binding, "backfill_orders")
    await run_attempts(processor, clock)
    assert len(await notes_for(migrated_dsn, binding.client_id)) == 1

    # Two catch-up cycles: each syncs (it has a cursor), re-queues the orders backfill, which
    # fails again. The episode stays open throughout and staff are not told again.
    for _ in range(2):
        await enqueue(pool, binding, "catch_up")
        await run_until_idle(processor, clock=clock)
        state = await state_row(migrated_dsn, binding.record_id)
        assert state is not None and state["problem_notified_at"] is not None
        await run_attempts(processor, clock)
    assert len(await notes_for(migrated_dsn, binding.client_id)) == 1
    kinds = [(r["kind"], r["status"]) for r in await job_rows(migrated_dsn, binding.record_id)]
    assert kinds.count(("catch_up", "done")) == 2
    assert kinds.count(("backfill_orders", "failed")) == 3

    # The orders backfill succeeds: both sides synced, the episode ends.
    poisoned = False
    await enqueue(pool, binding, "backfill_orders")
    await run_until_idle(processor, clock=clock)
    state = await state_row(migrated_dsn, binding.record_id)
    assert state is not None and state["orders_synced_at"] is not None
    assert state["problem_notified_at"] is None and state["last_error_code"] is None

    # A later failure is a new episode and notifies again.
    poisoned = True
    await enqueue(pool, binding, "backfill_orders")
    await run_attempts(processor, clock)
    assert len(await notes_for(migrated_dsn, binding.client_id)) == 2
