"""Bounded console uploads, durable source metadata and authorized downloads."""

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, BinaryIO
from urllib.parse import quote
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import UploadFile
from starlette.types import Receive, Scope, Send

from csa.agents.kb.extract import KbFileError, extract_document, file_kind_for, resolve_upload_path
from csa.agents.kb.lifecycle import delete_document, lock
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Role
from csa.console.multipart import MAX_FILE_BYTES, upload_form
from csa.console.services import package_gate
from csa.contracts.registry import CapabilityRegistry
from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)
MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "csv": "text/csv",
}


class FileOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    name: str
    media_type: str
    size_bytes: int
    document_id: UUID | None
    created_at: datetime


class FileRecord(FileOut):
    storage_name: str
    content_hash: str
    deleted_at: datetime | None


class UploadOut(BaseModel):
    job_id: UUID
    duplicate_request: bool


class ReconcileOut(BaseModel):
    removed_files: int


class SourceDownload(StreamingResponse):
    def __init__(self, stream: BinaryIO, record: FileRecord) -> None:
        self.stream = stream

        def chunks() -> Iterator[bytes]:
            while block := stream.read(64 * 1024):
                yield block

        super().__init__(
            chunks(),
            media_type=record.media_type,
            headers={
                "Content-Disposition": f"attachment; filename*=utf-8''{quote(record.name)}",
                "Content-Length": str(record.size_bytes),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.stream.close()


def pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool  # type: ignore[no-any-return]


async def human(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, "human session required")
    return actor


async def writer(actor: Annotated[ConsoleActor, Depends(human)]) -> ConsoleActor:
    if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}:
        raise HTTPException(403, "KB write permission required")
    return actor


async def audit(
    conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID, action: str, target: str
) -> None:
    await db.q.execute(
        "console/audit.sql", (actor.user_id, client_id, action, target, Jsonb({})), conn=conn
    )


def tenant_directory(root: Path, client_id: UUID) -> Path:
    root = root.resolve()
    directory = root / str(client_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.resolve() != directory:
        raise RuntimeError("KB tenant storage directory must not be a symlink")
    return directory


def publish(directory: Path, name: str, data: bytes) -> Path:
    final = directory / name
    temporary = directory / f".{name}.part"
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, final)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return final


async def durable_publish(directory: Path, name: str, data: bytes) -> None:
    task = asyncio.create_task(asyncio.to_thread(publish, directory, name, data))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Finish publication before releasing the tenant lock and reconciling.
        await task
        raise


async def cleanup_storage(pool: AsyncConnectionPool, root: Path, client_id: UUID) -> int:
    """Reconcile only server-generated files, under the same publication lock."""
    removed = 0
    async with tx(pool, str(client_id)) as conn:
        await lock(conn, client_id)
        rows = await db.q.fetch_all("kb/files_inventory.sql", (client_id,), conn=conn)
        keep = {r["storage_name"] for r in rows if r["deleted_at"] is None}
        directory = tenant_directory(root, client_id)
        for path in directory.iterdir():
            if re.fullmatch(r"\.?[a-f0-9-]{36}\.(pdf|docx|csv)(\.part)?", path.name):
                if path.name not in keep:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                    removed += 1
    return removed


def build_upload_router(root: Path | None) -> APIRouter:
    # Every KB file route, reads and downloads included, belongs to customer_support.
    router = APIRouter(dependencies=[Depends(package_gate("customer_support"))])

    def storage() -> Path:
        if root is None:
            raise HTTPException(503, "KB upload storage is not configured")
        return root

    async def enabled(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
    ) -> None:
        if not await CapabilityRegistry(db_pool).allows_execution(client_id, "kb_agent"):
            raise HTTPException(409, "KB capability is missing or disabled")

    @router.post(
        "/kb/uploads",
        response_model=UploadOut,
        status_code=202,
        dependencies=[Depends(writer), Depends(enabled)],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["files"],
                            "properties": {
                                "files": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 3,
                                    "items": {"type": "string", "format": "binary"},
                                }
                            },
                        }
                    }
                },
            }
        },
    )
    async def upload(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        actor: Annotated[ConsoleActor, Depends(writer)],
        directory_root: Annotated[Path, Depends(storage)],
        idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    ) -> UploadOut:
        staged: list[tuple[UUID, str, str, bytes, str]] = []
        try:
            async with upload_form(request) as form:
                for key, item in form.multi_items():
                    if key != "files" or not isinstance(item, UploadFile):
                        raise HTTPException(422, "only multipart files fields are accepted")
                    name = item.filename or ""
                    if (
                        not name.strip()
                        or len(name) > 255
                        or name != Path(name).name
                        or "\\" in name
                        or any(ord(c) < 32 for c in name)
                    ):
                        raise HTTPException(422, "invalid original filename")
                    kind = file_kind_for(name)
                    if item.size is not None and item.size > MAX_FILE_BYTES:
                        raise HTTPException(413, "file exceeds 10 MB")
                    data = await item.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES:
                        raise HTTPException(413, "file exceeds 10 MB")
                    if not data:
                        raise HTTPException(422, "file is empty")
                    await asyncio.to_thread(extract_document, data, kind, title=Path(name).stem)
                    staged.append((uuid4(), name, kind, data, hashlib.sha256(data).hexdigest()))
        except KbFileError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not staged:
            raise HTTPException(422, "upload one to three files")
        fingerprint = hashlib.sha256(
            json.dumps(
                [(name, kind, digest) for _, name, kind, _, digest in staged],
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        published: list[Path] = []
        try:
            async with tx(db_pool, str(client_id)) as conn:
                await lock(conn, client_id)
                previous = await db.q.fetch_one(
                    "kb/upload_find.sql", (client_id, idempotency_key), conn=conn
                )
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise HTTPException(409, "idempotency key already used for different files")
                    return UploadOut(job_id=previous["job_id"], duplicate_request=True)
                directory = await asyncio.to_thread(tenant_directory, directory_root, client_id)
                batch_id, job_id = uuid4(), uuid4()
                files = []
                for file_id, name, kind, data, _digest in staged:
                    storage_name = f"{file_id}.{kind}"
                    # Track the destination before publication, including fsync failures.
                    published.append(directory / storage_name)
                    await durable_publish(directory, storage_name, data)
                    files.append({"file_id": str(file_id), "path": storage_name, "name": name})
                await db.q.execute(
                    "kb/upload_job_insert.sql",
                    (job_id, client_id, Jsonb(files), Jsonb({}), str(actor.user_id)),
                    conn=conn,
                )
                await db.q.execute(
                    "kb/upload_batch_insert.sql",
                    (batch_id, client_id, idempotency_key, fingerprint, job_id),
                    conn=conn,
                )
                for file_id, name, kind, data, digest in staged:
                    await db.q.execute(
                        "kb/upload_file_insert.sql",
                        (
                            file_id,
                            client_id,
                            batch_id,
                            f"{file_id}.{kind}",
                            name,
                            MEDIA[kind],
                            len(data),
                            digest,
                        ),
                        conn=conn,
                    )
                await audit(conn, actor, client_id, "kb.upload_queued", str(job_id))
        except BaseException:
            # A lost COMMIT acknowledgement is ambiguous: reconcile against durable rows,
            # rather than removing a file that a committed job may already be reading.
            if published:
                try:
                    await asyncio.shield(cleanup_storage(db_pool, directory_root, client_id))
                except Exception:
                    logger.exception("kb_upload_reconciliation_failed", client_id=str(client_id))
            raise
        return UploadOut(job_id=job_id, duplicate_request=False)

    @router.get("/kb/files", response_model=list[FileOut], dependencies=[Depends(human)])
    async def files(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> list[FileOut]:
        async with tx(db_pool, str(client_id)) as conn:
            rows = await db.q.fetch_all("kb/files_list.sql", (client_id, limit), conn=conn)
        return [FileOut.model_validate(row) for row in rows]

    @router.get(
        "/kb/files/{file_id}/download",
        dependencies=[Depends(human)],
        response_class=StreamingResponse,
        responses={
            200: {
                "content": {
                    media: {"schema": {"type": "string", "format": "binary"}}
                    for media in MEDIA.values()
                }
            }
        },
    )
    async def download(
        file_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        directory_root: Annotated[Path, Depends(storage)],
    ) -> StreamingResponse:
        with ExitStack() as handles:
            async with tx(db_pool, str(client_id)) as conn:
                await lock(conn, client_id)
                row = await db.q.fetch_one("kb/file_get.sql", (client_id, file_id), conn=conn)
                if row is None or row["deleted_at"] is not None:
                    raise HTTPException(404, "source file not found")
                record = FileRecord.model_validate(row)
                try:
                    path = resolve_upload_path(directory_root, client_id, record.storage_name)
                    stream = handles.enter_context(path.open("rb"))
                except (KbFileError, OSError) as exc:
                    logger.exception(
                        "kb_source_missing", file_id=str(file_id), client_id=str(client_id)
                    )
                    raise HTTPException(
                        503, "source file unavailable; storage repair required"
                    ) from exc
            # Deletion may unlink the source without breaking an authorized download.
            response = SourceDownload(stream, record)
            handles.pop_all()
            return response

    @router.delete(
        "/kb/files/{file_id}", status_code=204, dependencies=[Depends(writer), Depends(enabled)]
    )
    async def remove_file(
        file_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        actor: Annotated[ConsoleActor, Depends(writer)],
        directory_root: Annotated[Path, Depends(storage)],
    ) -> None:
        async with tx(db_pool, str(client_id)) as conn:
            await lock(conn, client_id)
            row = await db.q.fetch_one("kb/file_delete.sql", (client_id, file_id), conn=conn)
            if row is None:
                raise HTTPException(404, "source file not found")
            if row["document_id"]:
                await delete_document(conn, client_id, row["document_id"])
            await audit(conn, actor, client_id, "kb.source_deleted", str(file_id))
        await cleanup_storage(db_pool, directory_root, client_id)

    @router.delete(
        "/kb/documents/{document_id}",
        status_code=204,
        dependencies=[Depends(writer), Depends(enabled)],
    )
    async def remove_document(
        document_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        actor: Annotated[ConsoleActor, Depends(writer)],
    ) -> None:
        async with tx(db_pool, str(client_id)) as conn:
            if not await delete_document(conn, client_id, document_id):
                raise HTTPException(404, "document not found")
            await audit(conn, actor, client_id, "kb.document_deleted", str(document_id))
        if root:
            await cleanup_storage(db_pool, root, client_id)

    @router.post("/kb/storage/reconcile", response_model=ReconcileOut)
    async def reconcile(
        client_id: Annotated[UUID, Depends(tenant)],
        db_pool: Annotated[AsyncConnectionPool, Depends(pool)],
        actor: Annotated[ConsoleActor, Depends(writer)],
        directory_root: Annotated[Path, Depends(storage)],
    ) -> ReconcileOut:
        removed = await cleanup_storage(db_pool, directory_root, client_id)
        async with tx(db_pool, str(client_id)) as conn:
            await audit(conn, actor, client_id, "kb.storage_reconciled", str(client_id))
        return ReconcileOut(removed_files=removed)

    return router
