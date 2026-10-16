"""Console backend service for agent capabilities, subscriptions, and specialized tabs (LG13)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from csa.core import db
from csa.core.tenancy import tx


class CapabilityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    is_enabled: bool  # Admin subscription switch
    client_enabled: bool  # Client self-service switch
    active: bool  # Active only when both is_enabled and client_enabled
    circuit_state: str
    fail_count: int
    capability_kind: str
    updated_at: datetime


class CapabilityToggleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    client_enabled: bool | None = None


class SpecializedTabInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tab_id: str
    title: str
    description: str
    agent_name: str
    is_subscribed: bool  # Admin is_enabled
    is_active: bool  # Active = is_enabled and client_enabled
    metrics: dict[str, Any] = Field(default_factory=dict)
    implementation: str
    readiness: str
    can_view: bool
    can_manage: bool


async def list_capabilities(pool: AsyncConnectionPool, client_id: UUID) -> list[CapabilityOut]:
    """List all capabilities with their admin subscription and client toggle state."""
    async with tx(pool, str(client_id)) as conn:
        rows = await db.q.fetch_all(
            "capability_registry/list_all.sql",
            (client_id,),
            conn=conn,
        )

    return [
        CapabilityOut(
            id=r["id"],
            name=r["name"],
            is_enabled=r["is_enabled"],
            client_enabled=r["client_enabled"],
            active=r["is_enabled"] and r["client_enabled"],
            circuit_state=r["circuit_state"],
            fail_count=r["fail_count"],
            capability_kind=r["capability_kind"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


async def toggle_capability(
    pool: AsyncConnectionPool,
    client_id: UUID,
    name: str,
    toggle: CapabilityToggleIn,
    *,
    actor_id: UUID,
) -> CapabilityOut | None:
    """Update admin subscription or client toggle for an agent capability."""
    async with tx(pool, str(client_id)) as conn:
        before = await db.q.fetch_one("console/capability_lock.sql", (client_id, name), conn=conn)
        if before is None:
            return None
        row = await db.q.fetch_one(
            "capability_registry/update_enabled.sql",
            (
                toggle.is_enabled,
                toggle.client_enabled,
                toggle.client_enabled,  # Trigger for client_enabled_changed_at
                str(actor_id),
                client_id,
                name,
            ),
            conn=conn,
        )
        if row is not None:
            await db.q.execute(
                "console/audit.sql",
                (
                    actor_id,
                    client_id,
                    "capability.updated",
                    name,
                    Jsonb(
                        {
                            "before": {k: before[k] for k in ("is_enabled", "client_enabled")},
                            "after": {k: row[k] for k in ("is_enabled", "client_enabled")},
                        }
                    ),
                ),
                conn=conn,
            )

    if not row:
        return None

    # Fetch updated full record
    all_caps = await list_capabilities(pool, client_id)
    return next((c for c in all_caps if c.name == name), None)
