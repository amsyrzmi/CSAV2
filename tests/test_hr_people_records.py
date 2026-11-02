"""Migration 0521: the Sensitive details switch, personal details, documents, leaving
(disposable database). Reuses the HrWorld fixture of tests/test_hr_people.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.core.db import q
from csa.core.tenancy import tx
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_hr_people import (
    make_people_manager,
    read_as,
    read_as_other_tenant_owner,
    refused,
)
from tests.test_hr_persistence import HrWorld, hr_database, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]


async def make_sensitive_holder(world: HrWorld, name: str = "operator") -> None:
    await world.grant(name, "people.sensitive", "hr_operator")


async def test_people_sensitive_grant_check_and_resolution(world: HrWorld) -> None:
    # The owner's access.save accepts the new grant.
    saved = await world.call(
        "owner",
        "POST",
        "/console/v1/hr/operations",
        {
            "operation": "access.save",
            "user_id": str(world.users["operator"]),
            "grants": [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}],
            "workflows": [],
        },
    )
    assert saved.status_code == 200, saved.text
    stored = await world.q(
        "SELECT action,scope,fields FROM hr_grants WHERE client_id=%s AND user_id=%s "
        "AND action='people.sensitive'",
        (world.tenant, world.users["operator"]),
    )
    assert stored == [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}]
    # The CHECK keeps the hr_operator scope only, and no fields.
    for scope in ("self", "assigned_manager", "client_owner"):
        with pytest.raises(psycopg.errors.CheckViolation):
            await world.grant("staff", "people.sensitive", scope)
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("staff", "people.sensitive", "hr_operator", ["display_name"])
    # The earlier pairs are still there.
    await world.grant("manager", "people.manage", "hr_operator")
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("staff", "training.decide", "hr_operator")
    checks = await world.q(
        "SELECT conname FROM pg_constraint WHERE conrelid='public.hr_grants'::regclass "
        "AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%client_owner%%'"
    )
    assert checks == [{"conname": "hr_grants_scope_action"}]

    holder = await read_as(world, "operator", "hr/sensitive_viewer.sql", (world.tenant,))
    assert holder == [{"sensitive": True}]
    # A people.manage holder does not see sensitive details; the HR owner does.
    assert await read_as(world, "manager", "hr/sensitive_viewer.sql", (world.tenant,)) == [
        {"sensitive": False}
    ]
    assert await read_as(world, "owner", "hr/sensitive_viewer.sql", (world.tenant,)) == [
        {"sensitive": True}
    ]
    assert await read_as(world, "staff", "hr/sensitive_viewer.sql", (world.tenant,)) == [
        {"sensitive": False}
    ]
    # Another tenant's real owner gets false for this tenant, and a session without an HR user too.
    assert await read_as_other_tenant_owner(world, "hr/sensitive_viewer.sql") == [
        {"sensitive": False}
    ]
    async with tx(world.service.pool, str(world.tenant)) as conn:
        assert await q.fetch_all("hr/sensitive_viewer.sql", (world.tenant,), conn=conn) == [
            {"sensitive": False}
        ]


async def test_people_manager_cannot_grant_sensitive_details(world: HrWorld) -> None:
    await make_people_manager(world)
    # The API turns permission_denied into a content-free 404, like every owner-only refusal.
    await refused(
        world,
        "operator",
        {
            "operation": "access.save",
            "user_id": str(world.users["staff"]),
            "grants": [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}],
            "workflows": [],
        },
        404,
        "resource_unavailable",
    )
    assert (
        await world.q(
            "SELECT 1 FROM hr_grants WHERE client_id=%s AND action='people.sensitive'",
            (world.tenant,),
        )
        == []
    )
    # Holding Sensitive details does not grant access changes either.
    await make_sensitive_holder(world, "manager")
    await refused(
        world,
        "manager",
        {
            "operation": "access.save",
            "user_id": str(world.users["staff"]),
            "grants": [{"action": "people.sensitive", "scope": "hr_operator", "fields": []}],
            "workflows": [],
        },
        404,
        "resource_unavailable",
    )


# ---- Personal details (Task 2) ----------------------------------------------------------------

ENCRYPTED = ("ic_or_passport", "bank_account", "tax_number", "epf_number", "socso_number")
SELF_FIELDS = (
    "home_address",
    "personal_phone",
    "emergency_name",
    "emergency_relationship",
    "emergency_phone",
)
PERSONAL_FUNCTIONS = (
    "hr_personal_access(uuid,uuid)",
    "hr_personal_row(uuid,uuid)",
    "hr_personal_apply(uuid,uuid,text,jsonb)",
    "hr_personal_reveal(uuid,uuid,text)",
)


def fingerprint_of(seed: str) -> str:
    return (seed.encode().hex() * 64)[:64]


def save_body(
    employee: UUID,
    expected_version: int | None,
    values: dict[str, Any] | None = None,
    encrypted: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    return {
        "operation": "personal.save",
        "employee_id": str(employee),
        "expected_version": expected_version,
        "values": values or {},
        "encrypted": encrypted or {},
    }


async def personal_save(
    world: HrWorld,
    who: str,
    body: dict[str, Any],
    *,
    key: UUID | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    rows = await read_as(
        world,
        who,
        "hr/personal_apply.sql",
        (world.tenant, key or uuid4(), fingerprint or fingerprint_of("ab"), Jsonb(body)),
    )
    return rows[0]["receipt"]


async def personal_row(
    world: HrWorld, who: str, employee: UUID, tenant: UUID | None = None
) -> list[dict[str, Any]]:
    return await read_as(world, who, "hr/personal_row.sql", (tenant or world.tenant, employee))


ACCESS_SQL = "SELECT public.hr_personal_access(%s,%s) AS access"


async def personal_access(world: HrWorld, who: str, employee: UUID) -> str | None:
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof(who))
        row = await (await conn.execute(ACCESS_SQL, (world.tenant, employee))).fetchone()
    assert row is not None
    return row["access"]


async def test_personal_access_classes(world: HrWorld) -> None:
    staff = world.employees["staff"]
    # The HR owner, and a Sensitive details holder, see any employee of the tenant.
    await make_sensitive_holder(world, "outsider")
    assert await personal_access(world, "owner", staff) == "hr"
    assert await personal_access(world, "outsider", staff) == "hr"
    assert await personal_access(world, "outsider", world.employees["manager"]) == "hr"
    # The person sees their own record as self, and nobody else's.
    assert await personal_access(world, "staff", staff) == "self"
    assert await personal_access(world, "staff", world.employees["manager"]) is None
    # Their manager, a Manage people holder and a directory-only colleague get nothing.
    await make_people_manager(world, "admin")
    for who in ("manager", "admin", "operator"):
        assert await personal_access(world, who, staff) is None, who
        assert await personal_row(world, who, staff) == [], who
    assert [row["access"] for row in await personal_row(world, "owner", staff)] == ["hr"]
    assert [row["access"] for row in await personal_row(world, "staff", staff)] == ["self"]
    # Another tenant's real owner, and a session without an HR user, get nothing for this tenant.
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        await conn.execute(
            "SELECT set_config('app.hr_user_id', %s, true)", (str(world.users["owner"]),)
        )
        row = await (await conn.execute(ACCESS_SQL, (world.tenant, staff))).fetchone()
    assert row == {"access": None}
    assert (
        await read_as_other_tenant_owner(world, "hr/personal_row.sql", (world.tenant, staff)) == []
    )
    async with tx(world.service.pool, str(world.tenant)) as conn:
        assert await q.fetch_all("hr/personal_row.sql", (world.tenant, staff), conn=conn) == []


async def test_personal_tables_have_no_direct_grants(world: HrWorld) -> None:
    for table in ("hr_employee_personal", "hr_personal_reveals"):
        async with tx(world.service.pool, str(world.tenant)) as conn:
            await world.service._session(conn, world.proof("owner"))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(f"SELECT * FROM {table}")  # type: ignore[arg-type]
    roles = ("csa_app", "csa_ro", "csa_identity", "csa_hr_maintenance", "public")
    for signature in PERSONAL_FUNCTIONS:
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
    for table in ("hr_employee_personal", "hr_personal_reveals"):
        rows = await world.q(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            (f"public.{table}",),
        )
        assert rows == [{"relrowsecurity": True, "relforcerowsecurity": False}], table


async def test_personal_save_by_hr_sets_and_clears_encrypted_columns(world: HrWorld) -> None:
    staff = world.employees["staff"]
    first = await personal_save(
        world,
        "owner",
        save_body(
            staff,
            None,
            {
                "home_address": "1 Synthetic Road",
                "date_of_birth": "1990-02-03",
                "bank_name": "Maybank",
            },
            {"ic_or_passport": "v1:test-ciphertext-1", "bank_account": "v1:test-ciphertext-2"},
        ),
    )
    assert first == {
        "operation": "personal.save",
        "resource_id": str(staff),
        "version": 1,
        "status": "saved",
        "duplicate": False,
    }
    (row,) = await personal_row(world, "owner", staff)
    assert (
        row["access"] == "hr" and row["version"] == 1 and row["updated_by"] == world.users["owner"]
    )
    assert row["home_address"] == "1 Synthetic Road" and str(row["date_of_birth"]) == "1990-02-03"
    assert row["bank_name"] == "Maybank"
    assert row["ic_or_passport"] == "v1:test-ciphertext-1"
    assert row["bank_account"] == "v1:test-ciphertext-2" and row["tax_number"] is None
    # A key that is absent keeps its value; null clears it. Plain values follow the same rule.
    second = await personal_save(
        world,
        "owner",
        save_body(
            staff,
            1,
            {"personal_phone": "+60 12 000 0000"},
            {"tax_number": "v1:test-ciphertext-3", "bank_account": None},
        ),
    )
    assert second["version"] == 2
    (row,) = await personal_row(world, "owner", staff)
    assert row["ic_or_passport"] == "v1:test-ciphertext-1"
    assert row["bank_account"] is None and row["tax_number"] == "v1:test-ciphertext-3"
    assert row["home_address"] == "1 Synthetic Road" and row["personal_phone"] == "+60 12 000 0000"
    await make_sensitive_holder(world, "operator")
    third = await personal_save(world, "operator", save_body(staff, 2, {"home_address": None}))
    assert third["version"] == 3
    (row,) = await personal_row(world, "owner", staff)
    assert row["home_address"] is None and row["tax_number"] == "v1:test-ciphertext-3"
    # An employee that is not in the tenant is unavailable, even for the HR owner.
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_employee_unavailable"):
        await personal_save(world, "owner", save_body(uuid4(), None, {"home_address": "x"}))
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_personal_field_refused"):
        await personal_save(world, "owner", save_body(staff, 3, {"nickname": "x"}))
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_personal_field_refused"):
        await personal_save(world, "owner", save_body(staff, 3, {}, {"home_address": "x"}))
    # The tenant check: the same call for another tenant's id is refused outright.
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_personal_access_required"):
        await read_as(
            world,
            "owner",
            "hr/personal_apply.sql",
            (world.other_tenant, uuid4(), fingerprint_of("ab"), Jsonb(save_body(staff, None))),
        )


async def test_personal_self_may_edit_contact_fields_only(world: HrWorld) -> None:
    staff = world.employees["staff"]
    saved = await personal_save(
        world, "staff", save_body(staff, None, {"home_address": "2 Synthetic Lane"})
    )
    assert saved["version"] == 1
    for field in SELF_FIELDS[1:]:
        await personal_save(
            world, "staff", save_body(staff, SELF_FIELDS.index(field), {field: "v"})
        )
    for refused_body in (
        save_body(staff, 5, {"bank_name": "Maybank"}),
        save_body(staff, 5, {"date_of_birth": "1990-01-01"}),
        save_body(staff, 5, {"home_address": "ok"}, {"tax_number": "v1:test-ciphertext-9"}),
        save_body(staff, 5, {}, {"tax_number": None}),
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="hr_personal_field_refused"):
            await personal_save(world, "staff", refused_body)
    # Nothing from a refused save is stored.
    (row,) = await personal_row(world, "owner", staff)
    assert row["bank_name"] is None and row["tax_number"] is None and row["version"] == 5
    # Another person's record is not the caller's.
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_personal_access_required"):
        await personal_save(
            world, "staff", save_body(world.employees["manager"], None, {"home_address": "x"})
        )
    # HR sees what the person wrote, and the HR owner may then change the other fields.
    after = await personal_save(world, "owner", save_body(staff, 5, {"bank_name": "CIMB"}))
    assert after["version"] == 6


async def test_personal_save_refuses_stale_version(world: HrWorld) -> None:
    staff = world.employees["staff"]
    # A first save that expects a version, or a later one that expects none, is stale.
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_person_changed"):
        await personal_save(world, "owner", save_body(staff, 1, {"home_address": "x"}))
    await personal_save(world, "owner", save_body(staff, None, {"home_address": "x"}))
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_person_changed"):
        await personal_save(world, "owner", save_body(staff, None, {"home_address": "y"}))
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_person_changed"):
        await personal_save(world, "owner", save_body(staff, 7, {"home_address": "y"}))
    await personal_save(world, "owner", save_body(staff, 1, {"home_address": "y"}))
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_person_changed"):
        await personal_save(world, "owner", save_body(staff, 1, {"home_address": "z"}))
    (row,) = await personal_row(world, "owner", staff)
    assert row["version"] == 2 and row["home_address"] == "y"


async def test_personal_save_writes_history_without_values(world: HrWorld) -> None:
    staff = world.employees["staff"]
    address, cipher = "99 Distinct Synthetic Avenue", "v1:test-ciphertext-hist"
    before = await world.q("SELECT count(*) AS n FROM hr_employee_changes")
    await personal_save(
        world,
        "owner",
        save_body(staff, None, {"home_address": address}, {"ic_or_passport": cipher}),
    )
    emptied = {"old": None, "new": None}
    rows = await world.q(
        "SELECT changes, changed_by FROM hr_employee_changes WHERE client_id=%s AND employee_id=%s "
        "ORDER BY changed_at DESC, id DESC LIMIT 1",
        (world.tenant, staff),
    )
    assert rows == [
        {
            "changes": {"home_address": emptied, "ic_or_passport": emptied},
            "changed_by": world.users["owner"],
        }
    ]
    # An unchanged plain value, an unchanged clear and a repeated ciphertext are not changes;
    # a resent but different ciphertext is, and so is clearing a stored one.
    await personal_save(
        world,
        "owner",
        save_body(staff, 1, {"home_address": address, "bank_name": None}, {"tax_number": None}),
    )
    await personal_save(world, "owner", save_body(staff, 1, {}, {"ic_or_passport": cipher}))
    await personal_save(world, "owner", save_body(staff, 1, {}, {"ic_or_passport": cipher + "-2"}))
    await personal_save(world, "owner", save_body(staff, 2, {}, {"ic_or_passport": None}))
    shown = await world.q(
        "SELECT changes FROM hr_employee_changes WHERE client_id=%s AND employee_id=%s "
        "ORDER BY changed_at DESC, id DESC LIMIT 3",
        (world.tenant, staff),
    )
    assert [row["changes"] for row in shown] == [
        {"ic_or_passport": emptied},
        {"ic_or_passport": emptied},
        {"home_address": emptied, "ic_or_passport": emptied},
    ]
    after = await world.q("SELECT count(*) AS n FROM hr_employee_changes")
    assert after[0]["n"] - before[0]["n"] == 3
    versions = await personal_row(world, "owner", staff)
    assert versions[0]["version"] == 3
    # No plaintext and no ciphertext appears in history or receipts.
    dump = await world.q(
        "SELECT (SELECT string_agg(changes::text,' ') FROM hr_employee_changes) AS history, "
        "(SELECT string_agg(receipt::text || fingerprint,' ') FROM hr_operations) AS receipts"
    )
    for text in (dump[0]["history"], dump[0]["receipts"]):
        assert address not in text and "ciphertext" not in text and "Distinct" not in text


async def test_personal_reveal_audits_each_show(world: HrWorld) -> None:
    staff = world.employees["staff"]
    await personal_save(
        world, "owner", save_body(staff, None, {}, {"bank_account": "v1:test-ciphertext-bank"})
    )

    async def reveal(who: str, field: str, employee: UUID = staff) -> str | None:
        rows = await read_as(world, who, "hr/personal_reveal.sql", (world.tenant, employee, field))
        return rows[0]["ciphertext"]

    assert await reveal("owner", "bank_account") == "v1:test-ciphertext-bank"
    # The person may reveal their own number. A value that is not stored reveals as null.
    assert await reveal("staff", "bank_account") == "v1:test-ciphertext-bank"
    assert await reveal("owner", "tax_number") is None
    rows = await world.q(
        "SELECT employee_id, field, actor FROM hr_personal_reveals WHERE client_id=%s "
        "ORDER BY revealed_at, id",
        (world.tenant,),
    )
    assert rows == [
        {"employee_id": staff, "field": "bank_account", "actor": world.users["owner"]},
        {"employee_id": staff, "field": "bank_account", "actor": world.users["staff"]},
        {"employee_id": staff, "field": "tax_number", "actor": world.users["owner"]},
    ]
    # A colleague, a manager and a Manage people holder are refused, and nothing is audited.
    await make_people_manager(world, "outsider")
    for who in ("operator", "manager", "outsider"):
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege, match="hr_personal_access_required"
        ):
            await reveal(who, "bank_account")
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_personal_field_refused"):
        await reveal("owner", "home_address")
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_employee_unavailable"):
        await reveal("owner", "bank_account", uuid4())
    count = await world.q("SELECT count(*) AS n FROM hr_personal_reveals")
    assert count[0]["n"] == 3
    # The audit is insert-only.
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_immutable_record"):
        await world.q("UPDATE hr_personal_reveals SET field='tax_number'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_immutable_record"):
        await world.q("DELETE FROM hr_personal_reveals")


async def test_personal_apply_same_key_and_fingerprint_returns_stored_receipt(
    world: HrWorld,
) -> None:
    staff = world.employees["staff"]
    key, fingerprint = uuid4(), fingerprint_of("cd")
    body = save_body(
        staff, None, {"home_address": "3 Synthetic Close"}, {"tax_number": "v1:test-ciphertext-5"}
    )
    first = await personal_save(world, "owner", body, key=key, fingerprint=fingerprint)
    again = await personal_save(world, "owner", body, key=key, fingerprint=fingerprint)
    assert first["duplicate"] is False
    assert again == {**first, "duplicate": True}
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_employee_changes WHERE client_id=%s AND employee_id=%s "
        "AND changes ? 'home_address'",
        (world.tenant, staff),
    )
    assert rows[0]["n"] == 1
    ops = await world.q(
        "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s AND request_key=%s",
        (world.tenant, key),
    )
    assert ops[0]["n"] == 1
    (row,) = await personal_row(world, "owner", staff)
    assert row["version"] == 1
    # The same key with a different fingerprint is a conflict, as for the other HR operations.
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_idempotency_conflict"):
        await personal_save(world, "owner", body, key=key, fingerprint=fingerprint_of("ef"))
    # The key belongs to the caller: someone else with the same key saves their own.
    await make_sensitive_holder(world, "operator")
    other = await personal_save(
        world, "operator", save_body(staff, 1, {"home_address": "4 Synthetic Close"}), key=key
    )
    assert other["duplicate"] is False and other["version"] == 2
