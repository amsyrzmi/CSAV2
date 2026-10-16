"""Tenant-scoped persistence of knowledge base documents and chunks (LG13)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg

from csa.agents.kb.embedder import vector_to_sql
from csa.agents.kb.extract import ExtractedChunk
from csa.agents.kb.lifecycle import lock
from csa.core import db


@dataclass(frozen=True)
class StoredDocument:
    outcome: Literal["ingested", "duplicate"]
    document_id: UUID
    chunk_count: int
    version: int
    replaced_document_ids: tuple[UUID, ...] = ()


async def store_document(
    conn: psycopg.AsyncConnection[Any],
    *,
    client_id: UUID,
    title: str,
    chunks: list[ExtractedChunk],
    embeddings: list[list[float]],
    content_hash: str,
    embed_model: str,
    kind: Literal["doc", "faq"] = "doc",
    category: str | None = None,
    language: str = "en",
    source_uri: str | None = None,
    file_name: str | None = None,
) -> StoredDocument:
    """Insert a document version inside the caller's tenant transaction.

    Identical active content is reported as a duplicate. An active document with
    the same ``file_name`` is superseded: it is deactivated, its chunks removed,
    and the new row takes the next version number.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must have the same length")
    await lock(conn, client_id)

    existing = await db.q.fetch_one(
        "kb/find_document_by_hash.sql", (client_id, content_hash), conn=conn
    )
    if existing is not None:
        return StoredDocument(
            outcome="duplicate",
            document_id=existing["id"],
            chunk_count=int(existing["chunk_count"]),
            version=int(existing["version"]),
        )

    version = 1
    replaced: list[UUID] = []
    if file_name:
        previous = await db.q.fetch_all(
            "kb/find_active_by_file_name.sql", (client_id, file_name), conn=conn
        )
        for row in previous:
            version = max(version, int(row["version"]) + 1)
            await db.q.execute("kb/deactivate_document.sql", (client_id, row["id"]), conn=conn)
            await db.q.execute(
                "kb/delete_chunks_by_document.sql", (client_id, row["id"]), conn=conn
            )
            replaced.append(row["id"])

    doc_id = uuid4()
    await db.q.execute(
        "kb/insert_document.sql",
        (
            doc_id,
            client_id,
            title,
            source_uri,
            category,
            language,
            file_name,
            content_hash,
            len(chunks),
            kind,
            embed_model,
            version,
        ),
        conn=conn,
    )
    async with conn.cursor() as cur:
        await cur.executemany(
            db.q.load("kb/insert_chunk.sql"),
            [
                (
                    uuid4(),
                    client_id,
                    doc_id,
                    index,
                    chunk.content,
                    chunk.heading or title,
                    vector_to_sql(embedding),
                    len(chunk.content.split()),
                    chunk.question,
                )
                for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
            ],
        )
    return StoredDocument(
        outcome="ingested",
        document_id=doc_id,
        chunk_count=len(chunks),
        version=version,
        replaced_document_ids=tuple(replaced),
    )
