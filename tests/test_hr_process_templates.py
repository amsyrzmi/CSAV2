"""Migration 0631: writing and reading HR process templates, and picking the template for a person
(disposable database). Reuses the HrWorld fixture of tests/test_hr_persistence.py: every call runs
as csa_app inside the caller's HR session, the way the service will.
"""

from __future__ import annotations

import re
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.tenancy import tx
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_hr_people import make_other_tenant_owner, make_people_manager
from tests.test_hr_persistence import HrWorld, hr_database, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

FP = "a" * 64
OTHER_FP = "b" * 64
APPLY_SQL = "SELECT public.hr_template_apply(%s,%s,%s,%s) AS result"
ROWS_SQL = "SELECT * FROM public.hr_template_rows(%s)"
GET_SQL = "SELECT public.hr_template_get(%s,%s) AS template"
MATCH_SQL = "SELECT public.hr_process_match(%s,%s,%s) AS template_id"
ANCHOR = {"onboarding": "start_date", "offboarding": "last_day", "custom": "probation_end"}
PUBLIC_FUNCTIONS = (
    "hr_template_apply(uuid,uuid,text,jsonb)",
    "hr_template_rows(uuid)",
    "hr_template_get(uuid,uuid)",
)
INTERNAL_FUNCTIONS = (
    "hr_template_write_rules(uuid,uuid,jsonb)",
    "hr_template_write_steps(uuid,uuid,text,jsonb)",
    "hr_template_conflicts(uuid,uuid)",
    "hr_template_warnings(uuid,uuid)",
    "hr_process_match(uuid,uuid,text)",
)
# Stand-ins replaced inside the test, where the world exists.
OTHER_TENANT_EMPLOYEE = "other-tenant-employee"
INACTIVE_EMPLOYEE = "inactive-employee"
OTHER_TENANT_DEPARTMENT = "other-tenant-department"


def step(**over: Any) -> dict[str, Any]:
    return {
        "title_en": "Sign the contract",
        "owner_role": "hr",
        "anchor": "start_date",
        "due_offset_days": 0,
        **over,
    }


def create_body(
    kind: str = "onboarding",
    name: str = "Synthetic template",
    rules: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "operation": "template.create",
        "kind": kind,
        "name": name,
        "rules": rules if rules is not None else {},
        "steps": steps if steps is not None else [step(anchor=ANCHOR.get(kind, "start_date"))],
    }


async def as_hr(
    world: HrWorld, who: str, sql: LiteralString, params: tuple[object, ...]
) -> list[dict[str, Any]]:
    """Run `sql` as csa_app inside `who`'s HR session of world.tenant."""
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof(who))
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()


async def apply(
    world: HrWorld,
    who: str,
    body: dict[str, Any],
    *,
    key: UUID | None = None,
    fingerprint: str = FP,
    tenant: UUID | None = None,
) -> dict[str, Any]:
    rows = await as_hr(
        world, who, APPLY_SQL, (tenant or world.tenant, key or uuid4(), fingerprint, Jsonb(body))
    )
    return rows[0]["result"]


async def apply_in_other_tenant(world: HrWorld, body: dict[str, Any]) -> dict[str, Any]:
    """The other tenant's HR owner (make_other_tenant_owner) writes in the other tenant."""
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        await conn.execute(
            "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users["owner"]),)
        )
        cursor = await conn.execute(APPLY_SQL, (world.other_tenant, uuid4(), FP, Jsonb(body)))
        row = await cursor.fetchone()
    assert row is not None
    return row["result"]


async def create(world: HrWorld, who: str = "owner", **kwargs: Any) -> UUID:
    return UUID((await apply(world, who, create_body(**kwargs)))["template_id"])


async def deactivate(world: HrWorld, template: UUID, version: int) -> dict[str, Any]:
    body = {
        "operation": "template.deactivate",
        "template_id": str(template),
        "expected_version": version,
    }
    return await apply(world, "owner", body)


async def get(
    world: HrWorld, who: str, template: UUID, tenant: UUID | None = None
) -> dict[str, Any] | None:
    rows = await as_hr(world, who, GET_SQL, (tenant or world.tenant, template))
    return rows[0]["template"]


async def template_rows(world: HrWorld, who: str) -> list[dict[str, Any]]:
    return await as_hr(world, who, ROWS_SQL, (world.tenant,))


async def match(
    world: HrWorld, employee: UUID, kind: str = "onboarding", tenant: UUID | None = None
) -> UUID | None:
    rows = await world.q(MATCH_SQL, (tenant or world.tenant, employee, kind))
    return rows[0]["template_id"]


async def department(world: HrWorld, name: str, tenant: UUID | None = None) -> UUID:
    rows = await world.q(
        "INSERT INTO hr_departments(client_id,id,name) VALUES (%s,gen_random_uuid(),%s)"
        " RETURNING id",
        (tenant or world.tenant, name),
    )
    return rows[0]["id"]


async def set_profile(
    world: HrWorld,
    employee: UUID,
    *,
    department_id: UUID | None = None,
    employment_type: str | None = None,
    legal_entity_reference: str | None = None,
    region: str | None = None,
) -> None:
    await world.q(
        "UPDATE hr_employees SET department_id=%s, employment_type=%s, legal_entity_reference=%s,"
        " region=%s WHERE client_id=%s AND id=%s",
        (department_id, employment_type, legal_entity_reference, region, world.tenant, employee),
    )


async def template_count(world: HrWorld) -> int:
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_process_templates WHERE client_id=%s", (world.tenant,)
    )
    return rows[0]["n"]


async def template_audit(world: HrWorld) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT action, actor_id, target_id, details FROM console_audit"
        " WHERE client_id=%s AND action LIKE 'hr.template%%' ORDER BY created_at, id",
        (world.tenant,),
    )


async def test_create_update_deactivate_template_bumps_version_and_writes_audit(
    world: HrWorld,
) -> None:
    await make_people_manager(world)
    ops = await department(world, "Operations")
    manager = world.employees["manager"]
    created = await apply(
        world,
        "owner",
        create_body(
            name="  Synthetic onboarding  ",
            rules={
                "department_id": str(ops),
                "employment_type": " full_time ",
                "legal_entity_reference": "   ",
                "region": " sabah ",
            },
            steps=[
                step(
                    title_en="  Sign the contract  ",
                    title_ms=" ",
                    description_en="Bring the identity card",
                    owner_role="manager",
                    due_offset_days=-3,
                    approval_by="hr",
                    auto_check="document_uploaded:contract",
                    reference_note="Synthetic reference",
                ),
                step(
                    title_en="Probation review",
                    title_ms="Semakan percubaan",
                    anchor="probation_end",
                    owner_role="person",
                    owner_employee_id=str(manager),
                    required=False,
                ),
            ],
        ),
    )
    template = UUID(created["template_id"])
    assert created == {
        "operation": "template.create",
        "template_id": str(template),
        "version": 1,
        "conflicts": [],
        "warnings": [],
        "status": "saved",
        "duplicate": False,
    }
    got = await get(world, "owner", template)
    assert got is not None
    assert (got["kind"], got["name"], got["version"], got["is_active"]) == (
        "onboarding",
        "Synthetic onboarding",
        1,
        True,
    )
    # Rule text is trimmed and an empty string is stored as NULL.
    assert got["rules"] == {
        "department_id": str(ops),
        "employment_type": "full_time",
        "legal_entity_reference": None,
        "region": "sabah",
    }
    assert got["steps"] == [
        {
            "ordinal": 1,
            "title_en": "Sign the contract",
            "title_ms": None,
            "description_en": "Bring the identity card",
            "description_ms": None,
            "owner_role": "manager",
            "owner_employee_id": None,
            "anchor": "start_date",
            "due_offset_days": -3,
            "required": True,
            "approval_by": "hr",
            "auto_check": "document_uploaded:contract",
            "reference_note": "Synthetic reference",
        },
        {
            "ordinal": 2,
            "title_en": "Probation review",
            "title_ms": "Semakan percubaan",
            "description_en": None,
            "description_ms": None,
            "owner_role": "person",
            "owner_employee_id": str(manager),
            "anchor": "probation_end",
            "due_offset_days": 0,
            "required": False,
            "approval_by": None,
            "auto_check": None,
            "reference_note": None,
        },
    ]
    assert got["conflicts"] == [] and got["warnings"] == []
    [row] = await template_rows(world, "operator")
    assert row["template_id"] == template and row["step_count"] == 2 and row["version"] == 1
    assert (row["department_id"], row["employment_type"], row["region"]) == (
        ops,
        "full_time",
        "sabah",
    )
    assert row["legal_entity_reference"] is None
    # A people.manage holder edits it: version + 1, rules and steps replaced.
    update = {
        "operation": "template.update",
        "template_id": str(template),
        "expected_version": 1,
        "name": "Renamed",
        "rules": {"legal_entity_reference": "", "region": None},
        "steps": [step(title_en="Only step")],
        "is_active": True,
    }
    updated = await apply(world, "operator", update)
    assert (updated["operation"], updated["version"]) == ("template.update", 2)
    got = await get(world, "operator", template)
    assert got is not None
    assert got["name"] == "Renamed" and got["version"] == 2
    assert got["rules"] == {
        "department_id": None,
        "employment_type": None,
        "legal_entity_reference": None,
        "region": None,
    }
    assert [s["title_en"] for s in got["steps"]] == ["Only step"]
    rules_rows = await world.q(
        "SELECT count(*) AS n FROM hr_process_template_rules WHERE client_id=%s", (world.tenant,)
    )
    assert rules_rows[0]["n"] == 0
    # A refused edit leaves the template as it was.
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_template_step_title_invalid"):
        await apply(
            world,
            "operator",
            {**update, "expected_version": 2, "steps": [step(title_en=" ")]},
        )
    got = await get(world, "owner", template)
    assert got is not None and got["version"] == 2 and len(got["steps"]) == 1
    deactivated = await deactivate(world, template, 2)
    assert (deactivated["operation"], deactivated["version"]) == ("template.deactivate", 3)
    got = await get(world, "owner", template)
    assert got is not None and got["is_active"] is False and got["version"] == 3
    audit = await template_audit(world)
    assert [(a["action"], a["actor_id"], a["target_id"]) for a in audit] == [
        ("hr.template_created", world.users["owner"], str(template)),
        ("hr.template_updated", world.users["operator"], str(template)),
        ("hr.template_deactivated", world.users["owner"], str(template)),
    ]
    assert [(a["details"]["version"], a["details"]["is_active"]) for a in audit] == [
        (1, True),
        (2, True),
        (3, False),
    ]
    assert audit[1]["details"] == {
        "template_id": str(template),
        "kind": "onboarding",
        "name": "Renamed",
        "version": 2,
        "is_active": True,
    }


@pytest.mark.parametrize(
    ("body_over", "step_over", "code"),
    [
        pytest.param({"kind": "probation"}, {}, "hr_template_kind_invalid", id="kind"),
        pytest.param({"name": "  "}, {}, "hr_template_name_invalid", id="blank-name"),
        pytest.param({"name": "x" * 121}, {}, "hr_template_name_invalid", id="long-name"),
        pytest.param({"name": 7}, {}, "hr_template_name_invalid", id="number-name"),
        pytest.param(
            {"rules": {"employment_category": "Executive"}},
            {},
            "hr_template_rules_invalid",
            id="category-is-not-a-rule",
        ),
        pytest.param({"rules": ["sabah"]}, {}, "hr_template_rules_invalid", id="rules-list"),
        pytest.param({"rules": {"region": 3}}, {}, "hr_template_rules_invalid", id="rule-number"),
        pytest.param(
            {"rules": {"region": "Selangor"}}, {}, "hr_template_rules_invalid", id="rule-region"
        ),
        pytest.param(
            {"rules": {"employment_type": "Full time"}},
            {},
            "hr_template_rules_invalid",
            id="rule-employment-type",
        ),
        pytest.param(
            {"rules": {"legal_entity_reference": "x" * 201}},
            {},
            "hr_template_rules_invalid",
            id="rule-entity-long",
        ),
        pytest.param(
            {"rules": {"department_id": OTHER_TENANT_DEPARTMENT}},
            {},
            "hr_department_unavailable",
            id="rule-department-other-tenant",
        ),
        pytest.param(
            {"rules": {"department_id": "not-a-uuid"}},
            {},
            "hr_department_unavailable",
            id="rule-department-malformed",
        ),
        pytest.param({"steps": []}, {}, "hr_template_steps_invalid", id="no-steps"),
        pytest.param({"steps": [step()] * 41}, {}, "hr_template_steps_invalid", id="41-steps"),
        pytest.param({"steps": step()}, {}, "hr_template_steps_invalid", id="steps-object"),
        pytest.param({"steps": ["Sign"]}, {}, "hr_template_steps_invalid", id="step-string"),
        pytest.param({}, {"ordinal": 1}, "hr_template_steps_invalid", id="unknown-step-key"),
        pytest.param({}, {"required": "yes"}, "hr_template_steps_invalid", id="required-text"),
        pytest.param({}, {"title_ms": 5}, "hr_template_steps_invalid", id="title-number"),
        pytest.param({}, {"title_en": "  "}, "hr_template_step_title_invalid", id="blank-title"),
        pytest.param({}, {"title_en": None}, "hr_template_step_title_invalid", id="no-title"),
        pytest.param(
            {}, {"title_en": "x" * 161}, "hr_template_step_title_invalid", id="long-title"
        ),
        pytest.param(
            {}, {"title_ms": "x" * 161}, "hr_template_step_title_invalid", id="long-malay-title"
        ),
        pytest.param(
            {},
            {"description_en": "x" * 2001},
            "hr_template_step_text_invalid",
            id="long-description",
        ),
        pytest.param(
            {}, {"reference_note": "x" * 501}, "hr_template_step_text_invalid", id="long-note"
        ),
        pytest.param({}, {"owner_role": "boss"}, "hr_template_step_owner_invalid", id="role"),
        pytest.param(
            {},
            {"owner_employee_id": INACTIVE_EMPLOYEE},
            "hr_template_step_owner_invalid",
            id="employee-on-hr-step",
        ),
        pytest.param(
            {},
            {"owner_role": "person"},
            "hr_template_step_person_unavailable",
            id="person-without-employee",
        ),
        pytest.param(
            {},
            {"owner_role": "person", "owner_employee_id": OTHER_TENANT_EMPLOYEE},
            "hr_template_step_person_unavailable",
            id="person-other-tenant",
        ),
        pytest.param(
            {},
            {"owner_role": "person", "owner_employee_id": INACTIVE_EMPLOYEE},
            "hr_template_step_person_unavailable",
            id="person-inactive",
        ),
        pytest.param(
            {},
            {"owner_role": "person", "owner_employee_id": "someone"},
            "hr_template_step_person_unavailable",
            id="person-malformed",
        ),
        pytest.param(
            {}, {"anchor": "last_day"}, "hr_template_step_anchor_invalid", id="onboarding-last-day"
        ),
        pytest.param(
            {"kind": "offboarding"},
            {"anchor": "start_date"},
            "hr_template_step_anchor_invalid",
            id="offboarding-start-date",
        ),
        pytest.param(
            {"kind": "offboarding"},
            {"anchor": "probation_end"},
            "hr_template_step_anchor_invalid",
            id="offboarding-probation-end",
        ),
        pytest.param({}, {"anchor": "hire_date"}, "hr_template_step_anchor_invalid", id="anchor"),
        pytest.param(
            {}, {"due_offset_days": 366}, "hr_template_step_offset_invalid", id="offset-366"
        ),
        pytest.param(
            {}, {"due_offset_days": -366}, "hr_template_step_offset_invalid", id="offset-minus-366"
        ),
        pytest.param(
            {}, {"due_offset_days": 1.5}, "hr_template_step_offset_invalid", id="offset-fraction"
        ),
        pytest.param(
            {}, {"due_offset_days": "3"}, "hr_template_step_offset_invalid", id="offset-text"
        ),
        pytest.param(
            {}, {"approval_by": "ceo"}, "hr_template_step_approval_invalid", id="approval"
        ),
        pytest.param(
            {}, {"auto_check": "bank_verified"}, "hr_template_step_check_invalid", id="check"
        ),
        pytest.param(
            {},
            {"auto_check": "document_uploaded:payslip"},
            "hr_template_step_check_invalid",
            id="document-category-unknown",
        ),
        pytest.param(
            {},
            {"auto_check": "document_uploaded:"},
            "hr_template_step_check_invalid",
            id="document-category-missing",
        ),
    ],
)
async def test_steps_and_rules_validated(
    world: HrWorld, body_over: dict[str, Any], step_over: dict[str, Any], code: str
) -> None:
    outsider = world.employees["outsider"]
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, outsider),
    )
    stand_ins = {
        OTHER_TENANT_EMPLOYEE: str(uuid4()),
        INACTIVE_EMPLOYEE: str(outsider),
        OTHER_TENANT_DEPARTMENT: str(await department(world, "Foreign", world.other_tenant)),
    }
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,'Synthetic Stranger')",
        (world.other_tenant, stand_ins[OTHER_TENANT_EMPLOYEE]),
    )
    kind = body_over.get("kind", "onboarding")
    resolved = {k: stand_ins.get(v, v) if isinstance(v, str) else v for k, v in step_over.items()}
    body = create_body(kind, steps=[step(**{"anchor": ANCHOR.get(kind, "start_date"), **resolved})])
    body.update(body_over)
    if isinstance(body["rules"], dict):
        body["rules"] = {
            k: stand_ins.get(v, v) if isinstance(v, str) else v for k, v in body["rules"].items()
        }
    with pytest.raises(psycopg.errors.CheckViolation, match=rf"^{code}\b"):
        await apply(world, "owner", body)
    assert await template_count(world) == 0
    receipts = await world.q(
        "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s", (world.tenant,)
    )
    assert receipts[0]["n"] == 0


def _quoted(definition: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'::text", definition))


async def _check_values(world: HrWorld, table: str, column: str) -> set[str]:
    rows = await world.q(
        "SELECT pg_get_constraintdef(c.oid) AS def FROM pg_constraint c"
        " WHERE c.conrelid=%s::regclass AND c.contype='c'",
        (f"public.{table}",),
    )
    [definition] = [r["def"] for r in rows if column in r["def"]]
    return _quoted(definition)


async def test_lists_in_the_validator_are_the_live_lists(world: HrWorld) -> None:
    """The validator's document categories, employment types and regions are the CHECK lists of
    hr_employee_documents.kind and hr_employees: a value added there must be added to the
    validator too, or no template could name it.
    """
    categories = await _check_values(world, "hr_employee_documents", "kind")
    assert categories == {"contract", "identity", "certificate", "other"}
    types = await _check_values(world, "hr_employees", "employment_type")
    assert types == {"full_time", "part_time", "contract", "intern"}
    regions = await _check_values(world, "hr_employees", "region")
    assert regions == {"peninsular_labuan", "sabah", "sarawak"}
    checks = ["account_linked", "personal_details_complete", "access_removed"]
    checks += [f"document_uploaded:{c}" for c in sorted(categories)]
    template = await create(
        world,
        kind="custom",
        steps=[
            step(anchor=anchor, auto_check=check)
            for anchor, check in zip(
                ["start_date", "last_day", "probation_end"] * 3, checks, strict=False
            )
        ],
    )
    got = await get(world, "owner", template)
    assert got is not None
    assert [s["auto_check"] for s in got["steps"]] == checks
    for value in sorted(types):
        await create(world, kind="custom", rules={"employment_type": value})
    for value in sorted(regions):
        await create(world, kind="custom", rules={"region": value})
    assert await template_count(world) == 1 + len(types) + len(regions)


async def test_matching_picks_most_specific_then_oldest(world: HrWorld) -> None:
    ops, sales = await department(world, "Operations"), await department(world, "Sales")
    staff, manager = world.employees["staff"], world.employees["manager"]
    await set_profile(world, staff, department_id=ops, employment_type="full_time", region="sabah")
    await set_profile(
        world, manager, department_id=sales, employment_type="full_time", region="sabah"
    )
    assert await match(world, staff) is None
    default = await create(world)
    assert await match(world, staff) == default
    assert await match(world, manager) == default
    by_department = await create(world, rules={"department_id": str(ops)})
    assert await match(world, staff) == by_department
    assert await match(world, manager) == default
    department_and_type = await create(
        world, rules={"department_id": str(ops), "employment_type": "full_time"}
    )
    assert await match(world, staff) == department_and_type
    # As specific and also matching, but newer: the oldest wins.
    newer_tie = await create(world, rules={"department_id": str(ops), "region": "sabah"})
    assert await match(world, staff) == department_and_type
    # An inactive template never matches.
    await deactivate(world, department_and_type, 1)
    assert await match(world, staff) == newer_tie
    # Templates of another kind never match.
    offboarding = await create(
        world,
        kind="offboarding",
        rules={"department_id": str(ops), "employment_type": "full_time", "region": "sabah"},
    )
    assert await match(world, staff, "onboarding") == newer_tie
    assert await match(world, staff, "offboarding") == offboarding
    assert await match(world, manager, "offboarding") is None


async def test_matching_is_trimmed_and_case_insensitive_for_entity(world: HrWorld) -> None:
    staff = world.employees["staff"]
    template = await create(
        world,
        rules={
            "legal_entity_reference": "  Synthetic Holdings Sdn Bhd ",
            "employment_type": "intern",
        },
    )
    got = await get(world, "owner", template)
    assert got is not None
    assert got["rules"]["legal_entity_reference"] == "Synthetic Holdings Sdn Bhd"
    await set_profile(
        world,
        staff,
        employment_type="intern",
        legal_entity_reference="  synthetic HOLDINGS sdn bhd   ",
    )
    assert await match(world, staff) == template
    # Employment type is exact; another entity, or none, does not match.
    await set_profile(
        world,
        staff,
        employment_type="part_time",
        legal_entity_reference="Synthetic Holdings Sdn Bhd",
    )
    assert await match(world, staff) is None
    await set_profile(
        world, staff, employment_type="intern", legal_entity_reference="Synthetic Holdings"
    )
    assert await match(world, staff) is None
    await set_profile(world, staff, employment_type="intern")
    assert await match(world, staff) is None


async def test_no_match_returns_null(world: HrWorld) -> None:
    staff = world.employees["staff"]
    ops, sales = await department(world, "Operations"), await department(world, "Sales")
    await set_profile(world, staff, department_id=ops)
    assert await match(world, staff) is None
    await create(world, rules={"department_id": str(sales)})
    assert await match(world, staff) is None
    # A default that is no longer active matches nobody.
    default = await create(world)
    assert await match(world, staff) == default
    await deactivate(world, default, 1)
    assert await match(world, staff) is None
    assert await match(world, uuid4()) is None
    # The other tenant's default is never this tenant's, though its employee has the same id.
    await make_other_tenant_owner(world)
    theirs = UUID((await apply_in_other_tenant(world, create_body()))["template_id"])
    assert await match(world, staff) is None
    assert await match(world, staff, tenant=world.other_tenant) == theirs


async def test_tie_is_reported_as_conflict_at_save(world: HrWorld) -> None:
    ops, sales = await department(world, "Operations"), await department(world, "Sales")
    first = await apply(world, "owner", create_body(rules={"department_id": str(ops)}))
    assert first["conflicts"] == []
    a = UUID(first["template_id"])
    # One rule each and both can hold for one person: a tie.
    second = await apply(world, "owner", create_body(rules={"employment_type": "full_time"}))
    b = UUID(second["template_id"])
    assert second["conflicts"] == [str(a)]
    # Another department cannot hold together with Operations, but can with full_time.
    third = await apply(world, "owner", create_body(rules={"department_id": str(sales)}))
    c = UUID(third["template_id"])
    assert third["conflicts"] == [str(b)]
    # More specific: no tie. Another kind: no tie. The entity compares without case.
    more = await apply(
        world,
        "owner",
        create_body(rules={"department_id": str(ops), "employment_type": "full_time"}),
    )
    assert more["conflicts"] == []
    other_kind = await apply(
        world,
        "owner",
        create_body(kind="offboarding", rules={"department_id": str(ops)}),
    )
    assert other_kind["conflicts"] == []
    entity = await apply(
        world,
        "owner",
        create_body(
            rules={"department_id": str(ops), "legal_entity_reference": "Synthetic Sdn Bhd"}
        ),
    )
    entity_case = await apply(
        world,
        "owner",
        create_body(
            rules={"department_id": str(ops), "legal_entity_reference": "SYNTHETIC sdn bhd"}
        ),
    )
    assert entity_case["conflicts"] == [more["template_id"], entity["template_id"]]
    # Two defaults tie.
    default = await apply(world, "owner", create_body())
    assert default["conflicts"] == []
    second_default = await apply(world, "owner", create_body())
    assert second_default["conflicts"] == [default["template_id"]]
    # The reads show it to the author too; an inactive template no longer conflicts.
    got = await get(world, "owner", a)
    assert got is not None and got["conflicts"] == [str(b)]
    await deactivate(world, b, 1)
    got = await get(world, "owner", c)
    assert got is not None and got["conflicts"] == []
    got = await get(world, "owner", b)
    assert got is not None and got["conflicts"] == []
    # Custom templates are started by hand, so they never conflict.
    await create(world, kind="custom", steps=[step()])
    custom = await apply(world, "owner", create_body(kind="custom"))
    assert custom["conflicts"] == []


async def test_offboarding_late_person_step_warns(world: HrWorld) -> None:
    manager = str(world.employees["manager"])
    steps = [
        step(anchor="last_day", owner_role="employee", due_offset_days=1),
        step(anchor="last_day", owner_role="person", owner_employee_id=manager, due_offset_days=3),
        step(anchor="last_day", owner_role="hr", due_offset_days=5),
        step(anchor="last_day", owner_role="manager", due_offset_days=5),
        step(anchor="last_day", owner_role="employee", due_offset_days=0),
        step(anchor="last_day", owner_role="employee", due_offset_days=-2),
    ]
    result = await apply(world, "owner", create_body(kind="offboarding", steps=steps))
    expected = [
        {"code": "hr_template_step_after_last_day", "ordinal": 1},
        {"code": "hr_template_step_after_last_day", "ordinal": 2},
    ]
    assert result["warnings"] == expected
    got = await get(world, "owner", UUID(result["template_id"]))
    assert got is not None and got["warnings"] == expected
    # The same steps on onboarding (anchored on start_date) are not late.
    onboarding = [{**s, "anchor": "start_date"} for s in steps]
    result = await apply(world, "owner", create_body(kind="onboarding", steps=onboarding))
    assert result["warnings"] == []


async def test_non_hr_and_other_tenant_are_refused(world: HrWorld) -> None:
    template = await create(world)
    # A staff member and a manager without manage_people.
    for who in ("staff", "manager", "admin"):
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege, match=r"^hr_people_manager_required\b"
        ):
            await apply(world, who, create_body())
        assert await template_rows(world, who) == []
        assert await get(world, who, template) is None
    # The HR owner naming another tenant.
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege, match=r"^hr_people_manager_required\b"
    ):
        await apply(world, "owner", create_body(), tenant=world.other_tenant)
    # Another tenant's template id behaves as not found.
    await make_other_tenant_owner(world)
    theirs = UUID((await apply_in_other_tenant(world, create_body()))["template_id"])
    for body in (
        {
            "operation": "template.update",
            "template_id": str(theirs),
            "expected_version": 1,
            "name": "Taken",
            "rules": {},
            "steps": [step()],
            "is_active": True,
        },
        {"operation": "template.deactivate", "template_id": str(theirs), "expected_version": 1},
        {"operation": "template.deactivate", "template_id": "not-a-uuid", "expected_version": 1},
        {"operation": "template.deactivate", "expected_version": 1},
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_template_unavailable\b"):
            await apply(world, "owner", body)
    assert await get(world, "owner", theirs) is None
    assert await get(world, "owner", theirs, tenant=world.other_tenant) is None
    assert [r["template_id"] for r in await template_rows(world, "owner")] == [template]
    rows = await world.q(
        "SELECT name, version, is_active FROM hr_process_templates WHERE client_id=%s",
        (world.other_tenant,),
    )
    assert rows == [{"name": "Synthetic template", "version": 1, "is_active": True}]
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_operation_unsupported\b"):
        await apply(world, "owner", {"operation": "template.delete"})
    assert await template_count(world) == 1


async def test_functions_are_pinned_definers_and_only_the_public_ones_are_granted(
    world: HrWorld,
) -> None:
    for signature in (*PUBLIC_FUNCTIONS, *INTERNAL_FUNCTIONS):
        rows = await world.q(
            "SELECT p.prosecdef, p.proconfig,"
            " has_function_privilege('csa_app', p.oid, 'EXECUTE') AS csa_app,"
            " has_function_privilege('csa_ro', p.oid, 'EXECUTE') AS csa_ro,"
            " has_function_privilege('csa_identity', p.oid, 'EXECUTE') AS csa_identity,"
            " has_function_privilege('csa_hr_maintenance', p.oid, 'EXECUTE') AS maintenance"
            " FROM pg_proc p WHERE p.oid=%s::regprocedure",
            (f"public.{signature}",),
        )
        [row] = rows
        assert row["prosecdef"] is True, signature
        assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"], signature
        assert row["csa_app"] is (signature in PUBLIC_FUNCTIONS), signature
        assert not (row["csa_ro"] or row["csa_identity"] or row["maintenance"]), signature
    # csa_app cannot call an internal one directly.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await as_hr(
            world, "owner", MATCH_SQL, (world.tenant, world.employees["staff"], "onboarding")
        )


async def test_same_key_replay_changes_nothing_and_stale_version_is_refused(
    world: HrWorld,
) -> None:
    key = uuid4()
    body = create_body()
    created = await apply(world, "owner", body, key=key)
    again = await apply(world, "owner", body, key=key)
    assert again == {**created, "duplicate": True}
    assert await template_count(world) == 1
    assert len(await template_audit(world)) == 1
    receipts = await world.q(
        "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s AND request_key=%s",
        (world.tenant, key),
    )
    assert receipts[0]["n"] == 1
    with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_idempotency_conflict\b"):
        await apply(world, "owner", body, key=key, fingerprint=OTHER_FP)
    template = UUID(created["template_id"])
    update = {
        "operation": "template.update",
        "template_id": str(template),
        "expected_version": 1,
        "name": "Renamed",
        "rules": {},
        "steps": [step()],
        "is_active": True,
    }
    update_key = uuid4()
    updated = await apply(world, "owner", update, key=update_key)
    assert updated["version"] == 2
    # A retry after a lost response gets the first receipt, not the version check.
    assert await apply(world, "owner", update, key=update_key) == {**updated, "duplicate": True}
    for stale in (
        update,
        {**update, "expected_version": "2"},
        {**update, "expected_version": None},
        {"operation": "template.deactivate", "template_id": str(template), "expected_version": 1},
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=r"^hr_template_changed\b"):
            await apply(world, "owner", stale)
    got = await get(world, "owner", template)
    assert got is not None and got["version"] == 2 and got["is_active"] is True
    assert len(await template_audit(world)) == 2
