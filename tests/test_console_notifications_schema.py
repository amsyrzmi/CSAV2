"""Schema contract for migration 0624: console_notifications (row security, grants, checks, the
membership foreign key, indexes) and the recipient functions inbox_team_user_ids,
inbox_admin_user_ids and console_notification_members (disposable database, synthetic data).
"""

from __future__ import annotations

import typing
import uuid
from pathlib import Path
from typing import Any, LiteralString

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_schema import _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

TABLE = "console_notifications"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
PRIORITIES = ("low", "normal", "high", "urgent")
FUNCTIONS = (
    "public.inbox_team_user_ids(uuid)",
    "public.inbox_admin_user_ids(uuid)",
    "public.console_notification_members(uuid,uuid[])",
)


def _q(text: str) -> LiteralString:
    """A query built from fixed names in this file, for the type checker."""
    return typing.cast("LiteralString", text)


def member(
    dsn: str,
    client_id: str,
    role: str = "tenant_staff",
    *,
    member_enabled: bool = True,
    account_enabled: bool = True,
    platform_admin: bool = False,
) -> str:
    """A synthetic console account with a membership in ``client_id``, written as the owner."""
    user_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO console_users (id, email, display_name, is_enabled, is_platform_admin)"
            " VALUES (%s, %s, 'Synthetic', %s, %s)",
            (user_id, f"{user_id}@example.test", account_enabled, platform_admin),
        )
        conn.execute(
            "INSERT INTO console_memberships (user_id, client_id, role, is_enabled)"
            " VALUES (%s, %s, %s, %s)",
            (user_id, client_id, role, member_enabled),
        )
    return user_id


def _notification(client_id: str, user_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "user_id": user_id,
        "kind": "synthetic.kind",
        "priority": "normal",
        "message_key": "synthetic.kind",
        "dedupe_key": f"k-{uuid.uuid4()}",
        **extra,
    }
    return (
        f"INSERT INTO {TABLE} ({', '.join(row)})"
        f" VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
        tuple(row.values()),
    )


def _migration_text() -> str:
    paths = dict(migrate.discover_migrations())
    return Path(paths["0624"]).read_text(encoding="utf-8")


def test_migration_stamps_the_expected_version(migrated_dsn: str) -> None:
    assert EXPECTED_SCHEMA_VERSION >= "0624"
    with _owner(migrated_dsn) as conn:
        row = conn.execute("SELECT 1 AS n FROM schema_migrations WHERE version = '0624'").fetchone()
    assert row is not None


def test_row_security_on_the_table(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    person = member(migrated_dsn, a)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_notification(a, person))
        [flags] = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{TABLE}",),
        )
        assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}
        policies = conn.execute(
            "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = %s", (TABLE,)
        ).fetchall()
        assert [p["policyname"] for p in policies] == ["tenant_isolation"]
        assert "app.client_id" in policies[0]["qual"]
        assert "app.client_id" in policies[0]["with_check"]
    with _app(app_dsn, b) as conn:
        [seen] = conn.execute(_q(f"SELECT count(*) AS n FROM {TABLE} WHERE client_id = %s"), (a,))
        assert seen["n"] == 0
    with _app(app_dsn, None) as conn:
        assert conn.execute(_q(f"SELECT 1 FROM {TABLE}")).fetchall() == []
    with _app(app_dsn, a) as conn:
        [seen] = conn.execute(_q(f"SELECT count(*) AS n FROM {TABLE}"))
        assert seen["n"] == 1


def test_another_company_cannot_write_into_a_tenant(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    in_a, in_b = member(migrated_dsn, a), member(migrated_dsn, b)
    with _app(app_dsn, b) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_notification(a, in_a))
        conn.rollback()
        [made] = conn.execute(*_notification(b, in_b))
        conn.execute(_q(f"UPDATE {TABLE} SET read_at = now() WHERE id = %s"), (made["id"],))
        conn.execute(_q(f"DELETE FROM {TABLE} WHERE id = %s"), (made["id"],))
        conn.commit()


def test_grants(migrated_dsn: str) -> None:
    expected = {
        "csa_app": set(PRIVILEGES),
        "csa_ro": {"SELECT"},
        "csa_identity": set(),
        "public": set(),
    }
    with _owner(migrated_dsn) as conn:
        for role, wanted in expected.items():
            held = {
                p
                for p in PRIVILEGES
                if conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s) AS ok", (role, f"public.{TABLE}", p)
                ).fetchone()["ok"]  # type: ignore[index]
            }
            assert held == wanted, role


@pytest.mark.parametrize("function", FUNCTIONS)
def test_functions_are_definers_with_a_pinned_path_and_only_the_app_runs_them(
    migrated_dsn: str, function: str
) -> None:
    with _owner(migrated_dsn) as conn:
        [proc] = conn.execute(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (function,)
        )
        assert proc["prosecdef"] is True
        assert proc["proconfig"] == ["search_path=pg_catalog, public, pg_temp"]
        for role, wanted in {
            "csa_app": True,
            "csa_ro": False,
            "csa_identity": False,
            "csa_hr_maintenance": False,
            "public": False,
        }.items():
            [held] = conn.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE') AS ok", (role, function)
            )
            assert held["ok"] is wanted, (role, function)


def test_defaults(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_notification(c, person))
        [row] = conn.execute(_q(f"SELECT * FROM {TABLE} WHERE id = %s"), (made["id"],))
    assert row["params"] == {} and row["link"] == {}
    assert row["read_at"] is None and row["created_at"] is not None


@pytest.mark.parametrize("priority", PRIORITIES)
def test_every_priority_is_accepted(migrated_dsn: str, priority: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_notification(c, person, priority=priority))


@pytest.mark.parametrize("priority", ["", "Normal", "critical"])
def test_an_unknown_priority_is_refused(migrated_dsn: str, priority: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn, pytest.raises(errors.CheckViolation):
        conn.execute(*_notification(c, person, priority=priority))


def test_dedupe_key_is_unique_per_company_and_person(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    one, two = member(migrated_dsn, c), member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_notification(c, one, dedupe_key="same"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_notification(c, one, dedupe_key="same"))
        conn.rollback()
        conn.execute(*_notification(c, two, dedupe_key="same"))


def test_a_person_outside_the_company_is_refused_by_the_foreign_key(migrated_dsn: str) -> None:
    c, other = _company(migrated_dsn), _company(migrated_dsn)
    elsewhere = member(migrated_dsn, other)
    with _owner(migrated_dsn) as conn:
        for user_id in (elsewhere, str(uuid.uuid4())):
            with pytest.raises(errors.ForeignKeyViolation):
                conn.execute(*_notification(c, user_id))
            conn.rollback()
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_notification(str(uuid.uuid4()), elsewhere))


def test_required_columns(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        for column in ("kind", "priority", "message_key", "params", "link", "dedupe_key"):
            with pytest.raises(errors.NotNullViolation):
                conn.execute(*_notification(c, person, **{column: None}))
            conn.rollback()


def test_indexes_serve_the_list_and_the_unread_count(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s", (TABLE,)
        ).fetchall()
    defs = [r["indexdef"] for r in rows]
    assert any("(client_id, user_id, created_at DESC)" in d for d in defs), defs
    assert any("(client_id, user_id)" in d and "read_at IS NULL" in d for d in defs), defs


class People(typing.NamedTuple):
    client_id: str
    admin: str
    staff: str
    reader: str
    member_off: str
    account_off: str
    platform: str
    elsewhere: str


def _people(dsn: str) -> People:
    c, other = _company(dsn), _company(dsn)
    return People(
        client_id=c,
        admin=member(dsn, c, "tenant_admin"),
        staff=member(dsn, c, "tenant_staff"),
        reader=member(dsn, c, "read_only"),
        member_off=member(dsn, c, "tenant_staff", member_enabled=False),
        account_off=member(dsn, c, "tenant_admin", account_enabled=False),
        platform=member(dsn, c, "tenant_admin", platform_admin=True),
        elsewhere=member(dsn, other, "tenant_admin"),
    )


def _ids(dsn: str, setting: str | None, query: str, *args: Any) -> set[str]:
    with _app(dsn, setting) as conn:
        rows = conn.execute(_q(query), args).fetchall()
    return {str(next(iter(r.values()))) for r in rows}


def test_team_is_the_enabled_admins_and_staff(migrated_dsn: str, app_dsn: str) -> None:
    p = _people(migrated_dsn)
    got = _ids(app_dsn, p.client_id, "SELECT * FROM inbox_team_user_ids(%s)", p.client_id)
    assert got == {p.admin, p.staff}


def test_admins_are_the_enabled_tenant_admins(migrated_dsn: str, app_dsn: str) -> None:
    p = _people(migrated_dsn)
    got = _ids(app_dsn, p.client_id, "SELECT * FROM inbox_admin_user_ids(%s)", p.client_id)
    assert got == {p.admin}


def test_members_keeps_enabled_members_of_any_role_only(migrated_dsn: str, app_dsn: str) -> None:
    p = _people(migrated_dsn)
    asked = [p.admin, p.staff, p.reader, p.member_off, p.account_off, p.platform, p.elsewhere]
    got = _ids(
        app_dsn,
        p.client_id,
        "SELECT * FROM console_notification_members(%s, %s::uuid[])",
        p.client_id,
        [*asked, str(uuid.uuid4()), p.staff],
    )
    assert got == {p.admin, p.staff, p.reader}


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM inbox_team_user_ids(%s)",
        "SELECT * FROM inbox_admin_user_ids(%s)",
        "SELECT * FROM console_notification_members(%s, ARRAY[]::uuid[])",
    ],
)
def test_functions_refuse_a_company_other_than_the_transaction_s(
    migrated_dsn: str, app_dsn: str, query: str
) -> None:
    p = _people(migrated_dsn)
    other = _company(migrated_dsn)
    for setting, asked in ((other, p.client_id), (None, p.client_id), (p.client_id, None)):
        with _app(app_dsn, setting) as conn, pytest.raises(errors.InsufficientPrivilege):
            conn.execute(_q(query), (asked,)).fetchall()


def _shape(dsn: str) -> dict[str, Any]:
    query = """
        SELECT
          (SELECT count(*) FROM pg_policies WHERE tablename = 'console_notifications') AS policies,
          (SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'console_notifications') AS columns,
          (SELECT count(*) FROM pg_constraint
            WHERE conrelid = 'public.console_notifications'::regclass) AS constraints,
          (SELECT count(*) FROM pg_indexes WHERE tablename = 'console_notifications') AS indexes,
          (SELECT count(*) FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND table_name = 'console_notifications') AS grants,
          (SELECT count(*) FROM pg_proc WHERE proname IN
            ('inbox_team_user_ids', 'inbox_admin_user_ids', 'console_notification_members'))
            AS functions,
          (SELECT count(*) FROM information_schema.routine_privileges
            WHERE routine_name IN
            ('inbox_team_user_ids', 'inbox_admin_user_ids', 'console_notification_members'))
            AS function_grants
    """
    with _owner(dsn) as conn:
        row = conn.execute(query).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def test_running_0624_again_changes_nothing(migrated_dsn: str) -> None:
    before = _shape(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(_migration_text())  # type: ignore[arg-type]
        conn.execute(_migration_text())  # type: ignore[arg-type]
    assert _shape(migrated_dsn) == before
    assert before["policies"] == 1 and before["grants"] > 0 and before["functions"] == 3
