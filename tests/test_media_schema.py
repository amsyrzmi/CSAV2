"""Schema contract for migration 0610: the shared picture store (media_assets for the metadata,
media_asset_data for the bytes; disposable database).
"""

from __future__ import annotations

import hashlib
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

TABLES = ("media_assets", "media_asset_data")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
DIGEST = hashlib.sha256(b"synthetic picture").hexdigest()

GOOD: dict[str, Any] = {
    "kind": "plan",
    "subject_key": "gold",
    "media_type": "image/png",
    "byte_size": 1024,
    "width": 800,
    "height": 600,
    "sha256": DIGEST,
    "source": "upload",
}


def _q(text: str) -> LiteralString:
    """A query built from fixed table names above, for the type checker."""
    return typing.cast("LiteralString", text)


def _asset(client_id: str, **extra: Any) -> tuple[Any, ...]:
    row = {"client_id": client_id, **GOOD, **extra}
    return (
        f"INSERT INTO media_assets ({', '.join(row)})"
        f" VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
        tuple(row.values()),
    )


def _data(client_id: str, asset_id: Any) -> tuple[Any, ...]:
    return (
        "INSERT INTO media_asset_data (asset_id, client_id, data) VALUES (%s, %s, %s)",
        (asset_id, client_id, b"synthetic bytes"),
    )


def _migration_text() -> str:
    paths = dict(migrate.discover_migrations())
    return Path(paths["0610"]).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "logo"},
        {"media_type": "image/gif"},
        {"byte_size": 0},
        {"byte_size": 2097153},
        {"sha256": "a" * 63},
        {"source": "scraped"},
        {"subject_key": ""},
        {"subject_key": "x" * 129},
        {"width": 0},
        {"height": 0},
    ],
)
def test_checks_refuse_bad_rows(migrated_dsn: str, bad: dict[str, Any]) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute(*_asset(c, **bad))
        conn.execute(*_asset(c))


def test_checks_accept_the_edges(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        for n, kind in enumerate(("payment_qr", "plan", "product", "training_thumbnail")):
            conn.execute(*_asset(c, kind=kind, subject_key="k" * 128, byte_size=1 + n))
        conn.execute(*_asset(c, subject_key="top", byte_size=2097152, media_type="image/jpeg"))
        conn.execute(*_asset(c, subject_key="gen", source="generated"))


def test_one_picture_per_subject(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(*_asset(a))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_asset(a))
        conn.execute(*_asset(a, kind="product"))
        conn.execute(*_asset(b))


def test_row_security_on_both_tables(migrated_dsn: str, app_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_asset(a))
        conn.execute(*_data(a, made["id"]))
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
    with _app(app_dsn, b) as conn:
        for table in TABLES:
            [seen] = conn.execute(
                _q(f"SELECT count(*) AS n FROM {table} WHERE client_id = %s"), (a,)
            )
            assert seen["n"] == 0, table
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_asset(a, subject_key="other"))
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(*_data(a, made["id"]))
        conn.rollback()
        # Own rows work through the app role.
        [own] = conn.execute(*_asset(b))
        conn.execute(*_data(b, own["id"]))
        conn.commit()
    with _app(app_dsn, None) as conn:
        for table in TABLES:
            assert conn.execute(_q(f"SELECT 1 FROM {table}")).fetchall() == []


def test_grants(migrated_dsn: str) -> None:
    expected = {
        ("csa_app", "media_assets"): set(PRIVILEGES),
        ("csa_app", "media_asset_data"): set(PRIVILEGES),
        ("csa_ro", "media_assets"): {"SELECT"},
        ("csa_ro", "media_asset_data"): set(),
        ("csa_identity", "media_assets"): set(),
        ("csa_identity", "media_asset_data"): set(),
        ("public", "media_assets"): set(),
        ("public", "media_asset_data"): set(),
    }
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


def test_bytes_go_with_their_asset(migrated_dsn: str) -> None:
    c = _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_asset(c))
        conn.execute(*_data(c, made["id"]))
        [before] = conn.execute(
            "SELECT count(*) AS n FROM media_asset_data WHERE client_id = %s", (c,)
        )
        assert before["n"] == 1
        conn.execute("DELETE FROM media_assets WHERE id = %s", (made["id"],))
        [after] = conn.execute(
            "SELECT count(*) AS n FROM media_asset_data WHERE client_id = %s", (c,)
        )
        assert after["n"] == 0


def test_bytes_never_pair_with_another_companys_asset(migrated_dsn: str) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        [made] = conn.execute(*_asset(a))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_data(b, made["id"]))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(*_data(a, uuid.uuid4()))
        conn.execute(*_data(a, made["id"]))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(*_data(a, made["id"]))


def _shape(dsn: str) -> dict[str, Any]:
    query = (
        "SELECT"
        " (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'"
        "   AND table_name = ANY(%(t)s)) AS columns,"
        " (SELECT count(*) FROM pg_constraint"
        "   WHERE conrelid = ANY(%(r)s::regclass[])) AS constraints,"
        " (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'"
        "   AND tablename = ANY(%(t)s)) AS indexes,"
        " (SELECT count(*) FROM pg_policies WHERE tablename = ANY(%(t)s)) AS policies,"
        " (SELECT count(*) FROM information_schema.role_table_grants"
        "   WHERE table_schema = 'public' AND table_name = ANY(%(t)s)) AS grants"
    )
    params = {"t": list(TABLES), "r": [f"public.{n}" for n in TABLES]}
    with _owner(dsn) as conn:
        row = conn.execute(query, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return dict(row)


def test_running_0610_again_changes_nothing(migrated_dsn: str) -> None:
    before = _shape(migrated_dsn)
    with _owner(migrated_dsn) as conn:
        conn.execute(_migration_text())  # type: ignore[arg-type]
        conn.execute(_migration_text())  # type: ignore[arg-type]
    assert _shape(migrated_dsn) == before
    assert before["policies"] == 2 and before["columns"] == 16 and before["grants"] > 0


def test_max_applied_version_is_the_expected_one(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute("SELECT max(version) AS v FROM schema_migrations")
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert row["v"] == EXPECTED_SCHEMA_VERSION
    assert "0610" in applied
