"""Opt-in HR retention/reminder worker. Requires its own least-privilege DB role.

Does not load .env files. Start explicitly after provisioning the worker role and
client opt-in; no model, mail, calendar, financial or approval action is called.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Literal

from csa.core.db import q
from psycopg import AsyncConnection
from pydantic import BaseModel, Field


class MaintenanceResult(BaseModel):
    status: Literal["completed", "disabled", "busy"]
    erased_count: int = Field(ge=0)
    notification_count: int = Field(ge=0)


async def run_once(dsn: str) -> list[MaintenanceResult]:
    async with await AsyncConnection.connect(dsn, autocommit=True) as conn:
        role = await q.fetch_one("hr/worker_role.sql", conn=conn)
        if not role or not role["allowed"]:
            raise ValueError("HR maintenance requires its dedicated nonprivileged database role")
        tenants = await q.fetch_all("hr/worker_tenants.sql", conn=conn)
        results = []
        for tenant in tenants:
            async with conn.transaction():
                row = await q.fetch_one(
                    "hr/worker_run.sql", (tenant["client_id"],), conn=conn, model=MaintenanceResult
                )
                if row is None:
                    raise RuntimeError("HR maintenance returned no result")
                results.append(row)
        return results


async def run(dsn: str, once: bool, interval: int) -> None:
    while True:
        results = await run_once(dsn)
        print(
            f"HR maintenance: {len(results)} tenants; "
            f"{sum(row.notification_count for row in results)} local reminders",
            flush=True,
        )
        if once:
            return
        await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=300)
    args = parser.parse_args()
    dsn = os.environ.get("HR_MAINTENANCE_DATABASE_URL")
    if not dsn or not 60 <= args.poll_seconds <= 86400:
        parser.error("Set HR_MAINTENANCE_DATABASE_URL and a poll interval from 60 to 86400 seconds")
    asyncio.run(run(dsn, args.once, args.poll_seconds))


if __name__ == "__main__":
    main()
