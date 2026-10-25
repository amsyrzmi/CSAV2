"""Authenticated uploads through a real synthetic queue, worker and isolated PostgreSQL."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.agents.base import AgentContext
from csa.agents.kb import DefaultKbEmbedder, KbAgent, KbEmbeddingError, KbInput
from csa.agents.kb.ingest_jobs import JobStatus, KbIngestConfig, KbIngestProcessor
from csa.console.security import digest
from csa.contracts.callbacks import PendingActionStore
from psycopg.rows import dict_row

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import MACHINE_TOKEN, SessionWorld
from tests.test_kb_ingest import FAQ_CSV, make_docx, make_pdf

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]


async def test_gap_listing_resolution_and_audit(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    [gap] = await sessions.q(
        "INSERT INTO kb_gaps(client_id,query) VALUES (%s,%s) RETURNING id",
        tenant,
        "Synthetic unanswered question",
    )
    listed = await sessions.admin.get(f"/console/v1/kb/gaps?client_id={tenant}")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [str(gap["id"])]
    reader, _ = await sessions.member(role="read_only")
    assert (
        await sessions.mutate(reader, f"/console/v1/kb/gaps/{gap['id']}/resolve")
    ).status_code == 403
    resolved = await sessions.mutate(
        sessions.admin, f"/console/v1/kb/gaps/{gap['id']}/resolve?client_id={tenant}"
    )
    assert resolved.status_code == 200, resolved.text
    remaining = await sessions.admin.get(f"/console/v1/kb/gaps?client_id={tenant}")
    assert remaining.status_code == 200 and remaining.json() == []
    assert await sessions.q(
        "SELECT id FROM console_audit WHERE client_id=%s AND action='kb.gap_resolved'", tenant
    )


async def enable(world: SessionWorld) -> str:
    tenant = world.tenants[0]
    # customer_support activation gates the KB routes and switches kb_agent on.
    await activate(world, tenant, "customer_support")
    return tenant


async def test_concurrent_poll_upload_and_cancelled_session_recovers(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    identity_pool = sessions.service.pool
    cookie = sessions.admin.cookies["csa_session"]
    async with await psycopg.AsyncConnection.connect(sessions.owner_dsn) as blocker:
        cursor = await blocker.execute(
            "SELECT id FROM console_sessions WHERE token_hash=%s FOR UPDATE", (digest(cookie),)
        )
        assert await cursor.fetchone()
        pending = asyncio.create_task(sessions.admin.get("/console/v1/auth/me"))
        try:
            for _ in range(100):
                if (
                    identity_pool.get_stats()["pool_available"]
                    < identity_pool.get_stats()["pool_size"]
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("session lookup never entered the identity pool")
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    key = str(uuid4())
    paths = [
        "/console/v1/auth/me",
        "/console/v1/auth/csrf",
        f"/console/v1/agents?client_id={tenant}",
        f"/console/v1/kb/ingest-jobs?client_id={tenant}",
        f"/console/v1/kb/documents?client_id={tenant}",
        f"/console/v1/kb/files?client_id={tenant}",
        f"/console/v1/kb/gaps?client_id={tenant}",
    ]

    async def traffic() -> list[httpx.Response]:
        return await asyncio.gather(
            *(sessions.admin.get(path) for _ in range(5) for path in paths),
            *(upload(sessions, [("concurrent.csv", FAQ_CSV)], key=key) for _ in range(3)),
        )

    results = await asyncio.wait_for(traffic(), timeout=10)
    assert [response.status_code for response in results[:35]] == [200] * 35
    uploads = results[35:]
    assert all(response.status_code == 202 for response in uploads)
    assert len({response.json()["job_id"] for response in uploads}) == 1
    assert sum(not response.json()["duplicate_request"] for response in uploads) == 1
    assert await handle(sessions) == "done"
    for pool in (identity_pool, sessions.pool):
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT current_setting('app.client_id', true)")
                row = await cursor.fetchone()
            assert row is not None and row["current_setting"] in (None, "")
    assert (await sessions.admin.get("/console/v1/auth/me")).status_code == 200


async def upload(
    world: SessionWorld,
    files: list[tuple[str, bytes]],
    *,
    browser: httpx.AsyncClient | None = None,
    key: str | None = None,
) -> httpx.Response:
    client = browser or world.admin
    query = f"?client_id={world.tenants[0]}" if browser is None else ""
    return await client.post(
        f"/console/v1/kb/uploads{query}",
        files=[("files", (name, data)) for name, data in files],
        headers=world.headers(client) | {"Idempotency-Key": key or str(uuid4())},
    )


def processor(world: SessionWorld, embedder: DefaultKbEmbedder | None = None) -> KbIngestProcessor:
    return KbIngestProcessor(
        world.pool,
        world.pool,
        embedder=embedder or DefaultKbEmbedder(),
        config=KbIngestConfig(upload_root=world.upload_root),
    )


async def handle(world: SessionWorld, worker: KbIngestProcessor | None = None) -> str:
    worker = worker or processor(world)
    job = await worker.claim(UUID(world.tenants[0]))
    assert job is not None
    return await worker.process(job)


async def test_three_files_queue_worker_download_and_safe_contract(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    files = [
        ("faq.csv", FAQ_CSV),
        ("manual.pdf", make_pdf(["Synthetic warranty is twelve months."])),
        ("shipping.docx", make_docx("Shipping", ["Delivery takes three days."])),
    ]
    queued = await upload(sessions, files)
    assert queued.status_code == 202, queued.text
    assert not queued.json()["duplicate_request"]
    assert await handle(sessions) == "done"
    jobs = await sessions.admin.get(f"/console/v1/kb/ingest-jobs?client_id={tenant}")
    job = jobs.json()[0]
    assert job["status"] == "done"
    assert [row["status"] for row in job["meta"]["results"]] == ["ingested"] * 3
    assert set(job["files"][0]) == {"name", "file_id"}
    assert "path" not in jobs.text and "requested_by" not in jobs.text
    docs = await sessions.admin.get(f"/console/v1/kb/documents?client_id={tenant}")
    assert len(docs.json()) == 3
    assert all(row["embed_model"] == "hash-384" and row["chunk_count"] > 0 for row in docs.json())
    listed = await sessions.admin.get(f"/console/v1/kb/files?client_id={tenant}")
    ctx = AgentContext(
        client_id=UUID(tenant),
        contact_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=None,
        idempotency_key=f"synthetic-search-{uuid4()}",
        channel="web_console",
        pool=sessions.pool,
        pending_actions=PendingActionStore(sessions.pool),
    )
    for question, expected in [
        ("What are your opening hours?", "9am"),
        ("Synthetic warranty is twelve months.", "twelve months"),
        ("Delivery takes three days.", "three days"),
    ]:
        answer = await KbAgent(embedder=DefaultKbEmbedder()).run(
            ctx, KbInput(action="query", query=question, threshold=0.3)
        )
        assert answer.ok and any(expected in fact.text for fact in answer.data.facts)
    ctx = replace(ctx, client_id=UUID(sessions.tenants[1]))
    other = await KbAgent(embedder=DefaultKbEmbedder()).run(
        ctx, KbInput(action="query", query="What are your opening hours?", threshold=0.3)
    )
    assert other.ok and not any("9am" in fact.text for fact in other.data.facts)
    assert len(listed.json()) == 3
    for record in listed.json():
        downloaded = await sessions.admin.get(
            f"/console/v1/kb/files/{record['id']}/download?client_id={tenant}"
        )
        assert downloaded.status_code == 200
        assert downloaded.content == dict(files)[record["name"]]
        assert "attachment" in downloaded.headers["content-disposition"]
    assert "storage_name" not in listed.text and str(sessions.upload_root) not in listed.text
    rows = await sessions.q("SELECT action FROM console_audit WHERE client_id=%s", tenant)
    assert sum(row["action"] == "kb.upload_queued" for row in rows) == 1


async def test_duplicate_concurrent_key_conflict_and_document_versions(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    key = str(uuid4())
    first, replay = await asyncio.gather(
        *(upload(sessions, [("faq.csv", FAQ_CSV)], key=key) for _ in range(2))
    )
    assert first.status_code == replay.status_code == 202
    assert first.json()["job_id"] == replay.json()["job_id"]
    assert sorted([first.json()["duplicate_request"], replay.json()["duplicate_request"]]) == [
        False,
        True,
    ]
    conflict = await upload(sessions, [("faq.csv", FAQ_CSV + b"new,answer\n")], key=key)
    assert conflict.status_code == 409
    assert await handle(sessions) == "done"
    assert (await upload(sessions, [("alias.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    jobs = (await sessions.admin.get(f"/console/v1/kb/ingest-jobs?client_id={tenant}")).json()
    assert jobs[0]["meta"]["results"][0]["status"] == "duplicate"
    assert (
        await upload(sessions, [("faq.csv", FAQ_CSV + b"New question?,New answer.\n")])
    ).status_code == 202
    assert await handle(sessions) == "done"
    docs = (await sessions.admin.get(f"/console/v1/kb/documents?client_id={tenant}")).json()
    assert sorted((row["version"], row["is_active"]) for row in docs) == [(1, False), (2, True)]
    directory = sessions.upload_root / tenant
    assert len(list(directory.iterdir())) == 3


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
async def test_upload_roles_scoping_csrf_and_machine(sessions: SessionWorld, role: str) -> None:
    tenant = await enable(sessions)
    client, _ = await sessions.member(role=role)
    response = await upload(sessions, [("faq.csv", FAQ_CSV)], browser=client)
    assert response.status_code == (403 if role == "read_only" else 202), response.text
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    own = (await client.get("/console/v1/kb/files")).json()[0]
    # The foreign tenant holds the package too, so the 404 proves isolation, not the gate.
    await activate(sessions, sessions.tenants[1], "customer_support")
    foreign, _ = await sessions.member(tenant=sessions.tenants[1])
    assert (await foreign.get(f"/console/v1/kb/files/{own['id']}/download")).status_code == 404
    assert (
        await client.get(
            f"/console/v1/kb/files/{own['id']}/download?client_id={sessions.tenants[1]}"
        )
    ).status_code == 403
    csrf = await client.post(
        "/console/v1/kb/uploads",
        files={"files": ("faq.csv", FAQ_CSV)},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert csrf.status_code == 403
    if role == "read_only":
        assert (
            await sessions.mutate(client, f"/console/v1/kb/files/{own['id']}", method="DELETE")
        ).status_code == 403
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sessions.app), base_url="https://console.example.test"
    ) as machine:
        headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": tenant}
        assert (await machine.get("/console/v1/kb/files", headers=headers)).status_code == 403
        assert (
            await machine.get(f"/console/v1/kb/files/{own['id']}/download", headers=headers)
        ).status_code == 403


async def test_invalid_uploads_and_exact_bounds(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    for files, expected in [
        ([("a.csv", FAQ_CSV)] * 4, 422),
        ([("../bad.csv", FAQ_CSV)], 422),
        ([("C:\\bad.csv", FAQ_CSV)], 422),
        ([("empty.csv", b"")], 422),
        ([("old.doc", b"bad")], 422),
        ([("bad.pdf", FAQ_CSV)], 422),
        ([("large.csv", b"x" * (10 * 1024 * 1024 + 1))], 413),
    ]:
        response = await upload(sessions, files)
        assert response.status_code == expected, response.text
    # Valid CSV exactly at the public per-file limit.
    data = FAQ_CSV + b"\n" * (10 * 1024 * 1024 - len(FAQ_CSV))
    assert len(data) == 10 * 1024 * 1024
    exact = await upload(sessions, [("bound.csv", data)])
    assert exact.status_code == 202, exact.text
    assert len(await sessions.q("SELECT id FROM kb_ingest_jobs WHERE client_id=%s", tenant)) == 1


async def test_deleted_during_embedding_cannot_resurrect(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    entered, release = asyncio.Event(), asyncio.Event()

    class Paused(DefaultKbEmbedder):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            entered.set()
            await release.wait()
            return await super().embed_batch(texts)

    worker = processor(sessions, Paused())
    task = asyncio.create_task(handle(sessions, worker))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        source = (await sessions.admin.get(f"/console/v1/kb/files?client_id={tenant}")).json()[0]
        deleted = await sessions.mutate(
            sessions.admin,
            f"/console/v1/kb/files/{source['id']}?client_id={tenant}",
            method="DELETE",
        )
        assert deleted.status_code == 204, deleted.text
    finally:
        release.set()
    assert await task == "failed"
    assert not await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)
    assert not list((sessions.upload_root / tenant).iterdir())
    assert (
        await sessions.admin.get(f"/console/v1/kb/files/{source['id']}/download?client_id={tenant}")
    ).status_code == 404


async def test_document_delete_blocks_older_unattached_jobs_and_removes_aliases(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    assert (await upload(sessions, [("alias.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"
    doc = (await sessions.admin.get(f"/console/v1/kb/documents?client_id={tenant}")).json()[0]
    # This queued alias is not linked yet; deleting the document fences its hash.
    assert (await upload(sessions, [("queued.csv", FAQ_CSV)])).status_code == 202
    response = await sessions.mutate(
        sessions.admin, f"/console/v1/kb/documents/{doc['id']}?client_id={tenant}", method="DELETE"
    )
    assert response.status_code == 204, response.text
    assert await handle(sessions) == "failed"
    assert not await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)
    [count] = await sessions.q(
        "SELECT count(*) AS n FROM kb_upload_files WHERE client_id=%s AND deleted_at IS NOT NULL",
        tenant,
    )
    assert count["n"] == 2
    assert (await upload(sessions, [("fresh.csv", FAQ_CSV)])).status_code == 202
    assert await handle(sessions) == "done"  # A deliberate new upload after deletion is allowed.
    assert await sessions.q(
        "SELECT id FROM console_audit WHERE client_id=%s AND action='kb.document_deleted'", tenant
    )


async def test_publication_rollback_and_reconciliation(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await enable(sessions)
    import csa.console.kb_uploads as storage

    original = storage.publish

    def fail_after_publish(directory: Path, name: str, data: bytes) -> Path:
        original(directory, name, data)
        raise OSError("synthetic disk fsync failure")

    monkeypatch.setattr(storage, "publish", fail_after_publish)
    with pytest.raises(OSError, match="synthetic"):
        await upload(sessions, [("faq.csv", FAQ_CSV)])
    directory = sessions.upload_root / tenant
    assert not list(directory.iterdir())
    assert not await sessions.q("SELECT id FROM kb_ingest_jobs WHERE client_id=%s", tenant)
    monkeypatch.setattr(storage, "publish", original)
    assert (await upload(sessions, [("faq.csv", FAQ_CSV)])).status_code == 202
    orphan = directory / f"{uuid4()}.csv"
    partial = directory / f".{uuid4()}.pdf.part"
    unknown = directory / "operator-note.txt"
    for path in [orphan, partial, unknown]:
        path.write_bytes(b"synthetic")
    reconciled = await sessions.mutate(
        sessions.admin, f"/console/v1/kb/storage/reconcile?client_id={tenant}"
    )
    assert reconciled.status_code == 200 and reconciled.json() == {"removed_files": 2}
    assert await sessions.q(
        "SELECT id FROM console_audit WHERE client_id=%s AND action='kb.storage_reconciled'", tenant
    )
    assert unknown.exists()
    assert len(list(directory.iterdir())) == 2


async def test_partial_retry_cannot_recreate_deleted_first_result(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)

    class FailSecond(DefaultKbEmbedder):
        calls = 0

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            if self.calls == 2:
                raise KbEmbeddingError("synthetic temporary embedder failure")
            return await super().embed_batch(texts)

    assert (
        await upload(
            sessions,
            [("faq.csv", FAQ_CSV), ("guide.pdf", make_pdf(["Synthetic guide information."]))],
        )
    ).status_code == 202
    worker = processor(sessions, FailSecond())
    assert await handle(sessions, worker) == "queued"
    doc = (await sessions.admin.get(f"/console/v1/kb/documents?client_id={tenant}")).json()[0]
    assert (
        await sessions.mutate(
            sessions.admin,
            f"/console/v1/kb/documents/{doc['id']}?client_id={tenant}",
            method="DELETE",
        )
    ).status_code == 204
    await sessions.q(
        "UPDATE kb_ingest_jobs SET locked_at=now()-interval '1 second' WHERE client_id=%s", tenant
    )
    assert await handle(sessions, worker) == "partial"
    job = (await sessions.admin.get(f"/console/v1/kb/ingest-jobs?client_id={tenant}")).json()[0]
    assert [result["status"] for result in job["meta"]["results"]] == ["failed", "ingested"]
    assert job["attempts"] == 2
    remaining = (await sessions.admin.get(f"/console/v1/kb/documents?client_id={tenant}")).json()
    assert len(remaining) == 1 and remaining[0]["file_name"] == "guide.pdf"


async def test_legacy_source_job_is_fenced_during_embedding(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    # A legacy persisted path has no managed file ID, so the hash fence is essential.
    directory = sessions.upload_root / tenant
    directory.mkdir()
    (directory / "legacy.csv").write_bytes(FAQ_CSV)
    entered, release = asyncio.Event(), asyncio.Event()

    class Pause(DefaultKbEmbedder):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            entered.set()
            await release.wait()
            return await super().embed_batch(texts)

    await sessions.q(
        "INSERT INTO kb_ingest_jobs(client_id,source,files) VALUES (%s,'upload',%s::jsonb)",
        tenant,
        '["legacy.csv"]',
    )
    worker = processor(sessions, Pause())
    task = asyncio.create_task(handle(sessions, worker))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # A separate deliberate text ingestion publishes the same hash while legacy work embeds.
        import hashlib

        from csa.agents.kb.extract import extract_document
        from csa.agents.kb.store import store_document
        from csa.core.tenancy import tx

        document = extract_document(FAQ_CSV, "csv", title="same content")
        embedder = DefaultKbEmbedder()
        vectors = await embedder.embed_batch([chunk.content for chunk in document.chunks])
        async with tx(sessions.pool, tenant) as conn:
            stored = await store_document(
                conn,
                client_id=UUID(tenant),
                title="same content",
                chunks=document.chunks,
                embeddings=vectors,
                content_hash=hashlib.sha256(FAQ_CSV).hexdigest(),
                embed_model=embedder.model_name,
            )
        deleted = await sessions.mutate(
            sessions.admin,
            f"/console/v1/kb/documents/{stored.document_id}?client_id={tenant}",
            method="DELETE",
        )
        assert deleted.status_code == 204
    finally:
        release.set()
    assert await task == "failed"
    assert not await sessions.q("SELECT id FROM kb_documents WHERE client_id=%s", tenant)


async def test_commit_acknowledgement_loss_retains_durable_sources(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager

    import csa.console.kb_uploads as storage

    tenant = await enable(sessions)
    real_tx = storage.tx
    raised = False

    @asynccontextmanager
    async def lose_ack(*args: Any, **kwargs: Any):
        nonlocal raised
        async with real_tx(*args, **kwargs) as conn:
            yield conn
        if not raised:
            raised = True
            raise OSError("synthetic lost COMMIT acknowledgement")

    monkeypatch.setattr(storage, "tx", lose_ack)
    key = str(uuid4())
    with pytest.raises(OSError, match="COMMIT"):
        await upload(sessions, [("faq.csv", FAQ_CSV)], key=key)
    [job] = await sessions.q("SELECT id FROM kb_ingest_jobs WHERE client_id=%s", tenant)
    assert len(list((sessions.upload_root / tenant).iterdir())) == 1
    replay = await upload(sessions, [("faq.csv", FAQ_CSV)], key=key)
    assert replay.status_code == 202 and replay.json() == {
        "job_id": str(job["id"]),
        "duplicate_request": True,
    }
    assert await handle(sessions) == "done"
    assert len(await sessions.q("SELECT id FROM kb_ingest_jobs WHERE client_id=%s", tenant)) == 1
    assert len(await sessions.q("SELECT id FROM kb_upload_batches WHERE client_id=%s", tenant)) == 1
    assert len(await sessions.q("SELECT id FROM kb_upload_files WHERE client_id=%s", tenant)) == 1
    assert (
        len(
            await sessions.q(
                "SELECT id FROM console_audit WHERE client_id=%s AND action='kb.upload_queued'",
                tenant,
            )
        )
        == 1
    )


async def test_worker_crash_after_document_commit_retries_without_duplicate_chunks(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await enable(sessions)
    queued = await upload(sessions, [("crash-window.csv", FAQ_CSV)])
    assert queued.status_code == 202, queued.text

    worker = processor(sessions)
    job = await worker.claim(UUID(tenant))
    assert job is not None and job["attempts"] == 1
    real_finish = worker._finish
    crashed = False

    async def fail_once(
        client_id: UUID,
        job_id: UUID,
        status: JobStatus,
        error: str | None,
        results: list[dict[str, Any]],
    ) -> None:
        nonlocal crashed
        if status == "done" and not crashed:
            crashed = True
            raise OSError("synthetic worker crash after document commit")
        await real_finish(client_id, job_id, status, error, results)

    monkeypatch.setattr(worker, "_finish", fail_once)
    with pytest.raises(OSError, match="worker crash after document commit"):
        await worker.process(job)
    assert crashed

    [interrupted] = await sessions.q(
        "SELECT status, attempts FROM kb_ingest_jobs WHERE id=%s", job["id"]
    )
    assert (interrupted["status"], interrupted["attempts"]) == ("processing", 1)
    [stored] = await sessions.q(
        "SELECT id, chunk_count FROM kb_documents WHERE client_id=%s", tenant
    )
    [chunks] = await sessions.q(
        "SELECT count(*) AS n FROM kb_chunks WHERE document_id=%s", stored["id"]
    )
    assert chunks["n"] == stored["chunk_count"] > 0
    [source] = await sessions.q(
        "SELECT document_id FROM kb_upload_files WHERE client_id=%s", tenant
    )
    assert source["document_id"] == stored["id"]

    await sessions.q(
        "UPDATE kb_ingest_jobs SET locked_at=now()-interval '1 hour' WHERE id=%s",
        job["id"],
    )
    retry = await worker.claim(UUID(tenant))
    assert retry is not None and retry["id"] == job["id"] and retry["attempts"] == 2
    assert await worker.process(retry) == "done"

    [completed] = await sessions.q(
        "SELECT status, attempts, meta FROM kb_ingest_jobs WHERE id=%s", job["id"]
    )
    assert completed["status"] == "done" and completed["attempts"] == 2
    assert completed["meta"]["results"][0]["status"] == "duplicate"
    assert len(await sessions.q("SELECT id FROM kb_ingest_jobs WHERE client_id=%s", tenant)) == 1
    [document_count] = await sessions.q(
        "SELECT count(*) AS n FROM kb_documents WHERE client_id=%s", tenant
    )
    [final_chunks] = await sessions.q(
        "SELECT count(*) AS n FROM kb_chunks WHERE document_id=%s", stored["id"]
    )
    assert document_count["n"] == 1 and final_chunks["n"] == chunks["n"]
    assert (
        len(
            await sessions.q(
                "SELECT id FROM console_audit WHERE client_id=%s AND action='kb.upload_queued'",
                tenant,
            )
        )
        == 1
    )


async def test_delete_between_resolution_and_read_is_per_file_failure(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    import csa.agents.kb.ingest_jobs as jobs

    tenant = await enable(sessions)
    assert (
        await upload(
            sessions,
            [("faq.csv", FAQ_CSV), ("guide.pdf", make_pdf(["Remaining synthetic source."]))],
        )
    ).status_code == 202
    files = (await sessions.admin.get(f"/console/v1/kb/files?client_id={tenant}")).json()
    source = next(row for row in files if row["name"] == "faq.csv")
    entered, release = threading.Event(), threading.Event()
    original = jobs.read_upload

    def paused(path: Path, max_bytes: int) -> bytes:
        if path.suffix == ".csv":
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError("synthetic read pause timed out")
        return original(path, max_bytes)

    monkeypatch.setattr(jobs, "read_upload", paused)
    task = asyncio.create_task(handle(sessions))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        deleted = await sessions.mutate(
            sessions.admin,
            f"/console/v1/kb/files/{source['id']}?client_id={tenant}",
            method="DELETE",
        )
        assert deleted.status_code == 204
    finally:
        release.set()
    assert await task == "partial"
    job = (await sessions.admin.get(f"/console/v1/kb/ingest-jobs?client_id={tenant}")).json()[0]
    assert [result["status"] for result in job["meta"]["results"]] == ["failed", "ingested"]
