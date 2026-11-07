"""Real browser acceptance for the Training library: the list, the add dialog, the pdf.js viewer
and the summary.

The real Next.js shell runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds. Its layouts fetch `/auth/me` and `/services/active`
server-side from the loopback stub in `test_services_browser`; every browser-side `/console/v1/*`
call is answered by Playwright from `Library`, except the PDF file route. That one goes on
through the console's `/console/v1` rewrite to the same loopback stub, which answers it with the
headers the gateway sends (`TrainingFile` in `csa/training/service.py`), so the tests prove that
the rewrite keeps the PDF's type and that the viewer accepts what it passes on.

The PDF is synthetic and built here with pypdf: three landscape pages, each filled with its own
colour, so a pixel read back from a canvas says which page is on it.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Page, Request, Route, expect
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from tests import test_services_redesign_browser as services
from tests.test_services_browser import TENANT_PROFILE, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

PACKAGES = [{"key": "training_library", "status": "active"}]
ITEM = "88888888-8888-4888-8888-000000000001"
ADDED = "88888888-8888-4888-8888-000000000002"
FILE_ROUTE = re.compile(r"/console/v1/training/items/([0-9a-f-]{36})/file")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
# Page n of the synthetic PDF is filled with COLOURS[n - 1]. A4 landscape, in PDF points.
COLOURS = ((200, 30, 30), (30, 160, 60), (30, 60, 200))
PAGE_W, PAGE_H = 842, 595
DAMAGED = (
    "This PDF is damaged, so we cannot show it. Ask the person who added it to upload it again."
)
GONE = "This PDF is no longer in the library."
PROVIDER_NOTICE = "Slide text is sent to our language-model provider when you make a summary."
# Model output that looks like markup: the page must show it as these characters, never parse it.
MARKUP = '<b>Bold claim</b> <img src="x" onerror="window.__owned = 1">'
JUNK = b"Synthetic junk bytes where a PDF used to be.\n" * 20


def landscape_pdf() -> bytes:
    writer = PdfWriter()
    for colour in COLOURS:
        page = writer.add_blank_page(width=PAGE_W, height=PAGE_H)
        stream = DecodedStreamObject()
        fill = " ".join(f"{part / 255:.4f}" for part in colour)
        stream.set_data(f"{fill} rg 0 0 {PAGE_W} {PAGE_H} re f".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


PDF = landscape_pdf()


def make_item(item_id: str, title: str, **extra: Any) -> dict[str, Any]:
    """An `ItemOut` (docs/console_openapi.json)."""
    item: dict[str, Any] = {
        "id": item_id,
        "title": title,
        "description": "",
        "category": "",
        "view_mode": "slides",
        "page_count": len(COLOURS),
        "byte_size": len(PDF),
        "created_at": "2026-11-06T09:00:00Z",
        "uploaded_by_name": "Synthetic User",
        "has_summary": False,
    }
    item.update(extra)
    return item


def make_summary(summary: str, *points: str) -> dict[str, Any]:
    """A `SummaryOut` (docs/console_openapi.json)."""
    return {
        "summary": summary,
        "key_points": list(points),
        "truncated": False,
        "model": "synthetic-model",
        "summarised_at": "2026-11-06T10:00:00Z",
    }


@dataclass
class Files:
    """What the server-side stub answers on the file route: the bytes, or None for a 404."""

    bodies: dict[str, bytes | None] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)


@pytest.fixture
def files(site: Site) -> Iterator[Files]:
    """Teach the loopback upstream the file route for one test, as `RecordsUpstream` does."""
    store = Files()
    server: Any = site.upstream.server  # its handler class is built inside Upstream
    base: Any = server.RequestHandlerClass

    class Handler(base):
        def do_GET(self) -> None:
            path, _, query = self.path.partition("?")
            found = FILE_ROUTE.fullmatch(path)
            if not found or found[1] not in store.bodies or query:
                return super().do_GET()
            store.reads.append(found[1])
            body = store.bodies[found[1]]
            if body is None:
                body = json.dumps({"detail": "training_not_found"}).encode()
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
            else:
                # The gateway's headers for the file (csa/training/service.py `TrainingFile`).
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", "inline; filename*=utf-8''synthetic.pdf")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("Content-Security-Policy", "sandbox")
                self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server.RequestHandlerClass = Handler
    try:
        yield store
    finally:
        server.RequestHandlerClass = base


@dataclass
class Library:
    """The browser-side answers for one company's Training library."""

    files: Files
    items: list[dict[str, Any]] = field(default_factory=list)
    can_manage: bool = True
    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    next_summary: dict[str, Any] = field(default_factory=lambda: make_summary("Synthetic."))
    uploads: list[dict[str, Any]] = field(default_factory=list)
    patches: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    summary_posts: list[Any] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def add(self, item: dict[str, Any], body: bytes | None = PDF) -> None:
        self.items.insert(0, item)
        self.files.bodies[item["id"]] = body

    def find(self, item_id: str) -> dict[str, Any] | None:
        return next((item for item in self.items if item["id"] == item_id), None)

    def listing(self) -> dict[str, Any]:
        """An `ItemList`: newest first, each category once, and whether this caller manages."""
        names = {item["category"] for item in self.items if item["category"]}
        return {
            "items": self.items,
            "categories": sorted(names, key=str.lower),
            "can_manage": self.can_manage,
        }

    async def route(self, request_route: Route) -> None:
        request = request_route.request
        url = urlsplit(request.url)
        if FILE_ROUTE.fullmatch(url.path):
            # On through the console's rewrite to the loopback upstream, as to the gateway.
            await request_route.continue_()
            return
        status, data = self.answer(request, url.path.removeprefix("/console/v1/"), url.query)
        if status == 204:
            await request_route.fulfill(status=204, body="")
        else:
            await request_route.fulfill(status=status, json=data)

    def answer(self, request: Request, path: str, query: str) -> tuple[int, Any]:
        method = request.method
        if path == "auth/csrf":
            return 200, {"csrf_token": "synthetic-csrf"}
        if path == "profile" and method == "GET":
            return 200, TENANT_PROFILE
        if path == "services/active" and method == "GET":
            return 200, {"packages": PACKAGES}
        # A member's session names the company: no training call may name one.
        if path.startswith("training/") and not query:
            if path == "training/items" and method == "GET":
                return 200, self.listing()
            if path == "training/items" and method == "POST":
                return self.upload(request)
            found = re.fullmatch(r"training/items/([0-9a-f-]{36})(/summary)?", path)
            item = self.find(found[1]) if found else None
            if found and item is None:
                return 404, {"detail": "training_not_found"}
            if found and item is not None:
                if found[2] and method == "GET":
                    return 200, self.summaries.get(item["id"], {"summary": None})
                if found[2] and method == "POST":
                    return self.summarise(item, request.post_data_json)
                if method == "PATCH":
                    change: dict[str, Any] = request.post_data_json or {}
                    self.patches.append(change)
                    item.update({k: v for k, v in change.items() if v is not None})
                    return 200, item
                if method == "DELETE":
                    self.deletes.append(item["id"])
                    self.items.remove(item)
                    self.files.bodies.pop(item["id"], None)
                    return 204, None
        self.unexpected.append(f"{method} {path}?{query}")
        return 500, {"detail": "unexpected synthetic call"}

    def upload(self, request: Request) -> tuple[int, Any]:
        """Step one of an add: the file alone, as the multipart part `file`."""
        self.uploads.append(
            {
                "key": request.headers.get("idempotency-key"),
                "type": request.headers.get("content-type", ""),
                "body": request.post_data_buffer,
            }
        )
        item = make_item(ADDED, "synthetic-induction")
        self.add(item)
        return 201, item

    def summarise(self, item: dict[str, Any], body: Any) -> tuple[int, Any]:
        self.summary_posts.append(body)
        if body.get("regenerate") or item["id"] not in self.summaries:
            self.summaries[item["id"]] = self.next_summary
            item["has_summary"] = True
        return 200, self.summaries[item["id"]]


@asynccontextmanager
async def training_page(
    browser: Any,
    site: Site,
    library: Library,
    *,
    role: str = "tenant_admin",
    path: str = "/portal/training",
    wait: str | None = "training-list",
    width: int = 1280,
    height: int = 1000,
) -> AsyncIterator[Page]:
    site.upstream.me, site.upstream.packages = make_me(role), PACKAGES
    context = await browser.new_context(viewport={"width": width, "height": height})
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    await context.add_cookies(
        [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    )
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            library.console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    await page.route("**/console/v1/**", library.route)
    await page.goto(f"{site.base}{path}")
    if wait:
        await expect(page.get_by_test_id(wait)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not library.unexpected, library.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


async def until(check: Callable[[], Awaitable[bool]], message: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not await check():
        assert time.monotonic() < deadline, message
        await asyncio.sleep(0.1)


CENTRE = """canvas => {
    const pixel = canvas.getContext("2d").getImageData(canvas.width >> 1, canvas.height >> 1, 1, 1);
    return Array.from(pixel.data.slice(0, 3));
}"""


def near(seen: list[int], colour: tuple[int, ...]) -> bool:
    return len(seen) == 3 and all(abs(a - b) <= 4 for a, b in zip(seen, colour, strict=True))


async def expect_slide(page: Page, number: int) -> None:
    """The counter says slide `number` and the stage's canvas shows that page's colour."""
    await expect(page.get_by_test_id("training-counter")).to_have_text(
        f"Slide {number} of {len(COLOURS)}"
    )
    canvas = page.get_by_test_id("training-page-canvas")
    seen: list[int] = []

    async def drawn() -> bool:
        nonlocal seen
        if await canvas.count() != 1:
            return False
        seen = await canvas.evaluate(CENTRE)
        return near(seen, COLOURS[number - 1])

    await until(drawn, f"slide {number} is not drawn: centre pixel {seen}")


async def open_item(page: Page, item_id: str) -> None:
    await page.get_by_test_id(f"training-open-{item_id}").click()
    await expect(page.get_by_test_id("training-viewer")).to_be_visible(timeout=WAIT)


async def no_sideways_scroll(page: Page, label: str) -> None:
    measured = await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )
    assert measured["scroll"] <= measured["inner"], (label, measured)
    # A control inside a strip that scrolls sideways on its own (the thumbnails) is reachable
    # there; anything else past the right edge is cut off or widens the page.
    outside = await page.evaluate(
        """() => [...document.querySelectorAll('[data-testid^="training-"]')].filter(e => {
            let right = e.getBoundingClientRect().right;
            for (let up = e.parentElement; up; up = up.parentElement) {
                if (["auto", "scroll"].includes(getComputedStyle(up).overflowX))
                    right = Math.min(right, up.getBoundingClientRect().right);
            }
            return right > window.innerWidth + 0.5;
        }).map(e => e.dataset.testid)"""
    )
    assert not outside, (label, outside)


async def scrolled(page: Page) -> float:
    """How far the page has scrolled, in whichever element scrolls it."""
    return await page.evaluate(
        "() => [...document.querySelectorAll('*')].reduce((sum, el) => sum + el.scrollTop, 0)"
    )


async def test_manager_adds_a_pdf_and_it_opens_as_slides(site: Site, files: Files) -> None:
    async def body(browser: Any) -> None:
        library = Library(files)
        async with training_page(browser, site, library, wait="training-empty") as page:
            await page.get_by_test_id("training-add").click()
            dialog = page.get_by_test_id("training-dialog")
            await expect(dialog).to_be_visible()
            await dialog.get_by_test_id("training-file").set_input_files(
                {"name": "synthetic-induction.pdf", "mimeType": "application/pdf", "buffer": PDF}
            )
            await expect(dialog.get_by_test_id("training-title")).to_have_value(
                "synthetic-induction"
            )
            await dialog.get_by_test_id("training-title").fill("Synthetic safety induction")
            await dialog.get_by_test_id("training-category").fill("Onboarding")
            await dialog.get_by_test_id("training-dialog-save").click()
            await expect(page.get_by_test_id("training-message")).to_have_text(
                "Added Synthetic safety induction. Everyone in your company can open it now."
            )
            await expect(dialog).to_have_count(0)
            # Step one sent the file alone, under one idempotency key; step two the details.
            assert len(library.uploads) == 1, library.uploads
            upload = library.uploads[0]
            assert UUID.fullmatch(upload["key"] or ""), upload["key"]
            assert upload["type"].startswith("multipart/form-data"), upload["type"]
            assert upload["body"] and PDF in upload["body"], "the file part is not the PDF"
            assert library.patches == [
                {"title": "Synthetic safety induction", "category": "Onboarding"}
            ]
            row = page.get_by_test_id(f"training-item-{ADDED}")
            await expect(row).to_contain_text("Synthetic safety induction")

            async with page.expect_response(
                lambda response: FILE_ROUTE.fullmatch(urlsplit(response.url).path) is not None,
                timeout=WAIT,
            ) as answer:
                await open_item(page, ADDED)
            response = await answer.value
            # The console's rewrite passes the gateway's answer on as it is.
            assert response.status == 200
            headers = await response.all_headers()
            assert headers["content-type"] == "application/pdf", headers
            assert headers["content-disposition"].startswith("inline"), headers
            assert headers["cache-control"] == "private, no-store", headers
            assert "nosniff" in headers["x-content-type-options"], headers
            assert await response.body() == PDF
            assert files.reads == [ADDED]

            await expect(page.get_by_test_id("training-title-heading")).to_have_text(
                "Synthetic safety induction"
            )
            await expect(page.get_by_test_id("training-mode-slides")).to_have_attribute(
                "aria-pressed", "true"
            )
            await expect_slide(page, 1)
            await expect(page.get_by_test_id("training-fullscreen")).to_be_visible()
            await page.get_by_test_id("training-next").click()
            await expect_slide(page, 2)
            await page.get_by_test_id("training-thumb-3").click()
            await expect_slide(page, 3)
            await expect(page.get_by_test_id("training-thumb-3")).to_have_attribute(
                "aria-current", "true"
            )
            # A short PDF: in document mode every page is drawn, in order.
            await page.get_by_test_id("training-mode-document").click()
            canvases = page.get_by_test_id("training-page-canvas")
            await expect(canvases).to_have_count(len(COLOURS))
            seen: list[list[int]] = []

            async def all_drawn() -> bool:
                nonlocal seen
                seen = await canvases.evaluate_all(f"all => all.map({CENTRE})")
                return len(seen) == len(COLOURS) and all(
                    near(pixel, colour) for pixel, colour in zip(seen, COLOURS, strict=True)
                )

            await until(all_drawn, f"document pages not drawn in order: {seen}")
            await expect(page.get_by_test_id("training-zoom-in")).to_be_visible()
            # Nothing failed on the way, so nothing was written to the console as an error.
            assert not library.console_errors, library.console_errors

    await with_browser(body)


async def test_plain_staff_sees_the_list_but_no_manage_buttons(site: Site, files: Files) -> None:
    async def body(browser: Any) -> None:
        library = Library(files, can_manage=False)
        library.add(make_item(ITEM, "Synthetic fire drill", category="Safety"))
        async with training_page(browser, site, library, role="tenant_staff") as page:
            await expect(page.get_by_test_id(f"training-item-{ITEM}")).to_contain_text(
                "Synthetic fire drill"
            )
            for control in ("training-add", "training-edit", "training-remove"):
                await expect(page.get_by_test_id(control)).to_have_count(0)
            await open_item(page, ITEM)
            await expect_slide(page, 1)
            await expect(page.get_by_test_id("training-download")).to_be_visible()
            for control in ("training-edit", "training-remove"):
                await expect(page.get_by_test_id(control)).to_have_count(0)

    await with_browser(body)


async def test_read_only_makes_the_first_summary_and_sees_it_as_text(
    site: Site, files: Files
) -> None:
    async def body(browser: Any) -> None:
        library = Library(files, can_manage=False)
        library.next_summary = make_summary(MARKUP, "Point <i>one</i>")
        library.add(make_item(ITEM, "Synthetic fire drill"))
        async with training_page(browser, site, library, role="read_only") as page:
            for control in ("training-add", "training-edit", "training-remove"):
                await expect(page.get_by_test_id(control)).to_have_count(0)
            await open_item(page, ITEM)
            panel = page.get_by_test_id("training-summary-panel")
            await expect(panel.get_by_test_id("training-provider-notice")).to_have_text(
                PROVIDER_NOTICE
            )
            await panel.get_by_test_id("training-summarise").click()
            summary = panel.get_by_test_id("training-summary")
            await expect(summary).to_contain_text(MARKUP)
            await expect(summary.get_by_test_id("training-summary-points")).to_have_text(
                "Point <i>one</i>"
            )
            # The model's markup is text: no element was made from it and nothing ran.
            assert await summary.locator("b, i, img").count() == 0
            assert await page.evaluate("() => window.__owned") is None
            await expect(panel.get_by_test_id("training-summary-note")).to_have_text(
                PROVIDER_NOTICE
            )
            assert library.summary_posts == [{}]
            # Asking is open to every member; replacing it is for managers only.
            await expect(panel.get_by_test_id("training-regenerate")).to_have_count(0)
            await expect(panel.get_by_test_id("training-summarise")).to_have_count(0)

    await with_browser(body)


async def test_manager_reads_the_stored_summary_and_replaces_it(site: Site, files: Files) -> None:
    async def body(browser: Any) -> None:
        library = Library(files)
        library.add(make_item(ITEM, "Synthetic fire drill", has_summary=True))
        library.summaries[ITEM] = make_summary(MARKUP)
        library.next_summary = make_summary("A newer synthetic summary.")
        async with training_page(browser, site, library) as page:
            await open_item(page, ITEM)
            panel = page.get_by_test_id("training-summary-panel")
            await expect(panel.get_by_test_id("training-summary")).to_contain_text(MARKUP)
            assert await panel.get_by_test_id("training-summary").locator("b, img").count() == 0
            # Reading a stored summary never makes one.
            assert library.summary_posts == []
            await expect(panel.get_by_test_id("training-summarise")).to_have_count(0)
            await panel.get_by_test_id("training-regenerate").click()
            confirm = panel.get_by_test_id("training-regenerate-confirm-box")
            await expect(confirm).to_contain_text(
                "This replaces the summary everyone in your company sees."
            )
            await confirm.get_by_test_id("training-regenerate-confirm").click()
            await expect(panel.get_by_test_id("training-summary")).to_contain_text(
                "A newer synthetic summary."
            )
            assert library.summary_posts == [{"regenerate": True}]

    await with_browser(body)


async def test_damaged_file_shows_the_damaged_sentence(site: Site, files: Files) -> None:
    async def body(browser: Any) -> None:
        library = Library(files)
        # The stored object was replaced with junk after the upload was checked.
        library.add(make_item(ITEM, "Synthetic fire drill"), body=JUNK)
        async with training_page(browser, site, library) as page:
            await open_item(page, ITEM)
            await expect(page.get_by_test_id("training-viewer-error")).to_have_text(DAMAGED)
            await expect(page.get_by_test_id("training-page-canvas")).to_have_count(0)
            await expect(page.get_by_test_id("training-viewer-retry")).to_have_count(0)
            assert files.reads == [ITEM]

    await with_browser(body)


async def test_missing_file_shows_the_not_in_library_sentence(site: Site, files: Files) -> None:
    async def body(browser: Any) -> None:
        library = Library(files)
        # Listed, but the file route answers 404: it was removed after the list was read.
        library.add(make_item(ITEM, "Synthetic fire drill"), body=None)
        async with training_page(browser, site, library) as page:
            await open_item(page, ITEM)
            await expect(page.get_by_test_id("training-viewer-error")).to_have_text(GONE)
            await expect(page.get_by_test_id("training-page-canvas")).to_have_count(0)
            assert files.reads == [ITEM]

    await with_browser(body)


async def test_remove_asks_first_then_the_pdf_leaves_the_list(site: Site, files: Files) -> None:
    async def body(browser: Any) -> None:
        library = Library(files)
        library.add(make_item(ITEM, "Synthetic fire drill"))
        async with training_page(browser, site, library) as page:
            await page.get_by_role("button", name="Remove PDF: Synthetic fire drill").click()
            dialog = page.get_by_test_id("training-remove-dialog")
            await expect(dialog).to_contain_text("Synthetic fire drill")
            await expect(dialog).to_contain_text(
                "This removes the PDF for everyone in your company."
            )
            assert library.deletes == []
            await dialog.get_by_test_id("training-remove-confirm").click()
            await expect(page.get_by_test_id("training-message")).to_have_text(
                "Removed Synthetic fire drill from the library."
            )
            await expect(page.get_by_test_id("training-empty")).to_be_visible()
            assert library.deletes == [ITEM]

    await with_browser(body)


async def test_phone_width_has_no_horizontal_scroll(site: Site, files: Files) -> None:
    async def body(browser: Any) -> None:
        library = Library(files)
        library.add(
            make_item(
                ITEM,
                "Keselamatan-tempat-kerja-dan-prosedur-kecemasan-untuk-semua-kakitangan-2026",
                description="A synthetic deck with a long title that has no spaces in it.",
                category="Keselamatan dan kesihatan pekerjaan",
            )
        )
        async with training_page(browser, site, library, width=360, height=800) as page:
            await no_sideways_scroll(page, "list")
            await open_item(page, ITEM)
            await expect_slide(page, 1)
            await no_sideways_scroll(page, "viewer, slides")
            await page.get_by_test_id("training-mode-document").click()
            await expect(page.get_by_test_id("training-page-canvas")).to_have_count(len(COLOURS))
            await no_sideways_scroll(page, "viewer, document")

    await with_browser(body)


async def test_arrow_keys_change_the_slide_only_from_inside_the_viewer(
    site: Site, files: Files
) -> None:
    async def body(browser: Any) -> None:
        library = Library(files)
        library.add(make_item(ITEM, "Synthetic fire drill"))
        # Short enough that the page scrolls.
        async with training_page(browser, site, library, height=600) as page:
            await open_item(page, ITEM)
            await expect_slide(page, 1)
            # Focus on a link outside the viewer: the arrow scrolls the page, the deck stays.
            await page.get_by_test_id("training-back").focus()
            assert await scrolled(page) == 0
            await page.keyboard.press("ArrowDown")

            async def page_moved() -> bool:
                return await scrolled(page) > 0

            await until(page_moved, "ArrowDown outside the viewer did not scroll the page", 5)
            await expect_slide(page, 1)
            # Focus on the stage: the arrows change the slide and leave the page where it is.
            await page.get_by_role("group", name=re.compile(r"^Slides")).focus()
            before = await scrolled(page)
            await page.keyboard.press("ArrowRight")
            await expect_slide(page, 2)
            await page.keyboard.press("ArrowDown")
            await expect_slide(page, 3)
            assert await scrolled(page) == before

    await with_browser(body)
