"""Refuse destructive reset unless psql and Python select the same local dev database."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import psycopg
from csa.core import db
from psycopg.conninfo import conninfo_to_dict


def validate_dsn(dsn: str) -> None:
    params = conninfo_to_dict(dsn)
    if (
        params.get("host") not in {"127.0.0.1", "localhost"}
        or params.get("port", "5432") in {"5432", "5433"}
        or params.get("dbname") != "csa"
        or params.get("user") != "csa"
        or any(params.get(key) for key in ("hostaddr", "service"))
    ):
        raise ValueError("Reset requires an explicit loopback dev port (not 5432/5433), csa/csa")


async def verify(dsn: str, command: list[str]) -> None:
    validate_dsn(dsn)
    if not command:
        raise ValueError("Reset requires an explicit psql command")
    query = db.q.load("jobs/reset_target.sql")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            "-X",
            "-A",
            "-t",
            "-w",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "psql command unavailable; no container fallback or reset performed"
        ) from None
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("Reset target check timed out; no reset performed") from None
    if process.returncode:
        raise RuntimeError("psql target check failed; no reset performed")
    psql_identity = json.loads(output)
    async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=5) as conn:
        cursor = await conn.execute(query)
        row = await cursor.fetchone()
    if row is None or json.loads(row[0]) != psql_identity:
        raise RuntimeError("psql/Python database targets differ; refusing reset")
    print("Verified psql and Python target the same isolated local dev database.")


def main() -> None:
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    asyncio.run(verify(os.environ["MIGRATE_DATABASE_URL"], command))


if __name__ == "__main__":
    main()
