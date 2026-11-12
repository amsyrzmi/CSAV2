"""Schema contract for migration 0572: client_billing_plans, the append-only plan history, and
the ``insert_plan`` path (rules enforced, audited in console_audit). Disposable database,
synthetic data.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from typing import Any, LiteralString, cast

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from csa.billing.cycles import cycle_containing
from csa.billing.plans import PlanRow, PlanRuleError, insert_plan, plan_for_cycle
from csa.core import db
from psycopg import errors
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from tests.conftest import CSA_RO_PASSWORD, _app_dsn, requires_docker
from tests.test_bookings_schema import DictConnection, _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

TABLE = "client_billing_plans"
MIGRATION = "0572"


def _sql(text: str) -> LiteralString:
    """Test SQL built from a constant table name and placeholders, never from input."""
    return cast(LiteralString, text)


def _plan(client_id: str, **extra: Any) -> tuple[LiteralString, tuple[Any, ...]]:
    values: dict[str, Any] = {
        "client_id": client_id,
        "stage": "pilot",
        "bundle": "individual",
        "effective_from": date(2026, 8, 15),
        "reason": "synthetic join",
        "created_by": "Synthetic Admin",
        **extra,
    }
    names, slots = ", ".join(values), ", ".join(["%s"] * len(values))
    return _sql(f"INSERT INTO {TABLE} ({names}) VALUES ({slots})"), tuple(values.values())


def _console_user(dsn: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (user_id, f"{user_id}@example.invalid"),
        )
    return user_id


def test_table_columns(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        columns = {
            r["column_name"]: (r["data_type"], r["is_nullable"])
            for r in conn.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = %s",
                (TABLE,),
            )
        }
    assert columns == {
        "id": ("bigint", "NO"),
        "client_id": ("uuid", "NO"),
        "stage": ("text", "NO"),
        "bundle": ("text", "NO"),
        "effective_from": ("date", "NO"),
        "reason": ("text", "NO"),
        "created_by": ("text", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
    }


def test_checks_on_stage_bundle_and_text(migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for stage in ("pilot", "collaborator", "terminated"):
            for bundle in ("individual", "curated", "complete"):
                conn.execute(*_plan(client, stage=stage, bundle=bundle))
        for bad in (
            {"stage": "trial"},
            {"stage": "Pilot"},
            {"bundle": "premium"},
            {"reason": ""},
            {"created_by": ""},
        ):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_plan(client, **bad))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_plan(str(uuid.uuid4())))


def test_rls_isolates_clients(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [flags] = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{TABLE}",),
        )
        assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}
    with _app(app_dsn, a) as conn:
        conn.execute(*_plan(a))
        conn.commit()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_plan(b))
        conn.rollback()
        assert len(conn.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall()) == 1
    with _app(app_dsn, b) as conn:
        assert conn.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall() == []
    with _app(app_dsn, None) as conn:
        assert conn.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall() == []


def test_app_role_selects_and_inserts_but_cannot_update_or_delete(
    migrated_dsn: str, app_dsn: str
) -> None:
    client = _company(migrated_dsn)
    with _app(app_dsn, client) as conn:
        conn.execute(*_plan(client))
        conn.commit()
        for statement in (
            f"UPDATE {TABLE} SET stage = 'collaborator'",
            f"DELETE FROM {TABLE}",
            f"TRUNCATE {TABLE}",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(_sql(statement))
            conn.rollback()
    with _owner(migrated_dsn) as conn:
        [grants] = conn.execute(
            "SELECT has_table_privilege('csa_app', %s, 'UPDATE') AS upd,"
            " has_table_privilege('csa_app', %s, 'DELETE') AS del,"
            " has_table_privilege('csa_identity', %s, 'SELECT') AS identity,"
            " has_table_privilege('public', %s, 'SELECT') AS pub",
            (f"public.{TABLE}",) * 4,
        )
    assert grants == {"upd": False, "del": False, "identity": False, "pub": False}


def test_even_the_owner_cannot_edit_or_delete_history(migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_plan(client))
        for statement in (
            f"UPDATE {TABLE} SET stage = 'collaborator' WHERE client_id = %s",
            f"DELETE FROM {TABLE} WHERE client_id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"):
                conn.execute(_sql(statement), (client,))
        [count] = conn.execute(
            _sql(f"SELECT count(*) AS n FROM {TABLE} WHERE client_id = %s"), (client,)
        )
        assert count["n"] == 1


def test_ro_role_is_select_only(migrated_dsn: str, app_dsn: str, pg_container: object) -> None:
    client = _company(migrated_dsn)
    with _app(app_dsn, client) as conn:
        conn.execute(*_plan(client))
        conn.commit()
    ro = DictConnection.connect(
        _app_dsn(pg_container, role="csa_ro", password=CSA_RO_PASSWORD), row_factory=dict_row
    )
    with ro:
        ro.execute("SELECT set_config('app.client_id', %s, false)", (client,))
        assert len(ro.execute(_sql(f"SELECT id FROM {TABLE}")).fetchall()) == 1
        ro.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            ro.execute(*_plan(client))
        ro.rollback()


async def _insert(
    app_dsn: str,
    client: str,
    actor_id: uuid.UUID,
    new: PlanRow,
    today: date,
    reason: str = "synthetic",
) -> PlanRow:
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            # The same transaction-local setting tx(pool, client_id) makes.
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (client,))
            return await insert_plan(
                conn, "Synthetic Admin", uuid.UUID(client), new, reason, today, actor_id=actor_id
            )


def _new(stage: str, bundle: str, effective: date) -> PlanRow:
    # created_at is set by the database; the value passed here is not stored.
    return PlanRow(stage, bundle, effective, datetime.now(UTC))  # type: ignore[arg-type]


def _audit_rows(dsn: str, client: str) -> list[dict[str, Any]]:
    with _owner(dsn) as conn:
        return conn.execute(
            "SELECT actor_id, action, target_id, details FROM console_audit"
            " WHERE client_id = %s ORDER BY created_at",
            (client,),
        ).fetchall()


async def test_insert_plan_round_trip_writes_the_row_and_an_audit_row(
    migrated_dsn: str, app_dsn: str
) -> None:
    client = _company(migrated_dsn)
    actor = _console_user(migrated_dsn)
    first = await _insert(
        app_dsn,
        client,
        actor,
        _new("pilot", "individual", date(2026, 8, 15)),
        date(2026, 8, 20),
        reason="joined",
    )
    assert (first.stage, first.bundle, first.effective_from) == (
        "pilot",
        "individual",
        date(2026, 8, 15),
    )
    second = await _insert(
        app_dsn,
        client,
        actor,
        _new("collaborator", "curated", date(2026, 9, 5)),
        date(2026, 8, 20),
        reason="converted",
    )
    assert second.created_at > first.created_at
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            _sql(
                f"SELECT id, stage, bundle, effective_from, reason, created_by FROM {TABLE}"
                " WHERE client_id = %s ORDER BY id"
            ),
            (client,),
        ).fetchall()
    assert [(r["stage"], r["reason"], r["created_by"]) for r in rows] == [
        ("pilot", "joined", "Synthetic Admin"),
        ("collaborator", "converted", "Synthetic Admin"),
    ]
    audits = _audit_rows(migrated_dsn, client)
    assert [a["action"] for a in audits] == ["billing.plan_inserted"] * 2
    assert {a["actor_id"] for a in audits} == {actor}
    assert [a["target_id"] for a in audits] == [str(r["id"]) for r in rows]
    assert audits[0]["details"] == {
        "before": None,
        "after": {"stage": "pilot", "bundle": "individual", "effective_from": "2026-08-15"},
        "reason": "joined",
        "created_by": "Synthetic Admin",
    }
    assert audits[1]["details"] == {
        "before": {"stage": "pilot", "bundle": "individual", "effective_from": "2026-08-15"},
        "after": {"stage": "collaborator", "bundle": "curated", "effective_from": "2026-09-05"},
        "reason": "converted",
        "created_by": "Synthetic Admin",
    }


async def test_insert_plan_rule_violation_writes_nothing(migrated_dsn: str, app_dsn: str) -> None:
    client = _company(migrated_dsn)
    actor = _console_user(migrated_dsn)
    await _insert(
        app_dsn, client, actor, _new("pilot", "individual", date(2026, 8, 15)), date(2026, 8, 20)
    )
    with pytest.raises(PlanRuleError) as caught:
        await _insert(
            app_dsn,
            client,
            actor,
            _new("collaborator", "individual", date(2026, 9, 6)),
            date(2026, 8, 20),
        )
    assert caught.value.code == "change_not_on_a_fifth"
    with pytest.raises(PlanRuleError) as first_row:
        await _insert(
            app_dsn,
            _company(migrated_dsn),
            actor,
            _new("pilot", "individual", date(2026, 7, 1)),
            date(2026, 8, 20),
        )
    assert first_row.value.code == "first_row_outside_current_cycle"
    with _owner(migrated_dsn) as conn:
        [count] = conn.execute(
            _sql(f"SELECT count(*) AS n FROM {TABLE} WHERE client_id = %s"), (client,)
        )
    assert count["n"] == 1
    assert len(_audit_rows(migrated_dsn, client)) == 1


async def test_insert_plan_reads_only_its_own_clients_history(
    migrated_dsn: str, app_dsn: str
) -> None:
    # Another company's terminated row must not block this company's first row.
    other, client = _company(migrated_dsn), _company(migrated_dsn)
    actor = _console_user(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_plan(other, stage="terminated", effective_from=date(2026, 8, 10)))
    await _insert(
        app_dsn, client, actor, _new("pilot", "individual", date(2026, 8, 15)), date(2026, 8, 20)
    )


async def test_insert_plan_refuses_a_mismatched_tenant_setting(
    migrated_dsn: str, app_dsn: str
) -> None:
    client, other = _company(migrated_dsn), _company(migrated_dsn)
    actor = _console_user(migrated_dsn)
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (other,))
            with pytest.raises(RuntimeError, match=re.escape("app.client_id")):
                await insert_plan(
                    conn,
                    "Synthetic Admin",
                    uuid.UUID(client),
                    _new("pilot", "individual", date(2026, 8, 15)),
                    "synthetic",
                    date(2026, 8, 20),
                    actor_id=actor,
                )


async def test_insert_plan_refuses_before_any_query_outside_a_transaction(app_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        with pytest.raises(RuntimeError, match="inside a transaction"):
            await insert_plan(
                conn,
                "Synthetic Admin",
                uuid.uuid4(),
                _new("pilot", "individual", date(2026, 8, 15)),
                "synthetic",
                date(2026, 8, 20),
                actor_id=uuid.uuid4(),
            )
        # No statement ran, so no implicit transaction was opened.
        assert conn.info.transaction_status == TransactionStatus.IDLE


async def test_two_inserts_in_one_transaction_order_by_insertion(
    migrated_dsn: str, app_dsn: str
) -> None:
    # now() is the same for the whole transaction; created_at must still order the rows, so a
    # same-date correction made right after the row it corrects wins.
    client = _company(migrated_dsn)
    actor = _console_user(migrated_dsn)
    today = date(2026, 8, 20)
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (client,))
            first = await insert_plan(
                conn,
                "Synthetic Admin",
                uuid.UUID(client),
                _new("pilot", "individual", date(2026, 8, 15)),
                "joined",
                today,
                actor_id=actor,
            )
            fix = await insert_plan(
                conn,
                "Synthetic Admin",
                uuid.UUID(client),
                _new("pilot", "curated", date(2026, 8, 15)),
                "corrected",
                today,
                actor_id=actor,
            )
            rows = await db.q.fetch_all("billing/plans_history.sql", (client,), conn=conn)
    assert fix.created_at > first.created_at
    history = [PlanRow(**r) for r in rows]
    assert plan_for_cycle(history, cycle_containing(today)) == fix


def test_migration_is_recorded_and_the_expected_version_has_reached_it(
    migrated_dsn: str,
) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE version = %s", (MIGRATION,)
        )
    assert row["n"] == 1
    assert EXPECTED_SCHEMA_VERSION >= MIGRATION
