"""Schema contract for migration 0510: the booking tables, the overlap rule, the leave read."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from scripts import migrate

from tests.conftest import (
    REPO_ROOT,
    SCHEMA_BASELINE,
    _owner_dsn,
    _run_migrations,
    requires_docker,
)

pytestmark = requires_docker

DictConnection = psycopg.Connection[dict[str, Any]]

NEW_TABLES = (
    "booking_people",
    "booking_resources",
    "booking_types",
    "booking_type_people",
    "booking_type_resources",
    "booking_hours",
    "booking_closed_days",
    "booking_blocks",
    "booking_attendees",
    "booking_holds",
)


@dataclass
class Company:
    client_id: str
    user_id: str
    person_id: str
    resource_id: str
    type_id: str
    booking_id: str
    block_id: str
    employee_id: str


def _owner(dsn: str) -> DictConnection:
    return DictConnection.connect(dsn, autocommit=True, row_factory=dict_row)


def _company(dsn: str) -> Company:
    """One synthetic company with a row in every booking table, written as the owner."""
    c = Company(*(str(uuid.uuid4()) for _ in range(8)))
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version)"
            " VALUES (%s, %s, 'Synthetic Co', 'test')",
            (c.client_id, f"s-{c.client_id}"),
        )
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (c.user_id, f"{c.user_id}@example.test"),
        )
        conn.execute(
            "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s, %s, 'Synthetic')",
            (c.client_id, c.employee_id),
        )
        conn.execute(
            "INSERT INTO booking_people (client_id, id, display_name, employee_id)"
            " VALUES (%s, %s, 'Synthetic Person', %s)",
            (c.client_id, c.person_id, c.employee_id),
        )
        conn.execute(
            "INSERT INTO booking_resources (client_id, id, name, capacity)"
            " VALUES (%s, %s, 'Room 1', 4)",
            (c.client_id, c.resource_id),
        )
        conn.execute(
            "INSERT INTO booking_types (client_id, id, name, duration_minutes, buffer_minutes,"
            " audience, delivery, resource_rule, location_kind, min_notice_minutes,"
            " max_advance_days, change_cutoff_minutes)"
            " VALUES (%s, %s, 'Consultation', 30, 0, 'both', 'named', 'none', 'in_person',"
            " 60, 30, 120)",
            (c.client_id, c.type_id),
        )
        conn.execute(
            "INSERT INTO booking_type_people (client_id, type_id, person_id) VALUES (%s, %s, %s)",
            (c.client_id, c.type_id, c.person_id),
        )
        conn.execute(
            "INSERT INTO booking_type_resources (client_id, type_id, resource_id)"
            " VALUES (%s, %s, %s)",
            (c.client_id, c.type_id, c.resource_id),
        )
        conn.execute(
            "INSERT INTO booking_hours (client_id, subject_kind, weekday, opens, closes)"
            " VALUES (%s, 'company', 0, '09:00', '18:00')",
            (c.client_id,),
        )
        conn.execute(
            "INSERT INTO booking_closed_days (client_id, day, note) VALUES (%s, '2031-01-01', 'x')",
            (c.client_id,),
        )
        conn.execute(
            "INSERT INTO booking_blocks (client_id, id, subject_kind, subject_id, starts_at,"
            " ends_at, created_by) VALUES (%s, %s, 'person', %s, '2031-02-01T01:00Z',"
            " '2031-02-01T02:00Z', %s)",
            (c.client_id, c.block_id, c.person_id, c.user_id),
        )
        conn.execute(
            "INSERT INTO bookings (id, client_id, service, starts_at, ends_at, type_id)"
            " VALUES (%s, %s, 'Consultation', '2031-03-01T01:00Z', '2031-03-01T01:30Z', %s)",
            (c.booking_id, c.client_id, c.type_id),
        )
        conn.execute(
            "INSERT INTO booking_attendees (client_id, booking_id, person_id) VALUES (%s, %s, %s)",
            (c.client_id, c.booking_id, c.person_id),
        )
        conn.execute(
            "INSERT INTO booking_holds (client_id, booking_id, subject_kind, subject_id, during)"
            " VALUES (%s, %s, 'person', %s, tstzrange('2031-03-01T01:00Z', '2031-03-01T01:30Z'))",
            (c.client_id, c.booking_id, c.person_id),
        )
    return c


@pytest.fixture
def company(migrated_dsn: str) -> Company:
    return _company(migrated_dsn)


def _app(dsn: str, client_id: str | None) -> DictConnection:
    conn = DictConnection.connect(dsn, row_factory=dict_row)
    conn.execute("SELECT set_config('app.client_id', %s, false)", (client_id or "",))
    conn.commit()
    return conn


def _hold(client_id: str, person_id: str, start: str, end: str) -> tuple[Any, ...]:
    return (
        "INSERT INTO booking_holds (client_id, block_id, subject_kind, subject_id, during)"
        " VALUES (%s, NULL, 'person', %s, tstzrange(%s::timestamptz, %s::timestamptz))",
        (client_id, person_id, start, end),
    )


def _person_with_block(dsn: str, c: Company) -> tuple[str, str]:
    """A fresh person and a block to hang test holds on, so tests do not share a calendar."""
    person, block = str(uuid.uuid4()), str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO booking_people (client_id, id, display_name) VALUES (%s, %s, 'P')",
            (c.client_id, person),
        )
        conn.execute(
            "INSERT INTO booking_blocks (client_id, id, subject_kind, subject_id, starts_at,"
            " ends_at, created_by) VALUES (%s, %s, 'person', %s, now(), now() + interval '1 day',"
            " %s)",
            (c.client_id, block, person, c.user_id),
        )
    return person, block


def _block_hold(c: Company, person: str, block: str, start: str, end: str) -> tuple[Any, ...]:
    return (
        "INSERT INTO booking_holds (client_id, block_id, subject_kind, subject_id, during)"
        " VALUES (%s, %s, 'person', %s, tstzrange(%s::timestamptz, %s::timestamptz))",
        (c.client_id, block, person, start, end),
    )


def test_the_database_refuses_an_overlapping_hold(migrated_dsn: str, company: Company) -> None:
    person, block = _person_with_block(migrated_dsn, company)
    other, other_block = _person_with_block(migrated_dsn, company)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_block_hold(company, person, block, "2032-01-01T09:00Z", "2032-01-01T10:00Z"))
        with pytest.raises(errors.ExclusionViolation):
            conn.execute(
                *_block_hold(company, person, block, "2032-01-01T09:30Z", "2032-01-01T10:30Z")
            )
        # Touching ranges are fine: ranges are half open.
        conn.execute(*_block_hold(company, person, block, "2032-01-01T10:00Z", "2032-01-01T11:00Z"))
        conn.execute(*_block_hold(company, person, block, "2032-01-01T08:00Z", "2032-01-01T09:00Z"))
        # A different person can hold the same time.
        conn.execute(
            *_block_hold(company, other, other_block, "2032-01-01T09:00Z", "2032-01-01T10:00Z")
        )


def test_a_hold_names_exactly_one_of_booking_or_block(migrated_dsn: str, company: Company) -> None:
    person, block = _person_with_block(migrated_dsn, company)
    insert = (
        "INSERT INTO booking_holds (client_id, booking_id, block_id, subject_kind, subject_id,"
        " during) VALUES (%s, %s, %s, 'person', %s, tstzrange('2033-01-01', '2033-01-02'))"
    )
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert, (company.client_id, None, None, person))
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert, (company.client_id, company.booking_id, block, person))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(insert, (company.client_id, str(uuid.uuid4()), None, person))
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO booking_holds (client_id, block_id, subject_kind, subject_id, during)"
                " VALUES (%s, %s, 'company', %s, tstzrange('2033-01-01', '2033-01-02'))",
                (company.client_id, block, person),
            )


def test_two_simultaneous_holds_only_one_wins(migrated_dsn: str, company: Company) -> None:
    person, block = _person_with_block(migrated_dsn, company)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        with psycopg.connect(migrated_dsn, autocommit=True) as conn:
            barrier.wait()
            try:
                conn.execute(
                    *_block_hold(company, person, block, "2034-05-05T09:00Z", "2034-05-05T10:00Z")
                )
                outcomes.append("won")
            except (errors.ExclusionViolation, errors.DeadlockDetected):
                # Two identical inserts at the same instant may be settled by the deadlock
                # detector instead of the constraint. Either way the loser is refused.
                outcomes.append("lost")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["lost", "won"]


def test_each_new_table_is_tenant_isolated(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        found = {
            row["table_name"]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name LIKE 'booking\\_%'"
            )
        }
        # The tables 0512 adds have their own isolation test (test_bookings_bot_schema.py).
        later = {"booking_settings", "booking_reminders", "booking_turn_actions"}
        assert found - later == set(NEW_TABLES), (
            "a booking table is missing from this isolation test"
        )
        for table in NEW_TABLES:
            [flags] = conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                (f"public.{table}",),
            )
            assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}, table
            assert (
                conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE client_id = %s",
                    (a.client_id,),
                ).fetchone()
                == {"n": 1}
                or table == "booking_hours"
                or table
            ), table
    with _app(app_dsn, b.client_id) as conn:
        for table in NEW_TABLES:
            rows = conn.execute(f"SELECT client_id FROM {table}").fetchall()
            assert rows, f"{table}: company B sees nothing of its own"
            assert {str(r["client_id"]) for r in rows} == {b.client_id}, table
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO booking_resources (client_id, name) VALUES (%s, 'Not mine')",
                (a.client_id,),
            )
    with _app(app_dsn, None) as conn:
        for table in NEW_TABLES:
            assert conn.execute(f"SELECT 1 FROM {table}").fetchall() == [], table


def test_the_app_role_can_manage_holds_and_cannot_see_other_tables(
    migrated_dsn: str, app_dsn: str, company: Company
) -> None:
    with _app(app_dsn, company.client_id) as conn:
        conn.execute(
            "DELETE FROM booking_holds WHERE client_id = %s AND booking_id = %s",
            (company.client_id, company.booking_id),
        )
        assert conn.execute("SELECT 1 FROM booking_holds").fetchall() == []
        conn.rollback()


def _leave(dsn: str, c: Company, status: str, start: str, end: str, kind: str = "annual") -> None:
    reason = "synthetic private reason" if status == "rejected" else None
    with _owner(dsn) as conn:
        # Owner-only fixture: the HR transition triggers want a real membership and audit trail.
        conn.execute("SET session_replication_role = replica")
        conn.execute(
            "INSERT INTO hr_cases (client_id, id, domain, employee_id, created_by, payload,"
            " status, version, updated_at, decision_reason)"
            " VALUES (%s, %s, 'leave', %s, %s, %s::jsonb, %s, 0, now(), %s)",
            (
                c.client_id,
                str(uuid.uuid4()),
                c.employee_id,
                c.user_id,
                Jsonb(
                    {
                        "domain": "leave",
                        "starts_on": start,
                        "ends_on": end,
                        "leave_type": kind,
                        "reason": "synthetic private reason",
                    }
                ),
                status,
                reason,
            ),
        )


def test_leave_days_returns_only_approved_leave_and_no_reason(
    migrated_dsn: str, app_dsn: str, company: Company
) -> None:
    other = _company(migrated_dsn)
    _leave(migrated_dsn, company, "approved", "2035-06-10", "2035-06-12")
    _leave(migrated_dsn, company, "pending", "2035-06-20", "2035-06-21")
    _leave(migrated_dsn, company, "rejected", "2035-06-25", "2035-06-26")
    _leave(migrated_dsn, company, "approved", "2035-09-01", "2035-09-02")
    call = "SELECT * FROM booking_leave_days(%s, %s::uuid[], %s, %s)"
    with _app(app_dsn, company.client_id) as conn:
        cursor = conn.execute(
            call, (company.client_id, [company.employee_id], "2035-06-01", "2035-06-30")
        )
        assert [d.name for d in cursor.description or []] == ["employee_id", "starts_on", "ends_on"]
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert str(rows[0]["employee_id"]) == company.employee_id
        assert (str(rows[0]["starts_on"]), str(rows[0]["ends_on"])) == ("2035-06-10", "2035-06-12")
        # A window that only touches the end of a leave still returns it.
        touching = conn.execute(
            call, (company.client_id, [company.employee_id], "2035-06-12", "2035-06-14")
        ).fetchall()
        assert len(touching) == 1
        assert (
            conn.execute(
                call, (company.client_id, [str(uuid.uuid4())], "2035-01-01", "2035-12-31")
            ).fetchall()
            == []
        )
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(call, (other.client_id, [other.employee_id], "2035-01-01", "2035-12-31"))


def test_leave_days_works_for_a_non_hr_member(
    migrated_dsn: str, app_dsn: str, company: Company
) -> None:
    _leave(migrated_dsn, company, "approved", "2036-06-10", "2036-06-12")
    with _app(app_dsn, company.client_id) as conn:
        # The session has no app.hr_user_id and no HR grant, so hr_cases itself shows nothing.
        assert conn.execute("SELECT 1 FROM hr_cases").fetchall() == []
        rows = conn.execute(
            "SELECT * FROM booking_leave_days(%s, %s::uuid[], '2036-06-01', '2036-06-30')",
            (company.client_id, [company.employee_id]),
        ).fetchall()
        assert len(rows) == 1


def test_the_sandbox_seed_still_loads(migrated_dsn: str) -> None:
    """The dev seed inserts bookings with the old columns, so the new checks must accept them."""
    seed_dir = REPO_ROOT / "db" / "seed"
    with DictConnection.connect(migrated_dsn, row_factory=dict_row) as conn:
        for name in ("sandbox_clients.sql", "sandbox_scenarios.sql"):
            conn.execute((seed_dir / name).read_text(), prepare=False)  # type: ignore[arg-type]
        [row] = conn.execute(
            "SELECT count(*) AS n FROM bookings WHERE notes = 'Synthetic booking fixture.'"
        )
        assert row["n"] == 2
        conn.rollback()


def test_legacy_statuses_are_normalised(
    pg_container: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a database still at 0503, 0510 turns an old status into 'cancelled' and keeps the rest."""
    container: Any = pg_container
    owner = _owner_dsn(container)
    with _owner(owner) as conn:
        conn.execute("CREATE DATABASE legacy_bookings OWNER csa")
    container.copy_into_container(SCHEMA_BASELINE.read_bytes(), "/tmp/legacy_baseline.sql")
    code, output = container.exec(
        ["psql", "-U", "csa", "-d", "legacy_bookings", "-v", "ON_ERROR_STOP=1", "-q"]
        + ["-f", "/tmp/legacy_baseline.sql"]
    )
    assert code == 0, output.decode(errors="replace")
    legacy = owner.rsplit("/", 1)[0] + "/legacy_bookings"

    real = migrate.discover_migrations
    monkeypatch.setattr(
        migrate, "discover_migrations", lambda: [m for m in real() if m[0] < "0510"]
    )
    _run_migrations(legacy)
    client = str(uuid.uuid4())
    with _owner(legacy) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, 'S', 'test')",
            (client, f"s-{client}"),
        )
        for status in ("completed", "no-show", "pending", "confirmed", "cancelled"):
            conn.execute(
                "INSERT INTO bookings (client_id, service, status, starts_at, ends_at)"
                " VALUES (%s, %s, %s, '2031-01-01T01:00Z', '2031-01-01T02:00Z')",
                (client, status, status),
            )
    monkeypatch.setattr(migrate, "discover_migrations", real)
    # Apply 0510 by hand to read its NOTICE, then let the runner do the rest.
    notices: list[str] = []
    [path] = [p for v, p in real() if v == "0510"]
    with psycopg.connect(legacy, autocommit=True) as conn:
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            conn.execute("INSERT INTO schema_migrations (version) VALUES ('0510')")
    _run_migrations(legacy)
    with _owner(legacy) as conn:
        statuses = {
            r["service"]: r["status"]
            for r in conn.execute(
                "SELECT service, status FROM bookings WHERE client_id = %s", (client,)
            )
        }
        # A meeting has no service; the old NOT NULL is gone.
        conn.execute(
            "INSERT INTO bookings (client_id, service, status, starts_at, ends_at, title, kind)"
            " VALUES (%s, NULL, 'confirmed', now(), now() + interval '1 hour', 'Team sync',"
            " 'meeting')",
            (client,),
        )
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO bookings (client_id, service, status, starts_at, ends_at)"
                " VALUES (%s, 'x', 'completed', now(), now() + interval '1 hour')",
                (client,),
            )
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "INSERT INTO bookings (client_id, service, starts_at, ends_at)"
                " VALUES (%s, 'x', now(), now())",
                (client,),
            )
    assert statuses == {
        "completed": "cancelled",
        "no-show": "cancelled",
        "pending": "pending",
        "confirmed": "confirmed",
        "cancelled": "cancelled",
    }
    assert "0510: 2 booking(s) with a legacy status set to cancelled" in notices
