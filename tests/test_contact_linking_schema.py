"""Schema contract for migration 0581: public.my_phone_digits(), contacts.phone_digits /
phone_verified_at / merged_into, the verified-phone unique index, contact_links and
contact_link_reviews (disposable database).
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

MIGRATION = "0581"
VERIFIED = "2026-01-01T00:00:00+00:00"
OTHER_ROLES = ("csa_ro", "csa_identity", "public")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
TABLES = ("contact_links", "contact_link_reviews")

DIGIT_CASES: list[tuple[str | None, str | None]] = [
    ("012-345 6789", "60123456789"),
    ("+60 12 345 6789", "60123456789"),
    ("60123456789", "60123456789"),
    ("+44 7911 123456", "447911123456"),
    ("(03) 1234-5678", "60312345678"),
    ("no digits", ""),
    ("", ""),
    (None, None),
]


def _contact(dsn: str, client_id: str, **extra: Any) -> str:
    row: dict[str, Any] = {"id": str(uuid.uuid4()), "client_id": client_id, **extra}
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    with _owner(dsn) as conn:
        conn.execute(f"INSERT INTO contacts ({names}) VALUES ({slots})", tuple(row.values()))  # type: ignore[arg-type]
    return str(row["id"])


def _link(client_id: str, survivor: str, loser: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "survivor_id": survivor,
        "loser_id": loser,
        "reason": "staff",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO contact_links ({names}) VALUES ({slots})", tuple(row.values())


def _review(client_id: str, a: str, b: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "client_id": client_id,
        "contact_a": a,
        "contact_b": b,
        "reason": "several_matches",
        **extra,
    }
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    return f"INSERT INTO contact_link_reviews ({names}) VALUES ({slots})", tuple(row.values())


@pytest.mark.parametrize(("raw", "digits"), DIGIT_CASES)
def test_my_phone_digits_normalises(migrated_dsn: str, raw: str | None, digits: str | None) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT public.my_phone_digits(%s) AS d", (raw,))
    assert row["d"] == digits


def test_my_phone_digits_is_immutable_sql(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT provolatile, l.lanname FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang"
            " WHERE p.oid = 'public.my_phone_digits(text)'::regprocedure"
        )
    assert row == {"provolatile": "i", "lanname": "sql"}


def test_phone_digits_column_follows_phone(migrated_dsn: str) -> None:
    client = _company(migrated_dsn)
    contact = _contact(migrated_dsn, client, phone="012-345 6789")
    with _owner(migrated_dsn) as conn:
        [column] = conn.execute(
            "SELECT is_generated, generation_expression, data_type FROM information_schema.columns"
            " WHERE table_name = 'contacts' AND column_name = 'phone_digits'"
        )
        assert column["is_generated"] == "ALWAYS" and column["data_type"] == "text"
        assert "my_phone_digits" in column["generation_expression"]

        def digits() -> str | None:
            [row] = conn.execute("SELECT phone_digits FROM contacts WHERE id = %s", (contact,))
            return row["phone_digits"]

        assert digits() == "60123456789"
        conn.execute("UPDATE contacts SET phone = '+44 7911 123456' WHERE id = %s", (contact,))
        assert digits() == "447911123456"
        conn.execute("UPDATE contacts SET phone = NULL WHERE id = %s", (contact,))
        assert digits() is None
        # A generated column cannot be written.
        with pytest.raises(errors.GeneratedAlways):
            conn.execute("UPDATE contacts SET phone_digits = '1' WHERE id = %s", (contact,))


def test_contact_columns_and_digit_index(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        columns = {
            r["column_name"]: r
            for r in conn.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'contacts'"
                " AND column_name IN ('phone_verified_at', 'merged_into')"
            )
        }
        assert columns["phone_verified_at"]["data_type"] == "timestamp with time zone"
        assert columns["merged_into"]["data_type"] == "uuid"
        assert all(c["is_nullable"] == "YES" for c in columns.values())
        [fk] = conn.execute(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
            " WHERE conrelid = 'public.contacts'::regclass AND contype = 'f'"
            " AND conkey = ARRAY[(SELECT attnum FROM pg_attribute"
            "   WHERE attrelid = 'public.contacts'::regclass AND attname = 'merged_into')]"
        )
        assert "REFERENCES contacts(id)" in fk["d"]
        [index] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'contacts_client_phone_digits'"
        )
        assert index["indexdef"].split(" USING ", 1)[1] == "btree (client_id, phone_digits)"


def test_a_verified_phone_has_one_live_holder_per_company(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    first = _contact(migrated_dsn, a, phone="012-345 6789", phone_verified_at=VERIFIED)
    second = _contact(migrated_dsn, a, phone="+60 12 345 6789")
    other_company = _contact(migrated_dsn, b, phone="0123456789", phone_verified_at=VERIFIED)
    assert other_company
    with _owner(migrated_dsn) as conn:
        # A second verified holder of the same digits in one company is refused.
        with pytest.raises(errors.UniqueViolation):
            conn.execute("UPDATE contacts SET phone_verified_at = now() WHERE id = %s", (second,))
        # An unverified holder is not counted: two contacts may share an unverified number.
        [count] = conn.execute(
            "SELECT count(*) AS n FROM contacts"
            " WHERE client_id = %s AND phone_digits = '60123456789'",
            (a,),
        )
        assert count["n"] == 2
        # A merged contact is ignored by the index, so it may be marked verified for the number.
        conn.execute("UPDATE contacts SET merged_into = %s WHERE id = %s", (first, second))
        conn.execute("UPDATE contacts SET phone_verified_at = now() WHERE id = %s", (second,))
        # The live verified holder still counts: a third live contact cannot be verified for it.
        third = _contact(migrated_dsn, a, phone="60123456789")
        with pytest.raises(errors.UniqueViolation):
            conn.execute("UPDATE contacts SET phone_verified_at = now() WHERE id = %s", (third,))
        # Contacts without a number (empty digits) may all be 'verified'.
        for _ in range(2):
            _contact(migrated_dsn, a, phone="", phone_verified_at=VERIFIED)
        [index] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'contacts_verified_phone_uq'"
        )
        assert "UNIQUE" in index["indexdef"]
        assert "(client_id, phone_digits)" in index["indexdef"]


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


def test_company_b_cannot_see_or_write_company_a_links(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    a1, a2 = _contact(migrated_dsn, a), _contact(migrated_dsn, a)
    b1, b2 = _contact(migrated_dsn, b), _contact(migrated_dsn, b)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_link(a, a1, a2))
        conn.execute(*_link(b, b1, b2))
        conn.execute(*_review(a, a1, a2))
        conn.execute(*_review(b, b1, b2))
    with _app(app_dsn, b) as conn:
        for table in TABLES:
            rows = conn.execute(f"SELECT client_id FROM {table}").fetchall()
            assert {str(r["client_id"]) for r in rows} == {b}, table
        # The contact trigger runs before the policy's WITH CHECK and cannot see company A's
        # contacts as company B, so either refusal is the right one.
        with pytest.raises((errors.InsufficientPrivilege, errors.RaiseException)):
            conn.execute(*_link(a, a2, a1))
        conn.rollback()
        with pytest.raises((errors.InsufficientPrivilege, errors.RaiseException)):
            conn.execute(*_review(a, a2, a1, reason="telegram_conflict"))
        conn.rollback()
        changed = conn.execute(
            "UPDATE contact_links SET reason = 'staff' WHERE client_id = %s", (a,)
        )
        assert changed.rowcount == 0
        changed = conn.execute(
            "UPDATE contact_link_reviews SET status = 'resolved' WHERE client_id = %s", (a,)
        )
        assert changed.rowcount == 0
        with pytest.raises((errors.InsufficientPrivilege, errors.RaiseException)):
            conn.execute("UPDATE contact_links SET client_id = %s WHERE client_id = %s", (a, b))
        conn.rollback()
        for table in TABLES:
            assert conn.execute(f"DELETE FROM {table} WHERE client_id = %s", (a,)).rowcount == 0
    with _app(app_dsn, None) as conn:
        for table in TABLES:
            assert conn.execute(f"SELECT 1 FROM {table}").fetchall() == []


def test_the_contacts_must_belong_to_the_company(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    a1, a2, b1 = (_contact(migrated_dsn, a), _contact(migrated_dsn, a), _contact(migrated_dsn, b))
    with _owner(migrated_dsn) as conn:
        for make in (_link, _review):
            for first, second in ((a1, b1), (b1, a1)):
                with pytest.raises(psycopg.Error, match="does not belong to client"):
                    conn.execute(*make(a, first, second))
        conn.execute(*_link(a, a1, a2))
        conn.execute(*_review(a, a1, a2))
        with pytest.raises(psycopg.Error, match="does not belong to client"):
            conn.execute("UPDATE contact_links SET loser_id = %s WHERE client_id = %s", (b1, a))
        with pytest.raises(psycopg.Error, match="does not belong to client"):
            conn.execute(
                "UPDATE contact_link_reviews SET contact_b = %s WHERE client_id = %s", (b1, a)
            )


def test_link_and_review_checks(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    one, two = _contact(migrated_dsn, c), _contact(migrated_dsn, c)
    with _owner(migrated_dsn) as conn:
        for reason in ("typed", "robot"):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_link(c, one, two, reason=reason))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_link(c, one, one))
        for reason in ("verified_phone", "staff"):
            conn.execute(*_link(c, one, two, reason=reason))
        [link] = conn.execute(
            "SELECT moved, created_by, created_at IS NOT NULL AS stamped, unlinked_at, unlinked_by"
            " FROM contact_links WHERE reason = 'staff' AND client_id = %s",
            (c,),
        )
        assert link == {
            "moved": {},
            "created_by": None,
            "stamped": True,
            "unlinked_at": None,
            "unlinked_by": None,
        }
        for extra in (
            {"reason": "typed_number"},
            {"status": "pending"},
            {"resolution": "merged"},
        ):
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_review(c, one, two, **extra))
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_review(c, one, one))
        for reason in ("several_matches", "telegram_conflict", "same_channel_identity"):
            conn.execute(
                *_review(c, one, two, reason=reason, status="resolved", resolution="linked")
            )
        [review] = conn.execute(
            "SELECT status, resolution, resolved_by, resolved_at, created_at IS NOT NULL AS stamped"
            " FROM contact_link_reviews WHERE client_id = %s LIMIT 1",
            (c,),
        )
        assert review["status"] == "resolved"
        conn.execute(*_review(c, one, two))
        [open_row] = conn.execute(
            "SELECT status, resolution, resolved_by, resolved_at FROM contact_link_reviews"
            " WHERE status = 'open' AND client_id = %s",
            (c,),
        )
        assert open_row == {
            "status": "open",
            "resolution": None,
            "resolved_by": None,
            "resolved_at": None,
        }


def test_one_open_review_per_unordered_pair(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    one, two, three = (_contact(migrated_dsn, c) for _ in range(3))
    with _owner(migrated_dsn) as conn:
        conn.execute(*_review(c, one, two))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_review(c, one, two, reason="telegram_conflict"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_review(c, two, one))
        # Another pair is another review.
        conn.execute(*_review(c, one, three))
        # Once resolved, the pair can be opened again; resolved rows may repeat.
        conn.execute(
            "UPDATE contact_link_reviews SET status = 'resolved', resolution = 'dismissed'"
            " WHERE client_id = %s AND contact_a = %s AND contact_b = %s",
            (c, one, two),
        )
        conn.execute(*_review(c, two, one))
        conn.execute(*_review(c, one, two, status="resolved", resolution="dismissed"))
        # The same pair in another company is a different review (cannot be: contacts differ), but
        # the index is scoped by company.
        [index] = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'contact_link_reviews_one_open'"
        )
        assert "client_id" in index["indexdef"] and "status = 'open'" in index["indexdef"]


def test_the_migration_is_safe_to_run_twice(migrated_dsn: str) -> None:
    [path] = [p for v, p in migrate.discover_migrations() if v == MIGRATION]
    query = (
        "SELECT pg_get_functiondef('public.my_phone_digits(text)'::regprocedure) AS fn,"
        " (SELECT count(*) FROM pg_policies"
        "   WHERE tablename IN ('contact_links', 'contact_link_reviews')) AS policies,"
        " (SELECT count(*) FROM pg_trigger"
        "   WHERE tgname IN ('trg_contact_link_tenant',"
        "     'trg_contact_link_review_tenant')) AS triggers,"
        " (SELECT count(*) FROM pg_indexes WHERE indexname IN ('contacts_verified_phone_uq',"
        "   'contacts_client_phone_digits', 'contact_link_reviews_one_open')) AS indexes,"
        " (SELECT count(*) FROM information_schema.columns WHERE table_name = 'contacts'"
        "   AND column_name IN ('phone_digits', 'phone_verified_at', 'merged_into')) AS columns"
    )
    with _owner(migrated_dsn) as conn:
        before = conn.execute(query).fetchone()  # type: ignore[arg-type]
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        after = conn.execute(query).fetchone()  # type: ignore[arg-type]
    assert before == after
    assert before is not None and before["columns"] == 3 and before["indexes"] == 3
    assert before["policies"] == 2 and before["triggers"] == 2


def test_max_applied_version_is_the_expected_one(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT max(version) AS v FROM schema_migrations")
    assert row["v"] == EXPECTED_SCHEMA_VERSION == MIGRATION
