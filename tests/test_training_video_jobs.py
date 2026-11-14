"""Training video jobs: claims, the fence, retries, the daily cap, the sweep and the retry route.

Every stage is a scripted fake ``StageRunner``; no provider is called. Job rows are made by SQL.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.storage.base import ObjectStore, StorageError
from csa.storage.multipart import OpenUpload
from csa.training import limits
from csa.training.video_jobs import (
    Claim,
    JobHandle,
    JobLost,
    Permanent,
    SweepReport,
    Transient,
    VideoProcessor,
    Waiting,
    reserve_processing,
)
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld
from tests.test_training_api import ITEMS, audits, detail, enable, make_owner, remove
from tests.test_training_video_items import RecordingStore, put_file, upload_row, video_row

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TRANSCRIPTION = "training_transcription_unavailable"
SUMMARY = "training_summary_unavailable"

Step = Callable[[Claim, JobHandle], Awaitable[Any]]


class UploadsStore(RecordingStore):
    """The local store that also reports open S3-style uploads the test lists for it."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.open_uploads: dict[UUID, list[OpenUpload]] = {}
        self.fail_next_delete = False

    def list_uploads(self, tenant: UUID) -> list[OpenUpload]:
        return list(self.open_uploads.get(tenant, []))

    def delete(self, tenant: UUID, name: str) -> None:
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise StorageError("object storage delete: synthetic failure")
        super().delete(tenant, name)


@pytest.fixture
def training_store(tmp_path: Path) -> ObjectStore:
    return UploadsStore(tmp_path / "training")


@pytest.fixture
async def owner_pool(sessions: SessionWorld) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(sessions.owner_dsn, min_size=1, max_size=2)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


class Script:
    """A ``StageRunner`` the test scripts; it records every claim it is handed."""

    def __init__(self, transcribe: Step | None = None, summarise: Step | None = None) -> None:
        self.claims: list[Claim] = []
        self._transcribe = transcribe
        self._summarise = summarise

    async def transcribe(self, claim: Claim, job: JobHandle) -> Literal["summarising", "ready"]:
        self.claims.append(claim)
        if self._transcribe is None:
            return "ready"
        return await self._transcribe(claim, job)

    async def summarise(self, claim: Claim, job: JobHandle) -> None:
        self.claims.append(claim)
        if self._summarise is not None:
            await self._summarise(claim, job)


def raising(error: BaseException) -> Step:
    async def step(claim: Claim, job: JobHandle) -> Any:
        raise error

    return step


def processor(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, store: ObjectStore, script: Script
) -> VideoProcessor:
    return VideoProcessor(owner_pool, sessions.pool, store, script)


async def only_active(world: SessionWorld, *tenants: str) -> None:
    """These companies are the active ones; every other company in the database is not."""
    await world.q(
        "UPDATE clients SET status = CASE WHEN id = ANY(%s::uuid[]) THEN 'active'"
        " ELSE 'onboarding' END",
        list(tenants),
    )


async def queued_video(
    world: SessionWorld,
    tenant: str,
    *,
    created_ago: int = 0,
    due_in: int = 0,
    stage: str = "transcribing",
    attempts: int = 0,
    chunks_done: int = 0,
    storage_name: str | None = None,
) -> str:
    """An uploaded video with a queued job, made by SQL."""
    item = await video_row(world, tenant, status="uploaded", storage_name=storage_name)
    await world.q(
        "INSERT INTO training_video_jobs (client_id, item_id, stage, status, attempts,"
        " chunks_done, created_at, next_attempt_at) VALUES (%s, %s, %s, 'queued', %s, %s,"
        " now() - make_interval(secs => %s), now() + make_interval(secs => %s))",
        tenant,
        item,
        stage,
        attempts,
        chunks_done,
        created_ago,
        due_in,
    )
    return item


async def job_of(world: SessionWorld, item: str) -> dict[str, Any] | None:
    found = await world.q(
        "SELECT *, extract(epoch FROM next_attempt_at - now())::float AS wait"
        " FROM training_video_jobs WHERE item_id = %s",
        item,
    )
    return found[0] if found else None


async def item_of(world: SessionWorld, item: str) -> dict[str, Any] | None:
    found = await world.q("SELECT * FROM training_items WHERE id = %s", item)
    return found[0] if found else None


async def make_due(world: SessionWorld, item: str) -> None:
    await world.q("UPDATE training_video_jobs SET next_attempt_at = now() WHERE item_id = %s", item)


async def age_lock(world: SessionWorld, item: str, minutes: int = 11) -> None:
    await world.q(
        "UPDATE training_video_jobs SET locked_at = now() - make_interval(mins => %s)"
        " WHERE item_id = %s",
        minutes,
        item,
    )


async def usage_row(world: SessionWorld, tenant: str, seconds: int, hours_ago: float) -> None:
    await world.q(
        "INSERT INTO training_video_usage (client_id, item_id, seconds, processed_at)"
        " VALUES (%s, gen_random_uuid(), %s, now() - make_interval(secs => %s))",
        tenant,
        seconds,
        hours_ago * 3600,
    )


async def reserve(world: SessionWorld, tenant: str, item: UUID, seconds: int) -> datetime | None:
    async with tx(world.pool, tenant) as conn:
        return await reserve_processing(conn, UUID(tenant), item, seconds)


async def test_claim_takes_the_oldest_due_job_and_one_per_company(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    old = await queued_video(sessions, tenant, created_ago=120)
    new = await queued_video(sessions, tenant, created_ago=60)
    later = await queued_video(sessions, tenant, created_ago=300, due_in=600)
    script = Script()
    worker = processor(sessions, owner_pool, training_store, script)

    claim = await worker.claim(UUID(tenant))
    assert claim is not None
    assert (claim.item_id, claim.client_id, claim.stage) == (
        UUID(old),
        UUID(tenant),
        "transcribing",
    )
    assert (claim.attempt, claim.chunks_done, claim.container) == (1, 0, "mp4")
    job = await job_of(sessions, old)
    assert job is not None and (job["status"], job["attempts"]) == ("processing", 1)
    found = await item_of(sessions, old)
    assert found is not None and found["status"] == "transcribing"
    # One job of a company in flight at a time.
    assert await worker.claim(UUID(tenant)) is None

    assert await worker.process(claim) == "ready"
    assert await worker.run_once() == 1
    assert [c.item_id for c in script.claims] == [UUID(old), UUID(new)]
    # The last one is not due yet.
    assert await worker.run_once() == 0
    later_job = await job_of(sessions, later)
    assert later_job is not None and later_job["status"] == "queued"
    done = await item_of(sessions, old)
    assert done is not None and done["status"] == "ready"
    finished = await job_of(sessions, old)
    assert finished is not None and finished["status"] == "done"


async def test_transient_error_backs_off_then_fails_retryable_after_three(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    item = await queued_video(sessions, tenant)
    script = Script(transcribe=raising(Transient(TRANSCRIPTION)))
    worker = processor(sessions, owner_pool, training_store, script)

    assert await worker.run_once() == 1
    job = await job_of(sessions, item)
    assert job is not None
    assert (job["status"], job["attempts"], job["error_code"]) == ("queued", 1, TRANSCRIPTION)
    assert 55 <= job["wait"] <= 65
    assert job["locked_at"] is None
    # Not due yet: nothing is claimed.
    assert await worker.run_once() == 0

    await make_due(sessions, item)
    assert await worker.run_once() == 1
    job = await job_of(sessions, item)
    assert job is not None and (job["status"], job["attempts"]) == ("queued", 2)
    assert 115 <= job["wait"] <= 125

    await make_due(sessions, item)
    assert await worker.run_once() == 1
    job = await job_of(sessions, item)
    assert job is not None
    assert (job["status"], job["attempts"], job["error_code"]) == ("failed", 3, TRANSCRIPTION)
    found = await item_of(sessions, item)
    assert found is not None
    assert (found["status"], found["failure_code"]) == ("failed", TRANSCRIPTION)
    assert len(script.claims) == 3
    await make_due(sessions, item)
    assert await worker.run_once() == 0


async def test_permanent_refusal_fails_at_once_deletes_the_file_and_releases_bytes(
    sessions: SessionWorld,
    owner_pool: AsyncConnectionPool,
    training_store: ObjectStore,
    tmp_path: Path,
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    name = f"{uuid4()}.mp4"
    stored = put_file(tmp_path, tenant, name)
    item = await queued_video(sessions, tenant, storage_name=name)

    async def video_bytes() -> int:
        async with tx(sessions.pool, tenant) as conn:
            usage = await db.q.fetch_one(
                "training/video_usage.sql", {"client_id": UUID(tenant)}, conn=conn
            )
        assert usage is not None
        return int(usage["bytes"])

    assert await video_bytes() == 4 * 1024 * 1024
    script = Script(transcribe=raising(Permanent("training_video_too_long")))
    worker = processor(sessions, owner_pool, training_store, script)

    assert await worker.run_once() == 1
    job = await job_of(sessions, item)
    assert job is not None
    assert (job["status"], job["attempts"]) == ("failed", 1)
    assert job["error_code"] == "training_video_too_long"
    found = await item_of(sessions, item)
    assert found is not None
    assert (found["status"], found["failure_code"]) == ("failed", "training_video_too_long")
    assert not stored.exists()
    assert await video_bytes() == 0
    assert len(script.claims) == 1


async def test_stale_job_is_requeued_and_resumes_where_it_stopped(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    item = await queued_video(sessions, tenant)
    script = Script()
    worker = processor(sessions, owner_pool, training_store, script)

    # The worker claims, commits two chunks and is killed.
    first = await worker.claim(UUID(tenant))
    assert first is not None and first.attempt == 1
    await sessions.q(
        "UPDATE training_video_jobs SET chunks_done = 2, chunks_total = 5 WHERE item_id = %s", item
    )
    # Still fresh: not requeued.
    assert await worker.run_once() == 0
    await age_lock(sessions, item)

    assert await worker.run_once() == 1
    [seen] = script.claims
    assert (seen.item_id, seen.attempt, seen.chunks_done, seen.chunks_total) == (
        UUID(item),
        2,
        2,
        5,
    )

    # Attempts used up: stale means failed with the stage's retryable code.
    spent = await queued_video(sessions, tenant, stage="summarising", attempts=2)
    again = await worker.claim(UUID(tenant))
    assert again is not None and (again.item_id, again.attempt) == (UUID(spent), 3)
    await age_lock(sessions, spent)
    assert await worker.run_once() == 0
    job = await job_of(sessions, spent)
    assert job is not None
    assert (job["status"], job["error_code"], job["locked_at"]) == ("failed", SUMMARY, None)
    found = await item_of(sessions, spent)
    assert found is not None and (found["status"], found["failure_code"]) == ("failed", SUMMARY)


async def test_stale_attempt_cannot_write_after_a_reclaim(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    item = await queued_video(sessions, tenant)
    script = Script()
    worker = processor(sessions, owner_pool, training_store, script)

    old = await worker.claim(UUID(tenant))
    assert old is not None
    await age_lock(sessions, item)
    new = await worker.claim(UUID(tenant))
    assert new is not None and new.attempt == old.attempt + 1
    before = await job_of(sessions, item)
    assert before is not None

    stale = JobHandle(sessions.pool, old)
    with pytest.raises(JobLost):
        async with stale.tx() as conn:
            await conn.execute(
                "UPDATE training_video_jobs SET chunks_done = 9 WHERE item_id = %s", (item,)
            )
    with pytest.raises(JobLost):
        await stale.heartbeat()
    # The old attempt finishing is lost too: nothing is written.
    with capture_logs() as logs:
        assert await worker.process(old) == "lost"
    assert any(entry.get("outcome") == "lost" for entry in logs)
    after = await job_of(sessions, item)
    assert after is not None
    assert {k: v for k, v in after.items() if k != "wait"} == {
        k: v for k, v in before.items() if k != "wait"
    }
    found = await item_of(sessions, item)
    assert found is not None and found["status"] == "transcribing"

    # The current attempt still writes.
    async with JobHandle(sessions.pool, new).tx() as conn:
        await conn.execute(
            "UPDATE training_video_jobs SET chunks_done = 1 WHERE item_id = %s", (item,)
        )
    current = await job_of(sessions, item)
    assert current is not None and current["chunks_done"] == 1


async def test_heartbeat_keeps_a_long_job_from_being_requeued(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    item = await queued_video(sessions, tenant)
    checked: list[dict[str, Any]] = []

    async def long_job(claim: Claim, job: JobHandle) -> Literal["ready"]:
        # Eleven minutes in, the heartbeat refreshes the lock before the stale check runs.
        await age_lock(sessions, item)
        await job.heartbeat()
        assert await worker.claim(UUID(tenant)) is None
        row = await job_of(sessions, item)
        assert row is not None
        checked.append(row)
        return "ready"

    worker = processor(sessions, owner_pool, training_store, Script(transcribe=long_job))
    assert await worker.run_once() == 1
    [row] = checked
    assert (row["status"], row["attempts"]) == ("processing", 1)
    finished = await job_of(sessions, item)
    assert finished is not None and finished["status"] == "done"


async def test_advance_to_summarising_resets_attempts(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    item = await queued_video(sessions, tenant, attempts=2)

    async def to_summary(claim: Claim, job: JobHandle) -> Literal["summarising"]:
        return "summarising"

    script = Script(transcribe=to_summary)
    worker = processor(sessions, owner_pool, training_store, script)

    assert await worker.run_once() == 1
    job = await job_of(sessions, item)
    assert job is not None
    assert (job["stage"], job["status"], job["attempts"]) == ("summarising", "queued", 0)
    assert job["error_code"] is None and job["locked_at"] is None
    found = await item_of(sessions, item)
    assert found is not None and found["status"] == "summarising"

    assert await worker.run_once() == 1
    assert [(c.stage, c.attempt) for c in script.claims] == [
        ("transcribing", 3),
        ("summarising", 1),
    ]
    job = await job_of(sessions, item)
    assert job is not None and job["status"] == "done"
    found = await item_of(sessions, item)
    assert found is not None and found["status"] == "ready"


async def test_waiting_for_the_daily_cap_keeps_attempts_and_says_why(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    await only_active(sessions, tenant)
    item = await queued_video(sessions, tenant, attempts=1)
    until = datetime.now(UTC) + timedelta(hours=2)
    worker = processor(
        sessions, owner_pool, training_store, Script(transcribe=raising(Waiting(until)))
    )

    assert await worker.run_once() == 1
    job = await job_of(sessions, item)
    assert job is not None
    assert (job["status"], job["attempts"]) == ("queued", 1)
    assert job["error_code"] == "training_processing_limit"
    assert abs((job["next_attempt_at"] - until).total_seconds()) < 1
    listed = await sessions.admin.get(f"{ITEMS}?client_id={tenant}")
    assert listed.status_code == 200, listed.text
    [shown] = [row for row in listed.json()["items"] if row["id"] == item]
    assert shown["processing"]["waiting"] is True
    assert await worker.run_once() == 0


async def test_reserve_processing_caps_five_hours_per_rolling_day_and_once_per_item(
    sessions: SessionWorld,
) -> None:
    tenant = sessions.tenants[0]
    for hours_ago in (1, 2, 3, 4):
        await usage_row(sessions, tenant, 3600, hours_ago)
    await usage_row(sessions, tenant, 3600, 25)  # out of the window: does not count
    first, second = uuid4(), uuid4()

    assert await reserve(sessions, tenant, first, 3600) is None
    until = await reserve(sessions, tenant, second, 60)
    assert until is not None
    # Room for 60 seconds frees when the oldest row in the window (4 hours ago) ages out.
    expected = datetime.now(UTC) + timedelta(hours=20)
    assert abs((until - expected).total_seconds()) < 60
    # The same item reserves once.
    assert await reserve(sessions, tenant, first, 3600) is None
    rows = await sessions.q(
        "SELECT item_id FROM training_video_usage WHERE client_id = %s AND item_id = ANY(%s)",
        tenant,
        [first, second],
    )
    assert [row["item_id"] for row in rows] == [first]


async def test_concurrent_reservations_cannot_both_pass_the_cap(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    for hours_ago in (1, 2, 3, 4):
        await usage_row(sessions, tenant, 3600, hours_ago)
    await usage_row(sessions, tenant, 1800, 5)
    results = await asyncio.gather(
        reserve(sessions, tenant, uuid4(), 1800), reserve(sessions, tenant, uuid4(), 1800)
    )
    assert sorted(result is None for result in results) == [False, True]
    [total] = await sessions.q(
        "SELECT sum(seconds)::int AS s FROM training_video_usage WHERE client_id = %s"
        " AND processed_at > now() - interval '24 hours'",
        tenant,
    )
    assert total["s"] == limits.DAILY_PROCESSING_SECONDS


async def test_retry_route_requeues_retryable_failures_only_and_audits(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    admin, admin_me = await sessions.member(role="tenant_admin")
    owner, owner_me = await sessions.member()
    await make_owner(sessions, tenant, owner_me["user"]["id"])
    staff, _ = await sessions.member()
    retryable = await video_row(sessions, tenant, status="failed", failure_code=SUMMARY)
    permanent = await video_row(
        sessions, tenant, status="failed", failure_code="training_video_codec"
    )
    for item, stage, code in (
        (retryable, "summarising", SUMMARY),
        (permanent, "transcribing", "training_video_codec"),
    ):
        await sessions.q(
            "INSERT INTO training_video_jobs (client_id, item_id, stage, status, attempts,"
            " chunks_done, chunks_total, error_code) VALUES (%s, %s, %s, 'failed', 3, 4, 4, %s)",
            tenant,
            item,
            stage,
            code,
        )

    async def retry(browser: Any, item: str) -> Any:
        return await sessions.mutate(browser, f"{ITEMS}/{item}/retry")

    refused = await retry(staff, retryable)
    assert refused.status_code == 403 and detail(refused) == "training_forbidden"
    job = await job_of(sessions, retryable)
    assert job is not None and job["status"] == "failed"

    answer = await retry(admin, retryable)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["id"], body["status"], body["failure"]) == (retryable, "summarising", None)
    assert body["processing"] == {
        "stage": "summarising",
        "chunks_done": 4,
        "chunks_total": 4,
        "waiting": False,
    }
    job = await job_of(sessions, retryable)
    assert job is not None
    assert (job["stage"], job["status"], job["attempts"], job["error_code"]) == (
        "summarising",
        "queued",
        0,
        None,
    )
    assert job["wait"] <= 1
    found = await item_of(sessions, retryable)
    assert found is not None and (found["status"], found["failure_code"]) == ("summarising", None)
    [audit] = await audits(sessions, tenant, "training.video_retried")
    assert (str(audit["actor_id"]), audit["target_id"]) == (admin_me["user"]["id"], retryable)

    # Not failed any more, a permanent refusal, or not there at all.
    again = await retry(owner, retryable)
    assert again.status_code == 409 and detail(again) == "training_retry_not_allowed"
    blocked = await retry(admin, permanent)
    assert blocked.status_code == 409 and detail(blocked) == "training_retry_not_allowed"
    kept = await job_of(sessions, permanent)
    assert kept is not None and kept["status"] == "failed"
    missing = await retry(admin, str(uuid4()))
    assert missing.status_code == 404 and detail(missing) == "training_not_found"
    assert len(await audits(sessions, tenant, "training.video_retried")) == 1


async def test_sweep_removes_an_abandoned_upload_and_keeps_a_fresh_one(
    sessions: SessionWorld,
    owner_pool: AsyncConnectionPool,
    training_store: UploadsStore,
    tmp_path: Path,
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    old_name, fresh_name = f"{uuid4()}.mp4", f"{uuid4()}.webm"
    old = await video_row(sessions, tenant, status="uploading", storage_name=old_name)
    fresh = await video_row(sessions, tenant, status="uploading", storage_name=fresh_name)
    await upload_row(sessions, tenant, old, "old-upload")
    await upload_row(sessions, tenant, fresh, "fresh-upload")
    await sessions.q(
        "UPDATE training_video_uploads SET last_piece_at = now() - interval '25 hours'"
        " WHERE item_id = %s",
        old,
    )
    # The fresh one started long ago but its last piece came an hour ago.
    await sessions.q(
        "UPDATE training_video_uploads SET created_at = now() - interval '30 hours',"
        " last_piece_at = now() - interval '1 hour' WHERE item_id = %s",
        fresh,
    )
    old_part = put_file(tmp_path, tenant, f".{old_name}.part")
    fresh_part = put_file(tmp_path, tenant, f".{fresh_name}.part")
    worker = processor(sessions, owner_pool, training_store, Script())

    report = await worker.sweep_once()

    assert isinstance(report, SweepReport)
    assert report.abandoned == 1
    assert await item_of(sessions, old) is None
    assert await item_of(sessions, fresh) is not None
    assert await sessions.q("SELECT 1 FROM training_video_uploads WHERE item_id = %s", old) == []
    [audit] = await audits(sessions, tenant, "training.upload_abandoned")
    assert (audit["actor_id"], audit["target_id"]) == (None, old)
    assert (UUID(tenant), old_name, "old-upload") in training_store.aborts
    assert all(name != fresh_name for _, name, _ in training_store.aborts)
    assert not old_part.exists() and fresh_part.exists()
    # A second sweep finds nothing more.
    again = await worker.sweep_once()
    assert again.abandoned == 0


async def test_sweep_aborts_s3_uploads_with_no_row(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: UploadsStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    running_name, orphan_name = f"{uuid4()}.mp4", f"{uuid4()}.mp4"
    running = await video_row(sessions, tenant, status="uploading", storage_name=running_name)
    await upload_row(sessions, tenant, running, "running-upload")
    started = datetime.now(UTC) - timedelta(hours=3)
    training_store.open_uploads[UUID(tenant)] = [
        OpenUpload(running_name, "running-upload", started),
        OpenUpload(orphan_name, "orphan-upload", started),
        OpenUpload("notes.txt", "foreign-upload", started),
    ]
    worker = processor(sessions, owner_pool, training_store, Script())

    report = await worker.sweep_once()

    assert report.aborted == 1
    assert training_store.aborts == [(UUID(tenant), orphan_name, "orphan-upload")]
    assert await item_of(sessions, running) is not None


async def test_sweep_prunes_usage_older_than_two_days_only(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    await usage_row(sessions, tenant, 600, 49)
    await usage_row(sessions, tenant, 600, 47)
    worker = processor(sessions, owner_pool, training_store, Script())

    report = await worker.sweep_once()

    assert report.pruned == 1
    left = await sessions.q(
        "SELECT extract(epoch FROM now() - processed_at)::int AS age"
        " FROM training_video_usage WHERE client_id = %s",
        tenant,
    )
    assert len(left) == 1 and left[0]["age"] < limits.USAGE_KEEP_SECONDS


async def test_item_deleted_mid_job_stops_quietly(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)

    async def delete(item: UUID) -> None:
        await sessions.q("DELETE FROM training_items WHERE id = %s", item)

    async def reserve_then_write(claim: Claim, job: JobHandle) -> Literal["ready"]:
        async with job.tx() as conn:
            assert await reserve_processing(conn, claim.client_id, claim.item_id, 90) is None
        await delete(claim.item_id)
        async with job.tx():
            pass
        return "ready"

    async def finish_after(claim: Claim, job: JobHandle) -> Literal["ready"]:
        await delete(claim.item_id)
        return "ready"

    async def insert_after(claim: Claim, job: JobHandle) -> Literal["ready"]:
        await delete(claim.item_id)
        async with tx(sessions.pool, str(claim.client_id)) as conn:
            await conn.execute(
                "INSERT INTO training_transcript_segments (client_id, item_id, chunk_index,"
                " position, start_ms, end_ms, text) VALUES (%s, %s, 0, 0, 0, 1000, 'synthetic')",
                (claim.client_id, claim.item_id),
            )
        return "ready"

    for step in (reserve_then_write, finish_after, insert_after):
        item = await queued_video(sessions, tenant)
        worker = processor(sessions, owner_pool, training_store, Script(transcribe=step))
        claim = await worker.claim(UUID(tenant))
        assert claim is not None and claim.item_id == UUID(item)
        with capture_logs() as logs:
            assert await worker.process(claim) == "lost"
        assert [entry["outcome"] for entry in logs if entry.get("item_id") == item] == ["lost"]
        assert await item_of(sessions, item) is None
        assert await job_of(sessions, item) is None
        assert (
            await sessions.q("SELECT 1 FROM training_transcript_segments WHERE item_id = %s", item)
            == []
        )
    # The reservation stays with the deleted item: deleting does not give the seconds back.
    rows = await sessions.q("SELECT seconds FROM training_video_usage WHERE client_id = %s", tenant)
    assert [row["seconds"] for row in rows] == [90]


async def test_removal_waits_for_a_fenced_worker_transaction_instead_of_deadlocking(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    """The worker locks the job row, then the item (a segment insert checks its foreign key);
    removal must take them in the same order, or Postgres kills one of the two (a 500 on Remove).
    """
    tenant = await enable(sessions)
    await only_active(sessions, tenant)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await queued_video(sessions, tenant)
    worker = processor(sessions, owner_pool, training_store, Script())
    claim = await worker.claim(UUID(tenant))
    assert claim is not None and claim.item_id == UUID(item)

    async def removal_waits() -> None:
        for _ in range(200):
            waiting = await sessions.q(
                "SELECT count(*) AS n FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if waiting[0]["n"] >= 1:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("the removal never waited for the worker's transaction")

    async with JobHandle(sessions.pool, claim).tx() as conn:
        removing = asyncio.create_task(remove(sessions, admin, item))
        await removal_waits()
        # The worker writes a segment while the removal waits: with the removal holding the item
        # first, this insert and the removal would deadlock.
        await conn.execute(
            "INSERT INTO training_transcript_segments (client_id, item_id, chunk_index,"
            " position, start_ms, end_ms, text) VALUES (%s, %s, 0, 0, 0, 1000, 'synthetic')",
            (claim.client_id, claim.item_id),
        )
        assert not removing.done()
    answer = await asyncio.wait_for(removing, 30)
    assert answer.status_code == 204, answer.text
    assert await item_of(sessions, item) is None
    assert await job_of(sessions, item) is None
    assert (
        await sessions.q("SELECT 1 FROM training_transcript_segments WHERE item_id = %s", item)
        == []
    )
    # The worker's next fenced write finds no job and stops quietly.
    with pytest.raises(JobLost):
        async with JobHandle(sessions.pool, claim).tx():
            pass


async def test_claims_never_cross_companies(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    first, second = sessions.tenants[0], sessions.tenants[1]
    await only_active(sessions, first, second)
    a1 = await queued_video(sessions, first, created_ago=60)
    a2 = await queued_video(sessions, first, created_ago=30)
    b1 = await queued_video(sessions, second, created_ago=10)
    script = Script()
    worker = processor(sessions, owner_pool, training_store, script)

    assert await worker.run_once() == 2
    assert {(c.client_id, c.item_id) for c in script.claims} == {
        (UUID(first), UUID(a1)),
        (UUID(second), UUID(b1)),
    }
    # The second company has nothing left; the first company's job is never handed to it.
    assert await worker.claim(UUID(second)) is None
    left = await job_of(sessions, a2)
    assert left is not None and left["status"] == "queued"
    claim = await worker.claim(UUID(first))
    assert claim is not None and (claim.client_id, claim.item_id) == (UUID(first), UUID(a2))
    # Row security: the second company's transaction cannot see or fence the first one's job.
    async with tx(sessions.pool, second) as conn:
        assert (
            await (
                await conn.execute("SELECT 1 FROM training_video_jobs WHERE item_id = %s", (a2,))
            ).fetchall()
            == []
        )
    stolen = JobHandle(
        sessions.pool,
        Claim(
            client_id=UUID(second),
            item_id=claim.item_id,
            stage=claim.stage,
            attempt=claim.attempt,
            chunks_done=0,
            chunks_total=None,
            parts=[],
            storage_name=claim.storage_name,
            container="mp4",
            duration_ms=None,
            speech=None,
        ),
    )
    with pytest.raises(JobLost):
        await stolen.heartbeat()


async def test_a_stale_transcribing_attempt_cannot_write_into_the_summarising_job(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    item = await queued_video(sessions, tenant)
    script = Script()
    worker = processor(sessions, owner_pool, training_store, script)

    transcribing = await worker.claim(UUID(tenant))
    assert transcribing is not None and (transcribing.stage, transcribing.attempt) == (
        "transcribing",
        1,
    )
    old = JobHandle(sessions.pool, transcribing)

    async def to_summary(claim: Claim, job: JobHandle) -> Literal["summarising"]:
        return "summarising"

    # The current attempt advances (through its own fresh handle); the summary is claimed as
    # attempt 1 again, the same number the old transcribing attempt holds.
    advancing = processor(sessions, owner_pool, training_store, Script(transcribe=to_summary))
    assert await advancing.process(transcribing) == "summarising"
    summarising = await worker.claim(UUID(tenant))
    assert summarising is not None and (summarising.stage, summarising.attempt) == (
        "summarising",
        1,
    )
    before = await job_of(sessions, item)
    assert before is not None and (before["stage"], before["status"]) == (
        "summarising",
        "processing",
    )

    with pytest.raises(JobLost):
        async with old.tx() as conn:
            await conn.execute(
                "UPDATE training_video_jobs SET chunks_done = 9 WHERE item_id = %s", (item,)
            )
    with pytest.raises(JobLost):
        await old.heartbeat()
    # Its finish and its release are lost too.
    assert await worker.process(transcribing) == "lost"
    failing = processor(
        sessions, owner_pool, training_store, Script(transcribe=raising(Transient(TRANSCRIPTION)))
    )
    assert await failing.process(transcribing) == "lost"

    after = await job_of(sessions, item)
    assert after is not None
    assert {k: v for k, v in after.items() if k != "wait"} == {
        k: v for k, v in before.items() if k != "wait"
    }
    found = await item_of(sessions, item)
    assert found is not None and (found["status"], found["failure_code"]) == ("summarising", None)


async def test_a_refused_file_left_by_a_failed_delete_goes_with_the_next_sweep(
    sessions: SessionWorld,
    owner_pool: AsyncConnectionPool,
    training_store: UploadsStore,
    tmp_path: Path,
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    refused_name, ready_name, running_name = (f"{uuid4()}.mp4" for _ in range(3))
    refused_file = put_file(tmp_path, tenant, refused_name)
    ready_file = put_file(tmp_path, tenant, ready_name)
    running_part = put_file(tmp_path, tenant, f".{running_name}.part")
    refused = await queued_video(sessions, tenant, storage_name=refused_name)
    await video_row(sessions, tenant, status="ready", storage_name=ready_name)
    running = await video_row(sessions, tenant, status="uploading", storage_name=running_name)
    await upload_row(sessions, tenant, running)
    # A retryable failure keeps its file: a manager may retry it.
    retry_name = f"{uuid4()}.mp4"
    retry_file = put_file(tmp_path, tenant, retry_name)
    await video_row(
        sessions, tenant, status="failed", storage_name=retry_name, failure_code=TRANSCRIPTION
    )
    training_store.fail_next_delete = True
    worker = processor(
        sessions,
        owner_pool,
        training_store,
        Script(transcribe=raising(Permanent("training_video_codec"))),
    )

    assert await worker.run_once() == 1
    assert refused_file.exists()  # the delete after the refusal failed

    report = await worker.sweep_once()

    assert report.removed == 1
    assert not refused_file.exists()
    assert ready_file.exists() and running_part.exists() and retry_file.exists()
    found = await item_of(sessions, refused)
    assert found is not None
    assert (found["status"], found["failure_code"]) == ("failed", "training_video_codec")


async def test_reserve_processing_refuses_seconds_outside_one_second_to_an_hour(
    sessions: SessionWorld,
) -> None:
    tenant = sessions.tenants[0]
    for seconds in (0, -5, limits.MAX_VIDEO_SECONDS + 1):
        with pytest.raises(ValueError):
            await reserve(sessions, tenant, uuid4(), seconds)
    assert await reserve(sessions, tenant, uuid4(), 1) is None
    assert await reserve(sessions, tenant, uuid4(), limits.MAX_VIDEO_SECONDS) is None
    rows = await sessions.q(
        "SELECT seconds FROM training_video_usage WHERE client_id = %s ORDER BY seconds", tenant
    )
    assert [row["seconds"] for row in rows] == [1, limits.MAX_VIDEO_SECONDS]


async def test_concurrent_claims_of_one_company_take_one_job(
    sessions: SessionWorld, owner_pool: AsyncConnectionPool, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    await only_active(sessions, tenant)
    for ago in (60, 30):
        await queued_video(sessions, tenant, created_ago=ago)
    worker = processor(sessions, owner_pool, training_store, Script())

    for _ in range(5):
        claims = await asyncio.gather(worker.claim(UUID(tenant)), worker.claim(UUID(tenant)))
        assert sum(claim is not None for claim in claims) == 1
        [running] = await sessions.q(
            "SELECT count(*)::int AS n FROM training_video_jobs"
            " WHERE client_id = %s AND status = 'processing'",
            tenant,
        )
        assert running["n"] == 1
        await sessions.q(
            "UPDATE training_video_jobs SET status = 'queued', attempts = 0, locked_at = NULL"
            " WHERE client_id = %s",
            tenant,
        )
