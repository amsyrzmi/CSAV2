"""Migration 0636: the template editor's preview (who a draft rule set applies to now, and which
active templates it ties with) and the explicit `replace` of a manual start (disposable database).
Reuses the HrWorld fixture of tests/test_hr_persistence.py: every call runs as csa_app inside the
caller's HR session, the way the service will. Four active people: staff, owner, manager, outsider.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_hr_people import make_other_tenant_owner, make_people_manager, ok
from tests.test_hr_people_records import db_today, leave_body, versioned
from tests.test_hr_persistence import HrWorld, hr_database, world
from tests.test_hr_process_instances import (
    START_SQL,
    add_person,
    events,
    left_before_0634,
    processes,
    run,
    start_body,
    tasks,
)
from tests.test_hr_process_templates import (
    apply_in_other_tenant,
    as_hr,
    create,
    create_body,
    deactivate,
    department,
    get,
    match,
    set_profile,
    step,
)

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

PREVIEW_SQL: LiteralString = "SELECT public.hr_template_preview(%s,%s,%s) AS preview"
INTERNAL_FUNCTIONS = (
    "hr_template_rules_parse(uuid,jsonb)",
    "hr_template_ties(uuid,text,uuid,text,text,text)",
)


async def preview(
    world: HrWorld,
    rules: Any,
    kind: str = "onboarding",
    *,
    who: str = "owner",
    tenant: UUID | None = None,
) -> dict[str, Any] | None:
    rows = await as_hr(world, who, PREVIEW_SQL, (tenant or world.tenant, kind, Jsonb(rules)))
    return rows[0]["preview"]


async def ties(world: HrWorld, rules: Any, kind: str = "onboarding") -> list[dict[str, Any]]:
    found = await preview(world, rules, kind)
    assert found is not None
    return found["ties"]


def tie(template: UUID, name: str) -> dict[str, Any]:
    return {"template_id": str(template), "name": name}


async def receipts(world: HrWorld) -> int:
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s AND operation='process.start'",
        (world.tenant,),
    )
    return rows[0]["n"]


# ---- The preview ------------------------------------------------------------------------------


async def test_preview_counts_people_each_rule_matches(world: HrWorld) -> None:
    ops, sales = await department(world, "Operations"), await department(world, "Sales")
    staff, manager = world.employees["staff"], world.employees["manager"]
    owner, outsider = world.employees["owner"], world.employees["outsider"]
    await set_profile(
        world,
        staff,
        department_id=ops,
        employment_type="full_time",
        legal_entity_reference="  Synthetic Holdings Sdn Bhd ",
        region="sabah",
    )
    await set_profile(
        world,
        manager,
        department_id=ops,
        employment_type="part_time",
        legal_entity_reference="Synthetic Other Sdn Bhd",
        region="sarawak",
    )
    await set_profile(
        world, owner, department_id=sales, employment_type="full_time", region="sabah"
    )
    # outsider keeps no profile: only the empty rule set reaches them.
    # Someone who has left matches every rule here and is never counted.
    gone = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,is_active,department_id,"
        "employment_type,legal_entity_reference,region) VALUES (%s,%s,'Synthetic Gone',false,%s,"
        "'full_time','Synthetic Holdings Sdn Bhd','sabah')",
        (world.tenant, gone, ops),
    )
    # Another company's person with the same profile is never counted either.
    await world.q(
        "UPDATE hr_employees SET employment_type='full_time', region='sabah' WHERE client_id=%s",
        (world.other_tenant,),
    )
    expected: list[tuple[Any, int]] = [
        ({}, 4),
        (None, 4),
        ({"department_id": str(ops)}, 2),
        ({"department_id": str(sales)}, 1),
        ({"employment_type": "full_time"}, 2),
        ({"employment_type": "intern"}, 0),
        # Trimmed and without case, on both sides.
        ({"legal_entity_reference": " synthetic HOLDINGS sdn bhd "}, 1),
        ({"legal_entity_reference": "Synthetic Holdings"}, 0),
        ({"region": "sabah"}, 2),
        ({"region": "sarawak"}, 1),
        ({"department_id": str(ops), "employment_type": "full_time", "region": "sabah"}, 1),
        ({"department_id": str(sales), "employment_type": "part_time"}, 0),
        # An empty or blank rule is no rule, as the template write stores it.
        ({"region": "  ", "legal_entity_reference": "", "department_id": None}, 4),
    ]
    for rules, matching in expected:
        assert await preview(world, rules) == {
            "matching": matching,
            "ties": [],
            "total_active": 4,
        }, rules
    # The count is the people hr_process_match would give a template with these rules, when it is
    # the only template of the kind.
    for rules in ({"department_id": str(ops)}, {"region": "sabah", "employment_type": "full_time"}):
        template = await create(world, rules=rules)
        found = await preview(world, rules)
        assert found is not None
        chosen = [p for p in (staff, manager, owner, outsider) if await match(world, p) == template]
        assert found["matching"] == len(chosen) > 0
        await deactivate(world, template, 1)


async def test_preview_validates_rules_like_the_template_write(world: HrWorld) -> None:
    foreign = await department(world, "Foreign", world.other_tenant)
    for kind, rules, code in (
        ("probation", {}, "hr_template_kind_invalid"),
        ("onboarding", [], "hr_template_rules_invalid"),
        ("onboarding", "sabah", "hr_template_rules_invalid"),
        ("onboarding", {"colour": "red"}, "hr_template_rules_invalid"),
        ("onboarding", {"region": 1}, "hr_template_rules_invalid"),
        ("onboarding", {"region": "mars"}, "hr_template_rules_invalid"),
        ("onboarding", {"employment_type": "volunteer"}, "hr_template_rules_invalid"),
        ("onboarding", {"legal_entity_reference": "x" * 201}, "hr_template_rules_invalid"),
        ("onboarding", {"department_id": "not-a-uuid"}, "hr_department_unavailable"),
        ("onboarding", {"department_id": str(uuid4())}, "hr_department_unavailable"),
        ("onboarding", {"department_id": str(foreign)}, "hr_department_unavailable"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=rf"^{code}\b"):
            await preview(world, rules, kind)
        # The template write refuses the same rules with the same code.
        if kind == "onboarding":
            with pytest.raises(psycopg.errors.CheckViolation, match=rf"^{code}\b"):
                await create(world, rules=rules)


async def test_preview_lists_ties(world: HrWorld) -> None:
    ops, sales = await department(world, "Operations"), await department(world, "Sales")
    by_ops = await create(world, name="By department", rules={"department_id": str(ops)})
    by_type = await create(world, name="By type", rules={"employment_type": "full_time"})
    by_sales = await create(world, name="Sales", rules={"department_id": str(sales)})
    await create(
        world,
        name="More specific",
        rules={"department_id": str(ops), "employment_type": "full_time"},
    )
    leaving = await create(world, kind="offboarding", rules={"department_id": str(ops)})
    retired = await create(world, name="Retired", rules={"region": "sabah"})
    await deactivate(world, retired, 1)
    await create(world, kind="custom", rules={"department_id": str(ops)})

    # As many set rules and all can hold for one person: Operations ties with the type rule, not
    # with Sales; the more specific one and the other kinds never tie; inactive ones never.
    assert await ties(world, {"department_id": str(ops)}) == [
        tie(by_ops, "By department"),
        tie(by_type, "By type"),
    ]
    assert await ties(world, {"region": "sabah"}) == [
        tie(by_ops, "By department"),
        tie(by_type, "By type"),
        tie(by_sales, "Sales"),
    ]
    assert await ties(world, {"department_id": str(ops)}, "offboarding") == [
        tie(leaving, "Synthetic template")
    ]
    # Custom templates are started by hand, so they never tie.
    assert await ties(world, {"department_id": str(ops)}, "custom") == []
    # A default ties only with another default.
    assert await ties(world, {}) == []
    default = await create(world, name="Default")
    assert await ties(world, None) == [tie(default, "Default")]
    # The entity compares trimmed and without case.
    entity = await create(
        world,
        name="Entity",
        rules={"department_id": str(sales), "legal_entity_reference": "Synthetic Sdn Bhd"},
    )
    assert await ties(
        world, {"department_id": str(sales), "legal_entity_reference": " SYNTHETIC sdn bhd "}
    ) == [tie(entity, "Entity")]
    # The same definition as the conflicts a save reports: a saved template's conflicts are the
    # preview of its rules, without itself.
    for template, rules in (
        (by_ops, {"department_id": str(ops)}),
        (by_type, {"employment_type": "full_time"}),
        (default, {}),
    ):
        saved = await get(world, "owner", template)
        assert saved is not None
        previewed = [t["template_id"] for t in await ties(world, rules)]
        assert saved["conflicts"] == [t for t in previewed if t != str(template)]


async def test_preview_is_hr_team_only_and_tenant_isolated(world: HrWorld) -> None:
    ops = await department(world, "Operations")
    mine = await create(world, name="Ours", rules={"employment_type": "full_time"})
    # Anyone outside the HR team gets nothing, not even a validation error.
    for who in ("staff", "manager", "admin", "operator"):
        assert await preview(world, {}, who=who) is None, who
        assert await preview(world, {"region": "mars"}, who=who) is None, who
    await make_people_manager(world)
    assert await preview(world, {}, who="operator") == {
        "matching": 4,
        "ties": [],
        "total_active": 4,
    }
    # The HR owner naming another company gets nothing.
    assert await preview(world, {}, tenant=world.other_tenant) is None
    # Another company's templates, people and departments never show here.
    await make_other_tenant_owner(world)
    await apply_in_other_tenant(world, create_body(rules={"employment_type": "part_time"}))
    await apply_in_other_tenant(world, create_body(rules={"employment_type": "full_time"}))
    await world.q(
        "UPDATE hr_employees SET employment_type='full_time' WHERE client_id=%s",
        (world.other_tenant,),
    )
    assert await preview(world, {"region": "sabah"}) == {
        "matching": 0,
        "ties": [tie(mine, "Ours")],
        "total_active": 4,
    }
    found = await preview(world, {"employment_type": "full_time"})
    assert found == {"matching": 0, "ties": [tie(mine, "Ours")], "total_active": 4}
    theirs = await department(world, "Theirs", world.other_tenant)
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_department_unavailable\b"):
        await preview(world, {"department_id": str(theirs)})
    assert (await preview(world, {"department_id": str(ops)})) == {
        "matching": 0,
        "ties": [tie(mine, "Ours")],
        "total_active": 4,
    }


async def test_preview_function_is_a_pinned_definer_granted_to_the_app_only(
    world: HrWorld,
) -> None:
    for signature in ("hr_template_preview(uuid,text,jsonb)", *INTERNAL_FUNCTIONS):
        rows = await world.q(
            "SELECT p.prosecdef, p.proconfig,"
            " has_function_privilege('csa_app', p.oid, 'EXECUTE') AS csa_app,"
            " has_function_privilege('csa_ro', p.oid, 'EXECUTE') AS csa_ro,"
            " has_function_privilege('csa_identity', p.oid, 'EXECUTE') AS csa_identity,"
            " has_function_privilege('csa_hr_maintenance', p.oid, 'EXECUTE') AS maintenance,"
            " has_function_privilege('public', p.oid, 'EXECUTE') AS everyone"
            " FROM pg_proc p WHERE p.oid=%s::regprocedure",
            (f"public.{signature}",),
        )
        [row] = rows
        assert row["prosecdef"] is True, signature
        assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"], signature
        assert row["csa_app"] is (signature not in INTERNAL_FUNCTIONS), signature
        assert not (row["csa_ro"] or row["csa_identity"] or row["maintenance"]), signature
        assert not row["everyone"], signature
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await as_hr(
            world,
            "owner",
            "SELECT * FROM public.hr_template_rules_parse(%s,%s)",
            (world.tenant, Jsonb({})),
        )


# ---- The manual start -------------------------------------------------------------------------


async def test_manual_start_refuses_when_one_is_active_unless_replace(world: HrWorld) -> None:
    staff = world.employees["staff"]
    await create(world)
    first = await run(world, "owner", START_SQL, start_body(staff, "onboarding"))
    before = (await processes(world, staff), await receipts(world))
    for body in (
        start_body(staff, "onboarding"),
        {**start_body(staff, "onboarding"), "replace": False},
        {**start_body(staff, "onboarding"), "replace": None},
        # Only JSON true replaces.
        {**start_body(staff, "onboarding"), "replace": "true"},
        {**start_body(staff, "onboarding"), "replace": 1},
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_process_already_active\b"):
            await run(world, "owner", START_SQL, body)
    assert (await processes(world, staff), await receipts(world)) == before
    assert [(str(p["id"]), p["status"]) for p in before[0]] == [(first["process_id"], "active")]
    # An offboarding the trigger started is just as active.
    await create(world, kind="offboarding")
    today = await db_today(world)
    await ok(world, "owner", await leave_body(world, staff, today + timedelta(days=30)))
    [leaving] = [p for p in await processes(world, staff) if p["kind"] == "offboarding"]
    assert leaving["status"] == "active"
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_process_already_active\b"):
        await run(world, "owner", START_SQL, start_body(staff, "offboarding"))
    # Custom processes may run side by side; replace means nothing for them.
    custom = await create(world, kind="custom", name="Synthetic plan")
    await run(world, "owner", START_SQL, start_body(staff, "custom", custom))
    await run(world, "owner", START_SQL, start_body(staff, "custom", custom))
    found = [(p["kind"], p["status"]) for p in await processes(world, staff)]
    assert found == [
        ("onboarding", "active"),
        ("offboarding", "active"),
        ("custom", "active"),
        ("custom", "active"),
    ]


async def test_manual_start_replace_cancels_the_old_process_with_an_event(world: HrWorld) -> None:
    staff, outsider = world.employees["staff"], world.employees["outsider"]
    await create(world, steps=[step(title_en="First"), step(title_en="Second")])
    old = await run(world, "owner", START_SQL, start_body(staff, "onboarding"))
    old_id = UUID(old["process_id"])
    await world.q(
        "UPDATE hr_process_tasks SET status='done', completed_at=now()"
        " WHERE client_id=%s AND process_id=%s AND ordinal=1",
        (world.tenant, old_id),
    )
    await make_people_manager(world)
    key = uuid4()
    body = {**start_body(staff, "onboarding"), "replace": True}
    new = await run(world, "operator", START_SQL, body, key=key)
    assert new == {**old, "process_id": new["process_id"]}
    new_id = UUID(new["process_id"])
    assert [(p["id"], p["status"]) for p in await processes(world, staff)] == [
        (old_id, "cancelled"),
        (new_id, "active"),
    ]
    assert [t["status"] for t in await tasks(world, old_id)] == ["done", "skipped"]
    logged = await events(world, old_id)
    [cancelled] = [e for e in logged if e["action"] == "process.cancelled"]
    assert (cancelled["detail"], cancelled["actor"]) == (
        {"reason": "superseded"},
        world.users["operator"],
    )
    [skipped] = [e for e in logged if e["action"] == "task.skipped"]
    assert (skipped["ordinal"], skipped["detail"]) == (2, {"reason": "process_cancelled"})
    # A retry of the same request returns the first receipt and replaces nothing again.
    assert await run(world, "operator", START_SQL, body, key=key) == {**new, "duplicate": True}
    assert len(await processes(world, staff)) == 2
    # With nothing active, replace simply starts one.
    fresh = await run(
        world, "owner", START_SQL, {**start_body(outsider, "onboarding"), "replace": True}
    )
    assert [(str(p["id"]), p["status"]) for p in await processes(world, outsider)] == [
        (fresh["process_id"], "active")
    ]


async def test_trigger_paths_still_supersede(world: HrWorld) -> None:
    """The person writes never ask: a rehire closes the old onboarding itself, and a leave that
    meets a stale active offboarding supersedes it (ruling R1), with no refusal."""
    today = await db_today(world)
    await create(world, steps=[step(title_en="Fill in details", owner_role="employee")])
    await create(world, kind="offboarding", steps=[step(anchor="last_day")])
    person = await add_person(world)
    [first] = await processes(world, person)
    await left_before_0634(world, person)
    rehire = await versioned(world, "person.rehire", person, start_date=today.isoformat())
    await ok(world, "owner", rehire)
    old, new = await processes(world, person)
    assert (old["id"], old["status"], new["kind"], new["status"]) == (
        first["id"],
        "cancelled",
        "onboarding",
        "active",
    )
    [cancelled] = [e for e in await events(world, old["id"]) if e["action"] == "process.cancelled"]
    assert cancelled["detail"] == {"reason": "rehired"}

    # Leaving starts an offboarding; old data may hold it active with the last day cleared.
    await ok(world, "owner", await leave_body(world, person, today + timedelta(days=30)))
    [stale] = [p for p in await processes(world, person) if p["kind"] == "offboarding"]
    async with world.owner_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("ALTER TABLE hr_employees DISABLE TRIGGER hr_process_on_employee")
            await conn.execute(
                "UPDATE hr_employees SET last_day=NULL, leaving_reason=NULL, leaving_note=NULL"
                " WHERE client_id=%s AND id=%s",
                (world.tenant, person),
            )
            await conn.execute("ALTER TABLE hr_employees ENABLE TRIGGER hr_process_on_employee")
    await ok(world, "owner", await leave_body(world, person, today + timedelta(days=40)))
    offboardings = [p for p in await processes(world, person) if p["kind"] == "offboarding"]
    assert [(p["id"] == stale["id"], p["status"]) for p in offboardings] == [
        (True, "cancelled"),
        (False, "active"),
    ]
    [superseded] = [
        e for e in await events(world, stale["id"]) if e["action"] == "process.cancelled"
    ]
    assert superseded["detail"] == {"reason": "superseded"}
