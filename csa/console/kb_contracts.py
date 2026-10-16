"""Public ingestion status omits server storage locations and internal job metadata."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from csa.agents.kb.ingest_jobs import parse_job_files


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
    )
