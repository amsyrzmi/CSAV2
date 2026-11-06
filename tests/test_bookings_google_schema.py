"""Schema contract for migration 0513: a person's Google Calendar connection, the PKCE states of
connecting it, and the ledger of what Google holds for each booking.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import SCHEMA_BASELINE, _owner_dsn, _run_migrations, requires_docker
from tests.test_bookings_schema import Company, _app, _company, _owner

pytestmark = requires_docker

CONNECTIONS = "booking_calendar_connections"
STATES = "booking_calendar_oauth_states"
EVENTS = "booking_calendar_events"
TABLES = (CONNECTIONS, STATES, EVENTS)
VERIFIER = "v" * 43


def _member(dsn: str, c: Company) -> None:
    """Make the company's user a member and link the booking person to that account."""
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO console_memberships (user_id, client_id, role)"
            " VALUES (%s, %s, 'tenant_staff') ON CONFLICT DO NOTHING",
            (c.user_id, c.client_id),
        )
        conn.execute(
            "UPDATE booking_people SET user_id = %s WHERE client_id = %s AND id = %s",
            (c.user_id, c.client_id, c.person_id),
        )


def _connection(c: Company, status: str = "connected") -> tuple[Any, ...]:
    return (
        "INSERT INTO booking_calendar_connections (client_id, id, user_id, person_id, status)"
        " VALUES (%s, %s, %s, %s, %s)",
        (c.client_id, str(uuid.uuid4()), c.user_id, c.person_id, status),
    )


def _event(c: Company, status: str = "pending", booking_id: str | None = None) -> tuple[Any, ...]:
    return (
        "INSERT INTO booking_calendar_events (client_id, booking_id, person_id, status)"
        " VALUES (%s, %s, %s, %s)",
        (c.client_id, booking_id or c.booking_id, c.person_id, status),
    )


def _state(
    c: Company, connection_id: str, state_hash: str | None = None, verifier: str = VERIFIER
) -> Any:
    return (
        "INSERT INTO booking_calendar_oauth_states (state_hash, client_id, user_id,"
        " connection_id, code_verifier, expires_at) VALUES (%s, %s, %s, %s, %s,"
        " now() + interval '10 minutes')",
        (state_hash or uuid.uuid4().hex * 2, c.client_id, c.user_id, connection_id, verifier),
    )


def _connection_id(dsn: str, c: Company) -> str:
    with _owner(dsn) as conn:
        [row] = conn.execute(
            "SELECT id FROM booking_calendar_connections WHERE client_id = %s", (c.client_id,)
        ).fetchall()
    return str(row["id"])


def _with_rows(dsn: str) -> Company:
    c = _company(dsn)
    _member(dsn, c)
    with _owner(dsn) as conn:
        conn.execute(*_connection(c))
    with _owner(dsn) as conn:
        conn.execute(*_event(c))
        conn.execute(*_state(c, _connection_id(dsn, c)))
    return c


def test_each_google_table_is_tenant_isolated(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _with_rows(migrated_dsn), _with_rows(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for table in TABLES:
            [flags] = conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                (f"public.{table}",),
            )
            assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}, table
            [policy] = conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE tablename = %s"
                " AND policyname = 'tenant_isolation'",
                (table,),
            )
            assert policy["n"] == 1, table
    with _app(app_dsn, b.client_id) as conn:
        for table in TABLES:
            rows = conn.execute(f"SELECT client_id FROM {table}").fetchall()
            assert {str(r["client_id"]) for r in rows} == {b.client_id}, table
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_event(a))
        conn.rollback()
    with _app(app_dsn, None) as conn:
        for table in TABLES:
            assert conn.execute(f"SELECT 1 FROM {table}").fetchall() == [], table


def test_the_app_role_holds_the_grants_and_nobody_else_reads_a_token(migrated_dsn: str) -> None:
    wanted = {
        CONNECTIONS: {"SELECT", "INSERT", "UPDATE", "DELETE"},
        EVENTS: {"SELECT", "INSERT", "UPDATE", "DELETE"},
        STATES: {"SELECT", "INSERT", "DELETE"},
    }
    with _owner(migrated_dsn) as conn:
        for table, held in wanted.items():
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                [row] = conn.execute(
                    "SELECT has_table_privilege('csa_app', %s, %s) AS ok", (table, privilege)
                )
                assert row["ok"] == (privilege in held), (table, privilege)
            for role in ("csa_ro", "csa_identity"):
                [row] = conn.execute(
                    "SELECT has_table_privilege(%s, %s, 'SELECT') AS ok", (role, table)
                )
                assert row["ok"] is False, (role, table)


def test_a_connection_and_a_ledger_row_hold_only_known_statuses(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    _member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_connection(c, "revoked"))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_event(c, "sent"))
        for status in ("pending", "synced", "failed", "deleted", "dropped"):
            conn.execute("DELETE FROM booking_calendar_events WHERE client_id = %s", (c.client_id,))
            conn.execute(*_event(c, status))
        for status in ("pending", "connected", "needs_reconnect"):
            conn.execute(
                "DELETE FROM booking_calendar_connections WHERE client_id = %s", (c.client_id,)
            )
            conn.execute(*_connection(c, status))


def test_a_ledger_row_defaults_to_pending_with_no_attempts(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO booking_calendar_events (client_id, booking_id, person_id)"
            " VALUES (%s, %s, %s)",
            (c.client_id, c.booking_id, c.person_id),
        )
        [row] = conn.execute(
            "SELECT status, attempts, synced_version, google_event_id, last_error,"
            " failed_version, file_free"
            " FROM booking_calendar_events WHERE client_id = %s",
            (c.client_id,),
        )
        assert row == {
            "status": "pending",
            "attempts": 0,
            "synced_version": None,
            "google_event_id": None,
            "last_error": None,
            "file_free": False,
            "failed_version": None,
        }
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "UPDATE booking_calendar_events SET attempts = -1 WHERE client_id = %s",
                (c.client_id,),
            )


def test_a_user_and_a_person_each_have_one_connection(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    _member(migrated_dsn, c)
    other = str(uuid.uuid4())
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Other')",
            (other, f"{other}@example.test"),
        )
        conn.execute(
            "INSERT INTO console_memberships (user_id, client_id, role)"
            " VALUES (%s, %s, 'tenant_staff')",
            (other, c.client_id),
        )
        conn.execute(*_connection(c))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_connection(c))  # the same user again
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.UniqueViolation):  # the same person, another user
            conn.execute(
                "INSERT INTO booking_calendar_connections (client_id, user_id, person_id, status)"
                " VALUES (%s, %s, %s, 'connected')",
                (c.client_id, other, c.person_id),
            )


def test_a_connection_names_a_member_and_a_person_of_its_own_company(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    _member(migrated_dsn, a)
    insert = (
        "INSERT INTO booking_calendar_connections (client_id, user_id, person_id, status)"
        " VALUES (%s, %s, %s, 'connected')"
    )
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):  # a person of another company
            conn.execute(insert, (a.client_id, a.user_id, b.person_id))
        with pytest.raises(errors.ForeignKeyViolation):  # a user who is no member of the company
            conn.execute(insert, (b.client_id, b.user_id, b.person_id))


def test_a_ledger_row_is_one_per_booking_and_person_and_stays_in_its_company(
    migrated_dsn: str,
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_event(a))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_event(a))
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):  # another company's booking
            conn.execute(*_event(a, booking_id=b.booking_id))


def test_a_state_holds_a_hash_and_a_verifier_and_goes_with_its_connection(
    migrated_dsn: str,
) -> None:
    c = _company(migrated_dsn)
    _member(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_connection(c, "pending"))
    connection = _connection_id(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_state(c, connection, state_hash="not-a-hash"))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_state(c, connection, verifier="short"))
        conn.execute(*_state(c, connection))
        conn.execute(
            "DELETE FROM booking_calendar_connections WHERE client_id = %s", (c.client_id,)
        )
        assert (
            conn.execute(f"SELECT 1 FROM {STATES} WHERE client_id = %s", (c.client_id,)).fetchall()
            == []
        )


def test_0513_arrives_on_a_database_already_at_0531(
    pg_container: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging is at 0531 when 0513 arrives: it runs late, and the highest version stays 0531."""
    container: Any = pg_container
    owner = _owner_dsn(container)
    with _owner(owner) as conn:
        conn.execute("CREATE DATABASE at_0531 OWNER csa")
    container.copy_into_container(SCHEMA_BASELINE.read_bytes(), "/tmp/at_0531_baseline.sql")
    code, output = container.exec(
        [
            "psql",
            "-U",
            "csa",
            "-d",
            "at_0531",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-f",
            "/tmp/at_0531_baseline.sql",
        ]
    )
    assert code == 0, output.decode(errors="replace")
    at = owner.rsplit("/", 1)[0] + "/at_0531"

    real = migrate.discover_migrations
    monkeypatch.setattr(
        migrate, "discover_migrations", lambda: [m for m in real() if m[0] != "0513"]
    )
    _run_migrations(at)
    with _owner(at) as conn:
        assert conn.execute(
            "SELECT to_regclass('public.booking_calendar_events') AS t"
        ).fetchone() == {"t": None}
    monkeypatch.setattr(migrate, "discover_migrations", real)
    _run_migrations(at)
    with _owner(at) as conn:
        [row] = conn.execute("SELECT max(version) AS top FROM schema_migrations")
        assert row["top"] == EXPECTED_SCHEMA_VERSION == "0531"
        [ledger] = conn.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE version = '0513'"
        )
        assert ledger["n"] == 1
        for table in TABLES:
            [found] = conn.execute("SELECT to_regclass(%s) AS t", (f"public.{table}",)).fetchall()
            assert found["t"]
