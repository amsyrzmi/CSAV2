"""Schema contract for migration 0626: per-person notification preferences, the console email
outbox, the staff-target dead-letter columns, the `cancelled` staff notification status and the
member email lookup (disposable database, synthetic data).
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
from tests.test_console_notifications_schema import member
from tests.test_training_schema import _company

pytestmark = requires_docker

PREFS = "console_notification_prefs"
OUTBOX = "console_email_outbox"
KINDS = [
    "inbox.assigned",
    "inbox.overdue",
    "inbox.snooze_due",
    "inbox.stale_claim",
    "inbox.follow_up",
    "inbox.handback",
]
FUNCTION = "public.console_member_emails(uuid,uuid[])"
INSERT_TARGET = (
    "INSERT INTO staff_notification_targets (client_id, channel, address)"
    " VALUES (%s, 'telegram', %s) RETURNING id"
)


def _q(text: str) -> LiteralString:
    """A query built from fixed names in this file, for the type checker."""
    return typing.cast("LiteralString", text)


def _migration_text() -> str:
    paths = dict(migrate.discover_migrations())
    return Path(paths["0626"]).read_text(encoding="utf-8")


def _prefs(client_id: str, user_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {"client_id": client_id, "user_id": user_id, **extra}
    return (
        f"INSERT INTO {PREFS} ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
        tuple(row.values()),
    )


def _outbox(client_id: str, user_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "user_id": user_id,
        "notification_id": str(uuid.uuid4()),
        "kind": "inbox.overdue",
        "priority": "high",
        "message_key": "inbox.overdue",
        **extra,
    }
    return (
        f"INSERT INTO {OUTBOX} ({', '.join(row)})"
        f" VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
        tuple(row.values()),
    )


def test_migration_stamps_the_version(migrated_dsn: str) -> None:
    assert EXPECTED_SCHEMA_VERSION >= "0625"
    with _owner(migrated_dsn) as conn:
        row = conn.execute("SELECT 1 AS n FROM schema_migrations WHERE version = '0626'").fetchone()
    assert row is not None


def test_prefs_defaults_and_pk(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_prefs(c, person))
        [row] = conn.execute(_q(f"SELECT * FROM {PREFS} WHERE client_id = %s"), (c,))
        assert row["email_enabled"] is False
        assert row["kinds"] == KINDS
        assert row["min_priority"] == "high"
        assert row["failure_notified_at"] is None and row["updated_at"] is not None
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_prefs(c, person))


def test_prefs_needs_a_membership(migrated_dsn: str) -> None:
    c, other = _company(migrated_dsn), _company(migrated_dsn)
    elsewhere = member(migrated_dsn, other)
    with _owner(migrated_dsn) as conn:
        for user_id in (elsewhere, str(uuid.uuid4())):
            with pytest.raises(errors.ForeignKeyViolation):
                conn.execute(*_prefs(c, user_id))
            conn.rollback()


def test_prefs_rejects_unknown_kind_and_priority(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        for extra in (
            {"kinds": ["inbox.overdue", "inbox.bogus"]},
            {"kinds": ["booking.created"]},
            {"min_priority": "critical"},
            {"min_priority": "High"},
        ):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_prefs(c, person, **extra))
            conn.rollback()
        conn.execute(*_prefs(c, person, kinds=[], min_priority="low"))
        conn.execute(
            _q(f"UPDATE {PREFS} SET kinds = %s WHERE user_id = %s"), (["inbox.handback"], person)
        )


def test_email_outbox_notification_id_unique(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    key = str(uuid.uuid4())
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_outbox(c, person, notification_id=key))
        [row] = conn.execute(_q(f"SELECT * FROM {OUTBOX} WHERE id = %s"), (made["id"],))
        assert row["status"] == "pending" and row["attempts"] == 0
        assert row["params"] == {} and row["link"] == {}
        assert row["next_attempt_at"] is not None and row["created_at"] is not None
        assert row["locked_at"] is None and row["last_error"] is None
        assert row["delivered_at"] is None
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_outbox(c, person, notification_id=key))


def test_outbox_status_check(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    person = member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        for status in ("pending", "delivering", "delivered", "dead"):
            conn.execute(*_outbox(c, person, status=status))
        for status in ("cancelled", "sent", ""):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_outbox(c, person, status=status))
            conn.rollback()


def test_outbox_has_a_due_index(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        defs = [
            r["indexdef"]
            for r in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s", (OUTBOX,)
            ).fetchall()
        ]
    assert any("(status, next_attempt_at)" in d for d in defs), defs


def test_staff_notifications_accepts_cancelled(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [target] = conn.execute(INSERT_TARGET, (c, "1"))
        for status in ("pending", "delivering", "delivered", "dead", "cancelled"):
            conn.execute(
                "INSERT INTO staff_notifications"
                " (client_id, target_id, event, payload, idempotency_key, status)"
                " VALUES (%s, %s, 'inbox.overdue', '{}'::jsonb, %s, %s)",
                (c, target["id"], f"k-{uuid.uuid4()}", status),
            )
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO staff_notifications"
                " (client_id, target_id, event, payload, idempotency_key, status)"
                " VALUES (%s, %s, 'inbox.overdue', '{}'::jsonb, %s, 'bogus')",
                (c, target["id"], f"k-{uuid.uuid4()}"),
            )


def test_target_columns_failure_notified_at_version_held_at(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        columns = {
            (r["table_name"], r["column_name"]): r
            for r in conn.execute(
                "SELECT table_name, column_name, data_type, is_nullable, column_default"
                " FROM information_schema.columns WHERE table_schema = 'public'"
                " AND table_name IN ('staff_notification_targets', 'staff_notifications')"
            ).fetchall()
        }
        flag = columns["staff_notification_targets", "failure_notified_at"]
        assert flag["data_type"] == "timestamp with time zone" and flag["is_nullable"] == "YES"
        version = columns["staff_notification_targets", "version"]
        assert version["data_type"] == "integer" and version["is_nullable"] == "NO"
        held = columns["staff_notifications", "held_at"]
        assert held["data_type"] == "timestamp with time zone" and held["is_nullable"] == "YES"
        [target] = conn.execute(INSERT_TARGET, (c, "1"))
        [row] = conn.execute(
            "SELECT failure_notified_at, version FROM staff_notification_targets WHERE id = %s",
            (target["id"],),
        )
        assert row == {"failure_notified_at": None, "version": 0}
        conn.execute(
            "INSERT INTO staff_notifications"
            " (client_id, target_id, event, payload, idempotency_key)"
            " VALUES (%s, %s, 'inbox.overdue', '{}'::jsonb, 'held')",
            (c, target["id"]),
        )
        [note] = conn.execute(
            "SELECT held_at FROM staff_notifications WHERE idempotency_key = 'held'"
        )
        assert note["held_at"] is None


@pytest.mark.parametrize("table", [PREFS, OUTBOX])
def test_rls_and_grants(migrated_dsn: str, table: str) -> None:
    with _owner(migrated_dsn) as conn:
        [flags] = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{table}",),
        )
        assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}
        policies = conn.execute(
            "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = %s", (table,)
        ).fetchall()
        assert [p["policyname"] for p in policies] == ["tenant_isolation"]
        assert "app.client_id" in policies[0]["qual"]
        assert "app.client_id" in policies[0]["with_check"]
        expected = {
            "csa_app": {"SELECT", "INSERT", "UPDATE", "DELETE"},
            "csa_ro": {"SELECT"},
            "csa_identity": set(),
            "public": set(),
        }
        for role, wanted in expected.items():
            held = {
                p
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
                if conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s) AS ok", (role, f"public.{table}", p)
                ).fetchone()["ok"]  # type: ignore[index]
            }
            assert held == wanted, (table, role)


def test_rls_isolates_prefs_and_outbox(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    in_a, in_b = member(migrated_dsn, a), member(migrated_dsn, b)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_prefs(a, in_a))
        conn.execute(*_outbox(a, in_a))
    with _app(app_dsn, b) as conn:
        for table in (PREFS, OUTBOX):
            [seen] = conn.execute(_q(f"SELECT count(*) AS n FROM {table}"))
            assert seen["n"] == 0, table
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_prefs(a, in_a))
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_outbox(a, in_a))
        conn.rollback()
        conn.execute(*_prefs(b, in_b))
        conn.execute(*_outbox(b, in_b))
        conn.commit()
    with _app(app_dsn, a) as conn:
        for table in (PREFS, OUTBOX):
            [seen] = conn.execute(_q(f"SELECT count(*) AS n FROM {table}"))
            assert seen["n"] == 1, table
    with _app(app_dsn, None) as conn:
        for table in (PREFS, OUTBOX):
            assert conn.execute(_q(f"SELECT 1 FROM {table}")).fetchall() == []


def test_member_emails_function(migrated_dsn: str, app_dsn: str) -> None:
    c, other = _company(migrated_dsn), _company(migrated_dsn)
    admin = member(migrated_dsn, c, "tenant_admin")
    reader = member(migrated_dsn, c, "read_only")
    member_off = member(migrated_dsn, c, member_enabled=False)
    account_off = member(migrated_dsn, c, account_enabled=False)
    elsewhere = member(migrated_dsn, other)
    asked = [admin, reader, member_off, account_off, elsewhere, str(uuid.uuid4())]
    with _app(app_dsn, c) as conn:
        rows = conn.execute(
            "SELECT user_id, email FROM console_member_emails(%s, %s::uuid[])", (c, asked)
        ).fetchall()
        assert {str(r["user_id"]): r["email"] for r in rows} == {
            admin: f"{admin}@example.test",
            reader: f"{reader}@example.test",
        }
        assert (
            conn.execute(
                "SELECT * FROM console_member_emails(%s, ARRAY[]::uuid[])", (c,)
            ).fetchall()
            == []
        )
    # Another company's person is never returned, whoever asks and whatever is named.
    with _app(app_dsn, c) as conn:
        assert (
            conn.execute(
                "SELECT * FROM console_member_emails(%s, %s::uuid[])", (c, [elsewhere])
            ).fetchall()
            == []
        )
    with _owner(migrated_dsn) as conn:
        [proc] = conn.execute(
            "SELECT prosecdef, proconfig, prorettype::regtype::text AS ret"
            " FROM pg_proc WHERE oid = %s::regprocedure",
            (FUNCTION,),
        )
        assert proc["prosecdef"] is True
        assert proc["proconfig"] == ["search_path=public, pg_catalog"]
        for role, wanted in {
            "csa_app": True,
            "csa_ro": False,
            "csa_identity": False,
            "public": False,
        }.items():
            [held] = conn.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE') AS ok", (role, FUNCTION)
            )
            assert held["ok"] is wanted, role


def _shape(dsn: str) -> dict[str, Any]:
    query = """
        SELECT
          (SELECT count(*) FROM pg_policies
            WHERE tablename IN ('console_notification_prefs', 'console_email_outbox')) AS policies,
          (SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND (table_name IN
              ('console_notification_prefs', 'console_email_outbox')
              OR (table_name, column_name) IN
                (('staff_notification_targets', 'failure_notified_at'),
                 ('staff_notification_targets', 'version'),
                 ('staff_notifications', 'held_at')))) AS columns,
          (SELECT count(*) FROM pg_constraint
            WHERE conrelid IN ('public.console_notification_prefs'::regclass,
                               'public.console_email_outbox'::regclass,
                               'public.staff_notifications'::regclass)) AS constraints,
          (SELECT string_agg(pg_get_constraintdef(oid), '|' ORDER BY conname) FROM pg_constraint
            WHERE conrelid = 'public.staff_notifications'::regclass AND contype = 'c') AS checks,
          (SELECT count(*) FROM pg_indexes
            WHERE tablename IN ('console_notification_prefs', 'console_email_outbox')) AS indexes,
          (SELECT count(*) FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND table_name IN
              ('console_notification_prefs', 'console_email_outbox')) AS grants,
          (SELECT count(*) FROM information_schema.routine_privileges
            WHERE routine_name = 'console_member_emails') AS function_grants
    """
    with _owner(dsn) as conn:
        row = conn.execute(query).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def test_migration_is_idempotent(migrated_dsn: str) -> None:
    before = _shape(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(_migration_text())  # type: ignore[arg-type]
        conn.execute(_migration_text())  # type: ignore[arg-type]
    assert _shape(migrated_dsn) == before
    assert before["policies"] == 2 and before["grants"] > 0 and before["function_grants"] > 0
    assert "cancelled" in before["checks"]
