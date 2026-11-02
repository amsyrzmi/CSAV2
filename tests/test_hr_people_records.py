"""Migration 0521: the Sensitive details switch, personal details, documents, leaving
(disposable database). Reuses the HrWorld fixture of tests/test_hr_people.py.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator, Mapping
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.core import crypto, settings
from csa.core.db import q
from csa.core.tenancy import tx
from csa.hr.automation_models import PersonalSave
from csa.hr.personal import fingerprint_payload, personal_fingerprint
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
