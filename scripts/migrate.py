"""Forward-only migration runner (LG01 §9.4).

Creates ``schema_migrations(version text pk, applied_at timestamptz)``, globs
ONLY ``db/migrations/*.sql`` (every ``*.py`` is skipped — the guarded
re-encrypt skeleton is never applied here), applies each unrecorded migration in
its own transaction, and stamps the version. Connects as the owner ``csa``.

Version-string invariant: every migration filename must start with a purely
numeric, 4+-digit token (``^\\d{4,}_``) so ``schema_migrations.version`` stays
fixed-width and lexically comparable. A mis-numbered file aborts with a non-zero
exit before anything is applied.

Role passwords are NEVER in SQL. Immediately after ``0100_roles.sql`` applies,
the runner sets ``csa_app`` / ``csa_ro`` LOGIN + password via a parameterized
``ALTER ROLE`` reading ``CSA_APP_PASSWORD`` / ``CSA_RO_PASSWORD`` from the
environment; a missing required password is fatal.
"""  # LG01

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

# db/migrations lives at <repo-root>/db/migrations; this file is
# <repo-root>/scripts/migrate.py.
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

# A migration filename version token: purely digits, at least 4 wide, then '_'.
VERSION_RE = re.compile(r"^\d{4,}_")

# The migration that creates the login roles; the password step runs right after.
ROLES_MIGRATION = "0100_roles.sql"


class MigrationError(Exception):
    """Raised on a fatal migration condition (bad version, missing password)."""


def _owner_dsn() -> str:
    """Return the DSN the runner connects with (owner ``csa``).

    Uses ``MIGRATE_DATABASE_URL`` when set (owner connection), else falls back
    to ``DATABASE_URL``. The runner MUST connect as the table owner ``csa`` so
    role creation and DDL succeed and ``resolve_endpoint`` is owned correctly.
    """
    dsn = os.environ.get("MIGRATE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise MigrationError("MIGRATE_DATABASE_URL or DATABASE_URL must be set")
    return dsn


def discover_migrations() -> list[tuple[str, Path]]:
    """Return ``(version, path)`` for every ``*.sql`` migration, sorted.

    ``*.py`` files are never globbed. A filename whose leading version token is
    not ``^\\d{4,}_`` aborts loudly.
    """
    migrations: list[tuple[str, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        name = path.name
        if not VERSION_RE.match(name):
            raise MigrationError(
                f"migration {name!r} has an invalid version: expected a 4+-digit "
                f"zero-padded numeric prefix matching {VERSION_RE.pattern!r}"
            )
        version = name[: name.index("_")]
        migrations.append((version, path))
    return migrations


async def _ensure_ledger(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version text PRIMARY KEY,"
        "  applied_at timestamptz NOT NULL DEFAULT now()"
        ")"
    )


async def _applied_versions(conn: psycopg.AsyncConnection) -> set[str]:
    async with conn.cursor() as cur:
        await cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in await cur.fetchall()}


async def _set_role_passwords(conn: psycopg.AsyncConnection) -> None:
    """Set LOGIN + password on ``csa_app`` / ``csa_ro`` from the environment.

    Parameterized; no password literal ever enters SQL or the ledger. A missing
    required password is fatal — an unauthenticatable ``csa_app`` must never be
    left behind for apps / the RLS tests to trip over.
    """
    for role, env_var in (("csa_app", "CSA_APP_PASSWORD"), ("csa_ro", "CSA_RO_PASSWORD")):
        pw = os.environ.get(env_var)
        if not pw:
            raise MigrationError(f"{env_var} is required to set the {role} login password")
        # ALTER ROLE is a utility statement and cannot bind a server-side
        # parameter, so the password is composed as a safely-quoted SQL literal
        # (psycopg sql.Literal, injection-safe). It is still read from the
        # environment and never written to a .sql file or the ledger.
        stmt = sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(role),
            sql.Literal(pw),
        )
        await conn.execute(stmt)


async def run() -> None:
    """Apply all unrecorded migrations forward-only, then stamp them."""
    dsn = _owner_dsn()
    migrations = discover_migrations()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await _ensure_ledger(conn)
        applied = await _applied_versions(conn)

        for version, path in migrations:
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            # Each migration + its stamp apply in one transaction.
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
            print(f"applied {path.name}")

            # The role-password step runs right after the roles migration, in
            # its own transaction, so csa_app/csa_ro can actually log in.
            if path.name == ROLES_MIGRATION:
                async with conn.transaction():
                    await _set_role_passwords(conn)
                print("set login passwords for csa_app / csa_ro")


def main() -> int:
    try:
        asyncio.run(run())
    except MigrationError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
