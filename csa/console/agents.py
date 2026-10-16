"""Truthful capability catalogue; configuration is not provider health evidence."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

from csa.console.capabilities import CapabilityOut, list_capabilities
from csa.console.contracts import Role
from csa.core import db
from csa.core.tenancy import tx

Stage = Literal["local", "prepared_only", "not_implemented"]
Readiness = Literal["available", "setup_required", "configured_unverified", "reindex_required"]

# Describes the actual default runtime wiring, not adapters that could be installed later.
CATALOG: dict[str, tuple[str, Stage, str]] = {
    "order_agent": ("Orders", "local", "Local product lookup and order drafts."),
    "ticket_agent": ("Support tickets", "local", "Tenant-scoped local support tickets."),
    "escalation_agent": ("Escalation", "local", "Required staff handoff safety capability."),
    "order_approval": ("Order approval", "local", "Required staff approval safety capability."),
    "kb_agent": ("Knowledge base", "local", "Model-compatible KB retrieval; no live TEI probe."),
    "invoice_agent": (
        "Invoices",
        "local",
        "Local lookup/status only. PDF delivery and invoice issuance are not implemented.",
    ),
    "booking_agent": (
        "Bookings",
        "prepared_only",
        "Local history and unexecuted requests. No Calendar availability or booking execution.",
    ),
    "subscription_agent": (
        "Subscriptions",
        "prepared_only",
        "Local plans/status and unexecuted requests. No payment or billing execution.",
    ),
    "ecommerce_agent": (
        "E-commerce",
        "local",
        "No marketplace adapters are wired in the default runtime. Setup is required.",
    ),
    "lhdn_agent": (
        "LHDN MyInvois",
        "not_implemented",
        "Sandbox validation, signing and submission are not implemented.",
    ),
}
SPECIALIZED = frozenset(
    {
        "kb_agent",
        "invoice_agent",
        "booking_agent",
        "subscription_agent",
        "ecommerce_agent",
        "lhdn_agent",
    }
)
REQUIRED = frozenset({"escalation_agent", "order_approval"})


class KbInventory(BaseModel):
    compatible_chunks: int
    incompatible_chunks: int


class AgentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    capability: CapabilityOut | None
    implementation: Stage
    readiness: Readiness
    detail: str
    required_system: bool
    specialized: bool
    can_view_specialized: bool
    can_subscribe: bool
    can_toggle: bool
    embedding_model: str | None = None
    compatible_chunks: int | None = None
    incompatible_chunks: int | None = None


async def list_agents(
    pool: AsyncConnectionPool,
    client_id: UUID,
    *,
    role: Role | None,
    kb_model: str | None = None,
    kb_tei_configured: bool = False,
) -> list[AgentOut]:
    caps = {cap.name: cap for cap in await list_capabilities(pool, client_id)}
    inventory: KbInventory | None = None
    if "kb_agent" in caps and kb_model is not None:
        async with tx(pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "console/kb_readiness.sql", (kb_model, kb_model, client_id), conn=conn
            )
        if row is None:
            raise RuntimeError("KB readiness aggregate missing")
        inventory = KbInventory.model_validate(row)

    result: list[AgentOut] = []
    for name in sorted(set(CATALOG) | caps.keys()):
        title, stage, detail = CATALOG.get(
            name, (name, "not_implemented", "No implementation registered in this runtime.")
        )
        cap = caps.get(name)
        readiness: Readiness = "available"
        if name == "kb_agent":
            readiness = "setup_required"
            if kb_model is not None and inventory is not None:
                if inventory.incompatible_chunks and not inventory.compatible_chunks:
                    readiness = "reindex_required"
                elif kb_tei_configured:
                    readiness = "configured_unverified"
                else:
                    detail += " Dev lexical hash embeddings only; not semantic retrieval."
        elif name == "ecommerce_agent" or stage == "not_implemented":
            readiness = "setup_required"
        required = name in REQUIRED
        result.append(
            AgentOut(
                name=name,
                title=title,
                capability=cap,
                implementation=stage,
                readiness=readiness,
                detail=detail,
                required_system=required,
                specialized=name in SPECIALIZED,
                can_view_specialized=bool(
                    name in SPECIALIZED and cap and cap.is_enabled and stage != "not_implemented"
                ),
                can_subscribe=cap is not None
                and role == Role.PLATFORM_ADMIN
                and (not required or not cap.is_enabled),
                can_toggle=cap is not None
                and role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}
                and (not required or not cap.client_enabled),
                embedding_model=kb_model if name == "kb_agent" else None,
                compatible_chunks=inventory.compatible_chunks
                if name == "kb_agent" and inventory
                else None,
                incompatible_chunks=inventory.incompatible_chunks
                if name == "kb_agent" and inventory
                else None,
            )
        )
    return result
