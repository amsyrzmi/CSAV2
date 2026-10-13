"""LangGraph checkpoint setup script (LG01 §7.1).

Runs ``AsyncPostgresSaver.setup()`` exactly ONCE, as the owner ``csa``, AFTER
``0140_lg_schema.sql`` has created the ``lg`` schema and set the default
privileges. ``setup()`` runs DDL — it creates the ``lg.*`` checkpoint tables —
so it must run as the owner and must NEVER run at app boot (that is a known
pitfall). The app processes build their saver against the already-created
tables (see :mod:`csa.graph.saver`).

Invoked by ``make db-reset`` and by the compose ``migrate`` one-shot. Reads the
owner DSN from ``LG_SETUP_DATABASE_URL`` (preferred, an owner ``csa`` DSN) or
falls back to ``LG_DATABASE_URL`` / ``DATABASE_URL``. The connection is opened
``autocommit=True`` with ``dict_row`` and ``search_path=lg`` so the tables land
in the ``lg`` schema.
"""  # LG01

from __future__ import annotations

import asyncio
import os
import sys

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class LgSetupError(Exception):
    """Raised when the owner DSN for the checkpoint setup is missing."""


def _owner_dsn() -> str:
    """Return the owner (``csa``) DSN used to create the ``lg.*`` tables.

    ``setup()`` issues DDL, so this must connect as the schema owner ``csa``,
    not the runtime ``csa_app`` role.
    """
    dsn = (
        os.environ.get("LG_SETUP_DATABASE_URL")
        or os.environ.get("LG_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        raise LgSetupError(
            "LG_SETUP_DATABASE_URL, LG_DATABASE_URL, or DATABASE_URL must be set "
            "(owner csa) to run the checkpoint setup"
        )
    return dsn


async def setup(dsn: str) -> None:
    """Open an autocommit pool in schema ``lg`` and run ``setup()`` once."""
    async with AsyncConnectionPool(
        conninfo=dsn,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
    ) as pool:
        async with pool.connection() as conn:
            await conn.execute("SET search_path TO lg")
            saver = AsyncPostgresSaver(conn)
            await saver.setup()


async def run() -> None:
    await setup(_owner_dsn())


def main() -> int:
    try:
        asyncio.run(run())
    except LgSetupError as exc:
        print(f"lg_setup error: {exc}", file=sys.stderr)
        return 1
    print("lg checkpoint tables ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
