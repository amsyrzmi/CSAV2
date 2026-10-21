"""Record an explicitly client-approved first HR owner. Never loads environment files.

Run deliberately with HR_BOOTSTRAP_DATABASE_URL; this is not a console-admin
privilege or an automatic tenant-onboarding rule. Existing assignments cannot be replaced.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from uuid import UUID

from csa.core.db import q
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict


async def assign(dsn: str, client_id: UUID, user_id: UUID, reference: str) -> None:
    config = conninfo_to_dict(dsn)
    if config.get("host") not in {"127.0.0.1", "localhost", "::1"} or config.get("port") == "5433":
        raise ValueError("HR bootstrap requires an explicitly isolated loopback database")
    async with await AsyncConnection.connect(dsn) as conn:
        async with conn.transaction():
            await q.execute("hr/key_lock.sql", (f"{client_id}:hr-owner-bootstrap",), conn=conn)
            row = await q.fetch_one(
                "hr/bootstrap_owner.sql", (user_id, reference, client_id, user_id), conn=conn
            )
            if row is None:
                raise ValueError("Owner exists or the selected active tenant member is unavailable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", type=UUID, required=True)
    parser.add_argument("--owner-id", type=UUID, required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--client-approved-assignment", action="store_true", required=True)
    args = parser.parse_args()
    dsn = os.environ.get("HR_BOOTSTRAP_DATABASE_URL")
    if not dsn or os.environ.get("ENV") not in {"dev", "ci"}:
        parser.error("Explicit dev/ci ENV and HR_BOOTSTRAP_DATABASE_URL are required")
    if not 1 <= len(args.approval_reference.strip()) <= 200:
        parser.error("Approval reference must contain 1–200 characters")
    asyncio.run(assign(dsn, args.client_id, args.owner_id, args.approval_reference.strip()))
    print("Client-approved HR owner assignment recorded")


if __name__ == "__main__":
    main()
