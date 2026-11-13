"""Subscriptions tab: subscription_action proposals executed through the registry (B1, task 3)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest
from csa.console.contracts import ConsoleActor, MembershipOut, Role
from csa.core.tenancy import tx
from csa.payments import invoices
from csa.payments.reference import new_invoice_id, new_reference
from csa.subscriptions.api import build_subscriptions_router
from csa.subscriptions.executor import SubscriptionActionExecutor
from csa.workspace.executors import build_proposal_registry
from csa.workspace.registry import ProposalError
from fastapi import HTTPException

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

    paused = await propose(w, staff, sub, {"action": "pause", "resume_at": "2099-12-01"})
    assert paused.status_code == 201, paused.text
    change = {f["label"]: f["value"] for f in paused.json()["card"]["fields"]}["Change"]
    assert "1 Dec 2099" in change
    assert (await confirm(w, staff, paused.json()["id"])).status_code == 200
    assert str((await row(w, sub))["resume_at"]) == "2099-12-01"

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


async def test_change_plan_holds_a_share_lock_on_the_target_plan(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from csa.subscriptions import executor
    from psycopg import errors

    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    sub = await subscribe(w, staff)
    created = await propose(w, staff, sub, {"action": "change_plan", "plan_code": "gold"})
    assert created.status_code == 201, created.text

    real_plan = executor._plan
    archive_blocked: list[bool] = []

    async def try_archive() -> bool:
        async with await psycopg.AsyncConnection.connect(w.owner_dsn, autocommit=True) as conn:
            await conn.execute("SET lock_timeout = '300ms'")
            try:
                await conn.execute(
                    "UPDATE plans SET is_active = false WHERE client_id = %s AND code = 'gold'",
                    (w.tenants[0],),
                )
            except errors.LockNotAvailable:
                return True
            return False

    async def plan_then_try_archive(conn: Any, client_id: UUID, code: Any, **kwargs: Any) -> Any:
        # Runs inside the executor's transaction: right after it read the target plan.
        row = await real_plan(conn, client_id, code, **kwargs)
        if kwargs.get("share"):
            archive_blocked.append(await try_archive())
        return row

    monkeypatch.setattr(executor, "_plan", plan_then_try_archive)
    done = await confirm(w, staff, created.json()["id"])
    assert done.status_code == 200, done.text
    assert archive_blocked == [True]


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


async def test_a_confirm_that_loses_the_race_replays_the_receipt_not_a_state_error(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second confirm checks the card after the first has already paused the subscription.

    Without the recheck that card check fails ``action_not_allowed`` (422) because the
    subscription is no longer active; the proposal was consumed, so the answer is its receipt.
    """
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    created = await propose(w, staff, sub, {"action": "pause"})
    assert created.status_code == 201, created.text
    proposal = created.json()["id"]

    original = SubscriptionActionExecutor.card
    calls = 0
    parked, release = asyncio.Event(), asyncio.Event()

    async def card(self: SubscriptionActionExecutor, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:  # the loser: held until the winner has executed and committed
            parked.set()
            await release.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(SubscriptionActionExecutor, "card", card)
    tasks = {asyncio.create_task(confirm(w, staff, proposal)) for _ in range(2)}
    await asyncio.wait_for(parked.wait(), 30)
    done, pending = await asyncio.wait(tasks, timeout=30, return_when=asyncio.FIRST_COMPLETED)
    assert len(done) == 1 and len(pending) == 1
    release.set()
    winner, loser = next(iter(done)).result(), await next(iter(pending))

    assert winner.status_code == 200, winner.text
    assert loser.status_code == 200, loser.text
    assert loser.json() == winner.json()
    assert len(await events(w, proposal)) == 1
    assert (await row(w, sub))["version"] == 2


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


async def test_change_plan_card_binds_the_plan_terms_it_shows(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    sub = await subscribe(w, staff)

    created = await propose(w, staff, sub, {"action": "change_plan", "plan_code": "gold"})
    assert created.status_code == 201, created.text
    assert created.json()["card"]["expected_version"] == "1:59.90:MYR:month"
    # The plan is repriced while the card waits: the confirm must not apply the new price.
    edited = await w.mutate(staff, f"{BASE}/plans/gold", {"price": "69.90"}, method="PATCH")
    assert edited.status_code == 200, edited.text
    stale = await confirm(w, staff, created.json()["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    assert await events(w, created.json()["id"]) == []
    current = await row(w, sub)
    assert (current["plan"], str(current["price"]), current["version"]) == ("basic", "29.90", 1)

    # An interval edit (allowed while nobody is on the plan) is caught the same way.
    created = await propose(w, staff, sub, {"action": "change_plan", "plan_code": "gold"})
    assert created.status_code == 201, created.text
    assert created.json()["card"]["expected_version"] == "1:69.90:MYR:month"
    edited = await w.mutate(
        staff, f"{BASE}/plans/gold", {"billing_interval": "year"}, method="PATCH"
    )
    assert edited.status_code == 200, edited.text
    stale = await confirm(w, staff, created.json()["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    assert (await row(w, sub))["plan"] == "basic"

    # Under the row lock too: terms that changed after the registry's re-check are refused.
    payload = {"subscription_id": sub, "action": "change_plan", "plan_code": "gold"}
    with pytest.raises(ProposalError) as refused:
        await direct_execute(w, me, payload, str(uuid.uuid4()), "1:69.90:MYR:month")
    assert refused.value.status_code == 409 and refused.value.detail == "proposal_stale"
    assert (await row(w, sub))["version"] == 1

    done = await direct_execute(w, me, payload, str(uuid.uuid4()), "1:69.90:MYR:year")
    assert done["version"] == 2
    current = await row(w, sub)
    assert current["plan"] == "gold" and str(current["price"]) == "69.90"


async def test_scheduled_change_plan_card_promises_no_price(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    sub = await subscribe(w, staff)
    effective = (datetime.now(UTC) + timedelta(days=10)).replace(microsecond=0)

    body = {"action": "change_plan", "plan_code": "gold", "effective_at": effective.isoformat()}
    created = await propose(w, staff, sub, body)
    assert created.status_code == 201, created.text
    card = created.json()["card"]
    # The due job applies the plan's price on the day, so only the subscription version binds.
    assert card["expected_version"] == "1"
    change = {f["label"]: f["value"] for f in card["fields"]}["Change"]
    assert change.startswith("Move from Basic (RM 29.90 a month) to Gold from "), change
    assert "The price will be the plan's price on that day (today RM 59.90 a month)." in change
    edited = await w.mutate(staff, f"{BASE}/plans/gold", {"price": "69.90"}, method="PATCH")
    assert edited.status_code == 200, edited.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    assert (await row(w, sub))["pending_plan"] == "gold"


async def test_missing_renewal_date_resumes_with_a_period_and_blocks_period_end_cancel(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    paused = await subscribe(w, staff, status="paused")
    await w.q("UPDATE subscriptions SET renews_at = NULL WHERE id=%s", paused)

    resumed = await propose(w, staff, paused, {"action": "resume"})
    assert resumed.status_code == 201, resumed.text
    change = {f["label"]: f["value"] for f in resumed.json()["card"]["fields"]}["Change"]
    assert change == (
        "Resume now. There is no renewal date, so the next renewal is one billing period "
        "from today."
    )
    assert (await confirm(w, staff, resumed.json()["id"])).status_code == 200
    check = await w.q(
        "SELECT s.renews_at = ((e.created_at AT TIME ZONE c.timezone) + interval '1 month')"
        " AT TIME ZONE c.timezone AS ok, s.status FROM subscriptions s"
        " JOIN subscription_events e ON e.subscription_id = s.id AND e.proposal_id = %s"
        " JOIN clients c ON c.id = s.client_id WHERE s.id = %s",
        resumed.json()["id"],
        paused,
    )
    assert check == [{"ok": True, "status": "active"}]
    [event] = await events(w, resumed.json()["id"])
    assert event["payload"]["old_renews_at"] is None
    assert event["payload"]["new_renews_at"] is not None

    # Without a renewal date the period never ends, so a period-end cancel is refused.
    active = await subscribe(w, staff)
    await w.q("UPDATE subscriptions SET renews_at = NULL WHERE id=%s", active)
    refused = await propose(w, staff, active, {"action": "cancel_at_period_end"})
    assert refused.status_code == 422 and refused.json()["detail"] == "action_not_allowed"
    assert (await row(w, active))["cancel_at_period_end"] is False

    # And again under the row lock, when the date is cleared after the card was made.
    later = await subscribe(w, staff)
    card = await propose(w, staff, later, {"action": "cancel_at_period_end"})
    assert card.status_code == 201, card.text
    await w.q("UPDATE subscriptions SET renews_at = NULL WHERE id=%s", later)
    stopped = await confirm(w, staff, card.json()["id"])
    assert stopped.status_code == 422 and stopped.json()["detail"] == "action_not_allowed"
    payload = {"subscription_id": later, "action": "cancel_at_period_end"}
    with pytest.raises(HTTPException) as blocked:
        await direct_execute(w, me, payload, card.json()["id"], "1")
    assert blocked.value.status_code == 422 and blocked.value.detail == "action_not_allowed"
    assert (await row(w, later))["cancel_at_period_end"] is False
    assert await events(w, card.json()["id"]) == []


async def test_pause_resume_date_must_be_after_the_tenant_today(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    today = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()

    for day in (today, today - timedelta(days=1)):
        refused = await propose(w, staff, sub, {"action": "pause", "resume_at": day.isoformat()})
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"] == "resume_at_not_after_today"
    assert await proposal_rows(w) == []
    tomorrow = (today + timedelta(days=1)).isoformat()
    accepted = await propose(w, staff, sub, {"action": "pause", "resume_at": tomorrow})
    assert accepted.status_code == 201, accepted.text

    payload = {"subscription_id": sub, "action": "pause", "resume_at": today.isoformat()}
    with pytest.raises(HTTPException) as blocked:
        await direct_execute(w, me, payload, str(uuid.uuid4()), "1")
    assert blocked.value.status_code == 422
    assert blocked.value.detail == "resume_at_not_after_today"
    assert (await row(w, sub))["status"] == "active"


async def anchored(w: SessionWorld, sub: str) -> dict[str, Any]:
    return (await w.q("SELECT renewal_anchor, renews_at FROM subscriptions WHERE id=%s", sub))[0]


async def test_extend_renewal_moves_the_anchor(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    await w.q(
        "UPDATE subscriptions SET renewal_anchor = '2026-10-31 10:00+08',"
        " renews_at = '2026-11-30 10:00+08' WHERE id=%s",
        sub,
    )

    later = datetime(2026, 12, 15, 10, 0, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))
    created = await propose(
        w, staff, sub, {"action": "extend_renewal", "renews_at": later.isoformat()}
    )
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    current = await anchored(w, sub)
    assert current["renewal_anchor"] == later and current["renews_at"] == later


async def test_lapsed_resume_restarts_the_cycle_from_now(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")

    lapsed = await subscribe(w, staff, status="paused")
    await w.q(
        "UPDATE subscriptions SET renewal_anchor = '2026-01-31 10:00+08',"
        " renews_at = now() - interval '3 days' WHERE id=%s",
        lapsed,
    )
    resumed = await propose(w, staff, lapsed, {"action": "resume"})
    assert resumed.status_code == 201, resumed.text
    assert (await confirm(w, staff, resumed.json()["id"])).status_code == 200
    check = await w.q(
        "SELECT abs(extract(epoch FROM now() - s.renewal_anchor)) < 5 AS anchored_now,"
        " s.renews_at = subscription_next_renewal("
        "     s.renewal_anchor, s.renewal_anchor, 'month', c.timezone) AS on_cycle,"
        " s.renews_at > s.renewal_anchor AS after_anchor"
        " FROM subscriptions s JOIN clients c ON c.id = s.client_id WHERE s.id=%s",
        lapsed,
    )
    assert check == [{"anchored_now": True, "on_cycle": True, "after_anchor": True}]

    # A resume that is not lapsed keeps the renewal and its anchor.
    future = await subscribe(w, staff, status="paused")
    await w.q(
        "UPDATE subscriptions SET renewal_anchor = '2026-10-31 10:00+08',"
        " renews_at = '2099-11-30 10:00+08' WHERE id=%s",
        future,
    )
    resumed = await propose(w, staff, future, {"action": "resume"})
    assert resumed.status_code == 201, resumed.text
    assert (await confirm(w, staff, resumed.json()["id"])).status_code == 200
    kept = await anchored(w, future)
    assert kept["renewal_anchor"] == datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
    assert kept["renews_at"] == datetime(2099, 11, 30, 2, 0, tzinfo=UTC)


async def test_interval_change_resets_the_anchor_to_the_period_end(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "plus", "39.90")
    await seed_plan(w, w.tenants[0], "annual", "299.00", billing_interval="year")
    cycle = (
        "UPDATE subscriptions SET renewal_anchor = '2026-10-31 10:00+08',"
        " renews_at = '2099-11-30 10:00+08' WHERE id=%s"
    )

    # Monthly -> yearly: the cycle restarts at the old period end, which stays the renewal.
    sub = await subscribe(w, staff)
    await w.q(cycle, sub)
    created = await propose(w, staff, sub, {"action": "change_plan", "plan_code": "annual"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    changed = await anchored(w, sub)
    assert changed["renewal_anchor"] == datetime(2099, 11, 30, 2, 0, tzinfo=UTC)
    assert changed["renews_at"] == datetime(2099, 11, 30, 2, 0, tzinfo=UTC)

    # Monthly -> monthly: the interval is the same, so the anchor stays.
    same = await subscribe(w, staff)
    await w.q(cycle, same)
    created = await propose(w, staff, same, {"action": "change_plan", "plan_code": "plus"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    kept = await anchored(w, same)
    assert kept["renewal_anchor"] == datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
    assert kept["renews_at"] == datetime(2099, 11, 30, 2, 0, tzinfo=UTC)


async def test_interval_change_without_renewal_date_keeps_both_null(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "annual", "299.00", billing_interval="year")
    sub = await subscribe(w, staff)
    await w.q("UPDATE subscriptions SET renews_at = NULL, renewal_anchor = NULL WHERE id=%s", sub)

    created = await propose(w, staff, sub, {"action": "change_plan", "plan_code": "annual"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    assert await anchored(w, sub) == {"renewal_anchor": None, "renews_at": None}


async def pending_subscription(
    w: SessionWorld, browser: httpx.AsyncClient, *, plan: str = "basic"
) -> str:
    """A sign-up the staff approved and nobody has started: no renewal date, an old start."""
    sub = await subscribe(w, browser, plan=plan)
    await w.q(
        "UPDATE subscriptions SET status='pending', started_at = now() - interval '10 days',"
        " renews_at = NULL, renewal_anchor = NULL WHERE id=%s",
        sub,
    )
    return sub


async def test_activate_starts_a_pending_subscription_on_the_tenant_calendar(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await w.q("UPDATE clients SET timezone='Asia/Kuala_Lumpur' WHERE id=%s", w.tenants[0])
    sub = await pending_subscription(w, staff)
    before = await row(w, sub)
    assert before["status"] == "pending" and before["activated_at"] is None

    created = await propose(w, staff, sub, {"action": "activate", "reason": "paid"})
    assert created.status_code == 201, created.text
    card = created.json()["card"]
    assert card["title"] == "Start the subscription"
    assert card["expected_version"] == "1"
    done = await confirm(w, staff, created.json()["id"])
    assert done.status_code == 200, done.text
    [event] = await events(w, created.json()["id"])
    assert done.json()["result"] == {
        "subscription_id": sub,
        "status": "active",
        "version": 2,
        "event_id": event["id"],
    }
    assert event["action"] == "activated" and event["actor"] == me["user"]["id"]
    assert (event["from_status"], event["to_status"]) == ("pending", "active")
    assert event["payload"]["reason"] == "paid" and event["payload"]["version"] == 2

    current = await row(w, sub)
    assert current["status"] == "active" and current["version"] == 2
    assert current["cancelled_at"] is None and current["paused_at"] is None
    # started_at and activated_at are the transaction's now(), shared with the event.
    check = await w.q(
        "SELECT s.started_at = e.created_at AS started_now,"
        " s.activated_at = e.created_at AS activated_now,"
        " s.renewal_anchor = e.created_at AS anchored_now,"
        " s.renews_at = ((e.created_at AT TIME ZONE c.timezone) + interval '1 month')"
        "     AT TIME ZONE c.timezone AS one_interval_later,"
        " s.renews_at = subscription_next_renewal("
        "     s.renewal_anchor, s.renewal_anchor, 'month', c.timezone) AS on_cycle"
        " FROM subscriptions s JOIN subscription_events e ON e.subscription_id = s.id"
        " AND e.proposal_id = %s JOIN clients c ON c.id = s.client_id WHERE s.id = %s",
        created.json()["id"],
        sub,
    )
    assert check == [
        {
            "started_now": True,
            "activated_now": True,
            "anchored_now": True,
            "one_interval_later": True,
            "on_cycle": True,
        }
    ]


async def test_activate_uses_the_plans_billing_interval(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "annual", "299.00", billing_interval="year")
    sub = await pending_subscription(w, staff, plan="annual")

    created = await propose(w, staff, sub, {"action": "activate"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    check = await w.q(
        "SELECT s.renews_at = ((s.activated_at AT TIME ZONE c.timezone) + interval '1 year')"
        " AT TIME ZONE c.timezone AS ok FROM subscriptions s"
        " JOIN clients c ON c.id = s.client_id WHERE s.id = %s",
        sub,
    )
    assert check == [{"ok": True}]


async def test_activate_is_refused_unless_the_subscription_is_pending(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    for status in ("active", "paused", "cancelled"):
        sub = await subscribe(w, staff, status=status)
        refused = await propose(w, staff, sub, {"action": "activate"})
        assert refused.status_code == 422, (status, refused.text)
        assert refused.json()["detail"] == "action_not_allowed"
        assert (await row(w, sub))["status"] == status
    assert await proposal_rows(w) == []


async def test_cancel_now_cancels_a_pending_subscription(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await pending_subscription(w, staff)

    created = await propose(w, staff, sub, {"action": "cancel_now", "reason": "changed mind"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    [event] = await events(w, created.json()["id"])
    assert event["action"] == "cancelled"
    assert (event["from_status"], event["to_status"]) == ("pending", "cancelled")
    current = await row(w, sub)
    assert current["status"] == "cancelled" and current["version"] == 2
    assert current["cancelled_at"] is not None and current["activated_at"] is None
    assert current["renews_at"] is None and current["renewal_anchor"] is None


async def test_other_actions_are_refused_from_pending(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    sub = await pending_subscription(w, staff)
    for body in (
        {"action": "pause"},
        {"action": "resume"},
        {"action": "change_plan", "plan_code": "gold"},
        {"action": "cancel_at_period_end"},
        {"action": "extend_renewal", "renews_at": "2099-01-01T00:00:00Z"},
    ):
        refused = await propose(w, staff, sub, body)
        assert refused.status_code == 422, (body, refused.text)
        assert refused.json()["detail"] == "action_not_allowed"
    assert await proposal_rows(w) == []
    assert (await row(w, sub))["status"] == "pending"


async def test_two_actors_activating_at_once_start_it_once(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await pending_subscription(w, staff)
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    payload = {"subscription_id": sub, "action": "activate"}

    # Two different proposals, both bound to version 1: the row lock serialises them, and the
    # loser finds the subscription already moved on.
    results = await asyncio.gather(
        direct_execute(w, me, payload, first, "1"),
        direct_execute(w, me, payload, second, "1"),
        return_exceptions=True,
    )
    won = [r for r in results if isinstance(r, dict)]
    lost = [r for r in results if isinstance(r, BaseException)]
    assert len(won) == 1 and len(lost) == 1, results
    assert isinstance(lost[0], ProposalError) and lost[0].detail == "proposal_stale"
    assert won[0]["status"] == "active" and won[0]["version"] == 2
    assert len(await events(w, first)) + len(await events(w, second)) == 1
    current = await row(w, sub)
    assert current["status"] == "active" and current["version"] == 2

    # A late activation of an already active subscription at its current version is refused by
    # the state check, not applied twice.
    with pytest.raises(HTTPException) as refused:
        await direct_execute(w, me, payload, str(uuid.uuid4()), "2")
    assert refused.value.status_code == 422 and refused.value.detail == "action_not_allowed"
    assert (await row(w, sub))["version"] == 2


# --- invoices (payments) -------------------------------------------------------------------------


async def payments_on(w: SessionWorld) -> None:
    await w.q(
        "INSERT INTO payment_settings (client_id, enabled) VALUES (%s, true) "
        "ON CONFLICT (client_id) DO UPDATE SET enabled = true",
        w.tenants[0],
    )


async def issue_opening(w: SessionWorld, sub: str) -> invoices.InvoiceRecord:
    async with tx(w.pool, w.tenants[0]) as conn:
        issued = await invoices.issue_opening_invoice(
            conn, UUID(w.tenants[0]), UUID(sub), now=datetime.now(UTC)
        )
    assert issued is not None
    return issued


async def seed_invoice(
    w: SessionWorld, sub: str, period_start: datetime | None, status: str
) -> str:
    """An invoice for the period starting at `period_start`, written as the job would leave it."""
    invoice_id = new_invoice_id(datetime.now(UTC))
    await w.q(
        "INSERT INTO invoices (id, client_id, contact_id, subscription_id, reference, status, "
        "amount, currency, issued_at, due_at, period_start, period_end) "
        "SELECT %s, client_id, contact_id, id, %s, %s, 29.90, 'MYR', now(), %s, %s, "
        "%s::timestamptz + interval '1 month' FROM subscriptions WHERE id = %s",
        invoice_id,
        new_reference(),
        status,
        period_start,
        period_start,
        period_start,
        sub,
    )
    return invoice_id


async def invoice_row(w: SessionWorld, invoice_id: str) -> dict[str, Any]:
    return (await w.q("SELECT * FROM invoices WHERE id=%s", invoice_id))[0]


async def open_invoices(w: SessionWorld, sub: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id, period_start FROM invoices WHERE subscription_id=%s "
        "AND status IN ('issued','overdue','proof_submitted','cash_pending')",
        sub,
    )


async def test_cancel_now_voids_the_opening_invoice_of_a_pending_subscription(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await payments_on(w)
    sub = await pending_subscription(w, staff)
    opening = await issue_opening(w, sub)

    created = await propose(w, staff, sub, {"action": "cancel_now", "reason": "changed mind"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200

    assert (await row(w, sub))["status"] == "cancelled"
    voided = await invoice_row(w, opening.id)
    assert voided["status"] == "void" and voided["void_reason"] == "subscription_cancelled"
    assert voided["voided_at"] is not None and voided["voided_by"] is None
    assert await open_invoices(w, sub) == []


async def test_cancel_now_voids_the_next_period_and_keeps_the_running_one(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    now = datetime.now(UTC)
    running = await seed_invoice(w, sub, now - timedelta(days=10), "overdue")
    upcoming = await seed_invoice(w, sub, now + timedelta(days=20), "proof_submitted")

    created = await propose(w, staff, sub, {"action": "cancel_now"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200

    assert (await row(w, sub))["status"] == "cancelled"
    assert (await invoice_row(w, upcoming))["status"] == "void"
    assert (await invoice_row(w, upcoming))["void_reason"] == "subscription_cancelled"
    # The period that already ran is still owed: its invoice stays open for staff to settle.
    assert [i["id"] for i in await open_invoices(w, sub)] == [running]


async def test_extend_renewal_voids_the_next_period_invoices_and_the_job_reissues_one(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await payments_on(w)
    sub = await subscribe(w, staff)
    now = datetime.now(UTC)
    old_renewal = now + timedelta(days=3)
    await w.q("UPDATE subscriptions SET renews_at = %s WHERE id=%s", old_renewal, sub)
    async with tx(w.pool, w.tenants[0]) as conn:
        issued = await invoices.issue_period_invoice(conn, UUID(w.tenants[0]), UUID(sub), now=now)
    assert issued is not None and issued.period_start == old_renewal
    running = await seed_invoice(w, sub, now - timedelta(days=27), "overdue")
    # A customer already said they paid a later period's invoice: it is voided too (staff see
    # the claim on the void invoice and settle it by hand).
    claimed = await seed_invoice(w, sub, now + timedelta(days=40), "proof_submitted")
    # An invoice with no period (an opening one) is never touched by an extension.
    opening = await seed_invoice(w, sub, None, "issued")

    new_renewal = now + timedelta(days=5)
    created = await propose(
        w, staff, sub, {"action": "extend_renewal", "renews_at": new_renewal.isoformat()}
    )
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200

    for voided_id in (issued.id, claimed):
        voided = await invoice_row(w, voided_id)
        assert voided["status"] == "void" and voided["void_reason"] == "subscription_changed"
        assert voided["voided_at"] is not None and voided["voided_by"] is None
    assert (await invoice_row(w, running))["status"] == "overdue"
    assert (await invoice_row(w, opening))["status"] == "issued"

    # The invoice job issues the new period's invoice: one open invoice per future period.
    async with tx(w.pool, w.tenants[0]) as conn:
        reissued = await invoices.issue_period_invoice(
            conn, UUID(w.tenants[0]), UUID(sub), now=datetime.now(UTC)
        )
    assert reissued is not None and reissued.id != issued.id
    future = [
        i for i in await open_invoices(w, sub) if i["period_start"] and i["period_start"] > now
    ]
    assert [(i["id"], i["period_start"]) for i in future] == [
        (reissued.id, (await row(w, sub))["renews_at"])
    ]


async def test_extend_after_a_missed_renewal_voids_the_stale_period_invoice(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await payments_on(w)
    sub = await subscribe(w, staff)
    now = datetime.now(UTC)
    # The renewal date has passed and the invoice job has not renewed yet: the next period's
    # invoice starts at that date, before now.
    old_renewal = now - timedelta(minutes=2)
    await w.q("UPDATE subscriptions SET renews_at = %s WHERE id=%s", old_renewal, sub)
    earlier = await seed_invoice(w, sub, old_renewal - timedelta(days=30), "overdue")
    stale = await seed_invoice(w, sub, old_renewal, "issued")

    new_renewal = now + timedelta(days=7)
    created = await propose(
        w, staff, sub, {"action": "extend_renewal", "renews_at": new_renewal.isoformat()}
    )
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200

    voided = await invoice_row(w, stale)
    assert voided["status"] == "void" and voided["void_reason"] == "subscription_changed"
    assert (await invoice_row(w, earlier))["status"] == "overdue"
    assert [i["id"] for i in await open_invoices(w, sub)] == [earlier]


async def test_pause_does_not_void_the_next_period_invoice(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub = await subscribe(w, staff)
    upcoming = await seed_invoice(w, sub, datetime.now(UTC) + timedelta(days=3), "issued")
    resume = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
    created = await propose(w, staff, sub, {"action": "pause", "resume_at": resume})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200
    assert (await invoice_row(w, upcoming))["status"] == "issued"


async def test_start_gives_the_open_opening_invoice_its_period_and_it_stays_payable(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await payments_on(w)
    sub = await pending_subscription(w, staff)
    opening = await issue_opening(w, sub)

    created = await propose(w, staff, sub, {"action": "activate", "reason": "paid in cash"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200

    started = await row(w, sub)
    assert started["status"] == "active" and started["version"] == 2
    invoice = await invoice_row(w, opening.id)
    assert invoice["status"] == "issued"
    assert invoice["period_start"] == started["started_at"]
    assert invoice["period_end"] == started["renews_at"]

    # Paying it later records the payment and changes nothing on the running subscription.
    async with tx(w.pool, w.tenants[0]) as conn:
        outcome = await invoices.mark_paid(
            conn,
            UUID(w.tenants[0]),
            opening.id,
            actor_user_id=UUID(me["user"]["id"]),
            request_key=uuid.uuid4(),
            method_kind="transfer",
            received_at=datetime.now(UTC) - timedelta(minutes=5),
            note=None,
        )
    assert outcome.invoice.status == "paid"
    assert (outcome.activated, outcome.resumed, outcome.recorded_only) == (False, False, False)
    assert await row(w, sub) == started


async def test_plan_price_change_after_issue_keeps_the_invoice_amount(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "gold", "59.90")
    await payments_on(w)
    sub = await subscribe(w, staff)
    await w.q("UPDATE subscriptions SET renews_at = now() + interval '3 days' WHERE id=%s", sub)
    async with tx(w.pool, w.tenants[0]) as conn:
        issued = await invoices.issue_period_invoice(
            conn, UUID(w.tenants[0]), UUID(sub), now=datetime.now(UTC)
        )
    assert issued is not None and str(issued.amount) == "29.90"

    created = await propose(w, staff, sub, {"action": "change_plan", "plan_code": "gold"})
    assert created.status_code == 201, created.text
    assert (await confirm(w, staff, created.json()["id"])).status_code == 200

    assert str((await row(w, sub))["price"]) == "59.90"
    kept = await invoice_row(w, issued.id)
    assert str(kept["amount"]) == "29.90" and kept["status"] == "issued"
