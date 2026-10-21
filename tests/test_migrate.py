"""Migration runner tests (LG01 §9.4)."""  # LG01

from __future__ import annotations

import os
import shlex
import subprocess

import psycopg
import pytest
from scripts import migrate

from tests.conftest import (
    CSA_APP_PASSWORD,
    CSA_RO_PASSWORD,
    requires_docker,
)

EXPECTED_SCHEMA_VERSION = "0410"
LG01_SQL_VERSIONS = {"0100", "0110", "0120"}


def test_discover_rejects_bad_version(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    bad = tmp_path / "150_bad.sql"
    bad.write_text("SELECT 1;")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(migrate.MigrationError):
        migrate.discover_migrations()


def test_version_regex_accepts_four_plus_digits() -> None:
    assert migrate.VERSION_RE.match("0100_roles.sql")
    assert migrate.VERSION_RE.match("01000_future.sql")
    assert not migrate.VERSION_RE.match("150_x.sql")
    assert not migrate.VERSION_RE.match("abc_x.sql")


def test_py_files_are_not_globbed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / "0100_roles.sql").write_text("SELECT 1;")
    (tmp_path / "0130_reencrypt_credentials.py").write_text("raise SystemExit(1)")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    discovered = {name for _, (name) in [(v, p.name) for v, p in migrate.discover_migrations()]}
    assert discovered == {"0100_roles.sql"}


def test_missing_app_password_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    # The runner must fail loudly (non-zero) when a required password is absent.
    monkeypatch.setenv("MIGRATE_DATABASE_URL", "postgresql://csa@localhost:1/csa")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CSA_APP_PASSWORD", raising=False)
    monkeypatch.delenv("CSA_RO_PASSWORD", raising=False)

    # _set_role_passwords is the loud-fail path; call it directly against a dummy
    # connection substitute to prove the env check fires before any SQL.
    import asyncio

    class _DummyConn:
        async def execute(self, *_a, **_k):  # pragma: no cover - must not run
            raise AssertionError("should not execute SQL without a password")

    with pytest.raises(migrate.MigrationError):
        asyncio.run(migrate._set_role_passwords(_DummyConn()))  # type: ignore[arg-type]


@requires_docker
def test_migrations_apply_and_are_idempotent(migrated_dsn: str) -> None:
    # migrated_dsn already ran baseline + migrations once (module fixture).
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    applied = {r[0] for r in rows}
    assert LG01_SQL_VERSIONS <= applied

    # Re-run must be a clean no-op (idempotent).
    old_app = os.environ.get("CSA_APP_PASSWORD")
    old_ro = os.environ.get("CSA_RO_PASSWORD")
    old_dsn = os.environ.get("MIGRATE_DATABASE_URL")
    os.environ["MIGRATE_DATABASE_URL"] = migrated_dsn
    os.environ["CSA_APP_PASSWORD"] = CSA_APP_PASSWORD
    os.environ["CSA_RO_PASSWORD"] = CSA_RO_PASSWORD
    try:
        assert migrate.main() == 0
    finally:
        for key, val in (
            ("MIGRATE_DATABASE_URL", old_dsn),
            ("CSA_APP_PASSWORD", old_app),
            ("CSA_RO_PASSWORD", old_ro),
        ):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        rows2 = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert {r[0] for r in rows2} == applied


@requires_docker
def test_resolve_endpoint_owned_by_csa(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT pg_get_userbyid(proowner) AS owner "
            "FROM pg_proc WHERE proname = 'resolve_endpoint'"
        ).fetchone()
    assert row is not None
    assert row[0] == "csa"


@requires_docker
def test_make_reset_only_disposable_container(pg_container: object, migrated_dsn: str) -> None:
    """Exercise the real reset command with both clients pinned to this test container."""
    from tests.conftest import REPO_ROOT

    container_id = pg_container.get_wrapped_container().id  # type: ignore[attr-defined]
    psql = ["docker", "exec", "-i", container_id, "psql", "-U", "csa", "-d", "csa"]
    result = subprocess.run(
        ["make", "-e", "db-reset"],
        cwd=REPO_ROOT,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "ENV": "ci",
            "NO_LOCAL_ENV": "1",
            "DEV_OWNER_DSN": migrated_dsn,
            "DEV_APP_PASSWORD": CSA_APP_PASSWORD,
            "DEV_RO_PASSWORD": CSA_RO_PASSWORD,
            "PSQL": shlex.join(psql),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert "Verified psql and Python target the same isolated local dev database." in result.stdout
    with psycopg.connect(migrated_dsn) as conn:
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone() == (
            EXPECTED_SCHEMA_VERSION,
        )
        rows = conn.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname IN "
            "('console_chat_participants','console_chat_threads','console_chat_turns','console_inbox_actions')"
        ).fetchall()
        assert len(rows) == 4 and all(row[1] and not row[2] for row in rows)
