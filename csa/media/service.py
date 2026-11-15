"""The picture store: put, get, find, delete.

Every function runs on a connection inside the caller's tenant transaction (`csa.core.tenancy.tx`),
so the row security policy scopes it to one company, and each query also names the company. The
caller's transaction decides when the change commits: a picture and its owner's row change
together or not at all.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import structlog
from psycopg import AsyncConnection

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.core import db
from csa.media.contracts import PictureKind, PictureOut
from csa.media.images import MAX_PICTURES, ImageInfo, PictureRefused

logger = structlog.get_logger(__name__)

__all__ = [
    "PictureMeta",
    "StoredPicture",
    "delete",
    "delete_for",
    "get",
    "get_meta",
    "picture_for",
    "put",
    "remove_picture",
    "set_picture",
]


@dataclass(frozen=True)
class PictureMeta:
    """A picture without its bytes: enough for the read rule and the `ETag` comparison."""

    id: UUID
    kind: PictureKind
    subject_key: str
    media_type: str
    sha256: str


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
    an upload is always allowed. `info` must describe `data` (its size and SHA-256 are what the
    row and the `ETag` carry), else `ValueError("info_does_not_match_data")`: that is a caller bug,
    not a picture the user can fix, so it is not a `PictureRefused`.
    """
    if info.byte_size != len(data) or info.sha256 != hashlib.sha256(data).hexdigest():
        raise ValueError("info_does_not_match_data")
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


async def get_meta(
    conn: AsyncConnection[Any], client_id: UUID, asset_id: UUID
) -> PictureMeta | None:
    """The picture's metadata by id, in one company; the bytes are not read."""
    row = await db.q.fetch_one("media/get_meta.sql", (client_id, asset_id), conn=conn)
    if row is None:
        return None
    return PictureMeta(
        id=row["id"],
        kind=row["kind"],
        subject_key=row["subject_key"],
        media_type=row["media_type"],
        sha256=row["sha256"],
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


async def set_picture(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    kind: PictureKind,
    subject_key: str,
    data: bytes,
    info: ImageInfo,
) -> PictureOut:
    """Keep an uploaded picture for the subject and audit `media.picture.set`.

    The caller has already checked that the subject exists in this company and that `actor` may
    change it. Raises `PictureRefused("picture_limit")` like `put`.
    """
    stored = await put(
        conn, client_id, kind, subject_key, data, info, source="upload", actor_id=actor.user_id
    )
    if stored is None:  # an upload always overwrites; None is only for a generated picture
        raise RuntimeError("an uploaded picture was not stored")
    await audit(conn, actor, client_id, "media.picture.set", f"{kind}:{subject_key}")
    return stored


async def remove_picture(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    kind: PictureKind,
    subject_key: str,
) -> None:
    """Remove the subject's picture; audit `media.picture.removed` only when one was there."""
    if await delete_for(conn, client_id, kind, subject_key):
        await audit(conn, actor, client_id, "media.picture.removed", f"{kind}:{subject_key}")
