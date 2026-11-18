"""Schema contract for migration 0625: the staff target language and the overdue opt-in
(disposable database, synthetic data).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Final

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import SCHEMA_BASELINE, _owner_dsn, _run_migrations, requires_docker
from tests.test_bookings_bot_schema import EVENT_DEFAULT
from tests.test_bookings_schema import _owner

pytestmark = requires_docker

EVENT = "inbox.overdue"
INSERT_ONE: Final = (
    "INSERT INTO staff_notification_targets (client_id, channel, address)"
    " VALUES (%s, 'telegram', '1')"
)
COLUMNS_SQL: Final = (
    "SELECT column_name FROM information_schema.columns"
    " WHERE table_name = 'staff_notification_targets'"
)
INSERT_TARGET: Final = (
    "INSERT INTO staff_notification_targets (client_id, channel, address, events, is_enabled)"
    " VALUES (%s, 'email', %s, %s, %s)"
)
READ_TARGETS: Final = (
    "SELECT address, events, language FROM staff_notification_targets WHERE client_id = %s"
)


def _migration_text() -> str:
    paths = dict(migrate.discover_migrations())
    return Path(paths["0625"]).read_text(encoding="utf-8")


def _client(conn: Any) -> str:
    client = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, 'S', 'test')",
        (client, f"s-{client}"),
    )
    return client


def test_migration_stamps_the_expected_version(migrated_dsn: str) -> None:
    assert EXPECTED_SCHEMA_VERSION >= "0625"
    with _owner(migrated_dsn) as conn:
        row = conn.execute("SELECT 1 AS n FROM schema_migrations WHERE version = '0625'").fetchone()
    assert row is not None


def test_language_defaults_to_english_and_refuses_other_values(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        client = _client(conn)
        conn.execute(
            INSERT_ONE,
            (client,),
        )
        [row] = conn.execute(
            "SELECT language FROM staff_notification_targets WHERE client_id = %s", (client,)
        )
        assert row["language"] == "en"
        conn.execute(
            "UPDATE staff_notification_targets SET language = 'ms' WHERE client_id = %s", (client,)
        )
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO staff_notification_targets (client_id, channel, address, language)"
                " VALUES (%s, 'telegram', '2', 'fr')",
                (client,),
            )
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "UPDATE staff_notification_targets SET language = 'fr' WHERE client_id = %s",
                (client,),
            )
        with pytest.raises(errors.NotNullViolation):
            conn.execute(
                "UPDATE staff_notification_targets SET language = NULL WHERE client_id = %s",
                (client,),
            )


def test_the_events_default_is_not_changed(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        client = _client(conn)
        conn.execute(
            INSERT_ONE,
            (client,),
        )
        [row] = conn.execute(
            "SELECT events FROM staff_notification_targets WHERE client_id = %s", (client,)
        )
    assert row["events"] == EVENT_DEFAULT.strip("{}").split(",")
    assert EVENT not in row["events"]


def test_a_database_already_past_0624_gets_the_opt_in_once(
    pg_container: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targets exist before 0625 runs: the enabled one that lacks the event gets it, nobody else."""
    container: Any = pg_container
    owner = _owner_dsn(container)
    with _owner(owner) as conn:
        conn.execute("CREATE DATABASE past_0625 OWNER csa")
    container.copy_into_container(SCHEMA_BASELINE.read_bytes(), "/tmp/past_baseline_0625.sql")
    code, output = container.exec(
        [
            "psql",
            "-U",
            "csa",
            "-d",
            "past_0625",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-f",
            "/tmp/past_baseline_0625.sql",
        ]
    )
    assert code == 0, output.decode(errors="replace")
    past = owner.rsplit("/", 1)[0] + "/past_0625"

    real = migrate.discover_migrations
    monkeypatch.setattr(
        migrate, "discover_migrations", lambda: [m for m in real() if m[0] != "0625"]
    )
    _run_migrations(past)
    with _owner(past) as conn:
        assert "language" not in {r["column_name"] for r in conn.execute(COLUMNS_SQL)}
        client = _client(conn)
        for address, events, enabled in (
            ("lacks@example.test", ["escalation", "dlq"], True),
            ("has@example.test", ["dlq", EVENT], True),
            ("off@example.test", ["escalation"], False),
            ("empty@example.test", [], True),
        ):
            conn.execute(
                INSERT_TARGET,
                (client, address, events, enabled),
            )
    monkeypatch.setattr(migrate, "discover_migrations", real)
    _run_migrations(past)

    def read(conn: Any) -> dict[str, tuple[list[str], str]]:
        return {
            r["address"]: (r["events"], r["language"])
            for r in conn.execute(
                READ_TARGETS,
                (client,),
            )
        }

    with _owner(past) as conn:
        after = read(conn)
        assert after == {
            "lacks@example.test": (["escalation", "dlq", EVENT], "en"),
            "has@example.test": (["dlq", EVENT], "en"),
            "off@example.test": (["escalation"], "en"),
            "empty@example.test": ([EVENT], "en"),
        }
        # A second run of the file changes nothing: no duplicate event, no second column error.
        conn.execute(_migration_text())  # type: ignore[arg-type]
        assert read(conn) == after
        [ledger] = conn.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE version = '0625'"
        )
        assert ledger["n"] == 1


def test_running_0625_again_changes_nothing(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        client = _client(conn)
        conn.execute(
            "INSERT INTO staff_notification_targets (client_id, channel, address, events)"
            " VALUES (%s, 'email', 'a@example.test', '{escalation}')",
            (client,),
        )
        conn.execute(_migration_text())  # type: ignore[arg-type]
        [row] = conn.execute(
            "SELECT events FROM staff_notification_targets WHERE client_id = %s", (client,)
        )
        assert row["events"] == ["escalation", EVENT]
        conn.execute(_migration_text())  # type: ignore[arg-type]
        [again] = conn.execute(
            "SELECT events FROM staff_notification_targets WHERE client_id = %s", (client,)
        )
        assert again["events"] == ["escalation", EVENT]
