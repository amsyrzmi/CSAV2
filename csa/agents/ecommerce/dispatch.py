"""Internal single-attempt read fencing. No live verifier or credential resolver."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from csa.agents.ecommerce.apps import require_app
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationError,
    TikTokGrantState,
    TikTokOperationEvidence,
    TikTokReadOperation,
    require_tiktok_grant,
    require_tiktok_order_read,
)
from csa.agents.ecommerce.grants import load_grant
from csa.core import db
from csa.core.tenancy import tx

DISPATCH_TIMEOUT_SECONDS = 15


class TikTokReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    kind: Literal["detail", "search"]
    order_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, min_length=1, max_length=100)
    limit: int = Field(default=10, strict=True, ge=1, le=50)
    page_token: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def require_exact_request(self) -> TikTokReadRequest:
        if self.kind == "detail":
            if (
                self.order_id is None
                or "," in self.order_id
                or self.status is not None
                or self.page_token is not None
                or self.limit != 10
            ):
                raise ValueError("detail requires only one order ID")
        elif self.order_id is not None:
            raise ValueError("search cannot include a detail order ID")
        return self


async def begin_read(
    pool: AsyncConnectionPool, client_id: UUID, grant_id: UUID, request: TikTokReadRequest
) -> TikTokReadOperation:
    """Server-only operation creation; fresh verifier evidence is still required."""
    grant = await load_grant(pool, client_id, grant_id)
    operation = TikTokReadOperation(id=uuid4(), context=grant.context, started_at=datetime.now(UTC))
    async with tx(pool, str(client_id)) as conn:
        await db.q.execute(
            "ecommerce/create_operation.sql",
            (
                operation.id,
                client_id,
                grant_id,
                Jsonb(operation.model_dump(mode="json")),
                Jsonb(request.model_dump(mode="json")),
            ),
            conn=conn,
        )
    return operation


async def _check_fence(
    conn: AsyncConnection[Any],
    operation: TikTokReadOperation,
    evidence: TikTokOperationEvidence
    | Callable[
        [TikTokReadOperation, TikTokGrantState, AsyncConnection[Any]],
        Awaitable[TikTokOperationEvidence],
    ],
    grant_id: UUID,
) -> TikTokOperationEvidence:
    context = operation.context
    binding = context.binding
    await require_app(conn, context.app_id, context.app_revision)
    credential = await db.q.fetch_one(
        "ecommerce/lock_credential.sql",
        (binding.client_id, binding.record_id, binding.shop_id, context.credential_generation),
        conn=conn,
    )
    if credential is None:
        raise TikTokAuthorizationError("credential_changed")
    locked = await db.q.fetch_one(
        "ecommerce/lock_grant.sql",
        (binding.client_id, grant_id),
        conn=conn,
    )
    if locked is None:
        raise TikTokAuthorizationError("missing_grant")
    row = await db.q.fetch_one(
        "ecommerce/get_grant.sql",
        (binding.client_id, grant_id),
        conn=conn,
    )
    grant = TikTokGrantState.model_validate(row)
    require_tiktok_grant(operation, grant=grant, now=datetime.now(UTC))
    if callable(evidence):
        evidence = await evidence(operation, grant, conn)
    require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=datetime.now(UTC))
    return evidence


async def dispatch_read[ResultT](
    pool: AsyncConnectionPool,
    client_id: UUID,
    operation_id: UUID,
    evidence: TikTokOperationEvidence
    | Callable[
        [TikTokReadOperation, TikTokGrantState, AsyncConnection[Any]],
        Awaitable[TikTokOperationEvidence],
    ],
    send: Callable[[TikTokReadRequest], Awaitable[ResultT]],
) -> ResultT:
    """Consume durably, recheck under row fences, then invoke one bounded trusted send.

    Failure/cancellation after consumption never makes an attempt reusable. The callback
    must be server-owned, read-only, single-send and cancellation-cooperative; no retries.
    Evidence factories receive the fenced connection and must not end its transaction.
    This is not an endpoint, live verifier, credential resolver or exactly-once delivery.
    """
    async with asyncio.timeout(DISPATCH_TIMEOUT_SECONDS):
        async with tx(pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "ecommerce/get_operation.sql",
                (client_id, operation_id),
                conn=conn,
            )
            if row is None:
                raise TikTokAuthorizationError("missing_operation")
            if row["consumed_at"] is not None:
                raise TikTokAuthorizationError("operation_consumed")
            operation = TikTokReadOperation.model_validate(row["operation"])
            request = TikTokReadRequest.model_validate(row["request"])
            if operation.id != operation_id or operation.context.binding.client_id != client_id:
                raise TikTokAuthorizationError("operation_mismatch")
            grant_id = UUID(str(row["grant_id"]))
            evidence = await _check_fence(conn, operation, evidence, grant_id)
            consumed = await db.q.fetch_one(
                "ecommerce/consume_operation.sql",
                (client_id, operation_id),
                conn=conn,
            )
            if consumed is None:
                raise TikTokAuthorizationError("operation_consumed")
        # Consumption commits before any network side effect, independently of its outcome.
        async with tx(pool, str(client_id)) as conn:
            await _check_fence(conn, operation, evidence, grant_id)
            return await send(request)
