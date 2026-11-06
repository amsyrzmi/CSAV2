"""Schema contract for migrations 0512 and 0531: the columns the bot and reminders need, the
settings and reminders tables, the booking notification event and the assistant constraints."""

from __future__ import annotations

import hashlib
import re
import typing
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from csa.workspace.contracts import ProposalKind, TabKey
from psycopg import errors, sql
from psycopg.rows import dict_row
from scripts import migrate

from tests.conftest import (
    REPO_ROOT,
    SCHEMA_BASELINE,
    _owner_dsn,
    _run_migrations,
    requires_docker,
)
from tests.test_bookings_schema import DictConnection, _app, _company, _owner

pytestmark = requires_docker

MIGRATIONS = REPO_ROOT / "db" / "migrations"
BOT_TABLES = ("booking_settings", "booking_reminders", "booking_turn_actions")
PROPOSALS_CHECK = "console_proposals_kind_check"
TURNS_CHECK = "console_workspace_turns_action_check"
PAGE_CHECK = "console_workspace_turns_page_key_check"
EVENT_DEFAULT = "{escalation,approval_needed,order_submitted,dlq,circuit_opened,booking_pending}"


def _reminder(client_id: str, booking_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "starts_at": "2031-03-01T01:00Z",
        "audience": "customer",
        "channel": "email",
        "address": "someone@example.test",
        "status": "queued",
        **extra,
    }
    return (
        "INSERT INTO booking_reminders (client_id, booking_id, starts_at, audience, channel,"
        " address, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            client_id,
            booking_id,
            row["starts_at"],
            row["audience"],
            row["channel"],
            row["address"],
            row["status"],
        ),
    )


def _turn_action(client_id: str, booking_id: str, turn_key: str, action: str = "book") -> Any:
    return (
        "INSERT INTO booking_turn_actions (client_id, turn_key, booking_id, action)"
        " VALUES (%s, %s, %s, %s)",
        (client_id, turn_key, booking_id, action),
    )


def test_each_bot_table_is_tenant_isolated(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for c in (a, b):
            conn.execute("INSERT INTO booking_settings (client_id) VALUES (%s)", (c.client_id,))
            conn.execute(*_reminder(c.client_id, c.booking_id))
            conn.execute(*_turn_action(c.client_id, c.booking_id, "turn-1"))
        for table in BOT_TABLES:
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
        for table in BOT_TABLES:
            rows = conn.execute(f"SELECT client_id FROM {table}").fetchall()
            assert {str(r["client_id"]) for r in rows} == {b.client_id}, table
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO booking_settings (client_id) VALUES (%s)", (uuid.uuid4(),))
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_reminder(a.client_id, a.booking_id))
        conn.rollback()
        # The app role can write its own company's rows and never delete a reminder.
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_turn_action(a.client_id, a.booking_id, "turn-3"))
        conn.rollback()
        conn.execute(*_turn_action(b.client_id, b.booking_id, "turn-2", "cancel"))
        conn.execute("UPDATE booking_reminders SET status = 'sending'")
        conn.execute("UPDATE booking_settings SET reminder_minutes = 60")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("DELETE FROM booking_reminders")
        conn.rollback()
        # What a turn key did is written once and never changed or removed.
        for statement in (
            "UPDATE booking_turn_actions SET action = 'cancel'",
            "DELETE FROM booking_turn_actions",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(statement)
            conn.rollback()
    with _app(app_dsn, None) as conn:
        for table in BOT_TABLES:
            assert conn.execute(f"SELECT 1 FROM {table}").fetchall() == [], table


def test_a_turn_key_does_one_thing_per_company_and_repeats_across_companies(
    migrated_dsn: str,
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_turn_action(a.client_id, a.booking_id, "turn-1"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_turn_action(a.client_id, a.booking_id, "turn-1", "cancel"))
        conn.execute(*_turn_action(b.client_id, b.booking_id, "turn-1"))
        conn.execute(*_turn_action(a.client_id, a.booking_id, "turn-2", "change"))


def test_a_turn_action_names_a_booking_of_the_same_company_and_a_known_action(
    migrated_dsn: str,
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_turn_action(a.client_id, b.booking_id, "turn-1"))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_turn_action(a.client_id, a.booking_id, "turn-1", "reschedule"))


def test_bookings_keep_no_turn_key_of_their_own(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'bookings' AND column_name = 'turn_key'"
        ).fetchall()
    assert rows == []


def test_a_customer_given_address_is_bounded_and_unique_to_nobody(migrated_dsn: str) -> None:
    """The address a customer typed into a chat is a claim: it is kept apart from the contact's
    own `email`, which identity and the walk-in match trust, and it identifies no one."""
    a = _company(migrated_dsn)
    insert = "INSERT INTO contacts (client_id, customer_given_email) VALUES (%s, %s) RETURNING id"
    with _owner(migrated_dsn) as conn:
        [column] = conn.execute(
            "SELECT is_nullable, data_type FROM information_schema.columns"
            " WHERE table_name = 'contacts' AND column_name = 'customer_given_email'"
        )
        assert column == {"is_nullable": "YES", "data_type": "text"}
        conn.execute(insert, (a.client_id, "same@example.test"))
        conn.execute(insert, (a.client_id, "same@example.test"))  # no uniqueness
        conn.execute(insert, (a.client_id, None))
        conn.execute(insert, (a.client_id, "a" * 241 + "@example.test"))  # 254 is the limit
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert, (a.client_id, "a" * 242 + "@example.test"))


def test_a_replacement_points_at_a_booking_of_the_same_company(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    insert = (
        "INSERT INTO bookings (client_id, service, starts_at, ends_at, replaces_booking_id,"
        " conversation_id, expires_at) VALUES (%s, 'Consultation', '2031-05-01T01:00Z',"
        " '2031-05-01T01:30Z', %s, %s, '2031-04-30T01:00Z')"
    )
    with _owner(migrated_dsn) as conn:
        conn.execute(insert, (a.client_id, a.booking_id, uuid.uuid4()))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(insert, (a.client_id, b.booking_id, uuid.uuid4()))
        [old] = conn.execute(
            "SELECT replaces_booking_id, conversation_id, expires_at FROM bookings WHERE id = %s",
            (a.booking_id,),
        )
        assert old == {
            "replaces_booking_id": None,
            "conversation_id": None,
            "expires_at": None,
        }


def test_a_reminder_is_one_row_per_booking_time_audience_and_address(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_reminder(c.client_id, c.booking_id))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_reminder(c.client_id, c.booking_id))
        # A new start time, another audience or another address is another reminder.
        conn.execute(*_reminder(c.client_id, c.booking_id, starts_at="2031-03-02T01:00Z"))
        conn.execute(*_reminder(c.client_id, c.booking_id, audience="staff"))
        conn.execute(*_reminder(c.client_id, c.booking_id, address="other@example.test"))
        # Telegram rows have no address, and two of them still collide.
        conn.execute(*_reminder(c.client_id, c.booking_id, channel="telegram", address=None))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_reminder(c.client_id, c.booking_id, channel="telegram", address=None))


@pytest.mark.parametrize(
    "bad",
    [{"audience": "everyone"}, {"channel": "whatsapp"}, {"status": "done"}],
)
def test_a_reminder_refuses_an_unknown_audience_channel_or_status(
    migrated_dsn: str, bad: dict[str, str]
) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn, pytest.raises(errors.CheckViolation):
        conn.execute(*_reminder(c.client_id, c.booking_id, **bad))


def test_reminder_statuses_and_a_reminder_for_another_companys_booking(
    migrated_dsn: str,
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for n, status in enumerate(("queued", "sending", "sent", "failed", "dropped")):
            conn.execute(
                *_reminder(a.client_id, a.booking_id, status=status, address=f"x{n}@example.test")
            )
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_reminder(a.client_id, b.booking_id))


def test_settings_start_at_the_defaults_and_refuse_values_out_of_range(
    migrated_dsn: str,
) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute("INSERT INTO booking_settings (client_id) VALUES (%s)", (c.client_id,))
        [row] = conn.execute(
            "SELECT reminder_minutes, pending_hours, customer_limit, version FROM booking_settings"
            " WHERE client_id = %s",
            (c.client_id,),
        )
        assert row == {
            "reminder_minutes": 1440,
            "pending_hours": 24,
            "customer_limit": 5,
            "version": 1,
        }
        for column, bad in (
            ("reminder_minutes", 14),
            ("reminder_minutes", 10081),
            ("pending_hours", 0),
            ("pending_hours", 169),
            ("customer_limit", 0),
            ("customer_limit", 51),
        ):
            with pytest.raises(errors.CheckViolation):
                conn.execute(
                    sql.SQL("UPDATE booking_settings SET {} = %s WHERE client_id = %s").format(
                        sql.Identifier(column)
                    ),
                    (bad, c.client_id),
                )


def test_booking_pending_is_in_the_default_events(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO staff_notification_targets (client_id, channel, address)"
            " VALUES (%s, 'email', 'staff@example.test')",
            (c.client_id,),
        )
        [row] = conn.execute(
            "SELECT events FROM staff_notification_targets WHERE client_id = %s", (c.client_id,)
        )
    assert row["events"] == EVENT_DEFAULT.strip("{}").split(",")


# --- the assistant constraints (0531) ---------------------------------------------------------


def _allowed(conn: DictConnection, constraint: str) -> set[str]:
    """The quoted values a CHECK allows, read from the constraint as the database stores it."""
    [row] = conn.execute(
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conname = %s",
        (constraint,),
    )
    return set(re.findall(r"'([^']+)'", row["definition"]))


def _proposal(kind: str) -> tuple[Any, ...]:
    return (
        "INSERT INTO console_proposals (client_id, user_id, login_session_id, kind, payload,"
        " payload_sha256, card, origin, status, expires_at) VALUES (%s, %s, %s, %s, '{}', %s,"
        " '{}', 'assistant', 'requested', now() + interval '10 minutes')",
        (uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), kind, hashlib.sha256(b"x").hexdigest()),
    )


def _turn(action: str) -> tuple[Any, ...]:
    return (
        "INSERT INTO console_workspace_turns (client_id, thread_id, owner_user_id, session_id,"
        " request_key, fingerprint, body, action, page_key, result)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'x', %s, 'workspace_home', '{}')",
        (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            hashlib.sha256(b"x").hexdigest(),
            action,
        ),
    )


def _accepts(dsn: str, statement: tuple[Any, ...]) -> bool:
    """Whether the CHECK constraints take the row. Foreign keys are switched off for the
    transaction (owner-only fixture) so only the checks decide, and nothing is kept."""
    with psycopg.connect(dsn) as conn:
        conn.execute("SET LOCAL session_replication_role = replica")
        try:
            conn.execute(*statement)  # type: ignore[call-overload]
        except errors.CheckViolation:
            return False
        finally:
            conn.rollback()
    return True


def test_every_proposal_kind_is_accepted_by_the_database(migrated_dsn: str) -> None:
    """A later migration that hard-codes the kinds again would drop one, and fail here."""
    kinds = set(typing.get_args(ProposalKind))
    assert kinds
    with _owner(migrated_dsn) as conn:
        assert kinds <= _allowed(conn, PROPOSALS_CHECK)
    for kind in sorted(kinds | {"booking_action"}):
        assert _accepts(migrated_dsn, _proposal(kind)), kind
    assert not _accepts(migrated_dsn, _proposal("not_a_kind"))


def test_the_booking_action_is_allowed_in_both_constraints(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        assert "booking_action" in _allowed(conn, PROPOSALS_CHECK)
        assert "booking_action" in _allowed(conn, TURNS_CHECK)
    assert _accepts(migrated_dsn, _turn("booking_action"))
    assert _accepts(migrated_dsn, _turn("hr_person_add"))
    assert not _accepts(migrated_dsn, _turn("not_an_action"))


def test_every_tab_the_assistant_can_be_asked_on_is_a_stored_page(migrated_dsn: str) -> None:
    """A turn stores the tab it was asked on, and 0460's list never named subscriptions or
    bookings, so a turn asked there could not be stored. Every TabKey must be accepted."""
    with _owner(migrated_dsn) as conn:
        allowed = _allowed(conn, PAGE_CHECK)
    assert set(typing.get_args(TabKey)) <= allowed
    assert {"workspace_home", "business_orders", "business_order"} <= allowed


def _definitions(dsn: str) -> dict[str, str]:
    with _owner(dsn) as conn:
        return {
            r["conname"]: r["definition"]
            for r in conn.execute(
                "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint"
                " WHERE conname IN (%s, %s, %s)",
                (PROPOSALS_CHECK, TURNS_CHECK, PAGE_CHECK),
            )
        }


def test_running_0531_again_changes_nothing(migrated_dsn: str) -> None:
    before = _definitions(migrated_dsn)
    [path] = [p for v, p in migrate.discover_migrations() if v == "0531"]
    with _owner(migrated_dsn) as conn:
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    assert _definitions(migrated_dsn) == before
    assert len(before) == 3


def test_a_database_already_past_0530_gets_the_columns_and_the_constraints(
    pg_container: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging is past 0530 when 0512 and 0531 arrive: 0512 runs late, 0531 follows it."""
    container: Any = pg_container
    owner = _owner_dsn(container)
    with _owner(owner) as conn:
        conn.execute("CREATE DATABASE past_0530 OWNER csa")
    container.copy_into_container(SCHEMA_BASELINE.read_bytes(), "/tmp/past_baseline.sql")
    code, output = container.exec(
        ["psql", "-U", "csa", "-d", "past_0530", "-v", "ON_ERROR_STOP=1", "-q"]
        + ["-f", "/tmp/past_baseline.sql"]
    )
    assert code == 0, output.decode(errors="replace")
    past = owner.rsplit("/", 1)[0] + "/past_0530"

    real = migrate.discover_migrations
    monkeypatch.setattr(
        migrate, "discover_migrations", lambda: [m for m in real() if m[0] not in ("0512", "0531")]
    )
    _run_migrations(past)
    with _owner(past) as conn:
        assert "booking_action" not in _allowed(conn, PROPOSALS_CHECK)
        client = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, 'S', 'test')",
            (client, f"s-{client}"),
        )
        for address, events in (
            ("a@example.test", ["escalation", "approval_needed"]),
            ("b@example.test", ["dlq"]),
        ):
            conn.execute(
                "INSERT INTO staff_notification_targets (client_id, channel, address, events)"
                " VALUES (%s, 'email', %s, %s)",
                (client, address, events),
            )
    monkeypatch.setattr(migrate, "discover_migrations", real)
    _run_migrations(past)

    with _owner(past) as conn:
        events = {
            r["address"]: r["events"]
            for r in conn.execute(
                "SELECT address, events FROM staff_notification_targets WHERE client_id = %s",
                (client,),
            )
        }
        assert events["a@example.test"] == ["escalation", "approval_needed", "booking_pending"]
        assert events["b@example.test"] == ["dlq"]
        assert {"booking_action", "hr_person"} <= _allowed(conn, PROPOSALS_CHECK)
        assert {"booking_action", "hr_person_open"} <= _allowed(conn, TURNS_CHECK)
        assert {"bookings", "subscriptions", "hr"} <= _allowed(conn, PAGE_CHECK)
        [ledger] = conn.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE version IN ('0512', '0531')"
        )
        assert ledger["n"] == 2


def test_0531_keeps_a_kind_it_does_not_know() -> None:
    """The rebuild reads the lists from the database, so it must not name the others itself."""
    text = (MIGRATIONS / "0531_booking_assistant_action.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
    assert "hr_person" not in code and "email_send" not in code


# Migrations that already redefine one of the three constraints (found by searching db/migrations).
# Any other migration that names either must be numbered above 0531 or read the current list.
KNOWN_REBUILDS = {"0460", "0480", "0484", "0521", "0531"}


def migrations_that_rebuild_the_constraints(
    migrations: list[tuple[str, Path]] | None = None,
) -> list[str]:
    """Each migration below or at 0531 that names a constraint in code and reads no live list."""
    offenders = []
    for version, path in migrations or migrate.discover_migrations():
        text = path.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
        names_one = any(name in code for name in (PROPOSALS_CHECK, TURNS_CHECK, PAGE_CHECK))
        if not names_one or version in KNOWN_REBUILDS or version > "0531":
            continue
        if "pg_get_constraintdef" not in code:
            offenders.append(path.name)
    return offenders


def test_no_migration_below_0531_rebuilds_the_assistant_constraints_from_a_fixed_list() -> None:
    """On a fresh database 0531 runs last and repairs a fixed list, so a database test cannot see
    this. On one already past 0531 the unrecorded migration would run after it and drop
    booking_action, with /readyz still green. So the migrations themselves are searched."""
    assert migrations_that_rebuild_the_constraints() == []
    # The allow-list is the real set: each of these does name a constraint in code.
    named = {
        v
        for v, p in migrate.discover_migrations()
        if any(
            name in p.read_text(encoding="utf-8")
            for name in (PROPOSALS_CHECK, TURNS_CHECK, PAGE_CHECK)
        )
    }
    assert named == KNOWN_REBUILDS


def test_the_scan_also_watches_the_page_key_constraint(tmp_path: Path) -> None:
    fixed = tmp_path / "0523_synthetic_pages.sql"
    fixed.write_text(
        "ALTER TABLE public.console_workspace_turns DROP CONSTRAINT"
        " console_workspace_turns_page_key_check;\n"
        "ALTER TABLE public.console_workspace_turns ADD CONSTRAINT"
        " console_workspace_turns_page_key_check CHECK (page_key IN ('overview'));\n",
        encoding="utf-8",
    )
    assert migrations_that_rebuild_the_constraints([("0523", fixed)]) == [fixed.name]


def test_the_scan_catches_a_fixed_list_below_0531(tmp_path: Path) -> None:
    fixed = tmp_path / "0523_synthetic_fixed_list.sql"
    fixed.write_text(
        "ALTER TABLE public.console_proposals DROP CONSTRAINT console_proposals_kind_check;\n"
        "ALTER TABLE public.console_proposals ADD CONSTRAINT console_proposals_kind_check\n"
        "    CHECK (kind IN ('email_export'));\n",
        encoding="utf-8",
    )
    reads = tmp_path / "0524_synthetic_reads_list.sql"
    reads.write_text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'console_proposals_kind_check';\n",
        encoding="utf-8",
    )
    later = tmp_path / "0532_synthetic_later.sql"
    later.write_text(fixed.read_text(encoding="utf-8"), encoding="utf-8")
    found = migrations_that_rebuild_the_constraints(
        [("0523", fixed), ("0524", reads), ("0532", later)]
    )
    assert found == [fixed.name]


def _rolled_back(dsn: str, setup: str, *, expect: str | None = None) -> set[str]:
    """Change the constraints inside a transaction, run 0531, report PROPOSALS_CHECK's values
    (or, with `expect`, check the refusal's message), and roll everything back."""
    [path] = [p for v, p in migrate.discover_migrations() if v == "0531"]
    with DictConnection.connect(dsn, row_factory=dict_row) as conn:
        try:
            conn.execute(setup)  # type: ignore[arg-type]
            if expect is not None:
                with pytest.raises(errors.RaiseException, match=expect):
                    conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                return set()
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            return _allowed(conn, PROPOSALS_CHECK)
        finally:
            conn.rollback()


def test_0531_keeps_a_kind_it_has_never_heard_of(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        before = _allowed(conn, PROPOSALS_CHECK)
    setup = (
        "ALTER TABLE public.console_proposals DROP CONSTRAINT console_proposals_kind_check;"
        "ALTER TABLE public.console_proposals ADD CONSTRAINT console_proposals_kind_check"
        " CHECK (kind IN ('email_export', 'hr_person', 'synthetic_other_track'))"
    )
    after = _rolled_back(migrated_dsn, setup)
    assert after == {"email_export", "hr_person", "synthetic_other_track", "booking_action"}
    with _owner(migrated_dsn) as conn:
        assert _allowed(conn, PROPOSALS_CHECK) == before, "the test left its change behind"


def test_0531_stops_when_a_constraint_is_missing(migrated_dsn: str) -> None:
    _rolled_back(
        migrated_dsn,
        "ALTER TABLE public.console_workspace_turns"
        " DROP CONSTRAINT console_workspace_turns_action_check",
        expect="console_workspace_turns_action_check is missing",
    )


def test_0531_stops_when_a_constraint_is_not_a_plain_list(migrated_dsn: str) -> None:
    _rolled_back(
        migrated_dsn,
        "ALTER TABLE public.console_proposals DROP CONSTRAINT console_proposals_kind_check;"
        "ALTER TABLE public.console_proposals ADD CONSTRAINT console_proposals_kind_check"
        " CHECK (kind <> 'nothing')",
        expect="does not read as a plain list",
    )
    with _owner(migrated_dsn) as conn:
        assert "booking_action" in _allowed(conn, PROPOSALS_CHECK)


def test_an_email_reminder_has_an_address_and_a_telegram_one_has_none(
    migrated_dsn: str,
) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_reminder(c.client_id, c.booking_id, channel="email", address=None))
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                *_reminder(c.client_id, c.booking_id, channel="telegram", address="x@example.test")
            )
        conn.execute(*_reminder(c.client_id, c.booking_id, channel="telegram", address=None))


def test_a_lapse_notice_is_its_own_audience_and_collides_with_itself(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_reminder(c.client_id, c.booking_id, audience="lapse"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_reminder(c.client_id, c.booking_id, audience="lapse"))
        # The same address on the booking's reminder is a different row.
        conn.execute(*_reminder(c.client_id, c.booking_id, audience="customer"))


def test_a_reminder_address_is_lower_case_so_two_spellings_cannot_both_be_queued(
    migrated_dsn: str,
) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_reminder(c.client_id, c.booking_id, address="A@example.test"))
        conn.execute(*_reminder(c.client_id, c.booking_id, address="a@example.test"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_reminder(c.client_id, c.booking_id, address="a@example.test"))


def test_the_queued_email_sweep_has_a_partial_index(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        row = conn.execute(
            "SELECT indexdef FROM pg_indexes"
            " WHERE tablename = 'booking_reminders' AND indexname = 'booking_reminders_queued'"
        ).fetchone()
    assert row is not None
    definition = row["indexdef"]
    assert "created_at, id" in definition
    assert "channel = 'email'" in definition and "status = 'queued'" in definition


def test_the_pending_change_and_customer_reads_have_partial_indexes(migrated_dsn: str) -> None:
    """The calendar's per-row look for a waiting change, the change lock on every cancel, decline
    and move, and the customer's own reads would otherwise scan the company's bookings."""
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'bookings'"
            " AND indexname IN ('bookings_pending_changes', 'bookings_contact')"
        ).fetchall()
    found = {r["indexname"]: r["indexdef"] for r in rows}
    assert set(found) == {"bookings_pending_changes", "bookings_contact"}
    changes, contact = found["bookings_pending_changes"], found["bookings_contact"]
    assert "(client_id, replaces_booking_id)" in changes and "status = 'pending'" in changes
    assert "(client_id, contact_id, starts_at)" in contact and "contact_id IS NOT NULL" in contact
