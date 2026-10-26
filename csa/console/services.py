"""Service packages: tenant requests, platform activation and suspension of package members."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from csa.console.access import tenant
from csa.console.agents import REQUIRED, AgentOut, list_agents
from csa.console.contracts import ConsoleActor, Role
from csa.console.packages import BY_KEY, PACKAGES, Package
from csa.core import db
from csa.core.tenancy import tx

Status = Literal["available", "requested", "active", "suspended"]


class PackageError(HTTPException):
    """409 for package state conflicts, 422 for unknown or unsellable package keys."""


class PackageState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    status: Status


class ServiceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    summary: str
    kind: str
    requires: list[str]
    sellable: bool
    tabs: list[str]
    status: Status
    reference: str | None
    note: str | None
    requested_at: datetime | None
    activated_at: datetime | None
    # csa_app cannot read console_users, so these carry the actor's user id.
    requested_by: str | None
    activated_by: str | None
    agents: list[AgentOut]
    inner_switch: bool | None
    consistent: bool
    inconsistencies: list[str]
    can_request: bool
    can_activate: bool
    can_suspend: bool


class ActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class SuspendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


def _known(key: str) -> Package:
    pkg = BY_KEY.get(key)
    if pkg is None:
        raise PackageError(422, f"unknown service package {key}")
    return pkg


def _sellable(key: str) -> Package:
    pkg = _known(key)
    if not pkg.sellable:
        raise PackageError(422, f"service package {key} is not available for sale")
    return pkg


def _statuses(rows: list[dict[str, Any]]) -> dict[str, Status]:
    return {r["package"]: r["status"] for r in rows}


def _held_elsewhere(key: str, statuses: dict[str, Status]) -> set[str]:
    """Capabilities kept on by another active package."""
    return {
        cap
        for other in PACKAGES
        if other.key != key and statuses.get(other.key) == "active"
        for cap in other.capabilities
    }


async def _lock(conn: AsyncConnection[Any], client_id: UUID) -> None:
    # One lock per tenant: activation checks requires and suspension checks dependants
    # across packages, so per-key locks would let those checks race.
    await db.q.fetch_one("console/service_package_lock.sql", (f"service:{client_id}",), conn=conn)


async def _switch(
    conn: AsyncConnection[Any],
    client_id: UUID,
    name: str,
    *,
    is_enabled: bool,
    client_enabled: bool | None,
    actor_id: UUID,
) -> None:
    """Set one member capability with the same SQL and audit shape as toggle_capability."""
    before = await db.q.fetch_one("console/capability_lock.sql", (client_id, name), conn=conn)
    if before is None:
        raise PackageError(409, f"capability {name} is not provisioned for this tenant")
    row = await db.q.fetch_one(
        "capability_registry/update_enabled.sql",
        (is_enabled, client_enabled, client_enabled, str(actor_id), client_id, name),
        conn=conn,
    )
    if row is None:
        raise RuntimeError(f"capability {name} update returned no row")
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


async def _audit(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    action: str,
    key: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    details = {
        "before": before["status"] if before else None,
        "after": after["status"],
        "reference": after["reference"],
    }
    await db.q.execute(
        "console/audit.sql", (actor.user_id, client_id, action, key, Jsonb(details)), conn=conn
    )


def _enabled(agent: AgentOut) -> bool:
    return agent.capability is not None and agent.capability.is_enabled


def _can_manage_tenant(actor: ConsoleActor | None) -> bool:
    return actor is not None and (
        actor.platform_admin or actor.role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}
    )


def _require_platform_admin(actor: ConsoleActor) -> None:
    if not actor.platform_admin:
        raise HTTPException(403, "platform admin required to change service packages")


async def list_services(
    pool: AsyncConnectionPool,
    client_id: UUID,
    *,
    actor: ConsoleActor | None,
    kb_model: str | None,
    kb_tei_configured: bool,
) -> list[ServiceOut]:
    async with tx(pool, str(client_id)) as conn:
        rows = await db.q.fetch_all("console/service_packages_list.sql", (client_id,), conn=conn)
        email = await db.q.fetch_one("email/settings_get.sql", (client_id,), conn=conn)
    if email is None:
        raise RuntimeError("email settings row missing for tenant")
    agents = {
        a.name: a
        for a in await list_agents(
            pool,
            client_id,
            role=actor.role if actor else None,
            kb_model=kb_model,
            kb_tei_configured=kb_tei_configured,
        )
    }
    by_key = {r["package"]: r for r in rows}
    statuses = _statuses(rows)
    platform = actor is not None and actor.platform_admin
    result: list[ServiceOut] = []
    for pkg in PACKAGES:
        row = by_key.get(pkg.key)
        status: Status = row["status"] if row else "available"
        active = status == "active"
        held = _held_elsewhere(pkg.key, statuses)
        members = [agents[name] for name in pkg.capabilities if name in agents]
        # Inactive packages expect members off unless required or held by another active package.
        inconsistencies = [
            a.name
            for a in members
            if (not _enabled(a) if active else _enabled(a) and a.name not in REQUIRED | held)
        ]
        requires_met = all(statuses.get(r) == "active" for r in pkg.requires)
        result.append(
            ServiceOut(
                key=pkg.key,
                title=pkg.title,
                summary=pkg.summary,
                kind=pkg.kind,
                requires=list(pkg.requires),
                sellable=pkg.sellable,
                tabs=list(pkg.tabs),
                status=status,
                reference=row["reference"] if row else None,
                note=row["note"] if row else None,
                requested_at=row["requested_at"] if row else None,
                activated_at=row["activated_at"] if row else None,
                requested_by=str(row["requested_by"]) if row and row["requested_by"] else None,
                activated_by=str(row["activated_by"]) if row and row["activated_by"] else None,
                agents=members,
                inner_switch=email["enabled"] if pkg.email else None,
                consistent=not inconsistencies,
                inconsistencies=inconsistencies,
                can_request=_can_manage_tenant(actor)
                and pkg.sellable
                and status in ("available", "suspended"),
                can_activate=platform and pkg.sellable and not active and requires_met,
                can_suspend=platform and active,
            )
        )
    return result


async def active_packages(pool: AsyncConnectionPool, client_id: UUID) -> list[PackageState]:
    async with tx(pool, str(client_id)) as conn:
        rows = await db.q.fetch_all("console/service_packages_list.sql", (client_id,), conn=conn)
    return [
        PackageState(key=r["package"], status=r["status"])
        for r in sorted(rows, key=lambda r: r["package"])
        if r["status"] == "active"
    ]


async def _service(
    pool: AsyncConnectionPool, client_id: UUID, key: str, actor: ConsoleActor
) -> ServiceOut:
    services = await list_services(
        pool, client_id, actor=actor, kb_model=None, kb_tei_configured=False
    )
    return next(s for s in services if s.key == key)


async def request_package(
    pool: AsyncConnectionPool, client_id: UUID, key: str, *, actor: ConsoleActor
) -> ServiceOut:
    if not _can_manage_tenant(actor):
        raise HTTPException(403, "tenant admin required to request service packages")
    _sellable(key)
    async with tx(pool, str(client_id)) as conn:
        await _lock(conn, client_id)
        before = await db.q.fetch_one(
            "console/service_package_get.sql", (client_id, key), conn=conn
        )
        if before is not None and before["status"] == "active":
            raise PackageError(409, f"service package {key} is already active")
        row = await db.q.fetch_one(
            "console/service_package_request.sql", (client_id, key, actor.user_id), conn=conn
        )
        if row is None:
            raise RuntimeError(f"service package {key} request returned no row")
        await _audit(conn, client_id, actor, "service.requested", key, before, row)
    return await _service(pool, client_id, key, actor)


async def activate_package(
    pool: AsyncConnectionPool,
    client_id: UUID,
    key: str,
    body: ActivateIn,
    *,
    actor: ConsoleActor,
) -> ServiceOut:
    _require_platform_admin(actor)
    pkg = _sellable(key)
    async with tx(pool, str(client_id)) as conn:
        await _lock(conn, client_id)
        before = await db.q.fetch_one(
            "console/service_package_get.sql", (client_id, key), conn=conn
        )
        if before is not None and before["status"] == "active":
            raise PackageError(409, f"service package {key} is already active")
        statuses = _statuses(
            await db.q.fetch_all("console/service_packages_list.sql", (client_id,), conn=conn)
        )
        missing = [r for r in pkg.requires if statuses.get(r) != "active"]
        if missing:
            raise PackageError(409, f"service package {key} requires active {', '.join(missing)}")
        row = await db.q.fetch_one(
            "console/service_package_activate.sql",
            (client_id, key, actor.user_id, body.reference, body.note),
            conn=conn,
        )
        if row is None:
            raise RuntimeError(f"service package {key} activation returned no row")
        for name in pkg.capabilities:
            await _switch(
                conn, client_id, name, is_enabled=True, client_enabled=True, actor_id=actor.user_id
            )
        await _audit(conn, client_id, actor, "service.activated", key, before, row)
    return await _service(pool, client_id, key, actor)


async def suspend_package(
    pool: AsyncConnectionPool,
    client_id: UUID,
    key: str,
    body: SuspendIn,
    *,
    actor: ConsoleActor,
) -> ServiceOut:
    _require_platform_admin(actor)
    # Unsellable packages may still be active from the 0450 backfill and must be suspendable.
    pkg = _known(key)
    async with tx(pool, str(client_id)) as conn:
        await _lock(conn, client_id)
        before = await db.q.fetch_one(
            "console/service_package_get.sql", (client_id, key), conn=conn
        )
        if before is None or before["status"] != "active":
            raise PackageError(409, f"service package {key} is not active")
        statuses = _statuses(
            await db.q.fetch_all("console/service_packages_list.sql", (client_id,), conn=conn)
        )
        dependants = [
            p.key for p in PACKAGES if key in p.requires and statuses.get(p.key) == "active"
        ]
        if dependants:
            raise PackageError(
                409, f"service package {key} is required by active {', '.join(dependants)}"
            )
        row = await db.q.fetch_one(
            "console/service_package_suspend.sql", (body.note, client_id, key), conn=conn
        )
        if row is None:
            raise RuntimeError(f"service package {key} suspension returned no row")
        held = _held_elsewhere(key, statuses)
        for name in pkg.capabilities:
            if name in held or name in REQUIRED:
                continue
            await _switch(
                conn, client_id, name, is_enabled=False, client_enabled=None, actor_id=actor.user_id
            )
        await _audit(conn, client_id, actor, "service.suspended", key, before, row)
    return await _service(pool, client_id, key, actor)


ACTIVE_ONLY: frozenset[str] = frozenset({"active"})
_GATE_STATUSES: frozenset[str] = frozenset({"active", "suspended"})


async def require_package(
    pool: AsyncConnectionPool,
    client_id: UUID,
    keys: frozenset[str],
    statuses: frozenset[str] = ACTIVE_ONLY,
) -> None:
    """403 package_inactive unless one of the packages is in an accepted status for the tenant.

    Gates accept ``active`` only, except the Inbox, which also accepts ``suspended`` so staff
    can still resolve held replies while the required safety capabilities keep running.
    """
    if not statuses or not statuses <= _GATE_STATUSES:
        raise ValueError(f"package gate statuses must be a subset of {sorted(_GATE_STATUSES)}")
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one(
            "console/service_package_any_in_status.sql",
            (client_id, sorted(keys), sorted(statuses)),
            conn=conn,
        )
    if row is None:
        raise HTTPException(403, "package_inactive")


def package_gate(
    *keys: str, statuses: tuple[str, ...] = ("active",)
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Dependency factory; runs after console authentication because it resolves the tenant."""
    unknown = [k for k in keys if k not in BY_KEY]
    if not keys or unknown:
        raise ValueError(f"package_gate needs known package keys, got {keys}")
    required, accepted = frozenset(keys), frozenset(statuses)
    if not accepted or not accepted <= _GATE_STATUSES:
        raise ValueError(f"package_gate statuses must be a subset of {sorted(_GATE_STATUSES)}")

    async def gate(request: Request, client_id: Annotated[UUID, Depends(tenant)]) -> None:
        await require_package(request.app.state.pool, client_id, required, accepted)

    return gate


def _human(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, "human session required")
    return actor


def _pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool  # type: ignore[no-any-return]


# Module level: handler annotations are strings and must resolve in module globals.
# `Human` is declared before `Tenant` in each handler so machine principals fail first.
Human = Annotated[ConsoleActor, Depends(_human)]
Tenant = Annotated[UUID, Depends(tenant)]
Pool = Annotated[AsyncConnectionPool, Depends(_pool)]


async def active_tenant(
    request: Request, actor: Human, client_id: Annotated[UUID | None, Query()] = None
) -> UUID | None:
    if actor.platform_admin and client_id is None:
        return None
    return await tenant(request, client_id)


ActiveTenant = Annotated[UUID | None, Depends(active_tenant)]


def build_services_router(kb_model: str | None, kb_tei_configured: bool) -> APIRouter:
    router = APIRouter(prefix="/services", tags=["services"])

    async def one(
        db_pool: AsyncConnectionPool, client_id: UUID, key: str, actor: ConsoleActor
    ) -> ServiceOut:
        services = await list_services(
            db_pool, client_id, actor=actor, kb_model=kb_model, kb_tei_configured=kb_tei_configured
        )
        return next(s for s in services if s.key == key)

    @router.get("", response_model=list[ServiceOut])
    async def get_services(actor: Human, client_id: Tenant, db_pool: Pool) -> list[ServiceOut]:
        return await list_services(
            db_pool, client_id, actor=actor, kb_model=kb_model, kb_tei_configured=kb_tei_configured
        )

    @router.get("/active")
    async def get_active(client_id: ActiveTenant, db_pool: Pool) -> dict[str, list[PackageState]]:
        if client_id is None:
            return {"packages": []}
        return {"packages": await active_packages(db_pool, client_id)}

    @router.post("/{key}/request", response_model=ServiceOut)
    async def post_request(key: str, actor: Human, client_id: Tenant, db_pool: Pool) -> ServiceOut:
        await request_package(db_pool, client_id, key, actor=actor)
        return await one(db_pool, client_id, key, actor)

    @router.post("/{key}/activate", response_model=ServiceOut)
    async def post_activate(
        key: str, body: ActivateIn, actor: Human, client_id: Tenant, db_pool: Pool
    ) -> ServiceOut:
        await activate_package(db_pool, client_id, key, body, actor=actor)
        return await one(db_pool, client_id, key, actor)

    @router.post("/{key}/suspend", response_model=ServiceOut)
    async def post_suspend(
        key: str, body: SuspendIn, actor: Human, client_id: Tenant, db_pool: Pool
    ) -> ServiceOut:
        await suspend_package(db_pool, client_id, key, body, actor=actor)
        return await one(db_pool, client_id, key, actor)

    return router
