"""Schema contract for migration 0630: the HR process tables (templates, rules, steps, processes,
tasks, the immutable event log) and hr_employees.probation_end (disposable database).
"""

from __future__ import annotations

import json
import typing
import uuid
from datetime import date
from pathlib import Path
from typing import Any, LiteralString

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from psycopg.rows import dict_row
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_schema import DictConnection, _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

TABLES = (
    "hr_process_templates",
    "hr_process_template_rules",
    "hr_process_template_steps",
    "hr_processes",
    "hr_process_tasks",
    "hr_process_events",
)
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
ROLES_WITH_NOTHING = ("csa_ro", "csa_identity", "csa_hr_maintenance", "public")

STEP: dict[str, Any] = {
    "ordinal": 1,
    "title_en": "Sign the contract",
    "owner_role": "hr",
    "anchor": "start_date",
    "due_offset_days": 0,
    "required": True,
}
TASK: dict[str, Any] = {
    "ordinal": 1,
    "title_en": "Sign the contract",
    "owner_role": "hr",
    "anchor": "start_date",
    "due_offset_days": 0,
    "required": True,
    "status": "todo",
}


def _q(text: str) -> LiteralString:
    """A query built from fixed table names above, for the type checker."""
    return typing.cast("LiteralString", text)


def _insert(conn: DictConnection, table: str, row: dict[str, Any]) -> dict[str, Any]:
    columns = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    values = tuple(json.dumps(v) if isinstance(v, dict) else v for v in row.values())
    made = conn.execute(
        _q(f"INSERT INTO {table} ({columns}) VALUES ({marks}) RETURNING *"), values
    ).fetchone()
    assert made is not None
    return dict(made)


class Seed(typing.NamedTuple):
    client: str
    employee: str
    other_employee: str
    template: str
    process: str
    task: str
    event: str


def _seed(dsn: str, *, kind: str = "onboarding") -> Seed:
    """One company: two people, a template with a rule and a step, a process, a task, an event."""
    client = _company(dsn)
    employee, other = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(dsn) as conn:
        for person in (employee, other):
            conn.execute(
                "INSERT INTO hr_employees (client_id, id, display_name)"
                " VALUES (%s, %s, 'Synthetic')",
                (client, person),
            )
        template = _insert(
            conn, "hr_process_templates", {"client_id": client, "kind": kind, "name": "Default"}
        )["id"]
        _insert(conn, "hr_process_template_rules", {"client_id": client, "template_id": template})
        _insert(
            conn,
            "hr_process_template_steps",
            {"client_id": client, "template_id": template, **STEP},
        )
        process = _insert(
            conn,
            "hr_processes",
            {
                "client_id": client,
                "employee_id": employee,
                "kind": kind,
                "template_id": template,
                "template_version": 1,
            },
        )["id"]
        task = _insert(
            conn, "hr_process_tasks", {"client_id": client, "process_id": process, **TASK}
        )["id"]
        event = _insert(
            conn,
            "hr_process_events",
            {"client_id": client, "process_id": process, "task_id": task, "action": "created"},
        )["id"]
    return Seed(client, employee, other, str(template), str(process), str(task), str(event))


def _migration_text() -> str:
    paths = dict(migrate.discover_migrations())
    return Path(paths["0630"]).read_text(encoding="utf-8")


def test_tables_have_rls_and_no_privilege_for_any_application_role(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        for table in TABLES:
            [flags] = conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                (f"public.{table}",),
            )
            assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}, table
            policies = conn.execute(
                "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = %s",
                (table,),
            ).fetchall()
            assert [p["policyname"] for p in policies] == ["tenant_isolation"], table
            assert "app.client_id" in policies[0]["qual"]
            assert "app.client_id" in policies[0]["with_check"]
            for role in ("csa_app", *ROLES_WITH_NOTHING):
                held = {
                    p
                    for p in PRIVILEGES
                    if conn.execute(
                        "SELECT has_table_privilege(%s, %s, %s) AS ok",
                        (role, f"public.{table}", p),
                    ).fetchone()["ok"]  # type: ignore[index]
                }
                assert held == set(), (role, table)


def test_the_app_role_cannot_read_or_write_any_table_directly(
    migrated_dsn: str, app_dsn: str
) -> None:
    s = _seed(migrated_dsn)
    with _app(app_dsn, s.client) as conn:
        for table in TABLES:
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(_q(f"SELECT 1 FROM {table}"))
            conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO hr_process_templates (client_id, kind, name)"
                " VALUES (%s, 'custom', 'Mine')",
                (s.client,),
            )


def test_row_security_isolates_tenants_on_every_table(migrated_dsn: str) -> None:
    """No application role can read these tables, so the tenant policy (defence in depth behind the
    definer functions) is proved with a scratch role that is granted access inside a transaction
    that is rolled back: it has no BYPASSRLS, so the policy alone decides what it sees."""
    a, b = _seed(migrated_dsn), _seed(migrated_dsn)
    probe = f"hr_process_probe_{uuid.uuid4().hex[:8]}"
    conn = DictConnection.connect(migrated_dsn, row_factory=dict_row)
    try:
        conn.execute(_q(f"CREATE ROLE {probe} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        for table in TABLES:
            conn.execute(_q(f"GRANT SELECT, INSERT ON public.{table} TO {probe}"))
        conn.execute(_q(f"SET LOCAL ROLE {probe}"))
        conn.execute("SELECT set_config('app.client_id', %s, true)", (b.client,))
        for table in TABLES:
            seen = conn.execute(_q(f"SELECT client_id FROM {table}")).fetchall()
            assert seen, table
            assert {str(r["client_id"]) for r in seen} == {b.client}, table
            [mine] = conn.execute(
                _q(f"SELECT count(*) AS n FROM {table} WHERE client_id = %s"), (a.client,)
            )
            assert mine["n"] == 0, table
        # A row for the other tenant fails the policy's WITH CHECK; one for this tenant passes.
        conn.execute("SAVEPOINT before_insert")
        with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(
                "INSERT INTO hr_process_templates (client_id, kind, name)"
                " VALUES (%s, 'custom', 'Other tenant')",
                (a.client,),
            )
        conn.execute("ROLLBACK TO SAVEPOINT before_insert")
        conn.execute(
            "INSERT INTO hr_process_templates (client_id, kind, name)"
            " VALUES (%s, 'custom', 'Own tenant')",
            (b.client,),
        )
        # No tenant set: nothing is visible.
        conn.execute("SELECT set_config('app.client_id', '', true)")
        for table in TABLES:
            assert conn.execute(_q(f"SELECT 1 FROM {table}")).fetchall() == [], table
    finally:
        conn.rollback()
        conn.close()


def test_events_are_immutable(migrated_dsn: str) -> None:
    s = _seed(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.InsufficientPrivilege, match="hr_immutable_record"):
            conn.execute("UPDATE hr_process_events SET action = 'x' WHERE id = %s", (s.event,))
        with pytest.raises(errors.InsufficientPrivilege, match="hr_immutable_record"):
            conn.execute("DELETE FROM hr_process_events WHERE id = %s", (s.event,))
        with pytest.raises(errors.InsufficientPrivilege, match="hr_immutable_record"):
            conn.execute("TRUNCATE hr_process_events")
        [row] = conn.execute("SELECT action FROM hr_process_events WHERE id = %s", (s.event,))
        assert row["action"] == "created"


def _expect_check(dsn: str, table: str, extra: dict[str, Any], s: Seed | None = None) -> None:
    """The base row for the table with the overrides must be refused by a CHECK."""
    seed = s or _seed(dsn)
    with _owner(dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            _insert(conn, table, _base_row(table, seed, extra))


def _base_row(table: str, s: Seed, extra: dict[str, Any]) -> dict[str, Any]:
    """A valid new row for the table in the seeded company, with the overrides applied."""
    c = {"client_id": s.client}
    if table == "hr_process_templates":
        row = {**c, "kind": "custom", "name": "Another"}
    elif table == "hr_process_template_steps":
        row = {**c, "template_id": s.template, **STEP, "ordinal": 2}
    elif table == "hr_processes":
        row = {
            **c,
            "employee_id": s.other_employee,
            "kind": "onboarding",
            "template_id": s.template,
            "template_version": 1,
        }
    elif table == "hr_process_tasks":
        row = {**c, "process_id": s.process, **TASK, "ordinal": 2}
    elif table == "hr_process_events":
        row = {**c, "process_id": s.process, "action": "noted"}
    else:
        raise AssertionError(table)
    return {**row, **extra}


@pytest.mark.parametrize(
    ("table", "bad"),
    [
        ("hr_process_templates", {"kind": "probation"}),
        ("hr_process_templates", {"version": 0}),
        ("hr_process_templates", {"name": "  "}),
        ("hr_processes", {"kind": "probation"}),
        ("hr_processes", {"status": "paused"}),
        ("hr_processes", {"template_version": 0}),
        ("hr_processes", {"anchor_dates": "[]"}),
        ("hr_process_template_steps", {"owner_role": "boss"}),
        ("hr_process_template_steps", {"owner_role": "person"}),
        ("hr_process_template_steps", {"owner_role": "hr", "owner_employee_id": "SELF"}),
        ("hr_process_template_steps", {"anchor": "hire_date"}),
        ("hr_process_template_steps", {"approval_by": "ceo"}),
        ("hr_process_template_steps", {"auto_check": "bank_verified"}),
        ("hr_process_template_steps", {"auto_check": "document_uploaded:"}),
        ("hr_process_template_steps", {"auto_check": "document_upload:contract"}),
        ("hr_process_template_steps", {"due_offset_days": 366}),
        ("hr_process_template_steps", {"due_offset_days": -366}),
        ("hr_process_template_steps", {"ordinal": 0}),
        ("hr_process_tasks", {"owner_role": "boss"}),
        ("hr_process_tasks", {"anchor": "hire_date"}),
        ("hr_process_tasks", {"approval_by": "ceo"}),
        ("hr_process_tasks", {"auto_check": "bank_verified"}),
        ("hr_process_tasks", {"auto_check": "document_uploaded:"}),
        ("hr_process_tasks", {"status": "blocked"}),
        ("hr_process_tasks", {"due_offset_days": 366}),
        ("hr_process_tasks", {"due_offset_days": -366}),
        ("hr_process_tasks", {"version": 0}),
        ("hr_process_events", {"action": ""}),
        ("hr_process_events", {"detail": "[]"}),
    ],
)
def test_checks_reject_bad_values(migrated_dsn: str, table: str, bad: dict[str, Any]) -> None:
    s = _seed(migrated_dsn)
    if bad.get("owner_employee_id") == "SELF":
        bad = {**bad, "owner_employee_id": s.employee}
    _expect_check(migrated_dsn, table, bad, s)


def test_person_owned_step_needs_an_employee_of_the_same_company(migrated_dsn: str) -> None:
    a, b = _seed(migrated_dsn), _seed(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        _insert(
            conn,
            "hr_process_template_steps",
            _base_row(
                "hr_process_template_steps",
                a,
                {"owner_role": "person", "owner_employee_id": a.employee},
            ),
        )
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(
                conn,
                "hr_process_template_steps",
                _base_row(
                    "hr_process_template_steps",
                    a,
                    {"ordinal": 3, "owner_role": "person", "owner_employee_id": b.employee},
                ),
            )


def test_the_edges_are_accepted(migrated_dsn: str) -> None:
    s = _seed(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for n, (anchor, offset, check) in enumerate(
            [
                ("start_date", 365, "account_linked"),
                ("last_day", -365, "personal_details_complete"),
                ("probation_end", 0, "access_removed"),
                ("start_date", 1, "document_uploaded:contract"),
            ],
            start=2,
        ):
            _insert(
                conn,
                "hr_process_template_steps",
                _base_row(
                    "hr_process_template_steps",
                    s,
                    {
                        "ordinal": n,
                        "anchor": anchor,
                        "due_offset_days": offset,
                        "auto_check": check,
                    },
                ),
            )
        for n, (status, approval) in enumerate(
            [
                ("awaiting_approval", "hr"),
                ("done", "manager"),
                ("rejected", None),
                ("skipped", None),
            ],
            start=2,
        ):
            _insert(
                conn,
                "hr_process_tasks",
                _base_row(
                    "hr_process_tasks", s, {"ordinal": n, "status": status, "approval_by": approval}
                ),
            )
        for n, role in enumerate(("manager", "employee"), start=10):
            _insert(
                conn,
                "hr_process_template_steps",
                _base_row("hr_process_template_steps", s, {"ordinal": n, "owner_role": role}),
            )
        for status in ("completed", "cancelled"):
            _insert(
                conn,
                "hr_processes",
                _base_row("hr_processes", s, {"employee_id": s.other_employee, "status": status}),
            )


def test_step_ordinal_is_unique_per_template_and_task_ordinal_per_process(
    migrated_dsn: str,
) -> None:
    s = _seed(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.UniqueViolation):
            _insert(
                conn,
                "hr_process_template_steps",
                _base_row("hr_process_template_steps", s, {"ordinal": 1}),
            )
        with pytest.raises(errors.UniqueViolation):
            _insert(conn, "hr_process_tasks", _base_row("hr_process_tasks", s, {"ordinal": 1}))
        # The same ordinal is fine under another template or process.
        other = _insert(
            conn, "hr_process_templates", {"client_id": s.client, "kind": "custom", "name": "Two"}
        )
        _insert(
            conn,
            "hr_process_template_steps",
            {"client_id": s.client, "template_id": other["id"], **STEP},
        )


def test_one_active_process_per_person_and_kind_except_custom(migrated_dsn: str) -> None:
    s = _seed(migrated_dsn)  # an active onboarding for s.employee already exists
    with _owner(migrated_dsn) as conn:
        dupe = _base_row(
            "hr_processes",
            s,
            {"employee_id": s.employee, "kind": "onboarding"},
        )
        with pytest.raises(errors.UniqueViolation):
            _insert(conn, "hr_processes", dupe)
        # A finished one does not count, nor does another kind or another person.
        _insert(conn, "hr_processes", {**dupe, "status": "completed"})
        _insert(conn, "hr_processes", {**dupe, "status": "cancelled"})
        _insert(conn, "hr_processes", {**dupe, "employee_id": s.other_employee})
        # Another kind: no template of that kind exists here, so it has none.
        no_template = {**dupe, "template_id": None, "template_version": None}
        _insert(conn, "hr_processes", {**no_template, "kind": "offboarding"})
        # Custom processes may overlap freely.
        for _ in range(2):
            _insert(conn, "hr_processes", {**no_template, "kind": "custom"})


def test_rows_never_point_at_another_companys_parent(migrated_dsn: str) -> None:
    a, b = _seed(migrated_dsn), _seed(migrated_dsn)
    cases = {
        "hr_process_template_rules": {"client_id": b.client, "template_id": a.template},
        "hr_process_template_steps": _base_row(
            "hr_process_template_steps", b, {"template_id": a.template}
        ),
        "hr_processes": _base_row("hr_processes", b, {"employee_id": a.employee}),
        "hr_process_tasks": _base_row("hr_process_tasks", b, {"process_id": a.process}),
        "hr_process_events": _base_row("hr_process_events", b, {"process_id": a.process}),
    }
    with _owner(migrated_dsn) as conn:
        for table, row in cases.items():
            with pytest.raises(errors.ForeignKeyViolation):
                _insert(conn, table, row)
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(
                conn,
                "hr_processes",
                _base_row("hr_processes", b, {"template_id": a.template}),
            )
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(
                conn,
                "hr_process_tasks",
                _base_row("hr_process_tasks", b, {"assignee_employee_id": a.employee}),
            )
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(
                conn,
                "hr_process_events",
                _base_row("hr_process_events", b, {"task_id": a.task}),
            )


def test_rules_are_one_row_per_template_and_may_name_a_department(migrated_dsn: str) -> None:
    s = _seed(migrated_dsn)
    other = _seed(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.UniqueViolation):
            _insert(
                conn,
                "hr_process_template_rules",
                {"client_id": s.client, "template_id": s.template},
            )
        two = _insert(
            conn, "hr_process_templates", {"client_id": s.client, "kind": "custom", "name": "Two"}
        )
        dept = conn.execute(
            "INSERT INTO hr_departments (client_id, id, name)"
            " VALUES (%s, gen_random_uuid(), 'Ops') RETURNING id",
            (s.client,),
        ).fetchone()
        assert dept is not None
        _insert(
            conn,
            "hr_process_template_rules",
            {
                "client_id": s.client,
                "template_id": two["id"],
                "department_id": dept["id"],
                "employment_type": "full_time",
                "legal_entity_reference": "Synthetic Sdn Bhd",
                "region": "Selangor",
            },
        )
        three = _insert(
            conn, "hr_process_templates", {"client_id": other.client, "kind": "custom", "name": "T"}
        )
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(
                conn,
                "hr_process_template_rules",
                {
                    "client_id": other.client,
                    "template_id": three["id"],
                    "department_id": dept["id"],
                },
            )


def test_task_lookup_indexes_exist(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        defs = [
            r["indexdef"]
            for r in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'"
                " AND tablename = 'hr_process_tasks'"
            )
        ]
    assert any("(client_id, assignee_employee_id, status, due_on)" in d for d in defs), defs
    assert any("(client_id, process_id, ordinal)" in d for d in defs), defs


def test_probation_end_column_is_a_date_and_nullable(migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    person = str(uuid.uuid4())
    with _owner(migrated_dsn) as conn:
        [col] = conn.execute(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'hr_employees' AND column_name = 'probation_end'"
        )
        assert col == {"data_type": "date", "is_nullable": "YES", "column_default": None}
        conn.execute(
            "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s, %s, 'Synthetic')",
            (client, person),
        )
        [row] = conn.execute("SELECT probation_end FROM hr_employees WHERE id = %s", (person,))
        assert row["probation_end"] is None
        conn.execute(
            "UPDATE hr_employees SET probation_end = '2027-03-01' WHERE id = %s", (person,)
        )
        [row] = conn.execute("SELECT probation_end FROM hr_employees WHERE id = %s", (person,))
        assert row["probation_end"] == date(2027, 3, 1)


def _shape(dsn: str) -> dict[str, Any]:
    query = (
        "SELECT"
        " (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'"
        "   AND table_name = ANY(%(t)s)) AS columns,"
        " (SELECT count(*) FROM pg_constraint"
        "   WHERE conrelid = ANY(%(r)s::regclass[])) AS constraints,"
        " (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'"
        "   AND tablename = ANY(%(t)s)) AS indexes,"
        " (SELECT count(*) FROM pg_policies WHERE tablename = ANY(%(t)s)) AS policies,"
        " (SELECT count(*) FROM pg_trigger WHERE tgrelid = ANY(%(r)s::regclass[])"
        "   AND NOT tgisinternal) AS triggers,"
        " (SELECT count(*) FROM information_schema.role_table_grants"
        "   WHERE table_schema = 'public' AND table_name = ANY(%(t)s)) AS grants"
    )
    params = {"t": list(TABLES), "r": [f"public.{n}" for n in TABLES]}
    with _owner(dsn) as conn:
        row = conn.execute(query, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def test_running_0630_again_changes_nothing(migrated_dsn: str) -> None:
    before = _shape(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(_q(_migration_text()))
    assert _shape(migrated_dsn) == before


def test_0630_is_recorded_and_is_the_expected_version(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert "0630" in applied
    assert EXPECTED_SCHEMA_VERSION == max(applied)


def test_a_process_has_the_kind_of_its_template(migrated_dsn: str) -> None:
    s = _seed(migrated_dsn)  # the template is an onboarding template
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(
                conn,
                "hr_processes",
                _base_row("hr_processes", s, {"kind": "offboarding"}),
            )
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(conn, "hr_processes", _base_row("hr_processes", s, {"kind": "custom"}))
        # A template's kind cannot change under a process that uses it.
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(
                "UPDATE hr_process_templates SET kind = 'offboarding' WHERE id = %s", (s.template,)
            )
        # The same kind passes, and a manual custom process may have no template at all.
        _insert(conn, "hr_processes", _base_row("hr_processes", s, {}))
        _insert(
            conn,
            "hr_processes",
            _base_row(
                "hr_processes",
                s,
                {"kind": "custom", "template_id": None, "template_version": None},
            ),
        )


@pytest.mark.parametrize(
    "extra",
    [{"template_id": None}, {"template_version": None}],
)
def test_template_and_version_are_both_set_or_both_null(
    migrated_dsn: str, extra: dict[str, Any]
) -> None:
    _expect_check(migrated_dsn, "hr_processes", extra)


def test_an_event_names_a_task_of_its_own_process(migrated_dsn: str) -> None:
    s = _seed(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        other = _insert(
            conn,
            "hr_processes",
            _base_row("hr_processes", s, {}),
        )
        # s.task belongs to s.process, so an event of the other process may not name it.
        with pytest.raises(errors.ForeignKeyViolation):
            _insert(
                conn,
                "hr_process_events",
                _base_row("hr_process_events", s, {"process_id": other["id"], "task_id": s.task}),
            )
        # Its own process's task, or no task at all, is fine.
        _insert(
            conn,
            "hr_process_events",
            _base_row("hr_process_events", s, {"task_id": s.task}),
        )
        _insert(
            conn,
            "hr_process_events",
            _base_row("hr_process_events", s, {"process_id": other["id"]}),
        )
