"""Migration 0521: the Sensitive details switch, personal details, documents, leaving
(disposable database). Reuses the HrWorld fixture of tests/test_hr_people.py.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.console.auth import build_identity_router
from csa.core import crypto, settings
from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr import documents
from csa.hr.api import build_hr_router
from csa.hr.automation_models import PersonalSave
from csa.hr.operations import HrOperations
from csa.hr.personal import fingerprint_payload, personal_fingerprint
from fastapi import FastAPI
from psycopg.types.json import Jsonb
from structlog.testing import capture_logs

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

_KEY_A = "base64:" + base64.b64encode(b"\x05" * 32).decode("ascii")
_KEY_B = "base64:" + base64.b64encode(b"\x06" * 32).decode("ascii")


def use_credentials_key(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """A synthetic CREDENTIALS_KEY and a full, valid Settings env (as tests/test_crypto.py)."""
    monkeypatch.setenv("ENV", "ci")
    monkeypatch.setenv("DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa")
    monkeypatch.setenv("LG_DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("CREDENTIALS_KEY", key)
    settings.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def credentials_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    use_credentials_key(monkeypatch, _KEY_A)
    yield
    settings.get_settings.cache_clear()


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
    encrypted: Mapping[str, str | None] | None = None,
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
    count = await world.q(
        "SELECT count(*) AS n FROM hr_personal_reveals WHERE client_id=%s", (world.tenant,)
    )
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


# ---- Personal details service and routes (Task 3) ---------------------------------------------

OPERATIONS = "/console/v1/hr/operations"
PEOPLE = "/console/v1/hr/people"
IC, BANK, TAX = "900101-14-5678", "1234 5678 9012", "SG 12345 6789"


def personal_path(employee: UUID, tail: str = "personal") -> str:
    return f"{PEOPLE}/{employee}/{tail}"


async def api_save(
    world: HrWorld,
    who: str,
    employee: UUID,
    version: int | None,
    values: dict[str, Any] | None = None,
    encrypted: Mapping[str, str | None] | None = None,
    *,
    key: UUID | None = None,
) -> httpx.Response:
    return await world.call(
        who, "POST", OPERATIONS, save_body(employee, version, values, encrypted), key=key
    )


async def saved(world: HrWorld, who: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    response = await api_save(world, who, *args, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


async def reveal(
    world: HrWorld, who: str, employee: UUID, field: str, *, body: Any = None
) -> httpx.Response:
    return await world.call(
        who,
        "POST",
        personal_path(employee, "personal/reveal"),
        {"field": field} if body is None else body,
    )


async def stored_row(world: HrWorld, employee: UUID) -> dict[str, Any]:
    (row,) = await world.q(
        "SELECT * FROM hr_employee_personal WHERE client_id=%s AND employee_id=%s",
        (world.tenant, employee),
    )
    return row


async def test_personal_get_masks_encrypted_values(world: HrWorld) -> None:
    staff = world.employees["staff"]
    # A person with no saved details: an empty record, no version, the owner may edit everything.
    empty = await world.call("owner", "GET", personal_path(staff))
    assert empty.status_code == 200, empty.text
    assert empty.headers["cache-control"] == "no-store"
    assert empty.json()["version"] is None and empty.json()["can_edit"] == "all"
    assert empty.json()["ic_or_passport"] is None and empty.json()["home_address"] is None

    receipt = await saved(
        world,
        "owner",
        staff,
        None,
        {"home_address": "1 Synthetic Road", "date_of_birth": "1990-02-03", "bank_name": "Maybank"},
        {"ic_or_passport": IC.lower(), "bank_account": BANK, "epf_number": "12345678"},
    )
    assert receipt["version"] == 1 and receipt["duplicate"] is False
    # At rest: ciphertext bound to the table, column and person; no plaintext in the row.
    row = await stored_row(world, staff)
    assert row["ic_or_passport"].startswith("v1:") and IC not in "".join(
        str(value) for value in row.values()
    )
    aad = crypto.credential_aad("hr_employee_personal", "ic_or_passport", str(staff))
    assert crypto.decrypt(row["ic_or_passport"], aad) == IC  # uppercased on the way in
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(
            row["ic_or_passport"],
            crypto.credential_aad("hr_employee_personal", "bank_account", str(staff)),
        )
    assert row["tax_number"] is None

    response = await world.call("owner", "GET", personal_path(staff))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["ic_or_passport"] == "•••• 5678"
    assert body["bank_account"] == "•••• 9012"
    assert body["epf_number"] == "•••• 5678"
    assert body["tax_number"] is None and body["socso_number"] is None
    assert body["home_address"] == "1 Synthetic Road" and body["bank_name"] == "Maybank"
    assert body["date_of_birth"] == "1990-02-03"
    assert body["version"] == 1 and body["can_edit"] == "all"
    for plaintext in (IC, "900101145678", "1234 5678 9012", "12345678"):
        assert plaintext not in response.text
    assert "v1:" not in response.text

    # The person sees their own record (masked) and may edit the contact fields only.
    own = await world.call("staff", "GET", personal_path(staff))
    assert own.status_code == 200, own.text
    assert own.json()["can_edit"] == "contact" and own.json()["ic_or_passport"] == "•••• 5678"
    assert IC not in own.text
    # Anyone else gets the same content-free answer as for a person who does not exist.
    await make_people_manager(world, "admin")
    for who in ("manager", "outsider", "operator", "admin"):
        refused_get = await world.call(who, "GET", personal_path(staff))
        assert refused_get.status_code == 404, who
        assert refused_get.json() == {"detail": "resource_unavailable"}
    missing = await world.call("owner", "GET", personal_path(uuid4()))
    assert missing.status_code == 404 and missing.json() == {"detail": "resource_unavailable"}
    # A Sensitive details holder sees it as the owner does.
    await make_sensitive_holder(world, "operator")
    held = await world.call("operator", "GET", personal_path(staff))
    assert held.status_code == 200 and held.json()["can_edit"] == "all"


async def test_personal_reveal_returns_plaintext_for_hr_and_self_only(world: HrWorld) -> None:
    staff, manager = world.employees["staff"], world.employees["manager"]
    await saved(world, "owner", staff, None, {}, {"bank_account": BANK, "tax_number": TAX})
    shown = await reveal(world, "owner", staff, "bank_account")
    assert shown.status_code == 200, shown.text
    assert shown.json() == {"value": BANK}
    assert shown.headers["cache-control"] == "no-store"
    assert (await reveal(world, "staff", staff, "tax_number")).json() == {"value": TAX}
    # Nothing stored shows as null, and is still audited.
    assert (await reveal(world, "owner", staff, "socso_number")).json() == {"value": None}
    audit = await world.q(
        "SELECT field, actor FROM hr_personal_reveals WHERE client_id=%s ORDER BY revealed_at, id",
        (world.tenant,),
    )
    assert audit == [
        {"field": "bank_account", "actor": world.users["owner"]},
        {"field": "tax_number", "actor": world.users["staff"]},
        {"field": "socso_number", "actor": world.users["owner"]},
    ]
    # A manager, a colleague and a Manage people holder are refused and nothing is audited. The
    # API answers every owner-only or self-only refusal with a content-free 404.
    await make_people_manager(world, "admin")
    for who in ("manager", "outsider", "operator", "admin"):
        refused_reveal = await reveal(world, who, staff, "bank_account")
        assert refused_reveal.status_code == 404, who
        assert refused_reveal.json() == {"detail": "resource_unavailable"}
        assert BANK not in refused_reveal.text
    # The person cannot reveal someone else's number.
    other = await reveal(world, "staff", manager, "bank_account")
    assert other.status_code == 404 and other.json() == {"detail": "resource_unavailable"}
    # Only the five numbers can be shown.
    for bad in (
        {"field": "home_address"},
        {"field": "nickname"},
        {},
        {"field": "bank_account", "x": 1},
    ):
        wrong = await reveal(world, "owner", staff, "", body=bad)
        assert wrong.status_code == 422 and wrong.json()["detail"] == "invalid_hr_input"
    unknown = await reveal(world, "owner", uuid4(), "bank_account")
    assert unknown.status_code == 404
    count = await world.q(
        "SELECT count(*) AS n FROM hr_personal_reveals WHERE client_id=%s", (world.tenant,)
    )
    assert count[0]["n"] == 3
    # A read-only member and a request without CSRF cannot reveal.
    assert (await reveal(world, "reader", staff, "bank_account")).status_code == 403
    no_csrf = await world.call(
        "owner",
        "POST",
        personal_path(staff, "personal/reveal"),
        {"field": "bank_account"},
        headers={"X-CSRF-Token": "wrong"},
    )
    assert no_csrf.status_code == 403


async def test_personal_validation_error_does_not_echo_values(world: HrWorld) -> None:
    staff = world.employees["staff"]
    secret_values = {
        "ic_or_passport": "BAD!!VALUE",
        "bank_account": "ACCOUNT-LETTERS-9",
        "epf_number": "NOT-DIGITS-77",
    }
    for field, value in secret_values.items():
        response = await api_save(world, "owner", staff, None, {}, {field: value})
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "invalid_hr_input"
        assert set(response.json()) <= {"detail", "invalid_fields"}
        assert value not in response.text and '"input"' not in response.text
        assert "ctx" not in response.text
    # Plain fields do not echo either.
    response = await api_save(
        world, "owner", staff, None, {"personal_phone": "SECRET-PHONE-TEXT", "nickname": "x"}
    )
    assert response.status_code == 422 and "SECRET-PHONE-TEXT" not in response.text
    # The reveal route has the same shape.
    response = await reveal(world, "owner", staff, "", body={"field": "BAD!!VALUE"})
    assert response.status_code == 422 and "BAD!!VALUE" not in response.text
    # Nothing was stored by any refused save.
    assert (
        await world.q("SELECT 1 FROM hr_employee_personal WHERE client_id=%s", (world.tenant,))
        == []
    )


async def test_personal_values_never_reach_logs(
    world: HrWorld, caplog: pytest.LogCaptureFixture
) -> None:
    staff = world.employees["staff"]
    plain = {"home_address": "Distinct Log Address 77", "bank_name": "Distinct Log Bank"}
    numbers = {"ic_or_passport": "LOGTEST-IC-9999", "bank_account": "7777 8888 9999 0000"}
    with capture_logs() as events, caplog.at_level(0):
        first = await saved(world, "owner", staff, None, plain, numbers, key=UUID(int=7))
        # A refused save and a repeated one, and both kinds of read.
        await api_save(world, "owner", staff, 1, {}, {"tax_number": "bad!!"})
        await api_save(world, "owner", staff, 9, plain, numbers)
        await saved(world, "owner", staff, None, plain, numbers, key=UUID(int=7))  # a retry
        await world.call("owner", "GET", personal_path(staff))
        await reveal(world, "owner", staff, "ic_or_passport")
        await reveal(world, "manager", staff, "ic_or_passport")
    assert first["version"] == 1
    # The real checks: the capture is live (a request logged something) or at least the plaintext
    # is in no event, no log record and no receipt.
    dump = repr(events) + caplog.text
    for secret in (*plain.values(), *numbers.values(), "LOGTEST", "9999 0000"):
        assert secret not in dump, secret
    receipts = await world.q(
        "SELECT receipt::text AS r, fingerprint FROM hr_operations WHERE client_id=%s",
        (world.tenant,),
    )
    assert receipts
    for row in receipts:
        for secret in (*plain.values(), *numbers.values()):
            assert secret not in row["r"] and secret not in row["fingerprint"]


async def test_profile_lists_personal_section_for_owner_sensitive_and_self_only(
    world: HrWorld,
) -> None:
    staff = world.employees["staff"]

    async def sections(who: str) -> list[str] | int:
        response = await world.call(who, "GET", f"{PEOPLE}/{staff}")
        return response.json()["sections"] if response.status_code == 200 else response.status_code

    assert await sections("owner") == ["overview", "personal", "documents", "access", "history"]
    # The person sees their own personal details and documents, nothing HR-only.
    assert await sections("staff") == ["overview", "personal", "documents"]
    # A directory viewer sees neither, until given Sensitive details.
    assert await sections("operator") == ["overview"]
    await make_sensitive_holder(world, "operator")
    assert await sections("operator") == ["overview", "personal"]
    # A Manage people holder is an HR viewer without personal details.
    await make_people_manager(world, "admin")
    assert await sections("admin") == ["overview", "documents", "access", "history"]
    # A manager without a people role does not see the profile at all.
    assert await sections("manager") == 404
    # Another person's profile: the person's own sections do not carry over.
    own_manager = await world.call("staff", "GET", f"{PEOPLE}/{world.employees['manager']}")
    assert own_manager.status_code == 404


async def test_personal_save_retry_returns_the_first_receipt_and_fingerprint_is_keyed(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    staff = world.employees["staff"]
    key = uuid4()
    values, numbers = {"home_address": "5 Retry Road"}, {"ic_or_passport": IC}
    first = await saved(world, "owner", staff, None, values, numbers, key=key)
    # The same request after a lost response: the first receipt, no 409, nothing stored twice.
    again = await saved(world, "owner", staff, None, values, numbers, key=key)
    assert first["duplicate"] is False and again == {**first, "duplicate": True}
    history = await world.q(
        "SELECT count(*) AS n FROM hr_employee_changes WHERE client_id=%s AND employee_id=%s "
        "AND changes ? 'home_address'",
        (world.tenant, staff),
    )
    assert history[0]["n"] == 1
    assert (await stored_row(world, staff))["version"] == 1

    body = PersonalSave.model_validate(save_body(staff, None, values, numbers))
    (op,) = await world.q(
        "SELECT fingerprint FROM hr_operations WHERE client_id=%s AND request_key=%s",
        (world.tenant, key),
    )
    stored = op["fingerprint"]
    assert stored == personal_fingerprint(body)
    # Never a plain hash of the body: an IC number could be guessed from a birth date.
    for plain in (IC, "personal.save" + IC, repr(body.model_dump())):
        assert stored != hashlib.sha256(plain.encode()).hexdigest()
    canonical = fingerprint_payload(body)
    assert IC in canonical
    assert stored != hashlib.sha256(canonical.encode()).hexdigest()
    # It depends on the secret key and on every value.
    use_credentials_key(monkeypatch, _KEY_B)
    assert personal_fingerprint(body) != stored
    use_credentials_key(monkeypatch, _KEY_A)
    assert personal_fingerprint(body) == stored
    other = PersonalSave.model_validate(
        save_body(staff, None, values, {"ic_or_passport": IC + "1"})
    )
    assert personal_fingerprint(other) != stored
    absent_vs_null = PersonalSave.model_validate(save_body(staff, None, {"home_address": None}, {}))
    assert personal_fingerprint(absent_vs_null) != personal_fingerprint(
        PersonalSave.model_validate(save_body(staff, None, {}, {}))
    )

    # The same key with a different body is a conflict, as for the other HR operations.
    conflict = await api_save(
        world, "owner", staff, None, values, {"ic_or_passport": IC + "1"}, key=key
    )
    assert conflict.status_code == 409 and conflict.json() == {"detail": "idempotency_conflict"}
    # A retry that would now be stale still returns the first receipt (duplicate lookup first).
    later = await saved(world, "owner", staff, 1, {"bank_name": "CIMB"})
    assert later["version"] == 2
    replay = await saved(world, "owner", staff, None, values, numbers, key=key)
    assert replay == {**first, "duplicate": True}


async def test_personal_save_gates_and_refusals(world: HrWorld) -> None:
    staff, manager = world.employees["staff"], world.employees["manager"]
    # A save that changes nothing is a receipt with version 0 and creates no row.
    noop = await saved(world, "owner", staff, None, {}, {})
    assert noop["version"] == 0 and noop["status"] == "saved"
    assert (
        await world.q("SELECT 1 FROM hr_employee_personal WHERE client_id=%s", (world.tenant,))
        == []
    )
    assert (await world.call("owner", "GET", personal_path(staff))).json()["version"] is None

    # The person edits the five contact fields; numbers and HR fields are refused.
    mine = await saved(world, "staff", staff, None, {"home_address": "2 Own Lane"})
    assert mine["version"] == 1
    own = await world.call("staff", "GET", personal_path(staff))
    assert own.json()["home_address"] == "2 Own Lane"
    for values, encrypted in (
        ({"bank_name": "Maybank"}, {}),
        ({"date_of_birth": "1990-01-01"}, {}),
        ({}, {"tax_number": TAX}),
        ({}, {"tax_number": None}),
    ):
        response = await api_save(world, "staff", staff, 1, values, encrypted)
        assert response.status_code == 409, (values, encrypted)
        assert response.json() == {"detail": "personal_field_refused"}
    # Someone else's record, and a person who is not in the tenant.
    for who, target in (("staff", manager), ("manager", staff), ("outsider", staff)):
        response = await api_save(world, who, target, None, {"home_address": "x"})
        assert response.status_code == 404, who
        assert response.json() == {"detail": "resource_unavailable"}
    missing = await api_save(world, "owner", uuid4(), None, {"home_address": "x"})
    assert missing.status_code == 404 and missing.json() == {"detail": "resource_unavailable"}
    # A stale edit is person_changed, with the usual 409.
    stale = await api_save(world, "owner", staff, 5, {"bank_name": "CIMB"})
    assert stale.status_code == 409 and stale.json() == {"detail": "person_changed"}
    # HR sees what the person wrote and may then change the other fields; absent keys are kept.
    after = await saved(world, "owner", staff, 1, {"bank_name": "CIMB"}, {"epf_number": "12345678"})
    assert after["version"] == 2
    row = await stored_row(world, staff)
    assert row["home_address"] == "2 Own Lane" and row["bank_name"] == "CIMB"
    cleared = await saved(world, "owner", staff, 2, {"home_address": None}, {"epf_number": None})
    assert cleared["version"] == 3
    row = await stored_row(world, staff)
    assert row["home_address"] is None and row["epf_number"] is None and row["bank_name"] == "CIMB"
    # Read-only members, missing CSRF and a missing key are stopped before anything else.
    assert (await api_save(world, "reader", staff, None, {"home_address": "x"})).status_code == 403
    no_csrf = await world.call(
        "owner",
        "POST",
        OPERATIONS,
        save_body(staff, 3, {"bank_name": "x"}),
        headers={"X-CSRF-Token": "wrong"},
    )
    assert no_csrf.status_code == 403
    assert (await stored_row(world, staff))["version"] == 3
    # Saving personal details changes the History without values.
    history = await world.call("owner", "GET", f"{PEOPLE}/{staff}/history")
    assert history.status_code == 200
    fields = {field["field"] for item in history.json()["items"] for field in item["fields"]}
    assert {"home_address", "bank_name", "epf_number"} <= fields
    for secret in ("2 Own Lane", "CIMB", "12345678"):
        assert secret not in history.text


# ---- Rulings after review: line breaks, uppercase numbers, unchanged numbers ------------------


async def test_personal_home_address_keeps_line_breaks(world: HrWorld) -> None:
    staff = world.employees["staff"]
    first = await saved(world, "owner", staff, None, {"home_address": "1 Road\r\nTaman X\nKL"})
    assert first["version"] == 1
    assert (await stored_row(world, staff))["home_address"] == "1 Road\nTaman X\nKL"
    read = await world.call("owner", "GET", personal_path(staff))
    assert read.json()["home_address"] == "1 Road\nTaman X\nKL"
    # The person may write it too.
    mine = await saved(world, "staff", staff, 1, {"home_address": "2 Lane\nSabah"})
    assert mine["version"] == 2
    # A tab or NUL is still refused, without echoing the text.
    for bad in ("Secret\tTab", "Secret\u0000Nul"):
        response = await api_save(world, "owner", staff, 2, {"home_address": bad})
        assert response.status_code == 422 and response.json()["detail"] == "invalid_hr_input"
        assert "Secret" not in response.text
    assert (await stored_row(world, staff))["version"] == 2


async def test_personal_numbers_are_uppercased_when_saved(world: HrWorld) -> None:
    staff = world.employees["staff"]
    await saved(
        world, "owner", staff, None, {}, {"tax_number": "sg 12345678", "socso_number": "a-12345"}
    )
    assert (await reveal(world, "owner", staff, "tax_number")).json() == {"value": "SG 12345678"}
    assert (await reveal(world, "owner", staff, "socso_number")).json() == {"value": "A-12345"}


async def history_count(world: HrWorld, employee: UUID, field: str) -> int:
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_employee_changes WHERE client_id=%s AND employee_id=%s "
        "AND changes ? %s",
        (world.tenant, employee, field),
    )
    return rows[0]["n"]


async def test_personal_resending_an_unchanged_number_writes_no_history(world: HrWorld) -> None:
    staff = world.employees["staff"]
    await saved(world, "owner", staff, None, {}, {"ic_or_passport": IC, "bank_account": BANK})
    before = await stored_row(world, staff)
    assert await history_count(world, staff, "ic_or_passport") == 1
    # The same number again (a new key): a no-op, not a duplicate, and no new history.
    again = await saved(world, "owner", staff, 1, {}, {"ic_or_passport": IC.lower()})
    assert again["duplicate"] is False and again["version"] == 1
    assert await history_count(world, staff, "ic_or_passport") == 1
    after = await stored_row(world, staff)
    assert after["ic_or_passport"] == before["ic_or_passport"] and after["version"] == 1
    # An unchanged number next to a changed one: only the changed one is recorded.
    mixed = await saved(
        world, "owner", staff, 1, {}, {"ic_or_passport": IC, "bank_account": "999 888 777"}
    )
    assert mixed["version"] == 2
    assert await history_count(world, staff, "ic_or_passport") == 1
    assert await history_count(world, staff, "bank_account") == 2
    assert (await reveal(world, "owner", staff, "bank_account")).json() == {"value": "999 888 777"}
    # A different number is still a change, and clearing still clears.
    changed = await saved(world, "owner", staff, 2, {}, {"ic_or_passport": IC + "1"})
    assert changed["version"] == 3 and await history_count(world, staff, "ic_or_passport") == 2
    cleared = await saved(world, "owner", staff, 3, {}, {"ic_or_passport": None})
    assert cleared["version"] == 4 and await history_count(world, staff, "ic_or_passport") == 3
    # A first number on a person with no row yet is stored.
    other = world.employees["manager"]
    first = await saved(world, "owner", other, None, {}, {"epf_number": "12345678"})
    assert first["version"] == 1
    # The person themself still may not send any number, even an unchanged one.
    refused_self = await api_save(world, "staff", staff, 4, {}, {"bank_account": "999 888 777"})
    assert refused_self.status_code == 409
    assert refused_self.json() == {"detail": "personal_field_refused"}


# ---- Documents (Task 4) ------------------------------------------------------------------------

PDF = b"%PDF-1.4\n% synthetic contract\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image"
JPG = b"\xff\xd8\xff\xe0synthetic image"
DOCUMENT_FUNCTIONS = (
    "hr_document_access(uuid,uuid)",
    "hr_document_rows(uuid,uuid)",
    "hr_document_get(uuid,uuid)",
    "hr_document_add(uuid,uuid,uuid,uuid,text,text,text,text,integer,text)",
    "hr_document_remove(uuid,uuid)",
    "hr_document_storage_names(uuid)",
)


def docx_bytes(*names: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name in names:
            archive.writestr(name, "<w:document/>")
    return out.getvalue()


DOCX = docx_bytes("[Content_Types].xml", "word/document.xml")


@pytest.fixture
def hr_root(world: HrWorld, tmp_path: Path) -> Path:
    """The same world, with the HR document routes mounted on a temporary storage root."""
    root = tmp_path / "hr-uploads"
    app = FastAPI()
    for name in ("identity_service", "hr_service", "pool"):
        setattr(app.state, name, getattr(world.app.state, name))
    app.include_router(build_identity_router())
    app.include_router(build_hr_router(hr_upload_root=root))
    assert not world.browsers  # browsers bind to the app of their first call
    world.app = app
    return root


async def browser(world: HrWorld, who: str) -> httpx.AsyncClient:
    if who not in world.browsers:
        await world.call(who, "GET", "/console/v1/hr/context")
    return world.browsers[who]


async def upload(
    world: HrWorld,
    who: str,
    employee: UUID,
    name: str,
    data: bytes,
    *,
    kind: str = "contract",
    key: UUID | None = None,
    field: str = "file",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    client = await browser(world, who)
    supplied = {
        "Origin": "https://hr.example.test",
        "X-CSRF-Token": world.csrf[who],
        "Idempotency-Key": str(key or uuid4()),
        **(headers or {}),
    }
    return await client.post(
        f"{PEOPLE}/{employee}/documents",
        params={"kind": kind},
        files={field: (name, data, "application/octet-stream")},
        headers=supplied,
    )


async def raw_upload(
    world: HrWorld, who: str, employee: UUID, quoted_name: str, data: bytes
) -> dict[str, Any]:
    """An upload with a hand-written filename in Content-Disposition (httpx escapes quotes)."""
    client = await browser(world, who)
    body = (
        b'--x\r\nContent-Disposition: form-data; name="file"; filename="'
        + quoted_name.encode()
        + b'"\r\n\r\n'
        + data
        + b"\r\n--x--\r\n"
    )
    response = await client.post(
        f"{PEOPLE}/{employee}/documents",
        params={"kind": "contract"},
        content=body,
        headers={
            "Origin": "https://hr.example.test",
            "X-CSRF-Token": world.csrf[who],
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "multipart/form-data; boundary=x",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def uploaded(world: HrWorld, who: str, employee: UUID, name: str, data: bytes, **kw: Any):
    response = await upload(world, who, employee, name, data, **kw)
    assert response.status_code == 201, response.text
    return response.json()


async def listed(world: HrWorld, who: str, employee: UUID) -> httpx.Response:
    return await world.call(who, "GET", f"{PEOPLE}/{employee}/documents")


async def download(world: HrWorld, who: str, document: str) -> httpx.Response:
    return await world.call(who, "GET", f"/console/v1/hr/documents/{document}/download")


async def remove(
    world: HrWorld, who: str, document: str, *, key: UUID | None = None
) -> httpx.Response:
    return await world.call(who, "POST", f"/console/v1/hr/documents/{document}/remove", key=key)


async def documents_rows(world: HrWorld) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT id, employee_id, storage_name FROM hr_employee_documents WHERE client_id=%s",
        (world.tenant,),
    )


def stored_files(root: Path, tenant: UUID) -> list[str]:
    directory = root / str(tenant)
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


async def document_history(world: HrWorld, employee: UUID) -> list[dict[str, Any]]:
    rows = await world.q(
        "SELECT changes->'document' AS document, changed_by FROM hr_employee_changes "
        "WHERE client_id=%s AND employee_id=%s AND changes ? 'document' ORDER BY changed_at, id",
        (world.tenant, employee),
    )
    return rows


async def test_document_upload_list_download_and_remove_round_trip(
    world: HrWorld, hr_root: Path
) -> None:
    staff = world.employees["staff"]
    made = await uploaded(world, "owner", staff, "Offer letter.pdf", PDF, kind="contract")
    assert set(made) == {
        "document_id",
        "kind",
        "file_name",
        "media_type",
        "byte_size",
        "uploaded_at",
        "uploaded_by_name",
    }
    assert made["kind"] == "contract" and made["file_name"] == "Offer letter.pdf"
    assert made["media_type"] == "application/pdf" and made["byte_size"] == len(PDF)
    assert made["uploaded_by_name"] == "Synthetic Staff"
    assert len(stored_files(hr_root, world.tenant)) == 1

    page = await listed(world, "owner", staff)
    assert page.status_code == 200 and page.json() == {"items": [made]}

    got = await download(world, "owner", made["document_id"])
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"] == "attachment; filename*=utf-8''Offer%20letter.pdf"
    assert got.headers["cache-control"] == "no-store"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-length"] == str(len(PDF))

    # History: one row for the add.
    history = await world.call("owner", "GET", f"{PEOPLE}/{staff}/history")
    fields = [f for item in history.json()["items"] for f in item["fields"]]
    assert {"field": "document", "old": None, "new": "Contract: Offer letter.pdf"} in fields

    gone = await remove(world, "owner", made["document_id"])
    assert gone.status_code == 204 and gone.content == b""
    assert (await listed(world, "owner", staff)).json() == {"items": []}
    assert (await download(world, "owner", made["document_id"])).status_code == 404
    # The file is unlinked once the removal commits; the row is kept (soft).
    assert stored_files(hr_root, world.tenant) == []
    kept = await world.q(
        "SELECT removed_at IS NOT NULL AS removed, removed_by FROM hr_employee_documents "
        "WHERE client_id=%s",
        (world.tenant,),
    )
    assert kept == [{"removed": True, "removed_by": world.users["owner"]}]
    assert await document_history(world, staff) == [
        {
            "document": {"old": None, "new": "Contract: Offer letter.pdf"},
            "changed_by": world.users["owner"],
        },
        {
            "document": {"old": "Contract: Offer letter.pdf", "new": None},
            "changed_by": world.users["owner"],
        },
    ]
    # Removing again (a retry after a lost response) succeeds and writes nothing more.
    again = await remove(world, "owner", made["document_id"])
    assert again.status_code == 204 and len(await document_history(world, staff)) == 2


async def test_document_visibility_by_viewer(world: HrWorld, hr_root: Path) -> None:
    staff = world.employees["staff"]
    await make_people_manager(world, "operator")
    made = {
        kind: await uploaded(world, "owner", staff, f"{kind}.pdf", PDF, kind=kind)
        for kind in ("contract", "identity", "certificate", "other")
    }
    ids = {kind: row["document_id"] for kind, row in made.items()}

    async def seen(who: str) -> list[str] | int:
        response = await listed(world, who, staff)
        return (
            sorted(item["kind"] for item in response.json()["items"])
            if response.status_code == 200
            else response.status_code
        )

    everything = ["certificate", "contract", "identity", "other"]
    assert await seen("owner") == everything
    assert await seen("operator") == everything  # a Manage people holder sees all
    assert await seen("staff") == ["certificate", "contract", "identity"]  # the person: not other
    # Their manager, a colleague and a Sensitive details holder are refused. The API turns
    # permission_denied into its content-free 404 (not 403).
    await make_sensitive_holder(world, "admin")
    for who in ("manager", "outsider", "admin"):
        response = await listed(world, who, staff)
        assert (response.status_code, response.json()) == (404, {"detail": "resource_unavailable"})
        assert (await download(world, who, ids["contract"])).status_code == 404, who
    # Downloads follow the same rule: the person gets theirs, not "other".
    assert (await download(world, "staff", ids["contract"])).content == PDF
    assert (await download(world, "staff", ids["other"])).status_code == 404
    assert (await download(world, "operator", ids["other"])).content == PDF
    # The person sees nothing of someone else's, and has no way to remove their own.
    assert (await listed(world, "staff", world.employees["manager"])).status_code == 404
    assert (await remove(world, "staff", ids["contract"])).status_code == 404
    # Profile sections agree with the list: documents for HR viewers and the person themself.
    for who in ("owner", "operator", "staff"):
        profile = await world.call(who, "GET", f"{PEOPLE}/{staff}")
        assert "documents" in profile.json()["sections"], who
    # (A directory viewer with Sensitive details only gets no documents section: see
    # test_profile_lists_personal_section_for_owner_sensitive_and_self_only.)
    await world.grant("admin", "directory.read", "hr_operator")
    profile = await world.call("admin", "GET", f"{PEOPLE}/{staff}")
    assert profile.json()["sections"] == ["overview", "personal"]
    # Another tenant: its owner gets no row, no list and no removal, for the same ids.
    assert (
        await read_as_other_tenant_owner(
            world, "hr/document_get.sql", (world.tenant, UUID(ids["contract"]))
        )
        == []
    )
    for statement, params in (
        ("hr/document_rows.sql", (world.tenant, staff)),
        ("hr/document_remove.sql", (world.tenant, UUID(ids["contract"]))),
        ("hr/document_names.sql", (world.tenant,)),
    ):
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation)):
            await read_as_other_tenant_owner(world, statement, params)
    # Left people keep their documents for HR.
    await world.q(
        "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
        (world.tenant, staff),
    )
    assert await seen("owner") == everything


async def test_document_upload_needs_owner_or_people_manager(world: HrWorld, hr_root: Path) -> None:
    staff = world.employees["staff"]
    for who in ("staff", "manager", "operator", "outsider"):
        response = await upload(world, who, staff, "mine.pdf", PDF)
        assert (response.status_code, response.json()) == (
            404,
            {"detail": "resource_unavailable"},
        ), who
    reader = await upload(world, "reader", staff, "mine.pdf", PDF)
    assert (reader.status_code, reader.json()) == (403, {"detail": "read_only_session"})
    bad_csrf = await upload(
        world, "owner", staff, "mine.pdf", PDF, headers={"X-CSRF-Token": "wrong"}
    )
    assert bad_csrf.status_code == 403
    # Nothing was stored for any of them.
    assert stored_files(hr_root, world.tenant) == []
    assert await documents_rows(world) == []
    # A Manage people holder may.
    await make_people_manager(world, "operator")
    assert (await upload(world, "operator", staff, "ok.pdf", PDF)).status_code == 201
    # A person that does not exist in the tenant is not found.
    missing = await upload(world, "owner", uuid4(), "ok.pdf", PDF)
    assert missing.status_code == 404
    assert len(stored_files(hr_root, world.tenant)) == 1  # the unreferenced copy was swept


async def test_document_upload_checks_content_not_just_extension(
    world: HrWorld, hr_root: Path
) -> None:
    staff = world.employees["staff"]
    refusals = [
        ("x.pdf", PNG, "hr_document_type_unsupported"),  # a PNG named .pdf
        ("x.pdf", b"MZ\x90\x00synthetic", "hr_document_type_unsupported"),  # an executable
        ("x.docx", docx_bytes("a.txt"), "hr_document_type_unsupported"),  # a ZIP, not a DOCX
        ("x.docx", b"not a zip at all", "hr_document_type_unsupported"),
        ("x.png", PDF, "hr_document_type_unsupported"),
        ("x.jpg", PNG, "hr_document_type_unsupported"),
        ("x.exe", b"MZ\x90\x00synthetic", "hr_document_type_unsupported"),
        ("x", PDF, "hr_document_type_unsupported"),
        ("x.pdf", b"", "hr_document_empty"),
    ]
    for name, data, detail in refusals:
        response = await upload(world, "owner", staff, name, data)
        assert (response.status_code, response.json()) == (409, {"detail": detail}), name
    assert stored_files(hr_root, world.tenant) == []
    assert await documents_rows(world) == []

    # Each supported type is stored, extensions in any case.
    for name, data, media in (
        ("scan.JPG", JPG, "image/jpeg"),
        ("scan.jpeg", JPG, "image/jpeg"),
        ("scan.png", PNG, "image/png"),
        ("letter.docx", DOCX, DOCX_MEDIA),
        ("contract.PDF", PDF, "application/pdf"),
    ):
        made = await uploaded(world, "owner", staff, name, data)
        assert made["media_type"] == media and made["file_name"] == name
    # A name with quotes and Unicode is stored as given and downloaded with an encoded header.
    odd = 'naïve "q".pdf'
    made = await raw_upload(world, "owner", staff, 'naïve \\"q\\".pdf', PDF)
    assert made["file_name"] == odd
    got = await download(world, "owner", made["document_id"])
    assert got.headers["content-disposition"] == (
        "attachment; filename*=utf-8''na%C3%AFve%20%22q%22.pdf"
    )
    assert got.content == PDF
    # Path characters are refused by the shared multipart parser, as for the knowledge base.
    for bad in ("a/b.pdf", "..\\x.pdf"):
        response = await upload(world, "owner", staff, bad, PDF)
        assert response.status_code == 422, bad
    # The wrong part name, no kind and a kind that is not one are refused.
    assert (await upload(world, "owner", staff, "x.pdf", PDF, field="files")).status_code == 422
    assert (await upload(world, "owner", staff, "x.pdf", PDF, kind="payslip")).status_code == 422
    # Storage names are generated and carry only the detected extension.
    names = stored_files(hr_root, world.tenant)
    assert len(names) == 6
    assert all(re.fullmatch(r"[a-f0-9-]{36}\.(pdf|jpg|png|docx)", n) for n in names)


DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def test_document_upload_size_limit(world: HrWorld, hr_root: Path) -> None:
    staff = world.employees["staff"]
    ten_mb = 10 * 1024 * 1024
    exact = PDF + b"\x00" * (ten_mb - len(PDF))
    made = await uploaded(world, "owner", staff, "big.pdf", exact)
    assert made["byte_size"] == ten_mb
    assert len((await download(world, "owner", made["document_id"])).content) == ten_mb
    over = await upload(world, "owner", staff, "bigger.pdf", exact + b"\x00")
    assert over.status_code == 413
    assert len(stored_files(hr_root, world.tenant)) == 1


async def test_document_upload_retry_with_same_key_stores_one_file(
    world: HrWorld, hr_root: Path
) -> None:
    staff = world.employees["staff"]
    key = uuid4()
    first = await upload(world, "owner", staff, "a.pdf", PDF, key=key)
    again = await upload(world, "owner", staff, "a.pdf", PDF, key=key)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert len(await documents_rows(world)) == 1
    files = stored_files(hr_root, world.tenant)
    assert len(files) == 1
    got = await download(world, "owner", first.json()["document_id"])
    assert got.content == PDF
    assert len(await document_history(world, staff)) == 1
    # The same key for another file or person is a conflict, never a second document.
    other = await upload(world, "owner", staff, "a.pdf", PDF + b"x", key=key)
    assert (other.status_code, other.json()) == (409, {"detail": "idempotency_conflict"})
    assert stored_files(hr_root, world.tenant) == files


async def test_document_upload_failure_leaves_no_file(
    world: HrWorld, hr_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staff = world.employees["staff"]
    keep = await uploaded(world, "owner", staff, "keep.pdf", PDF)
    real = HrOperations._people_apply

    async def failing(conn: Any, path: str, params: tuple[Any, ...]) -> dict[str, Any]:
        if path == "hr/document_add.sql":
            raise psycopg.errors.OperationalError("synthetic")
        return await real(conn, path, params)

    monkeypatch.setattr(HrOperations, "_people_apply", staticmethod(failing))
    response = await upload(world, "owner", staff, "lost.pdf", PDF)
    assert (response.status_code, response.json()) == (503, {"detail": "hr_database_unavailable"})
    monkeypatch.undo()
    # The file published before the failed insert is gone; the committed document's file stays.
    assert len(stored_files(hr_root, world.tenant)) == 1
    assert (await download(world, "owner", keep["document_id"])).content == PDF


async def test_document_cleanup_removes_only_unreferenced_stored_files(
    world: HrWorld, hr_root: Path
) -> None:
    staff = world.employees["staff"]
    made = await uploaded(world, "owner", staff, "keep.pdf", PDF)
    directory = hr_root / str(world.tenant)
    orphan = directory / f"{uuid4()}.pdf"
    half = directory / f".{uuid4()}.docx.part"
    stray = directory / "notes.txt"
    for path in (orphan, half, stray):
        path.write_bytes(b"x")
    removed = await documents.cleanup(world.service.pool, hr_root, world.tenant)
    assert removed == 2
    assert not orphan.exists() and not half.exists() and stray.exists()
    assert (await download(world, "owner", made["document_id"])).content == PDF


async def test_document_routes_unavailable_without_root(world: HrWorld) -> None:
    staff = world.employees["staff"]
    unavailable = (503, {"detail": "hr_documents_unavailable"})
    for response in (
        await upload(world, "owner", staff, "a.pdf", PDF),
        await listed(world, "owner", staff),
        await download(world, "owner", str(uuid4())),
        await remove(world, "owner", str(uuid4())),
    ):
        assert (response.status_code, response.json()) == unavailable


async def test_document_table_and_functions_are_closed_to_other_roles(world: HrWorld) -> None:
    async with tx(world.service.pool, str(world.tenant)) as conn:
        await world.service._session(conn, world.proof("owner"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT * FROM hr_employee_documents")
    roles = ("csa_app", "csa_ro", "csa_identity", "csa_hr_maintenance", "public")
    for signature in DOCUMENT_FUNCTIONS:
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
    rows = await world.q(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid='public.hr_employee_documents'::regclass"
    )
    assert rows == [{"relrowsecurity": True, "relforcerowsecurity": False}]
    privileges = await world.q(
        "SELECT grantee FROM information_schema.role_table_grants "
        "WHERE table_name='hr_employee_documents' AND grantee IN "
        "('csa_app','csa_ro','csa_identity','csa_hr_maintenance','PUBLIC')"
    )
    assert privileges == []
