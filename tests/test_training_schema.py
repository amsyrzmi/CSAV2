"""Schema contract for migrations 0540 and 0541: the assistant's training_library page key, the
training_items table and the training_is_hr_owner() check (disposable database).
"""

from __future__ import annotations

import hashlib
import typing
import uuid
from typing import Any

import psycopg
import pytest
from csa.workspace.contracts import TabKey
from psycopg import errors
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_bot_schema import PAGE_CHECK, _accepts, _allowed, _definitions
from tests.test_bookings_schema import _app, _owner

pytestmark = requires_docker

TABLE = "training_items"
OTHER_ROLES = ("csa_ro", "csa_identity", "csa_hr_maintenance", "public")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _company(dsn: str) -> str:
    client_id = str(uuid.uuid4())
    with _owner(dsn) as conn:
        insert = "INSERT INTO clients (id, slug, name, platform_version)"
        conn.execute(
            insert + " VALUES (%s, %s, 'Synthetic Co', 'test')", (client_id, f"t-{client_id}")
        )
    return client_id


def _item(client_id: str, **extra: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "kind": "pdf",
        "title": "Induction slides",
        "description": "",
        "category": "",
        "view_mode": "slides",
        "storage_name": f"{uuid.uuid4()}.pdf",
        "original_name": "induction.pdf",
        "media_type": "application/pdf",
        "byte_size": 1024,
        "sha256": hashlib.sha256(b"synthetic").hexdigest(),
        "page_count": 3,
        "request_key": str(uuid.uuid4()),
        "fingerprint": hashlib.sha256(b"request").hexdigest(),
        **extra,
    }
    columns = ["client_id", *row]
    names, slots = ", ".join(columns), ", ".join(["%s"] * len(columns))
    return f"INSERT INTO training_items ({names}) VALUES ({slots})", (client_id, *row.values())


def test_table_is_row_secured_and_not_forced(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [flags] = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{TABLE}",),
        )
        assert flags == {"relrowsecurity": True, "relforcerowsecurity": False}
        policies = conn.execute(
            "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = %s", (TABLE,)
        ).fetchall()
        assert [p["policyname"] for p in policies] == ["tenant_isolation"]
        assert "app.client_id" in policies[0]["qual"]
        assert "app.client_id" in policies[0]["with_check"]
        for privilege in PRIVILEGES:
            [app] = conn.execute(
                "SELECT has_table_privilege('csa_app', %s, %s) AS ok",
                (f"public.{TABLE}", privilege),
            )
            assert app["ok"] is True, privilege
            for role in OTHER_ROLES:
                [other] = conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s) AS ok",
                    (role, f"public.{TABLE}", privilege),
                )
                assert other["ok"] is False, (role, privilege)


def test_company_b_cannot_see_or_write_company_a_rows(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_item(a))
        conn.execute(*_item(b))
    with _app(app_dsn, b) as conn:
        rows = conn.execute(f"SELECT client_id FROM {TABLE}").fetchall()
        assert {str(r["client_id"]) for r in rows} == {b}
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_item(a))
        conn.rollback()
        changed = conn.execute(f"UPDATE {TABLE} SET title = 'x' WHERE client_id = %s", (a,))
        assert changed.rowcount == 0
        # Its own row cannot be moved to another company (the policy's WITH CHECK).
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(f"UPDATE {TABLE} SET client_id = %s WHERE client_id = %s", (a, b))
        conn.rollback()
        assert conn.execute(f"DELETE FROM {TABLE} WHERE client_id = %s", (a,)).rowcount == 0
        # Its own rows it can write, change and remove.
        conn.execute(*_item(b))
        assert conn.execute(f"UPDATE {TABLE} SET category = 'Safety'").rowcount == 2
        assert conn.execute(f"DELETE FROM {TABLE}").rowcount == 2
        conn.rollback()
    with _app(app_dsn, None) as conn:
        assert conn.execute(f"SELECT 1 FROM {TABLE}").fetchall() == []
    with _owner(migrated_dsn) as conn:
        [left] = conn.execute(f"SELECT count(*) AS n FROM {TABLE} WHERE client_id = %s", (a,))
        assert left["n"] == 1


def test_checks_refuse_bad_values(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    bad: list[dict[str, Any]] = [
        {"title": ""},
        {"title": "x" * 121},
        {"view_mode": "video"},
        {"byte_size": 0},
        {"kind": "video"},
        {"page_count": 0},
        {"description": "x" * 1001},
        {"category": "x" * 61},
    ]
    with _owner(migrated_dsn) as conn:
        for extra in bad:
            with pytest.raises(errors.CheckViolation):
                conn.execute(*_item(c, **extra))
        # The edges are allowed.
        edges = {"title": "x" * 120, "description": "x" * 1000, "category": "x" * 60}
        conn.execute(*_item(c, view_mode="document", **edges))
        [row] = conn.execute(
            f"SELECT summary, summary_truncated, created_at IS NOT NULL AS stamped,"
            f" id IS NOT NULL AS has_id FROM {TABLE} WHERE client_id = %s",
            (c,),
        )
        assert row == {"summary": None, "summary_truncated": False, "stamped": True, "has_id": True}
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_item(str(uuid.uuid4())))


def test_request_key_unique_per_company(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    key, name = str(uuid.uuid4()), f"{uuid.uuid4()}.pdf"
    with _owner(migrated_dsn) as conn:
        conn.execute(*_item(a, request_key=key, storage_name=name))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_item(a, request_key=key))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_item(a, storage_name=name))
        # Another company may use the same key and the same stored name.
        conn.execute(*_item(b, request_key=key, storage_name=name))


def _member(dsn: str, client_id: str, role: str = "tenant_staff") -> str:
    user = str(uuid.uuid4())
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (user, f"synthetic-{user}@example.test"),
        )
        conn.execute(
            "INSERT INTO console_memberships (user_id, client_id, role) VALUES (%s, %s, %s)",
            (user, client_id, role),
        )
    return user


def _with_owner(dsn: str) -> tuple[str, str]:
    """A fresh company whose HR owner is an enabled staff member."""
    client_id = _company(dsn)
    owner = _member(dsn, client_id)
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO hr_client_owners (client_id, user_id, approved_by) VALUES (%s, %s, %s)",
            (client_id, owner, owner),
        )
    return client_id, owner


def _is_owner(app_dsn: str, client_id: str | None, user_id: str | None) -> bool:
    """Ask the function as csa_app, with the settings the routes set."""
    with psycopg.connect(app_dsn) as conn:
        if client_id is not None:
            conn.execute("SELECT set_config('app.client_id', %s, true)", (client_id,))
        if user_id is not None:
            conn.execute("SELECT set_config('app.hr_user_id', %s, true)", (user_id,))
        row = conn.execute("SELECT public.training_is_hr_owner()").fetchone()
        conn.rollback()
    assert row is not None
    return row[0]


def test_hr_owner_function_true_for_owner_false_for_others(migrated_dsn: str, app_dsn: str) -> None:
    client_id, owner = _with_owner(migrated_dsn)
    staff = _member(migrated_dsn, client_id)
    admin = _member(migrated_dsn, client_id, "tenant_admin")
    assert _is_owner(app_dsn, client_id, owner) is True
    assert _is_owner(app_dsn, client_id, staff) is False
    assert _is_owner(app_dsn, client_id, admin) is False
    # Settings unset, one of them unset, or set to empty strings.
    assert _is_owner(app_dsn, None, None) is False
    assert _is_owner(app_dsn, client_id, None) is False
    assert _is_owner(app_dsn, None, owner) is False
    assert _is_owner(app_dsn, "", "") is False
    # Another company's owner is not this company's owner, and this one's owner is not theirs.
    other_client, other_owner = _with_owner(migrated_dsn)
    assert _is_owner(app_dsn, client_id, other_owner) is False
    assert _is_owner(app_dsn, other_client, owner) is False

    ro_client, ro_owner = _with_owner(migrated_dsn)
    off_client, off_owner = _with_owner(migrated_dsn)
    gone_client, gone_owner = _with_owner(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "UPDATE console_memberships SET role = 'read_only' WHERE user_id = %s", (ro_owner,)
        )
        conn.execute("UPDATE console_users SET is_enabled = false WHERE id = %s", (off_owner,))
        conn.execute(
            "UPDATE console_memberships SET is_enabled = false WHERE user_id = %s", (gone_owner,)
        )
    assert _is_owner(app_dsn, ro_client, ro_owner) is False
    assert _is_owner(app_dsn, off_client, off_owner) is False
    assert _is_owner(app_dsn, gone_client, gone_owner) is False


def test_hr_owner_function_pinned_to_csa_app(migrated_dsn: str) -> None:
    roles = ("csa_app", "csa_ro", "csa_identity", "csa_hr_maintenance", "public")
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT p.prosecdef, p.proconfig, p.pronargs, p.prorettype::regtype::text AS returns, "
            + ", ".join(
                f"has_function_privilege('{role}', p.oid, 'EXECUTE') AS {role}" for role in roles
            )
            + " FROM pg_proc p WHERE p.proname = 'training_is_hr_owner'"
            " AND p.pronamespace = 'public'::regnamespace"
        ).fetchall()
    # One function, with no arguments: it cannot be asked about another user or company.
    assert len(rows) == 1
    [row] = rows
    assert row["pronargs"] == 0
    assert row["returns"] == "boolean"
    assert row["prosecdef"] is True
    assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"]
    assert row["csa_app"] is True
    assert not any(row[role] for role in roles[1:])


def _page_turn(page_key: str) -> tuple[str, tuple[Any, ...]]:
    return (
        "INSERT INTO console_workspace_turns (client_id, thread_id, owner_user_id, session_id,"
        " request_key, fingerprint, body, action, page_key, result)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'x', 'answer', %s, '{}')",
        (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            hashlib.sha256(b"x").hexdigest(),
            page_key,
        ),
    )


def test_page_key_check_keeps_every_earlier_key_and_adds_training_library(
    migrated_dsn: str,
) -> None:
    with _owner(migrated_dsn) as conn:
        allowed = _allowed(conn, PAGE_CHECK)
    earlier = {"bookings", "subscriptions", "business_orders", "business_order", "workspace_home"}
    assert earlier <= allowed
    assert "training_library" in allowed
    assert set(typing.get_args(TabKey)) <= allowed
    assert _accepts(migrated_dsn, _page_turn("training_library"))
    assert _accepts(migrated_dsn, _page_turn("bookings"))
    assert not _accepts(migrated_dsn, _page_turn("training"))


def test_running_0540_again_changes_nothing(migrated_dsn: str) -> None:
    before = _definitions(migrated_dsn)
    [path] = [p for v, p in migrate.discover_migrations() if v == "0540"]
    with _owner(migrated_dsn) as conn:
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    assert _definitions(migrated_dsn) == before
