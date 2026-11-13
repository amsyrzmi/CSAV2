"""Real browser acceptance for videos in the Training library: the range request through the
console's rewrite, the resumable upload, the player, the clickable transcript, the summary states
and the manager's processing states.

The console runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds. Every browser-side `/console/v1/*` call is answered by
Playwright from `VideoLibrary` except the video route, which goes on through the console's
`/console/v1` rewrite to the loopback stub; the stub answers it with the headers and the single
byte range the gateway's video route sends (docs/CONSOLE_API.md, "The video route"), so the tests
prove that the rewrite keeps `Range`, `Content-Range`, `Accept-Ranges` and the video type, and
that the browser's `<video>` plays what comes through.

Videos are synthetic: a 3 s clip made with ffmpeg in the test (`requires_ffmpeg`) or plain bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Page, Request, Route, expect

from tests import test_services_redesign_browser as services
from tests.conftest import requires_ffmpeg
from tests.test_services_browser import WAIT
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_training_browser import (
    ITEM,
    MARKUP,
    UUID,
    Files,
    Library,
    make_item,
    make_summary,
    no_sideways_scroll,
    training_page,
    until,
)
from tests.training_media import make_video

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

PIECE = 8 * 1024 * 1024
VIDEO_ROUTE = re.compile(r"/console/v1/training/items/([0-9a-f-]{36})/video")
NEW_VIDEO = "88888888-8888-4888-8888-0000000000a1"
NOT_READY = "88888888-8888-4888-8888-0000000000a2"


def make_video_item(item_id: str, title: str, **extra: Any) -> dict[str, Any]:
    """An `ItemOut` for a video (docs/CONSOLE_API.md, Training library)."""
    item = make_item(
        item_id,
        title,
        kind="video",
        view_mode=None,
        page_count=None,
        byte_size=1_000_000,
        status="ready",
        duration_ms=3_000,
        failure=None,
        processing=None,
        notice=None,
    )
    item.update(extra)
    return item


@dataclass
class Videos:
    """What the loopback stub answers on the video route: the bytes, or None for a 404."""

    bodies: dict[str, bytes | None] = field(default_factory=dict)
    ranges: list[tuple[str, str | None]] = field(default_factory=list)
    media_type: str = "video/mp4"


@pytest.fixture
def videos(site: Site) -> Iterator[Videos]:
    """Teach the loopback upstream the video route for one test (the gateway's local route)."""
    store = Videos()
    server: Any = site.upstream.server
    base: Any = server.RequestHandlerClass

    class Handler(base):
        def do_GET(self) -> None:
            path, _, query = self.path.partition("?")
            found = VIDEO_ROUTE.fullmatch(path)
            if not found or found[1] not in store.bodies or query:
                return super().do_GET()
            body = store.bodies[found[1]]
            header = self.headers.get("Range")
            store.ranges.append((found[1], header))
            if body is None:
                data = json.dumps({"detail": "training_not_found"}).encode()
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            size = len(body)
            span = re.fullmatch(r"bytes=(\d*)-(\d*)", header or "")
            first, last = 0, size - 1
            status = 200
            if span and (span[1] or span[2]):
                status = 206
                if span[1]:
                    first = int(span[1])
                    last = min(int(span[2]), size - 1) if span[2] else size - 1
                else:
                    first = max(size - int(span[2]), 0)
            chunk = body[first : last + 1]
            self.send_response(status)
            self.send_header("Content-Type", store.media_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", "inline")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 206:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)

    server.RequestHandlerClass = Handler
    try:
        yield store
    finally:
        server.RequestHandlerClass = base


@dataclass
class VideoLibrary(Library):
    """`Library` plus the video routes, answered in the browser."""

    videos: Videos = field(default_factory=Videos)
    transcripts: dict[str, dict[str, Any]] = field(default_factory=dict)
    playbacks: list[str] = field(default_factory=list)
    transcript_reads: list[str] = field(default_factory=list)
    retries: list[str] = field(default_factory=list)
    # The upload: what the stub has received, and what it should refuse once.
    starts: list[dict[str, Any]] = field(default_factory=list)
    pieces: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    stored: set[int] = field(default_factory=set)
    completes: int = 0
    # Piece number -> the HTTP status to answer once, instead of storing it.
    fail_piece_once: dict[int, int] = field(default_factory=dict)
    # Video requests to refuse with 403 (an expired link) before serving.
    expire_links: int = 0
    fail_every_piece: set[int] = field(default_factory=set)
    forget_on_complete: set[int] = field(default_factory=set)
    piece_count: int = 0
    declared: int = 0

    async def route(self, request_route: Route) -> None:
        if VIDEO_ROUTE.fullmatch(urlsplit(request_route.request.url).path):
            if self.expire_links > 0:
                self.expire_links -= 1
                await request_route.fulfill(status=403, json={"detail": "expired"})
            else:
                await request_route.continue_()
            return
        await super().route(request_route)

    def answer(self, request: Request, path: str, query: str) -> tuple[int, Any]:
        method = request.method
        if path.startswith("training/") and not query:
            found = re.fullmatch(
                r"training/items/([0-9a-f-]{36})/(playback|transcript|retry)", path
            )
            if found:
                item = self.find(found[1])
                if item is None:
                    return 404, {"detail": "training_not_found"}
                if found[2] == "playback" and method == "GET":
                    self.playbacks.append(item["id"])
                    return 200, {
                        "url": f"/console/v1/training/items/{item['id']}/video",
                        "expires_at": "2099-01-01T00:00:00Z",
                        "media_type": self.videos.media_type,
                    }
                if found[2] == "transcript" and method == "GET":
                    self.transcript_reads.append(item["id"])
                    return 200, self.transcripts.get(
                        item["id"], {"language": None, "speech": None, "segments": []}
                    )
                if found[2] == "retry" and method == "POST":
                    self.retries.append(item["id"])
                    item.update(status="transcribing", failure=None)
                    item["processing"] = {
                        "stage": "transcribing",
                        "chunks_done": 0,
                        "chunks_total": None,
                        "waiting": False,
                    }
                    return 200, item
            if path == "training/videos" and method == "POST":
                return self.start(request)
            piece = re.fullmatch(r"training/videos/([0-9a-f-]{36})/pieces/(\d+)", path)
            if piece and method == "PUT":
                return self.put_piece(request, int(piece[2]))
            upload = re.fullmatch(r"training/videos/([0-9a-f-]{36})/(upload|complete)", path)
            if upload and upload[2] == "upload" and method == "GET":
                return 200, self.plan()
            if upload and upload[2] == "complete" and method == "POST":
                return self.complete()
        return super().answer(request, path, query)

    def plan(self) -> dict[str, Any]:
        item = self.find(NEW_VIDEO)
        assert item is not None
        return {
            "item": item,
            "piece_size": PIECE,
            "piece_count": self.piece_count,
            "received": sorted(self.stored),
        }

    def start(self, request: Request) -> tuple[int, Any]:
        body = request.post_data_json
        self.starts.append({**body, "key": request.headers.get("idempotency-key")})
        self.declared = body["byte_size"]
        self.piece_count = -(-self.declared // PIECE)
        item = make_video_item(
            NEW_VIDEO,
            re.sub(r"\.[^.]+$", "", body["file_name"]),
            status="uploading",
            duration_ms=None,
            byte_size=self.declared,
        )
        if self.find(NEW_VIDEO) is None:
            self.items.insert(0, item)
        return 201, self.plan()

    def put_piece(self, request: Request, number: int) -> tuple[int, Any]:
        data = request.post_data_buffer or b""
        record = {
            "number": number,
            "size": len(data),
            "sha": request.headers.get("x-piece-sha256"),
            "type": request.headers.get("content-type", ""),
            "ok": hashlib.sha256(data).hexdigest() == request.headers.get("x-piece-sha256"),
        }
        self.pieces.setdefault(number, []).append(record)
        if number in self.fail_every_piece:
            return 503, {"detail": "training_storage_unavailable"}
        failure = self.fail_piece_once.pop(number, None)
        if failure:
            return failure, {"detail": "training_storage_unavailable"}
        self.stored.add(number)
        return 200, {"number": number, "received": len(self.stored)}

    def complete(self) -> tuple[int, Any]:
        self.completes += 1
        if self.forget_on_complete:
            self.stored -= self.forget_on_complete
            self.forget_on_complete = set()
            return 409, {"detail": "training_piece_missing"}
        item = self.find(NEW_VIDEO)
        assert item is not None
        item.update(
            status="uploaded",
            processing={
                "stage": "transcribing",
                "chunks_done": 0,
                "chunks_total": None,
                "waiting": False,
            },
        )
        return 200, item


def segments(*lines: tuple[int, str]) -> list[dict[str, Any]]:
    """Transcript segments: each line runs from its start to the next one (or a second on)."""
    found: list[dict[str, Any]] = []
    for index, (start, text) in enumerate(lines):
        end = lines[index + 1][0] if index + 1 < len(lines) else start + 1000
        found.append({"start_ms": start, "end_ms": end, "text": text})
    return found


async def test_video_range_request_passes_through_the_console_rewrite(
    site: Site, videos: Videos
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), videos=videos)
        data = bytes(range(256)) * 8
        library.add(make_video_item(ITEM, "Synthetic induction video"), body=None)
        videos.bodies[ITEM] = data
        async with training_page(browser, site, library) as page:
            seen = await page.evaluate(
                """async (id) => {
                    const response = await fetch(`/console/v1/training/items/${id}/video`, {
                        headers: { Range: "bytes=0-99" }, credentials: "same-origin",
                    });
                    const bytes = new Uint8Array(await response.arrayBuffer());
                    return {
                        status: response.status,
                        contentRange: response.headers.get("Content-Range"),
                        acceptRanges: response.headers.get("Accept-Ranges"),
                        contentType: response.headers.get("Content-Type"),
                        length: bytes.length,
                        first: Array.from(bytes.slice(0, 4)),
                    };
                }""",
                ITEM,
            )
        assert seen == {
            "status": 206,
            "contentRange": f"bytes 0-99/{len(data)}",
            "acceptRanges": "bytes",
            "contentType": "video/mp4",
            "length": 100,
            "first": [0, 1, 2, 3],
        }
        assert videos.ranges == [(ITEM, "bytes=0-99")]

    await with_browser(body)


# ---- The upload -------------------------------------------------------------------------------

SOUND_NOTICE = (
    "The sound in this video is sent to our speech-to-text provider to make the transcript."
)
STOPPED = "Upload stopped. Check your connection and select Continue"
UPLOAD_SIZE = 2 * PIECE + 5
# Piece 1 starts as an MP4 does; the stub does not look, the shape is only for the picture.
UPLOAD_BYTES = b"\x00\x00\x00\x18ftypisom" + bytes(UPLOAD_SIZE - 12)


async def pick_video(page: Page, folder: Path) -> Any:
    """Open the add dialog and choose the synthetic video; returns the dialog."""
    await page.get_by_test_id("training-add").click()
    dialog = page.get_by_test_id("training-dialog")
    await expect(dialog).to_be_visible()
    video = folder / "synthetic-induction.mp4"
    video.write_bytes(UPLOAD_BYTES)
    await dialog.get_by_test_id("training-file").set_input_files(str(video))
    await expect(dialog.get_by_test_id("training-title")).to_have_value("synthetic-induction")
    # A video says where its sound goes, and has no PDF view mode.
    await expect(dialog.get_by_test_id("training-sound-notice")).to_have_text(SOUND_NOTICE)
    await expect(dialog.get_by_test_id("training-view-mode")).to_have_count(0)
    return dialog


def piece_counts(library: VideoLibrary) -> dict[int, int]:
    return {number: len(sent) for number, sent in sorted(library.pieces.items())}


async def test_manager_uploads_a_video_and_a_failed_piece_is_retried_alone(
    site: Site, videos: Videos, tmp_path: Path
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), videos=videos)
        library.fail_piece_once = {2: 500}
        async with training_page(browser, site, library, wait="training-empty") as page:
            dialog = await pick_video(page, tmp_path)
            await dialog.get_by_test_id("training-title").fill("Synthetic induction video")
            await dialog.get_by_test_id("training-category").fill("Onboarding")
            await dialog.get_by_test_id("training-dialog-save").click()
            await expect(page.get_by_test_id("training-message")).to_have_text(
                "Added Synthetic induction video. It is being processed, and everyone in your "
                "company can watch it once it is ready.",
                timeout=30_000,
            )
            await expect(dialog).to_have_count(0)
            # One start, with the file's name, size and type under one key.
            assert len(library.starts) == 1, library.starts
            start = library.starts[0]
            assert UUID.fullmatch(start["key"] or ""), start
            assert {k: start[k] for k in ("file_name", "byte_size", "media_type")} == {
                "file_name": "synthetic-induction.mp4",
                "byte_size": UPLOAD_SIZE,
                "media_type": "video/mp4",
            }
            # Piece 2 failed once and went again alone: the others were sent once.
            assert piece_counts(library) == {1: 1, 2: 2, 3: 1}, library.pieces
            sent = [record for records in library.pieces.values() for record in records]
            assert all(record["ok"] for record in sent), "a piece's hash is not its own bytes"
            assert all(record["type"] == "application/octet-stream" for record in sent)
            assert [records[-1]["size"] for _, records in sorted(library.pieces.items())] == [
                PIECE,
                PIECE,
                5,
            ]
            assert library.completes == 1
            assert library.patches == [
                {"title": "Synthetic induction video", "category": "Onboarding"}
            ]
            row = page.get_by_test_id(f"training-item-{NEW_VIDEO}")
            await expect(row).to_contain_text("Synthetic induction video")
            await expect(page.get_by_test_id(f"training-state-{NEW_VIDEO}")).to_have_text(
                "Waiting to be processed"
            )

    await with_browser(body)


async def test_upload_stopped_then_continue_sends_only_missing_pieces(
    site: Site, videos: Videos, tmp_path: Path
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), videos=videos)
        library.fail_every_piece = {2}
        async with training_page(browser, site, library, wait="training-empty") as page:
            dialog = await pick_video(page, tmp_path)
            await dialog.get_by_test_id("training-dialog-save").click()
            # Piece 2 is tried, then retried after 1 s, 2 s and 4 s, and then given up.
            await expect(dialog.get_by_test_id("training-dialog-error")).to_contain_text(
                STOPPED, timeout=45_000
            )
            assert piece_counts(library) == {1: 1, 2: 4, 3: 1}, library.pieces
            assert library.completes == 0, "an upload with a piece missing is not completed"
            # Leaving now would lose the upload, so the page asks first.
            asked = await page.evaluate(
                """() => {
                    const event = new Event("beforeunload", { cancelable: true });
                    window.dispatchEvent(event);
                    return event.defaultPrevented;
                }"""
            )
            assert asked, "the page did not ask before leaving a stopped upload"
            continue_button = dialog.get_by_test_id("training-upload-continue")
            await expect(continue_button).to_be_visible()
            # The connection is back: Continue asks what the server has and sends the rest.
            library.fail_every_piece = set()
            await continue_button.click()
            await expect(page.get_by_test_id("training-message")).to_contain_text(
                "Added synthetic-induction.", timeout=30_000
            )
            assert piece_counts(library) == {1: 1, 2: 5, 3: 1}, library.pieces
            assert library.completes == 1
            assert library.stored == {1, 2, 3}
            assert len(library.starts) == 1, "Continue does not start a second upload"

    await with_browser(body)


# ---- Playing ----------------------------------------------------------------------------------

LINES = segments(
    (0, "First line of the induction."),
    (1000, "Second line about boots."),
    (2000, "Third line about the exits."),
)


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A synthetic 3 s WebM (the test browser has no H.264), made by ffmpeg."""
    folder: Path = tmp_path_factory.mktemp("clip")
    return make_video(folder / "clip.webm", seconds=3, container="webm").read_bytes()


def add_video(
    library: VideoLibrary,
    videos: Videos,
    clip: bytes | None,
    item_id: str = ITEM,
    title: str = "Synthetic induction video",
    lines: list[dict[str, Any]] | None = None,
    speech: str | None = "speech",
    **extra: Any,
) -> dict[str, Any]:
    item = make_video_item(item_id, title, **extra)
    library.add(item, body=None)
    if clip is not None:
        videos.bodies[item_id] = clip
        videos.media_type = "video/webm"
    library.transcripts[item_id] = {
        "language": "en" if speech == "speech" else None,
        "speech": speech,
        "segments": LINES if lines is None else lines,
    }
    return item


async def current_time(page: Page) -> float:
    return await page.get_by_test_id("training-video-player").evaluate("v => v.currentTime")


@requires_ffmpeg
async def test_reader_plays_and_a_transcript_line_seeks(
    site: Site, videos: Videos, clip: bytes
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), can_manage=False, videos=videos)
        add_video(library, videos, clip, has_summary=True)
        library.summaries[ITEM] = make_summary("A synthetic summary.", "Wear boots")
        async with training_page(
            browser,
            site,
            library,
            role="tenant_staff",
            path=f"/portal/training/{ITEM}",
            wait="training-video-player",
        ) as page:
            player = page.get_by_test_id("training-video-player")
            await expect(player).to_have_attribute("preload", "metadata")
            await expect(player).to_have_attribute("controls", "")
            await expect(player).to_have_attribute("playsinline", "")

            async def has_metadata() -> bool:
                return await player.evaluate("v => v.readyState >= 1 && v.duration > 2")

            await until(has_metadata, "the video did not load its metadata", 30)
            # The browser's own loading went through the rewrite with a Range header.
            assert any(header and header.startswith("bytes=") for _, header in videos.ranges)
            lines = page.get_by_test_id("training-transcript-line")
            await expect(lines).to_have_count(3)
            await expect(lines.nth(0)).to_have_attribute("aria-current", "true")
            third = lines.filter(has_text="Third line")
            await expect(third.get_by_test_id("training-transcript-stamp")).to_have_text("0:02")
            await third.get_by_test_id("training-transcript-stamp").click()

            async def at_two() -> bool:
                return await current_time(page) >= 1.9

            await until(at_two, f"the video did not seek to 0:02: {await current_time(page)}")
            assert await current_time(page) < 3.1
            await expect(third).to_have_attribute("aria-current", "true")
            await expect(lines.nth(0)).not_to_have_attribute("aria-current", "true")
            # A reader has no manage controls and is told nothing about processing.
            for control in ("training-edit", "training-remove", "training-retry"):
                await expect(page.get_by_test_id(control)).to_have_count(0)
            await expect(page.get_by_test_id("training-video-state")).to_have_count(0)
            await expect(page.get_by_test_id("training-summary")).to_contain_text(
                "A synthetic summary."
            )
            await expect(page.get_by_test_id("training-summarise")).to_have_count(0)
            assert library.summary_posts == [], "a video's summary is never made by the page"

    await with_browser(body)


@requires_ffmpeg
async def test_t_query_seeks_on_load(site: Site, videos: Videos, clip: bytes) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), can_manage=False, videos=videos)
        add_video(library, videos, clip)
        async with training_page(
            browser,
            site,
            library,
            role="tenant_staff",
            path=f"/portal/training/{ITEM}?t=2",
            wait="training-video-player",
        ) as page:

            async def at_two() -> bool:
                return await current_time(page) >= 1.9

            await until(at_two, f"?t=2 did not seek: {await current_time(page)}", 30)
            # Seeking is not playing: the person presses play.
            assert await page.get_by_test_id("training-video-player").evaluate("v => v.paused")
            await expect(page.get_by_test_id("training-transcript-line").nth(2)).to_have_attribute(
                "aria-current", "true"
            )

    await with_browser(body)


@requires_ffmpeg
async def test_an_expired_link_is_replaced_and_the_video_still_loads(
    site: Site, videos: Videos, clip: bytes
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), can_manage=False, videos=videos)
        add_video(library, videos, clip)
        library.expire_links = 1
        async with training_page(
            browser,
            site,
            library,
            role="tenant_staff",
            path=f"/portal/training/{ITEM}",
            wait="training-video-player",
        ) as page:
            player = page.get_by_test_id("training-video-player")

            async def loaded() -> bool:
                return await player.evaluate("v => v.readyState >= 1")

            await until(loaded, "the video did not load after its link expired", 30)
            # The first link was refused (it had expired); another was asked for. (The dev server
            # runs effects twice, so more than two asks is fine.)
            assert library.expire_links == 0
            assert len(library.playbacks) >= 2, "a new playback link was not asked for"
            await expect(page.get_by_test_id("training-video-error")).to_have_count(0)

    await with_browser(body)


@requires_ffmpeg
async def test_transcript_and_summary_markup_render_as_text(
    site: Site, videos: Videos, clip: bytes
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), can_manage=False, videos=videos)
        add_video(
            library,
            videos,
            clip,
            has_summary=True,
            lines=segments((0, MARKUP), (1000, "A plain second line.")),
        )
        library.summaries[ITEM] = make_summary(MARKUP, "Point <i>one</i>")
        async with training_page(
            browser,
            site,
            library,
            role="tenant_staff",
            path=f"/portal/training/{ITEM}",
            wait="training-video-player",
        ) as page:
            lines = page.get_by_test_id("training-transcript-line")
            await expect(lines).to_have_count(2)
            await expect(lines.first).to_contain_text(MARKUP)
            summary = page.get_by_test_id("training-summary")
            await expect(summary).to_contain_text(MARKUP)
            await expect(summary.get_by_test_id("training-summary-points")).to_have_text(
                "Point <i>one</i>"
            )
            # The model's and the speech service's markup is text: nothing was made of it.
            transcript = page.get_by_test_id("training-transcript")
            assert await transcript.locator("b, i, img").count() == 0
            assert await summary.locator("b, i, img").count() == 0
            assert await page.evaluate("() => window.__owned") is None
            # The search box narrows the lines in the browser; nothing is sent.
            before = list(videos.ranges), list(library.transcript_reads)
            search = page.get_by_test_id("training-transcript-search")
            await search.fill("PLAIN")
            await expect(lines).to_have_count(1)
            await expect(page.get_by_test_id("training-transcript-count")).to_have_text(
                "Showing 1 of 2 lines"
            )
            await search.fill("zzz")
            await expect(page.get_by_test_id("training-transcript-none")).to_be_visible()
            await expect(lines).to_have_count(0)
            await search.fill("")
            await expect(lines).to_have_count(2)
            assert (list(videos.ranges), list(library.transcript_reads)) == before

    await with_browser(body)


@requires_ffmpeg
@pytest.mark.parametrize(
    ("speech", "sentence"),
    [
        ("no_speech", "This video has no speech, so there is no transcript or summary."),
        ("no_audio", "This video has no sound, so there is no transcript or summary."),
    ],
)
async def test_no_speech_video_shows_the_no_speech_sentence_and_no_summary_button(
    site: Site, videos: Videos, clip: bytes, speech: str, sentence: str
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), can_manage=False, videos=videos)
        add_video(library, videos, clip, lines=[], speech=speech)
        async with training_page(
            browser,
            site,
            library,
            role="tenant_staff",
            path=f"/portal/training/{ITEM}",
            wait="training-video-player",
        ) as page:
            await expect(page.get_by_test_id("training-summary-status")).to_have_text(sentence)
            await expect(page.get_by_test_id("training-summarise")).to_have_count(0)
            await expect(page.get_by_test_id("training-summary")).to_have_count(0)
            await expect(page.get_by_test_id("training-transcript")).to_have_count(0)
            assert library.summary_posts == []

    await with_browser(body)


@requires_ffmpeg
async def test_summary_is_being_made_for_a_video_without_one(
    site: Site, videos: Videos, clip: bytes
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), can_manage=False, videos=videos)
        add_video(library, videos, clip, has_summary=False)
        async with training_page(
            browser,
            site,
            library,
            role="tenant_staff",
            path=f"/portal/training/{ITEM}",
            wait="training-video-player",
        ) as page:
            await expect(page.get_by_test_id("training-summary-status")).to_have_text(
                "The summary is being made."
            )
            await expect(page.get_by_test_id("training-summarise")).to_have_count(0)

    await with_browser(body)


# ---- What a manager sees ------------------------------------------------------------------------

PROGRESS = "88888888-8888-4888-8888-0000000000b1"
WAITING = "88888888-8888-4888-8888-0000000000b2"
UNREADABLE = "88888888-8888-4888-8888-0000000000b3"
FAILED = "88888888-8888-4888-8888-0000000000b4"
HEVC = "88888888-8888-4888-8888-0000000000b5"


def processing(done: int, total: int | None, *, waiting: bool = False) -> dict[str, Any]:
    return {
        "stage": "transcribing",
        "chunks_done": done,
        "chunks_total": total,
        "waiting": waiting,
    }


@requires_ffmpeg
async def test_manager_sees_progress_failure_and_try_again(
    site: Site, videos: Videos, clip: bytes
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), videos=videos)
        add_video(
            library, videos, clip, HEVC, "Synthetic HEVC video", notice="hevc", has_summary=True
        )
        add_video(
            library,
            videos,
            clip,
            FAILED,
            "Synthetic failed video",
            status="failed",
            failure="training_transcription_unavailable",
            speech=None,
            lines=[],
        )
        add_video(
            library,
            videos,
            None,
            UNREADABLE,
            "Synthetic unreadable video",
            status="failed",
            failure="training_video_unreadable",
            speech=None,
            lines=[],
        )
        add_video(
            library,
            videos,
            None,
            WAITING,
            "Synthetic waiting video",
            status="uploaded",
            processing=processing(0, None, waiting=True),
            speech=None,
            lines=[],
        )
        add_video(
            library,
            videos,
            None,
            PROGRESS,
            "Synthetic progress video",
            status="transcribing",
            processing=processing(3, 12),
            speech=None,
            lines=[],
        )
        async with training_page(browser, site, library) as page:
            state = lambda item_id: page.get_by_test_id(f"training-state-{item_id}")  # noqa: E731
            await expect(state(PROGRESS)).to_have_text("Transcribing, 3 of 12 parts")
            await expect(state(WAITING)).to_contain_text("Waiting to start.")
            await expect(state(UNREADABLE)).to_have_text(
                "We could not open this video. It may be damaged. Save it again as an MP4, "
                "then add it again."
            )
            await expect(state(FAILED)).to_have_text(
                "We could not make the transcript because the speech service did not answer. "
                "Select Try again."
            )
            await expect(state(HEVC)).to_have_count(0)
            # The page reads the list again every 5 s while a video is not ready or failed.
            progress = library.find(PROGRESS)
            assert progress is not None
            progress["processing"] = processing(4, 12)
            await expect(state(PROGRESS)).to_have_text(
                "Transcribing, 4 of 12 parts", timeout=15_000
            )

            # A failure that is the file's own cannot be tried again.
            await page.get_by_test_id(f"training-open-{UNREADABLE}").click()
            await expect(page.get_by_test_id("training-video-state")).to_contain_text(
                "We could not open this video."
            )
            await expect(page.get_by_test_id("training-retry")).to_have_count(0)
            await expect(page.get_by_test_id("training-video-player")).to_have_count(0)
            await page.get_by_test_id("training-back").click()

            # A failure worth another go has "Try again", which asks the server to retry it.
            await page.get_by_test_id(f"training-open-{FAILED}").click()
            retry = page.get_by_test_id("training-retry")
            await expect(retry).to_have_text("Try again")
            await retry.click()
            await expect(page.get_by_test_id("training-video-state")).to_have_text(
                "Transcribing", timeout=15_000
            )
            assert library.retries == [FAILED]
            await expect(page.get_by_test_id("training-retry")).to_have_count(0)
            await page.get_by_test_id("training-back").click()

            # The picture codec some browsers cannot play: managers are told.
            await page.get_by_test_id(f"training-open-{HEVC}").click()
            await expect(page.get_by_test_id("training-hevc-note")).to_contain_text(
                "Some browsers cannot play this video."
            )

    await with_browser(body)


@requires_ffmpeg
async def test_phone_width_has_no_horizontal_scroll(
    site: Site, videos: Videos, clip: bytes
) -> None:
    async def body(browser: Any) -> None:
        library = VideoLibrary(Files(), videos=videos)
        long_word = "Keselamatan-tempat-kerja-dan-prosedur-kecemasan-untuk-semua-kakitangan-2026"
        add_video(
            library,
            videos,
            clip,
            title=long_word,
            description="A synthetic video with a long title that has no spaces in it.",
            category="Keselamatan dan kesihatan pekerjaan",
            has_summary=True,
            lines=segments((0, long_word * 2), (1000, "A short line."), (2000, long_word)),
        )
        library.summaries[ITEM] = make_summary(long_word * 3, long_word)
        async with training_page(browser, site, library, width=360, height=800) as page:
            await no_sideways_scroll(page, "list")
            await page.get_by_test_id(f"training-open-{ITEM}").click()
            await expect(page.get_by_test_id("training-video-player")).to_be_visible(timeout=WAIT)
            await expect(page.get_by_test_id("training-transcript-line")).to_have_count(3)
            await expect(page.get_by_test_id("training-summary")).to_be_visible()
            await no_sideways_scroll(page, "video page")
            # On a phone the transcript is below the player, not beside it.
            player = await page.get_by_test_id("training-video-player").bounding_box()
            transcript = await page.get_by_test_id("training-transcript").bounding_box()
            assert player and transcript
            assert transcript["y"] >= player["y"] + player["height"] - 1, (player, transcript)
            # And beside it on a wide screen.
            await page.set_viewport_size({"width": 1280, "height": 900})
            wide_player = await page.get_by_test_id("training-video-player").bounding_box()
            wide_transcript = await page.get_by_test_id("training-transcript").bounding_box()
            assert wide_player and wide_transcript
            assert wide_transcript["x"] >= wide_player["x"] + wide_player["width"] - 1

    await with_browser(body)
