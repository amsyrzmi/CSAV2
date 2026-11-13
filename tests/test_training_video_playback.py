"""Video playback and transcript routes: Range answers, the S3 URL, visibility and isolation."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.storage.base import ObjectStore
from csa.storage.local import LocalObjectStore
from csa.storage.s3 import S3ObjectStore
from csa.training import limits
from moto import mock_aws
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld
from tests.test_training_api import detail, enable, upload
from tests.test_training_pdf import make_pdf
from tests.test_training_video_items import video_row

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

ITEMS = "/console/v1/training/items"
BUCKET = "test-bucket"
S3_STORE = pytest.mark.parametrize("training_store", ["s3"], indirect=True)
SIZE = 200_000  # more than three 64 KiB blocks


@pytest.fixture
def training_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ObjectStore]:
    """Local disk by default; ``@S3_STORE`` swaps in a moto bucket for the whole world."""
    if getattr(request, "param", "local") == "s3":
        with mock_aws():
            store = S3ObjectStore.create(
                BUCKET, "us-east-1", "testing", "synthetic-secret", "training"
            )
            store.client.create_bucket(Bucket=BUCKET)
            yield store
    else:
        yield LocalObjectStore(tmp_path / "training")


def filler(size: int = SIZE) -> bytes:
    # Distinct at every offset window, so a misplaced range changes the bytes.
    block = b"".join(hashlib.sha256(b"playback" + bytes([i])).digest() for i in range(256))
    return (block * (size // len(block) + 1))[:size]


async def ready_video(
    world: SessionWorld,
    store: ObjectStore,
    tenant: str,
    *,
    data: bytes | None = None,
    status: str = "ready",
    title: str | None = None,
    container: str = "mp4",
    original_name: str = "induction.mp4",
    **fields: Any,
) -> str:
    """A video row plus its published object in the training store."""
    data = filler() if data is None else data
    name = f"{uuid4()}.{'webm' if container == 'webm' else 'mp4'}"
    store.put(UUID(tenant), name, data)  # type: ignore[attr-defined]
    item = await video_row(
        world, tenant, status=status, storage_name=name, title=title, byte_size=len(data), **fields
    )
    await world.q(
        "UPDATE training_items SET container=%s, original_name=%s WHERE id=%s",
        container,
        original_name,
        item,
    )
    return item


async def segment(
    world: SessionWorld, tenant: str, item: str, chunk: int, position: int, start: int, text: str
) -> None:
    await world.q(
        "INSERT INTO training_transcript_segments (client_id, item_id, chunk_index, position,"
        " start_ms, end_ms, text) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        tenant,
        item,
        chunk,
        position,
        start,
        start + 900,
        text,
    )


def assert_video_headers(response: httpx.Response, media_type: str = "video/mp4") -> None:
    assert response.headers["content-type"] == media_type
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_local_playback_points_at_the_same_origin_video_route(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    staff, _ = await sessions.member()
    item = await ready_video(sessions, training_store, tenant)
    before = datetime.now(UTC)

    answer = await staff.get(f"{ITEMS}/{item}/playback")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == {"url", "expires_at", "media_type"}
    assert body["url"] == f"/console/v1/training/items/{item}/video"
    assert body["media_type"] == "video/mp4"
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(seconds=880) < expires - before < timedelta(seconds=920)
    assert answer.headers["cache-control"] == "private, no-store"
    played = await staff.get(body["url"])
    assert played.status_code == 200 and played.content == filler()

    # A platform admin has no company of their own: the URL names the one they are viewing.
    admin_answer = await sessions.admin.get(f"{ITEMS}/{item}/playback?client_id={tenant}")
    assert admin_answer.status_code == 200, admin_answer.text
    admin_url = admin_answer.json()["url"]
    assert admin_url == f"/console/v1/training/items/{item}/video?client_id={tenant}"
    assert (await sessions.admin.get(admin_url)).status_code == 200


async def test_range_requests_answer_206_416_and_whole_file(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    staff, _ = await sessions.member(role="read_only")
    data = filler()
    n = len(data)
    item = await ready_video(sessions, training_store, tenant, data=data)
    url = f"{ITEMS}/{item}/video"

    async def ask(value: str | None) -> httpx.Response:
        return await staff.get(url, headers={} if value is None else {"Range": value})

    whole = await ask(None)
    assert whole.status_code == 200 and whole.content == data
    assert whole.headers["content-length"] == str(n)
    assert_video_headers(whole)

    first = await ask("bytes=0-99")
    assert first.status_code == 206 and first.content == data[:100]
    assert first.headers["content-range"] == f"bytes 0-99/{n}"
    assert first.headers["content-length"] == "100"
    assert_video_headers(first)

    middle = await ask("bytes=70000-140000")  # across 64 KiB block boundaries
    assert middle.status_code == 206 and middle.content == data[70000:140001]
    assert middle.headers["content-range"] == f"bytes 70000-140000/{n}"

    last = await ask("bytes=-10")
    assert last.status_code == 206 and last.content == data[-10:]
    assert last.headers["content-range"] == f"bytes {n - 10}-{n - 1}/{n}"

    open_ended = await ask(f"bytes={n - 5}-")
    assert open_ended.status_code == 206 and open_ended.content == data[-5:]
    assert open_ended.headers["content-range"] == f"bytes {n - 5}-{n - 1}/{n}"

    clamped = await ask(f"bytes=10-{n * 1000}")
    assert clamped.status_code == 206 and clamped.content == data[10:]
    assert clamped.headers["content-range"] == f"bytes 10-{n - 1}/{n}"

    one = await ask(f"bytes={n - 1}-{n - 1}")
    assert one.status_code == 206 and one.content == data[-1:]

    big_suffix = await ask(f"bytes=-{n * 1000}")
    assert big_suffix.status_code == 206 and big_suffix.content == data
    assert big_suffix.headers["content-range"] == f"bytes 0-{n - 1}/{n}"

    for value in (f"bytes={n}-", f"bytes={n}-{n + 5}", "bytes=-0", "bytes=0-1,5-6"):
        refused = await ask(value)
        assert refused.status_code == 416, value
        assert refused.headers["content-range"] == f"bytes */{n}", value
        assert refused.headers["accept-ranges"] == "bytes"
        assert refused.headers["cache-control"] == "private, no-store"
        assert detail(refused) == "training_range_not_satisfiable"

    # Another unit is not ours to answer: the whole file.
    other_unit = await ask("items=0-5")
    assert other_unit.status_code == 200 and other_unit.content == data


@pytest.mark.parametrize(
    ("value", "status"),
    [
        # The one well-formed range: its end is clamped to the file, so all of it comes back.
        ("bytes=0-99999999999999999999", 206),
        # Longer than MAX_RANGE_HEADER_CHARS, or several ranges: refused.
        ("bytes=" + "9" * 5000 + "-", 416),
        ("bytes=0-1,", 416),
        ("bytes=" + ",".join(f"{i}-{i}" for i in range(0, 4000, 2)), 416),
        ("bytes=" + "0-1," * 3000, 416),
        # Not a byte range at all: ignored, the whole file.
        ("bytes=abc", 200),
        ("bytes=5-2", 200),
        ("bytes=-", 200),
        ("bytes=", 200),
        ("bytes", 200),
        ("bytes=٢-٣", 200),
        ("bytes= 5 - 9", 200),
    ],
    ids=lambda value: value[:24] if isinstance(value, str) else str(value),
)
async def test_malformed_and_oversized_range_headers_never_fail_or_read_past_the_file(
    sessions: SessionWorld, training_store: ObjectStore, value: str, status: int
) -> None:
    tenant = await enable(sessions)
    staff, _ = await sessions.member()
    data = filler(5000)
    item = await ready_video(sessions, training_store, tenant, data=data)

    answer = await staff.get(f"{ITEMS}/{item}/video", headers=[(b"Range", value.encode())])

    assert answer.status_code == status, answer.text[:200]
    if status == 416:
        assert answer.headers["content-range"] == f"bytes */{len(data)}"
        assert len(answer.content) < len(data)
    elif status == 206:
        assert answer.headers["content-range"] == f"bytes 0-{len(data) - 1}/{len(data)}"
        assert answer.content == data
    else:
        assert "content-range" not in answer.headers
        assert answer.content == data


async def test_video_route_does_not_wait_for_the_company_lock(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = filler(70_000)
    item = await ready_video(sessions, training_store, tenant, data=data)
    await segment(sessions, tenant, item, 0, 0, 0, "synthetic line")
    # Hold the lock an upload holds across its durable_put, from another connection.
    async with await psycopg.AsyncConnection.connect(sessions.owner_dsn) as holder:
        await holder.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{tenant}:training",)
        )
        try:
            played = await asyncio.wait_for(
                admin.get(f"{ITEMS}/{item}/video", headers={"Range": "bytes=10-20000"}), 2
            )
            whole = await asyncio.wait_for(admin.get(f"{ITEMS}/{item}/video"), 2)
            link = await asyncio.wait_for(admin.get(f"{ITEMS}/{item}/playback"), 2)
            transcript = await asyncio.wait_for(admin.get(f"{ITEMS}/{item}/transcript"), 2)
        finally:
            await holder.rollback()
    assert played.status_code == 206 and played.content == data[10:20001]
    assert whole.status_code == 200 and whole.content == data
    assert link.status_code == 200
    assert transcript.status_code == 200 and len(transcript.json()["segments"]) == 1


async def test_video_streaming_holds_no_database_connection(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await ready_video(sessions, training_store, tenant)
    before = sessions.pool.get_stats()["pool_available"]
    answer = await admin.get(f"{ITEMS}/{item}/video", headers={"Range": "bytes=0-9"})
    assert answer.status_code == 206
    assert sessions.pool.get_stats()["pool_available"] == before


@S3_STORE
async def test_video_route_is_404_on_the_s3_backend(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await ready_video(sessions, training_store, tenant)

    for headers in ({}, {"Range": "bytes=0-9"}):
        answer = await admin.get(f"{ITEMS}/{item}/video", headers=headers)
        assert answer.status_code == 404, answer.text
        assert detail(answer) == "training_not_found"


@S3_STORE
async def test_s3_playback_is_a_15_minute_inline_no_store_url_under_the_callers_prefix(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    staff, _ = await sessions.member(role="read_only")
    item = await ready_video(
        sessions, training_store, tenant, original_name="Induction day 1.mp4", container="mp4"
    )
    [row] = await sessions.q("SELECT storage_name FROM training_items WHERE id=%s", item)
    before = datetime.now(UTC)

    answer = await staff.get(f"{ITEMS}/{item}/playback")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == {"url", "expires_at", "media_type"}
    assert body["media_type"] == "video/mp4"
    assert timedelta(seconds=880) < datetime.fromisoformat(body["expires_at"]) - before
    assert datetime.fromisoformat(body["expires_at"]) - before < timedelta(seconds=920)
    parts = urlsplit(body["url"])
    query = parse_qs(parts.query)
    key_path = f"/training/{tenant}/{row['storage_name']}"
    assert parts.path in {key_path, f"/{BUCKET}{key_path}"}
    assert query["X-Amz-Expires"] == ["900"]
    assert query["response-content-type"] == ["video/mp4"]
    assert query["response-cache-control"] == ["private, no-store"]
    assert query["response-content-disposition"][0].startswith("inline")
    assert answer.headers["cache-control"] == "private, no-store"


@S3_STORE
async def test_the_stated_expiry_is_never_later_than_the_signed_links(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await enable(sessions)
    staff, _ = await sessions.member(role="read_only")
    item = await ready_video(sessions, training_store, tenant)
    signed: list[datetime] = []
    assert isinstance(training_store, S3ObjectStore)
    real = training_store.presigned_playback

    def slow_signing(*args: Any, **kwargs: Any) -> str | None:
        signed.append(datetime.now(UTC))
        url = real(*args, **kwargs)
        time.sleep(1.2)  # a slow signer: the answer must not outlive the link it describes
        return url

    monkeypatch.setattr(training_store, "presigned_playback", slow_signing)
    answer = await staff.get(f"{ITEMS}/{item}/playback")

    assert answer.status_code == 200, answer.text
    expires = datetime.fromisoformat(answer.json()["expires_at"])
    [at] = signed
    assert expires <= at + timedelta(seconds=limits.PLAYBACK_URL_SECONDS)


@S3_STORE
async def test_another_companys_item_gets_no_url(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = await enable(sessions)
    theirs = await enable(sessions, sessions.tenants[1])
    staff, _ = await sessions.member(tenant=mine)
    foreign = await ready_video(sessions, training_store, theirs)
    calls: list[Any] = []

    def recording(*args: Any, **kwargs: Any) -> str:
        calls.append(args)
        return "https://example.invalid/never"

    monkeypatch.setattr(training_store, "presigned_playback", recording)

    for path in (f"{ITEMS}/{foreign}/playback", f"{ITEMS}/{foreign}/transcript"):
        answer = await staff.get(path)
        assert answer.status_code == 404, answer.text
        assert detail(answer) == "training_not_found"
    # A platform admin viewing their own company is no way in either.
    viewing = await sessions.admin.get(f"{ITEMS}/{foreign}/playback?client_id={mine}")
    assert viewing.status_code == 404 and detail(viewing) == "training_not_found"
    assert calls == []


async def test_another_companys_item_is_404_on_the_local_video_route(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    mine = await enable(sessions)
    theirs = await enable(sessions, sessions.tenants[1])
    staff, _ = await sessions.member(tenant=mine)
    foreign = await ready_video(sessions, training_store, theirs)
    answer = await staff.get(f"{ITEMS}/{foreign}/video")
    assert answer.status_code == 404 and detail(answer) == "training_not_found"
    query = await staff.get(f"{ITEMS}/{foreign}/video?client_id={theirs}")
    assert query.status_code == 403


async def test_mov_plays_as_video_mp4_and_webm_as_video_webm(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    staff, _ = await sessions.member()
    mov = await ready_video(
        sessions, training_store, tenant, container="mp4", original_name="Walkthrough.MOV"
    )
    webm = await ready_video(
        sessions, training_store, tenant, container="webm", original_name="clip.webm"
    )
    for item, media_type in ((mov, "video/mp4"), (webm, "video/webm")):
        link = await staff.get(f"{ITEMS}/{item}/playback")
        assert link.status_code == 200 and link.json()["media_type"] == media_type
        played = await staff.get(f"{ITEMS}/{item}/video", headers={"Range": "bytes=0-3"})
        assert played.status_code == 206
        assert_video_headers(played, media_type)


async def test_playback_url_and_transcript_never_logged(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    await _never_logged(sessions, training_store)


@S3_STORE
async def test_playback_url_and_transcript_never_logged_on_s3(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    await _never_logged(sessions, training_store)


async def _never_logged(sessions: SessionWorld, store: ObjectStore) -> None:
    tenant = await enable(sessions)
    staff, _ = await sessions.member()
    item = await ready_video(
        sessions,
        store,
        tenant,
        title="Secret induction title",
        original_name="Secret induction file.mp4",
    )
    await segment(sessions, tenant, item, 0, 0, 0, "Secret spoken sentence")
    [row] = await sessions.q("SELECT storage_name FROM training_items WHERE id=%s", item)
    with capture_logs() as logs:
        link = await staff.get(f"{ITEMS}/{item}/playback")
        assert link.status_code == 200
        if link.json()["url"].startswith("/"):
            assert (await staff.get(link.json()["url"])).status_code == 200
        transcript = await staff.get(f"{ITEMS}/{item}/transcript")
        assert transcript.status_code == 200
        await staff.get(f"{ITEMS}/{uuid4()}/playback")
    text = repr(logs)
    url = link.json()["url"]
    for secret in (
        "Secret induction",
        "Secret spoken",
        row["storage_name"],
        url,
        urlsplit(url).query or "no-query",
        "X-Amz-",
    ):
        assert secret not in text, secret
    mine = [entry for entry in logs if entry.get("item_id") == item]
    assert all(entry.get("client_id") == tenant for entry in mine)


async def test_transcript_shape_order_and_reader_rule(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    reader, _ = await sessions.member(role="read_only")
    admin, _ = await sessions.member(role="tenant_admin")
    item = await ready_video(sessions, training_store, tenant, status="transcribing")
    # Inserted out of order: the answer follows (chunk_index, position).
    await segment(sessions, tenant, item, 1, 0, 301_000, "Third.")
    await segment(sessions, tenant, item, 0, 1, 2_000, "Second.")
    await segment(sessions, tenant, item, 0, 0, 0, "First.")

    # A reader cannot see a video that is not ready, in any of the three routes.
    for suffix in ("transcript", "playback", "video"):
        hidden = await reader.get(f"{ITEMS}/{item}/{suffix}")
        assert hidden.status_code == 404, suffix
        assert detail(hidden) == "training_not_found"

    partial = await admin.get(f"{ITEMS}/{item}/transcript")
    assert partial.status_code == 200, partial.text
    assert partial.json() == {
        "language": None,
        "speech": None,
        "segments": [
            {"start_ms": 0, "end_ms": 900, "text": "First."},
            {"start_ms": 2_000, "end_ms": 2_900, "text": "Second."},
            {"start_ms": 301_000, "end_ms": 301_900, "text": "Third."},
        ],
    }
    assert partial.headers["cache-control"] == "private, no-store"

    await sessions.q(
        "UPDATE training_items SET status='ready', speech='speech', transcript_language='en'"
        " WHERE id=%s",
        item,
    )
    done = await reader.get(f"{ITEMS}/{item}/transcript")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["language"] == "en" and body["speech"] == "speech"
    assert [line["text"] for line in body["segments"]] == ["First.", "Second.", "Third."]
    assert set(body["segments"][0]) == {"start_ms", "end_ms", "text"}


async def test_transcript_of_a_silent_video_is_empty_with_its_speech_state(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    reader, _ = await sessions.member(role="read_only")
    item = await ready_video(sessions, training_store, tenant, speech="no_speech")
    answer = await reader.get(f"{ITEMS}/{item}/transcript")
    assert answer.status_code == 200
    assert answer.json() == {"language": None, "speech": "no_speech", "segments": []}


async def test_managers_see_every_state_but_a_file_that_is_not_there_is_404(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    for status in ("uploaded", "transcribing", "summarising"):
        item = await ready_video(sessions, training_store, tenant, status=status)
        assert (await admin.get(f"{ITEMS}/{item}/playback")).status_code == 200, status
        assert (await admin.get(f"{ITEMS}/{item}/video")).status_code == 200, status
        assert (await admin.get(f"{ITEMS}/{item}/transcript")).status_code == 200, status
    # Failed for a reason the file's retry can fix: the file is kept and can be played.
    kept = await ready_video(
        sessions,
        training_store,
        tenant,
        status="failed",
        failure_code="training_transcription_unavailable",
    )
    assert (await admin.get(f"{ITEMS}/{kept}/video")).status_code == 200
    # Failed for the file itself (the worker deleted it), or still uploading: nothing to play.
    gone = await video_row(
        sessions, tenant, status="failed", failure_code="training_video_unreadable"
    )
    uploading = await video_row(sessions, tenant, status="uploading")
    for item in (gone, uploading):
        for suffix in ("playback", "video"):
            answer = await admin.get(f"{ITEMS}/{item}/{suffix}")
            assert answer.status_code == 404, (suffix, answer.text)
            assert detail(answer) == "training_not_found"
        assert (await admin.get(f"{ITEMS}/{item}/transcript")).status_code == 200


async def test_a_pdf_item_is_404_on_the_video_routes(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    pdf = (await upload(sessions, admin, make_pdf(2))).json()["id"]
    for suffix in ("playback", "video", "transcript"):
        answer = await admin.get(f"{ITEMS}/{pdf}/{suffix}")
        assert answer.status_code == 404, suffix
        assert detail(answer) == "training_not_found"
    missing = await admin.get(f"{ITEMS}/{uuid4()}/transcript")
    assert missing.status_code == 404 and detail(missing) == "training_not_found"


async def test_a_missing_object_is_503_when_the_row_stays_and_404_when_it_is_gone(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = await video_row(sessions, tenant, status="ready")  # no object stored
    with capture_logs() as logs:
        answer = await admin.get(f"{ITEMS}/{item}/video")
    assert answer.status_code == 503 and detail(answer) == "training_storage_unavailable"
    assert any(entry["event"] == "training_file_missing" for entry in logs)
    await sessions.q("DELETE FROM training_items WHERE id=%s", item)
    gone = await admin.get(f"{ITEMS}/{item}/video")
    assert gone.status_code == 404


async def test_read_routes_roles_package_and_machine_token(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = sessions.tenants[0]
    staff, _ = await sessions.member()
    item = str(uuid4())
    for browser, query in ((sessions.admin, f"?client_id={tenant}"), (staff, "")):
        for suffix in ("playback", "video", "transcript"):
            answer = await browser.get(f"{ITEMS}/{item}/{suffix}{query}")
            assert answer.status_code == 403, (suffix, answer.text)
            assert detail(answer) == "package_inactive"

    await enable(sessions, tenant)
    read_only, _ = await sessions.member(role="read_only")
    item = await ready_video(sessions, training_store, tenant)
    for browser in (read_only, staff):
        for suffix in ("playback", "video", "transcript"):
            answer = await browser.get(f"{ITEMS}/{item}/{suffix}")
            assert answer.status_code == 200, (suffix, answer.text)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sessions.app), base_url=ORIGIN
    ) as machine:
        headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": tenant}
        for suffix in ("playback", "video", "transcript"):
            answer = await machine.get(f"{ITEMS}/{item}/{suffix}", headers=headers)
            assert answer.status_code == 403, (suffix, answer.text)
            assert detail(answer) == "human session required"
