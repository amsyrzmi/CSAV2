"""The staging reset's database sequence on a disposable cluster: rebuild it and restore a backup.

``DROP_DATABASE_SQL`` and ``CREATE_DATABASE_SQL`` run only inside this file's throwaway container.
Migrations set the same login passwords the staging gate sets, and the roles outlive the database
they were created in, so the second rebuild here is the one a real reset does.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, LiteralString, cast

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg.conninfo import make_conninfo
from scripts import staging_reset as rs
from scripts.migrate import MIGRATIONS_DIR, discover_migrations

from tests.conftest import (
    CSA_APP_PASSWORD,
    SCHEMA_BASELINE,
    _owner_dsn,
    _run_migrations,
    requires_docker,
)

pytestmark = requires_docker

IDENTITY_PASSWORD = "synthetic_reset_identity_pw"  # NON-SECRET disposable fixture password
BASELINE_IN_CONTAINER = "/tmp/schema_baseline.sql"
DUMP_IN_CONTAINER = "/tmp/csa_staging.dump"
# deploy/STAGING_PIPELINE.md, "Resetting staging", prints these; keep them the same.
PG_DUMP = " ".join(rs.PG_DUMP_COMMAND)
PG_RESTORE = "pg_restore -U csa -d csa_staging --exit-on-error --single-transaction"
SYNTHETIC_SLUG = "synthetic-reset"


def _immutable_triggers() -> set[str]:
    names: set[str] = set()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        names |= set(re.findall(r"CREATE TRIGGER (\w+_immutable)\b", path.read_text()))
    assert names
    return names


IMMUTABLE_TRIGGERS = _immutable_triggers()


def _dsn(container: Any, database: str, **overrides: str) -> str:
    return make_conninfo(_owner_dsn(container), dbname=database, **overrides)


def _maintenance(container: Any, statement: str) -> None:
    with psycopg.connect(_dsn(container, rs.MAINTENANCE_DATABASE), autocommit=True) as conn:
        conn.execute(cast(LiteralString, statement))


def _in_container(container: Any, *command: str) -> None:
    code, output = container.exec(list(command))
    if code != 0:
        raise AssertionError(f"exit {code}: {output.decode(errors='replace')}")


def _apply_baseline(container: Any) -> None:
    """As ``_apply_baseline`` in ``tests/conftest.py``, on the database the reset rebuilds."""
    container.copy_into_container(SCHEMA_BASELINE.read_bytes(), BASELINE_IN_CONTAINER)
    _in_container(
        container,
        "psql",
        "-U",
        "csa",
        "-d",
        rs.DATABASE,
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
        "-f",
        BASELINE_IN_CONTAINER,
    )


def _build(container: Any) -> None:
    _apply_baseline(container)
    _run_migrations(_dsn(container, rs.DATABASE))


def _add_client(container: Any) -> str:
    with psycopg.connect(_dsn(container, rs.DATABASE), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO clients (slug, name, platform_version) VALUES (%s, %s, %s) RETURNING id",
            (SYNTHETIC_SLUG, "Synthetic Reset", "synthetic"),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _owner_read(container: Any, statement: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(_dsn(container, rs.DATABASE), autocommit=True) as conn:
        return conn.execute(cast(LiteralString, statement)).fetchall()


def _slugs_as_app(container: Any, client_id: str) -> list[str]:
    """What ``csa_app``, logging in with the gate's password, sees of one tenant."""
    dsn = _dsn(container, rs.DATABASE, user="csa_app", password=CSA_APP_PASSWORD)
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('app.client_id', %s, true)", (client_id,))
        return [str(row[0]) for row in conn.execute("SELECT slug FROM clients").fetchall()]


def _accounts_as_identity(container: Any) -> int:
    """What ``csa_identity``, logging in with the gate's password, reads for sign-in."""
    dsn = _dsn(container, rs.DATABASE, user="csa_identity", password=IDENTITY_PASSWORD)
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT count(*) FROM console_users").fetchone()
    assert row is not None
    return int(row[0])


def _triggers_there(container: Any) -> set[str]:
    rows = _owner_read(container, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
    return {str(row[0]) for row in rows} & IMMUTABLE_TRIGGERS


@pytest.fixture
def staging_database(pg_container: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """An empty ``csa_staging`` in the disposable cluster, gone again afterwards."""
    monkeypatch.setenv("CSA_IDENTITY_PASSWORD", IDENTITY_PASSWORD)
    _maintenance(pg_container, rs.DROP_DATABASE_SQL)
    _maintenance(pg_container, rs.CREATE_DATABASE_SQL)
    yield pg_container
    _maintenance(pg_container, rs.DROP_DATABASE_SQL)


def test_rebuild_sequence_ends_at_the_newest_migration(staging_database: Any) -> None:
    container = staging_database
    _build(container)
    client_id = _add_client(container)
    assert _slugs_as_app(container, client_id) == [SYNTHETIC_SLUG]
    assert _accounts_as_identity(container) == 0

    # The drop is forced: a second connection is open, as a stray session would be.
    with psycopg.connect(_dsn(container, rs.DATABASE), autocommit=True) as stray:
        stray.execute("SELECT 1")
        _maintenance(container, rs.DROP_DATABASE_SQL)
        _maintenance(container, rs.CREATE_DATABASE_SQL)
        with pytest.raises(psycopg.OperationalError):
            stray.execute("SELECT 1")
    assert _owner_read(container, "SELECT to_regclass('public.clients')") == [(None,)]

    _build(container)
    newest = discover_migrations()[-1][0]
    assert _owner_read(container, "SELECT max(version) FROM schema_migrations") == [(newest,)]
    assert newest == EXPECTED_SCHEMA_VERSION
    assert _owner_read(container, "SELECT count(*) FROM clients") == [(0,)]
    assert _triggers_there(container) == IMMUTABLE_TRIGGERS
    assert _slugs_as_app(container, client_id) == []
    assert _accounts_as_identity(container) == 0


def test_backup_restores(staging_database: Any) -> None:
    container = staging_database
    _build(container)
    client_id = _add_client(container)

    _in_container(container, "sh", "-c", f"{PG_DUMP} > {DUMP_IN_CONTAINER}")
    _maintenance(container, rs.DROP_DATABASE_SQL)
    _maintenance(container, rs.CREATE_DATABASE_SQL)
    _in_container(container, "sh", "-c", f"{PG_RESTORE} < {DUMP_IN_CONTAINER}")

    assert _owner_read(container, "SELECT slug FROM clients") == [(SYNTHETIC_SLUG,)]
    assert _owner_read(container, "SELECT max(version) FROM schema_migrations") == [
        (discover_migrations()[-1][0],)
    ]
    assert _triggers_there(container) == IMMUTABLE_TRIGGERS
    assert _slugs_as_app(container, client_id) == [SYNTHETIC_SLUG]
