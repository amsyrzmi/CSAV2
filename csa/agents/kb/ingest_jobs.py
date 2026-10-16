"""Queue processor for console knowledge base uploads (LG13).

The console stores up to ``KB_MAX_FILES_PER_JOB`` files under
``<KB_UPLOAD_ROOT>/<client_id>/`` and inserts a ``kb_ingest_jobs`` row with
``source='upload'`` and ``files`` set to a list of relative paths (or objects
with ``path`` and optional ``name``/``title``/``category``/``language``).

Each job is claimed with ``FOR UPDATE SKIP LOCKED`` inside the tenant's RLS
transaction, every file is validated, extracted, embedded and stored
independently, and the job finishes as ``done``, ``partial`` or ``failed`` with
per-file results in ``meta.results``. Embedding outages requeue the job with
backoff; files already stored become duplicates on retry, so reprocessing is
idempotent.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from csa.agents.kb.embedder import KbEmbedder, KbEmbeddingError
from csa.agents.kb.extract import (
    KbFileError,
    extract_document,
    file_kind_for,
    read_upload,
    resolve_upload_path,
)
from csa.agents.kb.lifecycle import check_ingestion
from csa.agents.kb.store import store_document
from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

JobStatus = Literal["done", "partial", "failed", "queued"]
_KNOWN_SUFFIXES = frozenset({".pdf", ".docx", ".csv"})


class KbUploadFile(BaseModel):
    """One entry of ``kb_ingest_jobs.files``."""

    model_config = ConfigDict(extra="ignore")

    path: str = Field(min_length=1, max_length=1024)
    name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=10)
    file_id: UUID | None = None


class KbJobMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str | None = Field(default=None, max_length=64)
    language: str = Field(default="en", max_length=10)


@dataclass(frozen=True)
class KbIngestConfig:
    upload_root: Path
    max_files: int = 3
    max_file_bytes: int = 10 * 1024 * 1024
    max_attempts: int = 3
    stale_after_seconds: float = 900.0
    retry_backoff_seconds: float = 60.0


def parse_job_files(raw: Any) -> list[KbUploadFile]:
    if not isinstance(raw, list):
        raise KbFileError("job files must be a list")
    files: list[KbUploadFile] = []
    for entry in raw:
        try:
            if isinstance(entry, str):
                files.append(KbUploadFile(path=entry))
            else:
                files.append(KbUploadFile.model_validate(entry))
        except ValidationError as exc:
            raise KbFileError(f"invalid file entry: {exc.errors()[0]['msg']}") from exc
    return files


class KbIngestProcessor:
    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        *,
        embedder: KbEmbedder,
        config: KbIngestConfig,
    ) -> None:
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._embedder = embedder
        self._config = config

    async def run_once(self) -> int:
        """Process at most one queued job per active tenant; return jobs handled."""
        async with self._owner_pool.connection() as conn:
            clients = await db.q.fetch_all("jobs/list_active_clients.sql", conn=conn)
        handled = 0
        for client in clients:
            job = await self.claim(client["id"])
            if job is not None:
                await self.process(job)
                handled += 1
        return handled

    async def claim(self, client_id: UUID) -> dict[str, Any] | None:
        cfg = self._config
        async with tx(self._app_pool, str(client_id)) as conn:
            await db.q.fetch_all(
                "kb/requeue_stale_ingest_jobs.sql",
                (
                    cfg.max_attempts,
                    cfg.max_attempts,
                    cfg.max_attempts,
                    client_id,
                    cfg.stale_after_seconds,
                ),
                conn=conn,
            )
            row = await db.q.fetch_one("kb/claim_ingest_job.sql", (client_id,), conn=conn)
        return dict(row) if row is not None else None

    async def process(self, job: dict[str, Any]) -> JobStatus:
        client_id: UUID = job["client_id"]
        job_id: UUID = job["id"]
        log = logger.bind(client_id=str(client_id), job_id=str(job_id))
        try:
            files = parse_job_files(job["files"])
            if not files:
                raise KbFileError("job has no files")
            if len(files) > self._config.max_files:
                raise KbFileError(
                    f"job has {len(files)} files; the maximum is {self._config.max_files}"
                )
            meta = KbJobMeta.model_validate(job.get("meta") or {})
        except (KbFileError, ValidationError) as exc:
            await self._finish(client_id, job_id, "failed", str(exc), [])
            log.warning("kb_ingest_job_rejected", error=str(exc))
            return "failed"

        results: list[dict[str, Any]] = []
        try:
            for upload in files:
                results.append(await self._ingest_file(client_id, upload, meta, job["created_at"]))
        except KbEmbeddingError as exc:
            status = await self._release(client_id, job_id, int(job["attempts"]), str(exc))
            log.error("kb_ingest_embedding_failed", error=str(exc), status=status)
            return status
        except Exception as exc:
            error = f"ingestion failed: {type(exc).__name__}"
            await self._finish(client_id, job_id, "failed", error, results)
            log.exception("kb_ingest_job_failed")
            return "failed"

        failed = [r for r in results if r["status"] == "failed"]
        status: JobStatus
        if not failed:
            status = "done"
        elif len(failed) == len(results):
            status = "failed"
        else:
            status = "partial"
        error = "; ".join(f"{r['file']}: {r['error']}" for r in failed)[:500] or None
        await self._finish(client_id, job_id, status, error, results)
        log.info(
            "kb_ingest_job_finished",
            status=status,
            files=len(results),
            failed=len(failed),
        )
        return status

    async def _ingest_file(
        self, client_id: UUID, upload: KbUploadFile, meta: KbJobMeta, queued_at: datetime
    ) -> dict[str, Any]:
        display = upload.name or Path(upload.path).name
        result: dict[str, Any] = {"file": display, "path": upload.path}
        try:
            kind = file_kind_for(display)
            path = resolve_upload_path(self._config.upload_root, client_id, upload.path)
            if path.suffix.lower() in _KNOWN_SUFFIXES and file_kind_for(path.name) != kind:
                raise KbFileError("file name and stored file type do not match")
            data = await asyncio.to_thread(read_upload, path, self._config.max_file_bytes)
            content_hash = hashlib.sha256(data).hexdigest()

            async with tx(self._app_pool, str(client_id)) as conn:
                await check_ingestion(conn, client_id, content_hash, queued_at, upload.file_id)
                existing = await db.q.fetch_one(
                    "kb/find_document_by_hash.sql", (client_id, content_hash), conn=conn
                )
                if existing is not None and upload.file_id:
                    await db.q.execute(
                        "kb/file_attach.sql",
                        (existing["id"], client_id, upload.file_id),
                        conn=conn,
                    )
            if existing is not None:
                return result | {
                    "status": "duplicate",
                    "document_id": str(existing["id"]),
                    "chunks": int(existing["chunk_count"]),
                    "version": int(existing["version"]),
                }

            title = (upload.title or Path(display).stem or display).strip()[:255]
            document = await asyncio.to_thread(extract_document, data, kind, title=title)
        except KbFileError as exc:
            return result | {"status": "failed", "error": str(exc)}
        except FileNotFoundError:
            logger.warning(
                "kb_source_removed_before_read",
                client_id=str(client_id),
                file_id=str(upload.file_id) if upload.file_id else None,
            )
            return result | {
                "status": "failed",
                "error": "source file was deleted or is unavailable",
            }

        embeddings = await self._embedder.embed_batch([c.content for c in document.chunks])
        try:
            async with tx(self._app_pool, str(client_id)) as conn:
                await check_ingestion(conn, client_id, content_hash, queued_at, upload.file_id)
                stored = await store_document(
                    conn,
                    client_id=client_id,
                    title=title,
                    chunks=document.chunks,
                    embeddings=embeddings,
                    content_hash=content_hash,
                    embed_model=self._embedder.model_name,
                    kind=document.kind,
                    category=upload.category or meta.category,
                    language=upload.language or meta.language,
                    source_uri=f"file:{upload.file_id}"
                    if upload.file_id
                    else f"upload:{upload.path}",
                    file_name=display,
                )
                if upload.file_id:
                    await db.q.execute(
                        "kb/file_attach.sql",
                        (stored.document_id, client_id, upload.file_id),
                        conn=conn,
                    )
        except KbFileError as exc:
            return result | {"status": "failed", "error": str(exc)}
        return result | {
            "status": stored.outcome,
            "document_id": str(stored.document_id),
            "chunks": stored.chunk_count,
            "version": stored.version,
            "replaced": [str(d) for d in stored.replaced_document_ids],
        }

    async def _finish(
        self,
        client_id: UUID,
        job_id: UUID,
        status: JobStatus,
        error: str | None,
        results: list[dict[str, Any]],
    ) -> None:
        async with tx(self._app_pool, str(client_id)) as conn:
            await db.q.fetch_one(
                "kb/finish_ingest_job.sql",
                (status, error, Jsonb({"results": results}), client_id, job_id),
                conn=conn,
            )

    async def _release(self, client_id: UUID, job_id: UUID, attempts: int, error: str) -> JobStatus:
        cfg = self._config
        backoff = cfg.retry_backoff_seconds * max(1, attempts)
        async with tx(self._app_pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "kb/release_ingest_job.sql",
                (
                    cfg.max_attempts,
                    error[:500],
                    cfg.max_attempts,
                    cfg.max_attempts,
                    backoff,
                    client_id,
                    job_id,
                ),
                conn=conn,
            )
        return "failed" if row is not None and row["status"] == "failed" else "queued"


async def kb_ingest_loop(processor: KbIngestProcessor, *, poll_seconds: float) -> None:
    """Poll the upload queue until cancelled; failures are logged and retried."""
    while True:
        try:
            await processor.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("kb_ingest_loop_error")
        await asyncio.sleep(poll_seconds)
