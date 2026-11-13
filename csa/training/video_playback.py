"""Playing a training video and reading its transcript.

Reading is open to every human member of the company, ``read_only`` included, but a reader sees
only a ``ready`` video; a manager sees every state (the transcript made so far while it is still
transcribing). A PDF is never played here. Every row is read inside ``tx`` for the company and the
queries name it too, so another company's item is a plain 404 before the store is asked for
anything.

- **Playback** answers where to play from: on S3 a presigned inline GET under the caller's own
  prefix that lives ``PLAYBACK_URL_SECONDS`` (S3 answers Range requests itself); on the local store
  the gateway's own ``/video`` route.
- **The local video route** answers HTTP Range from a seeked handle: no ``Range`` is the whole file
  (200); one range is 206 with its ``Content-Range``; an unsatisfiable or several ranges are 416
  with ``Content-Range: bytes */size``. A header that is not a byte range of one range, or whose
  last byte is before its first, is ignored (the whole file), as RFC 9110 says. The object is
  streamed in 64 KiB blocks and no connection or company lock is held while it is: the handle
  outlives a later removal, as a PDF download's does.
- **The transcript** is read in one plain transaction, never under the company lock.

Logs carry ids, the company id, byte counts and outcome codes: never a transcript line, a title,
a file name or a URL (presigned or not).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO
from uuid import UUID

import structlog
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from psycopg_pool import AsyncConnectionPool
from starlette.types import Receive, Scope, Send

from csa.console.contracts import ConsoleActor
from csa.core import db
from csa.core.tenancy import tx
from csa.storage.base import StorageError
from csa.storage.local import LocalObjectStore
from csa.storage.multipart import MultipartStore
from csa.training import limits
from csa.training.access import can_manage
from csa.training.models import PlaybackOut, TranscriptLine, TranscriptOut
from csa.training.service import NOT_FOUND, STORAGE_UNAVAILABLE
from csa.training.video_jobs import RETRYABLE

logger = structlog.get_logger(__name__)

RANGE_UNSATISFIABLE = "training_range_not_satisfiable"
BLOCK_BYTES = 64 * 1024
# A real browser sends one short range. Anything longer is refused before it is parsed, so a header
# of thousands of ranges costs nothing.
MAX_RANGE_HEADER_CHARS = 256
VIDEO_HEADERS = {
    "Accept-Ranges": "bytes",
    "Content-Disposition": "inline",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}

_BYTE_RANGE = re.compile(r"(?P<first>[0-9]*)-(?P<last>[0-9]*)")


class Unsatisfiable(Exception):
    """The requested range lies outside the file (or several were asked for)."""

    def __init__(self, size: int = 0) -> None:
        super().__init__(size)
        self.size = size


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The inclusive ``(first, last)`` bytes to send, or ``None`` for the whole file.

    Raises ``Unsatisfiable`` for a range past the end, an empty suffix, an empty file or several
    ranges. A header that is not ``bytes=<first>-<last>`` is ignored.
    """
    if header is None:
        return None
    if len(header) > MAX_RANGE_HEADER_CHARS:
        raise Unsatisfiable
    unit, equals, spec = header.partition("=")
    if not equals or unit.strip().lower() != "bytes":
        return None
    if "," in spec:
        raise Unsatisfiable
    found = _BYTE_RANGE.fullmatch(spec.strip())
    if found is None:
        return None
    first, last = found["first"], found["last"]
    if not first and not last:
        return None
    if not first:
        # The last ``last`` bytes.
        count = int(last)
        if count == 0 or size == 0:
            raise Unsatisfiable
        return max(size - count, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if last and end < start:
        return None
    if start >= size:
        raise Unsatisfiable
    return start, min(end, size - 1)


class VideoStream(StreamingResponse):
    """A window of the stored video in 64 KiB blocks; the handle is closed when it ends."""

    def __init__(
        self,
        stream: BinaryIO,
        length: int,
        *,
        status_code: int,
        media_type: str,
        headers: Mapping[str, str],
    ) -> None:
        self.stream = stream

        def blocks() -> Iterator[bytes]:
            left = length
            while left > 0 and (block := stream.read(min(BLOCK_BYTES, left))):
                left -= len(block)
                yield block

        super().__init__(
            blocks(),
            status_code=status_code,
            media_type=media_type,
            headers={**headers, "Content-Length": str(length)},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.stream.close()


def _locate(stream: BinaryIO, header: str | None) -> tuple[int, int, int, bool]:
    """The window to send, as ``(size, start, length, partial)``; the handle is left at ``start``.

    ``partial`` says a range was asked for and honoured. Raises ``Unsatisfiable`` carrying the
    file's size, with nothing read.
    """
    size = stream.seek(0, os.SEEK_END)
    try:
        window = byte_range(header, size)
    except Unsatisfiable:
        raise Unsatisfiable(size) from None
    start, end = (0, size - 1) if window is None else window
    stream.seek(start)
    return size, start, max(end - start + 1, 0), window is not None


def _has_file(row: Mapping[str, Any]) -> bool:
    """Whether the video's file is published and kept: not while it uploads, and not once the
    worker refused the file itself (it deletes that file; a retryable failure keeps it)."""
    if row["status"] == "uploading":
        return False
    return row["status"] != "failed" or row["failure_code"] in RETRYABLE


class VideoPlayback:
    """The playback, video and transcript routes' work, for one request."""

    def __init__(self, pool: AsyncConnectionPool, store: MultipartStore) -> None:
        self.pool = pool
        self.store = store

    async def playback(self, actor: ConsoleActor, client_id: UUID, item_id: UUID) -> PlaybackOut:
        row = await self._visible(actor, client_id, item_id)
        media_type = limits.PLAYBACK_MEDIA_TYPE[row["container"]]
        # Taken before the link is signed, so the stated expiry is never later than the link's.
        expires_at = datetime.now(UTC) + timedelta(seconds=limits.PLAYBACK_URL_SECONDS)
        try:
            url = await asyncio.to_thread(
                self.store.presigned_playback,
                client_id,
                row["storage_name"],
                limits.PLAYBACK_URL_SECONDS,
                media_type,
                row["original_name"],
            )
        except StorageError as failure:
            logger.error(
                "training_playback_failed",
                item_id=str(item_id),
                client_id=str(client_id),
                outcome=type(failure).__name__,
            )
            raise HTTPException(503, STORAGE_UNAVAILABLE) from None
        if url is None:
            # The gateway serves this store itself; a platform admin has no company of their own.
            url = f"/console/v1/training/items/{item_id}/video"
            if actor.platform_admin:
                url += f"?client_id={client_id}"
        logger.info(
            "training_playback_issued",
            item_id=str(item_id),
            client_id=str(client_id),
            outcome="local" if url.startswith("/") else "presigned",
        )
        return PlaybackOut(url=url, expires_at=expires_at, media_type=media_type)

    async def open_video(
        self, actor: ConsoleActor, client_id: UUID, item_id: UUID, range_header: str | None
    ) -> StreamingResponse:
        """The video's whole file or one range of it (local store only). No connection or
        company lock is held while the object is opened or sent."""
        if not isinstance(self.store, LocalObjectStore):
            # S3 is played from its presigned URL; this route does not exist there.
            raise HTTPException(404, NOT_FOUND)
        row = await self._visible(actor, client_id, item_id)
        media_type = limits.PLAYBACK_MEDIA_TYPE[row["container"]]
        try:
            stream = await asyncio.to_thread(self.store.open_range, client_id, row["storage_name"])
        except (OSError, StorageError):
            # A removal may have committed and swept between the read and the open.
            if await self._row(client_id, item_id) is None:
                raise HTTPException(404, NOT_FOUND) from None
            logger.error("training_file_missing", item_id=str(item_id), client_id=str(client_id))
            raise HTTPException(503, STORAGE_UNAVAILABLE) from None
        with ExitStack() as handles:
            handles.enter_context(stream)
            try:
                size, start, length, partial = await asyncio.to_thread(
                    _locate, stream, range_header
                )
            except Unsatisfiable as refused:
                logger.info(
                    "training_video_range_refused", item_id=str(item_id), client_id=str(client_id)
                )
                raise HTTPException(
                    416,
                    RANGE_UNSATISFIABLE,
                    headers={**VIDEO_HEADERS, "Content-Range": f"bytes */{refused.size}"},
                ) from None
            headers = dict(VIDEO_HEADERS)
            if partial:
                headers["Content-Range"] = f"bytes {start}-{start + length - 1}/{size}"
            response = VideoStream(
                stream,
                length,
                status_code=206 if partial else 200,
                media_type=media_type,
                headers=headers,
            )
            handles.pop_all()
        logger.info(
            "training_video_opened",
            item_id=str(item_id),
            client_id=str(client_id),
            byte_size=length,
            outcome="range" if partial else "whole",
        )
        return response

    async def transcript(
        self, actor: ConsoleActor, client_id: UUID, item_id: UUID
    ) -> TranscriptOut:
        async with tx(self.pool, str(client_id)) as conn:
            row = await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn)
            await self._check(conn, actor, client_id, row)
            assert row is not None
            lines = await db.q.fetch_all(
                "training/video_transcript.sql",
                {"client_id": client_id, "item_id": item_id},
                conn=conn,
            )
        return TranscriptOut(
            language=row["transcript_language"],
            speech=row["speech"],
            segments=[
                TranscriptLine(start_ms=line["start_ms"], end_ms=line["end_ms"], text=line["text"])
                for line in lines
            ],
        )

    async def _visible(
        self, actor: ConsoleActor, client_id: UUID, item_id: UUID
    ) -> Mapping[str, Any]:
        """The video's row, or 404: not a video, not seen by this caller, or no file to play."""
        async with tx(self.pool, str(client_id)) as conn:
            row = await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn)
            await self._check(conn, actor, client_id, row)
        assert row is not None
        if not _has_file(row):
            raise HTTPException(404, NOT_FOUND)
        return row

    async def _check(
        self, conn: Any, actor: ConsoleActor, client_id: UUID, row: Mapping[str, Any] | None
    ) -> None:
        """404 unless the item is a video ``actor`` may see: readers see only a ready one."""
        if row is None or row["kind"] != "video":
            raise HTTPException(404, NOT_FOUND)
        if row["status"] != "ready" and not await can_manage(conn, actor, client_id):
            raise HTTPException(404, NOT_FOUND)

    async def _row(self, client_id: UUID, item_id: UUID) -> Mapping[str, Any] | None:
        async with tx(self.pool, str(client_id)) as conn:
            return await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn)
