"""The inbox queue's API surface (plan 1a, tasks 6 to 9): the snooze wake job, the company's inbox
settings, the list of people a conversation can be assigned to, and the queue list with its
filters, orders, cursor and counts (disposable database, synthetic data). Tasks 8 and 9 add their
routes' tests to this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from csa.agents.escalation.agent import MUTE_FOR
from csa.console.inbox_cursor import decode as decode_cursor
from csa.console.inbox_queue_list import (
    LIST_QUERIES,
    QueueFilter,
    fetch_counts,
    fetch_page,
    list_params,
)
from csa.console.inbox_settings import InboxSettingsIn, assignee_names, company_assignees
from csa.core import db
from csa.core.db import DB_SQL_DIR
from csa.core.tenancy import tx
from csa.inbox_queue.settings import InboxSettings
from csa.jobs.clock import FakeClock
from csa.jobs.inbox_queue import run_inbox_snooze_wake
from csa.jobs.runner import JobRunner, build_default_registry
from fastapi import HTTPException
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_inbox_queue_triggers import _escalate, _needs_staff, _seed, _state

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

FIELDS = (
    "target_urgent_min",
    "target_high_min",
    "target_normal_min",
    "target_low_min",
    "repeat_minutes",
    "max_repeats",
    "follow_up_hours",
    "stale_claim_hours",
    "handback_prompt_hours",
)
CUSTOM: dict[str, int] = {
    "target_urgent_min": 10,
    "target_high_min": 30,
    "target_normal_min": 120,
    "target_low_min": 720,
    "repeat_minutes": 30,
    "max_repeats": 5,
    "follow_up_hours": 24,
    "stale_claim_hours": 4,
    "handback_prompt_hours": 12,
}


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "customer_support")


def settings_path(w: SessionWorld, tenant: int = 0) -> str:
    return f"/console/v1/inbox/settings?client_id={w.tenants[tenant]}"


def assignees_path(w: SessionWorld, tenant: int = 0) -> str:
    return f"/console/v1/inbox/assignees?client_id={w.tenants[tenant]}"


async def saved_count(w: SessionWorld, tenant: int = 0) -> int:
    rows = await w.q(
        "SELECT count(*) AS n FROM inbox_settings WHERE client_id = %s", UUID(w.tenants[tenant])
    )
    return rows[0]["n"]


def put_body(version: int, **changes: int) -> dict[str, int]:
    return {**CUSTOM, **changes, "version": version}


# --- the snooze wake job --------------------------------------------------------------------------


def _snoozed(w: SessionWorld, tenant: int, *, due: bool) -> Any:
    until = datetime.now(UTC) + (timedelta(minutes=-5) if due else timedelta(hours=2))
    return _seed(
        w.owner_dsn,
        w.tenants[tenant],
        owned_by="human_support",
        queue_status="snoozed",
        snoozed_until=until,
    )


async def test_the_wake_job_wakes_a_due_snoozed_item_and_leaves_a_not_due_one(
    sessions: SessionWorld,
) -> None:
    w = sessions
    due, later = _snoozed(w, 0, due=True), _snoozed(w, 0, due=False)
    other_company = _snoozed(w, 1, due=True)
    async with tx(w.pool, w.tenants[0]) as conn:
        ran = await run_inbox_snooze_wake(conn, UUID(w.tenants[0]), FakeClock())
    assert ran == {"woken_count": 1}
    assert _state(w.owner_dsn, due)["queue_status"] == "needs_staff"
    assert _state(w.owner_dsn, later)["queue_status"] == "snoozed"
    assert _state(w.owner_dsn, other_company)["queue_status"] == "snoozed"
    async with tx(w.pool, w.tenants[0]) as conn:
        again = await run_inbox_snooze_wake(conn, UUID(w.tenants[0]), FakeClock())
    assert again == {"woken_count": 0}


async def test_the_wake_job_batch_size_limits_one_run(sessions: SessionWorld) -> None:
    w = sessions
    items = [_snoozed(w, 0, due=True) for _ in range(3)]
    async with tx(w.pool, w.tenants[0]) as conn:
        first = await run_inbox_snooze_wake(conn, UUID(w.tenants[0]), FakeClock(), batch_size=2)
    assert first == {"woken_count": 2}
    async with tx(w.pool, w.tenants[0]) as conn:
        rest = await run_inbox_snooze_wake(conn, UUID(w.tenants[0]), FakeClock())
    assert rest == {"woken_count": 1}
    assert {_state(w.owner_dsn, i)["queue_status"] for i in items} == {"needs_staff"}


async def test_the_registry_runs_the_wake_job_every_minute_for_each_client(
    sessions: SessionWorld,
) -> None:
    job = build_default_registry().get("inbox_snooze_wake")
    assert job is not None
    assert job.every == timedelta(minutes=1)
    assert job.per_client is True
    assert job.fn is run_inbox_snooze_wake


async def test_the_runner_wakes_the_due_item_through_the_registry(
    sessions: SessionWorld, app_dsn: str
) -> None:
    w = sessions
    due, later = _snoozed(w, 0, due=True), _snoozed(w, 0, due=False)
    async with (
        db.build_pool(w.owner_dsn, max_size=2, autocommit=True) as owner_pool,
        db.build_pool(app_dsn, max_size=2, autocommit=True) as app_pool,
    ):
        runner = JobRunner(owner_pool, app_pool, lock_conninfo=w.owner_dsn, clock=FakeClock())
        assert await runner.run_job_once("inbox_snooze_wake") is True
    assert _state(w.owner_dsn, due)["queue_status"] == "needs_staff"
    assert _state(w.owner_dsn, later)["queue_status"] == "snoozed"
    [run] = await w.q(
        "SELECT ok, stats FROM job_runs WHERE job = 'inbox_snooze_wake' AND client_id = %s",
        UUID(w.tenants[0]),
    )
    assert run["ok"] is True and run["stats"] == {"woken_count": 1}


# --- settings ------------------------------------------------------------


async def test_settings_default_to_version_zero_when_nothing_is_saved(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member()
    for client in (w.admin, staff):
        response = await client.get(settings_path(w))
        assert response.status_code == 200, response.text
        assert response.json() == {**InboxSettings().model_dump(), "version": 0}
    assert await saved_count(w) == 0


async def test_the_first_save_inserts_and_later_saves_update(sessions: SessionWorld) -> None:
    w = sessions
    first = await w.mutate(w.admin, settings_path(w), put_body(0), method="PUT")
    assert first.status_code == 200, first.text
    assert first.json() == {**CUSTOM, "version": 1}
    second = await w.mutate(
        w.admin, settings_path(w), put_body(1, target_urgent_min=20, max_repeats=0), method="PUT"
    )
    assert second.status_code == 200, second.text
    assert second.json() == {**CUSTOM, "target_urgent_min": 20, "max_repeats": 0, "version": 2}
    rows = await w.q(
        "SELECT client_id, version, target_urgent_min FROM inbox_settings WHERE client_id = %s",
        UUID(w.tenants[0]),
    )
    assert [(str(r["client_id"]), r["version"], r["target_urgent_min"]) for r in rows] == [
        (w.tenants[0], 2, 20)
    ]
    staff, _ = await w.member()
    seen = await staff.get(settings_path(w))
    assert seen.json() == second.json()


async def test_a_stale_or_future_version_is_409_and_changes_nothing(
    sessions: SessionWorld,
) -> None:
    w = sessions
    stale = await w.mutate(w.admin, settings_path(w), put_body(3), method="PUT")
    assert stale.status_code == 409 and stale.json()["detail"] == "version_conflict"
    assert await saved_count(w) == 0
    assert (await w.mutate(w.admin, settings_path(w), put_body(0), method="PUT")).status_code == 200
    again = await w.mutate(w.admin, settings_path(w), put_body(0, max_repeats=1), method="PUT")
    assert again.status_code == 409 and again.json()["detail"] == "version_conflict"
    assert (
        await w.q(
            "SELECT max_repeats, version FROM inbox_settings WHERE client_id = %s",
            UUID(w.tenants[0]),
        )
    )[0] == {
        "max_repeats": CUSTOM["max_repeats"],
        "version": 1,
    }


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("target_urgent_min", 4),
        ("target_urgent_min", 10081),
        ("target_high_min", 4),
        ("target_high_min", 10081),
        ("target_normal_min", 4),
        ("target_normal_min", 10081),
        ("target_low_min", 4),
        ("target_low_min", 10081),
        ("repeat_minutes", 14),
        ("repeat_minutes", 1441),
        ("max_repeats", -1),
        ("max_repeats", 11),
        ("follow_up_hours", -1),
        ("follow_up_hours", 721),
        ("stale_claim_hours", -1),
        ("stale_claim_hours", 169),
        ("handback_prompt_hours", -1),
        ("handback_prompt_hours", 721),
    ],
)
async def test_values_outside_the_ranges_are_422(
    sessions: SessionWorld, field: str, bad: int
) -> None:
    w = sessions
    response = await w.mutate(w.admin, settings_path(w), put_body(0, **{field: bad}), method="PUT")
    assert response.status_code == 422, response.text
    assert await saved_count(w) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_urgent_min", 5),
        ("target_urgent_min", 10080),
        ("target_low_min", 10080),
        ("repeat_minutes", 15),
        ("repeat_minutes", 1440),
        ("max_repeats", 0),
        ("max_repeats", 10),
        ("follow_up_hours", 0),
        ("follow_up_hours", 1),
        ("follow_up_hours", 720),
        ("stale_claim_hours", 0),
        ("stale_claim_hours", 1),
        ("stale_claim_hours", 168),
        ("handback_prompt_hours", 0),
        ("handback_prompt_hours", 1),
        ("handback_prompt_hours", 720),
    ],
)
async def test_the_bounds_themselves_are_accepted(
    sessions: SessionWorld, field: str, value: int
) -> None:
    w = sessions
    response = await w.mutate(
        w.admin, settings_path(w), put_body(0, **{field: value}), method="PUT"
    )
    assert response.status_code == 200, response.text
    assert response.json()[field] == value


async def test_a_save_must_carry_every_field_and_nothing_else(sessions: SessionWorld) -> None:
    w = sessions
    partial = {k: v for k, v in put_body(0).items() if k != "max_repeats"}
    assert (await w.mutate(w.admin, settings_path(w), partial, method="PUT")).status_code == 422
    extra = {**put_body(0), "unknown": 1}
    assert (await w.mutate(w.admin, settings_path(w), extra, method="PUT")).status_code == 422
    no_version = {k: v for k, v in put_body(0).items() if k != "version"}
    assert (await w.mutate(w.admin, settings_path(w), no_version, method="PUT")).status_code == 422
    negative = put_body(-1)
    assert (await w.mutate(w.admin, settings_path(w), negative, method="PUT")).status_code == 422


async def test_the_request_ranges_are_the_settings_model_ranges() -> None:
    for name in FIELDS:
        assert InboxSettingsIn.model_fields[name].metadata == (
            InboxSettings.model_fields[name].metadata
        ), name


async def test_roles_on_settings(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member()
    tenant_admin, _ = await w.member(role="tenant_admin")
    reader, _ = await w.member(role="read_only")
    assert (await staff.get(settings_path(w))).status_code == 200
    assert (await tenant_admin.get(settings_path(w))).status_code == 200
    assert (await reader.get(settings_path(w))).status_code == 403
    assert (await w.mutate(staff, settings_path(w), put_body(0), method="PUT")).status_code == 403
    assert (await w.mutate(reader, settings_path(w), put_body(0), method="PUT")).status_code == 403
    assert await saved_count(w) == 0
    done = await w.mutate(tenant_admin, settings_path(w), put_body(0), method="PUT")
    assert done.status_code == 200, done.text
    # A platform admin acts only for the company they select.
    assert (await w.admin.get("/console/v1/inbox/settings")).status_code == 400
    bare = await w.mutate(w.admin, "/console/v1/inbox/settings", put_body(1), method="PUT")
    assert bare.status_code == 400
    chosen = await w.mutate(w.admin, settings_path(w), put_body(1, max_repeats=2), method="PUT")
    assert chosen.status_code == 200, chosen.text


async def test_another_company_never_sees_or_changes_these_settings(
    sessions: SessionWorld,
) -> None:
    w = sessions
    assert (await w.mutate(w.admin, settings_path(w), put_body(0), method="PUT")).status_code == 200
    foreign, _ = await w.member(tenant=w.tenants[1])
    own = await foreign.get(settings_path(w, 1))
    assert own.json() == {**InboxSettings().model_dump(), "version": 0}
    assert (await foreign.get(settings_path(w, 0))).status_code == 403
    denied = await w.mutate(foreign, settings_path(w, 0), put_body(1), method="PUT")
    assert denied.status_code == 403
    # Saving for the second company leaves the first company's row alone.
    foreign_admin, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    saved = await w.mutate(
        foreign_admin, settings_path(w, 1), put_body(0, target_urgent_min=99), method="PUT"
    )
    assert saved.status_code == 200
    rows = await w.q(
        "SELECT client_id, target_urgent_min FROM inbox_settings WHERE client_id = ANY(%s)",
        [UUID(t) for t in w.tenants],
    )
    assert {str(r["client_id"]): r["target_urgent_min"] for r in rows} == {
        w.tenants[0]: CUSTOM["target_urgent_min"],
        w.tenants[1]: 99,
    }


async def test_a_save_is_audited(sessions: SessionWorld) -> None:
    w = sessions
    assert (await w.mutate(w.admin, settings_path(w), put_body(0), method="PUT")).status_code == 200
    rows = await w.q(
        "SELECT actor_id, client_id, target_id, details FROM console_audit "
        "WHERE action = 'inbox.settings.updated' AND client_id = %s",
        UUID(w.tenants[0]),
    )
    assert len(rows) == 1
    assert str(rows[0]["actor_id"]) == w.admin_id
    assert str(rows[0]["client_id"]) == w.tenants[0]
    assert rows[0]["target_id"] == w.tenants[0] and rows[0]["details"] == {}
    refused = await w.mutate(w.admin, settings_path(w), put_body(0), method="PUT")
    assert refused.status_code == 409
    audited = await w.q(
        "SELECT count(*) AS n FROM console_audit "
        "WHERE action = 'inbox.settings.updated' AND client_id = %s",
        UUID(w.tenants[0]),
    )
    assert audited[0]["n"] == 1


async def test_a_save_changes_only_items_that_enter_needs_staff_afterwards(
    sessions: SessionWorld, app_dsn: str
) -> None:
    w = sessions
    tenant = w.tenants[0]
    waiting = _seed(w.owner_dsn, tenant, **_needs_staff("escalation", "normal", minutes_ago=10))
    before = _state(w.owner_dsn, waiting)["due_at"]
    saved = await w.mutate(
        w.admin, settings_path(w), put_body(0, target_normal_min=45), method="PUT"
    )
    assert saved.status_code == 200, saved.text
    assert _state(w.owner_dsn, waiting)["due_at"] == before
    entering = _seed(w.owner_dsn, tenant)
    assert _escalate(app_dsn, entering, "normal")
    row = _state(w.owner_dsn, entering)
    assert row["queue_status"] == "needs_staff"
    assert row["due_at"] - row["needs_staff_since"] == timedelta(minutes=45)
    assert _state(w.owner_dsn, waiting)["due_at"] == before


# --- assignees ------------------------------------------------------------


async def _named(w: SessionWorld, name: str, *, role: str = "tenant_staff", tenant: int = 0) -> str:
    client, me = await w.member(role=role, tenant=w.tenants[tenant])
    del client
    await w.q("UPDATE console_users SET display_name = %s WHERE id = %s", name, me["user"]["id"])
    return str(me["user"]["id"])


async def test_assignees_are_enabled_admins_and_staff_by_name(sessions: SessionWorld) -> None:
    w = sessions
    zed = await _named(w, "Zed Synthetic")
    amy_admin = await _named(w, "amy Synthetic", role="tenant_admin")
    bob = await _named(w, "Bob Synthetic")
    reader = await _named(w, "Cara Synthetic", role="read_only")
    muted_member = await _named(w, "Dan Synthetic")
    muted_user = await _named(w, "Eve Synthetic")
    await w.q("UPDATE console_memberships SET is_enabled = false WHERE user_id = %s", muted_member)
    await w.q("UPDATE console_users SET is_enabled = false WHERE id = %s", muted_user)
    foreign = await _named(w, "Fay Synthetic", tenant=1)
    staff, _ = await w.member()
    response = await staff.get(assignees_path(w))
    assert response.status_code == 200, response.text
    listed = response.json()
    mine = {zed, amy_admin, bob}
    assert [(r["id"], r["display_name"], r["role"]) for r in listed if r["id"] in mine] == [
        (amy_admin, "amy Synthetic", "tenant_admin"),
        (bob, "Bob Synthetic", "tenant_staff"),
        (zed, "Zed Synthetic", "tenant_staff"),
    ]
    ids = {row["id"] for row in listed}
    assert {zed, amy_admin, bob} <= ids
    assert not ids & {reader, muted_member, muted_user, foreign}
    assert all(set(row) == {"id", "display_name", "role"} for row in listed)
    assert (await w.admin.get(assignees_path(w))).status_code == 200


async def test_assignees_are_ordered_by_display_name_then_id(sessions: SessionWorld) -> None:
    w = sessions
    ids = [await _named(w, name) for name in ("Mira Synthetic", "Mira Synthetic", "Abel Synthetic")]
    response = await w.admin.get(assignees_path(w))
    assert response.status_code == 200, response.text
    names = [(row["display_name"], row["id"]) for row in response.json()]
    assert names == sorted(names, key=lambda pair: (pair[0].lower(), pair[1]))
    assert [n for n, _ in names] == ["Abel Synthetic", "Mira Synthetic", "Mira Synthetic"]
    assert {i for _, i in names} == set(ids)


async def test_assignees_roles_and_isolation(sessions: SessionWorld) -> None:
    w = sessions
    own = await _named(w, "Gia Synthetic")
    other = await _named(w, "Hal Synthetic", tenant=1)
    reader, _ = await w.member(role="read_only")
    assert (await reader.get(assignees_path(w))).status_code == 403
    foreign, _ = await w.member(tenant=w.tenants[1])
    assert (await foreign.get(assignees_path(w, 0))).status_code == 403
    mine = [row["id"] for row in (await foreign.get(assignees_path(w, 1))).json()]
    assert other in mine and own not in mine
    assert (await w.admin.get("/console/v1/inbox/assignees")).status_code == 400


async def test_the_assignee_helper_serves_the_validation_in_tasks_7_and_8(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff_id = await _named(w, "Ida Synthetic")
    reader_id = await _named(w, "Jon Synthetic", role="read_only")
    members = await company_assignees(w.app.state.identity_service.pool, UUID(w.tenants[0]))
    ids = {m.id for m in members}
    assert UUID(staff_id) in ids and UUID(reader_id) not in ids
    with pytest.raises(HTTPException) as unavailable:
        await company_assignees(None, UUID(w.tenants[0]))
    assert unavailable.value.status_code == 503


# --- the queue list, filters, orders, cursor and counts (task 7) ---------------------------------

QUEUE_FILES = ("activity", "waiting", "due", "priority")
RANK = {"low": 1, "normal": 2, "high": 3, "urgent": 4}


def list_path(w: SessionWorld, query: str = "", tenant: int = 0) -> str:
    return f"/console/v1/inbox/conversations?client_id={w.tenants[tenant]}{query}"


def counts_path(w: SessionWorld, tenant: int = 0) -> str:
    return f"/console/v1/inbox/queue/counts?client_id={w.tenants[tenant]}"


@dataclass
class Item:
    """One seeded conversation and the queue values the tests sort and count by."""

    id: str
    state: str = "bot"
    assignee: str | None = None
    due: datetime | None = None
    since: datetime | None = None
    priority: str = "normal"
    activity: datetime = field(default_factory=lambda: datetime.now(UTC))


async def _item(
    w: SessionWorld,
    tenant: int = 0,
    *,
    state: str = "bot",
    reason: str | None = None,
    topic: str | None = None,
    priority: str = "normal",
    assignee: str | None = None,
    since: datetime | None = None,
    due: datetime | None = None,
    snoozed_until: datetime | None = None,
    activity: datetime | None = None,
    channel: str = "telegram",
    name: str = "Synthetic Customer",
    phone: str | None = None,
    username: str | None = None,
    email: str | None = None,
    contact: UUID | None = None,
) -> Item:
    """A conversation in the given queue state, written as the owner (synthetic values only)."""
    if state == "needs_staff":
        reason = reason or "escalation"
        since = since or datetime.now(UTC) - timedelta(minutes=5)
        due = due or since + timedelta(minutes=240)
    client = UUID(w.tenants[tenant])
    if contact is None:
        contact = uuid4()
        await w.q(
            "INSERT INTO contacts (id, client_id, full_name, phone, telegram_username, email)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            contact,
            client,
            name,
            phone,
            username,
            email,
        )
    conversation = uuid4()
    at = activity or datetime.now(UTC)
    await w.q(
        "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread,"
        " last_activity_at, queue_status, queue_reason, topic, priority, assignee_user_id,"
        " claimed_at, needs_staff_since, due_at, snoozed_until, resolved_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " CASE WHEN %s::uuid IS NULL THEN NULL ELSE now() END, %s, %s, %s,"
        " CASE WHEN %s = 'resolved' THEN now() END)",
        conversation,
        client,
        contact,
        channel,
        f"thread-{conversation}",
        at,
        state,
        reason,
        topic,
        priority,
        assignee,
        assignee,
        since,
        due,
        snoozed_until,
        state,
    )
    return Item(str(conversation), state, assignee, due, since, priority, at)


async def _page(w: SessionWorld, query: str = "", *, client: Any = None) -> dict[str, Any]:
    response = await (client or w.admin).get(list_path(w, query))
    assert response.status_code == 200, response.text
    return response.json()


async def _walk(w: SessionWorld, query: str = "", *, limit: int = 2) -> list[str]:
    """Every page of the list through the cursor; the ids in the order the pages gave them."""
    ids: list[str] = []
    cursor = ""
    for _ in range(100):
        page = await _page(w, f"&limit={limit}{query}{cursor}")
        ids.extend(item["id"] for item in page["items"])
        if page["next_cursor"] is None:
            assert len(ids) == len(set(ids)), "a row was visited twice"
            return ids
        assert isinstance(page["next_cursor"], str)
        cursor = "&" + urlencode({"before": page["next_cursor"]})
    raise AssertionError("pagination did not end")


async def _ids(w: SessionWorld, query: str = "") -> set[str]:
    return set(await _walk(w, query, limit=50))


async def _member_id(w: SessionWorld, name: str) -> str:
    return (await _signed_in(w, name))[1]


async def _signed_in(w: SessionWorld, name: str) -> tuple[Any, str]:
    """A tenant staff member with this display name, and their signed-in browser."""
    client, me = await w.member()
    await w.q("UPDATE console_users SET display_name = %s WHERE id = %s", name, me["user"]["id"])
    return client, str(me["user"]["id"])


async def _queue_world(w: SessionWorld) -> tuple[dict[str, Item], str, Any]:
    """One conversation per state and the variations the filters pick out."""
    now = datetime.now(UTC)
    staff_client, staff = await _signed_in(w, "Kay Synthetic")
    items = {
        "overdue_mine": await _item(
            w,
            state="needs_staff",
            reason="escalation",
            priority="urgent",
            topic="complaint",
            assignee=w.admin_id,
            since=now - timedelta(minutes=120),
            due=now - timedelta(minutes=105),
        ),
        "fresh": await _item(
            w,
            state="needs_staff",
            reason="held_reply",
            priority="normal",
            topic="question",
            since=now - timedelta(minutes=10),
            due=now + timedelta(minutes=230),
        ),
        "whatsapp": await _item(
            w,
            state="needs_staff",
            reason="customer_message",
            priority="high",
            topic="payment",
            channel="whatsapp",
            since=now - timedelta(minutes=60),
            due=now + timedelta(minutes=1),
        ),
        "waiting": await _item(
            w, state="waiting_customer", priority="high", topic="purchase", assignee=staff
        ),
        "snoozed": await _item(
            w, state="snoozed", priority="low", snoozed_until=now + timedelta(hours=2)
        ),
        "resolved": await _item(w, state="resolved"),
        "bot": await _item(w),
    }
    return items, staff, staff_client


def _names(items: dict[str, Item], ids: set[str]) -> set[str]:
    by_id = {item.id: name for name, item in items.items()}
    return {by_id[i] for i in ids}


async def test_each_queue_filter_alone_and_combined(sessions: SessionWorld) -> None:
    w = sessions
    items, staff, _ = await _queue_world(w)
    everything = set(items)
    cases: dict[str, set[str]] = {
        "": everything,
        "&state=needs_staff": {"overdue_mine", "fresh", "whatsapp"},
        "&state=needs_staff&state=waiting_customer": {
            "overdue_mine",
            "fresh",
            "whatsapp",
            "waiting",
        },
        "&state=bot": {"bot"},
        "&state=resolved": {"resolved"},
        "&state=snoozed": {"snoozed"},
        "&state=waiting_customer": {"waiting"},
        "&assignee=me": {"overdue_mine"},
        "&assignee=none": {"fresh", "whatsapp", "snoozed", "resolved", "bot"},
        f"&assignee={staff}": {"waiting"},
        f"&assignee={uuid4()}": set(),
        "&priority_min=low": everything,
        "&priority_min=high": {"overdue_mine", "whatsapp", "waiting"},
        "&priority_min=urgent": {"overdue_mine"},
        "&reason=escalation&reason=held_reply": {"overdue_mine", "fresh"},
        "&reason=manual": set(),
        "&topic=complaint&topic=purchase": {"overdue_mine", "waiting"},
        "&overdue=true": {"overdue_mine"},
        "&overdue=false": everything,
        "&waiting_over=30": {"overdue_mine", "whatsapp"},
        "&waiting_over=90": {"overdue_mine"},
        "&waiting_over=525600": set(),
        "&channel=whatsapp": {"whatsapp"},
        "&channel=telegram": everything - {"whatsapp"},
        # Two at a time.
        "&state=needs_staff&assignee=none": {"fresh", "whatsapp"},
        "&state=needs_staff&priority_min=high": {"overdue_mine", "whatsapp"},
        "&priority_min=high&channel=telegram": {"overdue_mine", "waiting"},
        "&overdue=true&assignee=none": set(),
        "&topic=payment&waiting_over=30": {"whatsapp"},
        "&state=waiting_customer&state=snoozed&priority_min=normal": {"waiting"},
    }
    for query, expected in cases.items():
        assert _names(items, await _ids(w, query)) == expected, query


BAD_QUERIES = (
    "&state=open",
    "&assignee=someone",
    "&assignee=",
    "&priority_min=highest",
    "&reason=whim",
    "&topic=weather",
    "&overdue=perhaps",
    "&waiting_over=0",
    "&waiting_over=525601",
    "&order=random",
    "&limit=0",
    "&limit=51",
    "&channel=web",
    "&q=a",
    "&handled_by=robot",
    "&handled_by=",
    "&handled_by=Person",
)


async def test_a_bad_filter_value_is_422(sessions: SessionWorld) -> None:
    w = sessions
    for query in BAD_QUERIES:
        assert (await w.admin.get(list_path(w, query))).status_code == 422, query


async def test_filter_lists_are_bounded_by_their_value_counts(sessions: SessionWorld) -> None:
    w = sessions
    too_long = (
        "&state=needs_staff" * 6,
        "&reason=manual" * 5,
        "&topic=other" * 8,
    )
    for query in too_long:
        assert (await w.admin.get(list_path(w, query))).status_code == 422, query
    every = (
        "".join(
            f"&state={v}" for v in ("bot", "needs_staff", "waiting_customer", "snoozed", "resolved")
        ),
        "".join(f"&reason={v}" for v in ("escalation", "held_reply", "customer_message", "manual")),
        "&topic=other&topic=other&topic=booking",
    )
    for query in every:
        assert (await w.admin.get(list_path(w, query))).status_code == 200, query


async def test_the_list_says_when_it_was_read(sessions: SessionWorld) -> None:
    """`as_of` is the server's time of the read: the client echoes it as the bulk `seen_at`."""
    w = sessions
    started = datetime.now(UTC)
    response = await w.admin.get(list_path(w))
    assert response.status_code == 200, response.text
    raw = response.json()["as_of"]
    as_of = datetime.fromisoformat(raw)
    assert as_of.utcoffset() == timedelta(0), raw
    assert started - timedelta(seconds=5) <= as_of <= datetime.now(UTC) + timedelta(seconds=5)


async def test_search_sender_and_needs_review_keep_their_rules(sessions: SessionWorld) -> None:
    w = sessions
    named = await _item(w, state="needs_staff", name="Nur Aisyah", phone="0125550148")
    handle = await _item(w, name="Someone Else", username="aisyah_r")
    intl = await _item(w, name="Intl Format", phone="+60 12-888 0099")
    known = await _item(w, name="Known Buyer", email="buyer@example.test", state="needs_staff")
    reviewed = await _item(w, name="Held Reply", state="needs_staff", reason="held_reply")
    await w.q(
        "INSERT INTO approvals (client_id, conversation_id, trace_id, draft, reason, status,"
        " expires_at) VALUES (%s, %s, %s, 'synthetic draft', 'synthetic', 'pending',"
        " now() + interval '1 hour')",
        UUID(w.tenants[0]),
        UUID(reviewed.id),
        uuid4(),
    )

    async def found(query: str) -> set[str]:
        return await _ids(w, query)

    for q in ("aisyah", "AISYAH", "@Aisyah"):
        assert await found("&" + urlencode({"q": q})) == {named.id, handle.id}, q
    assert await found("&" + urlencode({"q": "@aisyah_r"})) == {handle.id}
    for q in ("0125550148", "60125550148", "+60 12-555 0148", "125550148"):
        assert await found("&" + urlencode({"q": q})) == {named.id}, q
    for q in ("0128880099", "+60128880099", "128880099"):
        assert await found("&" + urlencode({"q": q})) == {intl.id}, q
    # A short number is a contains match only: "60" is not read as the country code.
    assert await found("&q=60") == {intl.id}
    assert await found("&q=012") == {named.id, intl.id}
    assert await found("&" + urlencode({"q": "h_r"})) == {handle.id}
    assert await found("&" + urlencode({"q": "h%r"})) == set()
    assert await found("&sender=known") == {known.id}
    assert known.id not in await found("&sender=new")
    assert await found("&needs_review=true") == {reviewed.id}
    # Combined with a queue filter.
    assert await found("&q=aisyah&state=needs_staff") == {named.id}
    assert await found("&sender=known&state=bot") == set()
    assert await found("&needs_review=true&reason=escalation") == set()


def _ordered(items: list[Item], order: str) -> list[str]:
    def due_key(item: Item) -> tuple[bool, datetime, UUID]:
        moment = item.due or datetime.max.replace(tzinfo=UTC)
        return (item.due is None, moment, UUID(item.id))

    if order == "activity":
        return [i.id for i in sorted(items, key=lambda i: (i.activity, UUID(i.id)), reverse=True)]
    if order == "due":
        return [i.id for i in sorted(items, key=due_key)]
    if order == "waiting":
        far = datetime.max.replace(tzinfo=UTC)
        return [
            i.id for i in sorted(items, key=lambda i: (i.since is None, i.since or far, UUID(i.id)))
        ]
    return [i.id for i in sorted(items, key=lambda i: (-RANK[i.priority], *due_key(i)))]


async def _tied_items(w: SessionWorld) -> list[Item]:
    """Rows sharing every sort value in pairs, and rows with no due or since time."""
    now = datetime.now(UTC).replace(microsecond=123456)
    a, b = now - timedelta(hours=3), now - timedelta(hours=1)
    rows: list[Item] = []
    for since, due, priority, activity in (
        (a, b, "high", a),
        (a, b, "high", a),
        (a, now, "urgent", b),
        (b, now, "high", b),
        (b, now + timedelta(hours=1), "normal", now),
    ):
        rows.append(
            await _item(
                w,
                state="needs_staff",
                since=since,
                due=due,
                priority=priority,
                activity=activity,
            )
        )
    for priority, activity in (("high", a), ("high", a), ("low", now)):
        rows.append(await _item(w, state="waiting_customer", priority=priority, activity=activity))
    # Two of the asking person's own (Mine: one needs us, one waits on the customer) and two
    # resolved ones, so those tabs have a second page as well.
    rows.append(
        await _item(
            w,
            state="needs_staff",
            assignee=w.admin_id,
            since=b,
            due=now + timedelta(hours=2),
            activity=b,
        )
    )
    rows.append(await _item(w, state="waiting_customer", assignee=w.admin_id, activity=now))
    for _ in range(2):
        rows.append(await _item(w, state="resolved", activity=a))
    return rows


@pytest.mark.parametrize("order", ["activity", "due", "waiting", "priority"])
async def test_each_order_pages_every_row_once_through_ties(
    sessions: SessionWorld, order: str
) -> None:
    w = sessions
    rows = await _tied_items(w)
    needs = [r for r in rows if r.state == "needs_staff"]
    # The priority order is the Needs us tab's only (every other scope is 422, tested below).
    scope, query = (needs, "&state=needs_staff") if order == "priority" else (rows, "")
    expected = _ordered(scope, order)
    for limit in (50, 2, 1):
        assert await _walk(w, f"&order={order}{query}", limit=limit) == expected, limit
    # The queue tab alone: only the rows that need us, every one of them carrying both times.
    assert await _walk(w, f"&order={order}&state=needs_staff", limit=2) == _ordered(needs, order)


async def test_order_priority_is_only_for_the_needs_us_tab(sessions: SessionWorld) -> None:
    w = sessions
    await _tied_items(w)
    for query in (
        "&order=priority",
        "&order=priority&state=waiting_customer",
        "&order=priority&state=needs_staff&state=waiting_customer",
        "&order=priority&overdue=true",
        "&order=priority&waiting_over=5",
        "&order=priority&assignee=me",
    ):
        response = await w.admin.get(list_path(w, query))
        assert response.status_code == 422, query
        detail = response.json()["detail"]
        assert isinstance(detail, str) and "priority" in detail, (query, detail)
    allowed = await w.admin.get(list_path(w, "&order=priority&state=needs_staff&assignee=none"))
    assert allowed.status_code == 200, allowed.text
    with pytest.raises(ValidationError):
        QueueFilter(order="priority")
    with pytest.raises(ValidationError):
        QueueFilter(order="priority", state=["needs_staff", "snoozed"])
    assert QueueFilter(order="priority", state=["needs_staff"]).effective_order() == "priority"


async def test_needs_staff_only_picks_the_default_order() -> None:
    cases: list[tuple[dict[str, Any], bool]] = [
        ({}, False),
        ({"state": ["needs_staff"]}, True),
        ({"state": ["needs_staff", "needs_staff"]}, True),
        ({"state": ["needs_staff", "waiting_customer"]}, False),
        ({"state": ["resolved"]}, False),
        ({"overdue": True}, True),
        ({"waiting_over": 5}, True),
        ({"assignee": "none"}, False),
    ]
    for query, only in cases:
        flt = QueueFilter.model_validate(query)
        assert flt.needs_staff_only() is only, query
        assert flt.effective_order() == ("due" if only else "activity"), query


async def test_the_list_and_counts_plan_each_run_for_its_own_values(sessions: SessionWorld) -> None:
    """After five runs on one connection a prepared statement may switch to a generic plan, in
    which `$n IS NULL OR ...` is not folded and the partial indexes stop serving: the list and the
    counts force a custom plan for their transaction only."""
    w = sessions
    client, me = UUID(w.tenants[0]), UUID(w.admin_id)

    async def mode(conn: Any) -> str:
        row = await (await conn.execute("SHOW plan_cache_mode")).fetchone()
        return row["plan_cache_mode"]

    async with tx(w.pool, w.tenants[0]) as conn:
        assert await mode(conn) == "auto"
        await fetch_page(
            conn, QueueFilter(), client_id=client, user_id=me, search=None, cursor=None, limit=5
        )
        assert await mode(conn) == "force_custom_plan"
    async with tx(w.pool, w.tenants[0]) as conn:
        assert await mode(conn) == "auto"
        await fetch_counts(conn, client, me)
        assert await mode(conn) == "force_custom_plan"


async def test_the_default_order_is_due_for_needs_staff_and_activity_otherwise(
    sessions: SessionWorld,
) -> None:
    w = sessions
    rows = await _tied_items(w)
    needs = [r for r in rows if r.state == "needs_staff"]
    assert await _walk(w, "&state=needs_staff") == _ordered(needs, "due")
    assert await _walk(w) == _ordered(rows, "activity")
    waiting = [r for r in rows if r.state == "waiting_customer"]
    assert await _walk(w, "&state=waiting_customer") == _ordered(waiting, "activity")
    both = "&state=needs_staff&state=waiting_customer"
    queued = [r for r in rows if r.state in {"needs_staff", "waiting_customer"}]
    assert await _walk(w, both) == _ordered(queued, "activity")
    # Overdue and waiting-over keep only rows that need us, so they are due-first too.
    assert await _walk(w, "&waiting_over=1") == _ordered(needs, "due")
    late = await _walk(w, "&overdue=true")
    assert late == _ordered([r for r in needs if r.id in set(late)], "due")
    past = datetime.now(UTC) - timedelta(minutes=30)
    assert {r.id for r in needs if r.due is not None and r.due < past} <= set(late)


async def test_a_cursor_from_another_order_or_a_malformed_one_is_422(
    sessions: SessionWorld,
) -> None:
    w = sessions
    await _tied_items(w)
    page = await _page(w, "&limit=1&order=due")
    assert page["next_cursor"]
    other = await w.admin.get(
        list_path(w, "&order=activity&" + urlencode({"before": page["next_cursor"]}))
    )
    assert other.status_code == 422
    for bad in ("garbage", "!!!", "x" * 300, "YWN0aXZpdHl8eHx5"):
        response = await w.admin.get(list_path(w, "&" + urlencode({"before": bad})))
        assert response.status_code == 422, bad


async def test_a_bare_conversation_id_still_pages_the_activity_order(
    sessions: SessionWorld,
) -> None:
    w = sessions
    now = datetime.now(UTC)
    rows = [await _item(w, activity=now - timedelta(minutes=n)) for n in range(4)]
    expected = _ordered(rows, "activity")
    after_first = await _page(w, f"&before={expected[0]}")
    assert [i["id"] for i in after_first["items"]] == expected[1:]
    legacy_due = await w.admin.get(list_path(w, f"&order=due&before={expected[0]}"))
    assert legacy_due.status_code == 422
    # An id that is not one of this company's conversations gives an empty page, as before.
    assert (await _page(w, f"&before={uuid4()}"))["items"] == []
    foreign = await _item(w, 1)
    assert (await _page(w, f"&before={foreign.id}"))["items"] == []


async def _sandbox_and_staff_chat(w: SessionWorld) -> set[str]:
    """A sandbox conversation and two console chat ones, all forced into needs_staff."""
    now = datetime.now(UTC)
    web = await _item(w, state="needs_staff", channel="web")
    created = await w.mutate(
        w.admin,
        f"/console/v1/chat/conversations?client_id={w.tenants[0]}",
        {"request_key": str(uuid4())},
    )
    assert created.status_code in {200, 201}, created.text
    thread = created.json()["id"]
    await w.q(
        "UPDATE conversations SET channel = 'telegram', queue_status = 'needs_staff',"
        " queue_reason = 'manual', needs_staff_since = %s, due_at = %s,"
        " assignee_user_id = %s WHERE id = %s",
        now - timedelta(hours=2),
        now - timedelta(hours=1),
        UUID(w.admin_id),
        UUID(thread),
    )
    [participant] = await w.q(
        "SELECT contact_id FROM console_chat_participants WHERE client_id = %s",
        UUID(w.tenants[0]),
    )
    staff_contact = await _item(
        w,
        state="needs_staff",
        contact=participant["contact_id"],
        assignee=w.admin_id,
        since=now - timedelta(hours=3),
        due=now - timedelta(hours=2),
    )
    return {web.id, thread, staff_contact.id}


def _brute_counts(items: list[Item], me: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    needs = [i for i in items if i.state == "needs_staff"]
    return {
        "needs_us": len(needs),
        "mine": sum(
            1 for i in items if i.assignee == me and i.state in {"needs_staff", "waiting_customer"}
        ),
        "unassigned": sum(1 for i in needs if i.assignee is None),
        "overdue": sum(1 for i in needs if i.due is not None and i.due < now),
        "waiting_customer": sum(1 for i in items if i.state == "waiting_customer"),
        "snoozed": sum(1 for i in items if i.state == "snoozed"),
        "oldest_waiting_at": min((i.since for i in needs if i.since), default=None),
    }


def _parsed(counts: dict[str, Any]) -> dict[str, Any]:
    at = counts["oldest_waiting_at"]
    return {**counts, "oldest_waiting_at": datetime.fromisoformat(at) if at else None}


async def test_counts_match_a_brute_force_count_and_skip_excluded_rows(
    sessions: SessionWorld,
) -> None:
    w = sessions
    empty = await w.admin.get(counts_path(w))
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "needs_us": 0,
        "mine": 0,
        "unassigned": 0,
        "overdue": 0,
        "waiting_customer": 0,
        "snoozed": 0,
        "oldest_waiting_at": None,
    }
    items, staff, staff_client = await _queue_world(w)
    extra = [
        await _item(w, state="waiting_customer", assignee=w.admin_id),
        await _item(w, state="needs_staff", assignee=staff),
        await _item(
            w,
            state="needs_staff",
            since=datetime.now(UTC) - timedelta(days=2),
            due=datetime.now(UTC) - timedelta(days=1),
        ),
    ]
    excluded = await _sandbox_and_staff_chat(w)
    foreign = await _item(w, 1, state="needs_staff", since=datetime.now(UTC) - timedelta(days=9))
    seeded = [*items.values(), *extra]
    expected = _brute_counts(seeded, w.admin_id)
    counts = (await w.admin.get(counts_path(w))).json()
    assert _parsed(counts) == expected
    page = await _page(w)
    assert page["counts"] == counts
    assert set(await _walk(w, limit=50)) == {i.id for i in seeded}
    assert not excluded & set(await _walk(w, "&state=needs_staff", limit=50))
    assert foreign.id not in set(await _walk(w, limit=50))
    # Another member's "mine" is their own.
    staff_counts = _brute_counts(seeded, staff)
    assert _parsed((await staff_client.get(counts_path(w))).json()) == staff_counts
    # The needs-review count keeps its definition: live pending approvals only.
    assert page["needs_review_count"] == 0
    await w.q(
        "INSERT INTO approvals (client_id, conversation_id, trace_id, draft, reason, status,"
        " expires_at) VALUES (%s, %s, %s, 'synthetic draft', 'synthetic', 'pending',"
        " now() + interval '1 hour')",
        UUID(w.tenants[0]),
        UUID(items["bot"].id),
        uuid4(),
    )
    assert (await _page(w, "&state=resolved"))["needs_review_count"] == 1


async def test_another_company_never_sees_the_queue_and_read_only_is_403(
    sessions: SessionWorld,
) -> None:
    w = sessions
    mine = await _item(w, state="needs_staff")
    theirs = await _item(w, 1, state="needs_staff", assignee=None)
    foreign, _ = await w.member(tenant=w.tenants[1])
    own = await foreign.get(list_path(w, tenant=1))
    assert [i["id"] for i in own.json()["items"]] == [theirs.id]
    assert own.json()["counts"]["needs_us"] == 1
    assert (await foreign.get(list_path(w))).status_code == 403
    assert (await foreign.get(counts_path(w))).status_code == 403
    assert [i["id"] for i in (await _page(w))["items"]] == [mine.id]
    reader, _ = await w.member(role="read_only")
    assert (await reader.get(list_path(w))).status_code == 403
    assert (await reader.get(counts_path(w))).status_code == 403
    staff, _ = await w.member()
    assert (await staff.get(counts_path(w))).status_code == 200
    assert (await w.admin.get("/console/v1/inbox/queue/counts")).status_code == 400


async def test_rows_carry_the_queue_fields_and_the_assignee_name(sessions: SessionWorld) -> None:
    w = sessions
    staff = await _member_id(w, "Lia Synthetic")
    now = datetime.now(UTC)
    held = await _item(
        w,
        state="needs_staff",
        reason="held_reply",
        topic="booking",
        priority="high",
        assignee=staff,
        since=now - timedelta(minutes=20),
        due=now + timedelta(minutes=40),
    )
    free = await _item(w)
    by_id = {i["id"]: i for i in (await _page(w))["items"]}
    row = by_id[held.id]
    assert row["queue_status"] == "needs_staff" and row["queue_reason"] == "held_reply"
    assert row["topic"] == "booking" and row["priority"] == "high"
    assert row["assignee_id"] == staff and row["claimed_at"]
    assert datetime.fromisoformat(row["needs_staff_since"]) == held.since
    assert datetime.fromisoformat(row["due_at"]) == held.due
    assert row["snoozed_until"] is None
    assert row["assignee"] == {"id": staff, "display_name": "Lia Synthetic"}
    assert by_id[free.id]["assignee"] is None and by_id[free.id]["queue_status"] == "bot"
    detail = await w.admin.get(
        f"/console/v1/inbox/conversations/{held.id}?client_id={w.tenants[0]}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["assignee"] == {"id": staff, "display_name": "Lia Synthetic"}
    assert detail.json()["due_at"] == row["due_at"]
    # A member whose membership was switched off still shows by name on the items they hold.
    await w.q("UPDATE console_memberships SET is_enabled = false WHERE user_id = %s", UUID(staff))
    again = {i["id"]: i for i in (await _page(w))["items"]}
    assert again[held.id]["assignee"] == {"id": staff, "display_name": "Lia Synthetic"}


async def test_assignee_names_are_cosmetic_and_never_fail_the_read(sessions: SessionWorld) -> None:
    w = sessions
    staff = await _member_id(w, "Mo Synthetic")
    client = UUID(w.tenants[0])
    pool = w.app.state.identity_service.pool
    assert await assignee_names(pool, client, {UUID(staff)}) == {UUID(staff): "Mo Synthetic"}
    assert await assignee_names(pool, client, set()) == {}
    assert await assignee_names(None, client, {UUID(staff)}) == {}

    class Broken:
        def connection(self) -> Any:
            raise RuntimeError("synthetic-identity-outage")

    assert await assignee_names(cast(Any, Broken()), client, {UUID(staff)}) == {}
    # A user of another company only is not named.
    other = await _named(w, "Ned Synthetic", tenant=1)
    assert await assignee_names(pool, client, {UUID(other)}) == {}


async def test_a_takeover_receipt_carries_the_queue_ids_and_no_name(
    sessions: SessionWorld,
) -> None:
    w = sessions
    item = await _item(w, name="Nur Receipt")
    key = str(uuid4())
    taken = await w.mutate(
        w.admin,
        f"/console/v1/inbox/conversations/{item.id}/takeover?client_id={w.tenants[0]}",
        {"request_key": key, "expected_version": 0},
    )
    assert taken.status_code == 200, taken.text
    result = taken.json()["conversation"]
    assert result["queue_status"] == "needs_staff" and result["queue_reason"] == "manual"
    assert result["assignee_id"] == w.admin_id and result["claimed_at"]
    assert result["due_at"] and result["needs_staff_since"]
    assert "assignee" not in result
    assert "Synthetic Admin" not in taken.text
    [receipt] = await w.q(
        "SELECT result FROM console_inbox_actions WHERE client_id = %s AND request_key = %s",
        UUID(w.tenants[0]),
        UUID(key),
    )
    stored = receipt["result"]["conversation"]
    assert stored["assignee_id"] == w.admin_id and stored["queue_status"] == "needs_staff"
    assert "assignee" not in stored
    text = json.dumps(receipt["result"])
    assert "Synthetic Admin" not in text and "Nur Receipt" not in text
    # The list names the holder.
    [row] = (await _page(w))["items"]
    assert row["assignee"] == {"id": w.admin_id, "display_name": "Synthetic Admin"}


async def _handled(
    w: SessionWorld, item: Item, owned_by: str | None, muted_until: datetime | None = None
) -> None:
    """Set who owns the chat and until when the assistant is muted (the escalation write)."""
    await w.q(
        "UPDATE conversations SET owned_by = %s, ai_muted_until = %s WHERE id = %s",
        owned_by,
        muted_until,
        UUID(item.id),
    )


async def _handled_world(w: SessionWorld) -> tuple[dict[str, Item], str]:
    """One chat per way the assistant is, or is not, paused (synthetic values only)."""
    now = datetime.now(UTC)
    _, staff = await _signed_in(w, "Kay Synthetic")
    items = {
        # A person took over: owned by support with no end to the mute.
        "taken_over": await _item(w, state="waiting_customer", assignee=w.admin_id),
        "taken_over_needs_us": await _item(w, state="needs_staff", assignee=staff),
        "assistant": await _item(w, state="needs_staff", assignee=w.admin_id),
        "assistant_unset": await _item(w, state="needs_staff"),
        "expired_mute": await _item(w, state="needs_staff", assignee=w.admin_id),
        # The bot escalated: the escalation agent mutes the assistant for MUTE_FOR (a day), so
        # an escalated chat nobody has opened yet is "person" (plan decision 11).
        "escalated": await _item(w, state="needs_staff", reason="escalation"),
    }
    await _handled(w, items["taken_over"], "human_support")
    await _handled(w, items["taken_over_needs_us"], "human_support")
    await _handled(w, items["assistant"], "ai")
    await _handled(w, items["expired_mute"], "human_support", now - timedelta(minutes=1))
    mute = await w.q(
        "UPDATE conversations SET status = 'escalated', owned_by = 'human_support',"
        " ai_muted_until = now() + %s WHERE id = %s RETURNING ai_muted_until",
        MUTE_FOR,
        UUID(items["escalated"].id),
    )
    assert mute[0]["ai_muted_until"] > now + timedelta(hours=23)
    return items, staff


async def test_handled_by_picks_the_chats_the_assistant_is_or_is_not_paused_for(
    sessions: SessionWorld,
) -> None:
    w = sessions
    items, staff = await _handled_world(w)
    person = {"taken_over", "taken_over_needs_us", "escalated"}
    assistant = set(items) - person
    assert _names(items, await _ids(w, "&handled_by=person")) == person
    assert _names(items, await _ids(w, "&handled_by=assistant")) == assistant
    # A mute that has ended and a chat the assistant owns are the assistant's.
    assert "expired_mute" in assistant and "assistant_unset" in assistant
    assert _names(items, await _ids(w)) == set(items)
    # Each filter value agrees with the row's own human_muted flag.
    for value, flag in (("person", True), ("assistant", False)):
        rows = (await _page(w, f"&limit=50&handled_by={value}"))["items"]
        assert rows and {row["human_muted"] for row in rows} == {flag}, value
    # Combined with the other filters.
    cases: dict[str, set[str]] = {
        "&handled_by=person&state=needs_staff": {"taken_over_needs_us", "escalated"},
        "&handled_by=assistant&state=needs_staff": {
            "assistant",
            "assistant_unset",
            "expired_mute",
        },
        f"&handled_by=person&assignee={staff}": {"taken_over_needs_us"},
        "&handled_by=person&assignee=me": {"taken_over"},
        "&handled_by=person&assignee=none": {"escalated"},
        "&handled_by=assistant&assignee=me&state=needs_staff": {"assistant", "expired_mute"},
        "&handled_by=person&state=needs_staff&assignee=none": {"escalated"},
        "&handled_by=person&state=bot": set(),
        "&handled_by=person&reason=escalation&state=needs_staff": {
            "taken_over_needs_us",
            "escalated",
        },
    }
    for query, expected in cases.items():
        assert _names(items, await _ids(w, query)) == expected, query


async def test_handled_by_pages_through_the_cursor_in_every_order(sessions: SessionWorld) -> None:
    w = sessions
    items, _ = await _handled_world(w)
    person = {items[n].id for n in ("taken_over", "taken_over_needs_us", "escalated")}
    for order in ("activity", "due", "waiting"):
        walked = await _walk(w, f"&handled_by=person&order={order}", limit=2)
        assert set(walked) == person, order
    walked = await _walk(w, "&handled_by=person&state=needs_staff&order=priority", limit=1)
    assert set(walked) == {items["taken_over_needs_us"].id, items["escalated"].id}


async def test_handled_by_leaves_the_counts_unfiltered_and_the_search_alone(
    sessions: SessionWorld,
) -> None:
    w = sessions
    items, _ = await _handled_world(w)
    named = await _item(w, name="Handled Person Synthetic", state="needs_staff")
    items["named"] = named
    plain = (await w.admin.get(counts_path(w))).json()
    for value in ("person", "assistant"):
        page = await _page(w, f"&handled_by={value}")
        assert page["counts"] == plain, value
        assert (await w.admin.get(counts_path(w))).json() == plain
    # The search reads the contact only: the words of a handled_by value find nothing by
    # themselves, and the search narrows the filtered list like any other filter.
    assert _names(items, await _ids(w, "&q=person")) == {"named"}
    assert _names(items, await _ids(w, "&q=assistant")) == set()
    assert _names(items, await _ids(w, "&q=Handled&handled_by=assistant")) == {"named"}
    assert _names(items, await _ids(w, "&q=Handled&handled_by=person")) == set()


async def test_handled_by_is_a_stored_filter_and_a_list_parameter() -> None:
    assert QueueFilter(handled_by="person").handled_by == "person"
    assert QueueFilter().handled_by is None
    for bad in ("robot", "", "Person"):
        with pytest.raises(ValidationError):
            QueueFilter.model_validate({"handled_by": bad})
    base: dict[str, Any] = {
        "client_id": uuid4(),
        "user_id": uuid4(),
        "search": None,
        "cursor": None,
        "limit": 5,
    }
    assert list_params(QueueFilter(handled_by="assistant"), **base)["handled_by"] == "assistant"
    assert list_params(QueueFilter(), **base)["handled_by"] is None
    # It does not change which order is the default.
    assert QueueFilter(handled_by="person").effective_order() == "activity"


def _filter_block(order: str) -> str:
    text = (DB_SQL_DIR / "inbox" / f"list_queue_{order}.sql").read_text(encoding="utf-8")
    start, end = "-- queue filter: begin", "-- queue filter: end"
    assert text.count(start) == 1 and text.count(end) == 1, order
    return text[text.index(start) : text.index(end)]


async def test_the_four_list_queries_share_one_filter_block() -> None:
    blocks = {order: _filter_block(order) for order in QUEUE_FILES}
    first = blocks["activity"]
    assert "console_chat_threads" in first and "console_chat_participants" in first
    assert "c.channel IN ('telegram', 'whatsapp')" in first
    assert "%(handled_by)s::text" in first
    for order, block in blocks.items():
        assert block == first, order
    # csa_app reads contacts under row security and the ILIKE operators are not leakproof, so
    # no trigram index serves the search; the headers say what does.
    for order in QUEUE_FILES:
        text = (DB_SQL_DIR / "inbox" / f"list_queue_{order}.sql").read_text(encoding="utf-8")
        header = text.split("\nSELECT", 1)[0]
        assert "trigram indexes (0620) can serve" not in header, order
        assert "client_id index" in header, order
    assert not (DB_SQL_DIR / "inbox" / "list_conversations.sql").exists()


async def _plan(w: SessionWorld, path: str, params: dict[str, Any]) -> str:
    """The plan of a list or counts query as the app role, sorts and sequential scans priced out.

    A plan that still sorts or scans has no index that serves the query.
    """
    async with tx(w.pool, w.tenants[0]) as conn:
        await conn.execute("SET LOCAL enable_seqscan = off")
        await conn.execute("SET LOCAL enable_sort = off")
        cursor = await conn.execute(cast(Any, "EXPLAIN (COSTS OFF) " + db.q.load(path)), params)
        return "\n".join(row["QUERY PLAN"] for row in await cursor.fetchall())


TABS: list[tuple[str, dict[str, Any], str]] = [
    ("all", {}, "conversations_activity_idx"),
    ("needs us", {"state": ["needs_staff"]}, "conversations_queue_due_idx"),
    (
        "unassigned",
        {"state": ["needs_staff"], "assignee": "none"},
        "conversations_queue_due_idx",
    ),
    ("overdue", {"state": ["needs_staff"], "overdue": True}, "conversations_queue_due_idx"),
    (
        "mine",
        {"state": ["needs_staff", "waiting_customer"], "assignee": "me", "order": "due"},
        "conversations_queue_mine_idx",
    ),
    (
        "waiting",
        {"state": ["needs_staff"], "order": "waiting"},
        "conversations_queue_since_idx",
    ),
    ("resolved", {"state": ["resolved"]}, "conversations_activity_idx"),
]


async def test_each_tab_reads_its_index_without_a_sort(sessions: SessionWorld) -> None:
    w = sessions
    await _tied_items(w)
    for label, query, index in TABS:
        await _tab_plan(w, label, query, index)


async def _tab_plan(w: SessionWorld, label: str, query: dict[str, Any], index: str) -> None:
    flt = QueueFilter.model_validate(query)
    order = flt.effective_order()
    for cursor in (None, "next"):
        after = None
        if cursor:
            first = await _page(w, "&limit=1&" + urlencode({**query, "order": order}, doseq=True))
            assert first["next_cursor"], (label, "the tab needs a second page")
            after = decode_cursor(first["next_cursor"], order)
        params = list_params(
            flt,
            client_id=UUID(w.tenants[0]),
            user_id=UUID(w.admin_id),
            search=None,
            cursor=after,
            limit=21,
        )
        plan = await _plan(w, LIST_QUERIES[order], params)
        assert index in plan, (label, plan)
        assert "Sort" not in plan, (label, plan)
        if after is not None and (order == "activity" or flt.needs_staff_only()):
            # The cursor is an index condition (a seek), not a filter over the skipped rows:
            # every plan line holding the row comparison is an Index Cond line.
            compared = [line for line in plan.splitlines() if "ROW(" in line]
            assert compared and all("Index Cond:" in line for line in compared), (label, plan)


async def test_the_counts_read_the_partial_indexes(sessions: SessionWorld) -> None:
    w = sessions
    await _queue_world(w)
    plan = await _plan(
        w, "inbox/queue_counts.sql", {"client_id": UUID(w.tenants[0]), "user_id": UUID(w.admin_id)}
    )
    # Either needs_staff partial index serves that branch (both hold only needs_staff rows).
    assert "conversations_queue_due_idx" in plan or "conversations_queue_since_idx" in plan, plan
    # Each other branch reads one of its state's two partial indexes (both hold that state only).
    for state in ("waiting", "snoozed"):
        names = (f"conversations_queue_{state}_idx", f"conversations_queue_{state}_activity_idx")
        assert any(name in plan for name in names), plan
    assert "Seq Scan on conversations" not in plan, plan
    assert "conversations_activity_idx" not in plan, plan
