"""Service package records: migration backfill from existing capability switches."""

from __future__ import annotations

from pathlib import Path
from typing import Any, LiteralString, cast
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.console.capabilities import CapabilityToggleIn, toggle_capability
from csa.console.contracts import ConsoleActor, MembershipOut, Role
from csa.console.services import (
    ActivateIn,
    PackageError,
    SuspendIn,
    activate_package,
    list_services,
    request_package,
    suspend_package,
)
from csa.core.tenancy import tx
from csa.email.api import build_email_router
from csa.hr.api import build_hr_router
from fastapi import HTTPException

from tests.conftest import requires_docker
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]
MIGRATION = Path(__file__).parents[1] / "db/migrations/0450_console_service_packages.sql"


async def test_backfill_marks_fully_enabled_packages_active(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    for name in ("kb_agent", "ticket_agent", "escalation_agent", "order_approval"):
        await sessions.q(
            "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s AND name=%s",
            tenant,
            name,
        )
    await sessions.q(
        "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s AND name='order_agent'",
        tenant,
    )
    await sessions.q("DELETE FROM console_service_packages WHERE client_id=%s", tenant)
    backfill = MIGRATION.read_text().split("-- BACKFILL")[1]
    await sessions.q(cast(LiteralString, backfill))
    rows = await sessions.q(
        "SELECT package, status, reference FROM console_service_packages"
        " WHERE client_id=%s ORDER BY package",
        tenant,
    )
    assert rows == [
        {"package": "customer_support", "status": "active", "reference": "backfill-0450"}
    ]


async def test_backfill_edge_cases_and_grants(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    await sessions.q(
        "UPDATE capability_registry SET is_enabled=true WHERE client_id=%s"
        " AND name IN ('kb_agent','ticket_agent','escalation_agent',"
        "'order_approval','order_agent')",
        tenant,
    )
    # A package whose member row is missing must not count as fully enabled.
    await sessions.q(
        "DELETE FROM capability_registry WHERE client_id=%s AND name='invoice_agent'", tenant
    )
    # A package with no member rows at all (zero-member rule).
    await sessions.q(
        "DELETE FROM capability_registry WHERE client_id=%s AND name='subscription_agent'", tenant
    )
    await sessions.q(
        "INSERT INTO hr_agent_settings(client_id,name,enabled) VALUES (%s,'hr_directory',true)"
        " ON CONFLICT (client_id,name) DO UPDATE SET enabled=true",
        tenant,
    )
    await sessions.q("UPDATE email_settings SET enabled=true WHERE client_id=%s", tenant)
    await sessions.q("DELETE FROM console_service_packages WHERE client_id=%s", tenant)
    await sessions.q(
        "INSERT INTO console_service_packages(client_id,package,status,reference)"
        " VALUES (%s,'customer_support','suspended','keep-me')",
        tenant,
    )
    backfill = MIGRATION.read_text().split("-- BACKFILL")[1]
    await sessions.q(cast(LiteralString, backfill))
    rows = await sessions.q(
        "SELECT package, status, reference FROM console_service_packages"
        " WHERE client_id=%s ORDER BY package",
        tenant,
    )
    assert rows == [
        {"package": "customer_support", "status": "suspended", "reference": "keep-me"},
        {"package": "hr", "status": "active", "reference": "backfill-0450"},
        {"package": "staff_email", "status": "active", "reference": "backfill-0450"},
    ]
    grants = await sessions.q(
        "SELECT has_table_privilege('csa_ro','public.console_service_packages','SELECT') AS ro,"
        " has_table_privilege('csa_identity','public.console_service_packages','SELECT') AS ident,"
        " has_table_privilege('csa_app','public.console_service_packages','UPDATE') AS app"
    )
    assert grants == [{"ro": False, "ident": False, "app": True}]


def _actor(me: dict[str, Any], tenant: str, role: Role) -> ConsoleActor:
    user = me["user"]
    return ConsoleActor(
        user_id=UUID(user["id"]),
        email=user["email"],
        display_name=user["display_name"],
        platform_admin=user["is_platform_admin"],
        must_change_password=user["must_change_password"],
        session_id=uuid4(),
        client_id=UUID(tenant),
        role=role,
        memberships=[MembershipOut.model_validate(m) for m in me["memberships"]],
        csrf_hash="synthetic",
    )


async def actors(sessions: SessionWorld) -> tuple[ConsoleActor, ConsoleActor]:
    """Return (platform admin, tenant admin) actors for sessions.tenants[0]."""
    tenant = sessions.tenants[0]
    me = (await sessions.admin.get("/console/v1/auth/me")).json()
    _, member_me = await sessions.member(role="tenant_admin")
    return _actor(me, tenant, Role.PLATFORM_ADMIN), _actor(member_me, tenant, Role.TENANT_ADMIN)


async def caps(sessions: SessionWorld, tenant: UUID) -> dict[str, dict[str, Any]]:
    rows = await sessions.q(
        "SELECT name,is_enabled,client_enabled FROM capability_registry WHERE client_id=%s",
        tenant,
    )
    return {r["name"]: r for r in rows}


async def test_request_then_activate_flips_member_switches_and_audits(
    sessions: SessionWorld,
) -> None:
    admin, tenant_admin = await actors(sessions)
    tenant = UUID(sessions.tenants[0])
    out = await request_package(sessions.pool, tenant, "customer_support", actor=tenant_admin)
    # csa_app cannot read console_users, so the actor is reported by id.
    assert out.status == "requested" and out.requested_by == str(tenant_admin.user_id)
    assert out.can_request is False
    out = await activate_package(
        sessions.pool, tenant, "customer_support", ActivateIn(reference="Q-2026-001"), actor=admin
    )
    assert out.status == "active" and out.reference == "Q-2026-001" and out.consistent
    assert out.activated_by == str(admin.user_id) and out.activated_at is not None
    assert out.can_suspend and not out.can_activate and not out.can_request
    found = await caps(sessions, tenant)
    assert all(
        found[n]["is_enabled"] and found[n]["client_enabled"]
        for n in ("ticket_agent", "escalation_agent", "order_approval")
    )
    assert not found["order_agent"]["is_enabled"] and not found["kb_agent"]["is_enabled"]
    audit = await sessions.q(
        "SELECT action, target_id, details FROM console_audit"
        " WHERE client_id=%s AND action LIKE 'service.%%' ORDER BY created_at",
        tenant,
    )
    assert [a["action"] for a in audit] == ["service.requested", "service.activated"]
    assert {a["target_id"] for a in audit} == {"customer_support"}
    assert audit[0]["details"] == {"before": None, "after": "requested", "reference": None}
    assert audit[1]["details"] == {
        "before": "requested",
        "after": "active",
        "reference": "Q-2026-001",
    }
    switches = await sessions.q(
        "SELECT target_id, details FROM console_audit"
        " WHERE client_id=%s AND action='capability.updated' ORDER BY target_id",
        tenant,
    )
    assert [s["target_id"] for s in switches] == [
        "escalation_agent",
        "order_approval",
        "ticket_agent",
    ]
    assert switches[0]["details"] == {
        "before": {"is_enabled": False, "client_enabled": False},
        "after": {"is_enabled": True, "client_enabled": True},
    }
    with pytest.raises(PackageError) as e:
        await request_package(sessions.pool, tenant, "customer_support", actor=tenant_admin)
    assert e.value.status_code == 409


async def test_addon_requires_active_base_and_base_cannot_suspend_under_active_addon(
    sessions: SessionWorld,
) -> None:
    admin, _ = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool
    with pytest.raises(PackageError) as e:
        await activate_package(
            pool, tenant, "orders_products", ActivateIn(reference="Q"), actor=admin
        )
    assert e.value.status_code == 409
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    await activate_package(pool, tenant, "orders_products", ActivateIn(reference="Q"), actor=admin)
    with pytest.raises(PackageError) as e:
        await suspend_package(pool, tenant, "customer_support", SuspendIn(), actor=admin)
    assert e.value.status_code == 409
    out = await suspend_package(
        pool, tenant, "orders_products", SuspendIn(note="trial ended"), actor=admin
    )
    assert out.status == "suspended" and out.note == "trial ended" and out.reference == "Q"
    assert out.consistent and out.can_request and out.can_activate and not out.can_suspend
    found = await caps(sessions, tenant)
    assert not found["order_agent"]["is_enabled"] and not found["invoice_agent"]["is_enabled"]
    assert (
        found["ticket_agent"]["is_enabled"]
        and found["escalation_agent"]["is_enabled"]
        and found["order_approval"]["is_enabled"]
    )
    audit = await sessions.q(
        "SELECT details FROM console_audit WHERE client_id=%s AND action='service.suspended'",
        tenant,
    )
    assert audit == [{"details": {"before": "active", "after": "suspended", "reference": "Q"}}]


async def test_manual_switch_off_keeps_package_active_but_reports_drift(
    sessions: SessionWorld,
) -> None:
    admin, _ = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    await sessions.q(
        "UPDATE capability_registry SET is_enabled=false"
        " WHERE client_id=%s AND name='ticket_agent'",
        tenant,
    )
    svc = {
        s.key: s
        for s in await list_services(
            pool, tenant, actor=admin, kb_model=None, kb_tei_configured=False
        )
    }
    assert svc["customer_support"].status == "active"
    assert svc["customer_support"].consistent is False
    assert svc["customer_support"].inconsistencies == ["ticket_agent"]
    assert svc["orders_products"].status == "available" and svc["orders_products"].consistent
    assert svc["orders_products"].can_activate


async def test_unknown_and_unsellable_keys_and_required_switch_protection(
    sessions: SessionWorld,
) -> None:
    admin, tenant_admin = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool
    for key in ("nope", "marketplaces"):
        with pytest.raises(PackageError) as e:
            await request_package(pool, tenant, key, actor=tenant_admin)
        assert e.value.status_code == 422
    with pytest.raises(HTTPException) as denied:
        await activate_package(
            pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=tenant_admin
        )
    assert denied.value.status_code == 403
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    out = await suspend_package(pool, tenant, "customer_support", SuspendIn(), actor=admin)
    assert out.consistent and out.note is None
    found = await caps(sessions, tenant)
    assert found["escalation_agent"]["is_enabled"] and found["order_approval"]["is_enabled"]
    assert not found["kb_agent"]["is_enabled"] and not found["ticket_agent"]["is_enabled"]
    with pytest.raises(PackageError) as e:
        await suspend_package(pool, tenant, "customer_support", SuspendIn(), actor=admin)
    assert e.value.status_code == 409
    # A backfilled unsellable package can still be suspended, but never activated.
    await sessions.q(
        "INSERT INTO console_service_packages(client_id,package,status,activated_at,reference)"
        " VALUES (%s,'marketplaces','active',now(),'backfill-0450')",
        tenant,
    )
    out = await suspend_package(pool, tenant, "marketplaces", SuspendIn(), actor=admin)
    assert out.status == "suspended" and not out.can_activate and not out.can_request
    with pytest.raises(PackageError) as e:
        await activate_package(pool, tenant, "marketplaces", ActivateIn(reference="Q"), actor=admin)
    assert e.value.status_code == 422


async def test_staff_email_and_hr_have_no_capability_members_and_email_inner_switch_is_reported(
    sessions: SessionWorld,
) -> None:
    _, tenant_admin = await actors(sessions)
    tenant = UUID(sessions.tenants[0])
    svc = {
        s.key: s
        for s in await list_services(
            sessions.pool, tenant, actor=tenant_admin, kb_model=None, kb_tei_configured=False
        )
    }
    assert svc["staff_email"].agents == [] and svc["staff_email"].inner_switch is False
    assert svc["hr"].inner_switch is None and svc["hr"].agents == []
    assert svc["customer_support"].inner_switch is None
    assert [a.name for a in svc["customer_support"].agents] == [
        "ticket_agent",
        "escalation_agent",
        "order_approval",
    ]
    assert [a.name for a in svc["knowledge_base"].agents] == ["kb_agent"]
    assert svc["customer_support"].can_request and not svc["customer_support"].can_activate
    assert not svc["marketplaces"].can_request
    assert [
        s.key
        for s in await list_services(
            sessions.pool, tenant, actor=None, kb_model=None, kb_tei_configured=False
        )
        if s.can_request or s.can_activate or s.can_suspend
    ] == []


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
async def test_services_routes_permissions(sessions: SessionWorld, role: str) -> None:
    browser, _ = await sessions.member(role=role)
    assert (await browser.get("/console/v1/services")).status_code == 200
    req = await sessions.mutate(browser, "/console/v1/services/customer_support/request")
    assert req.status_code == (200 if role == "tenant_admin" else 403)
    act = await sessions.mutate(
        browser, "/console/v1/services/customer_support/activate", {"reference": "Q"}
    )
    assert act.status_code == 403
    other = await browser.get("/console/v1/services", params={"client_id": sessions.tenants[1]})
    assert other.status_code == 403


async def test_platform_admin_activates_requested_addon_only_after_base(
    sessions: SessionWorld,
) -> None:
    t = sessions.tenants[0]
    base = "/console/v1/services"
    assert (
        await sessions.mutate(sessions.admin, f"{base}/orders_products/request?client_id={t}")
    ).status_code == 200
    assert (
        await sessions.mutate(
            sessions.admin, f"{base}/orders_products/activate?client_id={t}", {"reference": "Q"}
        )
    ).status_code == 409
    assert (
        await sessions.mutate(
            sessions.admin, f"{base}/customer_support/activate?client_id={t}", {"reference": "Q-1"}
        )
    ).status_code == 200
    assert (
        await sessions.mutate(
            sessions.admin, f"{base}/orders_products/activate?client_id={t}", {"reference": "Q-2"}
        )
    ).status_code == 200
    active = (await sessions.admin.get(f"{base}/active?client_id={t}")).json()
    assert sorted(p["key"] for p in active["packages"]) == ["customer_support", "orders_products"]
    assert (await sessions.admin.get(f"{base}/active")).json() == {"packages": []}


async def test_machine_token_cannot_read_or_change_services(sessions: SessionWorld) -> None:
    headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": sessions.tenants[0]}
    # A fresh client: the admin client's cookie jar would trip the mixed-auth 400 instead.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sessions.app), base_url=ORIGIN
    ) as machine:
        for path in ("/console/v1/services", "/console/v1/services/active"):
            response = await machine.get(path, headers=headers)
            assert response.status_code == 403
            assert response.json()["detail"] == "human session required"
        post = await machine.post("/console/v1/services/customer_support/request", headers=headers)
        assert post.status_code == 403


async def activate(sessions: SessionWorld, tenant: str, *keys: str) -> None:
    """Activate packages as the platform admin; add-ons get customer_support first."""
    ordered = list(keys)
    if any(k != "customer_support" for k in ordered) and "customer_support" not in ordered:
        ordered.insert(0, "customer_support")
    ordered.sort(key=lambda k: k != "customer_support")
    for key in ordered:
        r = await sessions.mutate(
            sessions.admin,
            f"/console/v1/services/{key}/activate?client_id={tenant}",
            {"reference": "synthetic-test"},
        )
        assert r.status_code == 200, r.text


async def test_inactive_packages_block_inbox_business_kb_for_every_role(sessions):
    for role in ("tenant_admin", "tenant_staff", "read_only"):
        browser, _ = await sessions.member(role=role)
        for path in (
            "/console/v1/kb/documents",
            "/console/v1/inbox/conversations",
            "/console/v1/business/orders",
            "/console/v1/invoices",
        ):
            r = await browser.get(path)
            assert r.status_code == 403 and r.json()["detail"] == "package_inactive", (
                role,
                path,
                r.text,
            )
    t = sessions.tenants[0]
    await sessions.mutate(
        sessions.admin,
        f"/console/v1/services/customer_support/activate?client_id={t}",
        {"reference": "Q"},
    )
    await sessions.mutate(
        sessions.admin,
        f"/console/v1/services/knowledge_base/activate?client_id={t}",
        {"reference": "Q"},
    )
    reader, _ = await sessions.member(role="read_only")
    assert (await reader.get("/console/v1/kb/documents")).status_code == 200
    assert (await reader.get("/console/v1/business/orders")).status_code == 403
    invoices = await reader.get("/console/v1/invoices")
    assert invoices.status_code == 403 and invoices.json()["detail"] == "package_inactive"
    staff, _ = await sessions.member(role="tenant_staff")
    assert (await staff.get("/console/v1/inbox/conversations")).status_code == 200
    r = await sessions.mutate(
        sessions.admin,
        f"/console/v1/services/orders_products/activate?client_id={t}",
        {"reference": "Q-2"},
    )
    assert r.status_code == 200, r.text
    assert (await reader.get("/console/v1/invoices")).status_code == 200


async def test_machine_token_is_refused_on_gated_route_without_active_package(
    sessions: SessionWorld,
) -> None:
    headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": sessions.tenants[0]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sessions.app), base_url=ORIGIN
    ) as machine:
        for path in ("/console/v1/business/orders", "/console/v1/invoices"):
            response = await machine.get(path, headers=headers)
            assert response.status_code == 403, (path, response.text)
            assert response.json()["detail"] == "package_inactive"


async def test_platform_admin_suspends_active_package_over_http(sessions: SessionWorld) -> None:
    t = sessions.tenants[0]
    await activate(sessions, t, "customer_support")
    url = f"/console/v1/services/customer_support/suspend?client_id={t}"
    tenant_admin, _ = await sessions.member(role="tenant_admin")
    denied = await sessions.mutate(
        tenant_admin, "/console/v1/services/customer_support/suspend", {}
    )
    assert denied.status_code == 403
    response = await sessions.mutate(sessions.admin, url, {"note": "trial ended"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suspended"
    assert response.json()["note"] == "trial ended"
    # Suspension keeps the Inbox reachable (held replies stay resolvable) but gates the KB.
    staff, _ = await sessions.member(role="tenant_staff")
    assert (await staff.get("/console/v1/inbox/conversations")).status_code == 200
    kb = await staff.get("/console/v1/kb/documents")
    assert kb.status_code == 403 and kb.json()["detail"] == "package_inactive"
    # The navigation feed carries the suspended row so the Inbox link stays visible.
    feed = (await staff.get("/console/v1/services/active")).json()["packages"]
    assert {"key": "customer_support", "status": "suspended"} in feed
    # A package that is only requested still keeps the Inbox shut.
    other = sessions.tenants[1]
    requested = await sessions.mutate(
        sessions.admin, f"/console/v1/services/customer_support/request?client_id={other}"
    )
    assert requested.status_code == 200, requested.text
    other_staff, _ = await sessions.member(role="tenant_staff", tenant=other)
    shut = await other_staff.get("/console/v1/inbox/conversations")
    assert shut.status_code == 403 and shut.json()["detail"] == "package_inactive"
    assert (await other_staff.get("/console/v1/services/active")).json() == {"packages": []}


@pytest.fixture
def services_app(sessions: SessionWorld) -> SessionWorld:
    sessions.app.include_router(build_hr_router())
    sessions.app.include_router(build_email_router())
    return sessions


async def test_hr_and_email_apis_require_their_packages(sessions, services_app):
    browser, _ = await sessions.member(role="tenant_admin")
    for path in ("/console/v1/hr/guidance", "/console/v1/email/context"):
        r = await browser.get(path)
        assert r.status_code == 403 and r.json()["detail"] == "package_inactive", (path, r.text)
    await activate(sessions, sessions.tenants[0], "hr")
    assert (await browser.get("/console/v1/hr/guidance")).status_code != 403
    r = await browser.get("/console/v1/email/context")
    assert r.status_code == 403 and r.json()["detail"] == "package_inactive"


# --- last_change (0502): newest matching console_audit row, read through a definer function.

ROLE_LABEL = "a platform administrator"
TEAM_LABEL = "a member of your team"


async def _services(sessions: SessionWorld, tenant: UUID, actor: ConsoleActor) -> dict[str, Any]:
    rows = await list_services(
        sessions.pool, tenant, actor=actor, kb_model=None, kb_tei_configured=False
    )
    return {s.key: s for s in rows}


def _agent_change(service: Any, name: str) -> Any:
    agent = next(a for a in service.agents if a.name == name)
    assert agent.capability is not None
    return agent.capability.last_change


async def test_last_change_names_the_actor_for_platform_admins_only(
    sessions: SessionWorld,
) -> None:
    admin, tenant_admin = await actors(sessions)
    tenant = UUID(sessions.tenants[0])
    before = await _services(sessions, tenant, admin)
    assert before["customer_support"].last_change is None
    assert _agent_change(before["customer_support"], "ticket_agent") is None
    await activate_package(
        sessions.pool, tenant, "customer_support", ActivateIn(reference="Q-LC"), actor=admin
    )
    seen = await _services(sessions, tenant, admin)
    change = seen["customer_support"].last_change
    assert change is not None and change.action == "activated"
    assert change.actor == admin.display_name == "Synthetic Admin"
    assert change.at is not None
    # Activation switches members on: is_enabled false->true reads as a subscription.
    member = _agent_change(seen["customer_support"], "ticket_agent")
    assert member is not None and member.action == "subscribed"
    assert member.actor == admin.display_name
    # A package nobody touched has no last change.
    assert seen["orders_products"].last_change is None
    assert _agent_change(seen["orders_products"], "order_agent") is None

    hidden = await _services(sessions, tenant, tenant_admin)
    change = hidden["customer_support"].last_change
    assert change is not None and change.action == "activated" and change.actor == ROLE_LABEL
    member = _agent_change(hidden["customer_support"], "ticket_agent")
    assert member is not None and member.actor == ROLE_LABEL
    blob = "".join(
        s.last_change.model_dump_json() if s.last_change else "" for s in hidden.values()
    ) + "".join(
        a.capability.last_change.model_dump_json()
        for s in hidden.values()
        for a in s.agents
        if a.capability and a.capability.last_change
    )
    assert "subscribed" in blob and "activated" in blob
    for secret in (str(admin.user_id), admin.email, admin.display_name):
        assert secret not in blob
    assert set(change.model_dump()) == {"action", "at", "actor"}


async def test_last_change_tracks_request_and_suspension(sessions: SessionWorld) -> None:
    admin, tenant_admin = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool
    await request_package(pool, tenant, "customer_support", actor=tenant_admin)
    change = (await _services(sessions, tenant, admin))["customer_support"].last_change
    assert change is not None and change.action == "requested"
    assert change.actor == tenant_admin.display_name
    # The tenant admin's own request reads as a team member's, never by name.
    mine = (await _services(sessions, tenant, tenant_admin))["customer_support"].last_change
    assert mine is not None and mine.action == "requested" and mine.actor == TEAM_LABEL
    for secret in (str(tenant_admin.user_id), tenant_admin.email, tenant_admin.display_name):
        assert secret not in mine.model_dump_json()
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    await activate_package(pool, tenant, "orders_products", ActivateIn(reference="Q"), actor=admin)
    await suspend_package(pool, tenant, "orders_products", SuspendIn(), actor=admin)
    seen = await _services(sessions, tenant, admin)
    change = seen["orders_products"].last_change
    assert change is not None and change.action == "suspended"
    off = _agent_change(seen["orders_products"], "order_agent")
    assert off is not None and off.action == "unsubscribed"
    assert seen["customer_support"].last_change.action == "activated"  # type: ignore[union-attr]


async def test_capability_toggles_map_to_last_change_actions(sessions: SessionWorld) -> None:
    admin, tenant_admin = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool

    async def toggle(actor: ConsoleActor, **switches: bool) -> Any:
        out = await toggle_capability(
            pool, tenant, "order_agent", CapabilityToggleIn(**switches), actor_id=actor.user_id
        )
        assert out is not None
        return _agent_change(
            (await _services(sessions, tenant, admin))["orders_products"], "order_agent"
        )

    change = await toggle(admin, is_enabled=True)
    assert (
        change is not None and change.action == "subscribed" and change.actor == "Synthetic Admin"
    )
    change = await toggle(tenant_admin, client_enabled=True)
    assert change is not None and change.action == "tenant_switch_on"
    assert change.actor == tenant_admin.display_name
    seen_by_tenant = (await _services(sessions, tenant, tenant_admin))["orders_products"]
    switch = _agent_change(seen_by_tenant, "order_agent")
    assert switch is not None and switch.action == "tenant_switch_on" and switch.actor == TEAM_LABEL
    change = await toggle(tenant_admin, client_enabled=False)
    assert change is not None and change.action == "tenant_switch_off"
    change = await toggle(admin, is_enabled=False)
    assert change is not None and change.action == "unsubscribed"
    # Both switches in one row: the subscription wins.
    change = await toggle(admin, is_enabled=True, client_enabled=True)
    assert change is not None and change.action == "subscribed"
    real = await toggle(tenant_admin, client_enabled=False)
    assert real is not None and real.action == "tenant_switch_off"
    # A toggle that changes nothing never becomes the last change: the older real one stays.
    assert await toggle(admin, client_enabled=False) == real
    assert await toggle(admin, is_enabled=True) == real


async def test_activation_no_op_member_rows_do_not_hide_an_older_change(
    sessions: SessionWorld,
) -> None:
    admin, tenant_admin = await actors(sessions)
    tenant, pool = UUID(sessions.tenants[0]), sessions.pool
    for actor, switches in (
        (admin, CapabilityToggleIn(is_enabled=True)),
        (tenant_admin, CapabilityToggleIn(client_enabled=True)),
    ):
        out = await toggle_capability(
            pool, tenant, "ticket_agent", switches, actor_id=actor.user_id
        )
        assert out is not None
    before = _agent_change(
        (await _services(sessions, tenant, admin))["customer_support"], "ticket_agent"
    )
    assert before is not None and before.action == "tenant_switch_on"
    # Activation writes a capability.updated row for ticket_agent where nothing changed.
    await activate_package(
        pool, tenant, "customer_support", ActivateIn(reference="Q-NOOP"), actor=admin
    )
    rows = await sessions.q(
        "SELECT details FROM console_audit WHERE client_id=%s AND target_id='ticket_agent'"
        " AND action='capability.updated' ORDER BY created_at DESC LIMIT 1",
        tenant,
    )
    assert rows[0]["details"]["before"] == rows[0]["details"]["after"]
    seen = await _services(sessions, tenant, admin)
    assert seen["customer_support"].last_change.action == "activated"  # type: ignore[union-attr]
    assert _agent_change(seen["customer_support"], "ticket_agent") == before


async def test_last_change_breaks_time_ties_by_id(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    low, high = "00000000-0000-4000-8000-000000000000", "ffffffff-ffff-4fff-bfff-ffffffffffff"
    # Same time; the higher id is inserted last, so insertion order alone would pick the other.
    for row_id, action in ((low, "service.requested"), (high, "service.activated")):
        await sessions.q(
            "INSERT INTO console_audit (id, client_id, action, target_id, details, created_at)"
            " VALUES (%s, %s, %s, 'customer_support', '{}', '2026-10-20T09:00:00Z')",
            row_id,
            tenant,
            action,
        )
    async with tx(sessions.pool, tenant) as conn:
        rows = await (
            await conn.execute(
                "SELECT action FROM public.console_last_change(%s, 'customer_support',"
                " ARRAY['service.requested', 'service.activated'])",
                (tenant,),
            )
        ).fetchall()
    assert [r["action"] for r in rows] == ["service.activated"]


async def test_last_change_never_crosses_tenants(sessions: SessionWorld) -> None:
    admin, _ = await actors(sessions)
    mine, other = UUID(sessions.tenants[0]), UUID(sessions.tenants[1])
    await activate_package(
        sessions.pool, other, "customer_support", ActivateIn(reference="Q"), actor=admin
    )
    seen = await _services(sessions, mine, admin)
    assert all(s.last_change is None for s in seen.values())
    assert all(
        a.capability.last_change is None for s in seen.values() for a in s.agents if a.capability
    )
    assert (await _services(sessions, other, admin))["customer_support"].last_change is not None
    call = (
        "SELECT * FROM public.console_last_change(%s, 'customer_support',"
        " ARRAY['service.activated'])"
    )
    async with tx(sessions.pool, str(mine)) as conn:
        assert await (await conn.execute(call, (mine,))).fetchall() == []
        # The function also refuses a tenant other than the transaction's own.
        assert await (await conn.execute(call, (other,))).fetchall() == []
    async with tx(sessions.pool, str(other)) as conn:
        rows = await (await conn.execute(call, (other,))).fetchall()
    assert len(rows) == 1 and rows[0]["action"] == "service.activated"
    assert set(rows[0]) == {"action", "at", "actor_name", "actor_is_platform_admin", "details"}
    assert rows[0]["actor_is_platform_admin"] is True
    # Without a tenant in the transaction the function returns nothing.
    async with sessions.pool.connection() as conn:
        assert await (await conn.execute(call, (other,))).fetchall() == []


async def test_last_change_function_does_not_widen_csa_app_grants(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM public.console_last_change(%s, 'x', ARRAY['service.activated'])",
                (tenant,),
            )
        ).fetchall()
    assert rows == []
    direct: tuple[LiteralString, ...] = (
        "SELECT 1 FROM public.console_audit LIMIT 1",
        "SELECT display_name FROM public.console_users LIMIT 1",
    )
    for statement in direct:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with tx(sessions.pool, tenant) as conn:
                await conn.execute(statement)
    grants = await sessions.q(
        "SELECT has_function_privilege('csa_app', p.oid, 'EXECUTE') AS app,"
        " has_function_privilege('csa_ro', p.oid, 'EXECUTE') AS ro,"
        " has_function_privilege('csa_identity', p.oid, 'EXECUTE') AS ident,"
        " has_table_privilege('csa_app', 'public.console_audit', 'SELECT') AS audit_select,"
        " has_table_privilege('csa_app', 'public.console_users', 'SELECT') AS users_select,"
        " (SELECT relforcerowsecurity FROM pg_class WHERE oid = 'public.console_audit'::regclass)"
        " AS forced"
        " FROM pg_proc p WHERE p.oid = 'public.console_last_change(uuid,text,text[])'::regprocedure"
    )
    assert grants == [
        {
            "app": True,
            "ro": False,
            "ident": False,
            "audit_select": False,
            "users_select": False,
            "forced": False,
        }
    ]
