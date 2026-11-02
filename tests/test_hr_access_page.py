"""The owner-only HR access page: people, batch changes and recent changes (disposable database)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4, uuid5

import httpx
import pytest
from csa.hr.access_switches import (
    SWITCH_IDS,
    SWITCHES,
    AccessRows,
    GrantRow,
    SwitchId,
    WorkflowRow,
    operations,
    rows,
    state,
)
from csa.hr.automation_models import AccessSave, PrivateAccessSave
from csa.hr.operations import HrOperations
from csa.hr.policy import HrPolicyError
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_hr_persistence import HrWorld, hr_database, workflow_grant, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

PEOPLE = "/console/v1/hr/access/people"
CHANGES = "/console/v1/hr/access/changes"
RECENT = "/console/v1/hr/access/recent"
OPERATIONS = "/console/v1/hr/operations"


async def people(world: HrWorld, who: str = "owner", **params: Any) -> dict[str, Any]:
    query = urlencode({k: str(v) for k, v in params.items() if v is not None})
    response = await world.call(who, "GET", PEOPLE + (f"?{query}" if query else ""))
    assert response.status_code == 200, response.text
    return response.json()


async def everyone(world: HrWorld) -> dict[UUID, dict[str, Any]]:
    found: dict[UUID, dict[str, Any]] = {}
    cursor = None
    while True:
        page = await people(world, cursor=cursor)
        found.update({UUID(item["user_id"]): item for item in page["items"]})
        cursor = page["next_cursor"]
        if cursor is None:
            return found


async def person(world: HrWorld, name: str) -> dict[str, Any]:
    return (await everyone(world))[world.users[name]]


async def post(
    world: HrWorld,
    changes: list[tuple[UUID, list[str]]] | list[tuple[UUID, list[str], list[str]]],
    *,
    version: str | None = None,
    batch_id: UUID | None = None,
    dry_run: bool = False,
) -> httpx.Response:
    """Each change is (user, on, base); base defaults to the person's current switches."""
    current = {user: item["on"] for user, item in (await everyone(world)).items()}
    body = {
        "batch_id": str(batch_id or uuid4()),
        "version": version or (await people(world))["version"],
        "dry_run": dry_run,
        "changes": [
            {
                "user_id": str(change[0]),
                "on": change[1],
                "base": change[2] if len(change) == 3 else current.get(change[0], []),
            }
            for change in changes
        ],
    }
    return await world.call("owner", "POST", CHANGES, body)


async def turn(world: HrWorld, name: str, on: list[str]) -> dict[str, Any]:
    response = await post(world, [(world.users[name], on)])
    assert response.status_code == 200, response.text
    return response.json()


async def stored(world: HrWorld, name: str) -> AccessRows:
    args = (world.tenant, world.users[name])
    grants = await world.q(
        "SELECT action,scope,fields FROM hr_grants WHERE client_id=%s AND user_id=%s", args
    )
    workflows = await world.q(
        "SELECT domain,action,scope FROM hr_workflow_grants WHERE client_id=%s AND user_id=%s", args
    )
    areas = await world.q(
        "SELECT area FROM hr_private_grants WHERE client_id=%s AND user_id=%s", args
    )
    return AccessRows(
        frozenset(GrantRow(g["action"], g["scope"], tuple(sorted(g["fields"]))) for g in grants),
        frozenset(WorkflowRow(w["domain"], w["action"], w["scope"]) for w in workflows),
        frozenset(a["area"] for a in areas),
    )


async def receipts(world: HrWorld) -> int:
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s", (world.tenant,)
    )
    return rows[0]["n"]


async def seed_members(world: HrWorld, prefix: str, count: int) -> None:
    """Members named "<prefix> 001" onwards; the email tag keeps other tests' users out."""
    tag = f"seed-{uuid4().hex}"
    await world.q(
        "INSERT INTO console_users(id,email,display_name) "
        "SELECT gen_random_uuid(), %s || '-' || i || '@example.test', "
        "%s || ' ' || lpad(i::text, 3, '0') FROM generate_series(1, %s) AS i",
        (tag, prefix, count),
    )
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) "
        "SELECT id, %s, 'tenant_staff' FROM console_users WHERE email LIKE %s",
        (world.tenant, f"{tag}-%"),
    )


async def clear_operator(world: HrWorld) -> None:
    """The operator starts with no rows, so the first save shows only what it turned on."""
    await world.q(
        "DELETE FROM hr_grants WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["operator"]),
    )


def result(world: HrWorld, name: str, outcome: str, reason: str | None = None) -> dict[str, Any]:
    return {"user_id": str(world.users[name]), "outcome": outcome, "reason": reason}


async def test_people_page_is_owner_only(world: HrWorld) -> None:
    page = await people(world)
    assert {UUID(item["user_id"]) for item in page["items"]} == set(world.users.values())
    assert set(page["counts"]) == set(SWITCH_IDS) and page["version"]
    owner = next(i for i in page["items"] if i["user_id"] == str(world.users["owner"]))
    assert owner["is_owner"] is True and owner["custom"]
    for name in ("admin", "staff"):
        response = await world.call(name, "GET", PEOPLE)
        assert response.status_code == 404 and response.json()["detail"] == "resource_unavailable"
        response = await world.call(name, "GET", RECENT)
        assert response.status_code == 404 and response.json()["detail"] == "resource_unavailable"
        # A bad cursor never tells a non-owner more than "unavailable".
        for path in (PEOPLE, RECENT):
            response = await world.call(name, "GET", f"{path}?cursor=bad")
            assert response.status_code == 404
    # The platform user is the HR owner of the other tenant, and asks about this one.
    await world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_admin')",
        (world.users["platform"], world.other_tenant),
    )
    await world.q(
        "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
        (world.other_tenant, world.users["platform"], world.users["platform"]),
    )
    response = await world.call("platform", "GET", f"{PEOPLE}?client_id={world.tenant}")
    assert response.status_code == 404 and response.json()["detail"] == "resource_unavailable"
    response = await world.call(
        "admin",
        "POST",
        CHANGES,
        {
            "batch_id": str(uuid4()),
            "version": page["version"],
            "changes": [{"user_id": str(world.users["staff"]), "on": ["directory"], "base": []}],
        },
    )
    assert response.status_code == 404 and response.json()["detail"] == "resource_unavailable"
    # Unknown query keys are refused like every other HR read.
    response = await world.call("owner", "GET", f"{PEOPLE}?limit=5")
    assert response.status_code == 400


async def test_fifty_person_batch_fits_the_body_limit(world: HrWorld) -> None:
    version = (await people(world))["version"]
    body = {
        "batch_id": str(uuid4()),
        "version": version,
        "dry_run": True,
        "changes": [
            {"user_id": str(uuid4()), "on": list(SWITCH_IDS), "base": list(SWITCH_IDS)}
            for _ in range(50)
        ],
    }
    assert len(json.dumps(body, separators=(",", ":"))) > 8192
    response = await world.call("owner", "POST", CHANGES, body)
    assert response.status_code == 200, response.text
    assert len(response.json()["results"]) == 50
    extra = {"user_id": str(uuid4()), "on": [], "base": []}
    too_many = {**body, "changes": [*body["changes"], extra]}
    assert (await world.call("owner", "POST", CHANGES, too_many)).status_code == 422
    twice = {**body, "changes": body["changes"][:1] * 2}
    assert (await world.call("owner", "POST", CHANGES, twice)).status_code == 422


@pytest.mark.parametrize("switch", SWITCH_IDS)
async def test_switch_on_then_off_writes_exact_rows(world: HrWorld, switch: SwitchId) -> None:
    await clear_operator(world)
    # A row no switch describes, which every save must keep.
    await workflow_grant(world, "operator", "leave", "read", "client_owner")
    start = await stored(world, "operator")
    assert (await person(world, "operator"))["on"] == []

    on = await turn(world, "operator", [switch])
    assert on["results"] == [result(world, "operator", "saved")]
    assert await stored(world, "operator") == start.union(SWITCHES[switch])
    assert switch in (await person(world, "operator"))["on"]

    off = await turn(world, "operator", [])
    assert off["results"] == [result(world, "operator", "saved")]
    assert await stored(world, "operator") == start


async def test_uncovered_rows_survive_every_save(world: HrWorld) -> None:
    partial = {
        "operation": "access.save",
        "user_id": str(world.users["operator"]),
        "grants": [
            {"action": "directory.read", "scope": "self", "fields": ["display_name"]},
            {"action": "training.read", "scope": "hr_operator", "fields": []},
        ],
        "workflows": [],
    }
    response = await world.call("owner", "POST", OPERATIONS, partial)
    assert response.status_code == 200, response.text
    seeded = GrantRow("directory.read", "self", ("display_name",))
    assert seeded in (await stored(world, "operator")).grants

    await turn(world, "operator", ["approves_team"])
    assert seeded in (await stored(world, "operator")).grants
    await turn(world, "operator", [])
    assert seeded in (await stored(world, "operator")).grants
    operator = await person(world, "operator")
    assert operator["on"] == [] and operator["custom"]
    assert "Sees their own details: name" in operator["custom"]


async def test_owner_saving_own_switches_keeps_owner_decisions(world: HrWorld) -> None:
    for domain in ("recruitment", "attendance", "leave"):
        await workflow_grant(world, "owner", domain, "decide", "client_owner")
    decisions = AccessRows(
        frozenset({GrantRow("training.decide", "client_owner", ())}),
        frozenset(
            WorkflowRow(d, "decide", "client_owner") for d in ("attendance", "leave", "recruitment")
        ),
        frozenset(),
    )
    assert decisions.issubset(await stored(world, "owner"))
    owner = await person(world, "owner")
    assert "own_requests" not in owner["on"]
    assert "Records client-owner decisions on leave" in owner["custom"]

    await turn(world, "owner", [*owner["on"], "own_requests"])
    after_on = await stored(world, "owner")
    assert decisions.issubset(after_on) and SWITCHES["own_requests"].issubset(after_on)
    await turn(world, "owner", owner["on"])
    assert decisions.issubset(await stored(world, "owner"))
    assert "Records client-owner decisions on leave" in (await person(world, "owner"))["custom"]


async def test_stale_version_is_409_and_writes_nothing(world: HrWorld) -> None:
    version = (await people(world))["version"]
    other = {
        "operation": "access.save",
        "user_id": str(world.users["staff"]),
        "grants": [{"action": "training.read", "scope": "self", "fields": []}],
        "workflows": [],
    }
    assert (await world.call("owner", "POST", OPERATIONS, other)).status_code == 200
    before, count = await stored(world, "operator"), await receipts(world)

    for dry_run in (False, True):
        response = await post(
            world, [(world.users["operator"], ["approves_team"])], version=version, dry_run=dry_run
        )
        assert response.status_code == 409 and response.json()["detail"] == "access_changed"
    assert await stored(world, "operator") == before and await receipts(world) == count


async def test_replayed_batch_returns_saved_without_reapplying(world: HrWorld) -> None:
    batch = uuid4()
    changes = [
        (world.users["operator"], ["approves_team", "payroll_prepare"]),
        (world.users["staff"], (await person(world, "staff"))["on"]),
    ]
    version = (await people(world))["version"]
    first = await post(world, changes, version=version, batch_id=batch)
    assert first.status_code == 200, first.text
    assert first.json()["results"] == [
        result(world, "operator", "saved"),
        result(world, "staff", "unchanged"),
    ]
    count, rows = await receipts(world), await stored(world, "operator")
    assert first.json()["version"] != version

    # The same body again: the old version is now stale, but the batch is a replay.
    second = await post(world, changes, version=version, batch_id=batch)
    assert second.status_code == 200, second.text
    assert second.json()["results"] == first.json()["results"]
    assert second.json()["version"] == first.json()["version"]
    assert await receipts(world) == count and await stored(world, "operator") == rows


async def test_unchanged_people_write_nothing(world: HrWorld) -> None:
    count = await receipts(world)
    everybody = await everyone(world)
    changes = [(user, item["on"]) for user, item in everybody.items()]
    response = await post(world, changes)
    assert response.status_code == 200, response.text
    assert {r["outcome"] for r in response.json()["results"]} == {"unchanged"}
    assert await receipts(world) == count


async def test_batch_reports_a_refusal_per_person_and_saves_the_rest(world: HrWorld) -> None:
    stranger = uuid4()
    response = await post(
        world, [(stranger, ["directory"]), (world.users["operator"], ["approves_team"])]
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"] == [
        {"user_id": str(stranger), "outcome": "refused", "reason": "not_a_member"},
        result(world, "operator", "saved"),
    ]
    assert "approves_team" in (await person(world, "operator"))["on"]


async def test_change_from_a_stale_base_is_refused_and_the_rest_save(world: HrWorld) -> None:
    await clear_operator(world)
    loaded = (await person(world, "operator"))["on"]
    # Another tab turns a switch on for the operator; this page still shows the old switches.
    await turn(world, "operator", ["approves_team"])
    staff = await person(world, "staff")
    assert "payroll_review" not in staff["on"]
    before, count = await stored(world, "operator"), await receipts(world)
    changes = [
        (world.users["operator"], ["payroll_prepare"], loaded),
        (world.users["staff"], [*staff["on"], "payroll_review"], staff["on"]),
    ]
    want = [
        result(world, "operator", "refused", "changed_since_load"),
        result(world, "staff", "saved"),
    ]
    dry = await post(world, changes, dry_run=True)
    assert dry.status_code == 200, dry.text
    assert dry.json()["results"] == want
    assert await receipts(world) == count
    response = await post(world, changes)
    assert response.status_code == 200, response.text
    assert response.json()["results"] == want
    assert await stored(world, "operator") == before
    assert "payroll_review" in (await person(world, "staff"))["on"]


async def record_access_save(world: HrWorld, name: str, batch: UUID, on: list[SwitchId]) -> None:
    """Lands only the access.save of a batch's change, as if the area changes after it were lost."""
    user, current = world.users[name], await stored(world, name)
    ops = operations(user, current, rows(frozenset(on), state(current).uncovered))
    assert any(isinstance(op, PrivateAccessSave) for op in ops)
    body = next(op for op in ops if isinstance(op, AccessSave)).model_dump(mode="json")
    key = uuid5(batch, f"{user}:access")
    response = await world.call("owner", "POST", OPERATIONS, body, key=key)
    assert response.status_code == 200, response.text


async def test_retry_finishes_a_person_whose_area_change_did_not_land(world: HrWorld) -> None:
    await clear_operator(world)
    batch, version = uuid4(), (await people(world))["version"]
    loaded = (await person(world, "operator"))["on"]
    staff = (await person(world, "staff"))["on"]
    await record_access_save(world, "operator", batch, ["payroll_prepare"])
    assert "payroll" not in (await stored(world, "operator")).areas
    changes = [
        (world.users["operator"], ["payroll_prepare"], loaded),
        # Someone with no receipt in this batch is still checked against what the page loaded.
        (world.users["staff"], [*staff, "payroll_review"], [*staff, "attendance"]),
    ]
    want = [
        result(world, "operator", "saved"),
        result(world, "staff", "refused", "changed_since_load"),
    ]
    retry = await post(world, changes, version=version, batch_id=batch)
    assert retry.status_code == 200, retry.text
    assert retry.json()["results"] == want
    assert SWITCHES["payroll_prepare"].issubset(await stored(world, "operator"))
    assert "payroll_review" not in (await person(world, "staff"))["on"]
    count, saved = await receipts(world), await stored(world, "operator")
    again = await post(world, changes, version=version, batch_id=batch)
    assert again.status_code == 200, again.text
    assert again.json()["results"] == want
    assert await receipts(world) == count and await stored(world, "operator") == saved


async def test_retry_with_a_different_change_is_refused(world: HrWorld) -> None:
    await clear_operator(world)
    batch, version = uuid4(), (await people(world))["version"]
    loaded = (await person(world, "operator"))["on"]
    await record_access_save(world, "operator", batch, ["payroll_prepare"])
    before, count = await stored(world, "operator"), await receipts(world)
    response = await post(
        world,
        [(world.users["operator"], ["approves_team"], loaded)],
        version=version,
        batch_id=batch,
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"] == [result(world, "operator", "refused", "access_changed")]
    assert await stored(world, "operator") == before and await receipts(world) == count


async def test_a_save_that_stops_halfway_is_reported_partly_saved(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    await clear_operator(world)
    real = HrOperations.apply

    async def refuse_areas(self: HrOperations, session: Any, body: Any, key: UUID) -> Any:
        if isinstance(body, PrivateAccessSave):
            raise HrPolicyError("synthetic_refusal")
        return await real(self, session, body, key)

    monkeypatch.setattr(HrOperations, "apply", refuse_areas)
    staff = (await person(world, "staff"))["on"]
    assert "approves_team" not in staff
    response = await post(
        world,
        [
            (world.users["operator"], ["payroll_prepare"]),
            (world.users["staff"], [*staff, "approves_team"]),
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"] == [
        result(world, "operator", "refused", "partly_saved"),
        result(world, "staff", "saved"),
    ]
    operator = await stored(world, "operator")
    assert SWITCHES["payroll_prepare"].workflows <= operator.workflows
    assert "payroll" not in operator.areas


async def test_dry_run_returns_warnings_and_writes_nothing(world: HrWorld) -> None:
    turned = await turn(world, "manager", ["approves_team"])
    staff_employee = str(world.employees["staff"])

    def no_approver(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            w for w in warnings if w["kind"] == "no_approver" and w["employee_id"] == staff_employee
        ]

    assert no_approver(turned["warnings"]) == []
    before, count = await stored(world, "manager"), await receipts(world)

    response = await post(world, [(world.users["manager"], [])], dry_run=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"] == [result(world, "manager", "saved")]
    assert no_approver(body["warnings"]) == [
        {
            "kind": "no_approver",
            "user_id": None,
            "employee_id": staff_employee,
            "user_name": None,
            "employee_name": "Synthetic Staff",
            "manager_name": "Synthetic Staff",
        }
    ]
    assert body["version"] == turned["version"]
    assert await stored(world, "manager") == before and await receipts(world) == count
    assert no_approver((await people(world))["warnings"]) == []


async def test_people_pages_with_search_return_each_match_once(world: HrWorld) -> None:
    await world.q(
        "UPDATE console_users SET display_name='Fixture member' WHERE id = ANY(%s)",
        (list(world.users.values()),),
    )
    await seed_members(world, "Synthetic", 120)
    seen: list[str] = []
    pages, cursor = 0, None
    while True:
        page = await people(world, q="synthetic", cursor=cursor)
        pages += 1
        assert len(page["items"]) <= 50
        seen += [item["user_id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and len(seen) == len(set(seen)) == 120
    assert (await people(world, q="SYNTHETIC 007"))["items"][0]["display_name"] == "Synthetic 007"
    bad = await world.call("owner", "GET", f"{PEOPLE}?cursor=not-a-cursor")
    assert bad.status_code == 409 and bad.json()["detail"] == "invalid_cursor"


async def test_counts_and_has_filter_cover_the_whole_client(world: HrWorld) -> None:
    await seed_members(world, "Bulk", 60)  # sorts before "Synthetic Staff"
    response = await post(
        world,
        [(world.users["manager"], ["approves_team"]), (world.users["operator"], ["approves_team"])],
    )
    assert response.status_code == 200, response.text
    first = await people(world)
    assert len(first["items"]) == 50 and first["next_cursor"] is not None
    assert all(item["display_name"].startswith("Bulk") for item in first["items"])
    assert first["counts"]["approves_team"] == 2 and first["counts"]["payroll_review"] == 0
    approvers = await people(world, has="approves_team")
    assert {item["user_id"] for item in approvers["items"]} == {
        str(world.users["manager"]),
        str(world.users["operator"]),
    }
    assert approvers["counts"] == first["counts"] and approvers["version"] == first["version"]
    # "has" also matches a switch that is on only because another switch includes it.
    await turn(world, "staff", ["hr_team"])
    directory = await people(world, has="directory")
    assert str(world.users["staff"]) in {item["user_id"] for item in directory["items"]}
    team = await people(world, team="Learning")
    assert {item["user_id"] for item in team["items"]} == {
        str(world.users[n]) for n in ("staff", "manager", "owner", "outsider")
    }
    reports = await people(world, reports_to=world.employees["manager"])
    assert {item["user_id"] for item in reports["items"]} == {
        str(world.users[n]) for n in ("staff", "owner")
    }


async def recent(world: HrWorld, cursor: str | None = None) -> dict[str, Any]:
    response = await world.call("owner", "GET", RECENT + (f"?cursor={cursor}" if cursor else ""))
    assert response.status_code == 200, response.text
    return response.json()


async def test_recent_changes_show_what_changed(world: HrWorld) -> None:
    await clear_operator(world)
    await turn(world, "operator", ["approves_team"])
    await turn(world, "operator", ["approves_team", "payroll_prepare"])
    items = (await recent(world))["items"]
    saves = [item for item in items if item["area"] is None]
    assert [s["turned_on"] for s in saves] == [["payroll_prepare"], ["approves_team"]]
    assert [s["turned_off"] for s in saves] == [[], []]
    newest = saves[0]
    assert newest["actor_user_id"] == str(world.users["owner"])
    assert newest["actor_name"] == "Synthetic Staff"
    assert newest["target_user_id"] == str(world.users["operator"])
    assert newest["target_name"] == "Synthetic Staff"
    area = items[0]
    assert (area["area"], area["area_enabled"]) == ("payroll", True)
    assert area["turned_on"] == [] and area["target_user_id"] == str(world.users["operator"])
    assert items == sorted(items, key=lambda i: i["occurred_at"], reverse=True)

    await turn(world, "operator", ["payroll_prepare"])
    newest = (await recent(world))["items"]
    latest = next(item for item in newest if item["area"] is None)
    assert latest["turned_on"] == [] and latest["turned_off"] == ["approves_team"]


async def test_recent_changes_find_the_previous_save_on_an_earlier_page(world: HrWorld) -> None:
    await clear_operator(world)
    await turn(world, "operator", ["approves_team"])
    # Fifty other events land between the operator's two saves.
    for _ in range(50):
        key = uuid4()
        details = {
            "operation": "private_access.save",
            "user_id": str(world.users["staff"]),
            "area": "recruitment",
            "enabled": True,
        }
        await world.q(
            "INSERT INTO hr_operations(client_id,subject_id,request_key,operation,fingerprint,"
            "receipt) VALUES (%s,%s,%s,'private_access.save',%s,%s)",
            (world.tenant, world.users["owner"], key, "0" * 64, Jsonb({"status": "saved"})),
        )
        await world.q(
            "INSERT INTO hr_authority_events(client_id,subject_id,request_key,operation,details) "
            "VALUES (%s,%s,%s,'private_access.save',%s)",
            (world.tenant, world.users["owner"], key, Jsonb(details)),
        )
    await turn(world, "operator", ["approves_team", "payroll_prepare"])

    first = await recent(world)
    assert len(first["items"]) == 50 and first["next_cursor"] is not None
    saves = [item for item in first["items"] if item["area"] is None]
    assert [s["turned_on"] for s in saves] == [["payroll_prepare"]]
    second = await recent(world, first["next_cursor"])
    assert len(second["items"]) == 3 and second["next_cursor"] is None
    assert [i["turned_on"] for i in second["items"] if i["area"] is None] == [["approves_team"]]
    assert len(first["items"]) + len(second["items"]) == 53
    newest_first = {i["occurred_at"] for i in first["items"]}
    assert all(i["occurred_at"] < min(newest_first) for i in second["items"])
    bad = await world.call("owner", "GET", f"{RECENT}?cursor=bad")
    assert bad.status_code == 409 and bad.json()["detail"] == "invalid_cursor"


async def test_recent_changes_show_disabled_people_without_names(world: HrWorld) -> None:
    await clear_operator(world)
    await turn(world, "operator", ["approves_team"])
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["operator"]),
    )
    items = (await recent(world))["items"]
    event = next(i for i in items if i["target_user_id"] == str(world.users["operator"]))
    assert event["target_name"] is None and event["actor_name"] == "Synthetic Staff"
    assert event["turned_on"] == ["approves_team"]


async def test_recent_changes_use_an_invitation_as_the_baseline(world: HrWorld) -> None:
    await clear_operator(world)
    key = uuid4()
    hr_team = ["attendance", "directory", "hr_team", "own_requests", "recruitment"]
    details = {
        "user_id": str(world.users["operator"]),
        "preset": "hr_team",
        "on": hr_team,
        "added": hr_team,
        "chosen_by": str(world.users["owner"]),
    }
    await world.q(
        "INSERT INTO hr_operations(client_id,subject_id,request_key,operation,fingerprint,"
        "receipt) VALUES (%s,%s,%s,'invitation_access',%s,%s)",
        (world.tenant, world.users["operator"], key, "0" * 64, Jsonb({"status": "saved"})),
    )
    await world.q(
        "INSERT INTO hr_authority_events(client_id,subject_id,request_key,operation,details) "
        "VALUES (%s,%s,%s,'invitation_access',%s)",
        (world.tenant, world.users["operator"], key, Jsonb(details)),
    )
    await turn(world, "operator", ["directory", "own_requests", "hr_team", "approves_team"])
    items = (await recent(world))["items"]
    save, invitation = [i for i in items if i["target_user_id"] == str(world.users["operator"])]
    assert save["source"] == "page" and save["preset"] is None
    # Areas are not part of an access.save, so the invitation's area switches are not "turned off".
    assert save["turned_on"] == ["approves_team"] and save["turned_off"] == []
    assert invitation["source"] == "invitation" and invitation["preset"] == "hr_team"
    assert invitation["actor_user_id"] == str(world.users["owner"])
    assert invitation["actor_name"] == "Synthetic Staff"
    assert invitation["turned_on"] == [
        "directory",
        "own_requests",
        "hr_team",
        "recruitment",
        "attendance",
    ]
    assert invitation["turned_off"] == [] and invitation["area"] is None


async def test_manage_people_switch_saves_and_counts(world: HrWorld) -> None:
    await clear_operator(world)
    await turn(world, "operator", ["directory"])
    before = await stored(world, "operator")
    assert (await people(world))["counts"]["manage_people"] == 0

    on = await turn(world, "operator", ["directory", "manage_people"])
    assert on["results"] == [result(world, "operator", "saved")]
    after = await stored(world, "operator")
    assert after.minus(before) == SWITCHES["manage_people"] and before.issubset(after)
    assert after.grants - before.grants == {GrantRow("people.manage", "hr_operator", ())}
    page = await people(world)
    assert page["counts"]["manage_people"] == 1
    operator = await person(world, "operator")
    assert sorted(operator["on"]) == ["directory", "manage_people"] and operator["custom"] == []
    holders = await people(world, has="manage_people")
    assert [item["user_id"] for item in holders["items"]] == [str(world.users["operator"])]
    # The holder manages people, and still cannot open the access page.
    assert (await world.call("operator", "GET", PEOPLE)).status_code == 404
    events = (await world.call("owner", "GET", RECENT)).json()["items"]
    assert (events[0]["turned_on"], events[0]["turned_off"]) == (["manage_people"], [])

    off = await turn(world, "operator", ["directory"])
    assert off["results"] == [result(world, "operator", "saved")]
    assert await stored(world, "operator") == before
    assert (await people(world))["counts"]["manage_people"] == 0
