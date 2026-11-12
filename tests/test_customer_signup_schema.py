"""Schema contract for migration 0580: signup_requests, plans.signup_mode / is_signup_open,
subscriptions.activated_at, subscription_settings and the `pending` subscription status (disposable
database).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_schema import _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

MIGRATION = "0580"
OTHER_ROLES = ("csa_ro", "csa_identity", "public")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
TABLES = ("signup_requests", "subscription_settings")


def _contact(dsn: str, client_id: str) -> str:
    contact_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Synthetic Person')",
            (contact_id, client_id),
        )
    return contact_id


def _signup(client_id: str, contact_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "contact_id": contact_id,
        "plan_code": "basic",
        "full_name": "Synthetic Person",
        "phone": "+60123456789",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO signup_requests ({names}) VALUES ({slots})", tuple(row.values())


def _plan(client_id: str, code: str = "basic") -> tuple[Any, ...]:
    return (
        "INSERT INTO plans (client_id, code, name, price, billing_interval)"
        " VALUES (%s, %s, 'Plan', 10, 'month')",
        (client_id, code),
    )


def _subscription(client_id: str, status: str) -> tuple[Any, ...]:
    return (
        "INSERT INTO subscriptions (client_id, plan, status, price, started_at)"
        " VALUES (%s, 'basic', %s, 10, now())",
        (client_id, status),
    )


@pytest.mark.parametrize("table", TABLES)
def test_tables_are_row_secured_and_not_forced(migrated_dsn: str, table: str) -> None:
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
        for privilege in PRIVILEGES:
            [app] = conn.execute(
                "SELECT has_table_privilege('csa_app', %s, %s) AS ok",
                (f"public.{table}", privilege),
            )
            assert app["ok"] is True, privilege
            for role in OTHER_ROLES:
                [other] = conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s) AS ok",
                    (role, f"public.{table}", privilege),
                )
                assert other["ok"] is False, (role, privilege)


def test_company_b_cannot_see_or_write_company_a_signups(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    contact_a, contact_b = _contact(migrated_dsn, a), _contact(migrated_dsn, b)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_signup(a, contact_a))
        conn.execute(*_signup(b, contact_b))
    with _app(app_dsn, b) as conn:
        rows = conn.execute("SELECT client_id FROM signup_requests").fetchall()
        assert {str(r["client_id"]) for r in rows} == {b}
        # The contact trigger runs before the policy's WITH CHECK and cannot see company A's
        # contact as company B, so either refusal is the right one.
        with pytest.raises((errors.InsufficientPrivilege, errors.RaiseException)):
            conn.execute(*_signup(a, contact_a, plan_code="other"))
        conn.rollback()
        changed = conn.execute(
            "UPDATE signup_requests SET plan_code = 'x' WHERE client_id = %s", (a,)
        )
        assert changed.rowcount == 0
        with pytest.raises((errors.InsufficientPrivilege, errors.RaiseException)):
            conn.execute("UPDATE signup_requests SET client_id = %s WHERE client_id = %s", (a, b))
        conn.rollback()
        assert conn.execute("DELETE FROM signup_requests WHERE client_id = %s", (a,)).rowcount == 0
        conn.execute(*_signup(b, contact_b, plan_code="second"))
        assert conn.execute("DELETE FROM signup_requests").rowcount == 2
        conn.rollback()
    with _app(app_dsn, None) as conn:
        assert conn.execute("SELECT 1 FROM signup_requests").fetchall() == []
    with _owner(migrated_dsn) as conn:
        [left] = conn.execute(
            "SELECT count(*) AS n FROM signup_requests WHERE client_id = %s", (a,)
        )
        assert left["n"] == 1


def test_settings_are_isolated_per_company(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute("INSERT INTO subscription_settings (client_id) VALUES (%s)", (a,))
        conn.execute("INSERT INTO subscription_settings (client_id) VALUES (%s)", (b,))
    with _app(app_dsn, b) as conn:
        rows = conn.execute("SELECT client_id FROM subscription_settings").fetchall()
        assert {str(r["client_id"]) for r in rows} == {b}
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO subscription_settings (client_id) VALUES (%s)", (uuid.uuid4(),)
            )
        conn.rollback()
        changed = conn.execute(
            "UPDATE subscription_settings SET pending_expiry_days = 3 WHERE client_id = %s", (a,)
        )
        assert changed.rowcount == 0


def test_the_contact_must_belong_to_the_company(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    contact_b = _contact(migrated_dsn, b)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(psycopg.Error, match="does not belong to client"):
            conn.execute(*_signup(a, contact_b))
        # Moving an existing request onto another company's contact is refused as well.
        own = _contact(migrated_dsn, a)
        conn.execute(*_signup(a, own))
        with pytest.raises(psycopg.Error, match="does not belong to client"):
            conn.execute(
                "UPDATE signup_requests SET contact_id = %s WHERE client_id = %s", (contact_b, a)
            )


def test_signup_checks_refuse_bad_values(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    contact = _contact(migrated_dsn, c)
    bad: list[dict[str, Any]] = [
        {"full_name": ""},
        {"full_name": "x" * 121},
        {"status": "open"},
        {"decided_via": "robot"},
        {"decline_reason": "x" * 301},
        {"phone": None},
        {"plan_code": None},
    ]
    with _owner(migrated_dsn) as conn:
        for extra in bad:
            with pytest.raises((errors.CheckViolation, errors.NotNullViolation)):
                conn.execute(*_signup(c, contact, **extra))
        conn.execute(
            *_signup(
                c,
                contact,
                plan_code="edges",
                full_name="x" * 120,
                decline_reason="x" * 300,
                status="declined",
                decided_via="staff",
            )
        )
        conn.execute(*_signup(c, contact, plan_code="auto", status="approved", decided_via="auto"))
        [row] = conn.execute(
            "SELECT status, decided_via, decided_by, subscription_id, decided_at,"
            " created_at IS NOT NULL AS stamped"
            " FROM signup_requests WHERE client_id = %s AND plan_code = 'edges'",
            (c,),
        )
        assert row["stamped"] is True
        assert (
            row["decided_by"] is None
            and row["subscription_id"] is None
            and row["decided_at"] is None
        )
        [default] = conn.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'signup_requests'"
            " AND column_name = 'status'"
        )
        assert default["column_default"].startswith("'pending'")


def test_one_pending_request_per_contact_and_plan(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    contact, other = _contact(migrated_dsn, c), _contact(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_signup(c, contact))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_signup(c, contact))
        # Another plan, or another contact, is a different request.
        conn.execute(*_signup(c, contact, plan_code="pro"))
        conn.execute(*_signup(c, other))
        # Once decided, a new pending request is allowed, and decided ones may repeat.
        conn.execute(
            "UPDATE signup_requests SET status = 'declined', decided_via = 'staff'"
            " WHERE contact_id = %s AND plan_code = 'basic'",
            (contact,),
        )
        conn.execute(*_signup(c, contact))
        conn.execute(*_signup(c, contact, status="declined"))
        conn.execute(*_signup(c, contact, status="expired"))
        [index] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'signup_requests_one_pending'"
        )
        assert "(client_id, contact_id, plan_code)" in index["indexdef"]
        assert "status = 'pending'" in index["indexdef"]
        [listing] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'signup_requests_client_status'"
        )
        assert "(client_id, status, created_at)" in listing["indexdef"]


def test_plan_columns_and_activation_stamp(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_plan(c))
        [plan] = conn.execute(
            "SELECT signup_mode, is_signup_open FROM plans WHERE client_id = %s", (c,)
        )
        assert plan == {"signup_mode": "staff", "is_signup_open": True}
        conn.execute(
            "UPDATE plans SET signup_mode = 'auto', is_signup_open = false WHERE client_id = %s",
            (c,),
        )
        with pytest.raises(errors.CheckViolation):
            conn.execute("UPDATE plans SET signup_mode = 'manual' WHERE client_id = %s", (c,))
        with pytest.raises(errors.NotNullViolation):
            conn.execute("UPDATE plans SET is_signup_open = NULL WHERE client_id = %s", (c,))
        conn.execute(*_subscription(c, "active"))
        [sub] = conn.execute("SELECT activated_at FROM subscriptions WHERE client_id = %s", (c,))
        assert sub["activated_at"] is None


def test_pending_is_a_subscription_status_and_trialing_is_not(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for status in ("active", "paused", "cancelled", "pending"):
            conn.execute(*_subscription(c, status))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_subscription(c, "trialing"))
        [constraint] = conn.execute(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
            " WHERE conname = 'subscriptions_status_chk'"
        )
    for status in ("active", "paused", "cancelled", "pending"):
        assert f"'{status}'" in constraint["d"]


def test_settings_default_and_range(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute("INSERT INTO subscription_settings (client_id) VALUES (%s)", (c,))
        [row] = conn.execute(
            "SELECT pending_expiry_days, version FROM subscription_settings WHERE client_id = %s",
            (c,),
        )
        assert row == {"pending_expiry_days": 14, "version": 1}
        for days in (0, 90):
            conn.execute("UPDATE subscription_settings SET pending_expiry_days = %s", (days,))
        for days in (-1, 91):
            with pytest.raises(errors.CheckViolation):
                conn.execute("UPDATE subscription_settings SET pending_expiry_days = %s", (days,))
        with pytest.raises(errors.UniqueViolation):
            conn.execute("INSERT INTO subscription_settings (client_id) VALUES (%s)", (c,))


def test_the_migration_is_safe_to_run_twice(migrated_dsn: str) -> None:
    [path] = [p for v, p in migrate.discover_migrations() if v == MIGRATION]
    query = (
        "SELECT (SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        "         WHERE conname = 'subscriptions_status_chk') AS status_check,"
        " (SELECT count(*) FROM pg_policies"
        "   WHERE tablename IN ('signup_requests', 'subscription_settings')) AS policies,"
        " (SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_signup_contact_tenant') AS triggers,"
        " (SELECT count(*) FROM pg_indexes WHERE indexname LIKE 'signup_requests_%%') AS indexes,"
        " (SELECT count(*) FROM information_schema.columns"
        "   WHERE (table_name, column_name) IN (('plans', 'signup_mode'),"
        "     ('plans', 'is_signup_open'), ('subscriptions', 'activated_at'))) AS columns"
    )
    with _owner(migrated_dsn) as conn:
        before = conn.execute(query).fetchone()  # type: ignore[arg-type]
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        after = conn.execute(query).fetchone()  # type: ignore[arg-type]
    assert before == after
    assert before is not None and before["columns"] == 3 and before["policies"] == 2
    assert before["triggers"] == 1 and "'pending'" in before["status_check"]


def test_max_applied_version_is_the_expected_one(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT max(version) AS v FROM schema_migrations")
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert row["v"] == EXPECTED_SCHEMA_VERSION
    assert MIGRATION in applied
