"""Reconcile one company's training store against its rows.

Files are stored as ``<company>/<uuid>.<ext>`` for a PDF or a video, and a video still arriving is
a ``.<name>.part`` file on the local store. Only names of that shape are ever swept; anything else
in the company's folder stays. The sweep runs under the company lock, the same one uploads take
for their quota check and write, so it never sees a file whose row is not yet committed.

The keep set is every row's ``storage_name`` plus ``.<storage_name>.part`` of every row still
``uploading``: a sweep that a PDF failure or a removal triggers must not delete a video that is
being uploaded right now. A video refused for its file (unreadable, too long, an unplayable codec)
keeps its row but not its file, so a file whose delete failed at refusal goes with the next sweep.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.core import db
from csa.core.tenancy import tx
from csa.storage.base import ObjectStore

__all__ = ["STORED", "cleanup", "lock"]

# Only names this module's callers generate are ever swept (and the local store's temporary files).
STORED = re.compile(r"\.?[a-f0-9-]{36}\.(pdf|mp4|m4v|mov|webm)(\.part)?")


def _lock_key(client_id: UUID) -> str:
    return f"{client_id}:training"


async def lock(conn: AsyncConnection[Any], client_id: UUID) -> None:
    """Take the company's training lock until the transaction ends."""
    await db.q.execute("training/lock.sql", (_lock_key(client_id),), conn=conn)


async def cleanup(pool: AsyncConnectionPool, store: ObjectStore, client_id: UUID) -> int:
    """Delete this company's stored files that no row keeps, under the company lock.

    Only names of the generated shape are touched; anything else in the prefix stays.
    """
    removed = 0
    async with tx(pool, str(client_id)) as conn:
        await lock(conn, client_id)
        rows = await db.q.fetch_all("training/names.sql", (client_id,), conn=conn)
        # A refused video's file is no longer kept (its delete at refusal time may have failed).
        keep = {row["storage_name"] for row in rows if not row["refused"]}
        keep |= {f".{row['storage_name']}.part" for row in rows if row["uploading"]}
        for name in await asyncio.to_thread(store.list_names, client_id):
            if STORED.fullmatch(name) and name not in keep:
                await asyncio.to_thread(store.delete, client_id, name)
                removed += 1
    return removed
