"""Video items in the training library: list, edit, summary rules, quotas, sweep and removal."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from csa.core import db
from csa.core.tenancy import tx
from csa.storage.base import ObjectStore, StorageError
from csa.storage.local import LocalObjectStore
from csa.training import limits
from csa.training.service import cleanup
from csa.training.store_sweep import STORED
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld
from tests.test_training_api import (
    ITEM_FIELDS,
    ITEMS,
    audits,
    detail,
    enable,
    make_owner,
    names,
    patch,
    remove,
    rows,
    upload,
)
from tests.test_training_pdf import make_pdf

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

STATES = ("uploading", "uploaded", "transcribing", "summarising", "ready", "failed")
ZERO_VECTOR = "[" + ",".join(["0"] * 384) + "]"
MEGABYTE = 1024 * 1024


class RecordingStore(LocalObjectStore):
    """The local store that remembers every abort, and can be told to fail one."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.aborts: list[tuple[UUID, str, str | None]] = []
        self.abort_error: StorageError | None = None

    def abort_upload(self, tenant: UUID, name: str, upload_id: str | None) -> None:
        self.aborts.append((tenant, name, upload_id))
        if self.abort_error is not None:
            raise self.abort_error
        super().abort_upload(tenant, name, upload_id)


@pytest.fixture
def training_store(tmp_path: Path) -> ObjectStore:
    return RecordingStore(tmp_path / "training")


async def video_row(
    world: SessionWorld,
    tenant: str,
    *,
    status: str,
    storage_name: str | None = None,
    failure_code: str | None = None,
    title: str | None = None,
    speech: str | None = None,
    video_codec: str | None = None,
    byte_size: int = 4 * MEGABYTE,
) -> str:
    """A video item inserted by SQL, as the upload and the worker will leave it."""
    [made] = await world.q(
        "INSERT INTO training_items (id, client_id, kind, title, view_mode, storage_name,"
        " original_name, media_type, byte_size, sha256, page_count, request_key, fingerprint,"
        " status, container, duration_ms, speech, failure_code, video_codec)"
        " VALUES (gen_random_uuid(), %s, 'video', %s, NULL, %s, 'induction.mp4', 'video/mp4', %s,"
        " %s, NULL, %s, 'synthetic', %s, 'mp4', 90000, %s, %s, %s) RETURNING id",
        tenant,
        title or f"Video {status}",
        storage_name or f"{uuid4()}.mp4",
        byte_size,
        None if status == "uploading" else "0" * 64,
        uuid4(),
        status,
        speech if speech is not None else ("speech" if status == "ready" else None),
        failure_code
        if failure_code is not None
        else ("training_video_codec" if status == "failed" else None),
        video_codec,
    )
    return str(made["id"])


async def job_row(
    world: SessionWorld,
    tenant: str,
    item: str,
    *,
    stage: str = "transcribing",
    status: str = "processing",
    chunks_done: int = 0,
    chunks_total: int | None = None,
    error_code: str | None = None,
) -> None:
    await world.q(
        "INSERT INTO training_video_jobs (client_id, item_id, stage, status, chunks_done,"
        " chunks_total, error_code) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        tenant,
        item,
        stage,
        status,
        chunks_done,
        chunks_total,
        error_code,
    )


async def upload_row(
    world: SessionWorld, tenant: str, item: str, upload_id: str | None = "synthetic-upload"
) -> None:
    await world.q(
        "INSERT INTO training_video_uploads (client_id, item_id, upload_id, piece_size,"
        " piece_count, declared_bytes) VALUES (%s, %s, %s, 5242880, 1, 4194304)",
        tenant,
        item,
        upload_id,
    )


async def count(world: SessionWorld, table: str, tenant: str) -> int:
    [row] = await world.q(
        f"SELECT count(*)::int AS n FROM {table} WHERE client_id=%s",  # type: ignore[arg-type]
        tenant,
    )
    return int(row["n"])


def put_file(tmp_path: Path, tenant: str, name: str, data: bytes = b"synthetic") -> Path:
    directory = tmp_path / "training" / tenant
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


async def all_states(world: SessionWorld, tenant: str) -> dict[str, str]:
    """One video per state, each with the job or failure that state carries."""
    made = {state: await video_row(world, tenant, status=state) for state in STATES}
    await job_row(
        world, tenant, made["uploaded"], stage="transcribing", status="queued", chunks_total=None
    )
    await job_row(
        world, tenant, made["transcribing"], chunks_done=3, chunks_total=12, status="processing"
    )
    await job_row(
        world, tenant, made["summarising"], stage="summarising", chunks_done=12, chunks_total=12
    )
    return made


async def test_reader_sees_only_ready_videos_and_managers_see_every_state(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    owner, owner_me = await sessions.member()
    await make_owner(sessions, tenant, owner_me["user"]["id"])
    staff, _ = await sessions.member()
    reader, _ = await sessions.member(role="read_only")
    made = await all_states(sessions, tenant)

    for member in (staff, reader):
        listed = await member.get(ITEMS)
        assert listed.status_code == 200, listed.text
        assert [row["id"] for row in listed.json()["items"]] == [made["ready"]]
        assert listed.json()["items"][0]["status"] == "ready"
    for manager in (admin, owner, sessions.admin):
        query = f"?client_id={tenant}" if manager is sessions.admin else ""
        listed = await manager.get(ITEMS + query)
        assert listed.status_code == 200, listed.text
        items = {row["status"]: row for row in listed.json()["items"]}
        assert set(items) == set(STATES) and len(listed.json()["items"]) == 6
        assert all(row["kind"] == "video" for row in items.values())
        assert items["failed"]["failure"] == "training_video_codec"
        assert items["transcribing"]["processing"] == {
            "stage": "transcribing",
            "chunks_done": 3,
            "chunks_total": 12,
            "waiting": False,
        }
        assert items["summarising"]["processing"]["stage"] == "summarising"
        assert items["uploaded"]["processing"] == {
            "stage": "transcribing",
            "chunks_done": 0,
            "chunks_total": None,
            "waiting": False,
        }
        assert items["uploading"]["processing"] is None
        assert items["ready"]["processing"] is None and items["ready"]["failure"] is None
    # A video that is not ready cannot be reached by a reader in any way.
    for state in (*STATES[:-2], "failed"):
        if state == "ready":
            continue
        assert (await staff.get(f"{ITEMS}/{made[state]}/summary")).status_code == 404


async def test_item_out_shapes_for_pdf_and_video(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    created = await upload(sessions, admin, make_pdf(2))
    assert created.status_code == 201, created.text
    pdf = created.json()
    assert set(pdf) == ITEM_FIELDS
    assert pdf["kind"] == "pdf" and pdf["status"] == "ready" and pdf["duration_ms"] is None
    assert pdf["view_mode"] == "document" and pdf["page_count"] == 2
    assert pdf["failure"] is None and pdf["processing"] is None and pdf["notice"] is None
    video = await video_row(sessions, tenant, status="ready")
    listed = (await admin.get(ITEMS)).json()["items"]
    assert all(set(row) == ITEM_FIELDS for row in listed)
    by_id = {row["id"]: row for row in listed}
    assert by_id[pdf["id"]]["kind"] == "pdf"
    shown = by_id[video]
    assert shown["kind"] == "video" and shown["status"] == "ready"
    assert shown["page_count"] is None and shown["view_mode"] is None
    assert shown["duration_ms"] == 90000 and shown["byte_size"] == 4 * MEGABYTE
    assert shown["has_summary"] is False


async def test_failure_progress_and_notice_are_for_managers_only(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    staff, _ = await sessions.member()
    hevc = await video_row(sessions, tenant, status="ready", video_codec="hevc")
    plain = await video_row(sessions, tenant, status="ready", video_codec="h264")
    waiting = await video_row(sessions, tenant, status="transcribing")
    await job_row(
        sessions,
        tenant,
        waiting,
        status="queued",
        chunks_total=4,
        error_code="training_processing_limit",
    )
    failed = await video_row(
        sessions, tenant, status="failed", failure_code="training_video_unreadable"
    )

    managed = {row["id"]: row for row in (await admin.get(ITEMS)).json()["items"]}
    assert managed[hevc]["notice"] == "hevc" and managed[plain]["notice"] is None
    assert managed[waiting]["processing"]["waiting"] is True
    assert managed[failed]["failure"] == "training_video_unreadable"

    seen = {row["id"]: row for row in (await staff.get(ITEMS)).json()["items"]}
    assert set(seen) == {hevc, plain}
    for row in seen.values():
        assert row["notice"] is None and row["failure"] is None and row["processing"] is None
    # A job's progress never reaches a reader even through the ready row's neighbours.
    assert failed not in seen and waiting not in seen


async def test_patch_video_title_ok_and_view_mode_is_422(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    ready = await video_row(sessions, tenant, status="ready")
    failed = await video_row(sessions, tenant, status="failed")

    edited = await patch(
        sessions, admin, ready, {"title": "Renamed", "description": "Day one", "category": "Safety"}
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert set(body) == ITEM_FIELDS
    assert (body["title"], body["description"], body["category"]) == (
        "Renamed",
        "Day one",
        "Safety",
    )
    assert body["kind"] == "video" and body["view_mode"] is None and body["page_count"] is None
    assert (await patch(sessions, admin, failed, {"title": "Still editable"})).status_code == 200
    refused = await patch(sessions, admin, ready, {"view_mode": "slides"})
    assert refused.status_code == 422 and detail(refused) == "training_not_pdf", refused.text
    mixed = await patch(sessions, admin, ready, {"title": "Never applied", "view_mode": "document"})
    assert mixed.status_code == 422 and detail(mixed) == "training_not_pdf", mixed.text
    [row] = [r for r in await rows(sessions, tenant) if str(r["id"]) == ready]
    assert row["title"] == "Renamed" and row["view_mode"] is None
    assert len(await audits(sessions, tenant, "training.item_edited")) == 2
    staff, _ = await sessions.member()
    refused = await patch(sessions, staff, ready, {"title": "Taken over"})
    assert refused.status_code == 403 and detail(refused) == "training_forbidden"


async def test_summary_post_on_video_is_409_and_file_route_is_404(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    staff, _ = await sessions.member()
    reader, _ = await sessions.member(role="read_only")
    ready = await video_row(sessions, tenant, status="ready", speech="speech")
    silent = await video_row(sessions, tenant, status="ready", speech="no_speech")
    failed = await video_row(sessions, tenant, status="failed")
    await sessions.q(
        "UPDATE training_items SET summary=%s, summary_model='synthetic'"
        " WHERE id = ANY(%s::uuid[])",
        '{"summary": "Welcome to the induction.", "key_points": ["One"]}',
        [ready, silent, failed],
    )

    for browser in (admin, staff, reader):
        for body in ({}, {"regenerate": False}):
            answer = await sessions.mutate(browser, f"{ITEMS}/{ready}/summary", body)
            assert answer.status_code == 409, answer.text
            assert detail(answer) == "training_summary_automatic"
        assert (await browser.get(f"{ITEMS}/{ready}/file")).status_code == 404
    regenerate = await sessions.mutate(admin, f"{ITEMS}/{ready}/summary", {"regenerate": True})
    assert regenerate.status_code == 409 and detail(regenerate) == "training_summary_automatic"
    assert (
        await sessions.mutate(staff, f"{ITEMS}/{ready}/summary", {"regenerate": True})
    ).status_code == 403

    # Reading the stored summary works for a video as for a PDF.
    for browser in (admin, staff, reader):
        shown = await browser.get(f"{ITEMS}/{ready}/summary")
        assert shown.status_code == 200, shown.text
        assert shown.json()["summary"] == "Welcome to the induction."
        assert shown.json()["key_points"] == ["One"]
    # A silent or failed video never shows a summary, even one a stray write left behind.
    for item in (silent, failed):
        shown = await admin.get(f"{ITEMS}/{item}/summary")
        assert shown.status_code == 200 and shown.json() == {"summary": None}, shown.text
    listed = {row["id"]: row for row in (await admin.get(ITEMS)).json()["items"]}
    assert listed[ready]["has_summary"] is True
    assert listed[silent]["has_summary"] is False and listed[failed]["has_summary"] is False
    assert (await staff.get(f"{ITEMS}/{failed}/summary")).status_code == 404
    answer = await sessions.mutate(staff, f"{ITEMS}/{failed}/summary", {})
    assert answer.status_code == 404 and detail(answer) == "training_not_found"


async def test_pdf_and_video_quotas_are_counted_apart(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    small = make_pdf(1)
    # Video bytes and video rows do not use up the PDF allowance.
    monkeypatch.setattr(limits, "MAX_TOTAL_BYTES", 2 * len(small) + 10)
    monkeypatch.setattr(limits, "MAX_ITEMS", 2)
    for state in ("ready", "uploading", "failed"):
        # The failed one is retryable, so it still holds its file and its bytes.
        code = "training_transcription_unavailable" if state == "failed" else None
        await video_row(sessions, tenant, status=state, failure_code=code, byte_size=100 * MEGABYTE)
    assert (await upload(sessions, admin, small, "one.pdf")).status_code == 201
    assert (await upload(sessions, admin, make_pdf(1), "two.pdf")).status_code == 201
    full = await upload(sessions, admin, make_pdf(1), "three.pdf")
    assert full.status_code == 409 and detail(full) == "training_library_full", full.text

    # And PDF bytes and rows do not count as video: the video usage sees only video rows.
    async with tx(sessions.pool, tenant) as conn:
        usage = await db.q.fetch_one(
            "training/video_usage.sql", {"client_id": UUID(tenant)}, conn=conn
        )
        pdfs = await db.q.fetch_one("training/usage.sql", (UUID(tenant),), conn=conn)
    assert usage is not None and pdfs is not None
    assert usage["items"] == 3 and usage["bytes"] == 300 * MEGABYTE and usage["uploading"] == 0
    assert pdfs["items"] == 2 and pdfs["bytes"] == 2 * len(small)


async def test_video_usage_excludes_permanent_failures_and_counts_uploads(
    sessions: SessionWorld,
) -> None:
    tenant = await enable(sessions)
    ready = await video_row(sessions, tenant, status="ready", byte_size=10 * MEGABYTE)
    uploading = await video_row(sessions, tenant, status="uploading", byte_size=20 * MEGABYTE)
    await upload_row(sessions, tenant, uploading)
    for code in ("training_video_unreadable", "training_video_too_long", "training_video_codec"):
        await video_row(sessions, tenant, status="failed", failure_code=code, byte_size=MEGABYTE)
    # A retryable failure still holds its file, so it still counts.
    await video_row(
        sessions,
        tenant,
        status="failed",
        failure_code="training_transcription_unavailable",
        byte_size=5 * MEGABYTE,
    )
    other = await enable(sessions, sessions.tenants[1])
    await video_row(sessions, other, status="ready", byte_size=50 * MEGABYTE)
    async with tx(sessions.pool, tenant) as conn:
        usage = await db.q.fetch_one(
            "training/video_usage.sql", {"client_id": UUID(tenant)}, conn=conn
        )
    assert usage is not None
    assert usage["items"] == 6
    assert usage["bytes"] == 35 * MEGABYTE
    assert usage["uploading"] == 1
    assert ready


async def test_stored_pattern_covers_pdf_and_video_names_only() -> None:
    name = str(uuid4())
    for ext in ("pdf", "mp4", "m4v", "mov", "webm"):
        assert STORED.fullmatch(f"{name}.{ext}") and STORED.fullmatch(f".{name}.{ext}.part")
    assert STORED.fullmatch("notes.txt") is None
    assert STORED.fullmatch(f"{name}.exe") is None


async def test_sweep_keeps_the_part_file_of_an_uploading_video(
    sessions: SessionWorld, training_store: ObjectStore, tmp_path: Path
) -> None:
    tenant = await enable(sessions)
    running = f"{uuid4()}.mp4"
    await video_row(sessions, tenant, status="uploading", storage_name=running)
    kept_final = f"{uuid4()}.mp4"
    await video_row(sessions, tenant, status="ready", storage_name=kept_final)
    part = put_file(tmp_path, tenant, f".{running}.part")
    ready_file = put_file(tmp_path, tenant, kept_final)
    orphan = put_file(tmp_path, tenant, f"{uuid4()}.mp4")
    orphan_part = put_file(tmp_path, tenant, f".{uuid4()}.mp4.part")
    notes = put_file(tmp_path, tenant, "notes.txt")

    removed = await cleanup(sessions.pool, training_store, UUID(tenant))
    assert removed == 2
    assert part.exists() and ready_file.exists() and notes.exists()
    assert not orphan.exists() and not orphan_part.exists()


async def test_a_pdf_failure_sweep_never_deletes_a_running_video_upload(
    sessions: SessionWorld,
    training_store: ObjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    running = f"{uuid4()}.mp4"
    await video_row(sessions, tenant, status="uploading", storage_name=running)
    part = put_file(tmp_path, tenant, f".{running}.part")
    original = training_store.put

    def failing(tenant_id: UUID, name: str, data: bytes) -> None:
        original(tenant_id, name, data)
        raise StorageError("synthetic storage outage")

    monkeypatch.setattr(training_store, "put", failing)
    answer = await upload(sessions, admin, make_pdf())
    assert answer.status_code == 503, answer.text
    assert part.exists()
    assert names(training_store, tenant) == [f".{running}.part"]


async def test_delete_uploading_video_aborts_the_store_upload(
    sessions: SessionWorld, training_store: ObjectStore, tmp_path: Path
) -> None:
    assert isinstance(training_store, RecordingStore)
    tenant = await enable(sessions)
    admin, admin_me = await sessions.member(role="tenant_admin")
    name = f"{uuid4()}.mp4"
    item = await video_row(sessions, tenant, status="uploading", storage_name=name)
    await upload_row(sessions, tenant, item, "upload-7")
    await sessions.q(
        "INSERT INTO training_video_pieces (client_id, item_id, number, byte_size, sha256, etag)"
        " VALUES (%s, %s, 1, 5242880, %s, 'e1')",
        tenant,
        item,
        "a" * 64,
    )
    part = put_file(tmp_path, tenant, f".{name}.part")

    with capture_logs() as logs:
        answer = await remove(sessions, admin, item)
    assert answer.status_code == 204, answer.text
    assert training_store.aborts == [(UUID(tenant), name, "upload-7")]
    # The removal's log line says what came of the abort, once it was tried.
    assert [log["outcome"] for log in logs if log["event"] == "training_item_removed"] == [
        "upload_aborted"
    ]
    assert not part.exists()
    assert names(training_store, tenant) == []
    assert await rows(sessions, tenant) == []
    assert await count(sessions, "training_video_uploads", tenant) == 0
    assert await count(sessions, "training_video_pieces", tenant) == 0
    [entry] = await audits(sessions, tenant, "training.item_removed")
    assert entry["target_id"] == item and str(entry["actor_id"]) == admin_me["user"]["id"]


async def test_delete_survives_a_failing_abort_and_still_sweeps(
    sessions: SessionWorld, training_store: ObjectStore, tmp_path: Path
) -> None:
    assert isinstance(training_store, RecordingStore)
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    name = f"{uuid4()}.mp4"
    item = await video_row(sessions, tenant, status="uploading", storage_name=name)
    await upload_row(sessions, tenant, item, "upload-8")
    part = put_file(tmp_path, tenant, f".{name}.part")
    training_store.abort_error = StorageError("synthetic storage outage")

    with capture_logs() as logs:
        answer = await remove(sessions, admin, item)
    assert answer.status_code == 204, answer.text
    assert len(training_store.aborts) == 1
    assert [log["outcome"] for log in logs if log["event"] == "training_item_removed"] == [
        "abort_failed"
    ]
    assert await rows(sessions, tenant) == []
    assert not part.exists()  # the sweep after the abort removes it


async def test_delete_ready_video_removes_file_children_and_summary_but_keeps_usage(
    sessions: SessionWorld, training_store: ObjectStore, tmp_path: Path
) -> None:
    assert isinstance(training_store, RecordingStore)
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    name = f"{uuid4()}.mp4"
    item = await video_row(sessions, tenant, status="ready", storage_name=name)
    path = put_file(tmp_path, tenant, name, b"synthetic video bytes")
    await job_row(sessions, tenant, item, status="done", chunks_done=1, chunks_total=1)
    await sessions.q(
        "INSERT INTO training_transcript_segments (client_id, item_id, chunk_index, position,"
        " start_ms, end_ms, text) VALUES (%s, %s, 0, 0, 0, 1500, 'Welcome.')",
        tenant,
        item,
    )
    await sessions.q(
        "INSERT INTO training_transcript_windows (client_id, item_id, position, start_ms, end_ms,"
        " text, embedding, embed_model) VALUES (%s, %s, 0, 0, 1500, 'Welcome.', %s::vector, 'm')",
        tenant,
        item,
        ZERO_VECTOR,
    )
    await sessions.q(
        "INSERT INTO training_video_usage (client_id, item_id, seconds) VALUES (%s, %s, 90)",
        tenant,
        item,
    )
    await sessions.q("UPDATE training_items SET summary='{}' WHERE id=%s", item)

    with capture_logs() as logs:
        answer = await remove(sessions, admin, item)
    assert answer.status_code == 204, answer.text
    assert [log["outcome"] for log in logs if log["event"] == "training_item_removed"] == [
        "removed"
    ]
    assert not path.exists() and names(training_store, tenant) == []
    # No upload was open, so the store was not asked to abort one.
    assert training_store.aborts == []
    assert await rows(sessions, tenant) == []
    for table in (
        "training_video_jobs",
        "training_transcript_segments",
        "training_transcript_windows",
    ):
        assert await count(sessions, table, tenant) == 0, table
    assert await count(sessions, "training_video_usage", tenant) == 1
    assert len(await audits(sessions, tenant, "training.item_removed")) == 1
    assert (await admin.get(f"{ITEMS}/{item}/summary")).status_code == 404


async def test_delete_a_failed_video_in_any_state(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    made = await all_states(sessions, tenant)
    for state, item in made.items():
        assert (await remove(sessions, admin, item)).status_code == 204, state
    assert await rows(sessions, tenant) == []
    assert (await remove(sessions, admin, made["ready"])).status_code == 404


async def test_company_b_cannot_list_patch_delete_or_summarise_company_a_video(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    other = await enable(sessions, sessions.tenants[1])
    foreign_admin, _ = await sessions.member(role="tenant_admin", tenant=other)
    foreign_staff, _ = await sessions.member(tenant=other)
    ready = await video_row(sessions, tenant, status="ready", speech="speech")
    failed = await video_row(sessions, tenant, status="failed")
    await sessions.q("UPDATE training_items SET summary='{}' WHERE id=%s", ready)

    for browser in (foreign_admin, foreign_staff):
        assert (await browser.get(ITEMS)).json()["items"] == []
    for item in (ready, failed):
        answers = [
            await patch(sessions, foreign_admin, item, {"title": "Taken over"}),
            await remove(sessions, foreign_admin, item),
            await sessions.mutate(foreign_admin, f"{ITEMS}/{item}/summary", {"regenerate": True}),
            await sessions.mutate(foreign_staff, f"{ITEMS}/{item}/summary", {}),
            await foreign_admin.get(f"{ITEMS}/{item}/summary"),
            await foreign_admin.get(f"{ITEMS}/{item}/file"),
        ]
        for answer in answers:
            assert answer.status_code == 404 and detail(answer) == "training_not_found", answer.text
    assert {str(row["id"]) for row in await rows(sessions, tenant)} == {ready, failed}
    assert all(row["title"].startswith("Video") for row in await rows(sessions, tenant))
