"""The training library: checked PDF uploads, retry-safe storage, listing, edits, removal, download.

Every row and every file belongs to one company. Rows are read and written inside ``tx`` for that
company (row security on ``app.client_id``) and every query also names the company. Files are
stored as ``<company>/<uuid>.pdf`` in the training store under a generated name; the uploaded
file name is kept as data only.

An upload is checked before any database work: the multipart body is bounded, the name and the
first bytes are checked here, then the PDF is parsed in a limited child process
(``pdf_guard.check_pdf_isolated``), so no pooled connection or lock is held across the child. The
idempotency check, the quota, the file write (``durable_put``) and the row insert then run under
one advisory lock per company, and any failure after the write reconciles the store against the
committed rows (``cleanup``), as the HR documents do. A removal deletes the row, then sweeps.
Reads (the list, a download) never take the lock, and a download holds no connection while the
object is opened or streamed.

Logs carry the item id, the company id, byte counts and an outcome: never a title, a file name or
any of the file's text.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from typing import Any, BinaryIO
from urllib.parse import quote
from uuid import UUID, uuid4

import structlog
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from starlette.datastructures import UploadFile

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import FileRecord, SourceDownload, audit
from csa.console.multipart import upload_form
from csa.core import db
from csa.core.tenancy import tx
from csa.storage.base import ObjectStore, StorageError, durable_put
from csa.training import limits
from csa.training.access import can_manage
from csa.training.limits import MAX_FILE_BYTES
from csa.training.models import TITLE_MAX_CHARS, ItemList, ItemOut, ItemPatch
from csa.training.pdf import PDF_MAGIC, PdfRefused, check_file_name
from csa.training.pdf_guard import PdfGuardError, check_pdf_isolated

logger = structlog.get_logger(__name__)

__all__ = [
    "FORBIDDEN",
    "NOT_FOUND",
    "STORAGE_UNAVAILABLE",
    "TrainingFile",
    "TrainingLibrary",
    "cleanup",
]

MEDIA_TYPE = "application/pdf"
# One file plus its multipart framing.
MAX_UPLOAD_BODY_BYTES = MAX_FILE_BYTES + 65536
# Covers waiting for one of the parser's child slots as well as the parse itself.
PARSE_TIMEOUT_SECONDS = 30
# Only names this module generates are ever swept (and the local store's temporary files).
STORED = re.compile(r"\.?[a-f0-9-]{36}\.pdf(\.part)?")

FORBIDDEN = "training_forbidden"
NOT_FOUND = "training_not_found"
STORAGE_UNAVAILABLE = "training_storage_unavailable"
PARSER_UNAVAILABLE = "training_parser_unavailable"
TOO_LARGE = "training_file_too_large"
LIBRARY_FULL = "training_library_full"
KEY_CONFLICT = "training_key_conflict"
NAME_INVALID = "training_name_invalid"
NOT_PDF = "training_not_pdf"
UPLOAD_INVALID = "training_upload_invalid"

# The shared parser's own refusals, by their text (csa/console/multipart.py).
_PARSER_BAD_NAME = "invalid original filename"
_PARSER_BIG_HEADERS = "multipart headers exceed 8 KB"


def _lock_key(client_id: UUID) -> str:
    return f"{client_id}:training"


async def _lock(conn: AsyncConnection[Any], client_id: UUID) -> None:
    await db.q.execute("training/lock.sql", (_lock_key(client_id),), conn=conn)


async def cleanup(pool: AsyncConnectionPool, store: ObjectStore, client_id: UUID) -> int:
    """Delete this company's stored files that no row keeps, under the company lock.

    Only names this module generates are touched; anything else in the prefix stays.
    """
    removed = 0
    async with tx(pool, str(client_id)) as conn:
        await _lock(conn, client_id)
        rows = await db.q.fetch_all("training/names.sql", (client_id,), conn=conn)
        keep = {row["storage_name"] for row in rows}
        for name in await asyncio.to_thread(store.list_names, client_id):
            if STORED.fullmatch(name) and name not in keep:
                await asyncio.to_thread(store.delete, client_id, name)
                removed += 1
    return removed


class TrainingFile(SourceDownload):
    """The stored PDF, streamed in 64 KB blocks, shown in the page rather than saved.

    The open handle outlives a later delete, so a removal never breaks a download in progress.
    """

    def __init__(self, stream: BinaryIO, record: FileRecord) -> None:
        super().__init__(stream, record)
        self.headers["Content-Disposition"] = f"inline; filename*=utf-8''{quote(record.name)}"
        self.headers["Cache-Control"] = "private, no-store"
        self.headers["Content-Security-Policy"] = "sandbox"


def _refusal(exc: HTTPException) -> HTTPException:
    """The shared parser's refusal under this library's codes."""
    if isinstance(exc.detail, str) and exc.detail.startswith("training_"):
        return exc
    if exc.status_code == 413:
        return HTTPException(
            413, UPLOAD_INVALID if exc.detail == _PARSER_BIG_HEADERS else TOO_LARGE
        )
    if exc.detail == _PARSER_BAD_NAME:
        return HTTPException(422, NAME_INVALID)
    return HTTPException(exc.status_code, UPLOAD_INVALID)


async def _read_upload(request: Request) -> tuple[str, bytes]:
    """The one uploaded file's name and bytes, read within the size bounds."""
    try:
        async with upload_form(
            request,
            max_files=1,
            max_body=MAX_UPLOAD_BODY_BYTES,
            field="file",
            max_file=MAX_FILE_BYTES,
        ) as form:
            items = form.multi_items()
            if len(items) != 1 or not isinstance(items[0][1], UploadFile):
                raise HTTPException(422, UPLOAD_INVALID)
            item = items[0][1]
            if item.size is not None and item.size > MAX_FILE_BYTES:
                raise HTTPException(413, TOO_LARGE)
            name = item.filename or ""
            data = await item.read(MAX_FILE_BYTES + 1)
    except HTTPException as exc:
        raise _refusal(exc) from None
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, TOO_LARGE)
    return name, data


async def _check(client_id: UUID, name: str, data: bytes) -> tuple[int, bool]:
    """Page count and landscape, or a refusal. Runs before any database work."""
    try:
        check_file_name(name)
        if not data.startswith(PDF_MAGIC):
            raise PdfRefused(NOT_PDF)
        try:
            async with asyncio.timeout(PARSE_TIMEOUT_SECONDS):
                info = await check_pdf_isolated(data, name)
        except (TimeoutError, PdfGuardError) as exc:
            logger.error(
                "training_parser_unavailable",
                client_id=str(client_id),
                byte_size=len(data),
                outcome=type(exc).__name__,
            )
            raise HTTPException(503, PARSER_UNAVAILABLE) from None
    except PdfRefused as refused:
        logger.info(
            "training_upload_refused",
            client_id=str(client_id),
            byte_size=len(data),
            outcome=refused.code,
        )
        raise HTTPException(422, refused.code) from None
    return info.page_count, info.landscape


def _default_title(name: str) -> str:
    """The file name without its extension, cut to the title limit."""
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    title = stem.strip()[:TITLE_MAX_CHARS].strip()
    return title or name.strip()[:TITLE_MAX_CHARS]


def _categories(items: Iterable[ItemOut]) -> list[str]:
    """Each non-empty category once, matched case-insensitively, in alphabetical order."""
    seen: dict[str, str] = {}
    for item in items:
        if item.category:
            key = item.category.casefold()
            seen[key] = min(seen.get(key, item.category), item.category)
    return [seen[key] for key in sorted(seen)]


class TrainingLibrary:
    """One company's library, for one request.

    ``identity_pool`` reads the uploaders' display names, which ``csa_app`` cannot see; without
    it the names are left out.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        store: ObjectStore,
        identity_pool: AsyncConnectionPool | None = None,
    ) -> None:
        self.pool = pool
        self.store = store
        self.identity_pool = identity_pool

    async def list_items(self, actor: ConsoleActor, client_id: UUID) -> ItemList:
        async with tx(self.pool, str(client_id)) as conn:
            manage = await can_manage(conn, actor, client_id)
            rows = await db.q.fetch_all("training/list.sql", (client_id,), conn=conn)
        items = await self._items(client_id, rows)
        return ItemList(items=items, categories=_categories(items), can_manage=manage)

    async def add_item(
        self, actor: ConsoleActor, client_id: UUID, request: Request, key: UUID
    ) -> ItemOut:
        # Refuse a reader before reading a byte of the body or starting a parser.
        async with tx(self.pool, str(client_id)) as conn:
            allowed = await can_manage(conn, actor, client_id)
        if not allowed:
            raise HTTPException(403, FORBIDDEN)
        name, data = await _read_upload(request)
        page_count, landscape = await _check(client_id, name, data)
        digest = hashlib.sha256(data).hexdigest()
        # The same key must carry the same file under the same name to count as a retry.
        fingerprint = hashlib.sha256(f"{name}\n{digest}".encode()).hexdigest()
        storage_name = f"{uuid4()}.pdf"
        published: list[str] = []
        row: Mapping[str, Any] | None = None
        duplicate = False
        try:
            async with tx(self.pool, str(client_id)) as conn:
                await _lock(conn, client_id)
                # Asked again: the HR owner may have moved while the file was being checked.
                if not await can_manage(conn, actor, client_id):
                    raise HTTPException(403, FORBIDDEN)
                row = await db.q.fetch_one("training/find_key.sql", (client_id, key), conn=conn)
                duplicate = row is not None
                if row is not None:
                    if row["fingerprint"] != fingerprint:
                        raise HTTPException(409, KEY_CONFLICT)
                else:
                    usage = await db.q.fetch_one("training/usage.sql", (client_id,), conn=conn)
                    if (
                        usage["items"] + 1 > limits.MAX_ITEMS
                        or usage["bytes"] + len(data) > limits.MAX_TOTAL_BYTES
                    ):
                        raise HTTPException(409, LIBRARY_FULL)
                    # Track the name before publication, including failures part-way through.
                    published.append(storage_name)
                    await durable_put(self.store, client_id, storage_name, data)
                    row = await db.q.fetch_one(
                        "training/insert.sql",
                        {
                            "id": uuid4(),
                            "client_id": client_id,
                            "title": _default_title(name),
                            "view_mode": "slides" if landscape else "document",
                            "storage_name": storage_name,
                            "original_name": name,
                            "media_type": MEDIA_TYPE,
                            "byte_size": len(data),
                            "sha256": digest,
                            "page_count": page_count,
                            "created_by": actor.user_id,
                            "request_key": key,
                            "fingerprint": fingerprint,
                        },
                        conn=conn,
                    )
                    assert row is not None
                    await audit(conn, actor, client_id, "training.item_added", str(row["id"]))
        except BaseException as exc:
            # A lost COMMIT acknowledgement is ambiguous: reconcile against the durable rows
            # instead of removing a file that a committed row may already point at.
            if published:
                try:
                    await asyncio.shield(cleanup(self.pool, self.store, client_id))
                except Exception as failure:
                    logger.error(
                        "training_reconciliation_failed",
                        client_id=str(client_id),
                        outcome=type(failure).__name__,
                    )
            if isinstance(exc, StorageError):
                # Throttling or a timeout after the client's retries: the caller may retry.
                logger.error(
                    "training_storage_failed", client_id=str(client_id), byte_size=len(data)
                )
                raise HTTPException(503, STORAGE_UNAVAILABLE) from exc
            raise
        assert row is not None
        logger.info(
            "training_item_added",
            item_id=str(row["id"]),
            client_id=str(client_id),
            byte_size=len(data),
            outcome="duplicate" if duplicate else "stored",
        )
        [item] = await self._items(client_id, [row])
        return item

    async def patch_item(
        self, actor: ConsoleActor, client_id: UUID, item_id: UUID, patch: ItemPatch
    ) -> ItemOut:
        async with tx(self.pool, str(client_id)) as conn:
            if not await can_manage(conn, actor, client_id):
                raise HTTPException(403, FORBIDDEN)
            row = await db.q.fetch_one(
                "training/patch.sql",
                {**patch.model_dump(), "client_id": client_id, "id": item_id},
                conn=conn,
            )
            if row is None:
                raise HTTPException(404, NOT_FOUND)
            await audit(conn, actor, client_id, "training.item_edited", str(item_id))
        [item] = await self._items(client_id, [row])
        return item

    async def remove_item(self, actor: ConsoleActor, client_id: UUID, item_id: UUID) -> None:
        async with tx(self.pool, str(client_id)) as conn:
            if not await can_manage(conn, actor, client_id):
                raise HTTPException(403, FORBIDDEN)
            row = await db.q.fetch_one("training/delete.sql", (client_id, item_id), conn=conn)
            if row is None:
                raise HTTPException(404, NOT_FOUND)
            await audit(conn, actor, client_id, "training.item_removed", str(item_id))
        logger.info(
            "training_item_removed",
            item_id=str(item_id),
            client_id=str(client_id),
            byte_size=row["byte_size"],
        )
        try:
            await cleanup(self.pool, self.store, client_id)
        except Exception as failure:
            # The row is already removed; a later sweep deletes the file.
            logger.error(
                "training_cleanup_failed", client_id=str(client_id), outcome=type(failure).__name__
            )

    async def open_file(self, client_id: UUID, item_id: UUID) -> StreamingResponse:
        """Stream the item's PDF. Reading never waits for the company lock or holds a
        connection while the object is opened or sent."""
        row = await self._row(client_id, item_id)
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        try:
            stream = await asyncio.to_thread(self.store.open, client_id, row["storage_name"])
        except (OSError, StorageError):
            # A removal may have committed and swept between the read and the open.
            if await self._row(client_id, item_id) is None:
                raise HTTPException(404, NOT_FOUND) from None
            logger.error("training_file_missing", item_id=str(item_id), client_id=str(client_id))
            raise HTTPException(503, STORAGE_UNAVAILABLE) from None
        with ExitStack() as handles:
            handles.enter_context(stream)
            record = FileRecord(
                id=row["id"],
                name=row["original_name"],
                media_type=MEDIA_TYPE,
                size_bytes=row["byte_size"],
                document_id=None,
                created_at=row["created_at"],
                storage_name=row["storage_name"],
                content_hash=row["sha256"],
                deleted_at=None,
            )
            # The open handle outlives a later delete, so a removal never breaks this download.
            response = TrainingFile(stream, record)
            handles.pop_all()
            return response

    async def _row(self, client_id: UUID, item_id: UUID) -> Mapping[str, Any] | None:
        async with tx(self.pool, str(client_id)) as conn:
            return await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn)

    async def _items(self, client_id: UUID, rows: list[Mapping[str, Any]]) -> list[ItemOut]:
        names = await self._names(client_id, {row["created_by"] for row in rows} - {None})
        return [
            ItemOut(
                id=row["id"],
                title=row["title"],
                description=row["description"],
                category=row["category"],
                view_mode=row["view_mode"],
                page_count=row["page_count"],
                byte_size=row["byte_size"],
                created_at=row["created_at"],
                uploaded_by_name=names.get(row["created_by"]),
                has_summary=row["has_summary"],
            )
            for row in rows
        ]

    async def _names(self, client_id: UUID, user_ids: set[UUID]) -> dict[UUID, str]:
        """Display names of the company's members among ``user_ids``; a name is decoration."""
        if not user_ids or self.identity_pool is None:
            return {}
        try:
            async with tx(self.identity_pool, str(client_id)) as conn:
                rows = await db.q.fetch_all(
                    "training/uploader_names.sql",
                    (client_id, sorted(user_ids, key=str)),
                    conn=conn,
                )
        except Exception as failure:
            logger.warning(
                "training_names_unavailable",
                client_id=str(client_id),
                outcome=type(failure).__name__,
            )
            return {}
        return {row["user_id"]: row["display_name"] for row in rows}
