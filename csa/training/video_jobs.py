"""Training video jobs: claims, the attempt fence, retries, the daily processing cap and the sweep.

A completed upload queues one job per video (stage ``transcribing``). The training worker
(``apps/training_worker``) runs ``VideoProcessor``: the owner pool lists the active companies, and
for each, inside ``tx(app_pool, company)``, stale jobs are requeued and then at most one due job is
claimed, oldest first, with ``FOR UPDATE SKIP LOCKED``, and only while no other job of that company
is processing. Each claim first takes the company's claim lock (an advisory transaction lock apart
from the upload lock), so two claims of one company cannot both pass that check. The worker is a
single process per deployment and handles one job at a time.

**The fence.** A claim adds one to ``attempts`` and stamps ``locked_at``. Every write a stage makes
goes through ``JobHandle.tx()``, which locks the job row and refreshes ``locked_at`` only while the
job is still ``processing`` at this stage and attempt (attempts restart for the summary, so the
stage is part of the fence); otherwise it raises ``JobLost`` and nothing is written.
A job whose heartbeat is older than ``JOB_STALE_SECONDS`` is requeued (resuming at ``chunks_done``
and its saved ``parts``) and claimed again with a higher attempt, so an old attempt that wakes up
later cannot write. A removed item takes its job row with it (cascade), so its attempt stops the
same way; a write outside the fence that hits the missing item (a ``ForeignKeyViolation``) also
ends the job quietly.

**Transitions.** ``"summarising"`` moves the job to that stage, queued, attempts reset;
``"ready"`` or a finished summary ends it ``done`` and the item ``ready``. ``Transient`` requeues
after ``JOB_BACKOFF_SECONDS x attempts``, and after ``JOB_MAX_ATTEMPTS`` fails job and item with
the code (a retryable one, which a manager can retry). ``Permanent`` fails both at once and deletes
the stored file (its bytes stop counting). ``Waiting(until)`` requeues until then, gives the attempt
back and records ``training_processing_limit``, which the list shows as waiting. Item status and
the job row change in one statement, so an item is never left in a stage without its job.

**The daily cap.** ``reserve_processing`` books a video's seconds against the company's rolling
24 hours under the company lock before its first billed chunk. The usage row is keyed by item and
has no foreign key, so deleting a video does not give its seconds back.

**The sweep** (every ``SWEEP_EVERY_SECONDS``): uploads idle for ``UPLOAD_ABANDON_SECONDS`` are
deleted under the company lock (audit ``training.upload_abandoned``, no actor) and their store
uploads aborted; stored files no row keeps are removed (``store_sweep.cleanup``); store multipart
uploads whose name has no ``uploading`` row are aborted (an abort that failed on delete, or a start
whose answer was lost); usage rows older than ``USAGE_KEEP_SECONDS`` are deleted.

Logs carry ids, the company id, the stage, the attempt, durations and outcome codes: never a title,
a file name, a URL or any transcript text.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

import structlog
from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.errors import ForeignKeyViolation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.core import db
from csa.core.tenancy import tx
from csa.storage.base import ObjectStore
from csa.storage.multipart import MultipartStore, OpenUpload
from csa.training import limits
from csa.training.access import can_manage
from csa.training.limits import VideoContainer
from csa.training.models import ItemOut
from csa.training.service import FORBIDDEN, LIMIT_REACHED, NOT_FOUND, TrainingLibrary
from csa.training.store_sweep import STORED, cleanup, lock

logger = structlog.get_logger(__name__)

__all__ = [
    "RETRYABLE",
    "RETRY_NOT_ALLOWED",
    "STORAGE_FAILED",
    "SUMMARY_FAILED",
    "TRANSCRIPTION_FAILED",
    "Claim",
    "JobHandle",
    "JobLost",
    "Permanent",
    "Stage",
    "StageRunner",
    "SweepReport",
    "Transient",
    "VideoProcessor",
    "Waiting",
    "reserve_processing",
    "retry",
]

Stage = Literal["transcribing", "summarising"]

TRANSCRIPTION_FAILED = "training_transcription_unavailable"
SUMMARY_FAILED = "training_summary_unavailable"
STORAGE_FAILED = "training_storage_unavailable"
RETRYABLE = frozenset({TRANSCRIPTION_FAILED, SUMMARY_FAILED, STORAGE_FAILED})
"""Failure codes a manager may retry; every other failure is a refusal of the file itself."""

RETRY_NOT_ALLOWED = "training_retry_not_allowed"

# The code a stage fails with when its worker keeps dying or raises something unexpected.
_STAGE_FAILED: dict[str, str] = {
    "transcribing": TRANSCRIPTION_FAILED,
    "summarising": SUMMARY_FAILED,
}
# The cap's rolling window.
_DAY_SECONDS = 86_400


@dataclass(frozen=True)
class Claim:
    """One attempt at one job, as claimed: where it stands and what the stage needs of the item."""

    client_id: UUID
    item_id: UUID
    stage: Stage
    attempt: int
    chunks_done: int
    chunks_total: int | None
    parts: list[dict[str, Any]]
    storage_name: str
    container: VideoContainer
    duration_ms: int | None
    speech: str | None


class Transient(Exception):
    """A failure worth trying again (provider, storage or embedding outage); ``code`` is kept."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Permanent(Exception):
    """The file itself is refused (unreadable, too long, an unplayable codec); no retry."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Waiting(Exception):
    """The company's daily processing limit is used up; try again at ``until``."""

    def __init__(self, until: datetime) -> None:
        super().__init__(LIMIT_REACHED)
        self.until = until


class JobLost(Exception):
    """The job is no longer this attempt's: its item was removed, or it was claimed again."""


class JobHandle:
    """The fenced way a stage writes: every transaction first proves the claim still holds."""

    def __init__(self, pool: AsyncConnectionPool, claim: Claim) -> None:
        self._pool = pool
        self.claim = claim

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[AsyncConnection[Any]]:
        """A company transaction with the job row locked; ``JobLost`` (nothing written) when
        the job is not ``processing`` at this attempt any more.
        """
        async with tx(self._pool, str(self.claim.client_id)) as conn:
            fenced = await db.q.fetch_one(
                "training/video_job_fence.sql",
                {
                    "client_id": self.claim.client_id,
                    "item_id": self.claim.item_id,
                    "stage": self.claim.stage,
                    "attempt": self.claim.attempt,
                },
                conn=conn,
            )
            if fenced is None:
                raise JobLost
            yield conn

    async def heartbeat(self) -> None:
        """Refresh ``locked_at`` (the fence does it), so a long stage is not taken for stale."""
        async with self.tx():
            pass


class StageRunner(Protocol):
    """The work of each stage; writes go through ``job.tx()``."""

    async def transcribe(self, claim: Claim, job: JobHandle) -> Literal["summarising", "ready"]: ...

    async def summarise(self, claim: Claim, job: JobHandle) -> None: ...


@dataclass(frozen=True)
class SweepReport:
    """What one sweep did, over every active company."""

    abandoned: int = 0
    removed: int = 0
    aborted: int = 0
    pruned: int = 0


async def reserve_processing(
    conn: AsyncConnection[Any], client_id: UUID, item_id: UUID, seconds: int
) -> datetime | None:
    """Book ``seconds`` of processing for the item against the company's rolling 24 hours.

    Takes the company lock (until ``conn``'s transaction ends), so two reservations cannot both
    take the last room. ``None`` when booked, or already booked for this item; otherwise the time
    at which enough booked seconds have aged out for this video to fit.

    ``seconds`` is the video's duration rounded up, from 1 to ``MAX_VIDEO_SECONDS`` (the usage
    row's own bounds); anything else is a ``ValueError`` and nothing is booked.
    """
    if not 1 <= seconds <= limits.MAX_VIDEO_SECONDS:
        raise ValueError("processing seconds must be from 1 to MAX_VIDEO_SECONDS")
    await lock(conn, client_id)
    row = await db.q.fetch_one(
        "training/video_usage_reserve.sql",
        {
            "client_id": client_id,
            "item_id": item_id,
            "seconds": seconds,
            "cap": limits.DAILY_PROCESSING_SECONDS,
            "window_seconds": _DAY_SECONDS,
        },
        conn=conn,
    )
    if row is None:
        raise RuntimeError("the processing reservation returned no row")
    if row["reserved"]:
        return None
    return row["until"]


def _claim(row: Mapping[str, Any]) -> Claim:
    return Claim(
        client_id=row["client_id"],
        item_id=row["item_id"],
        stage=row["stage"],
        attempt=row["attempts"],
        chunks_done=row["chunks_done"],
        chunks_total=row["chunks_total"],
        parts=list(row["parts"] or []),
        storage_name=row["storage_name"],
        container=row["container"],
        duration_ms=row["duration_ms"],
        speech=row["speech"],
    )


class VideoProcessor:
    """Claims and runs video jobs, one at a time, and sweeps; one instance per deployment."""

    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        store: ObjectStore,
        stages: StageRunner,
    ) -> None:
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._store = store
        self._stages = stages

    async def _companies(self) -> list[UUID]:
        # The owner pool is used for this list only; everything else runs in a company's tx.
        async with self._owner_pool.connection() as conn:
            rows = await db.q.fetch_all("jobs/list_active_clients.sql", conn=conn)
        return [row["id"] for row in rows]

    async def run_once(self) -> int:
        """Claim and run at most one job per active company, one after another; return how many."""
        handled = 0
        for client_id in await self._companies():
            try:
                claim = await self.claim(client_id)
                if claim is None:
                    continue
                await self.process(claim)
                handled += 1
            except Exception as failure:
                # A database outage for one company must not stop the others; a job left
                # processing is requeued once its heartbeat is stale.
                logger.error(
                    "training_video_job_error",
                    client_id=str(client_id),
                    outcome=type(failure).__name__,
                )
        return handled

    async def claim(self, client_id: UUID) -> Claim | None:
        """Requeue the company's stale jobs, then claim its oldest due job (none while one runs)."""
        async with tx(self._app_pool, str(client_id)) as conn:
            # The company's claim lock (not the upload lock): a second claim waits here, then sees
            # the first one's job processing and takes nothing.
            await db.q.execute("training/lock.sql", (f"{client_id}:training_jobs",), conn=conn)
            stale = await db.q.fetch_all(
                "training/video_job_requeue_stale.sql",
                {
                    "client_id": client_id,
                    "max_attempts": limits.JOB_MAX_ATTEMPTS,
                    "stale_seconds": limits.JOB_STALE_SECONDS,
                },
                conn=conn,
            )
            row = await db.q.fetch_one(
                "training/video_job_claim.sql", {"client_id": client_id}, conn=conn
            )
        for job in stale:
            logger.warning(
                "training_video_job_stale",
                client_id=str(client_id),
                item_id=str(job["item_id"]),
                stage=job["stage"],
                outcome=job["status"],
            )
        return _claim(row) if row is not None else None

    async def process(self, claim: Claim) -> str:
        """Run the claimed stage and record what came of it.

        Returns the outcome: ``summarising``, ``ready``, ``queued``, ``waiting``, ``failed`` or
        ``lost`` (the item was removed or the job claimed again; nothing was written).
        """
        began = time.monotonic()
        job = JobHandle(self._app_pool, claim)
        code: str | None = None
        try:
            try:
                if claim.stage == "transcribing":
                    outcome = await self._stages.transcribe(claim, job)
                    if outcome == "summarising":
                        await self._write(job, "training/video_job_advance.sql", {})
                    else:
                        await self._finish(job, "done", None)
                        outcome = "ready"
                else:
                    await self._stages.summarise(claim, job)
                    await self._finish(job, "done", None)
                    outcome = "ready"
            except Waiting as waiting:
                code = LIMIT_REACHED
                outcome = await self._release(job, code, until=waiting.until)
            except Transient as transient:
                code = transient.code
                outcome = await self._release(job, code)
            except Permanent as permanent:
                code = permanent.code
                await self._finish(job, "failed", code)
                await self._delete_file(claim)
                outcome = "failed"
            except (JobLost, ForeignKeyViolation):
                raise
            except Exception as failure:
                # Unexpected (a bug, a database error inside a stage): counted as a transient
                # failure of the stage, so it is retried and then fails with a retryable code.
                code = _STAGE_FAILED[claim.stage]
                logger.error(
                    "training_video_stage_error",
                    client_id=str(claim.client_id),
                    item_id=str(claim.item_id),
                    stage=claim.stage,
                    attempt=claim.attempt,
                    outcome=type(failure).__name__,
                )
                outcome = await self._release(job, code)
        except (JobLost, ForeignKeyViolation):
            outcome = "lost"
        logger.info(
            "training_video_job",
            client_id=str(claim.client_id),
            item_id=str(claim.item_id),
            stage=claim.stage,
            attempt=claim.attempt,
            duration_ms=int((time.monotonic() - began) * 1000),
            code=code,
            outcome=outcome,
        )
        return outcome

    async def _write(self, job: JobHandle, path: str, params: Mapping[str, Any]) -> Any:
        """One fenced transition; ``JobLost`` when its statement finds nothing to change."""
        async with job.tx() as conn:
            row = await db.q.fetch_one(
                path,
                {
                    "client_id": job.claim.client_id,
                    "item_id": job.claim.item_id,
                    "stage": job.claim.stage,
                    "attempt": job.claim.attempt,
                    **params,
                },
                conn=conn,
            )
        if row is None:
            raise JobLost
        return row

    async def _finish(self, job: JobHandle, status: str, code: str | None) -> None:
        await self._write(job, "training/video_job_finish.sql", {"status": status, "code": code})

    async def _release(self, job: JobHandle, code: str, *, until: datetime | None = None) -> str:
        row = await self._write(
            job,
            "training/video_job_release.sql",
            {
                "code": code,
                "until": until,
                "max_attempts": limits.JOB_MAX_ATTEMPTS,
                "backoff_seconds": limits.JOB_BACKOFF_SECONDS,
            },
        )
        if row["status"] == "failed":
            return "failed"
        return "waiting" if until is not None else "queued"

    async def _delete_file(self, claim: Claim) -> None:
        """A refused file goes at once (its row stays, failed, so the manager sees why)."""
        try:
            await asyncio.to_thread(self._store.delete, claim.client_id, claim.storage_name)
        except Exception as failure:
            logger.error(
                "training_file_delete_failed",
                client_id=str(claim.client_id),
                item_id=str(claim.item_id),
                outcome=type(failure).__name__,
            )

    async def sweep_once(self) -> SweepReport:
        """Sweep every active company once; a failure in one is logged and the rest go on."""
        totals = SweepReport()
        for client_id in await self._companies():
            try:
                swept = await self._sweep(client_id)
            except Exception as failure:
                logger.error(
                    "training_sweep_failed",
                    client_id=str(client_id),
                    outcome=type(failure).__name__,
                )
                continue
            totals = SweepReport(
                abandoned=totals.abandoned + swept.abandoned,
                removed=totals.removed + swept.removed,
                aborted=totals.aborted + swept.aborted,
                pruned=totals.pruned + swept.pruned,
            )
        return totals

    async def _sweep(self, client_id: UUID) -> SweepReport:
        multipart = self._store if isinstance(self._store, MultipartStore) else None
        async with tx(self._app_pool, str(client_id)) as conn:
            await lock(conn, client_id)
            gone = await db.q.fetch_all(
                "training/video_abandoned_uploads.sql",
                {"client_id": client_id, "abandon_seconds": limits.UPLOAD_ABANDON_SECONDS},
                conn=conn,
            )
            for row in gone:
                await db.q.execute(
                    "console/audit.sql",
                    (None, client_id, "training.upload_abandoned", str(row["id"]), Jsonb({})),
                    conn=conn,
                )
        for row in gone:
            logger.info(
                "training_upload_abandoned",
                client_id=str(client_id),
                item_id=str(row["id"]),
                outcome="removed",
            )
            if multipart is not None:
                await self._abort(multipart, client_id, row["storage_name"], row["upload_id"])
        removed = await cleanup(self._app_pool, self._store, client_id)
        aborted = await self._abort_orphans(multipart, client_id)
        async with tx(self._app_pool, str(client_id)) as conn:
            pruned = await db.q.fetch_all(
                "training/video_usage_prune.sql",
                {"client_id": client_id, "keep_seconds": limits.USAGE_KEEP_SECONDS},
                conn=conn,
            )
        return SweepReport(
            abandoned=len(gone), removed=removed, aborted=aborted, pruned=len(pruned)
        )

    async def _abort_orphans(self, multipart: MultipartStore | None, client_id: UUID) -> int:
        """Abort store uploads whose name has no ``uploading`` row.

        Listed under the company lock: a start opens its store upload and commits its row inside
        that lock, so an upload listed here either has a committed row or never will.
        """
        if multipart is None:
            return 0
        async with tx(self._app_pool, str(client_id)) as conn:
            await lock(conn, client_id)
            rows = await db.q.fetch_all("training/names.sql", (client_id,), conn=conn)
            uploads: list[OpenUpload] = await asyncio.to_thread(multipart.list_uploads, client_id)
        keep = {row["storage_name"] for row in rows if row["uploading"]}
        aborted = 0
        for upload in uploads:
            if not STORED.fullmatch(upload.name) or upload.name in keep:
                continue
            if await self._abort(multipart, client_id, upload.name, upload.upload_id):
                aborted += 1
        return aborted

    @staticmethod
    async def _abort(
        multipart: MultipartStore, client_id: UUID, name: str, upload_id: str | None
    ) -> bool:
        try:
            await asyncio.to_thread(multipart.abort_upload, client_id, name, upload_id)
        except Exception as failure:
            # Its row is gone, so the next sweep lists it again and retries.
            logger.error(
                "training_abort_failed",
                client_id=str(client_id),
                outcome=type(failure).__name__,
            )
            return False
        return True


async def retry(
    library: TrainingLibrary, actor: ConsoleActor, client_id: UUID, item_id: UUID
) -> ItemOut:
    """Queue a video that failed for a retryable reason again, at the stage that failed."""
    async with tx(library.pool, str(client_id)) as conn:
        if not await can_manage(conn, actor, client_id):
            raise HTTPException(403, FORBIDDEN)
        row = await db.q.fetch_one(
            "training/video_job_retry.sql",
            {"client_id": client_id, "item_id": item_id, "codes": sorted(RETRYABLE)},
            conn=conn,
        )
        if row is None:
            if await db.q.fetch_one("training/get.sql", (client_id, item_id), conn=conn) is None:
                raise HTTPException(404, NOT_FOUND)
            raise HTTPException(409, RETRY_NOT_ALLOWED)
        await audit(conn, actor, client_id, "training.video_retried", str(item_id))
    logger.info(
        "training_video_retried",
        client_id=str(client_id),
        item_id=str(item_id),
        stage=row["job_stage"],
        outcome="queued",
    )
    [item] = await library.present(client_id, [row], manage=True)
    return item
