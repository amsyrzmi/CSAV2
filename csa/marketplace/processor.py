"""The marketplace sync engine (spec 6.4): claims queued jobs and copies TikTok data in.

``MarketplaceSyncProcessor.run_once`` handles at most one job per active tenant: it requeues
the tenant's stale claims, claims one due job with ``FOR UPDATE SKIP LOCKED`` (never a second
job for a store that already has one claimed) and runs it. ``refresh_due`` refreshes the tokens
that are about to lapse (Task 6), a few stores at a time.

Every TikTok call is its own single-use, fenced read: ``begin_read`` then ``read_verified``
through ``TikTokStoredCredentialResolver`` (a fresh authorized-shops check, then the read). A
signed read is never retried inside an attempt; a failed attempt goes back to the queue with
backoff, and after ``max_attempts`` the job fails with a safe code and the store's staff get
one notification for that failure episode.

Before each page is stored the store is checked again under a share lock: a store that was
disconnected, expired or never connected cancels its job and its pending jobs, and nothing is
written after that. Backfills checkpoint after every page, so a crash resumes where it stopped.

Logs carry ids, kinds, counts and safe codes only: never shop ids, tokens, buyer data or
provider text.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import httpx
import structlog
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from csa.agents.ecommerce.adapters.tiktok_shop import TikTokReadError
from csa.agents.ecommerce.app_loader import AppLoader
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.reader import TikTokReadResult, read_verified
from csa.agents.ecommerce.refresh import REFRESH_DUE_WITHIN, list_refresh_due, refresh_store
from csa.agents.ecommerce.storage import TikTokStoredCredentialResolver
from csa.agents.ecommerce.tiktok_models import (
    TikTokOrderSnapshot,
    TikTokOrderSnapshotPage,
    TikTokProductSnapshot,
    TikTokProductSnapshotPage,
)
from csa.core import db
from csa.core.tenancy import tx
from csa.marketplace import jobs
from csa.marketplace.jobs import SyncJob, SyncStore
from csa.marketplace.mapping import MappingError, map_listings, map_order
from csa.marketplace.store import upsert_listings, upsert_order
from csa.notify.notifier import notify

logger = structlog.get_logger(__name__)

# A SKU TikTok no longer lists for its product keeps its row with this status (TikTok's own word
# for a deleted product), so staff still see what it was. Rows are only deleted by the purge.
REMOVED_PRODUCT_STATUS = "DELETED"
# Catch-up reads from a little before its cursor: provider and server clocks differ slightly,
# and upserts are idempotent, so reading a few minutes twice costs nothing.
CATCH_UP_OVERLAP = timedelta(minutes=10)
# Each read (authorized shops, then the page) is bounded again by DISPATCH_TIMEOUT_SECONDS.
READ_TIMEOUT_SECONDS = 15.0
SYNC_POLL_SECONDS = 5.0
REFRESH_EVERY_SECONDS = 900.0
# The one staff notification per failure episode: plain words, no ids, codes or provider text.
PROBLEM_EVENT = "dlq"
PROBLEM_TEXT = (
    "A TikTok Shop store has stopped syncing after several tries. "
    "Open Store connections to check it, and reconnect the store if it asks you to."
)

_GONE: dict[str, str] = {
    "pending": "store_pending",
    "disconnected": "store_disconnected",
    "expired": "store_expired",
}
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Store-wide jobs: their failure opens a problem episode, their success closes it. A failed
# single-order or single-product refresh is logged only.
_STORE_WIDE: frozenset[str] = frozenset({"backfill_orders", "backfill_products", "catch_up"})
_BACKFILL_SIDE: dict[str, Literal["orders", "products"]] = {
    "backfill_orders": "orders",
    "backfill_products": "products",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_read_transport() -> httpx.AsyncBaseTransport:
    """The transport for signed reads: no retries, no proxies from the environment."""
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=True)


@dataclass(frozen=True)
class SyncConfig:
    max_attempts: int = 5
    stale_after_seconds: float = 900.0
    page_size: int = 50
    page_delay_seconds: float = 1.0
    backfill_days: int = 90
    retry_backoff_seconds: float = 60.0
    max_backoff_seconds: float = 3600.0
    # refresh_store holds a credential row lock and a pool connection for up to 15 s per store.
    refresh_concurrency: int = 2
    # Pages one attempt may read. A backfill at the cap checkpoints and hands its claim back
    # (no attempt spent) so other tenants and the token refresh get their turn; a catch-up,
    # which has no checkpoint, fails with page_cap_reached.
    max_pages_per_attempt: int = 200

    def __post_init__(self) -> None:
        if not 1 <= self.page_size <= 50:
            raise ValueError("page_size must be between 1 and 50")
        if (
            min(
                self.max_attempts,
                self.refresh_concurrency,
                self.backfill_days,
                self.max_pages_per_attempt,
            )
            < 1
        ):
            raise ValueError("counts in the sync config must be at least 1")
        if min(self.stale_after_seconds, self.page_delay_seconds, self.retry_backoff_seconds) < 0:
            raise ValueError("durations must not be negative")

    def backoff(self, attempts: int) -> timedelta:
        seconds = self.retry_backoff_seconds * 2 ** max(attempts - 1, 0)
        return timedelta(seconds=min(seconds, self.max_backoff_seconds))


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class _OrdersCheckpoint(_Contract):
    page_token: str = Field(min_length=1, max_length=2048)
    updated_from: AwareDatetime
    updated_to: AwareDatetime


class _ProductsCheckpoint(_Contract):
    page_token: str = Field(min_length=1, max_length=2048)
    started_at: AwareDatetime


class _Checkpoint(_Contract):
    """``marketplace_store_state.backfill_cursor``: one resumable checkpoint per backfill.

    Stored as JSON text, ``{"orders": {...} | null, "products": {...} | null}``. Orders keep
    their update-time window, because a page token only continues the search it came from.
    """

    orders: _OrdersCheckpoint | None = None
    products: _ProductsCheckpoint | None = None


def _checkpoint(store: SyncStore) -> _Checkpoint:
    if store.backfill_cursor is None:
        return _Checkpoint()
    try:
        return _Checkpoint.model_validate_json(store.backfill_cursor)
    except ValidationError:
        # Restarting a backfill is safe (upserts are idempotent); a broken checkpoint is not.
        logger.warning("marketplace_backfill_checkpoint_reset", store_id=str(store.store_id))
        return _Checkpoint()


def _earlier(current: datetime | None, candidate: datetime) -> datetime:
    return candidate if current is None or candidate < current else current


class _StoreGone(Exception):
    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


class _LeaseLost(Exception):
    pass


class _HandedBack(Exception):
    """A backfill reached its page cap: checkpointed and back in the queue, no attempt spent."""


class _PageGuard:
    """Per-attempt paging limits: at most ``cap`` pages, and no page token seen twice.

    The adapter only refuses a token equal to the one it was sent, so a provider cycle such as
    A, B, A, or tokens that never end, would otherwise page forever while the heartbeat keeps the
    claim fresh.
    """

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.pages = 0
        self._seen: set[str] = set()

    def record(self, requested: str | None, next_token: str | None) -> None:
        self.pages += 1
        if requested is not None:
            self._seen.add(requested)
        if next_token is not None and next_token in self._seen:
            raise _SyncFailure("page_token_cycle")

    @property
    def full(self) -> bool:
        return self.pages >= self.cap


class _SyncFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _safe_code(exc: Exception) -> str:
    if isinstance(exc, (TikTokReadError, TikTokAuthorizationError, MappingError, _SyncFailure)):
        code = exc.code
        return code if isinstance(code, str) and _SAFE_CODE.match(code) else "sync_failed"
    if isinstance(exc, TimeoutError):
        return "read_timeout"
    return "sync_failed"


Writer = Callable[[AsyncConnection[Any], SyncStore], Awaitable[None]]


async def fenced_read(
    app_pool: AsyncConnectionPool,
    client_id: UUID,
    grant_id: UUID,
    request: TikTokReadRequest,
    *,
    load_app: AppLoader,
    decrypt: Callable[[str, bytes], str],
    wrap_transport: Callable[[httpx.AsyncBaseTransport], httpx.AsyncBaseTransport] | None = None,
) -> TikTokReadResult:
    """One single-use, fenced read on a grant: ``begin_read`` then ``read_verified``.

    A fresh authorized-shops check, then the read, on a transport with no retries. The caller
    checks the store first. The sync and the customer's live stock read (``reads.live_stock``)
    both read through here. ``wrap_transport`` is for ``scripts.tiktok_probe`` only: it wraps
    (never replaces) ``_new_read_transport()`` to see a response's shape; everyone else leaves
    it unset.
    """
    operation = await begin_read(app_pool, client_id, grant_id, request)
    transport = _new_read_transport()
    if wrap_transport is not None:
        transport = wrap_transport(transport)
    async with httpx.AsyncClient(
        transport=transport, timeout=READ_TIMEOUT_SECONDS, trust_env=False
    ) as http:
        resolver = TikTokStoredCredentialResolver(
            load_app=load_app, decrypt=decrypt, http_client=http
        )
        return await read_verified(app_pool, client_id, operation.id, resolver)


async def store_products(
    conn: AsyncConnection[Any],
    client_id: UUID,
    store_id: UUID,
    products: tuple[TikTokProductSnapshot, ...],
    now: datetime,
) -> None:
    """Upsert each product's listings (Task 11) and mark its SKUs TikTok no longer lists."""
    for product in products:
        rows = map_listings(client_id, store_id, product, now)
        await upsert_listings(conn, rows)
        # TikTok lists a product with all of its SKUs: any other stored SKU is gone.
        await db.q.fetch_all(
            "marketplace/mark_listings_removed.sql",
            {
                "client_id": client_id,
                "store_id": store_id,
                "product_id": product.id,
                "sku_ids": [sku.id for sku in product.skus],
                "updated_at": product.update_time,
                "removed": REMOVED_PRODUCT_STATUS,
                "now": now,
            },
            conn=conn,
        )


class MarketplaceSyncProcessor:
    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        *,
        load_app: AppLoader,
        encrypt: Callable[[str, bytes], str],
        decrypt: Callable[[str, bytes], str],
        config: SyncConfig,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._load_app = load_app
        self._encrypt = encrypt
        self._decrypt = decrypt
        self._config = config
        self._clock = clock
        self._sleep = sleep

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("the sync clock must be timezone-aware")
        return now

    async def _active_clients(self) -> list[UUID]:
        async with self._owner_pool.connection() as conn:
            rows = await db.q.fetch_all("jobs/list_active_clients.sql", conn=conn)
        return [row["id"] for row in rows]

    # -- queue ------------------------------------------------------------------------------

    async def run_once(self) -> int:
        """Run at most one job per active tenant; returns how many jobs were handled."""
        handled = 0
        for client_id in await self._active_clients():
            try:
                if await self.run_tenant(client_id):
                    handled += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "marketplace_sync_tenant_failed",
                    client_id=str(client_id),
                    error_type=type(exc).__name__,
                )
        return handled

    async def run_tenant(self, client_id: UUID) -> bool:
        """Requeue the tenant's stale claims, then claim and run one job; False when idle."""
        cfg = self._config
        now = self._now()
        async with tx(self._app_pool, str(client_id)) as conn:
            requeued = await jobs.requeue_stale_jobs(
                conn,
                client_id,
                now=now,
                stale_after_seconds=cfg.stale_after_seconds,
                max_attempts=cfg.max_attempts,
            )
            for row in requeued:
                if row.status == "failed" and row.kind in _STORE_WIDE:
                    await self._store_wide_failure(
                        conn, client_id, row.store_id, row.kind, "failed", now, "claim_abandoned"
                    )
            job = await jobs.claim_job(conn, client_id, now=now)
        if requeued:
            logger.info(
                "marketplace_sync_stale_requeued", client_id=str(client_id), jobs=len(requeued)
            )
        if job is None:
            return False
        await self._process(job)
        return True

    async def _process(self, job: SyncJob) -> None:
        log = logger.bind(
            client_id=str(job.client_id),
            store_id=str(job.store_id),
            job_id=str(job.id),
            kind=job.kind,
            attempt=job.attempts,
        )
        try:
            store = await self._store(job)
            if store is None or store.sync_state in _GONE:
                raise _StoreGone(store.sync_state if store is not None else "pending")
            if store.sync_state != "connected" or store.shop_id is None:
                raise _SyncFailure("store_unavailable")
            handlers = {
                "backfill_orders": self._backfill_orders,
                "backfill_products": self._backfill_products,
                "order_refresh": self._order_refresh,
                "product_refresh": self._product_refresh,
                "catch_up": self._catch_up,
            }
            await handlers[job.kind](job, store.shop_id)
        except asyncio.CancelledError:
            raise  # the claim stays; it is requeued once stale and resumes from the checkpoint
        except _LeaseLost:
            log.warning("marketplace_sync_claim_lost")
        except _HandedBack:
            log.info("marketplace_sync_job_handed_back")
        except _StoreGone as gone:
            await self._cancel(job, gone.state)
            log.info("marketplace_sync_job_cancelled", code=_GONE.get(gone.state, "store_pending"))
        except Exception as exc:
            code = _safe_code(exc)
            # A store that went away meanwhile cancels instead of retrying.
            current = await self._store(job)
            if current is None or current.sync_state in _GONE:
                state = current.sync_state if current is not None else "pending"
                await self._cancel(job, state)
                log.info("marketplace_sync_job_cancelled", code=_GONE[state])
                return
            status = await self._fail(job, code)
            if code == "sync_failed":
                log.error(
                    "marketplace_sync_job_error", status=status, error_type=type(exc).__name__
                )
            else:
                log.warning("marketplace_sync_job_failed", status=status, code=code)
        else:
            log.info("marketplace_sync_job_done")

    async def _store(self, job: SyncJob) -> SyncStore | None:
        async with tx(self._app_pool, str(job.client_id)) as conn:
            return await jobs.get_sync_store(conn, job.client_id, job.store_id)

    async def _cancel(self, job: SyncJob, state: str) -> None:
        code = _GONE.get(state, "store_pending")
        async with tx(self._app_pool, str(job.client_id)) as conn:
            await jobs.finish_job(conn, job, "cancelled", error_code=code)
            await jobs.cancel_store_jobs(conn, job.client_id, job.store_id, code=code)

    async def _fail(self, job: SyncJob, code: str) -> str | None:
        cfg = self._config
        now = self._now()
        async with tx(self._app_pool, str(job.client_id)) as conn:
            if job.attempts >= cfg.max_attempts:
                status = await jobs.finish_job(conn, job, "failed", error_code=code)
            else:
                status = await jobs.finish_job(
                    conn,
                    job,
                    "pending",
                    error_code=code,
                    retry_at=now + cfg.backoff(job.attempts),
                )
            if status is not None and job.kind in _STORE_WIDE:
                await self._store_wide_failure(
                    conn, job.client_id, job.store_id, job.kind, status, now, code
                )
        return status

    async def _store_wide_failure(
        self,
        conn: AsyncConnection[Any],
        client_id: UUID,
        store_id: UUID,
        kind: str,
        status: str,
        now: datetime,
        code: str,
    ) -> None:
        """Record a store-wide job's failure (also a crash-abandoned one); a terminal one opens
        the problem episode and drops that backfill's checkpoint, so a page that keeps failing
        is not resumed forever."""
        changes: dict[str, Any] = {"last_error_code": code}
        side = _BACKFILL_SIDE.get(kind)
        if status == "failed" and side is not None:
            state = await jobs.get_sync_store(conn, client_id, store_id)
            if state is not None:
                checkpoint = _checkpoint(state).model_copy(update={side: None})
                changes["backfill_cursor"] = checkpoint.model_dump_json()
        await jobs.set_store_state(conn, client_id, store_id, now=now, **changes)
        if status == "failed":
            await self._open_problem(conn, client_id, store_id, now, code)

    async def _open_problem(
        self, conn: AsyncConnection[Any], client_id: UUID, store_id: UUID, now: datetime, code: str
    ) -> None:
        """One notification per store per failure episode; a store-wide success closes it."""
        row = await db.q.fetch_one(
            "marketplace/claim_problem_notice.sql",
            {"client_id": client_id, "store_id": store_id, "code": code, "now": now},
            conn=conn,
        )
        if row is None:
            return
        await notify(
            conn,
            client_id=client_id,
            event=PROBLEM_EVENT,
            priority="high",
            payload={"text": PROBLEM_TEXT},
            dedupe_key=f"marketplace_sync:{store_id}:{row['problem_notified_at'].isoformat()}",
        )

    # -- reads and writes -------------------------------------------------------------------

    async def _read(
        self, job: SyncJob, shop_id: str, request: TikTokReadRequest
    ) -> TikTokReadResult:
        """One fenced, single-use read on the store's current grant; never retried here."""
        store = await self._store(job)
        if store is None or store.sync_state in _GONE:
            raise _StoreGone(store.sync_state if store is not None else "pending")
        if store.sync_state != "connected" or store.grant_id is None:
            raise _SyncFailure("store_unavailable")
        if store.shop_id != shop_id:
            raise _StoreGone("disconnected")
        return await fenced_read(
            self._app_pool,
            job.client_id,
            store.grant_id,
            request,
            load_app=self._load_app,
            decrypt=self._decrypt,
        )

    async def _write(
        self,
        job: SyncJob,
        shop_id: str,
        writer: Writer,
        *,
        final: bool,
        closes_episode: bool = False,
        hand_back: bool = False,
    ) -> None:
        """Store one page atomically behind the write gate and this worker's claim.

        ``final`` also finishes the job as done in the same transaction, so a finished job never
        runs twice. With ``closes_episode`` (this store-wide job may end a failure episode) the
        episode closes only if, after this write, both orders and products have been synced at
        least once: a store with one side still failing stays in its episode, so staff are not
        told again every catch-up cycle. ``hand_back`` returns the claim to the queue after this
        page without spending its attempt (the checkpoint written by ``writer`` resumes it).
        """
        now = self._now()
        async with tx(self._app_pool, str(job.client_id)) as conn:
            if not await jobs.lock_store_for_write(conn, job.client_id, job.store_id, shop_id):
                current = await jobs.get_sync_store(conn, job.client_id, job.store_id)
                state = current.sync_state if current is not None else "pending"
                # Same shop but no longer writable: report what it is; another shop: disconnected.
                raise _StoreGone(state if state in _GONE else "disconnected")
            if not await jobs.touch_job(conn, job, now=now):
                raise _LeaseLost
            store = await jobs.get_sync_store(conn, job.client_id, job.store_id)
            if store is None:
                raise _StoreGone("pending")
            await writer(conn, store)
            if final:
                if await jobs.finish_job(conn, job, "done") is None:
                    raise _LeaseLost
                after = await jobs.get_sync_store(conn, job.client_id, job.store_id)
                if (
                    closes_episode
                    and after is not None
                    and after.orders_synced_at is not None
                    and after.products_synced_at is not None
                ):
                    await jobs.set_store_state(
                        conn,
                        job.client_id,
                        job.store_id,
                        now=now,
                        last_error_code=None,
                        problem_notified_at=None,
                    )
            elif hand_back:
                status = await jobs.finish_job(
                    conn, job, "pending", retry_at=now, refund_attempt=True
                )
                if status is None:
                    raise _LeaseLost
        if hand_back and not final:
            raise _HandedBack

    async def _store_orders(
        self,
        conn: AsyncConnection[Any],
        job: SyncJob,
        shop_id: str,
        orders: tuple[TikTokOrderSnapshot, ...],
    ) -> None:
        now = self._now()
        for snapshot in orders:
            await upsert_order(conn, map_order(job.client_id, job.store_id, shop_id, snapshot, now))

    async def _store_products(
        self, conn: AsyncConnection[Any], job: SyncJob, products: tuple[TikTokProductSnapshot, ...]
    ) -> None:
        await store_products(conn, job.client_id, job.store_id, products, self._now())

    def _orders_writer(
        self, job: SyncJob, shop_id: str, orders: tuple[TikTokOrderSnapshot, ...]
    ) -> Writer:
        async def write(conn: AsyncConnection[Any], state: SyncStore) -> None:
            await self._store_orders(conn, job, shop_id, orders)

        return write

    def _products_writer(self, job: SyncJob, products: tuple[TikTokProductSnapshot, ...]) -> Writer:
        async def write(conn: AsyncConnection[Any], state: SyncStore) -> None:
            await self._store_products(conn, job, products)

        return write

    def _orders_backfill_writer(
        self,
        job: SyncJob,
        shop_id: str,
        page: TikTokOrderSnapshotPage,
        window: tuple[datetime, datetime],
    ) -> Writer:
        """Store one backfill page and checkpoint the next token (or finish the backfill)."""
        next_token = page.next_page_token

        async def write(conn: AsyncConnection[Any], state: SyncStore) -> None:
            await self._store_orders(conn, job, shop_id, page.items)
            changes: dict[str, Any] = {}
            if next_token is None:
                resume = None
                changes = {
                    "orders_synced_at": self._now(),
                    # Catch-up continues from where this backfill's window ended.
                    "catch_up_cursor": _earlier(state.catch_up_cursor, window[1]),
                }
            else:
                resume = _OrdersCheckpoint(
                    page_token=next_token, updated_from=window[0], updated_to=window[1]
                )
            checkpoint = _checkpoint(state).model_copy(update={"orders": resume})
            await jobs.set_store_state(
                conn,
                job.client_id,
                job.store_id,
                now=self._now(),
                backfill_cursor=checkpoint.model_dump_json(),
                **changes,
            )

        return write

    def _products_backfill_writer(
        self, job: SyncJob, page: TikTokProductSnapshotPage, started: datetime
    ) -> Writer:
        """Store one catalogue page and checkpoint the next token (or finish the backfill)."""
        next_token = page.next_page_token

        async def write(conn: AsyncConnection[Any], state: SyncStore) -> None:
            await self._store_products(conn, job, page.items)
            changes: dict[str, Any] = {}
            if next_token is None:
                resume = None
                changes = {
                    "products_synced_at": self._now(),
                    "catch_up_cursor": _earlier(state.catch_up_cursor, started),
                }
            else:
                resume = _ProductsCheckpoint(page_token=next_token, started_at=started)
            checkpoint = _checkpoint(state).model_copy(update={"products": resume})
            await jobs.set_store_state(
                conn,
                job.client_id,
                job.store_id,
                now=self._now(),
                backfill_cursor=checkpoint.model_dump_json(),
                **changes,
            )

        return write

    async def _page_pause(self) -> None:
        if self._config.page_delay_seconds > 0:
            await self._sleep(self._config.page_delay_seconds)

    # -- job kinds --------------------------------------------------------------------------

    async def _backfill_orders(self, job: SyncJob, shop_id: str) -> None:
        """The last ``backfill_days`` of orders by update time, page by page."""
        store = await self._store(job)
        resume = _checkpoint(store).orders if store is not None else None
        if resume is not None:
            window, token = (resume.updated_from, resume.updated_to), resume.page_token
        else:
            end = self._now()
            window, token = (end - timedelta(days=self._config.backfill_days), end), None
        guard = _PageGuard(self._config.max_pages_per_attempt)
        while True:
            page = await self._read(
                job,
                shop_id,
                TikTokReadRequest(
                    kind="search",
                    limit=self._config.page_size,
                    page_token=token,
                    updated_from=window[0],
                    updated_to=window[1],
                ),
            )
            if not isinstance(page, TikTokOrderSnapshotPage):
                raise _SyncFailure("unexpected_read_result")
            next_token = page.next_page_token
            guard.record(token, next_token)
            write = self._orders_backfill_writer(job, shop_id, page, window)
            await self._write(
                job,
                shop_id,
                write,
                final=next_token is None,
                closes_episode=True,
                hand_back=guard.full,
            )
            if next_token is None:
                return
            token = next_token
            await self._page_pause()

    async def _backfill_products(self, job: SyncJob, shop_id: str) -> None:
        """The whole catalogue, page by page."""
        store = await self._store(job)
        resume = _checkpoint(store).products if store is not None else None
        if resume is not None:
            started, token = resume.started_at, resume.page_token
        else:
            started, token = self._now(), None
        guard = _PageGuard(self._config.max_pages_per_attempt)
        while True:
            page = await self._read(
                job,
                shop_id,
                TikTokReadRequest(
                    kind="product_search", limit=self._config.page_size, page_token=token
                ),
            )
            if not isinstance(page, TikTokProductSnapshotPage):
                raise _SyncFailure("unexpected_read_result")
            next_token = page.next_page_token
            guard.record(token, next_token)
            write = self._products_backfill_writer(job, page, started)
            await self._write(
                job,
                shop_id,
                write,
                final=next_token is None,
                closes_episode=True,
                hand_back=guard.full,
            )
            if next_token is None:
                return
            token = next_token
            await self._page_pause()

    async def _order_refresh(self, job: SyncJob, shop_id: str) -> None:
        assert job.target is not None
        snapshot = await self._read(
            job, shop_id, TikTokReadRequest(kind="detail", order_id=job.target)
        )
        if not isinstance(snapshot, TikTokOrderSnapshot):
            raise _SyncFailure("unexpected_read_result")

        async def write(conn: AsyncConnection[Any], state: SyncStore) -> None:
            await self._store_orders(conn, job, shop_id, (snapshot,))

        await self._write(job, shop_id, write, final=True)

    async def _product_refresh(self, job: SyncJob, shop_id: str) -> None:
        assert job.target is not None
        product = await self._read(
            job, shop_id, TikTokReadRequest(kind="product_detail", product_id=job.target)
        )
        if not isinstance(product, TikTokProductSnapshot):
            raise _SyncFailure("unexpected_read_result")

        async def write(conn: AsyncConnection[Any], state: SyncStore) -> None:
            await self._store_products(conn, job, (product,))

        await self._write(job, shop_id, write, final=True)

    async def _catch_up(self, job: SyncJob, shop_id: str) -> None:
        """Orders and products changed since the cursor, in case webhooks were missed.

        A store never backfilled (no cursor, for example when the first-sync queue failed at
        connect) gets its backfills queued instead; a missing one is queued again too.
        """
        store = await self._store(job)
        if store is None:
            raise _StoreGone("pending")
        missing: list[jobs.JobKind] = []
        if store.orders_synced_at is None:
            missing.append("backfill_orders")
        if store.products_synced_at is None:
            missing.append("backfill_products")
        cursor = store.catch_up_cursor
        end = self._now()

        if cursor is not None:
            start = min(cursor - CATCH_UP_OVERLAP, end - CATCH_UP_OVERLAP)
            await self._catch_up_search(job, shop_id, "search", start, end)
            await self._catch_up_search(job, shop_id, "product_search", start, end)

        async def write(conn: AsyncConnection[Any], state: SyncStore) -> None:
            for kind in missing:
                await jobs.enqueue_job(
                    conn, client_id=job.client_id, store_id=job.store_id, kind=kind
                )
            if cursor is None:
                return
            now = self._now()
            changes: dict[str, Any] = {"catch_up_cursor": end}
            # Store-wide freshness only moves once that side has had its backfill.
            if state.orders_synced_at is not None:
                changes["orders_synced_at"] = now
            if state.products_synced_at is not None:
                changes["products_synced_at"] = now
            await jobs.set_store_state(conn, job.client_id, job.store_id, now=now, **changes)

        # Only a catch-up that actually synced and had nothing to queue may end a failure
        # episode; one that queued a missing backfill leaves it open until that side syncs.
        await self._write(
            job, shop_id, write, final=True, closes_episode=cursor is not None and not missing
        )

    async def _catch_up_search(
        self,
        job: SyncJob,
        shop_id: str,
        kind: Literal["search", "product_search"],
        start: datetime,
        end: datetime,
    ) -> None:
        token: str | None = None
        guard = _PageGuard(self._config.max_pages_per_attempt)
        while True:
            page = await self._read(
                job,
                shop_id,
                TikTokReadRequest(
                    kind=kind,
                    limit=self._config.page_size,
                    page_token=token,
                    updated_from=start,
                    updated_to=end,
                ),
            )
            if isinstance(page, TikTokOrderSnapshotPage) and kind == "search":
                write = self._orders_writer(job, shop_id, page.items)
            elif isinstance(page, TikTokProductSnapshotPage) and kind == "product_search":
                write = self._products_writer(job, page.items)
            else:
                raise _SyncFailure("unexpected_read_result")
            guard.record(token, page.next_page_token)
            await self._write(job, shop_id, write, final=False)
            token = page.next_page_token
            if token is None:
                return
            if guard.full:
                # No checkpoint to resume from: the whole window is read again next attempt.
                raise _SyncFailure("page_cap_reached")
            await self._page_pause()

    # -- token refresh ----------------------------------------------------------------------

    async def refresh_due(self) -> int:
        """Refresh every due store of the active tenants; returns how many were refreshed.

        Each store is refreshed on its own: a failed refresh is logged with its safe code only
        and does not stop the others. ``skipped`` (nothing to do) counts as neither.
        """
        due: list[tuple[UUID, UUID]] = []
        for client_id in await self._active_clients():
            try:
                async with tx(self._app_pool, str(client_id)) as conn:
                    stores = await list_refresh_due(conn, client_id, within=REFRESH_DUE_WITHIN)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "marketplace_refresh_list_failed",
                    client_id=str(client_id),
                    error_type=type(exc).__name__,
                )
                continue
            due.extend((client_id, store_id) for store_id in stores)
        gate = asyncio.Semaphore(self._config.refresh_concurrency)

        async def one(client_id: UUID, store_id: UUID) -> str:
            async with gate:
                try:
                    return await refresh_store(
                        self._app_pool,
                        client_id,
                        store_id,
                        load_app=self._load_app,
                        encrypt=self._encrypt,
                        decrypt=self._decrypt,
                        clock=self._clock,
                    )
                except TikTokAuthorizationError as exc:
                    logger.warning(
                        "marketplace_refresh_failed",
                        client_id=str(client_id),
                        store_id=str(store_id),
                        code=_safe_code(exc),
                    )
                except Exception as exc:
                    logger.error(
                        "marketplace_refresh_failed",
                        client_id=str(client_id),
                        store_id=str(store_id),
                        code="refresh_failed",
                        error_type=type(exc).__name__,
                    )
                return "failed"

        outcomes = await asyncio.gather(*(one(c, s) for c, s in due))
        if outcomes:
            logger.info(
                "marketplace_refresh_round",
                refreshed=outcomes.count("refreshed"),
                expired=outcomes.count("expired"),
                skipped=outcomes.count("skipped"),
                failed=outcomes.count("failed"),
            )
        return outcomes.count("refreshed")


async def marketplace_sync_loop(
    processor: MarketplaceSyncProcessor,
    *,
    poll_seconds: float,
    refresh_seconds: float = REFRESH_EVERY_SECONDS,
) -> None:
    """Refresh due tokens every ``refresh_seconds`` and drain the queue until cancelled.

    Errors are logged by type only and the loop carries on; it sleeps when there was no work.
    """
    next_refresh = 0.0
    while True:
        handled = 0
        try:
            if time.monotonic() >= next_refresh:
                next_refresh = time.monotonic() + refresh_seconds
                await processor.refresh_due()
            handled = await processor.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("marketplace_sync_loop_error", error_type=type(exc).__name__)
        if not handled:
            await asyncio.sleep(poll_seconds)
