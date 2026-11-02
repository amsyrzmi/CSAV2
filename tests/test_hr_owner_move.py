"""Migration 0522: moving the HR owner and the owner-only rows, in one definer function
(disposable database). Reuses the HrWorld fixture of tests/test_hr_persistence.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import timedelta
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.db import q
from csa.core.tenancy import tx
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_hr_people import make_people_manager, ok, op
from tests.test_hr_people_records import db_today, finish, leave_body, set_last_day
from tests.test_hr_persistence import HrWorld, hr_database, workflow_grant, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

OWNER_ONLY_EXPECTED: set[tuple[str, ...]] = {("hr_grants", "training.decide")} | {
    ("hr_workflow_grants", d, a)
    for d in ("recruitment", "attendance", "leave")
    for a in ("read", "prepare", "decide")
}  # 10 combinations today
DIRECTORY_FIELDS = ["assigned_manager", "department", "display_name", "job_title", "work_email"]


def fingerprint(seed: object) -> str:
    return hashlib.sha256(str(seed).encode()).hexdigest()


async def as_session(
    world: HrWorld,
    session_user: UUID | None,
    statement: LiteralString,
    params: tuple[object, ...],
    *,
    tenant: UUID | None = None,
) -> list[dict[str, Any]]:
    """Run as csa_app, in a transaction with the tenant and HR user settings the routes set."""
    async with tx(world.service.pool, str(tenant or world.tenant)) as conn:
        if session_user is not None:
            await conn.execute(
                "SELECT set_config('app.hr_user_id', %s, true)", (str(session_user),)
            )
        return await (await conn.execute(statement, params)).fetchall()


async def move(
    world: HrWorld,
    actor: UUID,
    new_owner: UUID | None,
    expected: UUID | None,
    *,
    key: UUID | None = None,
    seed: object = None,
) -> dict[str, Any]:
    rows = await as_session(
        world,
        actor,
        "SELECT * FROM public.hr_owner_move(%s,%s,%s,%s,%s,%s)",
        (world.tenant, actor, new_owner, expected, key or uuid4(), fingerprint(seed or new_owner)),
    )
    assert len(rows) == 1
    return rows[0]


async def read_owner(
    world: HrWorld, actor: UUID, *, session_user: UUID | None = None, tenant: UUID | None = None
) -> list[dict[str, Any]]:
    return await as_session(
        world,
        session_user or actor,
        "SELECT * FROM public.hr_owner_read(%s,%s)",
        (world.tenant, actor),
        tenant=tenant,
    )


async def new_user(
    world: HrWorld,
    role: str | None = "tenant_staff",
    *,
    client: UUID | None = None,
    platform: bool = False,
) -> UUID:
    """A user the fixture does not have: `role=None` means no membership at all."""
    user = uuid4()
    await world.q(
        "INSERT INTO console_users(id,email,display_name,is_platform_admin) VALUES (%s,%s,%s,%s)",
        (user, f"synthetic-{user}@example.test", "Synthetic New", platform),
    )
    if role is not None:
        await world.q(
            "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,%s)",
            (user, client or world.tenant, role),
        )
    return user


async def linked_person(world: HrWorld, user: UUID, *, active: bool = True) -> UUID:
    employee = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,is_active) VALUES (%s,%s,%s,%s)",
        (world.tenant, employee, "Synthetic Linked", active),
    )
    await world.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        (world.tenant, user, employee),
    )
    return employee


async def owner_row(world: HrWorld) -> dict[str, Any] | None:
    rows = await world.q(
        "SELECT user_id, approved_by, approved_at, approval_reference FROM hr_client_owners WHERE "
        "client_id=%s",
        (world.tenant,),
    )
    return rows[0] if rows else None


async def owner_only_rows(world: HrWorld, user: UUID) -> set[tuple[str, ...]]:
    grants = await world.q(
        "SELECT action FROM hr_grants WHERE client_id=%s AND user_id=%s AND scope='client_owner'",
        (world.tenant, user),
    )
    workflows = await world.q(
        "SELECT domain, action FROM hr_workflow_grants WHERE client_id=%s AND user_id=%s AND "
        "scope='client_owner'",
        (world.tenant, user),
    )
    return {("hr_grants", row["action"]) for row in grants} | {
        ("hr_workflow_grants", row["domain"], row["action"]) for row in workflows
    }


async def is_owner(world: HrWorld, user: UUID) -> bool:
    rows = await as_session(world, user, "SELECT public.hr_is_owner(%s) AS yes", (world.tenant,))
    return rows[0]["yes"]


async def everything(world: HrWorld) -> dict[str, Any]:
    """Every row a move could write, so a refusal can be shown to have written nothing."""
    rows = await world.q(
        "SELECT "
        "(SELECT jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text) FROM hr_client_owners x "
        " WHERE x.client_id=%s) AS owners, "
        "(SELECT jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text) FROM hr_grants x "
        " WHERE x.client_id=%s) AS grants, "
        "(SELECT jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text) FROM hr_workflow_grants x "
        " WHERE x.client_id=%s) AS workflows, "
        "(SELECT jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text) FROM hr_private_grants x "
        " WHERE x.client_id=%s) AS areas, "
        "(SELECT count(*) FROM hr_operations x WHERE x.client_id=%s) AS operations, "
        "(SELECT count(*) FROM hr_authority_events x WHERE x.client_id=%s) AS events, "
        "(SELECT count(*) FROM console_audit x WHERE x.client_id=%s) AS audit",
        (world.tenant,) * 7,
    )
    return rows[0]


async def assert_refused(
    world: HrWorld,
    code: str,
    actor: UUID,
    new_owner: UUID | None,
    expected: UUID | None,
    *,
    key: UUID | None = None,
    seed: object = None,
) -> None:
    before = await everything(world)
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        await move(world, actor, new_owner, expected, key=key, seed=seed)
    assert refused.value.diag.message_primary == code
    assert await everything(world) == before


async def assert_denied(
    world: HrWorld,
    actor: UUID,
    new_owner: UUID,
    expected: UUID | None,
    *,
    session_user: UUID | None = None,
    tenant: UUID | None = None,
    key: UUID | None = None,
    seed: object = None,
) -> None:
    before = await everything(world)
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
        await as_session(
            world,
            session_user,
            "SELECT * FROM public.hr_owner_move(%s,%s,%s,%s,%s,%s)",
            (world.tenant, actor, new_owner, expected, key or uuid4(), fingerprint(seed or "x")),
            tenant=tenant,
        )
    assert denied.value.diag.message_primary == "hr_owner_move_denied"
    assert await everything(world) == before


async def wait_for_a_blocked_lock(world: HrWorld) -> None:
    for _ in range(200):
        rows = await world.q("SELECT count(*) AS n FROM pg_locks WHERE NOT granted")
        if rows[0]["n"]:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("no session ever waited on a lock")


async def test_functions_are_pinned_to_csa_app(world: HrWorld) -> None:
    roles = ("csa_app", "csa_ro", "csa_identity", "csa_hr_maintenance", "public")
    for signature in (
        "hr_owner_move(uuid,uuid,uuid,uuid,uuid,text)",
        "hr_owner_read(uuid,uuid)",
    ):
        rows = await world.q(
            "SELECT p.prosecdef, p.proconfig, "
            + ", ".join(
                f"has_function_privilege('{role}', p.oid, 'EXECUTE') AS {role}" for role in roles
            )
            + " FROM pg_proc p WHERE p.oid = to_regprocedure(%s)",
            (f"public.{signature}",),
        )
        assert len(rows) == 1, signature
        assert rows[0]["prosecdef"] is True, signature
        assert rows[0]["proconfig"] == ["search_path=pg_catalog, public, pg_temp"], signature
        assert rows[0]["csa_app"] is True, signature
        assert not any(rows[0][role] for role in roles[1:]), signature


async def test_standard_set_covers_every_owner_only_combination(world: HrWorld) -> None:
    # The candidates come from the CHECKs themselves, so a new action or domain is tried too.
    scope_check = await world.q(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid='public.hr_grants'::regclass AND conname='hr_grants_scope_action'"
    )
    actions = set(re.findall(r"'(\w+\.\w+)'", scope_check[0]["def"]))
    assert {"training.decide", "directory.read", "people.manage", "people.sensitive"} <= actions
    workflow_checks = await world.q(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid='public.hr_workflow_grants'::regclass "
        "AND conname IN ('hr_workflow_grants_domain_check','hr_workflow_grants_action_check')"
    )
    checks = {row["conname"]: row["def"] for row in workflow_checks}
    domains = set(re.findall(r"'(\w+)'", checks["hr_workflow_grants_domain_check"]))
    workflow_actions = set(re.findall(r"'(\w+)'", checks["hr_workflow_grants_action_check"]))
    assert "payroll" in domains and "decide" in workflow_actions

    probe = world.users["staff"]
    accepted: set[tuple[str, ...]] = set()
    async with world.owner_pool.connection() as conn:
        for action in sorted(actions):
            try:
                async with conn.transaction(force_rollback=True):
                    await conn.execute(
                        "INSERT INTO hr_grants(client_id,user_id,action,scope) VALUES "
                        "(%s,%s,%s,'client_owner')",
                        (world.tenant, probe, action),
                    )
                    accepted.add(("hr_grants", action))
            except psycopg.errors.CheckViolation:
                pass
        for domain in sorted(domains):
            for action in sorted(workflow_actions):
                try:
                    async with conn.transaction(force_rollback=True):
                        await conn.execute(
                            "INSERT INTO hr_workflow_grants(client_id,user_id,domain,action,scope) "
                            "VALUES (%s,%s,%s,%s,'client_owner')",
                            (world.tenant, probe, domain, action),
                        )
                        accepted.add(("hr_workflow_grants", domain, action))
                except psycopg.errors.CheckViolation:
                    pass
    assert len(accepted) == 10
    assert accepted == OWNER_ONLY_EXPECTED

    await move(world, world.users["admin"], probe, world.users["owner"])
    assert await owner_only_rows(world, probe) == accepted


async def test_tenant_admin_moves_owner_and_powers_follow(world: HrWorld) -> None:
    admin, old, new = world.users["admin"], world.users["owner"], world.users["staff"]
    # Rows the old owner holds outside client_owner, which the move must leave alone.
    await world.grant("owner", "directory.read", "hr_operator")
    await workflow_grant(world, "owner", "attendance", "read", "hr_operator")
    await world.q(
        "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) VALUES "
        "(%s,%s,'recruitment',%s)",
        (world.tenant, old, old),
    )
    await workflow_grant(world, "owner", "recruitment", "read", "client_owner")
    # The new owner's narrower directory row is widened; their other rows stay.
    await world.grant("staff", "directory.read", "hr_operator", ["display_name"])
    await world.grant("staff", "training.read", "hr_operator")
    await workflow_grant(world, "staff", "leave", "read", "self")
    case_check: LiteralString = "SELECT public.hr_case_access(%s,'recruitment','read',NULL) AS yes"
    assert await is_owner(world, old) is True
    assert (await as_session(world, old, case_check, (world.tenant,)))[0]["yes"] is True
    assert (await as_session(world, new, case_check, (world.tenant,)))[0]["yes"] is False
    old_rows = await world.q(
        "SELECT action, scope FROM hr_grants WHERE client_id=%s AND user_id=%s AND "
        "scope<>'client_owner' ORDER BY 1,2",
        (world.tenant, old),
    )
    key = uuid4()

    result = await move(world, admin, new, old, key=key)

    assert result == {"old_owner": old, "new_owner": new, "duplicate": False}
    # Straight away, on the next transaction of each.
    assert await is_owner(world, new) is True
    assert await is_owner(world, old) is False
    assert (await as_session(world, new, case_check, (world.tenant,)))[0]["yes"] is True
    assert (await as_session(world, old, case_check, (world.tenant,)))[0]["yes"] is False
    leave_decision: LiteralString = "SELECT public.hr_case_access(%s,'leave','decide',%s) AS yes"
    params = (world.tenant, world.employees["staff"])
    assert (await as_session(world, new, leave_decision, params))[0]["yes"] is True
    assert (await as_session(world, old, leave_decision, params))[0]["yes"] is False

    row = await owner_row(world)
    assert row is not None
    assert (row["user_id"], row["approved_by"]) == (new, admin)
    assert row["approval_reference"] == f"console:{key}"
    # The old owner lost exactly the client_owner rows.
    assert await owner_only_rows(world, old) == set()
    assert (
        await world.q(
            "SELECT action, scope FROM hr_grants WHERE client_id=%s AND user_id=%s "
            "AND scope<>'client_owner' ORDER BY 1,2",
            (world.tenant, old),
        )
        == old_rows
    )
    assert await world.q(
        "SELECT domain, action, scope FROM hr_workflow_grants WHERE client_id=%s AND user_id=%s",
        (world.tenant, old),
    ) == [{"domain": "attendance", "action": "read", "scope": "hr_operator"}]
    assert await world.q(
        "SELECT area FROM hr_private_grants WHERE client_id=%s AND user_id=%s",
        (world.tenant, old),
    ) == [{"area": "recruitment"}]
    # The new owner has the standard set, and kept what they had.
    assert await owner_only_rows(world, new) == OWNER_ONLY_EXPECTED
    assert await world.q(
        "SELECT action, fields FROM hr_grants WHERE client_id=%s AND user_id=%s "
        "AND scope='hr_operator' ORDER BY action",
        (world.tenant, new),
    ) == [
        {"action": "directory.read", "fields": DIRECTORY_FIELDS},
        {"action": "training.read", "fields": []},
    ]
    assert await world.q(
        "SELECT domain, action FROM hr_workflow_grants WHERE client_id=%s AND user_id=%s AND "
        "scope='self'",
        (world.tenant, new),
    ) == [{"domain": "leave", "action": "read"}]
    assert await world.q(
        "SELECT area, approved_by FROM hr_private_grants WHERE client_id=%s AND user_id=%s ORDER "
        "BY area",
        (world.tenant, new),
    ) == [
        {"area": "attendance", "approved_by": admin},
        {"area": "recruitment", "approved_by": admin},
    ]
    # The records.
    assert await world.q(
        "SELECT subject_id, request_key, operation, fingerprint, receipt FROM hr_operations "
        "WHERE client_id=%s AND operation='owner.move'",
        (world.tenant,),
    ) == [
        {
            "subject_id": new,
            "request_key": key,
            "operation": "owner.move",
            "fingerprint": fingerprint(new),
            "receipt": {"old_owner": str(old), "new_owner": str(new)},
        }
    ]
    assert await world.q(
        "SELECT subject_id, request_key, operation, details FROM hr_authority_events "
        "WHERE client_id=%s AND operation='owner.move'",
        (world.tenant,),
    ) == [
        {
            "subject_id": new,
            "request_key": key,
            "operation": "owner.move",
            "details": {"user_id": str(new), "old_owner_id": str(old), "actor_id": str(admin)},
        }
    ]
    assert await world.q(
        "SELECT actor_id, target_id, details FROM console_audit WHERE client_id=%s AND "
        "action='hr.owner_moved'",
        (world.tenant,),
    ) == [
        {
            "actor_id": admin,
            "target_id": str(new),
            "details": {"old_owner_id": str(old), "new_owner_id": str(new)},
        }
    ]


async def test_a_tenant_admin_with_no_person_record_can_choose_themselves(world: HrWorld) -> None:
    admin = world.users["admin"]
    result = await move(world, admin, admin, world.users["owner"])
    assert result["new_owner"] == admin
    assert await is_owner(world, admin) is True
    assert await owner_only_rows(world, admin) == OWNER_ONLY_EXPECTED


async def test_no_owner_yet_sets_the_first_owner(world: HrWorld) -> None:
    admin, new = world.users["admin"], world.users["staff"]
    await world.q("DELETE FROM hr_client_owners WHERE client_id=%s", (world.tenant,))
    # Someone who expects an owner when there is none has stale information.
    await assert_refused(world, "hr_owner_changed", admin, new, world.users["owner"])

    result = await move(world, admin, new, None)

    assert result == {"old_owner": None, "new_owner": new, "duplicate": False}
    assert await is_owner(world, new) is True
    assert await owner_only_rows(world, new) == OWNER_ONLY_EXPECTED
    events = await world.q(
        "SELECT details FROM hr_authority_events WHERE client_id=%s", (world.tenant,)
    )
    assert events == [
        {"details": {"user_id": str(new), "old_owner_id": None, "actor_id": str(admin)}}
    ]
    assert (await everything(world))["audit"] == 1
    assert await world.q(
        "SELECT details FROM console_audit WHERE client_id=%s AND action='hr.owner_moved'",
        (world.tenant,),
    ) == [{"details": {"old_owner_id": None, "new_owner_id": str(new)}}]


async def test_platform_admin_can_move_and_is_recorded_as_actor(world: HrWorld) -> None:
    # The fixture's "platform" user has a membership. A real one has none.
    platform = await new_user(world, None, platform=True)
    old, new = world.users["owner"], world.users["staff"]

    result = await move(world, platform, new, old)

    assert result == {"old_owner": old, "new_owner": new, "duplicate": False}
    assert await is_owner(world, new) is True
    row = await owner_row(world)
    assert row is not None and row["approved_by"] == platform
    assert await world.q(
        "SELECT subject_id FROM hr_operations WHERE client_id=%s AND operation='owner.move'",
        (world.tenant,),
    ) == [{"subject_id": new}]
    events = await world.q(
        "SELECT details FROM hr_authority_events WHERE client_id=%s", (world.tenant,)
    )
    assert events[0]["details"]["actor_id"] == str(platform)
    assert await world.q(
        "SELECT actor_id FROM console_audit WHERE client_id=%s AND action='hr.owner_moved'",
        (world.tenant,),
    ) == [{"actor_id": platform}]
    # hr_private_grants.approved_by must be a member of the company (0410), so a platform admin
    # who is not one leaves the new owner there; the records above name the real actor.
    assert await world.q(
        "SELECT area, approved_by FROM hr_private_grants WHERE client_id=%s AND user_id=%s ORDER "
        "BY area",
        (world.tenant, new),
    ) == [
        {"area": "attendance", "approved_by": new},
        {"area": "recruitment", "approved_by": new},
    ]
    # Acting makes them no member.
    assert await world.q("SELECT 1 FROM console_memberships WHERE user_id=%s", (platform,)) == []


async def test_refused_actors(world: HrWorld) -> None:
    old, new = world.users["owner"], world.users["manager"]
    for name in ("staff", "reader", "operator"):
        user = world.users[name]
        await assert_denied(world, user, new, old, session_user=user)
    # The HR owner's title gives no right to this: only the console role does.
    await world.q(
        "UPDATE console_memberships SET role='tenant_staff' WHERE user_id=%s AND client_id=%s",
        (old, world.tenant),
    )
    await assert_denied(world, old, new, old, session_user=old)
    # A tenant admin of the other company: with their own session, and claiming this company.
    foreign = await new_user(world, "tenant_admin", client=world.other_tenant)
    await assert_denied(world, foreign, new, old, session_user=foreign, tenant=world.other_tenant)
    await assert_denied(world, foreign, new, old, session_user=foreign)
    # p_actor is not the session's user, or the session has no user.
    admin = world.users["admin"]
    await assert_denied(world, admin, new, old, session_user=world.users["staff"])
    await assert_denied(world, admin, new, old, session_user=None)
    # Disabled people, whatever they were.
    off_membership = await new_user(world, "tenant_admin")
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s", (off_membership,)
    )
    await assert_denied(world, off_membership, new, old, session_user=off_membership)
    off_user = await new_user(world, "tenant_admin")
    await world.q("UPDATE console_users SET is_enabled=false WHERE id=%s", (off_user,))
    await assert_denied(world, off_user, new, old, session_user=off_user)
    off_platform = await new_user(world, None, platform=True)
    await world.q("UPDATE console_users SET is_enabled=false WHERE id=%s", (off_platform,))
    await assert_denied(world, off_platform, new, old, session_user=off_platform)


async def test_refused_targets(world: HrWorld) -> None:
    admin, old = world.users["admin"], world.users["owner"]
    await assert_refused(world, "hr_owner_read_only", admin, world.users["reader"], old)
    off_membership = await new_user(world)
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s", (off_membership,)
    )
    await assert_refused(world, "hr_owner_unavailable", admin, off_membership, old)
    off_user = await new_user(world)
    await world.q("UPDATE console_users SET is_enabled=false WHERE id=%s", (off_user,))
    await assert_refused(world, "hr_owner_unavailable", admin, off_user, old)
    first_password = await new_user(world)
    await world.q(
        "UPDATE console_users SET must_change_password=true WHERE id=%s", (first_password,)
    )
    await assert_refused(world, "hr_owner_unavailable", admin, first_password, old)
    # Not a member here, or a member of the other company only, or nobody at all.
    await assert_refused(world, "hr_owner_unavailable", admin, await new_user(world, None), old)
    foreign = await new_user(world, "tenant_admin", client=world.other_tenant)
    await assert_refused(world, "hr_owner_unavailable", admin, foreign, old)
    await assert_refused(world, "hr_owner_unavailable", admin, uuid4(), old)
    await assert_refused(world, "hr_owner_unavailable", admin, None, old, seed="nobody")
    # Leaving: a last day set, or a person who is no longer active.
    leaving = world.users["staff"]
    await world.q(
        "UPDATE hr_employees SET last_day=current_date+1, leaving_reason='resigned' WHERE "
        "client_id=%s AND id=%s",
        (world.tenant, world.employees["staff"]),
    )
    await assert_refused(world, "hr_owner_leaving", admin, leaving, old)
    gone = await new_user(world)
    await linked_person(world, gone, active=False)
    await assert_refused(world, "hr_owner_leaving", admin, gone, old)
    # The same person.
    await assert_refused(world, "hr_owner_unchanged", admin, old, old)
    # A linked, active person with no last day is fine.
    fine = await new_user(world)
    await linked_person(world, fine)
    assert (await move(world, admin, fine, old))["new_owner"] == fine


async def test_stale_expected_owner_is_refused(world: HrWorld) -> None:
    admin, old, new = world.users["admin"], world.users["owner"], world.users["staff"]
    await assert_refused(world, "hr_owner_changed", admin, new, world.users["manager"])
    await assert_refused(world, "hr_owner_changed", admin, new, None)
    # Stale comes before every target answer: the page must reload, whatever it asked for.
    await assert_refused(world, "hr_owner_changed", admin, world.users["reader"], new)
    await assert_refused(world, "hr_owner_changed", admin, old, new)
    assert (await move(world, admin, new, old))["new_owner"] == new


async def test_same_key_returns_first_result_without_a_second_write(world: HrWorld) -> None:
    admin, old, first = world.users["admin"], world.users["owner"], world.users["staff"]
    key = uuid4()
    assert await move(world, admin, first, old, key=key) == {
        "old_owner": old,
        "new_owner": first,
        "duplicate": False,
    }
    written = await everything(world)

    # The retry's owner and target checks would now say "already the owner" and "stale".
    again = await move(world, admin, first, old, key=key)

    assert again == {"old_owner": old, "new_owner": first, "duplicate": True}
    assert await everything(world) == written
    # A later move does not change the answer, and the retry does not undo it.
    second = world.users["manager"]
    await move(world, admin, second, first)
    await world.q(
        "UPDATE console_memberships SET role='read_only' WHERE user_id=%s AND client_id=%s",
        (first, world.tenant),
    )
    written = await everything(world)
    again = await move(world, admin, first, old, key=key)
    assert again == {"old_owner": old, "new_owner": first, "duplicate": True}
    assert await everything(world) == written
    # A first owner's receipt replays its empty old owner.
    await world.q("DELETE FROM hr_client_owners WHERE client_id=%s", (world.tenant,))
    other_key = uuid4()
    await move(world, admin, second, None, key=other_key)
    assert await move(world, admin, second, None, key=other_key) == {
        "old_owner": None,
        "new_owner": second,
        "duplicate": True,
    }


async def test_a_receipt_is_never_shown_to_a_caller_without_the_right(world: HrWorld) -> None:
    admin, old, new = world.users["admin"], world.users["owner"], world.users["staff"]
    key = uuid4()
    await move(world, admin, new, old, key=key)
    # The new owner is staff here. Their own retry, with the same key and fingerprint, is refused.
    await assert_denied(world, new, new, old, session_user=new, key=key, seed=new)


async def test_same_key_with_another_person_is_a_conflict(world: HrWorld) -> None:
    admin, old, new = world.users["admin"], world.users["owner"], world.users["staff"]
    key = uuid4()
    await move(world, admin, new, old, key=key)
    other = world.users["manager"]
    # hr_operations keys on the subject, and the subject here would be another person.
    await assert_refused(world, "hr_idempotency_conflict", admin, other, new, key=key)
    row = await owner_row(world)
    assert row is not None and row["user_id"] == new
    # The conflict comes before the checks whose answers have changed.
    await assert_refused(world, "hr_idempotency_conflict", admin, new, old, key=key, seed="other")


async def test_a_key_already_used_by_another_operation_is_a_conflict(world: HrWorld) -> None:
    # The primary key of hr_operations is (client, subject, key), whatever the operation.
    admin, old, new = world.users["admin"], world.users["owner"], world.users["staff"]
    key = uuid4()
    await world.q(
        "INSERT INTO hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt) "
        "VALUES (%s,%s,%s,'access.save',%s,%s)",
        (world.tenant, new, key, fingerprint("access"), Jsonb({})),
    )
    await assert_refused(world, "hr_idempotency_conflict", admin, new, old, key=key)


async def test_two_moves_at_once_one_gets_owner_changed(world: HrWorld) -> None:
    admin, old = world.users["admin"], world.users["owner"]
    second_admin = await new_user(world, "tenant_admin")
    winner, loser = world.users["staff"], world.users["manager"]
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await conn.execute("SELECT set_config('app.hr_user_id', %s, true)", (str(admin),))
        await conn.execute(
            "SELECT * FROM public.hr_owner_move(%s,%s,%s,%s,%s,%s)",
            (world.tenant, admin, winner, old, uuid4(), fingerprint(winner)),
        )
        late = asyncio.create_task(move(world, second_admin, loser, old))
        await wait_for_a_blocked_lock(world)
        assert not late.done()
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        await late
    assert refused.value.diag.message_primary == "hr_owner_changed"
    row = await owner_row(world)
    assert row is not None and row["user_id"] == winner
    assert await owner_only_rows(world, loser) == set()


async def test_marking_the_new_owner_as_leaving_waits_for_the_move_and_is_refused(
    world: HrWorld,
) -> None:
    admin, old, new = world.users["admin"], world.users["owner"], world.users["staff"]
    employee = world.employees["staff"]
    await make_people_manager(world)
    today = await db_today(world)
    body = await leave_body(world, employee, today + timedelta(days=5))
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await conn.execute("SELECT set_config('app.hr_user_id', %s, true)", (str(admin),))
        await conn.execute(
            "SELECT * FROM public.hr_owner_move(%s,%s,%s,%s,%s,%s)",
            (world.tenant, admin, new, old, uuid4(), fingerprint(new)),
        )
        late = asyncio.create_task(op(world, "operator", body))
        await wait_for_a_blocked_lock(world)
    response = await late
    assert response.status_code != 200, response.text
    rows = await world.q(
        "SELECT last_day FROM hr_employees WHERE client_id=%s AND id=%s",
        (world.tenant, employee),
    )
    assert rows == [{"last_day": None}]


async def test_a_move_can_deadlock_with_person_leave_by_the_target(world: HrWorld) -> None:
    # hr_lock_authority locks the owner row only for the current owner, so the person being made
    # owner holds their grant rows without it. Their person.leave then waits on the owner row while
    # the move's directory.read upsert waits on their grant row. The ruling accepts Postgres
    # aborting one side, as long as nothing is half written.
    admin, old, target = world.users["admin"], world.users["owner"], world.users["operator"]
    await make_people_manager(world)
    leaver = world.employees["staff"]
    today = await db_today(world)
    body = await leave_body(world, leaver, today + timedelta(days=5))
    before = await everything(world)
    key = uuid4()
    leave_aborted = move_aborted = False
    mover: asyncio.Task[dict[str, Any]] | None = None
    try:
        async with tx(world.service.pool, str(world.tenant)) as conn:
            await conn.execute("SELECT set_config('app.hr_user_id', %s, true)", (str(target),))
            # The route's order: authority first, then the operations lock, then the people write.
            await q.fetch_one("hr/authority.sql", (world.tenant, target), conn=conn)
            await q.execute("hr/key_lock.sql", (f"{world.tenant}:hr-operations",), conn=conn)
            mover = asyncio.create_task(move(world, admin, target, old, key=key))
            # The move now holds the owner row and waits on the target's directory.read row.
            await wait_for_a_blocked_lock(world)
            await q.fetch_one(
                "hr/people_apply.sql",
                (world.tenant, uuid4(), fingerprint("leave"), Jsonb(body)),
                conn=conn,
            )
    except psycopg.errors.DeadlockDetected:
        leave_aborted = True
    assert mover is not None
    try:
        await asyncio.wait_for(mover, 30)
    except psycopg.errors.DeadlockDetected:
        move_aborted = True

    assert leave_aborted != move_aborted
    rows = await world.q(
        "SELECT last_day FROM hr_employees WHERE client_id=%s AND id=%s", (world.tenant, leaver)
    )
    row = await owner_row(world)
    assert row is not None
    if move_aborted:
        # The usual side: its deadlock timer started first. Only the leave's receipt is new.
        assert await everything(world) == {**before, "operations": before["operations"] + 1}
        assert row["user_id"] == old
        assert await owner_only_rows(world, target) == set()
        assert rows[0]["last_day"] is not None
        moved = await world.q(
            "SELECT 1 FROM hr_operations WHERE client_id=%s AND operation='owner.move'",
            (world.tenant,),
        )
        assert moved == []
        # The route's "try again": the same key finds no first run and moves.
        retried = await move(world, admin, target, old, key=key)
        assert retried == {"old_owner": old, "new_owner": target, "duplicate": False}
    else:
        # A stalled host can block the leave after the move's one deadlock check has passed.
        assert row["user_id"] == target
        assert rows[0]["last_day"] is None


async def test_moves_away_from_a_disabled_owner(world: HrWorld) -> None:
    admin, old = world.users["admin"], world.users["owner"]
    # Their account is off, their membership is off, then they are read only.
    await world.q("UPDATE console_users SET is_enabled=false WHERE id=%s", (old,))
    assert (await read_owner(world, admin))[0]["usable"] is False
    new = world.users["staff"]
    assert (await move(world, admin, new, old))["old_owner"] == old
    assert await is_owner(world, new) is True

    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s AND client_id=%s",
        (new, world.tenant),
    )
    assert (await read_owner(world, admin))[0]["usable"] is False
    newer = world.users["manager"]
    assert (await move(world, admin, newer, new))["old_owner"] == new

    await world.q(
        "UPDATE console_memberships SET role='read_only' WHERE user_id=%s AND client_id=%s",
        (newer, world.tenant),
    )
    assert (await read_owner(world, admin))[0]["usable"] is False
    newest = await new_user(world)
    assert (await move(world, admin, newest, newer))["old_owner"] == newer
    assert await owner_only_rows(world, newer) == set()
    assert (await read_owner(world, admin))[0]["usable"] is True


async def test_no_payroll_row_or_area_is_given_or_taken(world: HrWorld) -> None:
    admin, old = world.users["admin"], world.users["owner"]
    new = world.users["staff"]
    for who in ("owner", "manager"):
        await workflow_grant(world, who, "payroll", "read", "payroll_operator")
        await workflow_grant(world, who, "payroll", "decide", "payroll_reviewer")
        await world.q(
            "INSERT INTO hr_private_grants(client_id,user_id,area,approved_by) VALUES "
            "(%s,%s,'payroll',%s)",
            (world.tenant, world.users[who], old),
        )
    payroll: LiteralString = (
        "SELECT user_id, domain, action, scope, NULL AS area FROM hr_workflow_grants "
        "WHERE client_id=%s AND domain='payroll' "
        "UNION ALL SELECT user_id, NULL, NULL, NULL, area FROM hr_private_grants "
        "WHERE client_id=%s AND area='payroll' ORDER BY 1,2,3,4,5"
    )
    before = await world.q(payroll, (world.tenant, world.tenant))
    assert len(before) == 6

    # To someone with no payroll, and then to someone who has it.
    await move(world, admin, new, old)
    assert await world.q(payroll, (world.tenant, world.tenant)) == before
    await move(world, admin, world.users["manager"], new)
    assert await world.q(payroll, (world.tenant, world.tenant)) == before


async def test_old_owner_can_leave_and_the_finish_keeps_the_new_owner(world: HrWorld) -> None:
    admin, old, new = world.users["admin"], world.users["owner"], world.users["staff"]
    leaver = world.employees["owner"]
    today = await db_today(world)

    await move(world, admin, new, old)
    # The new owner, in an ordinary request, marks the old owner as leaving today.
    await ok(world, "staff", await leave_body(world, leaver, today))
    assert await finish(world, leaver) == "moved"
    # The next day.
    await set_last_day(world, leaver, today - timedelta(days=1))
    assert await finish(world, leaver) == "finished"

    row = await owner_row(world)
    assert row is not None and row["user_id"] == new
    assert await is_owner(world, new) is True
    assert await owner_only_rows(world, new) == OWNER_ONLY_EXPECTED
    assert await world.q(
        "SELECT is_enabled FROM console_memberships WHERE user_id=%s AND client_id=%s",
        (old, world.tenant),
    ) == [{"is_enabled": False}]


async def test_owner_read_returns_usable_and_refuses_other_actors(world: HrWorld) -> None:
    admin, old = world.users["admin"], world.users["owner"]
    await world.q(
        "UPDATE console_users SET display_name='Synthetic Owner Name' WHERE id=%s", (old,)
    )
    named = [{"user_id": old, "display_name": "Synthetic Owner Name", "usable": True}]
    assert await read_owner(world, admin) == named
    platform = await new_user(world, None, platform=True)
    assert await read_owner(world, platform) == named

    # Staff, a reader, another company's admin, a mismatched session: all refused.
    foreign = await new_user(world, "tenant_admin", client=world.other_tenant)
    for actor, session_user, tenant in (
        (world.users["staff"], None, None),
        (world.users["reader"], None, None),
        (foreign, None, None),
        (foreign, None, world.other_tenant),
        (admin, world.users["staff"], None),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            await read_owner(world, actor, session_user=session_user, tenant=tenant)
        assert denied.value.diag.message_primary == "hr_owner_move_denied"
    # A tenant admin who is also the owner is a tenant admin: the fixture's owner has that role.
    assert await read_owner(world, old) == named

    # usable: disabled account, disabled membership, read only.
    await world.q("UPDATE console_users SET is_enabled=false WHERE id=%s", (old,))
    assert (await read_owner(world, admin))[0]["usable"] is False
    await world.q("UPDATE console_users SET is_enabled=true WHERE id=%s", (old,))
    await world.q(
        "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s AND client_id=%s",
        (old, world.tenant),
    )
    assert (await read_owner(world, admin))[0]["usable"] is False
    await world.q(
        "UPDATE console_memberships SET is_enabled=true, role='read_only' WHERE user_id=%s AND "
        "client_id=%s",
        (old, world.tenant),
    )
    assert (await read_owner(world, admin))[0]["usable"] is False

    # No owner: no row.
    await world.q("DELETE FROM hr_client_owners WHERE client_id=%s", (world.tenant,))
    assert await read_owner(world, admin) == []
