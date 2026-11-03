"""Go live: a company's status, brand name, customer replies and warnings."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from csa.console.customer_replies import read_replies
from csa.core import db
from csa.core.tenancy import tx
from csa.graph.policy.gate import Policy

from tests.conftest import requires_docker
from tests.test_console_services import activate, actors
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]
pytestmark = [requires_docker, pytest.mark.asyncio]
PATH = "/console/v1/services/customer-replies"


def url(tenant: str) -> str:
    return f"{PATH}?client_id={tenant}"


async def save(sessions: SessionWorld, tenant: str, **body: Any) -> Any:
    return await sessions.mutate(sessions.admin, url(tenant), body, method="PUT")


async def activate_only(sessions: SessionWorld, tenant: str, *keys: str) -> None:
    """Unlike the shared helper, this doesn't activate Customer support first."""
    for key in keys:
        response = await sessions.mutate(
            sessions.admin,
            f"/console/v1/services/{key}/activate?client_id={tenant}",
            {"reference": "synthetic-test"},
        )
        assert response.status_code == 200, response.text


async def company(sessions: SessionWorld, tenant: str) -> dict[str, Any]:
    rows = await sessions.q(
        "SELECT status, brand_name, deployment_mode,"
        " config->'policy'->>'deployment_mode' AS policy_mode FROM clients WHERE id=%s",
        tenant,
    )
    return rows[0]


async def audits(sessions: SessionWorld, tenant: str, action: str) -> list[dict[str, Any]]:
    return await sessions.q(
        "SELECT actor_id, details FROM console_audit WHERE client_id=%s AND action=%s"
        " ORDER BY created_at",
        tenant,
        action,
    )


async def test_activating_customer_support_moves_onboarding_to_pilot(sessions) -> None:
    tenant = sessions.tenants[0]
    admin, _ = await actors(sessions)
    await activate(sessions, tenant, "customer_support")
    row = await company(sessions, tenant)
    assert row["status"] == "pilot"
    assert row["policy_mode"] == "shadow", "activating never turns replies on"
    rows = await audits(sessions, tenant, "tenant.status_changed")
    assert [r["details"] for r in rows] == [{"before": "onboarding", "after": "pilot"}]
    assert rows[0]["actor_id"] == admin.user_id


async def test_activating_leaves_an_active_company_alone(sessions) -> None:
    tenant = sessions.tenants[0]
    await sessions.q("UPDATE clients SET status='active' WHERE id=%s", tenant)
    await activate(sessions, tenant, "customer_support")
    assert (await company(sessions, tenant))["status"] == "active"
    assert await audits(sessions, tenant, "tenant.status_changed") == []


async def test_activating_another_package_moves_nothing(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate_only(sessions, tenant, "hr")
    assert (await company(sessions, tenant))["status"] == "onboarding"


async def test_new_company_reads_as_off(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    body = (await sessions.admin.get(url(tenant))).json()
    assert body["mode"] == "off"
    assert body["brand_name"] is None
    assert body["can_manage"] is True
    assert "replies_off" in body["warnings"]


@pytest.mark.parametrize(("mode", "stored"), [("approve", "suggest"), ("auto", "auto")])
async def test_save_turns_replies_on(sessions, mode: str, stored: str) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    response = await save(sessions, tenant, mode=mode, brand_name="  Team Synthetic  ")
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == mode
    assert "replies_off" not in response.json()["warnings"]
    row = await company(sessions, tenant)
    assert row["deployment_mode"] == stored and row["policy_mode"] == stored
    assert row["brand_name"] == "Team Synthetic"
    async with tx(sessions.pool, tenant) as conn:
        brand = await db.q.fetch_one("turn/get_client_brand.sql", (UUID(tenant),), conn=conn)
    assert brand is not None and brand["brand_name"] == "Team Synthetic"


@pytest.mark.parametrize("mode", ["approve", "auto"])
@pytest.mark.parametrize("name", [None, "", "   "])
async def test_blank_brand_refused_for_modes_that_reply(sessions, mode: str, name) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    response = await save(sessions, tenant, mode=mode, brand_name=name)
    assert response.status_code == 422
    assert response.json()["detail"] == "brand_name_required"
    row = await company(sessions, tenant)
    assert row["policy_mode"] == "shadow" and row["brand_name"] is None


@pytest.mark.parametrize("name", ["A\nB", "A\tB", "A\x00B", "A\x7fB", "A" * 121])
async def test_brand_name_rejects_control_characters_and_long_names(sessions, name: str) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    response = await save(sessions, tenant, mode="auto", brand_name=name)
    assert response.status_code == 422
    assert (await company(sessions, tenant))["brand_name"] is None


async def test_off_needs_no_brand_and_keeps_the_stored_one(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    assert (await save(sessions, tenant, mode="off")).status_code == 200
    await save(sessions, tenant, mode="auto", brand_name="Team Synthetic")
    response = await save(sessions, tenant, mode="off")
    assert response.status_code == 200, response.text
    row = await company(sessions, tenant)
    assert row["policy_mode"] == "shadow" and row["brand_name"] == "Team Synthetic"


async def test_the_stored_brand_counts_when_none_is_sent(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    await save(sessions, tenant, mode="off", brand_name="Team Synthetic")
    response = await save(sessions, tenant, mode="auto")
    assert response.status_code == 200, response.text
    assert response.json()["brand_name"] == "Team Synthetic"


async def test_panel_reports_what_the_bot_does(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    await sessions.q(
        "UPDATE clients SET deployment_mode='shadow',"
        " config=jsonb_set(config,'{policy,deployment_mode}','\"auto\"') WHERE id=%s",
        tenant,
    )
    assert (await sessions.admin.get(url(tenant))).json()["mode"] == "auto"
    await sessions.q("UPDATE clients SET config=config-'policy' WHERE id=%s", tenant)
    admin, _ = await actors(sessions)
    assert Policy().deployment_mode == "auto", "the gate's default for a missing policy"
    assert (await read_replies(sessions.pool, UUID(tenant), admin)).mode == "auto"


async def test_repeat_save_writes_no_audit_row(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    await save(sessions, tenant, mode="auto", brand_name="Team Synthetic")
    await save(sessions, tenant, mode="auto", brand_name="Team Synthetic")
    assert len(await audits(sessions, tenant, "tenant.customer_replies_set")) == 1


async def test_real_change_writes_one_audit_row(sessions) -> None:
    tenant = sessions.tenants[0]
    admin, _ = await actors(sessions)
    await activate(sessions, tenant, "customer_support")
    await save(sessions, tenant, mode="approve", brand_name="Team Synthetic")
    rows = await audits(sessions, tenant, "tenant.customer_replies_set")
    assert rows[0]["actor_id"] == admin.user_id
    assert rows[0]["details"] == {
        "before": {"mode": "off", "brand_name": None},
        "after": {"mode": "approve", "brand_name": "Team Synthetic"},
    }


async def test_save_moves_onboarding_to_pilot_and_leaves_active_alone(sessions) -> None:
    onboarding, active = sessions.tenants[0], sessions.tenants[1]
    await activate(sessions, onboarding, "customer_support")
    await activate(sessions, active, "customer_support")
    await sessions.q("UPDATE clients SET status='onboarding' WHERE id=%s", onboarding)
    await sessions.q("UPDATE clients SET status='active' WHERE id=%s", active)
    await save(sessions, onboarding, mode="off", brand_name="Team Synthetic")
    await save(sessions, active, mode="off", brand_name="Team Synthetic")
    assert (await company(sessions, onboarding))["status"] == "pilot"
    assert (await company(sessions, active))["status"] == "active"


async def test_suspended_customer_support_reads_but_refuses_save(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    await sessions.mutate(
        sessions.admin, f"/console/v1/services/customer_support/suspend?client_id={tenant}", {}
    )
    assert (await sessions.admin.get(url(tenant))).status_code == 200
    response = await save(sessions, tenant, mode="auto", brand_name="Team Synthetic")
    assert response.status_code == 409
    assert response.json()["detail"] == "package_suspended"
    assert (await company(sessions, tenant))["policy_mode"] == "shadow"


async def test_who_can_save(sessions) -> None:
    tenant, other = sessions.tenants[0], sessions.tenants[1]
    await activate(sessions, tenant, "customer_support")
    await activate(sessions, other, "customer_support")
    body = {"mode": "auto", "brand_name": "Team Synthetic"}
    for role in ("tenant_staff", "read_only"):
        browser, _ = await sessions.member(role=role)
        read = await browser.get(PATH)
        assert read.status_code == 200 and read.json()["can_manage"] is False
        refused = await sessions.mutate(browser, PATH, body, method="PUT")
        assert refused.status_code == 403, role
    outsider, _ = await sessions.member(role="tenant_admin", tenant=other)
    refused = await sessions.mutate(outsider, url(tenant), body, method="PUT")
    assert refused.status_code in {403, 404}
    assert (await company(sessions, tenant))["policy_mode"] == "shadow"
    owner, _ = await sessions.member(role="tenant_admin")
    assert (await owner.get(PATH)).json()["can_manage"] is True
    allowed = await sessions.mutate(owner, PATH, body, method="PUT")
    assert allowed.status_code == 200, allowed.text


async def test_save_needs_csrf(sessions) -> None:
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    refused = await sessions.admin.put(url(tenant), json={"mode": "off"})
    assert refused.status_code == 403


async def test_no_customer_support_is_refused(sessions) -> None:
    tenant = sessions.tenants[0]
    read = await sessions.admin.get(url(tenant))
    assert read.status_code == 403 and read.json()["detail"] == "package_inactive"
    write = await save(sessions, tenant, mode="off")
    assert write.status_code == 403 and write.json()["detail"] == "package_inactive"


async def test_warnings(sessions) -> None:
    tenant = sessions.tenants[0]

    async def warnings() -> list[str]:
        return (await sessions.admin.get(url(tenant))).json()["warnings"]

    await activate(sessions, tenant, "customer_support")
    assert "base_only" in await warnings()
    await activate_only(sessions, tenant, "hr", "staff_email")
    assert "base_only" in await warnings(), "HR and email don't answer customers"
    await activate_only(sessions, tenant, "orders_products")
    assert "base_only" not in await warnings()
    assert "no_knowledge" not in await warnings()
    await activate_only(sessions, tenant, "knowledge_base")
    assert "no_knowledge" in await warnings()
    rows = await sessions.q(
        "INSERT INTO kb_documents(client_id,title,is_active) VALUES"
        " (%s,'Synthetic',false) RETURNING id",
        tenant,
    )
    document = rows[0]["id"]
    embedding = "[" + ",".join(["0"] * 384) + "]"
    await sessions.q(
        "INSERT INTO kb_chunks(client_id,document_id,chunk_index,content,embedding)"
        " VALUES (%s,%s,0,'Synthetic',%s)",
        tenant,
        document,
        embedding,
    )
    assert "no_knowledge" in await warnings(), "an inactive document answers nothing"
    await sessions.q("UPDATE kb_documents SET is_active=true WHERE id=%s", document)
    assert "no_knowledge" not in await warnings()
