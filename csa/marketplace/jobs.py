"""The marketplace sync queue (``marketplace_sync_jobs``) and per-store sync state.

Every function runs on the caller's tenant connection from ``csa.core.tenancy.tx`` (RLS
applies) and leaves the transaction to the caller. ``enqueue_job`` is the one entry point for
the connect flow, the webhook and the catch-up schedule; duplicate pending triggers fold into
one job through the pending-unique index of migration 0492.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

import psycopg
from pydantic import AwareDatetime, BaseModel, ConfigDict

from csa.agents.ecommerce.tiktok_models import is_safe_product_id
from csa.core.db import q

JobKind = Literal[
    "backfill_orders", "backfill_products", "order_refresh", "product_refresh", "catch_up"
]
JobStatus = Literal["pending", "claimed", "done", "failed", "cancelled"]
SyncState = Literal["pending", "disconnected", "expired", "unavailable", "connected"]

_TARGETED: frozenset[str] = frozenset({"order_refresh", "product_refresh"})
_MAX_TARGET = 100
_STATE_FIELDS = (
    "orders_synced_at",
    "products_synced_at",
    "catch_up_cursor",
    "backfill_cursor",
    "last_error_code",
    "problem_notified_at",
)


class MarketplaceJobError(ValueError):
    """A fixed safe code; never the target or any provider value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)


class SyncJob(_Row):
    """One claimed job. ``attempts`` identifies this claim: a requeued job gets a new one."""

    id: UUID
    client_id: UUID
    store_id: UUID
    kind: JobKind
    target: str | None
    attempts: int
    claimed_at: AwareDatetime | None
    created_at: AwareDatetime


class RequeuedJob(_Row):
    id: UUID
    store_id: UUID
    kind: JobKind
    status: JobStatus


class SyncStore(_Row):
    """A TikTok store as the sync sees it (list_connected_stores.sql explains ``sync_state``)."""

    store_id: UUID
    client_id: UUID
    shop_id: str | None
    sync_state: SyncState
    grant_id: UUID | None
    orders_synced_at: AwareDatetime | None
    products_synced_at: AwareDatetime | None
    catch_up_cursor: AwareDatetime | None
    backfill_cursor: str | None
    last_error_code: str | None
    problem_notified_at: AwareDatetime | None


def _require_shape(kind: str, target: str | None) -> None:
    if kind not in get_args(JobKind):
        raise MarketplaceJobError("invalid_job")
    if kind not in _TARGETED:
        if target is not None:
            raise MarketplaceJobError("invalid_job")
        return
    if not isinstance(target, str) or not 0 < len(target) <= _MAX_TARGET:
        raise MarketplaceJobError("invalid_job")
    # A product id goes into the signed request path, so it must be a plain token.
    if kind == "product_refresh" and not is_safe_product_id(target):
        raise MarketplaceJobError("invalid_job")


async def enqueue_job(
    conn: psycopg.AsyncConnection[Any],
    *,
    client_id: UUID,
    store_id: UUID,
    kind: JobKind,
    target: str | None = None,
) -> bool:
    """Queue a job; False when an identical pending job already exists (folded into it).

    Raises ``MarketplaceJobError`` with ``invalid_job`` for a kind/target that does not fit,
    or ``unknown_store`` when the store is not one of this tenant's TikTok stores.
    """
    _require_shape(kind, target)
    row = await q.fetch_one(
        "marketplace/enqueue_job.sql",
        {"client_id": client_id, "store_id": store_id, "kind": kind, "target": target},
        conn=conn,
    )
    if row is None or not row["store_found"]:
        raise MarketplaceJobError("unknown_store")
    return row["job_id"] is not None


async def claim_job(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, *, now: datetime
) -> SyncJob | None:
    """Claim the tenant's next due job (FOR UPDATE SKIP LOCKED), one claimed job per store."""
    await q.execute("marketplace/lock_claims.sql", {"client_id": client_id}, conn=conn)
    row = await q.fetch_one(
        "marketplace/claim_job.sql", {"client_id": client_id, "now": now}, conn=conn
    )
    return SyncJob.model_validate(row) if row is not None else None


async def requeue_stale_jobs(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    *,
    now: datetime,
    stale_after_seconds: float,
    max_attempts: int,
) -> list[RequeuedJob]:
    """Claims not touched for ``stale_after_seconds``: pending again, folded, or failed."""
    rows = await q.fetch_all(
        "marketplace/requeue_stale_jobs.sql",
        {
            "client_id": client_id,
            "now": now,
            "stale_after": stale_after_seconds,
            "max_attempts": max_attempts,
        },
        conn=conn,
    )
    return [RequeuedJob.model_validate(row) for row in rows]


def _claim_key(job: SyncJob) -> dict[str, Any]:
    return {"client_id": job.client_id, "job_id": job.id, "attempts": job.attempts}


async def touch_job(conn: psycopg.AsyncConnection[Any], job: SyncJob, *, now: datetime) -> bool:
    """Heartbeat and lock this claim; False when it was lost (requeued as stale meanwhile)."""
    row = await q.fetch_one("marketplace/touch_job.sql", {**_claim_key(job), "now": now}, conn=conn)
    return row is not None


async def finish_job(
    conn: psycopg.AsyncConnection[Any],
    job: SyncJob,
    status: Literal["done", "failed", "cancelled", "pending"],
    *,
    error_code: str | None = None,
    retry_at: datetime | None = None,
    refund_attempt: bool = False,
) -> JobStatus | None:
    """End this claim with ``status`` (``pending`` needs ``retry_at`` and may fold into
    ``cancelled``); returns the stored status, or None when the claim was lost.

    ``refund_attempt`` hands the claim back without spending its attempt."""
    row = await q.fetch_one(
        "marketplace/finish_job.sql",
        {
            **_claim_key(job),
            "status": status,
            "error_code": error_code,
            "retry_at": retry_at,
            "refund_attempt": refund_attempt,
        },
        conn=conn,
    )
    return row["status"] if row is not None else None


async def cancel_store_jobs(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, store_id: UUID, *, code: str
) -> int:
    """Cancel the store's pending jobs; returns how many."""
    rows = await q.fetch_all(
        "marketplace/cancel_store_jobs.sql",
        {"client_id": client_id, "store_id": store_id, "code": code},
        conn=conn,
    )
    return len(rows)


async def _stores(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    *,
    store_id: UUID | None,
    connected_only: bool,
) -> list[SyncStore]:
    rows = await q.fetch_all(
        "marketplace/list_connected_stores.sql",
        {"client_id": client_id, "store_id": store_id, "connected_only": connected_only},
        conn=conn,
    )
    return [SyncStore.model_validate(row) for row in rows]


async def list_connected_stores(
    conn: psycopg.AsyncConnection[Any], client_id: UUID
) -> list[SyncStore]:
    """The tenant's TikTok stores that can sync now."""
    return await _stores(conn, client_id, store_id=None, connected_only=True)


async def reset_products_sync(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, store_id: UUID, *, now: datetime
) -> bool:
    """Forget that the store's products were ever copied: ``products_synced_at`` and the products
    part of the backfill checkpoint. Orders' progress is kept. Used after the listings are purged,
    so a reconnect's catch-up sees no products and queues ``backfill_products``. Returns whether
    anything was reset."""
    store = await get_sync_store(conn, client_id, store_id)
    if store is None:
        return False
    changes: dict[str, Any] = {}
    if store.products_synced_at is not None:
        changes["products_synced_at"] = None
    if store.backfill_cursor is not None:
        try:
            parsed = json.loads(store.backfill_cursor)
        except ValueError:
            parsed = None  # a broken checkpoint is already treated as empty by the processor
        if not isinstance(parsed, dict):
            changes["backfill_cursor"] = None
        elif parsed.get("products") is not None:
            parsed["products"] = None
            changes["backfill_cursor"] = json.dumps(parsed)
    if not changes:
        return False
    await set_store_state(conn, client_id, store_id, now=now, **changes)
    return True


async def get_sync_store(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, store_id: UUID
) -> SyncStore | None:
    """One TikTok store of the tenant in any state, or None."""
    found = await _stores(conn, client_id, store_id=store_id, connected_only=False)
    return found[0] if found else None


async def lock_store_for_write(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, store_id: UUID, shop_id: str
) -> bool:
    """The write gate: the store still has this shop and is not disconnected or expired."""
    row = await q.fetch_one(
        "marketplace/lock_store_for_write.sql",
        {"client_id": client_id, "store_id": store_id, "shop_id": shop_id},
        conn=conn,
    )
    return row is not None


async def set_store_state(
    conn: psycopg.AsyncConnection[Any],
    client_id: UUID,
    store_id: UUID,
    *,
    now: datetime,
    **changes: Any,
) -> None:
    """Write the named fields of ``marketplace_store_state``; the others are kept."""
    unknown = set(changes) - set(_STATE_FIELDS)
    if unknown:
        raise MarketplaceJobError("invalid_state_field")
    params: dict[str, Any] = {"client_id": client_id, "store_id": store_id, "now": now}
    for field in _STATE_FIELDS:
        params[field] = changes.get(field)
        params[f"set_{field}"] = field in changes
    await q.execute("marketplace/set_store_state.sql", params, conn=conn)
