"""The knowledge base as its own add-on: catalogue, the 0530 backfill, activation and gates."""

from __future__ import annotations

from pathlib import Path
from typing import LiteralString, cast
from uuid import UUID

import pytest
from csa.console.packages import package
from csa.console.services import (
    ActivateIn,
    PackageError,
    SuspendIn,
    activate_package,
    suspend_package,
)

from tests.conftest import requires_docker
from tests.test_console_services import actors, caps
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]
MIGRATION = Path(__file__).parents[1] / "db/migrations/0530_knowledge_base_addon.sql"
KB_ROUTES = ("/console/v1/kb/documents", "/console/v1/kb/ingest-jobs", "/console/v1/kb/gaps")


def test_catalogue_entry() -> None:
    addon = package("knowledge_base")
    assert addon.title == "Knowledge base"
    assert addon.kind == "addon"
    assert addon.requires == ("customer_support",)
    assert addon.capabilities == ("kb_agent",)
    assert addon.tabs == ("knowledge_base",)
    assert addon.sellable
    assert addon.summary == "Answers customer questions from the files you upload."
    support = package("customer_support")
    assert "kb_agent" not in support.capabilities
    assert support.tabs == ("inbox",)
    assert support.summary == (
        "A shared inbox for your channels, with tickets, handoff to your team and order "
        "approvals. Without an add-on, most customer questions go to your team."
    )


async def _backfill(sessions: SessionWorld) -> None:
    statement = MIGRATION.read_text().split("-- BACKFILL")[1]
    await sessions.q(cast(LiteralString, statement))


@requires_docker
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "suspended", "requested"])
async def test_backfill_copies_status(sessions: SessionWorld, status: str) -> None:
    tenant = sessions.tenants[0]
    activated = "now()" if status != "requested" else "NULL"
    await sessions.q(
        cast(
            LiteralString,
            "INSERT INTO console_service_packages"
            "(client_id,package,status,requested_at,activated_at,reference)"
            f" VALUES (%s,'customer_support',%s,now()-interval '1 day',{activated},'Q')",
        ),
        tenant,
        status,
    )
    await sessions.q(
        "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s AND name='kb_agent'",
        tenant,
    )
    await _backfill(sessions)
    rows = await sessions.q(
        "SELECT package,status,requested_by,requested_at,activated_by,activated_at,reference"
        " FROM console_service_packages WHERE client_id=%s ORDER BY package",
        tenant,
    )
    base, addon = rows[0], rows[1]
    assert (base["package"], addon["package"]) == ("customer_support", "knowledge_base")
    assert addon["status"] == status
    for field in ("requested_by", "requested_at", "activated_by", "activated_at"):
        assert addon[field] == base[field], field
    assert addon["reference"] == "backfill-0530"
    assert base["reference"] == "Q"
    assert (await caps(sessions, UUID(tenant)))["kb_agent"]["is_enabled"]


@requires_docker
@pytest.mark.asyncio
async def test_backfill_skips(sessions: SessionWorld) -> None:
    without_support, already_has = sessions.tenants[0], sessions.tenants[1]
    await sessions.q(
        "INSERT INTO console_service_packages(client_id,package,status,activated_at,reference)"
        " VALUES (%s,'customer_support','active',now(),'Q'),"
        " (%s,'knowledge_base','suspended',now(),'keep-me')",
        already_has,
        already_has,
    )
    await _backfill(sessions)
    assert (
        await sessions.q(
            "SELECT package FROM console_service_packages WHERE client_id=%s", without_support
        )
        == []
    )
    assert await sessions.q(
        "SELECT status,reference FROM console_service_packages WHERE client_id=%s AND package='knowledge_base'",
        already_has,
    ) == [{"status": "suspended", "reference": "keep-me"}]


@requires_docker
@pytest.mark.asyncio
async def test_activation_rules(sessions: SessionWorld) -> None:
    admin, _ = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool
    with pytest.raises(PackageError) as refused:
        await activate_package(
            pool, tenant, "knowledge_base", ActivateIn(reference="Q"), actor=admin
        )
    assert refused.value.status_code == 409
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    found = await caps(sessions, tenant)
    assert not found["kb_agent"]["is_enabled"]
    assert found["ticket_agent"]["is_enabled"] and found["escalation_agent"]["is_enabled"]
    await activate_package(pool, tenant, "knowledge_base", ActivateIn(reference="Q"), actor=admin)
    assert (await caps(sessions, tenant))["kb_agent"]["is_enabled"]


@requires_docker
@pytest.mark.asyncio
async def test_suspend_and_restore_order(sessions: SessionWorld) -> None:
    admin, _ = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool
    for key in ("customer_support", "knowledge_base"):
        await activate_package(pool, tenant, key, ActivateIn(reference="Q"), actor=admin)
    with pytest.raises(PackageError) as refused:
        await suspend_package(pool, tenant, "customer_support", SuspendIn(), actor=admin)
    assert refused.value.status_code == 409
    await suspend_package(pool, tenant, "knowledge_base", SuspendIn(), actor=admin)
    assert not (await caps(sessions, tenant))["kb_agent"]["is_enabled"]
    await suspend_package(pool, tenant, "customer_support", SuspendIn(), actor=admin)
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    assert not (await caps(sessions, tenant))["kb_agent"]["is_enabled"]
    await activate_package(pool, tenant, "knowledge_base", ActivateIn(reference="Q"), actor=admin)
    assert (await caps(sessions, tenant))["kb_agent"]["is_enabled"]


@requires_docker
@pytest.mark.asyncio
async def test_kb_routes_refuse_customer_support_only(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    await sessions.mutate(
        sessions.admin,
        f"/console/v1/services/customer_support/activate?client_id={tenant}",
        {"reference": "Q"},
    )
    staff, _ = await sessions.member(role="tenant_staff")
    assert (await staff.get("/console/v1/inbox/conversations")).status_code == 200
    assert (await staff.get("/console/v1/channels")).status_code != 403
    for path in KB_ROUTES:
        response = await staff.get(path)
        assert response.status_code == 403, path
        assert response.json()["detail"] == "package_inactive", path
    files = await staff.get("/console/v1/kb/files")
    assert files.status_code == 403 and files.json()["detail"] == "package_inactive"
    await sessions.mutate(
        sessions.admin,
        f"/console/v1/services/knowledge_base/activate?client_id={tenant}",
        {"reference": "Q"},
    )
    reader, _ = await sessions.member(role="read_only")
    for path in KB_ROUTES:
        assert (await reader.get(path)).status_code == 200, path
