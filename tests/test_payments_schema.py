"""Schema contract for migrations 0590 and 0591: invoices grow a reference, a period and a void
trail; payment_settings, payment_methods, payments (append-only) and invoice_reminders; the
`payment_agent` capability row and signup_requests.email (disposable database).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors
from scripts import migrate

from tests.conftest import SCHEMA_BASELINE, _owner_dsn, _run_migrations, requires_docker
from tests.test_bookings_schema import _app, _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

TABLES = ("payment_settings", "payment_methods", "payments", "invoice_reminders")
OTHER_ROLES = ("csa_ro", "csa_identity", "public")
STATUSES = ("draft", "issued", "proof_submitted", "paid", "overdue", "void", "cash_pending")


def _invoice(client_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "id": f"inv_{uuid.uuid4().hex}",
        "client_id": client_id,
        "status": "issued",
        "amount": "59.90",
        "issued_at": "2031-01-01T00:00:00Z",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO invoices ({names}) VALUES ({slots})", tuple(row.values())


def _new_invoice(dsn: str, client_id: str, **extra: Any) -> str:
    """One invoice written as the owner; returns its id."""
    sql, params = _invoice(client_id, **extra)
    with _owner(dsn) as conn:
        conn.execute(sql, params)
    return str(params[0])


def _subscription(dsn: str, client_id: str) -> str:
    subscription_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO subscriptions (id, client_id, plan, status, price, started_at)"
            " VALUES (%s, %s, 'basic', 'active', 10, now())",
            (subscription_id, client_id),
        )
    return subscription_id


def _payment(client_id: str, invoice_id: str | None, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "invoice_id": invoice_id,
        "amount": "59.90",
        "currency": "MYR",
        "received_at": "2031-01-02T00:00:00Z",
        "source": "staff",
        "matched_by": "staff",
        "idempotency_key": f"k-{uuid.uuid4().hex}",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO payments ({names}) VALUES ({slots})", tuple(row.values())


def _reminder(client_id: str, invoice_id: str, step: int = 0, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "invoice_id": invoice_id,
        "step": step,
        "channel": "telegram",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO invoice_reminders ({names}) VALUES ({slots})", tuple(row.values())


def _method(client_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {"client_id": client_id, "kind": "transfer", "label": "Bank", **extra}
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO payment_methods ({names}) VALUES ({slots})", tuple(row.values())


def _seed_every_table(dsn: str, client_id: str) -> None:
    invoice_id = _new_invoice(dsn, client_id)
    with _owner(dsn) as conn:
        conn.execute("INSERT INTO payment_settings (client_id) VALUES (%s)", (client_id,))
        conn.execute(*_method(client_id))
        conn.execute(*_payment(client_id, invoice_id))
        conn.execute(*_reminder(client_id, invoice_id))


@pytest.mark.parametrize("table", TABLES)
def test_new_tables_have_rls_not_forced_and_one_tenant_policy(
    migrated_dsn: str, table: str
) -> None:
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


@pytest.mark.parametrize("table", TABLES)
def test_grants_are_csa_app_only_and_payments_is_append_only(migrated_dsn: str, table: str) -> None:
    wanted = {
        "payments": {"SELECT", "INSERT"},
        "invoice_reminders": {"SELECT", "INSERT", "UPDATE"},
        "payment_settings": {"SELECT", "INSERT", "UPDATE"},
        "payment_methods": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    }[table]
    with _owner(migrated_dsn) as conn:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            [app] = conn.execute(
                "SELECT has_table_privilege('csa_app', %s, %s) AS ok",
                (f"public.{table}", privilege),
            )
            assert app["ok"] is (privilege in wanted), (table, privilege)
            for role in OTHER_ROLES:
                [other] = conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s) AS ok",
                    (role, f"public.{table}", privilege),
                )
                assert other["ok"] is False, (table, role, privilege)
    if table == "payments":
        assert wanted == {"SELECT", "INSERT"}


def test_company_b_sees_and_writes_nothing_of_company_a(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    _seed_every_table(migrated_dsn, a)
    _seed_every_table(migrated_dsn, b)
    invoice_a = _new_invoice(migrated_dsn, a)
    inserts = {
        "payment_settings": ("INSERT INTO payment_settings (client_id) VALUES (%s)", (a,)),
        "payment_methods": _method(a),
        "payments": _payment(a, invoice_a),
        "invoice_reminders": _reminder(a, invoice_a, step=3),
    }
    for table in TABLES:
        with _app(app_dsn, None) as conn:
            assert conn.execute(f"SELECT 1 FROM {table}").fetchall() == []  # type: ignore[arg-type]
        with _app(app_dsn, b) as conn:
            rows = conn.execute(f"SELECT client_id FROM {table}").fetchall()  # type: ignore[arg-type]
            assert rows and {str(r["client_id"]) for r in rows} == {b}, table
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(*inserts[table])
            conn.rollback()
    with _app(app_dsn, b) as conn:
        for sql in (
            "UPDATE payment_settings SET lead_days = 3 WHERE client_id = %s",
            "UPDATE payment_methods SET label = 'x' WHERE client_id = %s",
            "UPDATE invoice_reminders SET status = 'sent' WHERE client_id = %s",
            "DELETE FROM payment_methods WHERE client_id = %s",
        ):
            assert conn.execute(sql, (a,)).rowcount == 0, sql  # type: ignore[arg-type]
        conn.rollback()


def test_invoice_status_check_takes_the_seven_and_refuses_others(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for status in STATUSES:
            conn.execute(*_invoice(c, status=status))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_invoice(c, status="pending"))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_invoice(c, amount="-0.01"))
        conn.execute(*_invoice(c, amount="0"))
        [row] = conn.execute(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'invoices_status_chk'"
        )
        assert row["convalidated"] is True


def test_invoice_currency_is_three_upper_case_letters(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_invoice(c, currency="MYR"))
        conn.execute(*_invoice(c, currency="SGD"))
        for bad in ("RM", "myr", "MYRR", "MY1", ""):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_invoice(c, currency=bad))
        [row] = conn.execute(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'invoices_currency_chk'"
        )
        assert row["convalidated"] is True


def test_reference_check_and_uniqueness(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_invoice(a, reference="K7QX-4MP"))
        for bad in ("K7QX4MP", "K7QX-4M0", "k7qx-4mp", "K7QX-4MPP", "K7QI-4MP", "K7QX-4ML"):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_invoice(a, reference=bad))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_invoice(a, reference="K7QX-4MP"))
        conn.execute(*_invoice(b, reference="K7QX-4MP"))
        # No reference is allowed any number of times.
        conn.execute(*_invoice(a))
        conn.execute(*_invoice(a))


def test_one_invoice_per_subscription_period_void_included(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    sub = _subscription(migrated_dsn, c)
    other = _subscription(migrated_dsn, c)
    start = "2031-02-01T00:00:00Z"
    with _owner(migrated_dsn) as conn:
        conn.execute(*_invoice(c, subscription_id=sub, period_start=start, status="void"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_invoice(c, subscription_id=sub, period_start=start))
        # Another period, or another subscription, is another invoice.
        conn.execute(*_invoice(c, subscription_id=sub, period_start="2031-03-01T00:00:00Z"))
        conn.execute(*_invoice(c, subscription_id=other, period_start=start))
        # The opening invoice has no period yet; a second one for the subscription collides.
        conn.execute(*_invoice(c, subscription_id=sub))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_invoice(c, subscription_id=sub))
        # Invoices without a subscription never collide.
        for _ in range(3):
            conn.execute(*_invoice(c, period_start=start))
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                *_invoice(
                    c,
                    subscription_id=other,
                    period_start="2031-05-01T00:00:00Z",
                    period_end="2031-05-01T00:00:00Z",
                )
            )
        conn.execute(
            *_invoice(
                c,
                subscription_id=other,
                period_start="2031-06-01T00:00:00Z",
                period_end="2031-07-01T00:00:00Z",
            )
        )


def test_invoice_text_and_method_checks(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for column in ("claim_note", "void_reason"):
            conn.execute(*_invoice(c, **{column: "x" * 300}))
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_invoice(c, **{column: "x" * 301}))
        for kind in ("qr", "transfer", "cash"):
            conn.execute(*_invoice(c, method_kind=kind))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_invoice(c, method_kind="card"))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_invoice(c, voided_by=str(uuid.uuid4())))


def test_payment_is_once_per_invoice_and_once_per_key(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    first, second = _new_invoice(migrated_dsn, a), _new_invoice(migrated_dsn, a)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_payment(a, first, idempotency_key="key-1"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_payment(a, first, idempotency_key="key-2"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_payment(a, second, idempotency_key="key-1"))
        # The same key in another company is a different payment.
        conn.execute(*_payment(b, None, idempotency_key="key-1", source="statement"))
        # An unmatched payment has no invoice, and any number of them may exist.
        conn.execute(*_payment(a, None, source="statement"))
        conn.execute(*_payment(a, None, source="statement"))
        for bad in (
            {"amount": "0"},
            {"amount": "-1"},
            {"currency": "myr"},
            {"currency": "MYRR"},
            {"source": "card"},
            {"matched_by": "robot"},
            {"method_kind": "card"},
            {"reference_seen": "x" * 65},
            {"note": "x" * 301},
            {"idempotency_key": ""},
            {"idempotency_key": "x" * 201},
        ):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_payment(a, second, **bad))
        conn.execute(
            *_payment(
                a,
                second,
                source="cash",
                method_kind="cash",
                reference_seen="x" * 64,
                note="x" * 300,
                idempotency_key="x" * 200,
            )
        )
        [row] = conn.execute(
            "SELECT pg_typeof(amount)::text AS t, numeric_precision AS p, numeric_scale AS s"
            " FROM payments, information_schema.columns"
            " WHERE table_name = 'payments' AND column_name = 'amount' LIMIT 1"
        )
        assert (row["p"], row["s"]) == (12, 2)


def test_child_rows_cannot_point_at_another_companys_invoice(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    invoice_a = _new_invoice(migrated_dsn, a)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_payment(b, invoice_a))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_reminder(b, invoice_a))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_payment(a, f"inv_{uuid.uuid4().hex}"))
        conn.execute(*_payment(a, invoice_a))
        conn.execute(*_reminder(a, invoice_a))


def test_invoice_period_belongs_to_the_same_company(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    sub_a = _subscription(migrated_dsn, a)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_invoice(b, subscription_id=sub_a))
        conn.execute(*_invoice(a, subscription_id=sub_a))


def test_settings_checks(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute("INSERT INTO payment_settings (client_id) VALUES (%s)", (c,))
        [row] = conn.execute("SELECT * FROM payment_settings WHERE client_id = %s", (c,))
        assert row["enabled"] is False and row["lead_days"] == 7 and row["version"] == 1
        assert row["reminder_days"] == [-7, 0, 3, 7] and row["suspend_after_days"] is None
        assert row["updated_at"] is not None
        for column, good, bad in (
            ("lead_days", (1, 30), (0, 31)),
            ("suspend_after_days", (1, 90), (0, 91)),
        ):
            for value in good:
                conn.execute(f"UPDATE payment_settings SET {column} = %s", (value,))  # type: ignore[arg-type]
            for value in bad:
                with pytest.raises(errors.CheckViolation):
                    conn.execute(f"UPDATE payment_settings SET {column} = %s", (value,))  # type: ignore[arg-type]
        conn.execute("UPDATE payment_settings SET reminder_days = %s", ([-30, -7, 0, 3, 7, 60],))
        conn.execute("UPDATE payment_settings SET reminder_days = '{}'")
        for days in ([1, 2, 3, 4, 5, 6, 7], [61], [-31], [0, None]):
            with pytest.raises(errors.CheckViolation):
                conn.execute("UPDATE payment_settings SET reminder_days = %s", (days,))
        with pytest.raises(errors.UniqueViolation):
            conn.execute("INSERT INTO payment_settings (client_id) VALUES (%s)", (c,))


def test_method_checks(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_method(c, label="x" * 60, instructions="x" * 500, sort_order=100))
        [row] = conn.execute("SELECT * FROM payment_methods WHERE client_id = %s", (c,))
        assert row["details"] == {} and row["is_active"] is True and row["version"] == 1
        for bad in (
            {"kind": "card"},
            {"label": ""},
            {"label": "x" * 61},
            {"instructions": "x" * 501},
            {"sort_order": -1},
            {"sort_order": 101},
            {"details": "[]"},
        ):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_method(c, **bad))


def test_reminder_step_unique_per_invoice(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    invoice = _new_invoice(migrated_dsn, c)
    other = _new_invoice(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_reminder(c, invoice, step=-7))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_reminder(c, invoice, step=-7, channel="email"))
        conn.execute(*_reminder(c, invoice, step=0))
        conn.execute(*_reminder(c, other, step=-7))
        for bad in (
            {"step": -31},
            {"step": 61},
            {"channel": "sms"},
            {"status": "done"},
            {"address": "ab"},
            {"address": "Upper@Example.test"},
            {"address": "a" * 250 + "@x.my"},
        ):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_reminder(c, other, **{"step": 1, **bad}))
        conn.execute(*_reminder(c, other, step=1, channel="email", address="a@example.test"))
        conn.execute(*_reminder(c, other, step=2, channel="none", status="no_channel"))
        [row] = conn.execute(
            "SELECT status, sent_at FROM invoice_reminders WHERE invoice_id = %s AND step = -7",
            (invoice,),
        )
        assert row == {"status": "queued", "sent_at": None}
        [index] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'invoice_reminders_queued'"
        )
        assert "(client_id, created_at)" in index["indexdef"]
        assert "status = 'queued'" in index["indexdef"]


def test_invoice_indexes_and_keys(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        defs = {
            r["indexname"]: r["indexdef"]
            for r in conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'invoices'"
            )
        }
        assert "NULLS NOT DISTINCT" in defs["invoices_subscription_period_uq"]
        assert "WHERE (subscription_id IS NOT NULL)" in defs["invoices_subscription_period_uq"]
        assert "(client_id, due_at)" in defs["invoices_open"]
        for status in ("issued", "overdue", "proof_submitted", "cash_pending"):
            assert f"'{status}'" in defs["invoices_open"]
        assert "(client_id, contact_id, issued_at DESC)" in defs["invoices_contact"]
        assert "invoices_client_id_key" in defs
        assert "subscriptions_client_id_key" in {
            r["indexname"]
            for r in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'subscriptions'"
            )
        }
        [payments_index] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'payments_one_per_invoice'"
        )
        assert "UNIQUE" in payments_index["indexdef"]
        assert "WHERE (invoice_id IS NOT NULL)" in payments_index["indexdef"]


def test_pause_reason_only_unpaid(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    sub = _subscription(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT pause_reason FROM subscriptions WHERE id = %s", (sub,))
        assert row["pause_reason"] is None
        conn.execute("UPDATE subscriptions SET pause_reason = 'unpaid' WHERE id = %s", (sub,))
        with pytest.raises(errors.CheckViolation):
            conn.execute("UPDATE subscriptions SET pause_reason = 'other' WHERE id = %s", (sub,))


def test_payment_agent_row_mirrors_subscription_agent(migrated_dsn: str) -> None:
    with_row, without_row, off = (_company(migrated_dsn) for _ in range(3))
    with _owner(migrated_dsn) as conn:
        for client_id, enabled in ((with_row, True), (off, False)):
            conn.execute(
                "INSERT INTO capability_registry (client_id, name, display_name, description,"
                " pattern, workflow_id, input_schema, is_enabled, client_enabled)"
                " VALUES (%s, 'subscription_agent', 'Subs', 'd', 'python',"
                " 'python:subscription_agent', '{}', %s, %s)",
                (client_id, enabled, enabled),
            )
    [path] = [p for v, p in migrate.discover_migrations() if v == "0591"]
    with _owner(migrated_dsn) as conn:
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        rows = {
            str(r["client_id"]): r
            for r in conn.execute(
                "SELECT * FROM capability_registry WHERE name = 'payment_agent'"
                " AND client_id = ANY(%s::uuid[])",
                ([with_row, without_row, off],),
            )
        }
    assert rows[with_row]["is_enabled"] is True and rows[with_row]["client_enabled"] is True
    assert rows[off]["is_enabled"] is False and rows[off]["client_enabled"] is False
    assert rows[without_row]["is_enabled"] is False
    assert rows[without_row]["client_enabled"] is False
    for row in rows.values():
        assert row["display_name"] == "Payments" and row["pattern"] == "python"
        assert row["workflow_id"] == "python:payment_agent" and row["input_schema"] == {}
    with _owner(migrated_dsn) as conn:
        [count] = conn.execute(
            "SELECT count(*) AS n FROM capability_registry"
            " WHERE name = 'payment_agent' AND client_id = %s",
            (with_row,),
        )
        assert count["n"] == 1


def test_signup_email_lower_case_only(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    contact = str(uuid.uuid4())
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Synthetic Person')",
            (contact, c),
        )

        def signup(plan: str, email: str | None) -> None:
            conn.execute(
                "INSERT INTO signup_requests (client_id, contact_id, plan_code, full_name, phone,"
                " email) VALUES (%s, %s, %s, 'Synthetic Person', '+60123456789', %s)",
                (c, contact, plan, email),
            )

        signup("none", None)
        signup("good", "person@example.test")
        for bad in ("Person@Example.test", "ab", "a" * 250 + "@x.my"):
            with pytest.raises(errors.CheckViolation):
                signup("bad", bad)


def test_0590_refuses_unknown_invoice_statuses_and_currencies(
    pg_container: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    container: Any = pg_container
    owner = _owner_dsn(container)
    with _owner(owner) as conn:
        conn.execute("CREATE DATABASE at_0581 OWNER csa")
    container.copy_into_container(SCHEMA_BASELINE.read_bytes(), "/tmp/at_0581_baseline.sql")
    code, output = container.exec(
        [
            "psql",
            "-U",
            "csa",
            "-d",
            "at_0581",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-f",
            "/tmp/at_0581_baseline.sql",
        ]
    )
    assert code == 0, output.decode(errors="replace")
    at = owner.rsplit("/", 1)[0] + "/at_0581"

    real = migrate.discover_migrations
    monkeypatch.setattr(
        migrate, "discover_migrations", lambda: [m for m in real() if m[0] < "0590"]
    )
    _run_migrations(at)
    client = str(uuid.uuid4())
    with _owner(at) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, 'S', 'test')",
            (client, f"s-{client}"),
        )
        conn.execute(*_invoice(client, status="issued"))
        conn.execute(*_invoice(client, status="sent"))
    [path] = [p for v, p in real() if v == "0590"]
    with psycopg.connect(at, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    assert "0590: invoices hold statuses outside the new list" in str(raised.value)
    assert "sent" in str(raised.value)
    with _owner(at) as conn:
        conn.execute("UPDATE invoices SET status = 'void' WHERE status = 'sent'")
        conn.execute(*_invoice(client, currency="RM"))
        conn.execute(*_invoice(client, currency="myr"))
    with psycopg.connect(at, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    message = str(raised.value)
    assert "0590: invoices hold currencies that are not three upper-case letters" in message
    assert "'RM'" in message and "'myr'" in message
    with _owner(at) as conn:
        conn.execute("UPDATE invoices SET currency = 'MYR' WHERE currency IN ('RM', 'myr')")
    with psycopg.connect(at, autocommit=True) as conn:
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        # Safe to run twice.
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    monkeypatch.setattr(migrate, "discover_migrations", real)


def test_the_migrations_are_safe_to_run_twice(migrated_dsn: str) -> None:
    paths = [p for v, p in migrate.discover_migrations() if v in ("0590", "0591")]
    assert len(paths) == 2
    query = (
        "SELECT (SELECT count(*) FROM pg_constraint WHERE conrelid IN"
        "   ('public.invoices'::regclass, 'public.subscriptions'::regclass)) AS constraints,"
        " (SELECT count(*) FROM pg_policies WHERE tablename IN"
        "   ('payment_settings', 'payment_methods', 'payments', 'invoice_reminders')) AS policies,"
        " (SELECT count(*) FROM pg_indexes WHERE tablename IN ('invoices', 'payments',"
        "   'invoice_reminders', 'payment_methods')) AS indexes,"
        " (SELECT count(*) FROM capability_registry WHERE name = 'payment_agent') AS agents"
    )
    with _owner(migrated_dsn) as conn:
        # Companies made by earlier tests get their payment_agent row on the first run again; a
        # second run changes nothing.
        for path in paths:
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        before = conn.execute(query).fetchone()  # type: ignore[arg-type]
        for path in paths:
            conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        after = conn.execute(query).fetchone()  # type: ignore[arg-type]
    assert before == after
    assert before is not None and before["policies"] == 4


def test_new_tables_are_not_contact_tables(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND column_name = 'contact_id'"
            " AND table_name = ANY(%s)",
            (list(TABLES),),
        ).fetchall()
    assert rows == []


def test_max_applied_version_is_the_expected_one(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT max(version) AS v FROM schema_migrations")
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert row["v"] == EXPECTED_SCHEMA_VERSION
    assert {"0590", "0591"} <= applied
