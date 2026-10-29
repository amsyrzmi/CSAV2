"""HR access chosen on an invitation: applied on acceptance, or left for the HR owner."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.core.tenancy import tx
from csa.hr.access_switches import (
    EMPTY,
    PRESETS,
    SWITCHES,
    AccessRows,
    GrantRow,
    WorkflowRow,
    rows,
    switch_set,
)
from csa.hr.invitation_access import InvitationAccess
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_hr_persistence import ORIGIN, HrWorld, hr_database, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

PASSWORD = "synthetic-invitee-password"
INVITATIONS = "/console/v1/hr/access/invitations"
RECENT = "/console/v1/hr/access/recent"
PEOPLE = "/console/v1/hr/access/people"
CHANGES = "/console/v1/hr/access/changes"


async def invite(
    world: HrWorld, who: str, preset: str | None, *, role: str = "tenant_staff"
) -> dict[str, Any]:
    path = "/console/v1/admin/invites" if who == "platform" else "/console/v1/team/invites"
    body = {
        "email": f"invitee-{uuid4().hex}@example.test",
        "client_id": str(world.tenant),
        "role": role,
        "display_name": "Synthetic Invitee",
        "hr_preset": preset,
    }
    response = await world.call(who, "POST", path, body)
    assert response.status_code == 201, response.text
    assert response.json()["delivery"] == "manual" and response.json()["token"]
    return response.json()


async def accept(world: HrWorld, raw: str) -> UUID:
    """Accept through the route, which applies the invitation's HR access after it commits."""
    csrf = f"synthetic-csrf-{uuid4()}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app), base_url=ORIGIN
    ) as browser:
        browser.cookies.set("csa_csrf", csrf)
        response = await browser.post(
            "/console/v1/invites/accept",
            json={"token": raw, "password": PASSWORD},
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
    assert response.status_code == 200, response.text
    return UUID(response.json()["user"]["id"])


async def accept_only(world: HrWorld, raw: str) -> tuple[UUID, UUID]:
    """Accept through the identity service alone: nothing is applied yet."""
    actor, _raw, _csrf, token_id, client_id = await world.app.state.identity_service.accept_token(
        raw, PASSWORD
    )
    assert client_id == world.tenant
    return actor.user_id, token_id


async def held(world: HrWorld, user: UUID) -> AccessRows:
    args = (world.tenant, user)
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


async def records(world: HrWorld, token_id: UUID | str) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT o.subject_id,o.operation,e.details FROM hr_operations o "
        "JOIN hr_authority_events e USING (client_id,subject_id,request_key) "
        "WHERE o.client_id=%s AND o.request_key=%s",
        (world.tenant, UUID(str(token_id))),
    )


async def count(world: HrWorld) -> tuple[int, int]:
    ops = await world.q(
        "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s", (world.tenant,)
    )
    events = await world.q(
        "SELECT count(*) AS n FROM hr_authority_events WHERE client_id=%s", (world.tenant,)
    )
    return ops[0]["n"], events[0]["n"]


def payload(found: AccessRows) -> dict[str, Any]:
    return {
        "grants": [
            {"action": g.action, "scope": g.scope, "fields": list(g.fields)}
            for g in sorted(found.grants)
        ],
        "workflows": [
            {"domain": w.domain, "action": w.action, "scope": w.scope}
            for w in sorted(found.workflows)
        ],
        "areas": sorted(found.areas),
    }


async def direct_apply(
    world: HrWorld,
    token_id: UUID,
    body: dict[str, Any],
    *,
    override: bool | None = False,
    hr_user: UUID | None = None,
) -> None:
    async with tx(world.service.pool, str(world.tenant)) as conn:
        if hr_user is not None:
            await conn.execute("SELECT set_config('app.hr_user_id', %s, true)", (str(hr_user),))
        await conn.execute(
            "SELECT public.hr_apply_invitation_access(%s,%s,%s,%s::text[],%s::text[],%s::boolean)",
            (world.tenant, token_id, Jsonb(body), [], [], override),
        )


async def preset_row(world: HrWorld, tenant: UUID, client: UUID, token_id: UUID) -> list[Any]:
    async with tx(world.service.pool, str(tenant)) as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "SELECT * FROM public.hr_invitation_preset(%s,%s)", (client, token_id)
            )
            return await cursor.fetchall()


async def test_staff_invitation_applies_on_accept(world: HrWorld) -> None:
    issued = await invite(world, "admin", "staff")
    member, token_id = await accept_only(world, issued["token"])
    assert str(token_id) == issued["id"]
    assert await held(world, member) == EMPTY
    outcome = await InvitationAccess(world.service).apply_on_accept(world.tenant, token_id)
    assert outcome == "applied"
    assert await held(world, member) == rows(PRESETS["staff"], EMPTY)
    [record] = await records(world, token_id)
    assert record["subject_id"] == member and record["operation"] == "invitation_access"
    assert record["details"] == {
        "user_id": str(member),
        "preset": "staff",
        "on": sorted(PRESETS["staff"]),
        "added": sorted(PRESETS["staff"]),
        "chosen_by": str(world.users["admin"]),
    }


async def test_apply_is_idempotent_and_refuses_twice(world: HrWorld) -> None:
    issued = await invite(world, "admin", "staff")
    member = await accept(world, issued["token"])
    assert await held(world, member) == rows(PRESETS["staff"], EMPTY)
    before = await count(world)
    access = InvitationAccess(world.service)
    assert await access.apply_on_accept(world.tenant, UUID(issued["id"])) == "none"
    with pytest.raises(psycopg.Error, match="hr_invitation_applied"):
        await direct_apply(world, UUID(issued["id"]), payload(rows(PRESETS["staff"], EMPTY)))
    assert await count(world) == before
    assert len(await records(world, issued["id"])) == 1


async def test_renewed_manager_invitation_applies_for_the_original_owner(world: HrWorld) -> None:
    issued = await invite(world, "owner", "manager")
    renewed = await world.call("admin", "POST", f"/console/v1/admin/invites/{issued['id']}/renew")
    assert renewed.status_code == 201, renewed.text
    member = await accept(world, renewed.json()["token"])
    assert await held(world, member) == rows(PRESETS["manager"], EMPTY)
    [record] = await records(world, renewed.json()["id"])
    assert record["subject_id"] == member
    assert record["details"]["preset"] == "manager"
    assert record["details"]["chosen_by"] == str(world.users["owner"])


async def test_owner_payroll_invitation_applies_its_area(world: HrWorld) -> None:
    issued = await invite(world, "owner", "payroll_preparer")
    member = await accept(world, issued["token"])
    assert await held(world, member) == rows(PRESETS["payroll_preparer"], EMPTY)
    [area] = await world.q(
        "SELECT area,approved_by FROM hr_private_grants WHERE client_id=%s AND user_id=%s",
        (world.tenant, member),
    )
    assert area == {"area": "payroll", "approved_by": world.users["owner"]}


async def test_chooser_no_longer_owner_leaves_it_pending_and_owner_apply_works(
    world: HrWorld,
) -> None:
    manager = await invite(world, "owner", "manager")
    team = await invite(world, "owner", "hr_team")
    await world.q(
        "UPDATE hr_client_owners SET user_id=%s WHERE client_id=%s",
        (world.users["admin"], world.tenant),
    )
    first = await accept(world, manager["token"])
    second = await accept(world, team["token"])
    assert await held(world, first) == EMPTY and await held(world, second) == EMPTY
    access = InvitationAccess(world.service)
    assert await access.apply_on_accept(world.tenant, UUID(manager["id"])) == "pending"
    assert await records(world, manager["id"]) == []
    # The database refuses a non-Staff preset from a chooser who is no longer the owner.
    with pytest.raises(psycopg.Error, match="hr_invitation_owner_required"):
        await direct_apply(world, UUID(manager["id"]), payload(rows(PRESETS["manager"], EMPTY)))

    listed = await world.call("admin", "GET", INVITATIONS)
    assert listed.status_code == 200, listed.text
    items = {item["token_id"]: item for item in listed.json()["items"]}
    assert set(items) == {manager["id"], team["id"]}
    assert items[manager["id"]]["user_id"] == str(first)
    assert items[manager["id"]]["display_name"] == "Synthetic Invitee"
    assert items[manager["id"]]["preset"] == "manager"
    assert items[manager["id"]]["chosen_by"] == str(world.users["owner"])
    assert items[manager["id"]]["chooser_name"] == "Synthetic Staff"
    assert items[manager["id"]]["accepted_at"]
    # The former owner no longer sees the list.
    assert (await world.call("owner", "GET", INVITATIONS)).status_code == 404

    applied = await world.call("admin", "POST", f"{INVITATIONS}/{manager['id']}/apply")
    assert applied.status_code == 200, applied.text
    assert applied.json() == {"user_id": str(first), "outcome": "saved", "reason": None}
    assert await held(world, first) == rows(PRESETS["manager"], EMPTY)
    [record] = await records(world, manager["id"])
    assert record["subject_id"] == first and record["details"]["preset"] == "manager"
    again = await world.call("admin", "POST", f"{INVITATIONS}/{manager['id']}/apply")
    assert again.status_code == 409 and again.json()["detail"] == "invitation_already_handled"

    dismissed = await world.call("admin", "POST", f"{INVITATIONS}/{team['id']}/dismiss")
    assert dismissed.status_code == 204, dismissed.text
    assert await held(world, second) == EMPTY
    [dismissal] = await records(world, team["id"])
    assert dismissal["subject_id"] == world.users["admin"]
    assert dismissal["operation"] == "invitation_access.dismiss"
    listed = await world.call("admin", "GET", INVITATIONS)
    assert listed.json()["items"] == []
    assert await access.apply_on_accept(world.tenant, UUID(team["id"])) == "none"
    late = await world.call("admin", "POST", f"{INVITATIONS}/{team['id']}/apply")
    assert late.status_code == 409 and late.json()["detail"] == "invitation_already_handled"


async def test_non_owner_rows_outside_staff_are_refused(world: HrWorld) -> None:
    issued = await invite(world, "admin", "staff")
    member, token_id = await accept_only(world, issued["token"])
    staff = rows(PRESETS["staff"], EMPTY)
    payroll = WorkflowRow("payroll", "read", "payroll_operator")
    wider = staff._replace(workflows=staff.workflows | {payroll})
    before = await count(world)
    with pytest.raises(psycopg.Error, match="hr_invitation_rows_refused"):
        await direct_apply(world, token_id, payload(wider))
    team = staff._replace(
        grants=staff.grants | {GrantRow("training.decide", "assigned_manager", ())}
    )
    with pytest.raises(psycopg.Error, match="hr_invitation_rows_refused"):
        await direct_apply(world, token_id, payload(team))
    with pytest.raises(psycopg.Error, match="hr_invitation_rows_refused"):
        await direct_apply(world, token_id, payload(staff._replace(areas=frozenset({"payroll"}))))
    assert await held(world, member) == EMPTY
    assert await count(world) == before


async def test_null_override_is_refused(world: HrWorld) -> None:
    issued = await invite(world, "admin", "staff")
    member, token_id = await accept_only(world, issued["token"])
    staff = rows(PRESETS["staff"], EMPTY)
    payroll = WorkflowRow("payroll", "read", "payroll_operator")
    wider = staff._replace(workflows=staff.workflows | {payroll})
    before = await count(world)
    with pytest.raises(psycopg.Error, match="hr_invitation_arguments_invalid"):
        await direct_apply(world, token_id, payload(wider), override=None)
    assert await held(world, member) == EMPTY
    assert await count(world) == before


@pytest.mark.parametrize("hr_user", [None, "admin"])
async def test_override_without_an_owner_session_is_refused(
    world: HrWorld, hr_user: str | None
) -> None:
    issued = await invite(world, "admin", "staff")
    member, token_id = await accept_only(world, issued["token"])
    before = await count(world)
    with pytest.raises(psycopg.Error, match="hr_owner_required"):
        await direct_apply(
            world,
            token_id,
            payload(rows(PRESETS["manager"], EMPTY)),
            override=True,
            hr_user=world.users[hr_user] if hr_user else None,
        )
    assert await held(world, member) == EMPTY
    assert await count(world) == before


async def test_staff_pairs_in_sql_match_python(world: HrWorld) -> None:
    [row] = await world.q(
        "SELECT pg_get_functiondef('public.hr_apply_invitation_access"
        "(uuid,uuid,jsonb,text[],text[],boolean)'::regprocedure) AS body"
    )
    found = re.search(r"staff constant jsonb := '(\{.*?\})'", row["body"], re.S)
    assert found is not None
    pairs = json.loads(found.group(1))
    staff = rows(PRESETS["staff"], EMPTY)
    assert {tuple(p) for p in pairs["grants"]} == {(g.action, g.scope) for g in staff.grants}
    assert len(pairs["grants"]) == len(staff.grants)
    assert {tuple(p) for p in pairs["workflows"]} == set(staff.workflows)
    assert len(pairs["workflows"]) == len(staff.workflows)
    assert staff.areas == frozenset()


async def test_unconsumed_or_other_tenant_token_is_refused(world: HrWorld) -> None:
    issued = await invite(world, "admin", "staff")
    token_id = UUID(issued["id"])
    assert await preset_row(world, world.tenant, world.tenant, token_id) == []
    with pytest.raises(psycopg.Error, match="hr_invitation_unavailable"):
        await direct_apply(world, token_id, payload(rows(PRESETS["staff"], EMPTY)))
    member, _ = await accept_only(world, issued["token"])
    [found] = await preset_row(world, world.tenant, world.tenant, token_id)
    assert found == {
        "user_id": member,
        "preset": "staff",
        "chosen_by": world.users["admin"],
        "chooser_is_owner": False,
        "applied": False,
        "grants": [],
        "workflows": [],
        "areas": [],
    }
    assert await preset_row(world, world.other_tenant, world.other_tenant, token_id) == []
    assert await preset_row(world, world.tenant, world.other_tenant, token_id) == []
    assert await preset_row(world, world.other_tenant, world.tenant, token_id) == []
    assert await records(world, token_id) == []


async def test_existing_access_is_kept_not_replaced(world: HrWorld) -> None:
    issued = await invite(world, "admin", "staff")
    member, token_id = await accept_only(world, issued["token"])
    # The owner gives payroll review, and a partial directory grant, before the apply.
    await world.q(
        "INSERT INTO hr_workflow_grants(client_id,user_id,domain,action,scope) "
        "VALUES (%s,%s,'payroll','decide','payroll_reviewer')",
        (world.tenant, member),
    )
    await world.q(
        "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) "
        "VALUES (%s,%s,'payroll',%s)",
        (world.tenant, member, world.users["owner"]),
    )
    await world.q(
        "INSERT INTO hr_grants(client_id,user_id,action,scope,fields) "
        "VALUES (%s,%s,'directory.read','hr_operator',ARRAY['display_name'])",
        (world.tenant, member),
    )
    outcome = await InvitationAccess(world.service).apply_on_accept(world.tenant, token_id)
    assert outcome == "applied"
    expected = rows(PRESETS["staff"] | switch_set("payroll_review"), EMPTY)
    assert await held(world, member) == expected


async def test_recorded_switches_include_access_the_member_already_had(world: HrWorld) -> None:
    issued = await invite(world, "admin", "staff")
    member, token_id = await accept_only(world, issued["token"])
    review = SWITCHES["payroll_review"]
    for w in review.workflows:
        await world.q(
            "INSERT INTO hr_workflow_grants(client_id,user_id,domain,action,scope) "
            "VALUES (%s,%s,%s,%s,%s)",
            (world.tenant, member, w.domain, w.action, w.scope),
        )
    for area in review.areas:
        await world.q(
            "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) "
            "VALUES (%s,%s,%s,%s)",
            (world.tenant, member, area, world.users["owner"]),
        )
    access = InvitationAccess(world.service)
    assert await access.apply_on_accept(world.tenant, token_id) == "applied"
    [record] = await records(world, token_id)
    assert record["details"]["on"] == sorted(PRESETS["staff"] | switch_set("payroll_review"))
    assert record["details"]["added"] == sorted(PRESETS["staff"])

    def mine(page: dict[str, Any]) -> list[dict[str, Any]]:
        return [i for i in page["items"] if i["target_user_id"] == str(member)]

    [invitation] = mine((await world.call("owner", "GET", RECENT)).json())
    assert invitation["source"] == "invitation"
    assert invitation["turned_on"] == ["directory", "own_requests"]
    # An owner save that leaves payroll alone does not report payroll_review as turned on.
    people = await world.call("owner", "GET", f"{PEOPLE}?q=Synthetic+Invitee")
    assert people.status_code == 200, people.text
    [person] = [p for p in people.json()["items"] if p["user_id"] == str(member)]
    assert set(person["on"]) == set(PRESETS["staff"]) | {"payroll_review"}
    saved = await world.call(
        "owner",
        "POST",
        CHANGES,
        {
            "batch_id": str(uuid4()),
            "version": people.json()["version"],
            "changes": [
                {
                    "user_id": str(member),
                    "on": [*person["on"], "approves_team"],
                    "base": person["on"],
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["results"][0]["outcome"] == "saved"
    page = mine((await world.call("owner", "GET", RECENT)).json())
    save = next(i for i in page if i["area"] is None and i["source"] == "page")
    assert save["turned_on"] == ["approves_team"] and save["turned_off"] == []


async def test_platform_admin_staff_invitation_applies_and_is_named(world: HrWorld) -> None:
    issued = await invite(world, "platform", "staff")
    member = await accept(world, issued["token"])
    assert await held(world, member) == rows(PRESETS["staff"], EMPTY)
    [record] = await records(world, issued["id"])
    assert record["subject_id"] == member
    assert record["details"]["chosen_by"] == str(world.users["platform"])
    response = await world.call("owner", "GET", RECENT)
    assert response.status_code == 200, response.text
    [event] = [i for i in response.json()["items"] if i["target_user_id"] == str(member)]
    assert event["source"] == "invitation" and event["preset"] == "staff"
    assert event["actor_user_id"] == str(world.users["platform"])
    assert event["actor_name"] == "FourStacks admin"
    assert event["target_name"] == "Synthetic Invitee"
    assert event["turned_on"] == ["directory", "own_requests"] and event["turned_off"] == []
    assert event["area"] is None and event["area_enabled"] is None


@pytest.mark.parametrize("who", ["admin", "staff"])
async def test_pending_and_actions_are_owner_only(world: HrWorld, who: str) -> None:
    issued = await invite(world, "admin", "staff")
    await accept_only(world, issued["token"])
    before = await count(world)
    listed = await world.call(who, "GET", INVITATIONS)
    assert listed.status_code == 404 and listed.json() == {"detail": "resource_unavailable"}
    for action in ("apply", "dismiss"):
        response = await world.call(who, "POST", f"{INVITATIONS}/{issued['id']}/{action}")
        assert response.status_code == 404, response.text
    assert await count(world) == before
    owner = await world.call("owner", "GET", INVITATIONS)
    assert [item["token_id"] for item in owner.json()["items"]] == [issued["id"]]


async def test_accept_still_succeeds_when_apply_fails(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("synthetic apply failure")

    issued = await invite(world, "admin", "staff")
    monkeypatch.setattr(InvitationAccess, "apply_on_accept", broken)
    caplog.set_level("DEBUG")
    with capture_logs() as logs:
        member = await accept(world, issued["token"])
    members = await world.q(
        "SELECT is_enabled FROM console_memberships WHERE client_id=%s AND user_id=%s",
        (world.tenant, member),
    )
    assert members == [{"is_enabled": True}]
    assert await held(world, member) == EMPTY
    captured = repr(logs) + caplog.text
    assert "hr_invitation_access_failed" in captured and "unexpected_error" in captured
    assert issued["token"] not in captured and "synthetic apply failure" not in captured


async def test_apply_failure_inside_is_logged_by_category(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def outage(*_args: object, **_kwargs: object) -> str:
        raise psycopg.OperationalError("synthetic outage")

    issued = await invite(world, "admin", "staff")
    member, token_id = await accept_only(world, issued["token"])
    access = InvitationAccess(world.service)
    monkeypatch.setattr(access, "_apply_on_accept", outage)
    with capture_logs() as logs:
        assert await access.apply_on_accept(world.tenant, token_id) == "failed"
    assert [(e["event"], e["category"]) for e in logs] == [
        ("hr_invitation_access_failed", "database_error")
    ]
    assert await held(world, member) == EMPTY
