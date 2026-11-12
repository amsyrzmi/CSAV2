"""Schema contract for migration 0570: the ai_usage_events ledger, one row per model call a
company's work makes (disposable database).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, LiteralString, cast

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from psycopg.rows import dict_row

from tests.conftest import CSA_RO_PASSWORD, _app_dsn, requires_docker
from tests.test_bookings_schema import DictConnection, _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

TABLE = "ai_usage_events"
MIGRATION = "0570"
ROOT = Path(__file__).resolve().parent.parent
JOBS = (
    "customer_turn",
    "workspace_assistant",
    "hr_assistant",
    "email_analysis",
    "email_reply",
    "email_compose",
    "training_summary",
)


def _sql(text: str) -> LiteralString:
    """Test SQL built from a constant table name and placeholders, never from input."""
    return cast(LiteralString, text)


def _event(client_id: str, **extra: Any) -> tuple[LiteralString, tuple[Any, ...]]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "trace_id": str(uuid.uuid4()),
        "job": "customer_turn",
        "provider": "groq",
        "model": "synthetic-model",
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "schema_name": "SyntheticReply",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return _sql(f"INSERT INTO {TABLE} ({names}) VALUES ({slots})"), tuple(row.values())


def test_table_columns_and_job_check(migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        columns = {
            row["column_name"]: (row["data_type"], row["is_nullable"])
            for row in conn.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = %s",
                (TABLE,),
            )
        }
        assert columns == {
            "id": ("bigint", "NO"),
            "client_id": ("uuid", "NO"),
            "occurred_at": ("timestamp with time zone", "NO"),
            "trace_id": ("uuid", "NO"),
            "conversation_id": ("uuid", "YES"),
            "job": ("text", "NO"),
            "provider": ("text", "NO"),
            "model": ("text", "NO"),
            "prompt_tokens": ("integer", "NO"),
            "completion_tokens": ("integer", "NO"),
            "schema_name": ("text", "NO"),
        }
        for job in JOBS:
            conn.execute(*_event(client, job=job))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_event(client, job="nonsense"))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_event(client, prompt_tokens=-1))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_event(client, completion_tokens=-1))
        # Zero tokens are a valid call (a provider that reports none).
        conn.execute(*_event(client, prompt_tokens=0, completion_tokens=0))
        [count] = conn.execute(
            "SELECT count(*) AS n FROM ai_usage_events WHERE client_id = %s", (client,)
        )
        assert count["n"] == len(JOBS) + 1


def test_the_two_indexes_exist(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        defs = {
            row["indexname"]: row["indexdef"].split(" USING ", 1)[1]
            for row in conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = 'public' AND tablename = %s",
                (TABLE,),
            )
        }
    assert "btree (client_id, occurred_at)" in defs.values()
    assert "btree (client_id, trace_id)" in defs.values()


def test_rls_isolates_clients(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [flags] = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{TABLE}",),
        )
        assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}
    with _app(app_dsn, a) as conn:
        conn.execute(*_event(a))
        conn.commit()
        # A row for another company is refused by the policy, not silently written.
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_event(b))
        conn.rollback()
        assert len(conn.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall()) == 1
    with _app(app_dsn, b) as conn:
        assert conn.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall() == []
    with _app(app_dsn, None) as conn:
        assert conn.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall() == []


def test_app_role_can_insert_and_select_but_not_update_or_delete(
    migrated_dsn: str, app_dsn: str
) -> None:
    client = _company(migrated_dsn)
    with _app(app_dsn, client) as conn:
        conn.execute(*_event(client))
        conn.commit()
        assert len(conn.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall()) == 1
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(_sql(f"UPDATE {TABLE} SET model = 'x'"))
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(_sql(f"DELETE FROM {TABLE}"))
        conn.rollback()
    with _owner(migrated_dsn) as conn:
        [grants] = conn.execute(
            "SELECT has_table_privilege('csa_app', %s, 'TRUNCATE') AS trunc,"
            " has_table_privilege('csa_identity', %s, 'SELECT') AS identity,"
            " has_table_privilege('public', %s, 'SELECT') AS pub",
            (f"public.{TABLE}",) * 3,
        )
    assert grants == {"trunc": False, "identity": False, "pub": False}


def test_ro_role_selects(migrated_dsn: str, app_dsn: str, pg_container: object) -> None:
    client = _company(migrated_dsn)
    with _app(app_dsn, client) as conn:
        conn.execute(*_event(client))
        conn.commit()
    ro = DictConnection.connect(
        _app_dsn(pg_container, role="csa_ro", password=CSA_RO_PASSWORD), row_factory=dict_row
    )
    with ro:
        ro.execute("SELECT set_config('app.client_id', %s, false)", (client,))
        assert len(ro.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall()) == 1
        ro.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            ro.execute(*_event(client))
        ro.rollback()


def test_not_in_retention_purge_list() -> None:
    # The ledger backs billing, so the 30-day purges must never reach it. Scan every purge
    # and retention source (Python and SQL) for the table name.
    sources = [
        *ROOT.glob("csa/**/*retention*.py"),
        *ROOT.glob("csa/**/*purge*.py"),
        *ROOT.glob("db/sql/**/*purge*.sql"),
        *ROOT.glob("db/sql/**/*retention*.sql"),
        ROOT / "csa" / "graph" / "retention.py",
        ROOT / "csa" / "jobs" / "runner.py",
    ]
    assert sources
    for path in sources:
        assert re.search(TABLE, path.read_text(encoding="utf-8")) is None, path


def test_migration_is_recorded_and_the_expected_version_has_reached_it(
    migrated_dsn: str,
) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE version = %s", (MIGRATION,)
        )
    assert row["n"] == 1
    assert EXPECTED_SCHEMA_VERSION >= MIGRATION
