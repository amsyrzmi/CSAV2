"""Typed capability registry loading and schema synchronization (LG02 §5-6)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from csa.contracts.envelope import ContractModel
from csa.core import db
from csa.core.tenancy import tx


class CapabilityRow(ContractModel):
    """Existing capability row plus LG11-planned implementation metadata."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    client_id: UUID
    name: str = Field(min_length=1)
    display_name: str
    description: str
    pattern: str
    tier: str
    version: str
    risk_class: str
    workflow_id: str | None
    input_schema: dict[str, Any]
    config_schema: dict[str, Any] | None = None
    timeout_ms: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_iterations: int = Field(ge=0)
    requires_human: bool
    is_enabled: bool
    circuit_state: Literal["closed", "open", "half_open"]
    fail_count: int = Field(ge=0)
    opened_at: datetime | None = None
    capability_kind: Literal["dispatched", "ingress"]
    read_scopes: list[str] = Field(default_factory=list)
    write_scopes: list[str] = Field(default_factory=list)
    authorization_mode: str
    client_enabled: bool
    client_enabled_changed_at: datetime | None = None
    client_enabled_changed_by: str | None = None
    impl_kind: Literal["n8n", "python", "mcp", "http", "n8n_webhook"] = "n8n"
    impl_ref: str | None = None
    routing_hint: str | None = None


class CapabilityRegistry:
    """Cached tenant-scoped view of currently routable capabilities."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        cache_seconds: float = 30.0,
    ) -> None:
        if cache_seconds < 0:
            raise ValueError("cache_seconds must be non-negative")
        self._pool = pool
        self._cache_seconds = cache_seconds
        self._cache: dict[UUID, tuple[float, tuple[CapabilityRow, ...]]] = {}
        self._lock = asyncio.Lock()

    async def load(self, client_id: UUID | str, *, refresh: bool = False) -> list[CapabilityRow]:
        """Load enabled capabilities, caching each tenant for at most 30 seconds."""
        tenant_id = UUID(str(client_id))
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(tenant_id)
            if not refresh and cached is not None and cached[0] > now:
                return list(cached[1])

            async with tx(self._pool, str(tenant_id)) as conn:
                rows = await db.q.fetch_all(
                    "capability_registry/list_enabled.sql",
                    (tenant_id,),
                    conn=conn,
                )
            capabilities = tuple(CapabilityRow.model_validate(row) for row in rows)
            self._cache[tenant_id] = (time.monotonic() + self._cache_seconds, capabilities)
            return list(capabilities)

    async def invalidate(self, client_id: UUID | str) -> None:
        """Invalidate one tenant after a registry mutation."""
        tenant_id = UUID(str(client_id))
        async with self._lock:
            self._cache.pop(tenant_id, None)

    async def sync_input_schema(
        self,
        client_id: UUID | str,
        capability_name: str,
        input_model: type[BaseModel],
    ) -> bool:
        """Write a Pydantic input model's JSON Schema to its tenant registry row."""
        tenant_id = UUID(str(client_id))
        schema: Mapping[str, Any] = input_model.model_json_schema()
        async with tx(self._pool, str(tenant_id)) as conn:
            row = await db.q.fetch_one(
                "capability_registry/update_input_schema.sql",
                (Jsonb(dict(schema)), tenant_id, capability_name),
                conn=conn,
            )
        await self.invalidate(tenant_id)
        return row is not None
