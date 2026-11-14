"""Video uploads in pieces: start, piece, resume status and complete.

A video is sent as fixed-size pieces (``limits.VIDEO_PIECE_BYTES``; the last one the remainder),
each its own request, so a dropped connection costs one piece and an upload can resume. The item is
``uploading`` from the start until every piece is stored and checked, then ``uploaded`` with a job
queued for the training worker. Files are named ``<uuid>.<ext>``; the uploaded file name is data.

- **Start** checks the name, the extension and the size, then under the company lock: manage, the
  idempotency key, the video count and bytes (the declared size is reserved), and the uploads open.
  It opens the store's upload and inserts the item with its upload row.
- **A piece** is checked against the company in a short transaction that holds no lock (manage,
  the item still ``uploading``), then its number and its exact length (``Content-Length``) before
  any body byte is read. It takes one of ``PIECES_IN_FLIGHT`` per-process slots without waiting
  (503 ``training_busy`` with ``Retry-After``), reads the body bounded, checks the hash and, for
  piece 1, the container's first bytes, writes it at the offset its number gives and records it
  while the upload row exists.
- **Complete** runs under the company lock with the upload row locked: every piece present with its
  exact size, the store checks every piece's bytes against its hash and publishes the file, the item
  becomes ``uploaded``, the upload and piece rows go and a job is queued. A piece the store finds
  changed is forgotten (and sent again); a repeat of a finished completion answers the item.

The local store writes a piece with positional writes into the upload's file and publishes it with
a rename, so a write that had opened the file before the rename would land in the published file.
Completion therefore closes the item to new pieces and waits for those in flight (``_ItemGate``)
before it checks and publishes. The gate is per process: the gateway runs as one process.

Logs carry ids, the company id, the stage, byte counts, durations and outcome codes: never a file
name, a title, a URL or a hash of the content.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from starlette.requests import ClientDisconnect

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.core import db
from csa.core.tenancy import tx
from csa.storage.base import ObjectStore, StorageError, StorageNotFound
from csa.storage.multipart import MultipartStore, Part, PartMismatch
from csa.training import limits
from csa.training.access import can_manage
from csa.training.models import TITLE_MAX_CHARS, ItemOut, PieceOut, VideoStart, VideoUploadOut
from csa.training.pdf import PdfRefused, check_name
from csa.training.service import (
    FORBIDDEN,
    KEY_CONFLICT,
    LIBRARY_FULL,
    NAME_INVALID,
    NOT_FOUND,
    STORAGE_UNAVAILABLE,
    TOO_LARGE,
    TrainingLibrary,
)
from csa.training.store_sweep import cleanup, lock

logger = structlog.get_logger(__name__)

__all__ = [
    "BUSY",
    "NOT_VIDEO",
    "PIECE_CORRUPT",
    "PIECE_INVALID",
    "PIECE_MISSING",
    "PIECE_SLOTS",
    "UPLOADS_BUSY",
    "UPLOAD_CLOSED",
    "VideoUploads",
]

NOT_VIDEO = "training_not_video"
PIECE_INVALID = "training_piece_invalid"
PIECE_CORRUPT = "training_piece_corrupt"
PIECE_MISSING = "training_piece_missing"
UPLOAD_CLOSED = "training_upload_closed"
UPLOADS_BUSY = "training_uploads_in_progress"
BUSY = "training_busy"

RETRY_AFTER_SECONDS = 2
# An ISO-BMFF file (MP4, M4V, QuickTime) opens with a box whose type, bytes 4 to 8, is "ftyp";
# a WebM file opens with the EBML header's id.
_FTYP = b"ftyp"
_EBML = b"\x1a\x45\xdf\xa3"
_HEX = frozenset("0123456789abcdef")

PIECE_SLOTS = asyncio.Semaphore(limits.PIECES_IN_FLIGHT)
"""Pieces this process holds in memory at once; a piece past it is refused, never queued."""


class _ItemGate:
    """Pieces being written for one item, and whether a completion has closed it to new ones."""

    def __init__(self) -> None:
        self.active = 0
        self.closing = 0
        self.idle = asyncio.Event()


_GATES: dict[tuple[UUID, UUID], _ItemGate] = {}


def _closing(client_id: UUID, item_id: UUID) -> bool:
    """Whether a completion of the item is running in this process."""
    gate = _GATES.get((client_id, item_id))
    return gate is not None and gate.closing > 0


@asynccontextmanager
async def _writing(client_id: UUID, item_id: UUID) -> AsyncIterator[None]:
    """Count a piece in flight from its write to its record; refused while a completion runs."""
    key = (client_id, item_id)
    gate = _GATES.setdefault(key, _ItemGate())
    if gate.closing:
        raise HTTPException(409, UPLOAD_CLOSED)
    gate.active += 1
    try:
        yield
    finally:
        gate.active -= 1
        if not gate.active:
            gate.idle.set()
            if not gate.closing:
                _GATES.pop(key, None)


@asynccontextmanager
async def _completing(client_id: UUID, item_id: UUID) -> AsyncIterator[None]:
    """Close the item to new pieces and wait until the pieces in flight are written and recorded."""
    key = (client_id, item_id)
    gate = _GATES.setdefault(key, _ItemGate())
    gate.closing += 1
    try:
        while gate.active:
            gate.idle.clear()
            await gate.idle.wait()
        yield
    finally:
        gate.closing -= 1
        if not gate.closing and not gate.active:
            _GATES.pop(key, None)


async def _finish[T](call: Callable[..., T], *args: Any) -> T:
    """Run a blocking store call in a thread and let it finish even if the caller is cancelled,
    so a lock is never released (or a count dropped) while the store is still writing."""
    task = asyncio.create_task(asyncio.to_thread(call, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def _title(name: str, extension: str) -> str:
    """The file name without its extension, cut to the title limit."""
    title = name[: -len(extension)].strip()[:TITLE_MAX_CHARS].strip()
    return title or name.strip()[:TITLE_MAX_CHARS]


def _expected_size(number: int, piece_size: int, declared: int) -> int:
    return min(piece_size, declared - (number - 1) * piece_size)


def _looks_like(container: str, data: bytes) -> bool:
    if container == "webm":
        return data[:4] == _EBML
    return data[4:8] == _FTYP


def _os_code(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, type(exc).__name__)


async def _read_body(request: Request, expected: int) -> bytes:
    """Exactly ``expected`` bytes of body, reading at most one byte more."""
    buffer = bytearray()
    try:
        async for chunk in request.stream():
            buffer += chunk
            if len(buffer) > expected:
                break
    except ClientDisconnect:
        raise HTTPException(422, PIECE_INVALID) from None
    if len(buffer) != expected:
        raise HTTPException(422, PIECE_INVALID)
    return bytes(buffer)


class VideoUploads:
    """One company's video uploads, for one request.

    ``store`` is the training store; ``video`` is the same store seen as a ``MultipartStore``.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        store: ObjectStore,
        video: MultipartStore,
        identity_pool: AsyncConnectionPool | None = None,
    ) -> None:
        self.pool = pool
        self.store = store
        self.video = video
        self.library = TrainingLibrary(pool, store, identity_pool)

    async def start(
        self, actor: ConsoleActor, client_id: UUID, body: VideoStart, key: UUID
    ) -> VideoUploadOut:
        name = body.file_name
        fields = {"client_id": str(client_id), "stage": "start", "byte_size": body.byte_size}
        try:
            check_name(name)
        except PdfRefused:
            self._refused(fields, NAME_INVALID)
            raise HTTPException(422, NAME_INVALID) from None
        extension = _extension(name)
        container = limits.VIDEO_EXTENSIONS.get(extension)
        if container is None:
            self._refused(fields, NOT_VIDEO)
            raise HTTPException(422, NOT_VIDEO)
        if body.byte_size > limits.MAX_VIDEO_BYTES:
            self._refused(fields, TOO_LARGE)
            raise HTTPException(413, TOO_LARGE)
        # The same key must carry the same name, size and type to count as a retry.
        fingerprint = hashlib.sha256(
            f"{name}\n{body.byte_size}\n{body.media_type}".encode()
        ).hexdigest()
        piece_size = limits.VIDEO_PIECE_BYTES
        item_id, storage_name = uuid4(), f"{uuid4()}{extension}"
        opened = False
        upload_id: str | None = None
        try:
            async with tx(self.pool, str(client_id)) as conn:
                await lock(conn, client_id)
                if not await can_manage(conn, actor, client_id):
                    raise HTTPException(403, FORBIDDEN)
                found = await db.q.fetch_one("training/find_key.sql", (client_id, key), conn=conn)
                if found is not None:
                    if found["fingerprint"] != fingerprint:
                        raise HTTPException(409, KEY_CONFLICT)
                    plan = await self._plan(conn, client_id, found)
                    logger.info(
                        "training_video_upload_started",
                        item_id=str(found["id"]),
                        **fields,
                        outcome="duplicate",
                    )
                    return plan
                # Inside the lock, so two starts cannot both take the last slot or bytes.
                usage = await db.q.fetch_one(
                    "training/video_usage.sql", {"client_id": client_id}, conn=conn
                )
                assert usage is not None
                if (
                    usage["items"] + 1 > limits.MAX_VIDEO_ITEMS
                    or usage["bytes"] + body.byte_size > limits.MAX_VIDEO_TOTAL_BYTES
                ):
                    raise HTTPException(409, LIBRARY_FULL)
                if usage["uploading"] + 1 > limits.MAX_UPLOADS_IN_PROGRESS:
                    raise HTTPException(409, UPLOADS_BUSY)
                opened = True
                upload_id = await _finish(
                    self.video.start_upload,
                    client_id,
                    storage_name,
                    limits.PLAYBACK_MEDIA_TYPE[container],
                )
                row = await db.q.fetch_one(
                    "training/video_start_insert.sql",
                    {
                        "id": item_id,
                        "client_id": client_id,
                        "title": _title(name, extension),
                        "storage_name": storage_name,
                        "original_name": name,
                        "media_type": limits.PLAYBACK_MEDIA_TYPE[container],
                        "byte_size": body.byte_size,
                        "created_by": actor.user_id,
                        "request_key": key,
                        "fingerprint": fingerprint,
                        "container": container,
                        "upload_id": upload_id,
                        "piece_size": piece_size,
                        "piece_count": -(-body.byte_size // piece_size),
                    },
                    conn=conn,
                )
                assert row is not None
                await audit(conn, actor, client_id, "training.video_upload_started", str(item_id))
        except BaseException as exc:
            if opened:
                await asyncio.shield(self._forget(client_id, item_id, storage_name, upload_id))
            failure = self._storage_failure(exc, {**fields, "item_id": str(item_id)})
            if failure is not None:
                raise failure from None
            raise
        logger.info("training_video_upload_started", item_id=str(item_id), **fields, outcome="open")
        [item] = await self.library.present(client_id, [row], manage=True)
        return VideoUploadOut(
            item=item,
            piece_size=piece_size,
            piece_count=-(-body.byte_size // piece_size),
            received=[],
        )

    async def put_piece(
        self,
        actor: ConsoleActor,
        client_id: UUID,
        item_id: UUID,
        number: int,
        request: Request,
        sha256_hex: str,
    ) -> PieceOut:
        began = time.monotonic()
        fields: dict[str, Any] = {
            "item_id": str(item_id),
            "client_id": str(client_id),
            "stage": "piece",
            "number": number,
        }
        # Refuse before reading a byte of the body.
        async with tx(self.pool, str(client_id)) as conn:
            if not await can_manage(conn, actor, client_id):
                raise HTTPException(403, FORBIDDEN)
            row = await db.q.fetch_one(
                "training/video_piece_get.sql",
                {"client_id": client_id, "item_id": item_id, "number": number},
                conn=conn,
            )
        if row is None or row["kind"] != "video":
            raise HTTPException(404, NOT_FOUND)
        if row["status"] != "uploading" or not row["open"] or _closing(client_id, item_id):
            self._refused(fields, UPLOAD_CLOSED)
            raise HTTPException(409, UPLOAD_CLOSED)
        piece_size, declared = row["piece_size"], row["declared_bytes"]
        digest = sha256_hex.lower()
        if not 1 <= number <= row["piece_count"] or len(digest) != 64 or not set(digest) <= _HEX:
            self._refused(fields, PIECE_INVALID)
            raise HTTPException(422, PIECE_INVALID)
        expected = _expected_size(number, piece_size, declared)
        length = request.headers.get("content-length", "")
        if not length.isdigit() or int(length) != expected:
            self._refused(fields, PIECE_INVALID)
            raise HTTPException(422, PIECE_INVALID)
        if PIECE_SLOTS.locked():
            self._refused(fields, BUSY)
            raise HTTPException(503, BUSY, headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
        await PIECE_SLOTS.acquire()
        try:
            data = await _read_body(request, expected)
            fields["byte_size"] = len(data)
            if hashlib.sha256(data).hexdigest() != digest:
                self._refused(fields, PIECE_CORRUPT)
                raise HTTPException(422, PIECE_CORRUPT)
            if number == 1 and not _looks_like(row["container"], data):
                self._refused(fields, NOT_VIDEO)
                raise HTTPException(422, NOT_VIDEO)
            if row["recorded"] == digest:
                # Nothing to write, but a completion that has begun still closes the item.
                if _closing(client_id, item_id):
                    self._refused(fields, UPLOAD_CLOSED)
                    raise HTTPException(409, UPLOAD_CLOSED)
                outcome = "duplicate"
                received = await self._received_count(client_id, item_id)
            else:
                outcome = "stored"
                received = await self._store_piece(client_id, item_id, row, number, data, digest)
        finally:
            PIECE_SLOTS.release()
        logger.info(
            "training_video_piece_stored",
            **fields,
            duration_ms=int((time.monotonic() - began) * 1000),
            outcome=outcome,
        )
        return PieceOut(number=number, received=received)

    async def received(self, actor: ConsoleActor, client_id: UUID, item_id: UUID) -> VideoUploadOut:
        async with tx(self.pool, str(client_id)) as conn:
            if not await can_manage(conn, actor, client_id):
                raise HTTPException(403, FORBIDDEN)
            item = await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn)
            if item is None or item["kind"] != "video":
                raise HTTPException(404, NOT_FOUND)
            return await self._plan(conn, client_id, item)

    async def complete(self, actor: ConsoleActor, client_id: UUID, item_id: UUID) -> ItemOut:
        began = time.monotonic()
        fields = {"item_id": str(item_id), "client_id": str(client_id), "stage": "complete"}
        changed: list[int] = []
        job: Mapping[str, Any] | None = None
        row: Mapping[str, Any] | None = None
        async with _completing(client_id, item_id):
            async with tx(self.pool, str(client_id)) as conn:
                await lock(conn, client_id)
                if not await can_manage(conn, actor, client_id):
                    raise HTTPException(403, FORBIDDEN)
                # Locked, so a removal waits for this completion (and the reverse).
                upload = await db.q.fetch_one(
                    "training/video_upload_get.sql", (client_id, item_id), conn=conn
                )
                item = await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn)
                if item is None or item["kind"] != "video":
                    raise HTTPException(404, NOT_FOUND)
                if item["status"] != "uploading":
                    # Completed before (its answer may have been lost): answer the item.
                    [done] = await self.library.present(client_id, [item], manage=True)
                    return done
                if upload is None:
                    raise HTTPException(409, UPLOAD_CLOSED)
                pieces = await db.q.fetch_all(
                    "training/video_pieces_list.sql",
                    {"client_id": client_id, "item_id": item_id},
                    conn=conn,
                )
                if not self._whole(pieces, upload):
                    self._refused(fields, PIECE_MISSING)
                    raise HTTPException(409, PIECE_MISSING)
                parts = [
                    Part(piece["number"], piece["etag"], bytes.fromhex(piece["sha256"]))
                    for piece in pieces
                ]
                try:
                    await _finish(
                        self.video.complete_upload,
                        client_id,
                        item["storage_name"],
                        upload["upload_id"],
                        parts,
                        upload["piece_size"],
                        upload["declared_bytes"],
                    )
                except PartMismatch as mismatch:
                    # Forget those pieces (committed below) so the client sends them again.
                    changed = mismatch.numbers
                    await db.q.execute(
                        "training/video_pieces_delete.sql",
                        {"client_id": client_id, "item_id": item_id, "numbers": changed},
                        conn=conn,
                    )
                except StorageNotFound:
                    self._refused(fields, UPLOAD_CLOSED)
                    raise HTTPException(409, UPLOAD_CLOSED) from None
                except (StorageError, OSError) as exc:
                    failure = self._storage_failure(exc, fields)
                    assert failure is not None
                    raise failure from None
                else:
                    digest = hashlib.sha256(
                        "\n".join(piece["sha256"] for piece in pieces).encode()
                    ).hexdigest()
                    row = await db.q.fetch_one(
                        "training/video_complete.sql",
                        {
                            "client_id": client_id,
                            "item_id": item_id,
                            "byte_size": upload["declared_bytes"],
                            "sha256": digest,
                        },
                        conn=conn,
                    )
                    if row is None:
                        raise HTTPException(404, NOT_FOUND)
                    job = await db.q.fetch_one(
                        "training/video_job_queue.sql",
                        {"client_id": client_id, "item_id": item_id},
                        conn=conn,
                    )
                    await audit(conn, actor, client_id, "training.item_added", str(item_id))
        if changed:
            logger.info(
                "training_video_piece_refused",
                **fields,
                pieces=changed,
                outcome=PIECE_MISSING,
            )
            raise HTTPException(409, PIECE_MISSING)
        assert row is not None
        logger.info(
            "training_video_upload_completed",
            **fields,
            byte_size=row["byte_size"],
            duration_ms=int((time.monotonic() - began) * 1000),
            outcome="uploaded",
        )
        [done] = await self.library.present(client_id, [{**row, **(job or {})}], manage=True)
        return done

    async def _store_piece(
        self,
        client_id: UUID,
        item_id: UUID,
        row: Mapping[str, Any],
        number: int,
        data: bytes,
        digest: str,
    ) -> int:
        """Write the piece at the offset its number gives and record it; the received count."""
        fields = {"item_id": str(item_id), "client_id": str(client_id), "stage": "piece"}
        offset = (number - 1) * row["piece_size"]
        async with _writing(client_id, item_id):
            try:
                etag = await _finish(
                    self.video.write_part,
                    client_id,
                    row["storage_name"],
                    row["upload_id"],
                    number,
                    offset,
                    data,
                    bytes.fromhex(digest),
                )
            except PartMismatch:
                # The store found the bytes do not match their hash (S3 BadDigest).
                self._refused({**fields, "number": number}, PIECE_CORRUPT)
                raise HTTPException(422, PIECE_CORRUPT) from None
            except StorageNotFound:
                # Removed meanwhile: the local file or the S3 upload is gone.
                self._refused({**fields, "number": number}, UPLOAD_CLOSED)
                raise HTTPException(409, UPLOAD_CLOSED) from None
            except (StorageError, OSError) as exc:
                failure = self._storage_failure(exc, {**fields, "number": number})
                assert failure is not None
                raise failure from None
            async with tx(self.pool, str(client_id)) as conn:
                recorded = await db.q.fetch_one(
                    "training/video_piece_upsert.sql",
                    {
                        "client_id": client_id,
                        "item_id": item_id,
                        "number": number,
                        "byte_size": len(data),
                        "sha256": digest,
                        "etag": etag,
                    },
                    conn=conn,
                )
                pieces = (
                    await db.q.fetch_all(
                        "training/video_pieces_list.sql",
                        {"client_id": client_id, "item_id": item_id},
                        conn=conn,
                    )
                    if recorded is not None
                    else []
                )
        if recorded is None:
            # Removed after the write: sweep what the write may have left.
            self._refused({**fields, "number": number}, UPLOAD_CLOSED)
            try:
                await cleanup(self.pool, self.store, client_id)
            except Exception as failure:
                logger.error(
                    "training_cleanup_failed",
                    client_id=str(client_id),
                    outcome=type(failure).__name__,
                )
            raise HTTPException(409, UPLOAD_CLOSED)
        return len(pieces)

    async def _received_count(self, client_id: UUID, item_id: UUID) -> int:
        async with tx(self.pool, str(client_id)) as conn:
            pieces = await db.q.fetch_all(
                "training/video_pieces_list.sql",
                {"client_id": client_id, "item_id": item_id},
                conn=conn,
            )
        return len(pieces)

    async def _plan(
        self, conn: AsyncConnection[Any], client_id: UUID, item: Mapping[str, Any]
    ) -> VideoUploadOut:
        """The item with its upload's plan and received pieces (all of them once it is past
        uploading)."""
        [shown] = await self.library.present(client_id, [item], manage=True)
        upload = await db.q.fetch_one(
            "training/video_piece_get.sql",
            {"client_id": client_id, "item_id": item["id"], "number": None},
            conn=conn,
        )
        if upload is not None and upload["open"]:
            pieces = await db.q.fetch_all(
                "training/video_pieces_list.sql",
                {"client_id": client_id, "item_id": item["id"]},
                conn=conn,
            )
            return VideoUploadOut(
                item=shown,
                piece_size=upload["piece_size"],
                piece_count=upload["piece_count"],
                received=[piece["number"] for piece in pieces],
            )
        piece_size = limits.VIDEO_PIECE_BYTES
        count = -(-item["byte_size"] // piece_size)
        return VideoUploadOut(
            item=shown,
            piece_size=piece_size,
            piece_count=count,
            received=list(range(1, count + 1)),
        )

    @staticmethod
    def _whole(pieces: Sequence[Mapping[str, Any]], upload: Mapping[str, Any]) -> bool:
        """Every piece present once, each its exact size, adding up to the declared size."""
        piece_size, count, declared = (
            upload["piece_size"],
            upload["piece_count"],
            upload["declared_bytes"],
        )
        if [piece["number"] for piece in pieces] != list(range(1, count + 1)):
            return False
        sizes_fit = all(
            piece["byte_size"] == _expected_size(piece["number"], piece_size, declared)
            for piece in pieces
        )
        return sizes_fit and sum(piece["byte_size"] for piece in pieces) == declared

    async def _forget(
        self, client_id: UUID, item_id: UUID, storage_name: str, upload_id: str | None
    ) -> None:
        """After a failed start: abort the store's upload unless its row was committed after all
        (a lost COMMIT acknowledgement), which a later removal or the sweep then handles."""
        try:
            async with tx(self.pool, str(client_id)) as conn:
                kept = await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn)
            if kept is None:
                await _finish(self.video.abort_upload, client_id, storage_name, upload_id)
        except Exception as failure:
            logger.error(
                "training_abort_failed",
                item_id=str(item_id),
                client_id=str(client_id),
                outcome=type(failure).__name__,
            )

    @staticmethod
    def _refused(fields: Mapping[str, Any], code: str) -> None:
        logger.info("training_video_piece_refused", **fields, outcome=code)

    @staticmethod
    def _storage_failure(exc: BaseException, fields: Mapping[str, Any]) -> HTTPException | None:
        """503 for a store failure, logged by its type and code only; ``None`` for anything else.

        An ``OSError``'s text carries the local path, so only its error code is logged.
        """
        if isinstance(exc, HTTPException) or not isinstance(exc, StorageError | OSError):
            return None
        code = _os_code(exc) if isinstance(exc, OSError) else type(exc).__name__
        logger.error("training_storage_failed", **fields, outcome=code)
        return HTTPException(503, STORAGE_UNAVAILABLE)
