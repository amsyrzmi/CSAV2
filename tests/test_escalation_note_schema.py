"""Schema contract for migration 0560: escalations.note (a staff context note, at most 500
characters) and the escalations_conversation_created index (disposable database).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_schema import _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

MIGRATION = "0560"
CHECK = "escalations_note_length"
INDEX = "escalations_conversation_created"


def _escalation(client_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "trace_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "reason": "unresolved_turns",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO escalations ({names}) VALUES ({slots})", tuple(row.values())


def test_note_is_nullable_and_existing_rows_keep_null(migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [column] = conn.execute(
            "SELECT data_type, is_nullable FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'escalations' AND column_name = 'note'"
        )
        assert column == {"data_type": "text", "is_nullable": "YES"}
        # A row written without a note (as every row before this migration) keeps NULL.
        sql, params = _escalation(client)
        conn.execute(sql, params)
        [row] = conn.execute("SELECT note FROM escalations WHERE trace_id = %s", (params[1],))
        assert row["note"] is None


def test_a_note_over_500_characters_is_refused(migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        sql, params = _escalation(client, note="x" * 500)
        conn.execute(sql, params)
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_escalation(client, note="x" * 501))
        # Characters are counted, not bytes: 500 multi-byte characters are allowed.
        conn.execute(*_escalation(client, note="é" * 500))


def test_the_app_role_writes_a_note_only_in_its_own_company(
    migrated_dsn: str, app_dsn: str
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    sql, params = _escalation(a)
    with _owner(migrated_dsn) as conn:
        conn.execute(sql, params)
    trace = params[1]
    with _app(app_dsn, b) as conn:
        changed = conn.execute("UPDATE escalations SET note = 'x' WHERE trace_id = %s", (trace,))
        assert changed.rowcount == 0
        conn.rollback()
    with _app(app_dsn, a) as conn:
        changed = conn.execute("UPDATE escalations SET note = 'x' WHERE trace_id = %s", (trace,))
        assert changed.rowcount == 1
        conn.commit()
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT note FROM escalations WHERE trace_id = %s", (trace,))
        assert row["note"] == "x"


def test_the_index_exists(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
            (INDEX,),
        )
    assert row["indexdef"].split(" USING ", 1)[1] == (
        "btree (client_id, conversation_id, created_at DESC)"
    )


def test_the_migration_is_safe_to_run_twice(migrated_dsn: str) -> None:
    [path] = [p for v, p in migrate.discover_migrations() if v == MIGRATION]
    query = (
        "SELECT (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s) AS c,"
        " (SELECT indexdef FROM pg_indexes WHERE indexname = %s) AS i,"
        " (SELECT count(*) FROM information_schema.columns"
        "   WHERE table_name = 'escalations' AND column_name = 'note') AS n"
    )
    with _owner(migrated_dsn) as conn:
        before = conn.execute(query, (CHECK, INDEX)).fetchone()  # type: ignore[arg-type]
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        after = conn.execute(query, (CHECK, INDEX)).fetchone()  # type: ignore[arg-type]
    assert before == after
    assert before is not None and before["n"] == 1 and before["c"] is not None


def test_max_applied_version_is_the_expected_one(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT max(version) AS v FROM schema_migrations")
    # Later migrations raise the version; this one only needs to be at or below it.
    assert row["v"] == EXPECTED_SCHEMA_VERSION >= MIGRATION
