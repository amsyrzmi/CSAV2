"""Subscriptions tab: plans, subscription reads, creation and search (lane B1, task 2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.subscriptions.api import build_subscriptions_router

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/subscriptions"


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_subscriptions_router())
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "subscriptions")


async def seed_customer(
    w: SessionWorld,
    tenant: str,
    *,
    name: str = "Aisha Rahman",
    phone: str = "+60 12-345 6789",
    email: str = "aisha@example.test",
) -> str:
    rows = await w.q(
        "INSERT INTO contacts (client_id, full_name, phone, email) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        tenant,
        name,
        phone,
        email,
    )
    return str(rows[0]["id"])


async def seed_plan(
    w: SessionWorld, tenant: str, code: str = "basic", price: str = "29.90", **extra: Any
) -> None:
    await w.q(
        "INSERT INTO plans (client_id, code, name, price, billing_interval, is_active) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        tenant,
        code,
        code.title(),
        price,
        extra.get("billing_interval", "month"),
        extra.get("is_active", True),
    )


def when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_plans_crud_roles_and_isolation(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    staff, _ = await w.member(role="tenant_staff")
    reader, _ = await w.member(role="read_only")
    other, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])

    body = {"code": "gold", "name": "Gold", "price": "59.90", "features": ["priority"]}
    created = await w.mutate(admin, f"{BASE}/plans", body)
    assert created.status_code == 201, created.text
    assert created.json() == {
        "code": "gold",
        "name": "Gold",
        "price": "59.90",
        "currency": "MYR",
        "billing_interval": "month",
        "features": ["priority"],
        "sort_order": 0,
        "is_active": True,
    }
    duplicate = await w.mutate(admin, f"{BASE}/plans", body)
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "plan_exists"

    patched = await w.mutate(staff, f"{BASE}/plans/gold", {"price": "49.90"}, method="PATCH")
    assert patched.status_code == 200, patched.text
    assert patched.json()["price"] == "49.90" and patched.json()["name"] == "Gold"
    missing = await w.mutate(staff, f"{BASE}/plans/nope", {"price": "1.00"}, method="PATCH")
    assert missing.status_code == 404 and missing.json()["detail"] == "plan_not_found"

    refused = await w.mutate(reader, f"{BASE}/plans", {**body, "code": "silver"})
    assert refused.status_code == 403
    refused = await w.mutate(reader, f"{BASE}/plans/gold", {"price": "1.00"}, method="PATCH")
    assert refused.status_code == 403
    assert (await reader.get(f"{BASE}/plans")).status_code == 200

    archived = await w.mutate(admin, f"{BASE}/plans/gold", {"is_active": False}, method="PATCH")
    assert archived.status_code == 200 and archived.json()["is_active"] is False
    assert (await admin.get(f"{BASE}/plans")).json() == []
    shown = await admin.get(f"{BASE}/plans?include_archived=true")
    assert [plan["code"] for plan in shown.json()] == ["gold"]

    assert (await other.get(f"{BASE}/plans?include_archived=true")).json() == []
    assert (
        await w.mutate(other, f"{BASE}/plans/gold", {"price": "1"}, method="PATCH")
    ).status_code == 404

    actions = await w.q(
        "SELECT action, target_id FROM console_audit WHERE client_id=%s AND action LIKE %s "
        "ORDER BY created_at",
        w.tenants[0],
        "subscriptions.plan.%",
    )
    assert [row["action"] for row in actions] == [
        "subscriptions.plan.created",
        "subscriptions.plan.updated",
        "subscriptions.plan.updated",
    ]


async def test_create_subscription_snapshots_price_and_records_event(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    contact = await seed_customer(w, w.tenants[0])

    response = await w.mutate(
        staff,
        BASE,
        {"contact_id": contact, "plan_code": "basic", "started_at": "2026-03-15T08:00:00Z"},
    )
    assert response.status_code == 201, response.text
    out = response.json()
    assert out["price"] == "29.90" and out["plan"] == "basic" and out["plan_name"] == "Basic"
    assert out["status"] == "active" and out["version"] == 1 and out["currency"] == "MYR"
    assert out["customer"]["id"] == contact and out["customer"]["phone"] == "+60 12-345 6789"
    assert when(out["renews_at"]) == datetime(2026, 4, 15, 8, 0, tzinfo=UTC)

    # The price on the subscription is a snapshot; repricing the plan does not move it.
    await w.mutate(staff, f"{BASE}/plans/basic", {"price": "39.90"}, method="PATCH")
    detail = await staff.get(f"{BASE}/{out['id']}")
    assert detail.json()["subscription"]["price"] == "29.90"

    events = await w.q(
        "SELECT actor, action, from_status, to_status FROM subscription_events "
        "WHERE subscription_id=%s",
        out["id"],
    )
    assert events == [
        {
            "actor": me["user"]["id"],
            "action": "created",
            "from_status": None,
            "to_status": "active",
        }
    ]
    audit = await w.q(
        "SELECT target_id FROM console_audit WHERE client_id=%s AND action=%s",
        w.tenants[0],
        "subscriptions.created",
    )
    assert [row["target_id"] for row in audit] == [out["id"]]

    # Defaults: started now, renewing one interval later (a yearly plan renews in a year).
    await seed_plan(w, w.tenants[0], "annual", "299.00", billing_interval="year")
    again = await w.mutate(staff, BASE, {"contact_id": contact, "plan_code": "annual"})
    assert again.status_code == 201, again.text
    started, renews = when(again.json()["started_at"]), when(again.json()["renews_at"])
    assert abs((datetime.now(UTC) - started).total_seconds()) < 60
    assert renews.year == started.year + 1


async def test_create_refuses_archived_plan_and_foreign_contact(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    reader, _ = await w.member(role="read_only")
    await seed_plan(w, w.tenants[0], "old", "10.00", is_active=False)
    await seed_plan(w, w.tenants[0], "new", "12.00")
    mine = await seed_customer(w, w.tenants[0])
    theirs = await seed_customer(w, w.tenants[1], name="Other Tenant")

    archived = await w.mutate(admin, BASE, {"contact_id": mine, "plan_code": "old"})
    assert archived.status_code == 422 and archived.json()["detail"] == "plan_unavailable"
    unknown = await w.mutate(admin, BASE, {"contact_id": mine, "plan_code": "ghost"})
    assert unknown.status_code == 422 and unknown.json()["detail"] == "plan_unavailable"
    foreign = await w.mutate(admin, BASE, {"contact_id": theirs, "plan_code": "new"})
    assert foreign.status_code == 404 and foreign.json()["detail"] == "contact_not_found"
    absent = await w.mutate(admin, BASE, {"contact_id": str(uuid.uuid4()), "plan_code": "new"})
    assert absent.status_code == 404 and absent.json()["detail"] == "contact_not_found"
    forbidden = await w.mutate(reader, BASE, {"contact_id": mine, "plan_code": "new"})
    assert forbidden.status_code == 403
    assert await w.q("SELECT 1 FROM subscriptions WHERE client_id=%s", w.tenants[0]) == []


async def test_search_by_name_phone_digits_email_status_plan_with_cursor(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "pro", "99.00")
    aisha = await seed_customer(w, w.tenants[0])
    budi = await seed_customer(
        w, w.tenants[0], name="Budi 100%", phone="+60 19 888 0000", email="budi@shop.test"
    )
    made: dict[str, str] = {}
    for name, contact, plan, started in (
        ("aisha", aisha, "basic", "2026-01-01T00:00:00Z"),
        ("budi", budi, "pro", "2026-02-01T00:00:00Z"),
    ):
        response = await w.mutate(
            staff, BASE, {"contact_id": contact, "plan_code": plan, "started_at": started}
        )
        assert response.status_code == 201, response.text
        made[name] = response.json()["id"]
    await w.q("UPDATE subscriptions SET status='paused' WHERE id=%s", made["budi"])

    async def ids(query: str) -> list[str]:
        response = await staff.get(f"{BASE}?{query}")
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert await ids("") == [made["budi"], made["aisha"]]  # newest start first
    assert await ids("q=0123") == [made["aisha"]]  # digits of '+60 12-345 6789'
    assert await ids("q=%2B60%2012") == [made["aisha"]]
    assert await ids("q=rahm") == [made["aisha"]]
    assert await ids("q=SHOP.test") == [made["budi"]]
    assert await ids("q=100%25") == [made["budi"]]  # '%' is literal, not a wildcard
    assert await ids("q=%25") == [made["budi"]]
    assert await ids("q=nobody") == []
    assert await ids("status=paused") == [made["budi"]]
    assert await ids("status=active") == [made["aisha"]]
    assert await ids("plan=pro") == [made["budi"]]
    assert await ids("plan=pro&status=active") == []
    assert (await staff.get(f"{BASE}?status=bogus")).status_code == 422

    first = (await staff.get(f"{BASE}?limit=1")).json()
    assert [item["id"] for item in first["items"]] == [made["budi"]]
    assert first["next_cursor"] == made["budi"]
    second = (await staff.get(f"{BASE}?limit=1&before={first['next_cursor']}")).json()
    assert [item["id"] for item in second["items"]] == [made["aisha"]]
    assert second["next_cursor"] is None


async def test_detail_lists_events_newest_first(sessions: SessionWorld) -> None:
    w = sessions
    reader, _ = await w.member(role="read_only")
    admin, _ = await w.member(role="tenant_admin")
    other, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    await seed_plan(w, w.tenants[0])
    contact = await seed_customer(w, w.tenants[0])
    created = await w.mutate(admin, BASE, {"contact_id": contact, "plan_code": "basic"})
    sub = created.json()["id"]
    for action, stamp in (("paused", "2026-06-02"), ("resumed", "2026-06-03")):
        await w.q(
            "INSERT INTO subscription_events (client_id, subscription_id, actor, action, payload, "
            "created_at) VALUES (%s,%s,'system',%s,'{\"k\": 1}',%s)",
            w.tenants[0],
            sub,
            action,
            stamp,
        )
    response = await reader.get(f"{BASE}/{sub}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["subscription"]["id"] == sub
    # 'created' happened now (after the back-dated rows), so it leads.
    assert [event["action"] for event in detail["events"]] == ["created", "resumed", "paused"]
    assert detail["events"][1]["payload"] == {"k": 1} and detail["events"][1]["actor"] == "system"

    missing = await reader.get(f"{BASE}/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["detail"] == "subscription_not_found"
    assert (await other.get(f"{BASE}/{sub}")).status_code == 404


async def test_package_gate_and_cross_tenant_query(sessions: SessionWorld) -> None:
    w = sessions
    created = await w.mutate(
        w.admin,
        "/console/v1/admin/clients",
        {"slug": f"synthetic-{uuid.uuid4()}", "name": "No Package Tenant"},
    )
    assert created.status_code == 201, created.text
    bare, _ = await w.member(role="tenant_admin", tenant=created.json()["id"])
    for response in (
        await bare.get(f"{BASE}/plans"),
        await bare.get(BASE),
        await w.mutate(bare, f"{BASE}/plans", {"code": "x", "name": "X", "price": "1.00"}),
    ):
        assert response.status_code == 403 and response.json()["detail"] == "package_inactive"

    admin, _ = await w.member(role="tenant_admin")
    crossing = await admin.get(f"{BASE}/plans?client_id={w.tenants[1]}")
    assert crossing.status_code == 403
    crossing = await admin.get(f"{BASE}?client_id={w.tenants[1]}")
    assert crossing.status_code == 403
    # A platform admin names the tenant explicitly.
    viewed = await w.admin.get(f"{BASE}/plans?client_id={w.tenants[0]}")
    assert viewed.status_code == 200


async def test_search_digits_only_match_phones_for_phone_like_terms(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0])
    ali = await seed_customer(
        w, w.tenants[0], name="Ali", phone="+60 11 222 3333", email="ali88@gmail.com"
    )
    budi = await seed_customer(
        w, w.tenants[0], name="Budi", phone="+60 19 888 0000", email="budi@shop.test"
    )
    made: dict[str, str] = {}
    for name, contact in (("ali", ali), ("budi", budi)):
        response = await w.mutate(staff, BASE, {"contact_id": contact, "plan_code": "basic"})
        assert response.status_code == 201, response.text
        made[name] = response.json()["id"]

    async def ids(query: str) -> set[str]:
        response = await staff.get(f"{BASE}?q={query}")
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    # The 88 in an email address must not match Budi's phone (+60 19 888 0000).
    assert await ids("ali88@gmail.com") == {made["ali"]}
    assert await ids("88") == {made["ali"]}  # too short for a phone search; the email has it
    assert await ids("unit%205") == set()
    assert await ids("888") == {made["budi"]}  # three or more digits, phone-shaped
    assert await ids("%2B60%2019-888") == {made["budi"]}


async def test_default_renewal_follows_the_tenant_calendar(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    assert (await w.q("SELECT timezone FROM clients WHERE id=%s", w.tenants[0]))[0][
        "timezone"
    ] == "Asia/Kuala_Lumpur"
    await seed_plan(w, w.tenants[0], "basic")
    contact = await seed_customer(w, w.tenants[0])

    async def renews(started: str) -> datetime:
        response = await w.mutate(
            staff,
            BASE,
            {"contact_id": contact, "plan_code": "basic", "started_at": started},
        )
        assert response.status_code == 201, response.text
        return when(response.json()["renews_at"])

    # 2026-02-28T17:00Z is 1 Mar 01:00 in Kuala Lumpur, so the month ends on 1 Apr 01:00 there,
    # not on 28 Mar in UTC.
    assert await renews("2026-02-28T17:00:00Z") == datetime(2026, 3, 31, 17, 0, tzinfo=UTC)
    # 31 Jan 12:00 local clamps to the end of February (28 Feb 12:00 local).
    assert await renews("2026-01-31T04:00:00Z") == datetime(2026, 2, 28, 4, 0, tzinfo=UTC)


async def test_create_refuses_renewal_not_after_start(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0])
    contact = await seed_customer(w, w.tenants[0])
    start = "2026-03-15T08:00:00Z"
    for renews in (start, "2026-03-14T08:00:00Z"):
        response = await w.mutate(
            staff,
            BASE,
            {"contact_id": contact, "plan_code": "basic", "started_at": start, "renews_at": renews},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "renews_at_before_start"
    assert await w.q("SELECT 1 FROM subscriptions WHERE client_id=%s", w.tenants[0]) == []
    ok = await w.mutate(
        staff,
        BASE,
        {
            "contact_id": contact,
            "plan_code": "basic",
            "started_at": start,
            "renews_at": "2026-03-16T08:00:00Z",
        },
    )
    assert ok.status_code == 201, ok.text
    assert when(ok.json()["renews_at"]) == datetime(2026, 3, 16, 8, 0, tzinfo=UTC)


async def test_insert_requires_an_active_plan_at_write_time(sessions: SessionWorld) -> None:
    w = sessions
    await seed_plan(w, w.tenants[0], "old", is_active=False)
    contact = await seed_customer(w, w.tenants[0])
    async with tx(w.pool, w.tenants[0]) as conn:
        row = await db.q.fetch_one(
            "subscriptions/subscription_insert.sql",
            {
                "client_id": w.tenants[0],
                "contact_id": contact,
                "plan_code": "old",
                "started_at": None,
                "renews_at": None,
            },
            conn=conn,
        )
    assert row is None
