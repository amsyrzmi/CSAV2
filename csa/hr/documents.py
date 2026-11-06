"""Employee documents: checked uploads, retry-safe storage, listing, download and soft remove.

The database decides who may see, add or remove which document (`hr_document_*`); this module
checks the file itself (its extension must agree with its content), stores it
as `<tenant>/<uuid>.<ext>` in the HR object store and keeps the file and its row in step. The
KB upload code supplies the download response; nothing here scans a file for viruses or
encrypts it on disk (see docs/hr/SETUP.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import zipfile
from contextlib import ExitStack
from pathlib import Path
from typing import Literal, get_args
from uuid import UUID, uuid4

import psycopg
import structlog
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, Field
from starlette.datastructures import UploadFile

from csa.console.kb_uploads import FileRecord, SourceDownload
from csa.console.multipart import MAX_FILE_BYTES, upload_form
from csa.core.db import q as sql
from csa.core.tenancy import tx
from csa.hr.models import HrModel
from csa.hr.operations import HrOperations
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService
from csa.storage.base import ObjectStore, StorageError, durable_put

logger = structlog.get_logger(__name__)

DocumentKind = Literal["contract", "identity", "certificate", "other"]
DocumentExtension = Literal["pdf", "jpg", "png", "docx"]

__all__ = [
    "MAX_UPLOAD_BODY_BYTES",
    "MEDIA",
    "DocumentKind",
    "DocumentList",
    "DocumentOut",
    "HrDocuments",
    "cleanup",
    "sniff",
]

# One file plus its multipart framing.
MAX_UPLOAD_BODY_BYTES = MAX_FILE_BYTES + 65536
MEDIA: dict[str, str] = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "png": "image/png",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_EXTENSIONS = {".pdf": "pdf", ".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".docx": "docx"}
_MAGIC = {"pdf": b"%PDF-", "jpg": b"\xff\xd8\xff", "png": b"\x89PNG\r\n\x1a\n"}
_STORED = re.compile(r"\.?[a-f0-9-]{36}\.(" + "|".join(get_args(DocumentExtension)) + r")(\.part)?")
_LOCK = ":hr-documents"  # shared with hr_document_add and hr_document_remove


class DocumentOut(HrModel):
    document_id: UUID
    kind: DocumentKind
    file_name: str
    media_type: str
    byte_size: int = Field(ge=1, le=MAX_FILE_BYTES)
    uploaded_at: AwareDatetime
    uploaded_by_name: str | None


class DocumentList(HrModel):
    items: list[DocumentOut] = Field(max_length=500)


def sniff(data: bytes, file_name: str) -> DocumentExtension:
    """The file's type, when its extension agrees with its content; otherwise a refusal."""
    if not data:
        raise HrPolicyError("hr_document_empty")
    suffix = Path(file_name).suffix.lower()
    ext = _EXTENSIONS.get(suffix)
    if ext is None:
        raise HrPolicyError("hr_document_type_unsupported")
    if ext == "docx":
        if not data.startswith(b"PK\x03\x04"):
            raise HrPolicyError("hr_document_type_unsupported")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise HrPolicyError("hr_document_type_unsupported")
        except zipfile.BadZipFile:
            raise HrPolicyError("hr_document_type_unsupported") from None
    elif not data.startswith(_MAGIC[ext]):
        raise HrPolicyError("hr_document_type_unsupported")
    return ext  # type: ignore[return-value]


def _checked_name(item: UploadFile) -> str:
    name = item.filename or ""
    if (
        not name.strip()
        or len(name) > 255
        or name != Path(name).name
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise HrPolicyError("hr_document_name_invalid")
    return name


async def cleanup(pool: AsyncConnectionPool, store: ObjectStore, client_id: UUID) -> int:
    """Remove files of this tenant that no document row keeps, under the document lock."""
    removed = 0
    async with tx(pool, str(client_id)) as conn:
        await sql.execute("hr/key_lock.sql", (f"{client_id}{_LOCK}",), conn=conn)
        rows = await sql.fetch_all("hr/document_names.sql", (client_id,), conn=conn)
        keep = {row["storage_name"] for row in rows}
        for name in await asyncio.to_thread(store.list_names, client_id):
            if _STORED.fullmatch(name) and name not in keep:
                await asyncio.to_thread(store.delete, client_id, name)
                removed += 1
    return removed


class HrDocuments:
    def __init__(self, core: HrService, store: ObjectStore):
        self.core = core
        self.store = store

    @staticmethod
    def _out(row: dict) -> DocumentOut:  # type: ignore[type-arg]
        return DocumentOut(
            document_id=row["document_id"],
            kind=row["kind"],
            file_name=row["file_name"],
            media_type=row["media_type"],
            byte_size=row["byte_size"],
            uploaded_at=row["uploaded_at"],
            uploaded_by_name=row["uploaded_by_name"],
        )

    async def for_person(self, session: HrSession, employee_id: UUID) -> DocumentList:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            try:
                rows = await sql.fetch_all(
                    "hr/document_rows.sql", (session.tenant_id, employee_id), conn=conn
                )
            except psycopg.errors.InsufficientPrivilege:
                raise HrPolicyError("permission_denied") from None
            except psycopg.errors.CheckViolation:
                # hr_document_unavailable: the person is not in this tenant.
                raise HrPolicyError("resource_unavailable") from None
            await self.core._session(conn, session)
        return DocumentList(items=[self._out({**row, "document_id": row["id"]}) for row in rows])

    async def upload(
        self,
        session: HrSession,
        request: Request,
        employee_id: UUID,
        kind: DocumentKind,
        key: UUID,
    ) -> DocumentOut:
        if session.csrf_hash is None or session.capability is not None or session.assistant:
            raise HrPolicyError("human_operation_required")
        async with upload_form(
            request, max_files=1, max_body=MAX_UPLOAD_BODY_BYTES, field="file"
        ) as form:
            items = form.multi_items()
            if len(items) != 1 or not isinstance(items[0][1], UploadFile):
                raise HTTPException(422, "upload one file")
            item = items[0][1]
            name = _checked_name(item)
            if item.size is not None and item.size > MAX_FILE_BYTES:
                raise HTTPException(413, "file exceeds 10 MB")
            data = await item.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, "file exceeds 10 MB")
        ext = sniff(data, name)
        storage_name = f"{uuid4()}.{ext}"
        published: list[str] = []
        try:
            async with tx(self.core.pool, str(session.tenant_id)) as conn:
                access = await self.core._access(conn, session)
                HrOperations.people_manager(access)
                # Serialise with cleanup: a stored file is never swept before its row commits.
                await sql.execute("hr/key_lock.sql", (f"{session.tenant_id}{_LOCK}",), conn=conn)
                # Track the name before publication, including failures part-way through.
                published.append(storage_name)
                await durable_put(self.store, session.tenant_id, storage_name, data)
                row = await HrOperations._people_apply(
                    conn,
                    "hr/document_add.sql",
                    (
                        session.tenant_id,
                        key,
                        employee_id,
                        uuid4(),
                        kind,
                        name,
                        storage_name,
                        MEDIA[ext],
                        len(data),
                        hashlib.sha256(data).hexdigest(),
                    ),
                )
                receipt = row["receipt"]
                if receipt["duplicate"]:
                    # A retry: the first request's file is kept, this copy is not referenced.
                    try:
                        await asyncio.to_thread(self.store.delete, session.tenant_id, storage_name)
                    except Exception:
                        # The first file is safe; a later cleanup removes this copy.
                        logger.exception("hr_document_duplicate_cleanup_failed")
                await self.core._session(conn, session)
        except BaseException as exc:
            # A lost COMMIT acknowledgement is ambiguous: reconcile against the durable rows
            # instead of removing a file that a committed row may already point at.
            if published:
                try:
                    await asyncio.shield(cleanup(self.core.pool, self.store, session.tenant_id))
                except Exception:
                    logger.exception("hr_document_reconciliation_failed")
            if isinstance(exc, StorageError):
                # Throttling or a timeout after the client's retries: the caller may retry.
                logger.error("hr_document_storage_failed")
                raise HTTPException(503, "hr_document_storage_unavailable") from exc
            raise
        return self._out(receipt)

    async def download(self, session: HrSession, document_id: UUID) -> StreamingResponse:
        with ExitStack() as handles:
            async with tx(self.core.pool, str(session.tenant_id)) as conn:
                await self.core._access(conn, session)
                await sql.execute("hr/key_lock.sql", (f"{session.tenant_id}{_LOCK}",), conn=conn)
                row = await sql.fetch_one(
                    "hr/document_get.sql", (session.tenant_id, document_id), conn=conn
                )
                if row is None:
                    raise HrPolicyError("resource_unavailable")
                try:
                    stream = await asyncio.to_thread(
                        self.store.open, session.tenant_id, row["storage_name"]
                    )
                except (OSError, StorageError):
                    logger.exception("hr_document_file_missing", document_id=str(document_id))
                    raise HTTPException(503, "hr_document_file_unavailable") from None
                handles.enter_context(stream)
                await self.core._session(conn, session)
            record = FileRecord(
                id=row["id"],
                name=row["file_name"],
                media_type=row["media_type"],
                size_bytes=row["byte_size"],
                document_id=None,
                created_at=row["uploaded_at"],
                storage_name=row["storage_name"],
                content_hash=row["sha256"],
                deleted_at=None,
            )
            # The open handle outlives a later unlink, so a removal never breaks this download.
            response = SourceDownload(stream, record)
            handles.pop_all()
            return response

    async def remove(self, session: HrSession, document_id: UUID) -> None:
        if session.csrf_hash is None or session.capability is not None or session.assistant:
            raise HrPolicyError("human_operation_required")
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            await HrOperations._people_apply(
                conn, "hr/document_remove.sql", (session.tenant_id, document_id)
            )
            await self.core._session(conn, session)
        try:
            await cleanup(self.core.pool, self.store, session.tenant_id)
        except Exception:
            # The row is already removed; a later cleanup deletes the file.
            logger.exception("hr_document_cleanup_failed")
