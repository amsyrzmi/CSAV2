"""Schema contract for migration 0621: inbox_settings, inbox_queue_events and inbox_saved_views
(row security, grants, checks, foreign keys; disposable database).
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

TABLES = ("inbox_settings", "inbox_queue_events", "inbox_saved_views")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
ACTIONS = (
    "entered",
    "replied",
    "claimed",
    "unclaimed",
    "assigned",
    "snoozed",
    "woke",
    "resolved",
    "handed_back",
    "priority_set",
    "topic_set",
    "reason_changed",
)


def _q(text: str) -> LiteralString:
    """A query built from fixed names in this file, for the type checker."""
    return typing.cast("LiteralString", text)


def _user(dsn: str) -> str:
    user_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (user_id, f"{user_id}@example.test"),
        )
    return user_id


def _conversation(dsn: str, client_id: str) -> str:
    contact_id, conversation_id = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Synthetic Customer')",
            (contact_id, client_id),
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread)"
            " VALUES (%s, %s, %s, 'telegram', %s)",
            (conversation_id, client_id, contact_id, f"thread-{conversation_id}"),
        )
    return conversation_id


def _settings(client_id: str, **extra: Any) -> tuple[Any, ...]:
    row = {"client_id": client_id, **extra}
    return (
        f"INSERT INTO inbox_settings ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
        tuple(row.values()),
    )


def _event(
    client_id: str, conversation_id: str | None, action: str = "entered", **extra: Any
) -> tuple[Any, ...]:
    row = {"client_id": client_id, "conversation_id": conversation_id, "action": action, **extra}
    return (
        f"INSERT INTO inbox_queue_events ({', '.join(row)})"
        f" VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
        tuple(row.values()),
    )


def _view(client_id: str, user_id: str, name: str = "Mine today", **extra: Any) -> tuple[Any, ...]:
    row = {
        "client_id": client_id,
        "user_id": user_id,
        "name": name,
        "filters": '{"priority": "high"}',
        **extra,
    }
    return (
        f"INSERT INTO inbox_saved_views ({', '.join(row)})"
        f" VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
        tuple(row.values()),
    )


def _migration_text() -> str:
    paths = dict(migrate.discover_migrations())
    return Path(paths["0621"]).read_text(encoding="utf-8")


def _seed_all(dsn: str, client_id: str) -> None:
    user_id = _user(dsn)
    conversation_id = _conversation(dsn, client_id)
    with _owner(dsn) as conn:
        conn.execute(*_settings(client_id))
        conn.execute(*_event(client_id, conversation_id))
        conn.execute(*_view(client_id, user_id))


def test_migration_stamps_the_expected_version(migrated_dsn: str) -> None:
    assert EXPECTED_SCHEMA_VERSION >= "0621"
    with _owner(migrated_dsn) as conn:
        row = conn.execute("SELECT 1 AS n FROM schema_migrations WHERE version = '0621'").fetchone()
    assert row is not None


def test_row_security_on_all_three_tables(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    _seed_all(migrated_dsn, a)
    with _owner(migrated_dsn) as conn:
        for table in TABLES:
            [flags] = conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                (f"public.{table}",),
            )
            assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}
            policies = conn.execute(
                "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = %s",
                (table,),
            ).fetchall()
            assert [p["policyname"] for p in policies] == ["tenant_isolation"]
            assert "app.client_id" in policies[0]["qual"]
            assert "app.client_id" in policies[0]["with_check"]
    # Another company sees nothing of company a.
    with _app(app_dsn, b) as conn:
        for table in TABLES:
            [seen] = conn.execute(
                _q(f"SELECT count(*) AS n FROM {table} WHERE client_id = %s"), (a,)
            )
            assert seen["n"] == 0, table
    # No tenant setting: nothing at all.
    with _app(app_dsn, None) as conn:
        for table in TABLES:
            assert conn.execute(_q(f"SELECT 1 FROM {table}")).fetchall() == [], table
    # The owning company reads its own rows.
    with _app(app_dsn, a) as conn:
        for table in TABLES:
            [seen] = conn.execute(_q(f"SELECT count(*) AS n FROM {table}"))
            assert seen["n"] == 1, table


def test_another_company_cannot_write_into_a_tenant(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    user_id = _user(migrated_dsn)
    conversation_id = _conversation(migrated_dsn, a)
    with _app(app_dsn, b) as conn:
        for statement in (
            _settings(a),
            _event(a, conversation_id),
            _view(a, user_id),
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(*statement)
            conn.rollback()
        # Own rows work through the app role (the event needs the sequence as well).
        conn.execute(*_settings(b))
        conn.execute(*_view(b, user_id))
        conn.commit()
    c = _company(migrated_dsn)
    own_conversation = _conversation(migrated_dsn, c)
    with _app(app_dsn, c) as conn:
        conn.execute(*_event(c, own_conversation))
        conn.commit()


def test_grants(migrated_dsn: str) -> None:
    expected = {
        ("csa_app", "inbox_settings"): {"SELECT", "INSERT", "UPDATE"},
        ("csa_app", "inbox_queue_events"): {"SELECT", "INSERT"},
        ("csa_app", "inbox_saved_views"): set(PRIVILEGES),
        ("csa_ro", "inbox_settings"): {"SELECT"},
        ("csa_ro", "inbox_queue_events"): {"SELECT"},
        ("csa_ro", "inbox_saved_views"): {"SELECT"},
    }
    for role in ("csa_identity", "public"):
        for table in TABLES:
            expected[(role, table)] = set()
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


def test_the_event_log_is_append_only_for_the_app_role(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    conversation_id = _conversation(migrated_dsn, c)
    with _app(app_dsn, c) as conn:
        [made] = conn.execute(*_event(c, conversation_id))
        conn.commit()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE inbox_queue_events SET action = 'woke' WHERE id = %s", (made["id"],)
            )
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("DELETE FROM inbox_queue_events WHERE id = %s", (made["id"],))
        conn.rollback()
        [still] = conn.execute("SELECT action FROM inbox_queue_events WHERE id = %s", (made["id"],))
        assert still["action"] == "entered"


def test_a_company_without_a_settings_row_reads_nothing(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _app(app_dsn, c) as conn:
        assert conn.execute("SELECT * FROM inbox_settings").fetchall() == []
    with _owner(migrated_dsn) as conn:
        [n] = conn.execute("SELECT count(*) AS n FROM inbox_settings WHERE client_id = %s", (c,))
        assert n["n"] == 0


def test_settings_defaults(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_settings(c))
        [row] = conn.execute("SELECT * FROM inbox_settings WHERE client_id = %s", (c,))
    assert {k: v for k, v in row.items() if k not in ("client_id", "updated_at")} == {
        "version": 0,
        "target_urgent_min": 15,
        "target_high_min": 60,
        "target_normal_min": 240,
        "target_low_min": 1440,
        "repeat_minutes": 60,
        "max_repeats": 3,
        "follow_up_hours": 48,
        "stale_claim_hours": 8,
        "handback_prompt_hours": 24,
    }
    assert row["updated_at"] is not None


@pytest.mark.parametrize(
    ("column", "good", "bad"),
    [
        ("target_urgent_min", (5, 10080), (4, 10081)),
        ("target_high_min", (5, 10080), (4, 10081)),
        ("target_normal_min", (5, 10080), (4, 10081)),
        ("target_low_min", (5, 10080), (0, 4, 10081)),
        ("repeat_minutes", (15, 1440), (14, 1441)),
        ("max_repeats", (0, 10), (-1, 11)),
        ("follow_up_hours", (0, 1, 720), (-1, 721)),
        ("stale_claim_hours", (0, 1, 168), (-1, 169)),
        ("handback_prompt_hours", (0, 1, 720), (-1, 721)),
    ],
)
def test_settings_ranges_accept_the_edges_and_refuse_the_rest(
    migrated_dsn: str, column: str, good: tuple[int, ...], bad: tuple[int, ...]
) -> None:
    with _owner(migrated_dsn) as conn:
        for value in good:
            c = _company(migrated_dsn)
            conn.execute(*_settings(c, **{column: value}))
        for value in bad:
            c = _company(migrated_dsn)
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_settings(c, **{column: value}))


def test_settings_refuse_an_unknown_company_and_a_null_version(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_settings(str(uuid.uuid4())))
        with pytest.raises(errors.NotNullViolation):
            conn.execute(*_settings(_company(migrated_dsn), version=None))


def test_one_settings_row_per_company(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_settings(c))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_settings(c))


@pytest.mark.parametrize("action", ACTIONS)
def test_every_event_action_is_accepted(migrated_dsn: str, action: str) -> None:
    c = _company(migrated_dsn)
    conversation_id = _conversation(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_event(c, conversation_id, action))
        assert made["id"] > 0


@pytest.mark.parametrize("action", ["", "created", "Claimed", "deleted", "closed"])
def test_an_unknown_event_action_is_refused(migrated_dsn: str, action: str) -> None:
    c = _company(migrated_dsn)
    conversation_id = _conversation(migrated_dsn, c)
    with _owner(migrated_dsn) as conn, pytest.raises(errors.CheckViolation):
        conn.execute(*_event(c, conversation_id, action))


def test_event_action_list_is_exactly_the_agreed_twelve(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid = 'public.inbox_queue_events'::regclass AND contype = 'c'"
            " AND pg_get_constraintdef(oid) LIKE '%action%'"
        )
    listed = {part.split("'")[1] for part in row["def"].split("::text") if "'" in part}
    assert listed == set(ACTIONS)


def test_event_columns_and_defaults(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    conversation_id = _conversation(migrated_dsn, c)
    user_id = _user(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_event(c, conversation_id))
        conn.execute(
            *_event(
                c,
                conversation_id,
                "claimed",
                actor_user_id=user_id,
                from_status="needs_staff",
                to_status="needs_staff",
                details='{"via": "test"}',
            )
        )
        rows = conn.execute(
            "SELECT * FROM inbox_queue_events WHERE client_id = %s ORDER BY id", (c,)
        ).fetchall()
    first, second = rows
    assert first["actor_user_id"] is None and first["from_status"] is None
    assert first["details"] == {}
    assert first["created_at"] is not None
    assert second["id"] > first["id"]
    assert second["actor_user_id"] == uuid.UUID(user_id)
    assert second["details"] == {"via": "test"}


def test_event_foreign_keys(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    conversation_id = _conversation(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_event(str(uuid.uuid4()), conversation_id))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_event(c, str(uuid.uuid4())))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_event(c, conversation_id, actor_user_id=str(uuid.uuid4())))
        with pytest.raises(errors.NotNullViolation):
            conn.execute(*_event(c, None))


def test_event_index_serves_the_history_of_one_conversation(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'inbox_queue_events'"
            " AND indexdef LIKE '%conversation_id%'"
        )
    assert "(client_id, conversation_id, id DESC)" in row["indexdef"]


def test_saved_view_name_length(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    user_id = _user(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_view(c, user_id, "a"))
        conn.execute(*_view(c, user_id, "b" * 60))
        for bad in ("", "c" * 61):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_view(c, user_id, bad))


def test_saved_view_name_is_unique_per_person_ignoring_case(migrated_dsn: str) -> None:
    c, other = _company(migrated_dsn), _company(migrated_dsn)
    user_id, another = _user(migrated_dsn), _user(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_view(c, user_id, "Urgent"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_view(c, user_id, "urgent"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_view(c, user_id, "URGENT"))
        # A different person, and a different company, may use the same name.
        conn.execute(*_view(c, another, "Urgent"))
        conn.execute(*_view(other, user_id, "Urgent"))


def test_saved_view_defaults_and_not_null_filters(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    user_id = _user(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_view(c, user_id))
        [row] = conn.execute("SELECT * FROM inbox_saved_views WHERE id = %s", (made["id"],))
        assert row["filters"] == {"priority": "high"}
        assert row["created_at"] is not None
        with pytest.raises(errors.NotNullViolation):
            conn.execute(*_view(c, user_id, "No filters", filters=None))


def test_saved_view_foreign_keys(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    user_id = _user(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_view(str(uuid.uuid4()), user_id))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_view(c, str(uuid.uuid4())))


def test_the_app_role_can_change_and_remove_its_saved_views(
    migrated_dsn: str, app_dsn: str
) -> None:
    c = _company(migrated_dsn)
    user_id = _user(migrated_dsn)
    with _app(app_dsn, c) as conn:
        [made] = conn.execute(*_view(c, user_id))
        conn.execute("UPDATE inbox_saved_views SET name = 'Renamed' WHERE id = %s", (made["id"],))
        conn.execute("DELETE FROM inbox_saved_views WHERE id = %s", (made["id"],))
        conn.commit()
        assert conn.execute("SELECT 1 FROM inbox_saved_views").fetchall() == []


def test_the_app_role_can_update_settings(migrated_dsn: str, app_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _app(app_dsn, c) as conn:
        conn.execute(*_settings(c))
        conn.execute(
            "UPDATE inbox_settings SET version = version + 1, repeat_minutes = 30"
            " WHERE client_id = %s",
            (c,),
        )
        conn.commit()
        [row] = conn.execute("SELECT version, repeat_minutes FROM inbox_settings")
        assert row == {"version": 1, "repeat_minutes": 30}


def _shape(dsn: str) -> dict[str, Any]:
    query = """
        SELECT
          (SELECT count(*) FROM pg_policies WHERE tablename = ANY(%(t)s)) AS policies,
          (SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(%(t)s)) AS columns,
          (SELECT count(*) FROM pg_constraint
            WHERE conrelid = ANY(%(r)s::regclass[])) AS constraints,
          (SELECT count(*) FROM pg_indexes WHERE tablename = ANY(%(t)s)) AS indexes,
          (SELECT count(*) FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND table_name = ANY(%(t)s)) AS grants
    """
    params = {"t": list(TABLES), "r": [f"public.{n}" for n in TABLES]}
    with _owner(dsn) as conn:
        row = conn.execute(query, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def test_running_0621_again_changes_nothing(migrated_dsn: str) -> None:
    before = _shape(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(_migration_text())  # type: ignore[arg-type]
        conn.execute(_migration_text())  # type: ignore[arg-type]
    assert _shape(migrated_dsn) == before
    assert before["policies"] == 3 and before["grants"] > 0
