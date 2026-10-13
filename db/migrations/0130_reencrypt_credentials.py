"""GUARDED credential re-encryption skeleton (LG01 §6).

DO NOT RUN against real data without first migrating every reader of the
credential columns to a decrypting read path. This script is a one-off that
envelope-encrypts the plaintext ``channel_credentials.access_token`` /
``refresh_token`` columns in place, idempotently (rows already ``v1:`` are
skipped).

It is NOT a SQL migration: ``scripts/migrate.py`` globs only ``*.sql`` and skips
every ``*.py``, so this file never enters ``schema_migrations`` and is never
auto-applied. It is NOT wired into ``make db-reset`` or CI. It carries a
top-of-file guard: it refuses to run without
``--i-understand-this-writes-credentials`` and refuses to run when ``ENV=prod``
without a second ``--yes-really-prod`` confirmation.

BLOCKING READERS that MUST move to a decrypting read path BEFORE this runs
(design §6 / §12.9) — any that still expect plaintext will silently read
ciphertext the instant this script writes:

1. n8n workflows that read ``channel_credentials.access_token`` /
   ``refresh_token`` directly.
2. The committed ``public.webhook_credentials`` security_barrier VIEW, which
   re-exposes ``access_token`` -> ``secret_enc`` and ``refresh_token`` ->
   ``secret_prev_enc`` for ``channel='webhook'`` rows.
3. Any MCP / KB / other service that selects those columns.

The encrypt-and-write helper builds AAD via
``credential_aad("channel_credentials", column, str(row.id))`` — byte-identical
to the adapter decrypt path — so a round-trip across the two boundaries
succeeds (proven by ``tests/test_reencrypt.py``).
"""  # LG01

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import psycopg
from csa.core.crypto import credential_aad, encrypt
from psycopg.rows import dict_row

# The columns this migration encrypts, in a fixed order.
_CREDENTIAL_COLUMNS = ("access_token", "refresh_token")
_TABLE = "channel_credentials"
_VERSION_PREFIX = "v1:"

# The understood-and-acknowledged guard flags.
_ACK_FLAG = "--i-understand-this-writes-credentials"
_PROD_FLAG = "--yes-really-prod"


class ReencryptGuardError(Exception):
    """Raised when a safety guard blocks the run."""


def _is_already_encrypted(value: str | None) -> bool:
    """Return True when a cell is NULL or already a ``v1:`` ciphertext."""
    return value is None or value.startswith(_VERSION_PREFIX)


def encrypt_cell(column: str, row_id: str, plaintext: str) -> str:
    """Encrypt one credential cell, binding it to its exact (table, column, row).

    This is the migration-side encrypt path. It constructs AAD via the single
    pinned :func:`csa.core.crypto.credential_aad` builder so it is byte-identical
    to the adapter's decrypt path.
    """
    aad = credential_aad(_TABLE, column, row_id)
    return encrypt(plaintext, aad)


def _check_guards(args: argparse.Namespace, env: str | None) -> None:
    """Enforce the acknowledgement + prod-confirmation guards."""
    if not args.acknowledged:
        raise ReencryptGuardError(
            f"refusing to run without {_ACK_FLAG}: this script rewrites live "
            "credential columns and every reader must migrate first"
        )
    if env == "prod" and not args.yes_really_prod:
        raise ReencryptGuardError(f"ENV=prod requires a second confirmation flag {_PROD_FLAG}")


async def reencrypt(dsn: str) -> int:
    """Encrypt all plaintext credential cells; return the number of cells written.

    Idempotent: cells that are NULL or already ``v1:`` are skipped. Each row's
    updates run inside a transaction.
    """
    written = 0
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, {', '.join(_CREDENTIAL_COLUMNS)} FROM {_TABLE}"  # noqa: S608
            )
            rows = await cur.fetchall()

        for row in rows:
            row_id = str(row["id"])
            updates: dict[str, str] = {}
            for column in _CREDENTIAL_COLUMNS:
                current = row[column]
                if _is_already_encrypted(current):
                    continue
                updates[column] = encrypt_cell(column, row_id, current)
            if not updates:
                continue
            set_clause = ", ".join(f"{col} = %({col})s" for col in updates)
            async with conn.transaction():
                await conn.execute(
                    f"UPDATE {_TABLE} SET {set_clause} WHERE id = %(id)s",  # noqa: S608
                    {**updates, "id": row["id"]},
                )
            written += len(updates)
    return written


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GUARDED one-off: envelope-encrypt channel_credentials in place."
    )
    parser.add_argument(
        _ACK_FLAG,
        dest="acknowledged",
        action="store_true",
        help="required acknowledgement that this rewrites live credentials",
    )
    parser.add_argument(
        _PROD_FLAG,
        dest="yes_really_prod",
        action="store_true",
        help="second confirmation required when ENV=prod",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    env = os.environ.get("ENV")
    try:
        _check_guards(args, env)
    except ReencryptGuardError as exc:
        print(f"guard: {exc}", file=sys.stderr)
        return 2

    dsn = os.environ.get("MIGRATE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("MIGRATE_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
        return 1

    written = asyncio.run(reencrypt(dsn))
    print(f"re-encrypted {written} credential cell(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
