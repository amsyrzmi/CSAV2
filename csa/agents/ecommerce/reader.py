"""Server-only composition; callers must provide a trusted resolver, never browser input."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.agents.ecommerce.adapters.tiktok_shop import TikTokShopAdapter
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokGrantState,
    TikTokOperationEvidence,
    TikTokReadOperation,
)
from csa.agents.ecommerce.dispatch import TikTokReadRequest, dispatch_read
from csa.agents.ecommerce.models import TikTokShopCredentials
from csa.agents.ecommerce.tiktok_models import TikTokOrderSnapshot, TikTokOrderSnapshotPage


class BoundTikTokReader:
    """Trusted resolver output, not proof that arbitrary supplied credentials are bound.

    The resolver must authenticate the app-ID-to-app-key mapping and exact current
    credential generation. This reader never looks up or decrypts credentials itself.
    The injected HTTP client is caller-owned and must not retry or log signed requests.
    """

    def __init__(
        self,
        context: TikTokAuthorizationContext,
        credentials: TikTokShopCredentials,
        *,
        expected_app_key: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        if (
            not expected_app_key
            or credentials.app_key != expected_app_key
            or credentials.shop_id != context.binding.shop_id
            or not credentials.shop_cipher
        ):
            raise TikTokAuthorizationError("adapter_binding_mismatch")
        self._context = context
        self._adapter = TikTokShopAdapter(
            credentials.model_copy(deep=True), http_client=http_client
        )

    def require_context(self, context: TikTokAuthorizationContext) -> None:
        if self._context != context:
            raise TikTokAuthorizationError("adapter_binding_mismatch")

    async def verify_shop(self) -> None:
        await self._adapter.require_authorized_shop(self._context.binding.shop_id)

    async def read(
        self, request: TikTokReadRequest
    ) -> TikTokOrderSnapshot | TikTokOrderSnapshotPage:
        tenant = self._context.binding.client_id
        if request.kind == "detail":
            assert request.order_id is not None
            return await self._adapter.get_order_snapshot(tenant, request.order_id)
        return await self._adapter.search_order_snapshot_page(
            tenant, status=request.status, limit=request.limit, page_token=request.page_token
        )


async def read_verified_order(
    pool: AsyncConnectionPool,
    client_id: UUID,
    operation_id: UUID,
    resolve: Callable[
        [TikTokAuthorizationContext, AsyncConnection[Any]], Awaitable[BoundTikTokReader]
    ],
) -> TikTokOrderSnapshot | TikTokOrderSnapshotPage:
    """Resolve on the fenced connection, then consume and dispatch the target once.

    The resolver must not commit, roll back, close or release the caller's connection.
    """
    reader: BoundTikTokReader | None = None

    async def verify(
        operation: TikTokReadOperation, grant: TikTokGrantState, conn: AsyncConnection[Any]
    ) -> TikTokOperationEvidence:
        nonlocal reader
        reader = await resolve(operation.context, conn)
        reader.require_context(operation.context)
        await reader.verify_shop()
        return TikTokOperationEvidence(
            operation_id=operation.id,
            context=operation.context,
            grant_id=grant.id,
            grant_revision=grant.revision,
            source="fresh_authorized_shop_with_stored_grant_scopes",
            verified_at=datetime.now(UTC),
        )

    async def send(request: TikTokReadRequest) -> TikTokOrderSnapshot | TikTokOrderSnapshotPage:
        assert reader is not None
        return await reader.read(request)

    return await dispatch_read(pool, client_id, operation_id, verify, send)
