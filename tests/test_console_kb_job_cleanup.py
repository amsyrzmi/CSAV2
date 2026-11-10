"""Deleting a source or a document closes the jobs only it kept alive (knowledge cleanup, PR 1)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from csa.agents.kb import DefaultKbEmbedder

from tests.conftest import requires_docker
from tests.test_console_kb_uploads import enable, handle, processor, upload
from tests.test_console_sessions import SessionWorld
from tests.test_kb_ingest import FAQ_CSV

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

FAIL_JOBS = (
    "UPDATE kb_ingest_jobs SET status='failed', error='synthetic', finished_at=now()"
    " WHERE client_id=%s"
)
INSERT_FAILED = (
    "INSERT INTO kb_ingest_jobs(client_id,source,status,files)"
    " VALUES (%s,'upload','failed',%s::jsonb)"
)
OTHER_CSV = b"question,answer\nWhere are you?,Synthetic street.\n"


async def jobs(world: SessionWorld, tenant: str) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT id, status, dismissed_at, locked_at, finished_at, files"
        " FROM kb_ingest_jobs WHERE client_id=%s ORDER BY created_at",
        tenant,
    )


async def files(world: SessionWorld, tenant: str) -> list[dict[str, Any]]:
    listed = await world.admin.get(f"/console/v1/kb/files?client_id={tenant}")
    assert listed.status_code == 200, listed.text
    return listed.json()


async def remove_source(world: SessionWorld, tenant: str, file_id: str) -> None:
    response = await world.mutate(
        world.admin, f"/console/v1/kb/files/{file_id}?client_id={tenant}", method="DELETE"
    )
    assert response.status_code == 204, response.text


def assert_closed(job: dict[str, Any]) -> None:
    assert job["status"] == "cancelled"
    assert job["dismissed_at"] is not None
    assert job["locked_at"] is None
    assert job["finished_at"] is not None


async def test_removing_the_only_file_of_a_failed_job_cancels_and_hides_it(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    # A job that failed while its file was stored: the owner can still try it again.
    await sessions.q(
        FAIL_JOBS,
        tenant,
    )
    [source] = await files(sessions, tenant)
    await remove_source(sessions, tenant, source["id"])
    [job] = await jobs(sessions, tenant)
    assert_closed(job)


async def test_removing_a_document_closes_the_jobs_of_its_files(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    # A later upload of the same content is queued; its file is not linked to the document yet.
    assert (await upload(sessions, [("queued.csv", FAQ_CSV)])).status_code == 202
    # The first job later reads as partial (a retry); its file is linked to the document.
    await sessions.q(
        "UPDATE kb_ingest_jobs SET status='partial' WHERE client_id=%s AND status='done'", tenant
    )
    [doc] = (await sessions.admin.get(f"/console/v1/kb/documents?client_id={tenant}")).json()
    response = await sessions.mutate(
        sessions.admin, f"/console/v1/kb/documents/{doc['id']}?client_id={tenant}", method="DELETE"
    )
    assert response.status_code == 204, response.text
    [first, second] = await jobs(sessions, tenant)
    assert_closed(first)
    # The queued alias still has a live file, so its job stays for the worker to settle.
    assert second["status"] == "queued" and second["dismissed_at"] is None


async def test_a_job_with_another_live_file_is_left_untouched(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (
        await upload(sessions, [("faq.csv", FAQ_CSV), ("other.csv", OTHER_CSV)])
    ).status_code == 202
    await sessions.q(
        FAIL_JOBS,
        tenant,
    )
    listed = await files(sessions, tenant)
    gone = next(row for row in listed if row["name"] == "faq.csv")
    await remove_source(sessions, tenant, gone["id"])
    [job] = await jobs(sessions, tenant)
    assert job["status"] == "failed" and job["dismissed_at"] is None
    # Removing the second file as well leaves no live file: now the job closes.
    kept = next(row for row in listed if row["name"] == "other.csv")
    await remove_source(sessions, tenant, kept["id"])
    [job] = await jobs(sessions, tenant)
    assert_closed(job)


async def test_a_done_job_is_never_changed_by_a_delete(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    [before] = await jobs(sessions, tenant)
    [source] = await files(sessions, tenant)
    await remove_source(sessions, tenant, source["id"])
    [after] = await jobs(sessions, tenant)
    assert after == before
    assert after["status"] == "done" and after["dismissed_at"] is None


async def test_a_path_only_job_is_never_closed(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    [source] = await files(sessions, tenant)
    # A legacy job: entries carry a path or a bare string and no file_id.
    legacy = [
        json.dumps([{"path": "legacy.csv", "name": "legacy.csv"}]),
        json.dumps(["legacy.csv"]),
        json.dumps([{"path": "legacy.csv"}, {"file_id": source["id"], "name": "faq.csv"}]),
    ]
    for value in legacy:
        await sessions.q(
            INSERT_FAILED,
            tenant,
            value,
        )
    await remove_source(sessions, tenant, source["id"])
    rows = await jobs(sessions, tenant)
    assert len(rows) == 4
    legacy_rows = [
        row
        for row in rows
        if row["files"] in ([{"path": "legacy.csv", "name": "legacy.csv"}], ["legacy.csv"])
    ]
    assert len(legacy_rows) == 2
    mixed = [row for row in rows if len(row["files"]) == 2]
    assert len(mixed) == 1
    for row in legacy_rows + mixed:
        assert row["status"] == "failed" and row["dismissed_at"] is None
    # The upload's own job (one entry, the removed file) is the only one that closed.
    assert [row["status"] for row in rows].count("cancelled") == 1


async def test_a_job_with_malformed_files_does_not_break_a_delete(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    [source] = await files(sessions, tenant)
    for value in ['"text"', "{}", "[]", "null", "[null]"]:
        await sessions.q(
            INSERT_FAILED,
            tenant,
            value,
        )
    await remove_source(sessions, tenant, source["id"])
    rows = await jobs(sessions, tenant)
    assert [row["status"] for row in rows].count("cancelled") == 1
    assert [row["status"] for row in rows].count("failed") == 5


async def test_remove_while_a_worker_holds_the_job_ignores_the_workers_result(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    worker = processor(sessions)
    job = await worker.claim(UUID(tenant))
    assert job is not None
    [row] = await jobs(sessions, tenant)
    assert row["status"] == "processing"
    [source] = await files(sessions, tenant)
    await remove_source(sessions, tenant, source["id"])
    [row] = await jobs(sessions, tenant)
    assert_closed(row)
    # The worker finishes late and reports a failure; the closed job must not change.
    await worker._finish(UUID(tenant), job["id"], "failed", "x", [])
    [row] = await jobs(sessions, tenant)
    assert_closed(row)
    assert not await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)


async def test_remove_during_embedding_leaves_the_job_cancelled_and_no_document(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    entered, release = asyncio.Event(), asyncio.Event()

    class Paused(DefaultKbEmbedder):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            entered.set()
            await release.wait()
            return await super().embed_batch(texts)

    task = asyncio.create_task(handle(sessions, processor(sessions, Paused())))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        [source] = await files(sessions, tenant)
        await remove_source(sessions, tenant, source["id"])
    finally:
        release.set()
    await task
    [row] = await jobs(sessions, tenant)
    assert_closed(row)
    assert not await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)
