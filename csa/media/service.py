"""The picture store: put, get, find, delete.

Every function runs on a connection inside the caller's tenant transaction (`csa.core.tenancy.tx`),
so the row security policy scopes it to one company, and each query also names the company. The
caller's transaction decides when the change commits: a picture and its owner's row change
together or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import structlog
from psycopg import AsyncConnection

from csa.core import db
from csa.media.contracts import PictureKind, PictureOut
from csa.media.images import MAX_PICTURES, ImageInfo, PictureRefused

logger = structlog.get_logger(__name__)

__all__ = ["StoredPicture", "delete", "delete_for", "get", "picture_for", "put"]


@dataclass(frozen=True)
class StoredPicture:
    id: UUID
    kind: PictureKind
    subject_key: str
    media_type: str
    sha256: str
    data: bytes


async def put(
    conn: AsyncConnection[Any],
    client_id: UUID,
    kind: PictureKind,
    subject_key: str,
    data: bytes,
    info: ImageInfo,
    *,
    source: Literal["upload", "generated"],
    actor_id: UUID | None,
) -> PictureOut | None:
    """Keep `data` as the picture of this subject (an upload replaces; the row id stays).

    A generated picture never overwrites: it returns None when the subject already has one. An
    upload that would be the company's 301st raises `PictureRefused("picture_limit")`; replacing
    an upload is always allowed.
    """
    if source == "upload":
        # Under the company's media lock for the rest of the transaction, so the count below
        # cannot be passed by a second upload.
        await db.q.execute("media/lock.sql", (f"{client_id}:media",), conn=conn)
        existing = await db.q.fetch_one(
            "media/for_subject.sql", (client_id, kind, subject_key), conn=conn
        )
        if existing is None or existing["source"] != "upload":
            counted = await db.q.fetch_one("media/count_uploads.sql", (client_id,), conn=conn)
            if counted is not None and counted["n"] >= MAX_PICTURES:
                raise PictureRefused("picture_limit")
    row = await db.q.fetch_one(
        "media/upsert_asset.sql",
        {
            "client_id": client_id,
            "kind": kind,
            "subject_key": subject_key,
            "media_type": info.media_type,
            "byte_size": info.byte_size,
            "width": info.width,
            "height": info.height,
            "sha256": info.sha256,
            "source": source,
            "created_by": actor_id,
            "overwrite": source == "upload",
        },
        conn=conn,
    )
    if row is None:
        return None
    await db.q.execute("media/upsert_data.sql", (row["id"], client_id, data), conn=conn)
    logger.info(
        "media_picture_stored",
        client_id=str(client_id),
        asset_id=str(row["id"]),
        kind=kind,
        source=source,
        byte_size=info.byte_size,
        inserted=row["inserted"],
    )
    return PictureOut(id=row["id"], version=row["sha256"])


async def get(conn: AsyncConnection[Any], client_id: UUID, asset_id: UUID) -> StoredPicture | None:
    row = await db.q.fetch_one("media/get.sql", (client_id, asset_id), conn=conn)
    if row is None:
        return None
    return StoredPicture(
        id=row["id"],
        kind=row["kind"],
        subject_key=row["subject_key"],
        media_type=row["media_type"],
        sha256=row["sha256"],
        data=bytes(row["data"]),
    )


async def picture_for(
    conn: AsyncConnection[Any], client_id: UUID, kind: PictureKind, subject_key: str
) -> PictureOut | None:
    row = await db.q.fetch_one("media/for_subject.sql", (client_id, kind, subject_key), conn=conn)
    return None if row is None else PictureOut(id=row["id"], version=row["sha256"])


async def delete(conn: AsyncConnection[Any], client_id: UUID, asset_id: UUID) -> bool:
    row = await db.q.fetch_one("media/delete.sql", (client_id, asset_id), conn=conn)
    return row is not None


async def delete_for(
    conn: AsyncConnection[Any], client_id: UUID, kind: PictureKind, subject_key: str
) -> bool:
    row = await db.q.fetch_one("media/delete_for.sql", (client_id, kind, subject_key), conn=conn)
    return row is not None
