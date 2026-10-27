"""Subscriptions tab: request queue, approve-and-confirm, decline (lane B1, task 4)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
from csa.subscriptions import executor as executor_module
from csa.subscriptions import service
from csa.subscriptions.api import build_subscriptions_router
from csa.subscriptions.executor import SubscriptionActionExecutor
from csa.workspace.executors import build_proposal_registry
from fastapi import HTTPException

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_console_subscriptions import seed_customer, seed_plan

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/subscriptions"


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
    tenant: str | None = None,
    name: str = "Aisha Rahman",
) -> tuple[str, str]:
    """Create a subscription through the API; return (subscription id, contact id)."""
    contact = await seed_customer(
        w, tenant or w.tenants[0], name=name, email=f"synthetic-{uuid.uuid4()}@example.test"
    )
    created = await w.mutate(
        browser,
        f"{BASE}?client_id={tenant or w.tenants[0]}",
        {"contact_id": contact, "plan_code": "basic"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"], contact


async def seed_request(
    w: SessionWorld,
    contact: str,
    action: str = "request_pause",
    *,
    subscription_id: str | None,
    tenant: str | None = None,
    plan_code: str | None = None,
    reason: str | None = "holiday",
    agent: str = "subscription_agent",
) -> str:
    payload: dict[str, Any] = {"action": action}
    for key, value in (
        ("subscription_id", subscription_id),
        ("plan_code", plan_code),
        ("reason", reason),
    ):
        if value is not None:
            payload[key] = value
    rows = await w.q(
        "INSERT INTO agent_preparation_requests (client_id, contact_id, agent, action, "
        "idempotency_key, payload, trace_id, requested_by) "
        "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s) RETURNING id",
        tenant or w.tenants[0],
        contact,
        agent,
        action,
        f"synthetic-{uuid.uuid4()}",
        json.dumps(payload),
        str(uuid.uuid4()),
        contact,
    )
    return str(rows[0]["id"])


async def decide(
    w: SessionWorld, browser: httpx.AsyncClient, request: str, body: dict[str, Any]
) -> httpx.Response:
    return await w.mutate(browser, f"{BASE}/requests/{request}/decide", body)


async def decisions(w: SessionWorld) -> list[dict[str, Any]]:
    return await w.q("SELECT * FROM subscription_request_decisions ORDER BY decided_at")


async def sub_row(w: SessionWorld, sub: str) -> dict[str, Any]:
    return (await w.q("SELECT * FROM subscriptions WHERE id=%s", sub))[0]


async def audit_actions(w: SessionWorld) -> list[str]:
    rows = await w.q(
        "SELECT action FROM console_audit WHERE action LIKE 'subscriptions.request.%%' ORDER BY id"
    )
    return [r["action"] for r in rows]


async def test_open_queue_lists_only_undecided_requests_with_customer(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    reader, _ = await w.member(role="read_only")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    pause = await seed_request(w, contact, "request_pause", subscription_id=sub)
    change = await seed_request(
        w, contact, "request_change", subscription_id=sub, plan_code="pro", reason=None
    )
    declined = await seed_request(w, contact, "request_cancel", subscription_id=sub)
    # Not subscription requests: another agent's row never reaches this queue.
    await seed_request(w, contact, "request_book", subscription_id=None, agent="booking_agent")
    assert (
        await decide(w, staff, declined, {"decision": "decline", "note": "no"})
    ).status_code == 200

    # `/requests` is not swallowed by the `/{subscription_id}` UUID route.
    listed = await reader.get(f"{BASE}/requests")
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page["next_cursor"] is None
    assert {item["id"] for item in page["items"]} == {pause, change}
    by_id = {item["id"]: item for item in page["items"]}
    assert by_id[pause]["action"] == "request_pause" and by_id[pause]["reason"] == "holiday"
    assert by_id[pause]["subscription_id"] == sub and by_id[pause]["plan_code"] is None
    assert by_id[pause]["customer"] == {
        "id": contact,
        "full_name": "Aisha Rahman",
        "phone": "+60 12-345 6789",
        "email": by_id[pause]["customer"]["email"],
    }
    assert by_id[pause]["decision"] is None and by_id[pause]["proposal_id"] is None
    assert by_id[change]["plan_code"] == "pro" and by_id[change]["reason"] is None

    done = (await staff.get(f"{BASE}/requests?status=decided")).json()
    assert [item["id"] for item in done["items"]] == [declined]
    assert done["items"][0]["decision"] == "declined" and done["items"][0]["note"] == "no"
    assert done["items"][0]["decided_by"] == me["user"]["id"]

    first = (await staff.get(f"{BASE}/requests?limit=1")).json()
    assert len(first["items"]) == 1 and first["next_cursor"] == first["items"][0]["id"]
    second = (await staff.get(f"{BASE}/requests?limit=1&before={first['next_cursor']}")).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert second["items"][0]["id"] != first["items"][0]["id"]


async def test_approve_pause_creates_and_confirms_proposal_and_records_decision(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)

    approved = await decide(
        w, staff, request, {"decision": "approve", "note": "ok", "resume_at": "2026-12-01"}
    )
    assert approved.status_code == 200, approved.text
    out = approved.json()
    assert out["id"] == request and out["decision"] == "approved" and out["note"] == "ok"
    assert out["decided_by"] == me["user"]["id"] and out["decided_at"] is not None

    current = await sub_row(w, sub)
    assert current["status"] == "paused" and str(current["resume_at"]) == "2026-12-01"
    rows = await decisions(w)
    assert len(rows) == 1 and str(rows[0]["request_id"]) == request
    assert rows[0]["decision"] == "approved" and str(rows[0]["proposal_id"]) == out["proposal_id"]
    proposal = (
        await w.q("SELECT status, origin FROM console_proposals WHERE id=%s", out["proposal_id"])
    )[0]
    assert proposal == {"status": "executed", "origin": "tab"}
    events = await w.q(
        "SELECT action, to_status FROM subscription_events WHERE proposal_id=%s", out["proposal_id"]
    )
    assert events == [{"action": "paused", "to_status": "paused"}]
    assert await audit_actions(w) == ["subscriptions.request.approved"]


async def test_approve_cancel_and_change_map_to_their_actions(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "pro", "59.90")
    sub, contact = await subscribe(w, staff)
    sub2, contact2 = await subscribe(w, staff, name="Bala Raj")
    cancel = await seed_request(w, contact, "request_cancel", subscription_id=sub)
    change = await seed_request(
        w, contact2, "request_change", subscription_id=sub2, plan_code="pro"
    )

    assert (await decide(w, staff, cancel, {"decision": "approve"})).status_code == 200
    scheduled = await sub_row(w, sub)
    assert scheduled["cancel_at_period_end"] is True and scheduled["status"] == "active"

    assert (await decide(w, staff, change, {"decision": "approve"})).status_code == 200
    changed = await sub_row(w, sub2)
    assert changed["plan"] == "pro" and changed["pending_plan"] is None
    assert str(changed["price"]) == "59.90"


async def test_approve_change_to_archived_plan_is_refused_without_decision(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "old", "9.90", is_active=False)
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_change", subscription_id=sub, plan_code="old")

    refused = await decide(w, staff, request, {"decision": "approve"})
    assert refused.status_code == 422 and refused.json()["detail"] == "plan_unavailable"
    assert await decisions(w) == []
    assert (await sub_row(w, sub))["plan"] == "basic"
    assert await audit_actions(w) == []
    still_open = (await staff.get(f"{BASE}/requests")).json()
    assert [item["id"] for item in still_open["items"]] == [request]

    # An illegal transition (pausing a cancelled subscription) is refused the same way.
    await w.q("UPDATE subscriptions SET status='cancelled', cancelled_at=now() WHERE id=%s", sub)
    pause = await seed_request(w, contact, "request_pause", subscription_id=sub)
    refused = await decide(w, staff, pause, {"decision": "approve"})
    assert refused.status_code == 422 and refused.json()["detail"] == "action_not_allowed"
    assert await decisions(w) == []


async def test_approve_refuses_requests_without_a_usable_subscription(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    other = await seed_customer(w, w.tenants[0], name="Other", email="other@example.test")
    missing = await seed_request(w, contact, "request_pause", subscription_id=None)
    foreign = await seed_request(w, other, "request_pause", subscription_id=sub)
    unknown = await seed_request(w, contact, "request_pause", subscription_id=str(uuid.uuid4()))
    no_plan = await seed_request(w, contact, "request_change", subscription_id=sub)

    for request in (missing, foreign, unknown, no_plan):
        refused = await decide(w, staff, request, {"decision": "approve"})
        assert refused.status_code == 422, refused.text
    assert await decisions(w) == []
    assert (await sub_row(w, sub))["status"] == "active"
    assert await w.q("SELECT id FROM console_proposals WHERE kind='subscription_action'") == []
    # A request that names no subscription can still be declined to clear the queue.
    cleared = await decide(w, staff, missing, {"decision": "decline"})
    assert cleared.status_code == 200 and cleared.json()["subscription_id"] is None


async def test_decline_records_note_and_leaves_subscription(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_cancel", subscription_id=sub)

    declined = await decide(w, staff, request, {"decision": "decline", "note": "within contract"})
    assert declined.status_code == 200, declined.text
    out = declined.json()
    assert out["decision"] == "declined" and out["note"] == "within contract"
    assert out["proposal_id"] is None and out["decided_by"] == me["user"]["id"]
    current = await sub_row(w, sub)
    assert current["status"] == "active" and current["cancel_at_period_end"] is False
    assert current["version"] == 1
    rows = await decisions(w)
    assert len(rows) == 1 and rows[0]["proposal_id"] is None
    assert await w.q("SELECT id FROM console_proposals WHERE kind='subscription_action'") == []
    assert await audit_actions(w) == ["subscriptions.request.declined"]


async def test_decide_twice_is_409_and_reader_is_403(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    reader, _ = await w.member(role="read_only")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)

    assert (await decide(w, reader, request, {"decision": "decline"})).status_code == 403
    assert await decisions(w) == []
    assert (await decide(w, staff, request, {"decision": "decline"})).status_code == 200
    again = await decide(w, staff, request, {"decision": "approve"})
    assert again.status_code == 409 and again.json()["detail"] == "request_decided"
    assert (await sub_row(w, sub))["status"] == "active"
    assert len(await decisions(w)) == 1


async def paused_events(w: SessionWorld, sub: str) -> int:
    rows = await w.q(
        "SELECT id FROM subscription_events WHERE subscription_id=%s AND action='paused'", sub
    )
    return len(rows)


async def executed_proposals(w: SessionWorld) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id, user_id FROM console_proposals WHERE kind='subscription_action' "
        "AND status='executed' AND client_id=%s",
        w.tenants[0],
    )


async def test_concurrent_approvals_by_two_users_decide_and_execute_once(
    sessions: SessionWorld,
) -> None:
    w = sessions
    first, _ = await w.member(role="tenant_staff")
    second, _ = await w.member(role="tenant_admin")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, first)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)

    results = await asyncio.gather(
        decide(w, first, request, {"decision": "approve"}),
        decide(w, second, request, {"decision": "approve"}),
        decide(w, first, request, {"decision": "approve"}),
        decide(w, second, request, {"decision": "approve"}),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409], [r.text for r in results]
    assert {r.json()["detail"] for r in results if r.status_code == 409} == {"request_decided"}
    winner = next(r for r in results if r.status_code == 200).json()
    rows = await decisions(w)
    assert len(rows) == 1 and str(rows[0]["proposal_id"]) == winner["proposal_id"]
    assert [str(p["id"]) for p in await executed_proposals(w)] == [winner["proposal_id"]]
    assert await paused_events(w, sub) == 1
    assert (await sub_row(w, sub))["version"] == 2
    assert await audit_actions(w) == ["subscriptions.request.approved"]


async def test_approve_after_a_crash_following_confirm_is_409_and_shows_approved(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated crash after confirm")

    monkeypatch.setattr(service, "request_out", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await decide(w, staff, request, {"decision": "approve"})
    monkeypatch.undo()

    # The action and the decision committed together, so there is no half-finished state.
    assert (await sub_row(w, sub))["status"] == "paused"
    assert len(await decisions(w)) == 1 and len(await executed_proposals(w)) == 1
    again = await decide(w, staff, request, {"decision": "approve"})
    assert again.status_code == 409 and again.json()["detail"] == "request_decided"
    done = (await staff.get(f"{BASE}/requests?status=decided")).json()["items"]
    assert [(i["id"], i["decision"]) for i in done] == [(request, "approved")]
    assert done[0]["proposal_id"] == str((await executed_proposals(w))[0]["id"])
    assert await paused_events(w, sub) == 1
    assert await audit_actions(w) == ["subscriptions.request.approved"]


async def test_approve_after_decline_is_409_and_executes_nothing(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)

    assert (await decide(w, staff, request, {"decision": "decline"})).status_code == 200
    late = await decide(w, staff, request, {"decision": "approve"})
    assert late.status_code == 409 and late.json()["detail"] == "request_decided"
    assert (await sub_row(w, sub))["status"] == "active"
    assert await w.q("SELECT id FROM console_proposals WHERE kind='subscription_action'") == []
    assert [d["decision"] for d in await decisions(w)] == ["declined"]


async def test_decline_between_create_and_confirm_stops_the_approval(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    other, _ = await w.member(role="tenant_admin")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)
    registry = w.app.state.proposal_registry
    real = registry.confirm

    async def decline_first(*args: Any, **kwargs: Any) -> Any:
        assert (await decide(w, other, request, {"decision": "decline"})).status_code == 200
        return await real(*args, **kwargs)

    monkeypatch.setattr(registry, "confirm", decline_first)
    lost = await decide(w, staff, request, {"decision": "approve"})
    assert lost.status_code == 409 and lost.json()["detail"] == "request_decided"
    assert (await sub_row(w, sub))["status"] == "active"
    assert await paused_events(w, sub) == 0 and await executed_proposals(w) == []
    assert [d["decision"] for d in await decisions(w)] == ["declined"]
    assert await audit_actions(w) == ["subscriptions.request.declined"]


async def test_decline_committed_before_execute_runs_is_refused_by_its_request_check(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    other, _ = await w.member(role="tenant_admin")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)
    real = SubscriptionActionExecutor.execute

    async def decline_first(self: Any, *args: Any, **kwargs: Any) -> Any:
        assert (await decide(w, other, request, {"decision": "decline"})).status_code == 200
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(SubscriptionActionExecutor, "execute", decline_first)
    lost = await decide(w, staff, request, {"decision": "approve"})
    assert lost.status_code == 409 and lost.json()["detail"] == "request_decided"
    assert (await sub_row(w, sub))["status"] == "active"
    assert await paused_events(w, sub) == 0 and await executed_proposals(w) == []
    assert [d["decision"] for d in await decisions(w)] == ["declined"]


async def test_decline_after_execute_checked_the_request_hits_the_insert_conflict(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    other, _ = await w.member(role="tenant_admin")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)
    real = executor_module._check_request
    calls: list[str] = []

    async def check_then_decline(*args: Any, **kwargs: Any) -> None:
        await real(*args, **kwargs)
        calls.append("check")
        # card at create, card at confirm, then execute's own check: decline right after it.
        if len(calls) == 3:
            assert (await decide(w, other, request, {"decision": "decline"})).status_code == 200

    monkeypatch.setattr(executor_module, "_check_request", check_then_decline)
    lost = await decide(w, staff, request, {"decision": "approve"})
    assert len(calls) == 3
    assert lost.status_code == 409 and lost.json()["detail"] == "request_decided"
    assert (await sub_row(w, sub))["status"] == "active"
    assert await paused_events(w, sub) == 0 and await executed_proposals(w) == []
    assert [d["decision"] for d in await decisions(w)] == ["declined"]
    assert await audit_actions(w) == ["subscriptions.request.declined"]


async def test_workspace_proposals_route_cannot_approve_a_request_with_another_action(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[0], "pro", "59.90")
    await seed_plan(w, w.tenants[0], "max", "99.90")
    sub, contact = await subscribe(w, staff)
    pause = await seed_request(w, contact, "request_pause", subscription_id=sub)
    change = await seed_request(
        w, contact, "request_change", subscription_id=sub, plan_code="pro", reason=None
    )
    other_sub, _ = await subscribe(w, staff, name="Bala Raj")
    proposals = "/console/v1/workspace/proposals"
    mismatches: list[tuple[str, dict[str, Any]]] = [
        (pause, {"action": "cancel_now"}),
        (pause, {"action": "cancel_at_period_end"}),
        (change, {"action": "change_plan", "plan_code": "max"}),
        (
            change,
            {
                "action": "change_plan",
                "plan_code": "pro",
                "price_override": "1.00",
                "reason": "discount",
            },
        ),
        (
            change,
            {
                "action": "change_plan",
                "plan_code": "pro",
                "effective_at": "2099-01-01T00:00:00+00:00",
            },
        ),
    ]
    for request, extra in mismatches:
        payload = {"subscription_id": sub, "request_id": request, **extra}
        refused = await w.mutate(
            staff, proposals, {"kind": "subscription_action", "payload": payload}
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"] == "request_action_mismatch"
    # A request naming another subscription is refused too, and a decided one is final.
    crossed = {"subscription_id": other_sub, "request_id": pause, "action": "pause"}
    refused = await w.mutate(staff, proposals, {"kind": "subscription_action", "payload": crossed})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "request_subscription_mismatch"
    assert await w.q("SELECT id FROM console_proposals WHERE kind='subscription_action'") == []
    assert await decisions(w) == [] and (await sub_row(w, sub))["status"] == "active"

    # The matching action works end to end through the same route.
    payload = {
        "subscription_id": sub,
        "request_id": pause,
        "action": "pause",
        "decision_note": "via workspace",
    }
    created = await w.mutate(staff, proposals, {"kind": "subscription_action", "payload": payload})
    assert created.status_code == 201, created.text
    done = await w.mutate(staff, f"{proposals}/{created.json()['id']}/confirm")
    assert done.status_code == 200, done.text
    assert (await sub_row(w, sub))["status"] == "paused"
    [decision] = await decisions(w)
    assert str(decision["request_id"]) == pause and decision["decision"] == "approved"
    assert str(decision["proposal_id"]) == created.json()["id"]
    assert decision["note"] == "via workspace"
    assert await audit_actions(w) == ["subscriptions.request.approved"]


async def test_another_user_can_approve_after_the_first_attempt_failed(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    first, _ = await w.member(role="tenant_staff")
    second, me2 = await w.member(role="tenant_admin")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, first)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)
    registry = w.app.state.proposal_registry
    real = registry.confirm

    async def refuse(*args: Any, **kwargs: Any) -> Any:
        raise HTTPException(422, "action_not_allowed")

    monkeypatch.setattr(registry, "confirm", refuse)
    assert (await decide(w, first, request, {"decision": "approve"})).status_code == 422
    left = await w.q("SELECT id, status FROM console_proposals WHERE kind='subscription_action'")
    assert [p["status"] for p in left] == ["requested"]
    await w.q("UPDATE console_proposals SET status='expired' WHERE id=%s", left[0]["id"])

    monkeypatch.setattr(registry, "confirm", real)
    approved = await decide(w, second, request, {"decision": "approve", "note": "second try"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["decided_by"] == me2["user"]["id"]
    assert approved.json()["note"] == "second try"
    assert (await sub_row(w, sub))["status"] == "paused"
    assert len(await decisions(w)) == 1 and await paused_events(w, sub) == 1


async def test_public_actions_route_rejects_internal_request_keys(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    request = await seed_request(w, contact, "request_pause", subscription_id=sub)
    for extra in ({"request_id": request}, {"decision_note": "x"}):
        body = {"action": "pause", **extra}
        refused = await w.mutate(staff, f"{BASE}/{sub}/actions", body)
        assert refused.status_code == 422, refused.text
    assert await w.q("SELECT id FROM console_proposals WHERE kind='subscription_action'") == []
    assert await decisions(w) == []


async def test_requests_are_tenant_scoped(sessions: SessionWorld) -> None:
    w = sessions
    admin_a, _ = await w.member(role="tenant_admin")
    admin_b, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    await seed_plan(w, w.tenants[1], "basic", "29.90")
    sub_a, contact_a = await subscribe(w, admin_a)
    sub_b, contact_b = await subscribe(w, admin_b, tenant=w.tenants[1])
    request_a = await seed_request(w, contact_a, "request_pause", subscription_id=sub_a)
    request_b = await seed_request(
        w, contact_b, "request_pause", subscription_id=sub_b, tenant=w.tenants[1]
    )

    seen_a = (await admin_a.get(f"{BASE}/requests")).json()
    assert [item["id"] for item in seen_a["items"]] == [request_a]
    seen_b = (await admin_b.get(f"{BASE}/requests")).json()
    assert [item["id"] for item in seen_b["items"]] == [request_b]
    assert (await admin_a.get(f"{BASE}/requests?client_id={w.tenants[1]}")).status_code == 403

    # Tenant A names tenant B's request id: RLS hides it, nothing is written.
    for body in ({"decision": "decline"}, {"decision": "approve"}):
        hidden = await decide(w, admin_a, request_b, body)
        assert hidden.status_code == 404 and hidden.json()["detail"] == "request_not_found"
    assert await decisions(w) == []
    assert (await sub_row(w, sub_b))["status"] == "active"
    assert (await decide(w, admin_b, request_b, {"decision": "decline"})).status_code == 200
    rows = await decisions(w)
    assert [str(r["client_id"]) for r in rows] == [w.tenants[1]]


async def test_approving_a_cancel_without_a_renewal_date_is_refused_without_decision(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, contact = await subscribe(w, staff)
    await w.q("UPDATE subscriptions SET renews_at = NULL WHERE id=%s", sub)
    request = await seed_request(w, contact, "request_cancel", subscription_id=sub)

    refused = await decide(w, staff, request, {"decision": "approve"})
    assert refused.status_code == 422 and refused.json()["detail"] == "action_not_allowed"
    assert await decisions(w) == []
    assert (await sub_row(w, sub))["cancel_at_period_end"] is False
    assert await audit_actions(w) == []
    still_open = (await staff.get(f"{BASE}/requests")).json()
    assert [item["id"] for item in still_open["items"]] == [request]


async def test_workspace_route_refuses_a_request_from_another_customer(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    await seed_plan(w, w.tenants[0], "basic", "29.90")
    sub, _ = await subscribe(w, staff)
    other = await seed_customer(w, w.tenants[0], name="Other", email="other@example.test")
    # Another customer's request that names this customer's subscription.
    foreign = await seed_request(w, other, "request_pause", subscription_id=sub)

    payload = {"subscription_id": sub, "request_id": foreign, "action": "pause"}
    refused = await w.mutate(
        staff,
        "/console/v1/workspace/proposals",
        {"kind": "subscription_action", "payload": payload},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "request_subscription_mismatch"
    assert await w.q("SELECT id FROM console_proposals WHERE kind='subscription_action'") == []
    assert await decisions(w) == [] and (await sub_row(w, sub))["status"] == "active"
