"""The inbox queue actions (plan 1a, task 8): one route per action on one conversation and the bulk
route (design 2026-11-16, sections 4, 5.2 and 6.6). Disposable database, synthetic data only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from csa.inbox_queue import transitions

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_inbox_queue_api import _item, _named

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

ACTIONS = ("claim", "unclaim", "assign", "snooze", "resolve", "priority", "topic")
EVENT_NAMES = {
    "claim": "claimed",
    "unclaim": "unclaimed",
    "assign": "assigned",
    "snooze": "snoozed",
    "resolve": "resolved",
    "priority": "priority_set",
    "topic": "topic_set",
}
_STATE = (
    "SELECT queue_status, queue_reason, topic, priority, assignee_user_id, claimed_at,"
    " needs_staff_since, due_at, snoozed_until, resolved_at, last_customer_at,"
    " last_staff_reply_at, inbox_version, owned_by, status FROM conversations WHERE id = %s"
)


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "customer_support")


def queue_path(w: SessionWorld, conversation: str, action: str, tenant: int = 0) -> str:
    query = f"client_id={w.tenants[tenant]}"
    return f"/console/v1/inbox/conversations/{conversation}/queue/{action}?{query}"


def bulk_path(w: SessionWorld, tenant: int = 0) -> str:
    return f"/console/v1/inbox/bulk?client_id={w.tenants[tenant]}"


async def state(w: SessionWorld, conversation: str) -> dict[str, Any]:
    [row] = await w.q(_STATE, UUID(conversation))
    return row


async def events(w: SessionWorld, conversation: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT action, from_status, to_status, actor_user_id, details FROM inbox_queue_events"
        " WHERE conversation_id = %s ORDER BY id",
        UUID(conversation),
    )


async def snapshot(w: SessionWorld, conversation: str) -> tuple[Any, ...]:
    return (await state(w, conversation), await events(w, conversation))


def soon(**delta: float) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


async def act(
    w: SessionWorld,
    client: httpx.AsyncClient,
    conversation: str,
    action: str,
    *,
    key: str | None = None,
    version: int | None = None,
    tenant: int = 0,
    **extra: Any,
) -> httpx.Response:
    if version is None:
        rows = await w.q(
            "SELECT inbox_version FROM conversations WHERE id = %s", UUID(conversation)
        )
        version = rows[0]["inbox_version"] if rows else 0
    return await w.mutate(
        client,
        queue_path(w, conversation, action, tenant),
        {"request_key": key or str(uuid4()), "expected_version": version, **extra},
    )


async def pending_approval(w: SessionWorld, conversation: str, tenant: int = 0) -> None:
    await w.q(
        "INSERT INTO approvals (client_id, conversation_id, trace_id, draft, reason, status,"
        " expires_at) VALUES (%s, %s, %s, 'synthetic draft', 'synthetic', 'pending',"
        " now() + interval '1 hour')",
        UUID(w.tenants[tenant]),
        UUID(conversation),
        uuid4(),
    )


async def customer_wrote_now(w: SessionWorld, conversation: str) -> datetime:
    [row] = await w.q(
        "UPDATE conversations SET last_customer_at = now() WHERE id = %s"
        " RETURNING last_customer_at",
        UUID(conversation),
    )
    return row["last_customer_at"]


async def extras(w: SessionWorld, action: str) -> dict[str, Any]:
    if action == "assign":
        return {"assignee_id": await _named(w, "Ana Synthetic")}
    if action == "snooze":
        return {"until": soon(hours=2)}
    if action == "priority":
        return {"priority": "urgent"}
    if action == "topic":
        return {"topic": "complaint"}
    return {}


def raw_client(w: SessionWorld, signed_in: httpx.AsyncClient) -> httpx.AsyncClient:
    """The same signed-in browser, but an unhandled error comes back as the 500 a browser sees."""
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app, raise_app_exceptions=False),
        base_url=signed_in.base_url,
        cookies=signed_in.cookies,
    )
    w.browsers.append(client)
    return client


# --- one conversation ----------------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
async def test_each_action_changes_the_item_once_and_replays(
    sessions: SessionWorld, action: str
) -> None:
    w = sessions
    item = await _item(w, state="needs_staff", assignee=w.admin_id if action == "unclaim" else None)
    before = await state(w, item.id)
    body = await extras(w, action)
    key = str(uuid4())
    response = await act(w, w.admin, item.id, action, key=key, **body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == "applied"
    after = await state(w, item.id)
    assert after["inbox_version"] == before["inbox_version"] + 1
    assert result["conversation"]["inbox_version"] == after["inbox_version"]
    assert result["conversation"]["id"] == item.id
    expected = {
        "claim": lambda s: str(s["assignee_user_id"]) == w.admin_id and s["claimed_at"],
        "unclaim": lambda s: s["assignee_user_id"] is None and s["claimed_at"] is None,
        "assign": lambda s: str(s["assignee_user_id"]) == body.get("assignee_id"),
        "snooze": lambda s: (
            s["queue_status"] == "snoozed"
            and s["snoozed_until"] == datetime.fromisoformat(body["until"])
        ),
        "resolve": lambda s: s["queue_status"] == "resolved" and s["resolved_at"] is not None,
        "priority": lambda s: s["priority"] == "urgent",
        "topic": lambda s: s["topic"] == "complaint",
    }[action]
    assert expected(after), after
    [event] = await events(w, item.id)
    assert event["action"] == EVENT_NAMES[action]
    assert str(event["actor_user_id"]) == w.admin_id
    [audited] = await w.q(
        "SELECT action, target_id FROM console_audit WHERE client_id = %s AND action LIKE %s",
        UUID(w.tenants[0]),
        "inbox.queue_%",
    )
    assert (audited["action"], audited["target_id"]) == (f"inbox.queue_{action}", item.id)
    # The same key and body replays the receipt: no second change, no second event.
    again = await act(w, w.admin, item.id, action, key=key, version=before["inbox_version"], **body)
    assert again.status_code == 200, again.text
    assert again.json() == result
    assert await state(w, item.id) == after
    assert len(await events(w, item.id)) == 1
    # The same key with another body is refused.
    other = await act(
        w, w.admin, item.id, action, key=key, version=before["inbox_version"] + 7, **body
    )
    assert other.status_code == 409
    assert other.json()["detail"] == "request key was used with different content"
    assert await state(w, item.id) == after


async def test_calls_that_change_nothing_are_already_handled(sessions: SessionWorld) -> None:
    w = sessions
    staff = await _named(w, "Bea Synthetic")
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        ("claim", {"state": "needs_staff", "assignee": w.admin_id}, {}),
        ("unclaim", {"state": "needs_staff"}, {}),
        ("assign", {"state": "needs_staff", "assignee": staff}, {"assignee_id": staff}),
        ("resolve", {"state": "resolved"}, {}),
        ("priority", {"state": "needs_staff", "priority": "high"}, {"priority": "high"}),
        ("topic", {"state": "needs_staff", "topic": "booking"}, {"topic": "booking"}),
    ]
    for action, seeded, body in cases:
        item = await _item(w, **seeded)
        before = await snapshot(w, item.id)
        response = await act(w, w.admin, item.id, action, **body)
        assert response.status_code == 200, (action, response.text)
        assert response.json()["outcome"] == "already_handled", action
        assert await snapshot(w, item.id) == before, action
        assert response.json()["conversation"]["inbox_version"] == before[0]["inbox_version"]


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_stale_version_is_409_and_writes_nothing(
    sessions: SessionWorld, action: str
) -> None:
    w = sessions
    item = await _item(w, state="needs_staff", assignee=w.admin_id)
    await w.q("UPDATE conversations SET inbox_version = 4 WHERE id = %s", UUID(item.id))
    before = await snapshot(w, item.id)
    response = await act(w, w.admin, item.id, action, version=3, **await extras(w, action))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "conversation changed; refresh before acting"
    assert await snapshot(w, item.id) == before


async def test_assign_needs_an_administrator_and_an_enabled_member(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    staff_client, me = await w.member()
    tenant_admin, _ = await w.member(role="tenant_admin")
    target = await _named(w, "Cal Synthetic")
    item = await _item(w, state="needs_staff")
    before = await snapshot(w, item.id)
    refused = await act(w, staff_client, item.id, "assign", assignee_id=target)
    assert refused.status_code == 403, refused.text
    assert await snapshot(w, item.id) == before
    reader = await _named(w, "Dee Synthetic", role="read_only")
    disabled = await _named(w, "Eli Synthetic")
    await w.q("UPDATE console_memberships SET is_enabled = false WHERE user_id = %s", disabled)
    foreign = await _named(w, "Fin Synthetic", tenant=1)
    for invalid in (reader, disabled, foreign, str(uuid4())):
        response = await act(w, tenant_admin, item.id, "assign", assignee_id=invalid)
        assert response.status_code == 422, response.text
        assert await snapshot(w, item.id) == before
    done = await act(w, tenant_admin, item.id, "assign", assignee_id=target)
    assert done.status_code == 200, done.text
    assert str((await state(w, item.id))["assignee_user_id"]) == target
    # Staff may claim an unclaimed item for themselves.
    free = await _item(w, state="needs_staff")
    claimed = await act(w, staff_client, free.id, "claim")
    assert claimed.status_code == 200, claimed.text
    assert str((await state(w, free.id))["assignee_user_id"]) == str(me["user"]["id"])
    # Without the member directory the assignee cannot be checked: 503, never 422.
    import csa.console.inbox_queue_actions as actions

    monkeypatch.setattr(actions, "_directory", lambda request: None)
    unavailable = await act(w, tenant_admin, item.id, "assign", assignee_id=target)
    assert unavailable.status_code == 503, unavailable.text


async def test_staff_release_only_their_own_claim(sessions: SessionWorld) -> None:
    w = sessions
    staff_client, me = await w.member()
    tenant_admin, _ = await w.member(role="tenant_admin")
    other = await _named(w, "Gus Synthetic")
    theirs = await _item(w, state="needs_staff", assignee=other)
    before = await snapshot(w, theirs.id)
    refused = await act(w, staff_client, theirs.id, "unclaim")
    assert refused.status_code == 403, refused.text
    assert await snapshot(w, theirs.id) == before
    released = await act(w, tenant_admin, theirs.id, "unclaim")
    assert released.status_code == 200 and released.json()["outcome"] == "applied"
    assert (await state(w, theirs.id))["assignee_user_id"] is None
    mine = await _item(w, state="needs_staff", assignee=str(me["user"]["id"]))
    own = await act(w, staff_client, mine.id, "unclaim")
    assert own.status_code == 200 and own.json()["outcome"] == "applied"
    unclaimed = await _item(w, state="needs_staff")
    nothing = await act(w, staff_client, unclaimed.id, "unclaim")
    assert nothing.status_code == 200 and nothing.json()["outcome"] == "already_handled"


async def test_only_administrators_take_over_someone_elses_claim(sessions: SessionWorld) -> None:
    w = sessions
    staff_client, me = await w.member()
    tenant_admin, admin_me = await w.member(role="tenant_admin")
    holder = await _named(w, "Hana Synthetic")
    item = await _item(w, state="needs_staff", assignee=holder)
    before = await snapshot(w, item.id)
    refused = await act(w, staff_client, item.id, "claim")
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == "only an administrator can take over someone else's claim"
    assert await snapshot(w, item.id) == before
    # Staff still act on the item someone else holds; the claim stays.
    changes: tuple[tuple[str, dict[str, Any]], ...] = (
        ("priority", {"priority": "urgent"}),
        ("topic", {"topic": "complaint"}),
    )
    for action, body in changes:
        response = await act(w, staff_client, item.id, action, **body)
        assert response.status_code == 200 and response.json()["outcome"] == "applied", action
        assert str((await state(w, item.id))["assignee_user_id"]) == holder, action
    # A tenant admin takes it over; the event records the previous holder.
    taken = await act(w, tenant_admin, item.id, "claim")
    assert taken.status_code == 200 and taken.json()["outcome"] == "applied", taken.text
    assert str((await state(w, item.id))["assignee_user_id"]) == str(admin_me["user"]["id"])
    claimed = (await events(w, item.id))[-1]
    assert claimed["action"] == "claimed"
    assert claimed["details"] == {
        "assignee_user_id": str(admin_me["user"]["id"]),
        "previous_assignee_user_id": holder,
    }
    # A platform admin may too.
    again = await act(w, w.admin, item.id, "claim")
    assert again.status_code == 200 and again.json()["outcome"] == "applied", again.text
    assert str((await state(w, item.id))["assignee_user_id"]) == w.admin_id
    # Staff snooze and resolve items someone else holds.
    snoozed = await act(w, staff_client, item.id, "snooze", until=soon(hours=2))
    assert snoozed.status_code == 200 and snoozed.json()["outcome"] == "applied", snoozed.text
    assert str((await state(w, item.id))["assignee_user_id"]) == w.admin_id
    other = await _item(w, state="needs_staff", assignee=holder)
    resolved = await act(w, staff_client, other.id, "resolve")
    assert resolved.status_code == 200 and resolved.json()["outcome"] == "applied", resolved.text
    # Claiming an unclaimed item, or one's own again, is fine for staff.
    free = await _item(w, state="needs_staff")
    first = await act(w, staff_client, free.id, "claim")
    assert first.status_code == 200 and first.json()["outcome"] == "applied", first.text
    assert str((await state(w, free.id))["assignee_user_id"]) == str(me["user"]["id"])
    own = await act(w, staff_client, free.id, "claim")
    assert own.status_code == 200 and own.json()["outcome"] == "already_handled", own.text


async def test_an_applied_assign_replays_its_receipt_without_the_directory(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    tenant_admin, _ = await w.member(role="tenant_admin")
    target = await _named(w, "Ola Synthetic")
    item = await _item(w, state="needs_staff")
    version = (await state(w, item.id))["inbox_version"]
    key = str(uuid4())
    done = await act(
        w, tenant_admin, item.id, "assign", key=key, version=version, assignee_id=target
    )
    assert done.status_code == 200, done.text
    after = await snapshot(w, item.id)
    # The assignee is disabled since: the replay still returns the stored receipt.
    await w.q("UPDATE console_memberships SET is_enabled = false WHERE user_id = %s", target)
    replay = await act(
        w, tenant_admin, item.id, "assign", key=key, version=version, assignee_id=target
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == done.json()
    # A new assign to the disabled member is refused.
    fresh = await act(w, tenant_admin, item.id, "assign", assignee_id=target)
    assert fresh.status_code == 422, fresh.text
    # The directory is down: the replay is still the receipt, a new assign is 503.
    import csa.console.inbox_queue_actions as actions

    monkeypatch.setattr(actions, "_directory", lambda request: None)
    replay = await act(
        w, tenant_admin, item.id, "assign", key=key, version=version, assignee_id=target
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == done.json()
    fresh = await act(w, tenant_admin, item.id, "assign", assignee_id=target)
    assert fresh.status_code == 503, fresh.text
    assert await snapshot(w, item.id) == after
    # Staff are refused before any receipt is read.
    staff_client, _ = await w.member()
    refused = await act(
        w, staff_client, item.id, "assign", key=key, version=version, assignee_id=target
    )
    assert refused.status_code == 403, refused.text


async def test_snooze_needs_a_time_in_the_next_sixty_days(sessions: SessionWorld) -> None:
    w = sessions
    item = await _item(w, state="needs_staff")
    before = await snapshot(w, item.id)
    naive = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    for until in (soon(minutes=-1), soon(days=60, minutes=5), naive):
        response = await act(w, w.admin, item.id, "snooze", until=until)
        assert response.status_code == 422, (until, response.text)
        assert await snapshot(w, item.id) == before
    missing = await act(w, w.admin, item.id, "snooze")
    assert missing.status_code == 422
    near_limit = await act(w, w.admin, item.id, "snooze", until=soon(days=59))
    assert near_limit.status_code == 200, near_limit.text


@pytest.mark.parametrize("action", ["resolve", "snooze"])
async def test_a_pending_held_reply_refuses_resolve_and_snooze(
    sessions: SessionWorld, action: str
) -> None:
    w = sessions
    item = await _item(w, state="needs_staff", reason="held_reply")
    await pending_approval(w, item.id)
    before = await snapshot(w, item.id)
    body: dict[str, Any] = {"until": soon(hours=1)} if action == "snooze" else {}
    response = await act(w, w.admin, item.id, action, **body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {"code": "held_reply_pending"}
    assert await snapshot(w, item.id) == before


@pytest.mark.parametrize("action", ["resolve", "snooze"])
async def test_a_newer_customer_message_refuses_resolve_and_snooze(
    sessions: SessionWorld, action: str
) -> None:
    w = sessions
    item = await _item(w, state="needs_staff", assignee=w.admin_id)
    wrote = await customer_wrote_now(w, item.id)
    before = await snapshot(w, item.id)
    body: dict[str, Any] = {"until": soon(hours=1)} if action == "snooze" else {}
    seen = (wrote - timedelta(seconds=30)).isoformat()
    response = await act(w, w.admin, item.id, action, seen_customer_at=seen, **body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {"code": "customer_wrote"}
    assert await snapshot(w, item.id) == before
    # Having seen that message, the person may act.
    seen_it = await act(w, w.admin, item.id, action, seen_customer_at=wrote.isoformat(), **body)
    assert seen_it.status_code == 200, seen_it.text
    assert seen_it.json()["outcome"] == "applied"


async def test_read_only_is_403_and_another_company_is_404_on_every_route(
    sessions: SessionWorld,
) -> None:
    w = sessions
    reader, _ = await w.member(role="read_only")
    tenant_admin, _ = await w.member(role="tenant_admin")
    staff_client, _ = await w.member()
    target = await _named(w, "Ivy Synthetic")
    mine = await _item(w, state="needs_staff")
    theirs = await _item(w, 1, state="needs_staff")
    before = (await snapshot(w, mine.id), await snapshot(w, theirs.id))
    for action in ACTIONS:
        body: dict[str, Any] = (
            {"assignee_id": target} if action == "assign" else await extras(w, action)
        )
        refused = await act(w, reader, mine.id, action, **body)
        assert refused.status_code == 403, (action, refused.text)
        hidden = await act(w, tenant_admin, theirs.id, action, version=0, **body)
        assert hidden.status_code == 404, (action, hidden.text)
        as_admin = await act(w, w.admin, theirs.id, action, version=0, **body)
        assert as_admin.status_code == 404, (action, as_admin.text)
        if action != "assign":
            as_staff = await act(w, staff_client, theirs.id, action, version=0, **body)
            assert as_staff.status_code == 404, (action, as_staff.text)
    bulk = await w.mutate(
        reader,
        bulk_path(w),
        {"request_key": str(uuid4()), "action": "claim", "ids": [mine.id], "seen_at": soon()},
    )
    assert bulk.status_code == 403
    assert (await snapshot(w, mine.id), await snapshot(w, theirs.id)) == before


async def test_a_queue_action_receipt_carries_ids_and_no_name(sessions: SessionWorld) -> None:
    w = sessions
    item = await _item(w, state="needs_staff", name="Nur Receipt")
    key = str(uuid4())
    response = await act(w, w.admin, item.id, "claim", key=key)
    assert response.status_code == 200, response.text
    [receipt] = await w.q(
        "SELECT result FROM console_inbox_actions WHERE client_id = %s AND request_key = %s",
        UUID(w.tenants[0]),
        UUID(key),
    )
    stored = receipt["result"]["conversation"]
    assert stored["assignee_id"] == w.admin_id and "assignee" not in stored
    assert stored["contact"] is None and stored["last_message"] is None
    text = json.dumps(receipt["result"])
    assert "Synthetic Admin" not in text and "Nur Receipt" not in text
    assert "Synthetic Admin" not in response.text and "Nur Receipt" not in response.text


# --- bulk ----------------------------------------------------------------------------------------


def bulk_body(action: str, ids: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "request_key": str(uuid4()),
        "action": action,
        "ids": ids,
        "seen_at": extra.pop("seen_at", soon()),
        **extra,
    }


def unsorted(ids: list[str]) -> list[str]:
    """The ids in an order that differs from ascending id order."""
    ordered = sorted(ids, key=UUID)
    shuffled = ordered[1::2][::-1] + ordered[0::2]
    assert shuffled != ordered
    return shuffled


async def test_bulk_resolve_reports_each_item_and_changes_only_the_done_ones(
    sessions: SessionWorld,
) -> None:
    w = sessions
    items = [(await _item(w, state="needs_staff")).id for _ in range(30)]
    wrote_item, held_item = items[3], items[17]
    seen_at = datetime.now(UTC)
    await customer_wrote_now(w, wrote_item)
    await pending_approval(w, held_item)
    untouched = {i: await snapshot(w, i) for i in (wrote_item, held_item)}
    request_ids = unsorted(items)
    response = await w.mutate(
        w.admin, bulk_path(w), bulk_body("resolve", request_ids, seen_at=seen_at.isoformat())
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [r["id"] for r in results] == request_ids
    outcomes = {r["id"]: r["outcome"] for r in results}
    assert outcomes[wrote_item] == "customer_wrote"
    assert outcomes[held_item] == "held_reply_pending"
    done = [i for i in items if i not in untouched]
    assert {outcomes[i] for i in done} == {"done"} and len(done) == 28
    for conversation in done:
        current = await state(w, conversation)
        assert current["queue_status"] == "resolved" and current["inbox_version"] == 1
    for conversation, before in untouched.items():
        assert await snapshot(w, conversation) == before
    # Processed in ascending id order (the lock order), reported in the request's order.
    written = await w.q(
        "SELECT conversation_id FROM inbox_queue_events WHERE client_id = %s ORDER BY id",
        UUID(w.tenants[0]),
    )
    assert [str(r["conversation_id"]) for r in written] == sorted(done, key=UUID)
    [audited] = await w.q(
        "SELECT details FROM console_audit WHERE client_id = %s AND action = 'inbox.queue_bulk'",
        UUID(w.tenants[0]),
    )
    assert audited["details"] == {
        "action": "resolve",
        "items": 30,
        "outcomes": {"customer_wrote": 1, "done": 28, "held_reply_pending": 1},
    }


async def test_bulk_snooze_uses_seen_at_and_bounds_the_time(sessions: SessionWorld) -> None:
    w = sessions
    fresh, wrote = (
        (await _item(w, state="needs_staff")).id,
        (await _item(w, state="needs_staff")).id,
    )
    seen_at = datetime.now(UTC)
    await customer_wrote_now(w, wrote)
    for until in (soon(minutes=-1), soon(days=61)):
        refused = await w.mutate(w.admin, bulk_path(w), bulk_body("snooze", [fresh], until=until))
        assert refused.status_code == 422, refused.text
    response = await w.mutate(
        w.admin,
        bulk_path(w),
        bulk_body("snooze", [fresh, wrote], until=soon(hours=3), seen_at=seen_at.isoformat()),
    )
    assert response.status_code == 200, response.text
    assert [r["outcome"] for r in response.json()["results"]] == ["done", "customer_wrote"]
    assert (await state(w, fresh))["queue_status"] == "snoozed"
    assert (await state(w, wrote))["queue_status"] == "needs_staff"


async def test_bulk_field_actions_claims_and_missing_items(sessions: SessionWorld) -> None:
    w = sessions
    a, b = (await _item(w, state="needs_staff")).id, (await _item(w, state="needs_staff")).id
    already = (await _item(w, state="needs_staff", priority="urgent")).id
    foreign = (await _item(w, 1, state="needs_staff")).id
    missing = str(uuid4())
    response = await w.mutate(
        w.admin,
        bulk_path(w),
        bulk_body("priority", [a, already, foreign, missing, b], priority="urgent"),
    )
    assert response.status_code == 200, response.text
    assert [r["outcome"] for r in response.json()["results"]] == [
        "done",
        "already_handled",
        "not_found",
        "not_found",
        "done",
    ]
    assert (await state(w, a))["priority"] == (await state(w, b))["priority"] == "urgent"
    assert (await state(w, foreign))["priority"] == "normal"
    topic = await w.mutate(w.admin, bulk_path(w), bulk_body("topic", [a, b], topic="payment"))
    assert [r["outcome"] for r in topic.json()["results"]] == ["done", "done"]
    assert (await state(w, b))["topic"] == "payment"
    claim = await w.mutate(w.admin, bulk_path(w), bulk_body("claim", [a, b]))
    assert [r["outcome"] for r in claim.json()["results"]] == ["done", "done"]
    assert str((await state(w, a))["assignee_user_id"]) == w.admin_id


async def test_bulk_unclaim_by_staff_releases_only_their_own_claims(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff_client, me = await w.member()
    other = await _named(w, "Jai Synthetic")
    mine = (await _item(w, state="needs_staff", assignee=str(me["user"]["id"]))).id
    theirs = (await _item(w, state="needs_staff", assignee=other)).id
    free = (await _item(w, state="needs_staff")).id
    before = await snapshot(w, theirs)
    response = await w.mutate(
        staff_client, bulk_path(w), bulk_body("unclaim", [mine, theirs, free])
    )
    assert response.status_code == 200, response.text
    assert [r["outcome"] for r in response.json()["results"]] == [
        "done",
        "not_allowed",
        "already_handled",
    ]
    assert (await state(w, mine))["assignee_user_id"] is None
    assert await snapshot(w, theirs) == before


async def test_bulk_claim_by_staff_never_takes_over_a_claim(sessions: SessionWorld) -> None:
    w = sessions
    staff_client, me = await w.member()
    tenant_admin, admin_me = await w.member(role="tenant_admin")
    other = await _named(w, "Ivy Synthetic")
    mine = (await _item(w, state="needs_staff", assignee=str(me["user"]["id"]))).id
    theirs = (await _item(w, state="needs_staff", assignee=other)).id
    free = (await _item(w, state="needs_staff")).id
    before = await snapshot(w, theirs)
    response = await w.mutate(staff_client, bulk_path(w), bulk_body("claim", [mine, theirs, free]))
    assert response.status_code == 200, response.text
    assert [r["outcome"] for r in response.json()["results"]] == [
        "already_handled",
        "not_allowed",
        "done",
    ]
    assert await snapshot(w, theirs) == before
    assert str((await state(w, free))["assignee_user_id"]) == str(me["user"]["id"])
    # Other actions by staff still apply to an item someone else holds.
    priority = await w.mutate(
        staff_client, bulk_path(w), bulk_body("priority", [theirs], priority="high")
    )
    assert [r["outcome"] for r in priority.json()["results"]] == ["done"]
    assert str((await state(w, theirs))["assignee_user_id"]) == other
    # Administrators take it over.
    taken = await w.mutate(tenant_admin, bulk_path(w), bulk_body("claim", [theirs]))
    assert [r["outcome"] for r in taken.json()["results"]] == ["done"]
    assert str((await state(w, theirs))["assignee_user_id"]) == str(admin_me["user"]["id"])
    again = await w.mutate(w.admin, bulk_path(w), bulk_body("claim", [theirs]))
    assert [r["outcome"] for r in again.json()["results"]] == ["done"]
    assert str((await state(w, theirs))["assignee_user_id"]) == w.admin_id


async def test_an_applied_bulk_assign_replays_its_receipt_without_the_directory(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    target = await _named(w, "Pia Synthetic")
    ids = [(await _item(w, state="needs_staff")).id for _ in range(2)]
    body = bulk_body("assign", ids, assignee_id=target)
    done = await w.mutate(w.admin, bulk_path(w), body)
    assert done.status_code == 200, done.text
    after = [await snapshot(w, i) for i in ids]
    await w.q("UPDATE console_memberships SET is_enabled = false WHERE user_id = %s", target)
    replay = await w.mutate(w.admin, bulk_path(w), body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == done.json()
    fresh = await w.mutate(w.admin, bulk_path(w), bulk_body("assign", ids, assignee_id=target))
    assert fresh.status_code == 422, fresh.text
    import csa.console.inbox_queue_actions as actions

    monkeypatch.setattr(actions, "_directory", lambda request: None)
    replay = await w.mutate(w.admin, bulk_path(w), body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == done.json()
    fresh = await w.mutate(w.admin, bulk_path(w), bulk_body("assign", ids, assignee_id=target))
    assert fresh.status_code == 503, fresh.text
    assert [await snapshot(w, i) for i in ids] == after


async def test_bulk_assign_needs_an_administrator_and_a_valid_member(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    staff_client, _ = await w.member()
    target = await _named(w, "Kit Synthetic")
    reader = await _named(w, "Lou Synthetic", role="read_only")
    ids = [(await _item(w, state="needs_staff")).id for _ in range(3)]
    before = [await snapshot(w, i) for i in ids]
    refused = await w.mutate(
        staff_client, bulk_path(w), bulk_body("assign", ids, assignee_id=target)
    )
    assert refused.status_code == 403, refused.text
    invalid = await w.mutate(w.admin, bulk_path(w), bulk_body("assign", ids, assignee_id=reader))
    assert invalid.status_code == 422, invalid.text
    assert [await snapshot(w, i) for i in ids] == before
    done = await w.mutate(w.admin, bulk_path(w), bulk_body("assign", ids, assignee_id=target))
    assert done.status_code == 200, done.text
    assert {str((await state(w, i))["assignee_user_id"]) for i in ids} == {target}
    import csa.console.inbox_queue_actions as actions

    monkeypatch.setattr(actions, "_directory", lambda request: None)
    unavailable = await w.mutate(
        w.admin, bulk_path(w), bulk_body("assign", ids, assignee_id=target)
    )
    assert unavailable.status_code == 503, unavailable.text


async def test_bulk_bodies_outside_the_shape_are_422(sessions: SessionWorld) -> None:
    w = sessions
    ids = [(await _item(w, state="needs_staff")).id for _ in range(2)]
    before = [await snapshot(w, i) for i in ids]
    many = [str(uuid4()) for _ in range(51)]
    no_seen_at = bulk_body("claim", ids)
    del no_seen_at["seen_at"]
    naive = bulk_body("claim", ids, seen_at=datetime.now(UTC).replace(tzinfo=None).isoformat())
    bodies = [
        bulk_body("claim", many),
        bulk_body("claim", [ids[0], ids[0]]),
        bulk_body("claim", []),
        bulk_body("claim", ids, topic="payment"),
        bulk_body("resolve", ids, until=soon(hours=1)),
        bulk_body("assign", ids),
        bulk_body("snooze", ids),
        bulk_body("priority", ids),
        bulk_body("topic", ids),
        bulk_body("topic", ids, topic=None),
        bulk_body("topic", ids, topic="weather"),
        bulk_body("reply", ids),
        no_seen_at,
        naive,
        {**bulk_body("claim", ids), "expected_version": 0},
    ]
    for body in bodies:
        response = await w.mutate(w.admin, bulk_path(w), body)
        assert response.status_code == 422, (body, response.text)
    assert [await snapshot(w, i) for i in ids] == before
    fifty = await w.mutate(w.admin, bulk_path(w), bulk_body("claim", many[:50]))
    assert fifty.status_code == 200, fifty.text
    assert {r["outcome"] for r in fifty.json()["results"]} == {"not_found"}


async def test_bulk_replays_its_results_and_refuses_another_body(sessions: SessionWorld) -> None:
    w = sessions
    ids = [(await _item(w, state="needs_staff")).id for _ in range(3)]
    body = bulk_body("claim", ids)
    first = await w.mutate(w.admin, bulk_path(w), body)
    assert first.status_code == 200, first.text
    after = [await snapshot(w, i) for i in ids]
    again = await w.mutate(w.admin, bulk_path(w), body)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert [await snapshot(w, i) for i in ids] == after
    other = await w.mutate(w.admin, bulk_path(w), {**body, "ids": ids[:2]})
    assert other.status_code == 409, other.text
    assert other.json()["detail"] == "request key was used with different content"
    # A bulk key is never read as a single action's receipt.
    single = await act(w, w.admin, ids[0], "claim", key=body["request_key"])
    assert single.status_code == 409
    audits = await w.q(
        "SELECT count(*) AS n FROM console_audit WHERE client_id = %s AND action = %s",
        UUID(w.tenants[0]),
        "inbox.queue_bulk",
    )
    assert audits[0]["n"] == 1


async def test_a_database_error_aborts_the_whole_bulk_request(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    ids = sorted([(await _item(w, state="needs_staff")).id for _ in range(3)], key=UUID)
    before = [await snapshot(w, i) for i in ids]
    real = transitions.set_topic
    calls: list[UUID] = []

    async def flaky(*args: Any, **kwargs: Any) -> None:
        calls.append(args[2])
        if len(calls) == 2:
            raise RuntimeError("synthetic-database-failure")
        await real(*args, **kwargs)

    monkeypatch.setattr(transitions, "set_topic", flaky)
    body = bulk_body("topic", ids, topic="other")
    response = await w.mutate(raw_client(w, w.admin), bulk_path(w), body)
    assert response.status_code == 500
    assert len(calls) == 2
    assert [await snapshot(w, i) for i in ids] == before
    assert (
        await w.q(
            "SELECT 1 FROM console_inbox_actions WHERE request_key = %s", UUID(body["request_key"])
        )
        == []
    )
    assert (
        await w.q(
            "SELECT 1 FROM console_audit WHERE client_id = %s AND action = 'inbox.queue_bulk'",
            UUID(w.tenants[0]),
        )
        == []
    )
