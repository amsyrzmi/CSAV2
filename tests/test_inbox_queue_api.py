"""The inbox queue's API surface (plan 1a, tasks 6 to 9): the snooze wake job, the company's inbox
settings and the list of people a conversation can be assigned to (disposable database, synthetic
data). Tasks 7 to 9 add their routes' tests to this file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from csa.console.inbox_settings import InboxSettingsIn, company_assignees
from csa.core import db
from csa.core.tenancy import tx
from csa.inbox_queue.settings import InboxSettings
from csa.jobs.clock import FakeClock
from csa.jobs.inbox_queue import run_inbox_snooze_wake
from csa.jobs.runner import JobRunner, build_default_registry
from fastapi import HTTPException

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
