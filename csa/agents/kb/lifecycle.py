"""Serialize publication/deletion; stale jobs cannot recreate deleted knowledge."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from csa.agents.kb.extract import KbFileError
from csa.core import db


async def lock(conn: AsyncConnection[Any], client_id: UUID) -> None:
    await db.q.execute("kb/lifecycle_lock.sql", (str(client_id),), conn=conn)


async def check_ingestion(
    conn: AsyncConnection[Any],
    client_id: UUID,
    content_hash: str,
    started_at: datetime,
    file_id: UUID | None,
) -> None:
    await lock(conn, client_id)
    if await db.q.fetch_one(
        "kb/deletion_check.sql", (client_id, content_hash, started_at), conn=conn
    ):
        raise KbFileError(
            "content was deleted after this job was queued; upload again deliberately"
        )
    if file_id is not None:
        row = await db.q.fetch_one("kb/file_get.sql", (client_id, file_id), conn=conn)
        if row is None or row["deleted_at"] is not None or row["content_hash"] != content_hash:
            raise KbFileError("uploaded source was deleted or is no longer valid")


async def delete_document(conn: AsyncConnection[Any], client_id: UUID, document_id: UUID) -> bool:
    await lock(conn, client_id)
    row = await db.q.fetch_one("kb/document_get.sql", (client_id, document_id), conn=conn)
    if row is None:
        return False
    if row["content_hash"]:
        await db.q.execute("kb/deletion_record.sql", (client_id, row["content_hash"]), conn=conn)
    await db.q.fetch_all("kb/files_delete_document.sql", (client_id, document_id), conn=conn)
    await db.q.execute("kb/delete_chunks_by_document.sql", (client_id, document_id), conn=conn)
    await db.q.fetch_one("kb/delete_document.sql", (client_id, document_id), conn=conn)
    return True
