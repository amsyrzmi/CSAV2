"""Deleting a source or a document closes the jobs only it kept alive (knowledge cleanup, PR 1)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from csa.agents.kb import DefaultKbEmbedder
from csa.agents.kb.lifecycle import CONTENT_DELETED_ERROR
from csa.storage.base import StorageError

from tests.conftest import requires_docker
from tests.test_console_kb_uploads import enable, handle, processor, upload
from tests.test_console_services import activate
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
    assert response.status_code == 200, response.text


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
    assert response.status_code == 200, response.text
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


# Job routes: dismiss, retry, cancel and clear failed jobs (Task 3).

JOBS = "/console/v1/kb/ingest-jobs"


def job_path(tenant: str, job_id: Any, action: str = "") -> str:
    suffix = f"/{action}" if action else ""
    return f"{JOBS}/{job_id}{suffix}?client_id={tenant}"


async def only_job(world: SessionWorld, tenant: str) -> dict[str, Any]:
    [row] = await world.q(
        "SELECT id, status, attempts, error, created_at, started_at, finished_at, locked_at,"
        " dismissed_at, meta FROM kb_ingest_jobs WHERE client_id=%s",
        tenant,
    )
    return row


async def listed_jobs(world: SessionWorld, tenant: str) -> list[dict[str, Any]]:
    response = await world.admin.get(f"{JOBS}?client_id={tenant}")
    assert response.status_code == 200, response.text
    return response.json()


async def audited(world: SessionWorld, tenant: str, action: str) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT target_id::text AS target FROM console_audit WHERE client_id=%s AND action=%s",
        tenant,
        action,
    )


async def agent_off(world: SessionWorld, tenant: str) -> None:
    off = await world.mutate(
        world.admin,
        f"/console/v1/capabilities/kb_agent?client_id={tenant}",
        {"is_enabled": False},
        method="PATCH",
    )
    assert off.status_code == 200, off.text


async def test_dismiss_hides_a_failed_job_and_audits(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    await sessions.q(FAIL_JOBS, tenant)
    job = await only_job(sessions, tenant)
    response = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "dismiss"))
    assert response.status_code == 204, response.text
    assert (await only_job(sessions, tenant))["dismissed_at"] is not None
    assert await listed_jobs(sessions, tenant) == []
    assert [row["target"] for row in await audited(sessions, tenant, "kb.job_dismissed")] == [
        str(job["id"])
    ]
    # Already dismissed: the job is gone from the console, so a second call is 404.
    again = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "dismiss"))
    assert again.status_code == 404, again.text


async def test_dismiss_of_a_queued_or_processing_job_is_409(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    job = await only_job(sessions, tenant)
    queued = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "dismiss"))
    assert queued.status_code == 409 and queued.json()["detail"] == "job_still_running"
    assert await processor(sessions).claim(UUID(tenant)) is not None
    running = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "dismiss"))
    assert running.status_code == 409 and running.json()["detail"] == "job_still_running"
    assert (await only_job(sessions, tenant))["dismissed_at"] is None
    assert not await audited(sessions, tenant, "kb.job_dismissed")


async def test_dismiss_of_another_tenants_job_is_404(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    await sessions.q(FAIL_JOBS, tenant)
    job = await only_job(sessions, tenant)
    # The foreign tenant holds the package too, so the 404 proves isolation, not the gate.
    foreign_tenant = sessions.tenants[1]
    await activate(sessions, foreign_tenant, "knowledge_base")
    foreign, _ = await sessions.member(role="tenant_admin", tenant=foreign_tenant)
    for action in ("dismiss", "retry", "cancel"):
        response = await sessions.mutate(foreign, f"{JOBS}/{job['id']}/{action}")
        assert response.status_code == 404, (action, response.text)
        # A platform admin naming the other tenant is scoped the same way.
        response = await sessions.mutate(
            sessions.admin, job_path(foreign_tenant, job["id"], action)
        )
        assert response.status_code == 404, (action, response.text)
    cleared = await sessions.mutate(foreign, f"{JOBS}/dismiss-failed")
    assert cleared.status_code == 200 and cleared.json() == {"dismissed": 0}
    row = await only_job(sessions, tenant)
    assert row["status"] == "failed" and row["dismissed_at"] is None


async def test_retry_requeues_from_the_stored_original_and_resets_attempts_and_created_at(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    await sessions.q(
        "UPDATE kb_ingest_jobs SET status='failed', error='synthetic', attempts=3,"
        " started_at=now(), finished_at=now(), locked_at=now() WHERE client_id=%s",
        tenant,
    )
    before = await only_job(sessions, tenant)
    listed = await listed_jobs(sessions, tenant)
    assert listed[0]["original_stored"] is True
    response = await sessions.mutate(sessions.admin, job_path(tenant, before["id"], "retry"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(before["id"])
    assert body["status"] == "queued" and body["attempts"] == 0 and body["error"] is None
    assert body["started_at"] is None and body["finished_at"] is None
    assert body["original_stored"] is True
    after = await only_job(sessions, tenant)
    assert after["created_at"] > before["created_at"]
    assert after["locked_at"] is None and after["dismissed_at"] is None
    assert [row["target"] for row in await audited(sessions, tenant, "kb.job_retried")] == [
        str(before["id"])
    ]
    # The worker reads the stored original again.
    assert await handle(sessions) == "done"
    assert await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)


async def test_retry_of_a_partial_job_reruns_every_file_and_ends_done_with_duplicates(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (
        await upload(sessions, [("faq.csv", FAQ_CSV), ("other.csv", OTHER_CSV)])
    ).status_code == 202
    [second] = await sessions.q(
        "SELECT storage_name FROM kb_upload_files WHERE client_id=%s AND name='other.csv'", tenant
    )
    # The second stored file no longer matches its recorded hash, so that file fails.
    sessions.kb_store.put(UUID(tenant), second["storage_name"], b"question,answer\nx,y\n")
    assert await handle(sessions) == "partial"
    sessions.kb_store.put(UUID(tenant), second["storage_name"], OTHER_CSV)
    job = await only_job(sessions, tenant)
    response = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "retry"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert len(response.json()["files"]) == 2
    assert await handle(sessions) == "done"
    [listed] = await listed_jobs(sessions, tenant)
    assert [r["status"] for r in listed["meta"]["results"]] == ["duplicate", "ingested"]
    documents = await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)
    assert len(documents) == 2


async def test_retry_after_the_source_was_deleted_is_409_original_removed(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (
        await upload(sessions, [("faq.csv", FAQ_CSV), ("other.csv", OTHER_CSV)])
    ).status_code == 202
    await sessions.q(FAIL_JOBS, tenant)
    gone = next(row for row in await files(sessions, tenant) if row["name"] == "faq.csv")
    await remove_source(sessions, tenant, gone["id"])
    job = await only_job(sessions, tenant)
    # One file is still live, so the job stays on the list, but it can't be tried again.
    assert job["status"] == "failed" and job["dismissed_at"] is None
    shown = (await sessions.admin.get(job_path(tenant, job["id"]))).json()
    assert shown["original_stored"] is False
    assert "deleted_file_ids" not in shown
    response = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "retry"))
    assert response.status_code == 409 and response.json()["detail"] == "original_removed"
    assert (await only_job(sessions, tenant))["status"] == "failed"
    assert not await audited(sessions, tenant, "kb.job_retried")


async def test_retry_of_a_content_deleted_failure_is_409_original_removed(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    # Queued before the document is deleted: the deletion fences this job's content.
    assert (await upload(sessions, [("queued.csv", FAQ_CSV)])).status_code == 202
    [doc] = (await sessions.admin.get(f"/console/v1/kb/documents?client_id={tenant}")).json()
    deleted = await sessions.mutate(
        sessions.admin, f"/console/v1/kb/documents/{doc['id']}?client_id={tenant}", method="DELETE"
    )
    assert deleted.status_code == 200, deleted.text
    assert await handle(sessions) == "failed"
    [job] = [row for row in await listed_jobs(sessions, tenant) if row["status"] == "failed"]
    assert job["meta"]["results"][0]["error"] == CONTENT_DELETED_ERROR
    assert job["original_stored"] is False
    response = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "retry"))
    assert response.status_code == 409 and response.json()["detail"] == "original_removed"
    # The job's own error, written by the worker or a legacy row, is read the same way.
    await sessions.q(
        "UPDATE kb_ingest_jobs SET meta='{}'::jsonb, error=%s WHERE id=%s",
        CONTENT_DELETED_ERROR,
        UUID(job["id"]),
    )
    shown = (await sessions.admin.get(job_path(tenant, job["id"]))).json()
    assert shown["original_stored"] is False


async def test_retry_twice_queues_once(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    queued = await only_job(sessions, tenant)
    # Already queued: nothing to try again.
    early = await sessions.mutate(sessions.admin, job_path(tenant, queued["id"], "retry"))
    assert early.status_code == 409 and early.json()["detail"] == "not_retryable"
    await sessions.q(FAIL_JOBS, tenant)
    first, second = await asyncio.gather(
        sessions.mutate(sessions.admin, job_path(tenant, queued["id"], "retry")),
        sessions.mutate(sessions.admin, job_path(tenant, queued["id"], "retry")),
    )
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (first.text, second.text)
    refused = first if first.status_code == 409 else second
    assert refused.json()["detail"] == "not_retryable"
    rows = await jobs(sessions, tenant)
    assert [row["status"] for row in rows] == ["queued"]
    assert len(await audited(sessions, tenant, "kb.job_retried")) == 1


async def test_retry_when_the_knowledge_agent_is_off_is_409(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    await sessions.q(FAIL_JOBS, tenant)
    job = await only_job(sessions, tenant)
    await agent_off(sessions, tenant)
    response = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "retry"))
    assert response.status_code == 409, response.text
    assert "disabled" in response.json()["detail"]
    assert (await only_job(sessions, tenant))["status"] == "failed"
    # Taking a message off the list does not need the agent.
    dismissed = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "dismiss"))
    assert dismissed.status_code == 204, dismissed.text


async def test_cancel_a_queued_job_only(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    job = await only_job(sessions, tenant)
    await agent_off(sessions, tenant)  # A paused queued job can still be cancelled.
    response = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "cancel"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled" and response.json()["id"] == str(job["id"])
    row = await only_job(sessions, tenant)
    assert row["status"] == "cancelled" and row["dismissed_at"] is not None
    assert row["locked_at"] is None
    assert await listed_jobs(sessions, tenant) == []
    assert [r["target"] for r in await audited(sessions, tenant, "kb.job_cancelled")] == [
        str(job["id"])
    ]
    # Anything but a visible queued job is refused.
    await sessions.q(INSERT_FAILED, tenant, json.dumps([{"path": "x.csv"}]))
    [failed] = await sessions.q(
        "SELECT id FROM kb_ingest_jobs WHERE client_id=%s AND status='failed'", tenant
    )
    refused = await sessions.mutate(sessions.admin, job_path(tenant, failed["id"], "cancel"))
    assert refused.status_code == 409 and refused.json()["detail"] == "not_cancellable"
    hidden = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "cancel"))
    assert hidden.status_code == 404, hidden.text


async def test_cancel_of_a_processing_job_is_409(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    job = await only_job(sessions, tenant)
    assert await processor(sessions).claim(UUID(tenant)) is not None
    refused = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], "cancel"))
    assert refused.status_code == 409 and refused.json()["detail"] == "not_cancellable"
    assert (await only_job(sessions, tenant))["status"] == "processing"


async def test_dismiss_failed_clears_all_and_reports_the_count(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    other = sessions.tenants[1]
    entry = json.dumps([{"path": "x.csv"}])
    for status in ("failed", "partial", "cancelled", "done", "queued", "processing"):
        await sessions.q(
            "INSERT INTO kb_ingest_jobs(client_id,source,status,files)"
            " VALUES (%s,'upload',%s,%s::jsonb)",
            tenant,
            status,
            entry,
        )
    # Already dismissed jobs are not counted again; other tenants are never touched.
    await sessions.q(
        "INSERT INTO kb_ingest_jobs(client_id,source,status,files,dismissed_at)"
        " VALUES (%s,'upload','failed',%s::jsonb,now())",
        tenant,
        entry,
    )
    await sessions.q(INSERT_FAILED, other, entry)
    response = await sessions.mutate(sessions.admin, f"{JOBS}/dismiss-failed?client_id={tenant}")
    assert response.status_code == 200 and response.json() == {"dismissed": 3}, response.text
    assert sorted(job["status"] for job in await listed_jobs(sessions, tenant)) == [
        "done",
        "processing",
        "queued",
    ]
    [foreign] = await sessions.q(
        "SELECT status, dismissed_at FROM kb_ingest_jobs WHERE client_id=%s", other
    )
    assert foreign["dismissed_at"] is None
    assert [r["target"] for r in await audited(sessions, tenant, "kb.jobs_dismissed")] == [tenant]
    again = await sessions.mutate(sessions.admin, f"{JOBS}/dismiss-failed?client_id={tenant}")
    assert again.json() == {"dismissed": 0}


async def test_dismissed_jobs_are_missing_from_list_and_get(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    job = await only_job(sessions, tenant)
    assert (await sessions.admin.get(job_path(tenant, job["id"]))).status_code == 200
    await sessions.q("UPDATE kb_ingest_jobs SET dismissed_at=now() WHERE client_id=%s", tenant)
    assert await listed_jobs(sessions, tenant) == []
    assert (await sessions.admin.get(job_path(tenant, job["id"]))).status_code == 404
    for action in ("dismiss", "retry", "cancel"):
        response = await sessions.mutate(sessions.admin, job_path(tenant, job["id"], action))
        assert response.status_code == 404, (action, response.text)


async def test_a_path_only_job_counts_as_original_stored(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    for value in (
        json.dumps([{"path": "legacy.csv", "name": "legacy.csv"}]),
        json.dumps(["legacy.csv"]),
    ):
        await sessions.q(INSERT_FAILED, tenant, value)
    listed = await listed_jobs(sessions, tenant)
    assert [job["original_stored"] for job in listed] == [True, True]
    response = await sessions.mutate(sessions.admin, job_path(tenant, listed[0]["id"], "retry"))
    assert response.status_code == 200 and response.json()["status"] == "queued", response.text


@pytest.mark.parametrize("route", ["file", "document"])
async def test_delete_file_and_document_work_while_the_agent_is_off_and_answer_200_with_storage_cleanup(  # noqa: E501
    sessions: SessionWorld, route: str
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    [record] = await files(sessions, tenant)
    await agent_off(sessions, tenant)
    target = (
        f"/console/v1/kb/files/{record['id']}"
        if route == "file"
        else f"/console/v1/kb/documents/{record['document_id']}"
    )
    response = await sessions.mutate(
        sessions.admin, f"{target}?client_id={tenant}", method="DELETE"
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"storage_cleanup": "done"}
    assert sessions.kb_store.list_names(UUID(tenant)) == []
    assert not await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)


@pytest.mark.parametrize("route", ["file", "document"])
async def test_delete_reports_pending_when_the_storage_sweep_fails(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    [record] = await files(sessions, tenant)
    target = (
        f"/console/v1/kb/files/{record['id']}"
        if route == "file"
        else f"/console/v1/kb/documents/{record['document_id']}"
    )

    def broken(client_id: UUID, name: str) -> None:
        raise StorageError("synthetic storage outage")

    monkeypatch.setattr(sessions.kb_store, "delete", broken)
    response = await sessions.mutate(
        sessions.admin, f"{target}?client_id={tenant}", method="DELETE"
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"storage_cleanup": "pending"}
    assert not await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)


async def test_job_routes_need_a_writer_and_a_package(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    await sessions.q(FAIL_JOBS, tenant)
    job = await only_job(sessions, tenant)
    paths = [f"{JOBS}/{job['id']}/{action}" for action in ("dismiss", "retry", "cancel")]
    paths.append(f"{JOBS}/dismiss-failed")
    reader, _ = await sessions.member(role="read_only")
    for path in paths:
        response = await sessions.mutate(reader, path)
        assert response.status_code == 403, (path, response.text)
    # The second tenant has no knowledge base add-on: the package gate answers first.
    outsider, _ = await sessions.member(role="tenant_admin", tenant=sessions.tenants[1])
    for path in paths:
        response = await sessions.mutate(outsider, path)
        assert response.status_code == 403 and response.json()["detail"] == "package_inactive", (
            path,
            response.text,
        )
    row = await only_job(sessions, tenant)
    assert row["status"] == "failed" and row["dismissed_at"] is None
