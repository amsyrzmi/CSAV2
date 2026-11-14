"""Resumable video uploads in pieces: start, piece, resume status and complete.

Every byte here is synthetic: a minimal ``ftyp`` box or EBML header followed by generated filler.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
import httpx
import pytest
from csa.console.access import reader_may_post
from csa.core import db
from csa.core.tenancy import tx
from csa.storage import local as local_module
from csa.storage.base import ObjectStore
from csa.storage.local import LocalObjectStore
from csa.storage.multipart import PartMismatch
from csa.storage.s3 import S3ObjectStore
from csa.training import limits, video_upload
from csa.training.service import cleanup
from moto import mock_aws
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld
from tests.test_training_api import audits, detail, enable, make_owner, names, remove, rows

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

VIDEOS = "/console/v1/training/videos"
MIB = 1024 * 1024
# The smallest piece the upload table accepts (and S3 takes for every part but the last).
PIECE = 5 * MIB
SIZE = 2 * PIECE + 1000  # three pieces, the last one short
BUCKET = "test-bucket"
S3_STORE = pytest.mark.parametrize("training_store", ["s3"], indirect=True)
MP4_HEAD = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
WEBM_HEAD = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01"


@pytest.fixture
def training_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ObjectStore]:
    """Local disk by default; ``@S3_STORE`` swaps in a moto bucket for the whole world."""
    if getattr(request, "param", "local") == "s3":
        with mock_aws():
            client = boto3.client(
                "s3",
                region_name="us-east-1",
                aws_access_key_id="testing",
                aws_secret_access_key="testing",
            )
            client.create_bucket(Bucket=BUCKET)
            yield S3ObjectStore(BUCKET, "training", client)
    else:
        yield LocalObjectStore(tmp_path / "training")


@pytest.fixture(autouse=True)
def small_pieces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "VIDEO_PIECE_BYTES", PIECE)


def filler(size: int, seed: bytes = b"synthetic") -> bytes:
    # Non-repeating at piece boundaries, so a misplaced piece changes the result.
    block = b"".join(hashlib.sha256(seed + bytes([i])).digest() for i in range(256))
    return (block * (size // len(block) + 1))[:size]


def mp4(size: int = SIZE, seed: bytes = b"synthetic") -> bytes:
    return (MP4_HEAD + filler(size, seed))[:size]


def webm(size: int = SIZE) -> bytes:
    return (WEBM_HEAD + filler(size))[:size]


def split(data: bytes, piece: int = PIECE) -> list[bytes]:
    return [data[offset : offset + piece] for offset in range(0, len(data), piece)]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def joined(chunks: list[bytes]) -> str:
    return sha("\n".join(sha(chunk) for chunk in chunks).encode())


async def video_world(world: SessionWorld, tenant: str | None = None) -> str:
    tenant = await enable(world, tenant)
    world.app.state.training_video_enabled = True
    return tenant


async def start(
    world: SessionWorld,
    browser: httpx.AsyncClient,
    name: str = "induction.mp4",
    size: int = SIZE,
    media_type: str = "video/mp4",
    *,
    key: str | None = None,
    query: str = "",
) -> httpx.Response:
    return await browser.post(
        VIDEOS + query,
        json={"file_name": name, "byte_size": size, "media_type": media_type},
        headers={**world.headers(browser), "Idempotency-Key": key or str(uuid4())},
    )


async def put_piece(
    world: SessionWorld,
    browser: httpx.AsyncClient,
    item: str,
    number: int,
    data: Any,
    *,
    digest: str | None = None,
    query: str = "",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await browser.put(
        f"{VIDEOS}/{item}/pieces/{number}{query}",
        content=data,
        headers={
            **world.headers(browser),
            "Content-Type": "application/octet-stream",
            "X-Piece-SHA256": digest if digest is not None else sha(data),
            **(headers or {}),
        },
    )


async def complete(
    world: SessionWorld, browser: httpx.AsyncClient, item: str, query: str = ""
) -> httpx.Response:
    return await world.mutate(browser, f"{VIDEOS}/{item}/complete{query}")


async def progress(browser: httpx.AsyncClient, item: str, query: str = "") -> httpx.Response:
    return await browser.get(f"{VIDEOS}/{item}/upload{query}")


async def started(world: SessionWorld, browser: httpx.AsyncClient, **kwargs: Any) -> str:
    answer = await start(world, browser, **kwargs)
    assert answer.status_code == 201, answer.text
    return str(answer.json()["item"]["id"])


async def send_all(
    world: SessionWorld,
    browser: httpx.AsyncClient,
    item: str,
    data: bytes,
    order: list[int] | None = None,
) -> None:
    chunks = split(data)
    for number in order or range(1, len(chunks) + 1):
        answer = await put_piece(world, browser, item, number, chunks[number - 1])
        assert answer.status_code == 200, answer.text


async def count(world: SessionWorld, table: str, tenant: str) -> int:
    [row] = await world.q(
        f"SELECT count(*)::int AS n FROM {table} WHERE client_id=%s",  # type: ignore[arg-type]
        tenant,
    )
    return int(row["n"])


async def item_row(world: SessionWorld, item: str) -> dict[str, Any]:
    [row] = await world.q("SELECT * FROM training_items WHERE id=%s", item)
    return row


def stored_path(world: SessionWorld, tenant: str, name: str) -> Path:
    return world.upload_root / "training" / tenant / name


async def video_usage(world: SessionWorld, tenant: str) -> dict[str, Any]:
    async with tx(world.pool, tenant) as conn:
        usage = await db.q.fetch_one(
            "training/video_usage.sql", {"client_id": UUID(tenant)}, conn=conn
        )
    assert usage is not None
    return dict(usage)


async def test_flag_off_answers_503_video_unavailable(sessions: SessionWorld) -> None:
    await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = str(uuid4())
    answers = [
        await start(sessions, admin),
        await put_piece(sessions, admin, item, 1, mp4(100)),
        await progress(admin, item),
        await complete(sessions, admin, item),
    ]
    for answer in answers:
        assert answer.status_code == 503, answer.text
        assert detail(answer) == "training_video_unavailable"


async def test_start_validates_name_extension_and_size(sessions: SessionWorld) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    refused = await start(sessions, admin, "induction.avi")
    assert refused.status_code == 422 and detail(refused) == "training_not_video", refused.text
    big = await start(sessions, admin, size=limits.MAX_VIDEO_BYTES + 1)
    assert big.status_code == 413 and detail(big) == "training_file_too_large", big.text
    bad = await start(sessions, admin, "induc\x07tion.mp4")
    assert bad.status_code == 422 and detail(bad) == "training_name_invalid", bad.text
    for name in ("Induction.MP4", "a.m4v", "b.mov", "c.webm"):
        assert (await start(sessions, admin, name, size=1000)).status_code in (201, 409)
    assert await rows(sessions, tenant) != []


async def test_start_returns_the_piece_plan_and_reserves_bytes(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    answer = await start(sessions, admin, "Safety walk.mov", size=SIZE)
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert set(body) == {"item", "piece_size", "piece_count", "received"}
    assert body["piece_size"] == PIECE
    assert body["piece_count"] == -(-SIZE // PIECE) == 3
    assert body["received"] == []
    item = body["item"]
    assert item["kind"] == "video" and item["status"] == "uploading"
    assert item["title"] == "Safety walk" and item["byte_size"] == SIZE
    [row] = await rows(sessions, tenant)
    assert row["container"] == "mp4" and row["sha256"] is None
    assert row["original_name"] == "Safety walk.mov"
    assert row["storage_name"].endswith(".mov") and "Safety" not in row["storage_name"]
    usage = await video_usage(sessions, tenant)
    assert usage["items"] == 1 and usage["bytes"] == SIZE and usage["uploading"] == 1
    assert names(training_store, tenant) == [f".{row['storage_name']}.part"]
    [entry] = await audits(sessions, tenant, "training.video_upload_started")
    assert entry["target_id"] == item["id"]


async def test_start_same_key_returns_the_same_item_and_other_fingerprint_is_409(
    sessions: SessionWorld,
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    key = str(uuid4())
    first = await start(sessions, admin, key=key)
    again = await start(sessions, admin, key=key)
    assert first.status_code == 201 and again.status_code == 201, again.text
    assert again.json() == first.json()
    for changed in (
        {"name": "other.mp4"},
        {"size": SIZE + 1},
        {"media_type": "video/quicktime"},
    ):
        answer = await start(sessions, admin, key=key, **changed)  # type: ignore[arg-type]
        assert answer.status_code == 409 and detail(answer) == "training_key_conflict", changed
    assert len(await rows(sessions, tenant)) == 1
    assert len(await audits(sessions, tenant, "training.video_upload_started")) == 1


async def test_pieces_out_of_order_complete_byte_identical(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await video_world(sessions)
    admin, admin_me = await sessions.member(role="tenant_admin")
    data = mp4()
    item = await started(sessions, admin)
    chunks = split(data)
    for number in (3, 1, 2):
        answer = await put_piece(sessions, admin, item, number, chunks[number - 1])
        assert answer.status_code == 200, answer.text
        assert answer.json()["number"] == number
    assert answer.json()["received"] == 3
    shown = await progress(admin, item)
    assert shown.status_code == 200 and shown.json()["received"] == [1, 2, 3], shown.text

    done = await complete(sessions, admin, item)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "uploaded" and done.json()["byte_size"] == SIZE
    assert done.json()["processing"]["stage"] == "transcribing"
    row = await item_row(sessions, item)
    assert row["status"] == "uploaded" and row["byte_size"] == SIZE
    assert row["sha256"] == joined(chunks)
    assert stored_path(sessions, tenant, row["storage_name"]).read_bytes() == data
    assert names(training_store, tenant) == [row["storage_name"]]
    [job] = await sessions.q("SELECT * FROM training_video_jobs WHERE item_id=%s", item)
    assert job["status"] == "queued" and job["stage"] == "transcribing"
    [entry] = await audits(sessions, tenant, "training.item_added")
    assert entry["target_id"] == item and str(entry["actor_id"]) == admin_me["user"]["id"]
    # Completion closes the upload: no upload or piece rows remain, nothing counts as uploading.
    assert await count(sessions, "training_video_uploads", tenant) == 0
    assert await count(sessions, "training_video_pieces", tenant) == 0
    assert (await video_usage(sessions, tenant))["uploading"] == 0


async def test_same_piece_twice_writes_once(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await started(sessions, admin)
    calls: list[int] = []
    original = training_store.write_part  # type: ignore[attr-defined]

    def spy(*args: Any) -> str:
        calls.append(args[3])
        return original(*args)

    monkeypatch.setattr(training_store, "write_part", spy)
    first_piece = split(mp4())[0]
    answers = [await put_piece(sessions, admin, item, 1, first_piece) for _ in range(2)]
    assert [answer.status_code for answer in answers] == [200, 200], answers[1].text
    assert answers[1].json() == {"number": 1, "received": 1}
    assert calls == [1]


async def test_wrong_hash_is_422_and_nothing_is_written(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await started(sessions, admin)
    calls: list[int] = []
    original = training_store.write_part  # type: ignore[attr-defined]

    def spy(*args: Any) -> str:
        calls.append(args[3])
        return original(*args)

    monkeypatch.setattr(training_store, "write_part", spy)
    piece = split(mp4())[0]
    answer = await put_piece(sessions, admin, item, 1, piece, digest=sha(b"something else"))
    assert answer.status_code == 422 and detail(answer) == "training_piece_corrupt", answer.text
    assert calls == []
    row = await item_row(sessions, item)
    assert stored_path(sessions, tenant, f".{row['storage_name']}.part").stat().st_size == 0
    assert await count(sessions, "training_video_pieces", tenant) == 0

    # A store that finds the bytes do not match their hash (S3 BadDigest) is the same refusal.
    def corrupt(*args: Any) -> str:
        raise PartMismatch([args[3]])

    monkeypatch.setattr(training_store, "write_part", corrupt)
    answer = await put_piece(sessions, admin, item, 1, piece)
    assert answer.status_code == 422 and detail(answer) == "training_piece_corrupt", answer.text
    assert await count(sessions, "training_video_pieces", tenant) == 0
    # A digest that is not 64 hex characters is refused as an invalid piece.
    answer = await put_piece(sessions, admin, item, 1, piece, digest="xyz")
    assert answer.status_code == 422 and detail(answer) == "training_piece_invalid"


async def test_wrong_size_or_number_out_of_range_is_422(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await started(sessions, admin)
    chunks = split(mp4())
    for number, data in (
        (0, chunks[0]),
        (4, chunks[2]),
        (1, chunks[0][:-1]),  # one byte short
        (1, chunks[0] + b"\x00"),  # one byte long
        (2, chunks[2]),  # the last piece's size for a middle piece
        (3, chunks[1]),  # a full piece for the short last one
    ):
        answer = await put_piece(sessions, admin, item, number, data)
        assert answer.status_code == 422, (number, len(data), answer.text)
        assert detail(answer) == "training_piece_invalid", answer.text

    # A declared length that is wrong is refused before any byte of the body is read.
    consumed: list[bool] = []

    async def body() -> AsyncIterator[bytes]:
        consumed.append(True)
        yield b"\x00" * 10

    answer = await put_piece(
        sessions, admin, item, 1, body(), digest=sha(b"\x00" * 10), headers={"Content-Length": "10"}
    )
    assert answer.status_code == 422 and detail(answer) == "training_piece_invalid", answer.text
    assert consumed == []
    # A body longer or shorter than the (correct) declared length is refused too.
    expected = str(PIECE)
    for data in (chunks[0] + b"extra", chunks[0][:-5]):
        answer = await put_piece(
            sessions,
            admin,
            item,
            1,
            data,
            digest=sha(chunks[0]),
            headers={"Content-Length": expected},
        )
        assert answer.status_code == 422 and detail(answer) == "training_piece_invalid", answer.text
    assert await count(sessions, "training_video_pieces", tenant) == 0
    row = await item_row(sessions, item)
    assert stored_path(sessions, tenant, f".{row['storage_name']}.part").stat().st_size == 0


async def test_first_piece_that_is_not_a_video_is_422(sessions: SessionWorld) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await started(sessions, admin)
    text = (b"plain text, not a video " * 300_000)[:PIECE]
    answer = await put_piece(sessions, admin, item, 1, text)
    assert answer.status_code == 422 and detail(answer) == "training_not_video", answer.text
    # An MP4 named .webm is refused too: the header must match the extension.
    other = await started(sessions, admin, name="clip.webm", media_type="video/webm")
    answer = await put_piece(sessions, admin, other, 1, split(mp4())[0])
    assert answer.status_code == 422 and detail(answer) == "training_not_video", answer.text
    # The right header for each is accepted.
    assert (await put_piece(sessions, admin, other, 1, split(webm())[0])).status_code == 200
    assert (await put_piece(sessions, admin, item, 1, split(mp4())[0])).status_code == 200
    assert await count(sessions, "training_video_pieces", tenant) == 2


async def test_complete_with_a_missing_piece_is_409_and_twice_gives_one_item_and_one_job(
    sessions: SessionWorld,
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = mp4()
    item = await started(sessions, admin)
    await send_all(sessions, admin, item, data, order=[1, 3])
    early = await complete(sessions, admin, item)
    assert early.status_code == 409 and detail(early) == "training_piece_missing", early.text
    assert (await item_row(sessions, item))["status"] == "uploading"
    await send_all(sessions, admin, item, data, order=[2])
    first = await complete(sessions, admin, item)
    second = await complete(sessions, admin, item)
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"] == item
    assert second.json()["status"] == "uploaded"
    assert len(await rows(sessions, tenant)) == 1
    assert await count(sessions, "training_video_jobs", tenant) == 1
    assert len(await audits(sessions, tenant, "training.item_added")) == 1


async def test_complete_catches_a_piece_changed_after_it_was_recorded(
    sessions: SessionWorld,
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = mp4()
    item = await started(sessions, admin)
    await send_all(sessions, admin, item, data)
    row = await item_row(sessions, item)
    part = stored_path(sessions, tenant, f".{row['storage_name']}.part")
    with part.open("r+b") as stream:
        stream.seek(PIECE + 100)
        stream.write(b"tampered")

    answer = await complete(sessions, admin, item)
    assert answer.status_code == 409 and detail(answer) == "training_piece_missing", answer.text
    shown = await progress(admin, item)
    assert shown.status_code == 200 and shown.json()["received"] == [1, 3], shown.text
    assert (await item_row(sessions, item))["status"] == "uploading"
    await send_all(sessions, admin, item, data, order=[2])
    done = await complete(sessions, admin, item)
    assert done.status_code == 200, done.text
    final = await item_row(sessions, item)
    assert stored_path(sessions, tenant, final["storage_name"]).read_bytes() == data
    assert final["sha256"] == joined(split(data))


async def test_two_starts_race_for_the_last_slot_and_the_last_bytes_one_wins(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    monkeypatch.setattr(limits, "MAX_VIDEO_ITEMS", 1)
    answers = await asyncio.gather(
        start(sessions, admin, "one.mp4"), start(sessions, admin, "two.mp4")
    )
    assert sorted(answer.status_code for answer in answers) == [201, 409], [
        answer.text for answer in answers
    ]
    assert {detail(a) for a in answers if a.status_code == 409} == {"training_library_full"}
    assert len(await rows(sessions, tenant)) == 1

    monkeypatch.setattr(limits, "MAX_VIDEO_ITEMS", 50)
    monkeypatch.setattr(limits, "MAX_VIDEO_TOTAL_BYTES", SIZE + 1000 + 600)
    answers = await asyncio.gather(
        start(sessions, admin, "three.mp4", size=1000),
        start(sessions, admin, "four.mp4", size=1000),
    )
    assert sorted(answer.status_code for answer in answers) == [201, 409], [
        answer.text for answer in answers
    ]
    assert {detail(a) for a in answers if a.status_code == 409} == {"training_library_full"}
    assert len(await rows(sessions, tenant)) == 2
    assert (await video_usage(sessions, tenant))["bytes"] == SIZE + 1000


async def test_third_upload_in_progress_is_409(sessions: SessionWorld) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    first = await started(sessions, admin, name="one.mp4")
    await started(sessions, admin, name="two.mp4")
    third = await start(sessions, admin, "three.mp4")
    assert third.status_code == 409, third.text
    assert detail(third) == "training_uploads_in_progress"
    # Finishing one frees its slot.
    await send_all(sessions, admin, first, mp4())
    assert (await complete(sessions, admin, first)).status_code == 200
    assert (await start(sessions, admin, "three.mp4")).status_code == 201
    assert len(await rows(sessions, tenant)) == 3


async def test_sweep_racing_a_completion_keeps_the_file(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = mp4()
    item = await started(sessions, admin)
    await send_all(sessions, admin, item, data)
    _, done = await asyncio.gather(
        cleanup(sessions.pool, training_store, UUID(tenant)), complete(sessions, admin, item)
    )
    assert done.status_code == 200, done.text
    row = await item_row(sessions, item)
    assert row["status"] == "uploaded"
    assert stored_path(sessions, tenant, row["storage_name"]).read_bytes() == data
    assert names(training_store, tenant) == [row["storage_name"]]
    # A later sweep keeps it too.
    assert await cleanup(sessions.pool, training_store, UUID(tenant)) == 0


async def test_complete_waits_for_a_piece_in_flight(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = mp4()
    item = await started(sessions, admin)
    await send_all(sessions, admin, item, data)
    chunks = split(data)
    replaced = filler(PIECE, b"second take")
    entered, release = threading.Event(), threading.Event()
    real_os = local_module.os

    class SlowOs:
        """``os`` for the local store, whose positional write waits after the file is open."""

        def __getattr__(self, name: str) -> Any:
            return getattr(real_os, name)

        @staticmethod
        def pwrite(descriptor: int, view: Any, offset: int) -> int:
            entered.set()
            release.wait(10)
            return real_os.pwrite(descriptor, view, offset)

    monkeypatch.setattr(local_module, "os", SlowOs())
    try:
        piece = asyncio.create_task(put_piece(sessions, admin, item, 2, replaced))
        assert await asyncio.to_thread(entered.wait, 10)
        finishing = asyncio.create_task(complete(sessions, admin, item))
        await asyncio.sleep(0.5)
        # Completion has begun: it waits for the piece in flight and refuses any new piece.
        assert not finishing.done()
        late = await put_piece(sessions, admin, item, 3, chunks[2])
        assert late.status_code == 409 and detail(late) == "training_upload_closed", late.text
    finally:
        release.set()
    stored, done = await piece, await finishing
    assert stored.status_code == 200, stored.text
    assert done.status_code == 200, done.text
    row = await item_row(sessions, item)
    expected = [chunks[0], replaced, chunks[2]]
    # The published file is exactly the recorded pieces.
    assert stored_path(sessions, tenant, row["storage_name"]).read_bytes() == b"".join(expected)
    assert row["sha256"] == joined(expected)


async def test_piece_after_delete_or_complete_is_409_and_creates_no_file(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = mp4()
    chunks = split(data)
    finished = await started(sessions, admin, name="done.mp4")
    await send_all(sessions, admin, finished, data)
    assert (await complete(sessions, admin, finished)).status_code == 200
    kept = (await item_row(sessions, finished))["storage_name"]

    # After completion: closed, and the published file is untouched.
    answer = await put_piece(sessions, admin, finished, 1, filler(PIECE, b"x")[:PIECE])
    assert answer.status_code == 409 and detail(answer) == "training_upload_closed", answer.text
    assert names(training_store, tenant) == [kept]
    assert stored_path(sessions, tenant, kept).read_bytes() == data

    original = training_store.write_part  # type: ignore[attr-defined]
    for when in ("before the write", "after the write"):
        item = await started(sessions, admin)
        entered, release = threading.Event(), threading.Event()

        def held(*args: Any, when: str = when, entered=entered, release=release) -> str:
            etag = ""
            if when == "after the write":
                etag = original(*args)
            entered.set()
            release.wait(10)
            return original(*args) if when == "before the write" else etag

        monkeypatch.setattr(training_store, "write_part", held)
        try:
            piece = asyncio.create_task(put_piece(sessions, admin, item, 1, chunks[0]))
            assert await asyncio.to_thread(entered.wait, 10)
            assert (await remove(sessions, admin, item)).status_code == 204
        finally:
            release.set()
        answer = await piece
        assert answer.status_code == 409, (when, answer.text)
        assert detail(answer) == "training_upload_closed"
        assert names(training_store, tenant) == [kept], when
        assert await count(sessions, "training_video_pieces", tenant) == 0
        monkeypatch.setattr(training_store, "write_part", original)

    # Once the removal has committed the item is gone, as an unknown id is.
    item = await started(sessions, admin)
    assert (await remove(sessions, admin, item)).status_code == 204
    answer = await put_piece(sessions, admin, item, 1, chunks[0])
    assert answer.status_code == 404 and detail(answer) == "training_not_found"
    assert names(training_store, tenant) == [kept]


async def test_full_piece_slots_answer_503_with_retry_after(sessions: SessionWorld) -> None:
    await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await started(sessions, admin)
    piece = split(mp4())[0]
    held = 0
    try:
        for _ in range(limits.PIECES_IN_FLIGHT):
            await video_upload.PIECE_SLOTS.acquire()
            held += 1
        busy = await put_piece(sessions, admin, item, 1, piece)
        assert busy.status_code == 503 and detail(busy) == "training_busy", busy.text
        assert busy.headers["Retry-After"] == "2"
    finally:
        for _ in range(held):
            video_upload.PIECE_SLOTS.release()
    assert (await put_piece(sessions, admin, item, 1, piece)).status_code == 200


async def test_roles_and_isolation_on_upload_routes(sessions: SessionWorld) -> None:
    tenant = await video_world(sessions)
    other = await video_world(sessions, sessions.tenants[1])
    q = f"?client_id={tenant}"
    admin, _ = await sessions.member(role="tenant_admin")
    owner, owner_me = await sessions.member()
    await make_owner(sessions, tenant, owner_me["user"]["id"])
    staff, _ = await sessions.member()
    reader, _ = await sessions.member(role="read_only")
    foreign_admin, _ = await sessions.member(role="tenant_admin", tenant=other)
    data = mp4()

    # Platform admin acting on the company, tenant admin and the HR owner manage uploads.
    for browser, query in ((sessions.admin, q), (admin, ""), (owner, "")):
        answer = await start(sessions, browser, query=query)
        assert answer.status_code == 201, answer.text
        item = answer.json()["item"]["id"]
        for number, chunk in enumerate(split(data), start=1):
            stored = await put_piece(sessions, browser, item, number, chunk, query=query)
            assert stored.status_code == 200, stored.text
        assert (await progress(browser, item, query)).status_code == 200
        assert (await complete(sessions, browser, item, query)).status_code == 200

    item = await started(sessions, admin)
    piece = split(data)[0]
    refused = [
        await start(sessions, staff),
        await put_piece(sessions, staff, item, 1, piece),
        await progress(staff, item),
        await complete(sessions, staff, item),
        await progress(reader, item),
    ]
    for answer in refused:
        assert answer.status_code == 403 and detail(answer) == "training_forbidden", answer.text
    for answer in (
        await start(sessions, reader),
        await put_piece(sessions, reader, item, 1, piece),
        await complete(sessions, reader, item),
    ):
        assert answer.status_code == 403, answer.text
        assert detail(answer) == "write permission required"

    # Another company's admin cannot reach this company's upload by its id.
    for answer in (
        await put_piece(sessions, foreign_admin, item, 1, piece),
        await progress(foreign_admin, item),
        await complete(sessions, foreign_admin, item),
    ):
        assert answer.status_code == 404 and detail(answer) == "training_not_found", answer.text

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sessions.app), base_url=ORIGIN
    ) as machine:
        headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": tenant}
        for answer in (
            await machine.post(
                VIDEOS,
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={"file_name": "a.mp4", "byte_size": 10, "media_type": "video/mp4"},
            ),
            await machine.put(f"{VIDEOS}/{item}/pieces/1", headers=headers, content=piece),
            await machine.get(f"{VIDEOS}/{item}/upload", headers=headers),
            await machine.post(f"{VIDEOS}/{item}/complete", headers=headers),
        ):
            assert answer.status_code == 403, answer.text
    assert await count(sessions, "training_video_pieces", tenant) == 0
    assert len(await rows(sessions, tenant)) == 4
    assert await rows(sessions, other) == []


async def test_every_video_route_needs_the_package(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    sessions.app.state.training_video_enabled = True
    q = f"?client_id={tenant}"
    staff, _ = await sessions.member()
    item = str(uuid4())
    for browser, query in ((sessions.admin, q), (staff, "")):
        answers = [
            await start(sessions, browser, query=query),
            await put_piece(sessions, browser, item, 1, mp4(100), query=query),
            await progress(browser, item, query),
            await complete(sessions, browser, item, query),
        ]
        for answer in answers:
            assert answer.status_code == 403, answer.text
            assert detail(answer) == "package_inactive"


async def test_reader_may_post_still_matches_only_the_summary_route() -> None:
    item = str(uuid4())
    for method, route, path in (
        ("POST", "/training/videos", "/console/v1/training/videos"),
        (
            "POST",
            "/training/videos/{item_id}/complete",
            f"/console/v1/training/videos/{item}/complete",
        ),
        (
            "PUT",
            "/training/videos/{item_id}/pieces/{number}",
            f"/console/v1/training/videos/{item}/pieces/1",
        ),
        ("GET", "/training/videos/{item_id}/upload", f"/console/v1/training/videos/{item}/upload"),
    ):
        assert reader_may_post(method, route, path) is False, (method, route)
    assert reader_may_post(
        "POST", "/training/items/{item_id}/summary", f"/console/v1/training/items/{item}/summary"
    )


@S3_STORE
async def test_upload_round_trip_on_the_s3_backend(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    assert isinstance(training_store, S3ObjectStore)
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = mp4()
    item = await started(sessions, admin)
    await send_all(sessions, admin, item, data, order=[2, 3, 1])
    done = await complete(sessions, admin, item)
    assert done.status_code == 200, done.text
    row = await item_row(sessions, item)
    key = f"training/{tenant}/{row['storage_name']}"
    listed = training_store.client.list_objects_v2(Bucket=BUCKET, Prefix=f"training/{tenant}/")
    assert [entry["Key"] for entry in listed["Contents"]] == [key]
    stored = training_store.client.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert stored == data
    assert row["sha256"] == joined(split(data))
    assert await count(sessions, "training_video_uploads", tenant) == 0
    assert await count(sessions, "training_video_pieces", tenant) == 0
    assert training_store.list_uploads(UUID(tenant)) == []


async def test_upload_logs_carry_no_file_name(sessions: SessionWorld) -> None:
    tenant = await video_world(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = mp4()
    with capture_logs() as logs:
        item = await started(sessions, admin, name="Secret induction.mp4")
        chunks = split(data)
        await put_piece(sessions, admin, item, 1, chunks[0], digest=sha(b"other"))
        await send_all(sessions, admin, item, data)
        assert (await complete(sessions, admin, item)).status_code == 200
    row = await item_row(sessions, item)
    mine = [entry for entry in logs if entry.get("item_id") == item]
    assert {entry["event"] for entry in mine} >= {
        "training_video_upload_started",
        "training_video_piece_stored",
        "training_video_piece_refused",
        "training_video_upload_completed",
    }, logs
    assert all(entry.get("client_id") == tenant for entry in mine)
    text = repr(logs)
    for secret in ("Secret induction", row["storage_name"], str(stored_path(sessions, tenant, ""))):
        assert secret not in text
