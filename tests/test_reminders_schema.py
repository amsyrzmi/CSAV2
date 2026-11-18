"""Schema contract for migration 0623: the generic reminders table (row security, grants, checks,
indexes; disposable database, synthetic data).
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

TABLE = "reminders"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
STATES = ("scheduled", "fired", "cancelled", "done")


def _q(text: str) -> LiteralString:
    """A query built from fixed names in this file, for the type checker."""
    return typing.cast("LiteralString", text)


def _reminder(client_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "kind": "synthetic.kind",
        "subject_type": "thing",
        "subject_id": "thing-1",
        "due_at": "2030-01-01T00:00:00Z",
        "dedupe_key": f"k-{uuid.uuid4()}",
        **extra,
    }
    return (
        f"INSERT INTO reminders ({', '.join(row)})"
        f" VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
        tuple(row.values()),
    )


def _migration_text() -> str:
    paths = dict(migrate.discover_migrations())
    return Path(paths["0623"]).read_text(encoding="utf-8")


def test_migration_stamps_the_expected_version(migrated_dsn: str) -> None:
    assert EXPECTED_SCHEMA_VERSION >= "0623"
    with _owner(migrated_dsn) as conn:
        row = conn.execute("SELECT 1 AS n FROM schema_migrations WHERE version = '0623'").fetchone()
    assert row is not None


def test_row_security_on_the_table(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_reminder(a))
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
    # Another company sees nothing of company a.
    with _app(app_dsn, b) as conn:
        [seen] = conn.execute(_q(f"SELECT count(*) AS n FROM {TABLE} WHERE client_id = %s"), (a,))
        assert seen["n"] == 0
    # No tenant setting: nothing at all.
    with _app(app_dsn, None) as conn:
        assert conn.execute(_q(f"SELECT 1 FROM {TABLE}")).fetchall() == []
    # The owning company reads its own row.
    with _app(app_dsn, a) as conn:
        [seen] = conn.execute(_q(f"SELECT count(*) AS n FROM {TABLE}"))
        assert seen["n"] == 1


def test_another_company_cannot_write_into_a_tenant(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _app(app_dsn, b) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_reminder(a))
        conn.rollback()
        # Own rows work through the app role, and it can change and remove them.
        [made] = conn.execute(*_reminder(b))
        conn.execute("UPDATE reminders SET fire_count = 1 WHERE id = %s", (made["id"],))
        conn.execute("DELETE FROM reminders WHERE id = %s", (made["id"],))
        conn.commit()


def test_grants(migrated_dsn: str) -> None:
    expected = {
        ("csa_app", TABLE): set(PRIVILEGES),
        ("csa_ro", TABLE): {"SELECT"},
        ("csa_identity", TABLE): set(),
        ("public", TABLE): set(),
    }
    with _owner(migrated_dsn) as conn:
        for (role, table), wanted in expected.items():
            held = {
                p
                for p in PRIVILEGES
                if conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s) AS ok", (role, f"public.{table}", p)
                ).fetchone()["ok"]  # type: ignore[index]
            }
            assert held == wanted, (role, table)


def test_defaults(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_reminder(c))
        [row] = conn.execute("SELECT * FROM reminders WHERE id = %s", (made["id"],))
    assert row["state"] == "scheduled"
    assert row["fire_count"] == 0 and row["attempts"] == 0
    assert row["last_fired_at"] is None and row["last_error"] is None
    assert row["payload"] == {}
    assert row["created_at"] is not None and row["updated_at"] is not None


@pytest.mark.parametrize("state", STATES)
def test_every_state_is_accepted(migrated_dsn: str, state: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_reminder(c, state=state))


@pytest.mark.parametrize("state", ["", "Scheduled", "sent", "failed"])
def test_an_unknown_state_is_refused(migrated_dsn: str, state: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn, pytest.raises(errors.CheckViolation):
        conn.execute(*_reminder(c, state=state))


def test_dedupe_key_is_unique_per_company(migrated_dsn: str) -> None:
    c, other = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_reminder(c, dedupe_key="same"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_reminder(c, dedupe_key="same"))
        conn.rollback()
        conn.execute(*_reminder(other, dedupe_key="same"))


def test_required_columns_and_the_company_foreign_key(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_reminder(str(uuid.uuid4())))
        conn.rollback()
        for column in ("kind", "subject_type", "subject_id", "due_at", "dedupe_key", "payload"):
            with pytest.raises(errors.NotNullViolation):
                conn.execute(*_reminder(c, **{column: None}))
            conn.rollback()


def test_indexes_serve_the_due_scan_and_one_subject(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s", (TABLE,)
        ).fetchall()
    defs = [r["indexdef"] for r in rows]
    assert any("(due_at)" in d and "state = 'scheduled'" in d for d in defs), defs
    assert any("(client_id, kind, subject_type, subject_id)" in d for d in defs), defs


def _shape(dsn: str) -> dict[str, Any]:
    query = """
        SELECT
          (SELECT count(*) FROM pg_policies WHERE tablename = 'reminders') AS policies,
          (SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'reminders') AS columns,
          (SELECT count(*) FROM pg_constraint
            WHERE conrelid = 'public.reminders'::regclass) AS constraints,
          (SELECT count(*) FROM pg_indexes WHERE tablename = 'reminders') AS indexes,
          (SELECT count(*) FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND table_name = 'reminders') AS grants
    """
    with _owner(dsn) as conn:
        row = conn.execute(query).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def test_running_0623_again_changes_nothing(migrated_dsn: str) -> None:
    before = _shape(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(_migration_text())  # type: ignore[arg-type]
        conn.execute(_migration_text())  # type: ignore[arg-type]
    assert _shape(migrated_dsn) == before
    assert before["policies"] == 1 and before["grants"] > 0
