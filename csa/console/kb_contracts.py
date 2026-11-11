"""Public ingestion status omits server storage locations and internal job metadata."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from csa.agents.kb.ingest_jobs import parse_job_files
from csa.agents.kb.lifecycle import CONTENT_DELETED_ERROR


class JobFileOut(BaseModel):
    name: str
    file_id: UUID | None = None


class JobResultOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file: str
    status: str
    document_id: UUID | None = None
    chunks: int | None = None
    version: int | None = None
    error: str | None = None


class JobMetaOut(BaseModel):
    results: list[JobResultOut] = []


class JobOut(BaseModel):
    id: UUID
    source: str
    status: str
    files: list[JobFileOut]
    meta: JobMetaOut
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # False when a file of the job was removed or its content was deleted after it was queued:
    # the job can't be tried again, only taken off the list.
    original_stored: bool


# pending: the job is off the list and its file record is gone, but the storage sweep failed; the
# existing storage reconcile finishes it (same meaning as on the file and document delete routes).
StorageCleanup = Literal["done", "pending"]


class JobCancelledOut(JobOut):
    storage_cleanup: StorageCleanup


class JobsDismissedOut(BaseModel):
    dismissed: int
    storage_cleanup: StorageCleanup


def content_deleted(error: str | None) -> bool:
    return error is not None and error.startswith(CONTENT_DELETED_ERROR)


def original_stored(row: dict[str, Any], files: list[JobFileOut], meta: JobMetaOut) -> bool:
    """Read from the raw row: a path-only entry (no file_id) counts as stored."""
    deleted = {str(file_id) for file_id in row.get("deleted_file_ids") or ()}
    if any(item.file_id is not None and str(item.file_id) in deleted for item in files):
        return False
    return not content_deleted(row["error"]) and not any(
        content_deleted(result.error) for result in meta.results
    )


def safe_error(error: str | None) -> str | None:
    if error is None:
        return None
    if "/" in error or "\\" in error:
        return "File processing failed; inspect the server logs."
    return error[:500]


def public_job(row: dict[str, Any]) -> JobOut:
    files = [
        JobFileOut(
            name=PurePosixPath((item.name or item.path).replace("\\", "/")).name,
            file_id=item.file_id,
        )
        for item in parse_job_files(row["files"])
    ]
    meta = JobMetaOut.model_validate(row["meta"] or {})
    stored = original_stored(row, files, meta)
    for result in meta.results:
        result.file = PurePosixPath(result.file.replace("\\", "/")).name
        result.error = safe_error(result.error)
    return JobOut(
        **{
            key: row[key]
            for key in (
                "id",
                "source",
                "status",
                "attempts",
                "created_at",
                "started_at",
                "finished_at",
            )
        },
        files=files,
        meta=meta,
        error=safe_error(row["error"]),
        original_stored=stored,
    )
