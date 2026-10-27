"""Subscriptions tab: subscription_action proposals executed through the registry (B1, task 3)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from csa.console.contracts import ConsoleActor, MembershipOut, Role
from csa.subscriptions.api import build_subscriptions_router
from csa.subscriptions.executor import SubscriptionActionExecutor
from csa.workspace.executors import build_proposal_registry
from csa.workspace.registry import ProposalError

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_console_subscriptions import seed_customer, seed_plan, when

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/subscriptions"
PROPOSALS = "/console/v1/workspace/proposals"


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_subscriptions_router())
    sessions.app.state.proposal_registry = build_proposal_registry()
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "subscriptions")


async def subscribe(
    w: SessionWorld,
    browser: httpx.AsyncClient,
    *,
    plan: str = "basic",
    status: str = "active",
    tenant: str | None = None,
) -> str:
    contact = await seed_customer(
        w, tenant or w.tenants[0], email=f"synthetic-{uuid.uuid4()}@example.test"
    )
    created = await w.mutate(browser, BASE, {"contact_id": contact, "plan_code": plan})
    assert created.status_code == 201, created.text
    sub: str = created.json()["id"]
    if status == "paused":
        await w.q("UPDATE subscriptions SET status='paused', paused_at=now() WHERE id=%s", sub)
    elif status == "cancelled":
        await w.q(
            "UPDATE subscriptions SET status='cancelled', cancelled_at=now() WHERE id=%s", sub
        )
    return sub


async def propose(
    w: SessionWorld, browser: httpx.AsyncClient, sub: str, body: dict[str, Any]
) -> httpx.Response:
    return await w.mutate(browser, f"{BASE}/{sub}/actions", body)


async def confirm(w: SessionWorld, browser: httpx.AsyncClient, proposal: str) -> httpx.Response:
    return await w.mutate(browser, f"{PROPOSALS}/{proposal}/confirm")


async def row(w: SessionWorld, sub: str) -> dict[str, Any]:
    return (await w.q("SELECT * FROM subscriptions WHERE id=%s", sub))[0]


async def events(w: SessionWorld, proposal: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id, actor, action, from_status, to_status, payload FROM subscription_events "
        "WHERE proposal_id=%s",
        proposal,
    )


async def proposal_rows(w: SessionWorld) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id FROM console_proposals WHERE kind='subscription_action' AND client_id=%s",
        w.tenants[0],
    )


def actor_from(me: dict[str, Any]) -> ConsoleActor:
    user = me["user"]
    return ConsoleActor(
        user_id=UUID(user["id"]),
        email=user["email"],
        display_name=user["display_name"],
        platform_admin=user["is_platform_admin"],
        must_change_password=user["must_change_password"],
        session_id=uuid.uuid4(),
        client_id=UUID(me["active_client_id"]),
        role=Role(me["role"]),
        memberships=[MembershipOut.model_validate(m) for m in me["memberships"]],
        csrf_hash="synthetic",
    )


async def direct_execute(
    w: SessionWorld, me: dict[str, Any], payload: dict[str, Any], proposal: str, version: str
) -> dict[str, Any]:
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=w.pool)))
    return await SubscriptionActionExecutor().execute(
        request,
        actor_from(me),
        UUID(w.tenants[0]),
        payload,
        proposal_id=UUID(proposal),
        expected_version=version,
        origin="tab",
    )


@pytest.mark.parametrize(
    "action,from_status,to_status",
    [
        ("pause", "active", "paused"),
        ("resume", "paused", "active"),
        ("cancel_now", "active", "cancelled"),
        ("cancel_at_period_end", "active", "active"),
    ],
)
async def test_action_card_then_confirm_applies_once_with_event(
    sessions: SessionWorld, action: str, from_status: str, to_status: str
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff, status=from_status)

    created = await propose(w, staff, sub, {"action": action, "reason": "customer asked"})
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["status"] == "requested" and out["kind"] == "subscription_action"
    assert out["card"]["expected_version"] == "1"
    assert out["card"]["summary"] == "Aisha Rahman · Basic · RM 29.90 a month"
    fields = {f["label"]: f["value"] for f in out["card"]["fields"]}
    assert list(fields) == ["Customer", "Plan", "Change", "Reason"]
    assert fields["Customer"] == "Aisha Rahman" and fields["Reason"] == "customer asked"
    assert created.headers["Cache-Control"] == "no-store"
    assert await events(w, out["id"]) == []

    done = await confirm(w, staff, out["id"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed"
    result = done.json()["result"]
    recorded = await events(w, out["id"])
    assert len(recorded) == 1
    assert result == {
        "subscription_id": sub,
        "status": to_status,
        "version": 2,
        "event_id": recorded[0]["id"],
    }
    assert recorded[0]["actor"] == me["user"]["id"]
    assert (recorded[0]["from_status"], recorded[0]["to_status"]) == (from_status, to_status)
    assert recorded[0]["payload"]["reason"] == "customer asked"
    current = await row(w, sub)
    assert current["status"] == to_status and current["version"] == 2
    if action == "pause":
        assert current["paused_at"] is not None and current["resume_at"] is None
    if action == "resume":
        assert current["paused_at"] is None and current["resume_at"] is None
    if action == "cancel_now":
        assert current["cancelled_at"] is not None and current["cancel_at_period_end"] is False
    if action == "cancel_at_period_end":
        assert current["cancel_at_period_end"] is True

    replay = await confirm(w, staff, out["id"])
    assert replay.status_code == 200 and replay.json() == done.json()
    assert len(await events(w, out["id"])) == 1
    assert (await row(w, sub))["version"] == 2


async def test_pause_until_a_date_and_resume_rolls_a_lapsed_renewal(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)

    paused = await propose(w, staff, sub, {"action": "pause", "resume_at": "2026-12-01"})
    assert paused.status_code == 201, paused.text
    change = {f["label"]: f["value"] for f in paused.json()["card"]["fields"]}["Change"]
    assert "1 Dec 2026" in change
    assert (await confirm(w, staff, paused.json()["id"])).status_code == 200
    assert str((await row(w, sub))["resume_at"]) == "2026-12-01"

    await w.q("UPDATE subscriptions SET renews_at = now() - interval '3 days' WHERE id=%s", sub)
    resumed = await propose(w, staff, sub, {"action": "resume"})
    assert resumed.status_code == 201, resumed.text
    assert (await confirm(w, staff, resumed.json()["id"])).status_code == 200
    # The lapsed renewal moves one interval past the resume, on the tenant's calendar; the
    # event and the update share the transaction's now().
    check = await w.q(
        "SELECT s.renews_at = ((e.created_at AT TIME ZONE c.timezone) + interval '1 month')"
        " AT TIME ZONE c.timezone AS ok, s.resume_at FROM subscriptions s"
        " JOIN subscription_events e ON e.subscription_id = s.id AND e.proposal_id = %s"
        " JOIN clients c ON c.id = s.client_id WHERE s.id = %s",
        resumed.json()["id"],
        sub,
    )
    assert check == [{"ok": True, "resume_at": None}]


async def test_change_plan_immediate_uses_new_plan_price_and_records_both(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    sub = await subscribe(w, staff)

    created = await propose(w, staff, sub, {"action": "change_plan", "plan_code": "gold"})
    assert created.status_code == 201, created.text
    card = created.json()["card"]
    assert card["title"] == "Change plan"
    change = {f["label"]: f["value"] for f in card["fields"]}["Change"]
    assert "Gold" in change and "RM 59.90" in change
    done = await confirm(w, staff, created.json()["id"])
    assert done.status_code == 200, done.text
    current = await row(w, sub)
    assert current["plan"] == "gold" and str(current["price"]) == "59.90"
    assert current["pending_plan"] is None and current["status"] == "active"
    [event] = await events(w, created.json()["id"])
    assert event["action"] == "plan_changed"
    assert event["payload"]["old_plan"] == "basic" and event["payload"]["new_plan"] == "gold"
    assert event["payload"]["old_price"] == "29.90" and event["payload"]["new_price"] == "59.90"

    # An override with a reason replaces the plan price; the reason is recorded.
    other = await subscribe(w, staff, status="paused")
    body = {
        "action": "change_plan",
        "plan_code": "gold",
        "price_override": "45.00",
        "reason": "loyal customer",
    }
    created = await propose(w, staff, other, body)
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    current = await row(w, other)
    assert current["plan"] == "gold" and str(current["price"]) == "45.00"
    assert current["status"] == "paused"
    [event] = await events(w, created.json()["id"])
    assert event["payload"]["new_price"] == "45.00" and event["payload"]["price_override"] is True
    assert event["payload"]["reason"] == "loyal customer"


async def test_change_plan_future_sets_pending_fields_only(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    sub = await subscribe(w, staff)
    effective = (datetime.now(UTC) + timedelta(days=10)).replace(microsecond=0)

    body = {"action": "change_plan", "plan_code": "gold", "effective_at": effective.isoformat()}
    created = await propose(w, staff, sub, body)
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    current = await row(w, sub)
    assert current["plan"] == "basic" and str(current["price"]) == "29.90"
    assert current["pending_plan"] == "gold" and current["pending_effective_at"] == effective
    assert current["version"] == 2
    [event] = await events(w, created.json()["id"])
    assert event["action"] == "plan_change_scheduled"
    assert event["payload"]["new_plan"] == "gold"
    assert when(event["payload"]["effective_at"]) == effective

    # The due job applies a pending change at the plan's price then, so an override cannot ride
    # along with a future change.
    body = {**body, "price_override": "10.00", "reason": "promo"}
    refused = await propose(w, staff, sub, body)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "price_override_requires_immediate_change"


async def test_price_override_requires_reason(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    sub = await subscribe(w, staff)
    for body in (
        {"action": "change_plan", "plan_code": "gold", "price_override": "45.00"},
        {"action": "change_plan", "plan_code": "gold", "price_override": "45.00", "reason": "  "},
    ):
        refused = await propose(w, staff, sub, body)
        assert refused.status_code == 422, refused.text
        assert "price_override requires a reason" in refused.json()["detail"][0]["msg"]
    for body, name in (
        ({"action": "change_plan"}, "plan_code"),
        ({"action": "extend_renewal"}, "renews_at"),
        ({"action": "pause", "plan_code": "gold"}, "plan_code"),
        ({"action": "resume", "resume_at": "2026-12-01"}, "resume_at"),
        ({"action": "cancel_now", "price_override": "1.00", "reason": "x"}, "price_override"),
    ):
        refused = await propose(w, staff, sub, body)
        assert refused.status_code == 422, refused.text
        assert name in refused.json()["detail"][0]["msg"]
    assert await proposal_rows(w) == []


async def test_illegal_transition_and_unavailable_plan_are_refused_at_card(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "old", "19.90", is_active=False)
    await seed_plan(w, w.tenants[1], "theirs", "9.90")
    active = await subscribe(w, staff)
    paused = await subscribe(w, staff, status="paused")
    cancelled = await subscribe(w, staff, status="cancelled")
    await w.q("UPDATE subscriptions SET cancel_at_period_end = true WHERE id=%s", active)

    for sub, body in (
        (active, {"action": "resume"}),
        (active, {"action": "cancel_at_period_end"}),  # already set
        (paused, {"action": "pause"}),
        (paused, {"action": "cancel_at_period_end"}),
        (paused, {"action": "extend_renewal", "renews_at": "2030-01-01T00:00:00Z"}),
        (cancelled, {"action": "resume"}),
        (cancelled, {"action": "cancel_now"}),
        (cancelled, {"action": "change_plan", "plan_code": "basic"}),
    ):
        refused = await propose(w, staff, sub, body)
        assert refused.status_code == 422, (body, refused.text)
        assert refused.json()["detail"] == "action_not_allowed"
    for plan in ("old", "theirs", "ghost"):
        refused = await propose(w, staff, active, {"action": "change_plan", "plan_code": plan})
        assert refused.status_code == 422 and refused.json()["detail"] == "plan_unavailable"
    same = await propose(w, staff, active, {"action": "change_plan", "plan_code": "basic"})
    assert same.status_code == 422 and same.json()["detail"] == "plan_unchanged"
    missing = await propose(w, staff, str(uuid.uuid4()), {"action": "pause"})
    assert missing.status_code == 404 and missing.json()["detail"] == "subscription_not_found"
    other, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    foreign = await propose(w, other, paused, {"action": "cancel_now"})
    assert foreign.status_code == 404 and foreign.json()["detail"] == "subscription_not_found"
    assert await proposal_rows(w) == []
    assert await w.q("SELECT 1 FROM console_proposals WHERE client_id=%s", w.tenants[1]) == []


async def test_stale_version_is_refused(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    created = await propose(w, staff, sub, {"action": "pause"})
    assert created.status_code == 201, created.text
    await w.q("UPDATE subscriptions SET version = version + 1 WHERE id=%s", sub)

    stale = await confirm(w, staff, created.json()["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    assert await events(w, created.json()["id"]) == []
    assert (await row(w, sub))["status"] == "active"

    # The executor checks the bound version again under the row lock.
    payload = {"subscription_id": sub, "action": "pause"}
    with pytest.raises(ProposalError) as refused:
        await direct_execute(w, me, payload, created.json()["id"], "1")
    assert refused.value.status_code == 409 and refused.value.detail == "proposal_stale"
    assert await events(w, created.json()["id"]) == []
    assert (await row(w, sub))["status"] == "active"


async def test_concurrent_confirms_execute_once(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    created = await propose(w, staff, sub, {"action": "pause"})
    assert created.status_code == 201, created.text
    proposal = created.json()["id"]

    replies = await asyncio.gather(confirm(w, staff, proposal), confirm(w, staff, proposal))
    codes = sorted(reply.status_code for reply in replies)
    assert codes in ([200, 200], [200, 409]), [reply.text for reply in replies]
    for reply in replies:
        if reply.status_code == 409:
            assert reply.json()["detail"] == "proposal_consumed"
    assert len(await events(w, proposal)) == 1
    assert (await row(w, sub))["version"] == 2

    # Two executions of one proposal racing past the registry: the row lock serialises them,
    # the second finds the first's event and replays its receipt.
    second = await subscribe(w, staff)
    twin = str(uuid.uuid4())
    payload = {"subscription_id": second, "action": "pause"}
    first, again = await asyncio.gather(
        direct_execute(w, me, payload, twin, "1"), direct_execute(w, me, payload, twin, "1")
    )
    assert first == again and first["version"] == 2
    assert len(await events(w, twin)) == 1
    assert (await row(w, second))["version"] == 2


async def test_read_only_cannot_create_action_and_reader_sees_no_proposals(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    reader, _ = await w.member(role="read_only")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)

    refused = await propose(w, reader, sub, {"action": "pause"})
    assert refused.status_code == 403
    direct = await w.mutate(
        reader,
        PROPOSALS,
        {"kind": "subscription_action", "payload": {"subscription_id": sub, "action": "pause"}},
    )
    assert direct.status_code == 403
    assert await proposal_rows(w) == []
    assert (await reader.get(f"{BASE}/{sub}")).status_code == 200

    # A platform admin acts for the tenant it names.
    named = await w.mutate(
        w.admin, f"{BASE}/{sub}/actions?client_id={w.tenants[0]}", {"action": "pause"}
    )
    assert named.status_code == 201, named.text


async def test_workspace_route_is_gated_on_the_subscriptions_package(
    sessions: SessionWorld,
) -> None:
    w = sessions
    created = await w.mutate(
        w.admin,
        "/console/v1/admin/clients",
        {"slug": f"synthetic-{uuid.uuid4()}", "name": "No Package Tenant"},
    )
    assert created.status_code == 201, created.text
    bare, _ = await w.member(role="tenant_staff", tenant=created.json()["id"])
    payload = {"subscription_id": str(uuid.uuid4()), "action": "pause"}
    refused = await w.mutate(bare, PROPOSALS, {"kind": "subscription_action", "payload": payload})
    assert refused.status_code == 403 and refused.json()["detail"] == "package_inactive"


async def test_extend_renewal_must_move_forward(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    current = (await row(w, sub))["renews_at"]

    for moved in (current, current - timedelta(days=1)):
        body = {"action": "extend_renewal", "renews_at": moved.isoformat()}
        refused = await propose(w, staff, sub, body)
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"] == "renews_at_not_after_current"
    later = current + timedelta(days=14)
    created = await propose(
        w, staff, sub, {"action": "extend_renewal", "renews_at": later.isoformat()}
    )
    assert created.status_code == 201, created.text
    assert created.json()["card"]["title"] == "Extend renewal"
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    assert (await row(w, sub))["renews_at"] == later
    [event] = await events(w, created.json()["id"])
    assert event["action"] == "renewal_extended"
    assert when(event["payload"]["old_renews_at"]) == current
    assert when(event["payload"]["new_renews_at"]) == later
