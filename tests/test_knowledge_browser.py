"""Real browser acceptance for the calmer Knowledge base page.

The real Next.js shell runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds. Its layouts fetch `/auth/me` and
`/services/active` server-side, so the loopback stub from `test_services_browser` answers those;
every browser-side `/console/v1/*` call is answered by Playwright from `Stub`, which serves the
five lists (agents, files, documents, jobs, gaps), can fail any one of them, and records how
often each list was read so the tests can prove which refreshes ran.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Page, expect

from tests import test_services_redesign_browser as services
from tests.test_services_browser import WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
PACKAGE_SENTENCE = (
    "This area is not part of your organisation's active service packages. "
    "Ask your administrator to activate it in Services."
)
LISTS = ("agents", "files", "documents", "jobs", "gaps")
RESOURCES = {
    "agents": "agents",
    "kb/files": "files",
    "kb/documents": "documents",
    "kb/ingest-jobs": "jobs",
    "kb/gaps": "gaps",
}


def make_agent(active: bool = True) -> dict[str, Any]:
    return {
        "name": "kb_agent",
        "title": "Knowledge base",
        "capability": {
            "name": "kb_agent",
            "is_enabled": True,
            "client_enabled": active,
            "active": active,
            "capability_kind": "agent",
            "circuit_state": "closed",
            "fail_count": 0,
            "updated_at": "2026-11-01T00:00:00Z",
            "last_change": None,
        },
        "implementation": "local",
        "readiness": "available",
        "detail": "Model-compatible KB retrieval; no live TEI probe.",
        "required_system": False,
        "specialized": False,
        "can_view_specialized": True,
        "can_subscribe": False,
        "can_toggle": True,
        "embedding_model": "synthetic-embed-1",
        "compatible_chunks": 24,
        "incompatible_chunks": 0,
    }


def make_file(file_id: str, name: str, size: int, document_id: str | None) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": name,
        "media_type": "application/pdf",
        "size_bytes": size,
        "document_id": document_id,
        "created_at": "2026-11-02T01:00:00Z",
    }


def make_document(doc_id: str, title: str, file_name: str | None, chunks: int) -> dict[str, Any]:
    return {
        "id": doc_id,
        "title": title,
        "source_uri": None,
        "category": "Shop",
        "language": "en",
        "version": 1,
        "is_active": True,
        "file_name": file_name,
        "chunk_count": chunks,
        "kind": "faq" if file_name and file_name.endswith(".csv") else "doc",
        "embed_model": "synthetic-embed-1",
        "created_at": "2026-11-02T02:00:00Z",
        "updated_at": "2026-11-02T02:00:00Z",
    }


def make_job(
    job_id: str,
    status: str,
    file_name: str,
    file_id: str | None,
    *,
    attempts: int = 1,
    error: str | None = None,
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "source": "upload",
        "status": status,
        "files": [{"name": file_name, "file_id": file_id}],
        "attempts": attempts,
        "error": error,
        "meta": {"results": results or []},
        "created_at": "2026-11-02T03:00:00Z",
        "started_at": None,
        "finished_at": None,
    }


def default_files() -> list[dict[str, Any]]:
    return [
        make_file("file-hours", "opening-hours.pdf", 2_097_152, "doc-hours"),
        make_file("file-faq", "delivery-faq.csv", 4096, "doc-faq"),
        make_file("file-queued", "price-list.pdf", 10_240, None),
        make_file("file-running", "returns-policy.docx", 20_480, None),
        make_file("file-failed", "scanned-menu.pdf", 300, None),
        make_file("file-loose", "old-notes.pdf", 512, None),
    ]


def default_documents() -> list[dict[str, Any]]:
    return [
        make_document("doc-hours", "Opening hours", "opening-hours.pdf", 12),
        make_document("doc-faq", "Delivery questions", "delivery-faq.csv", 8),
    ]


def default_jobs() -> list[dict[str, Any]]:
    return [
        make_job("job-queued", "queued", "price-list.pdf", "file-queued", attempts=0),
        make_job("job-running", "running", "returns-policy.docx", "file-running"),
        make_job(
            "job-failed",
            "failed",
            "scanned-menu.pdf",
            "file-failed",
            attempts=3,
            error="Could not read any text in this PDF",
        ),
        make_job(
            "job-done",
            "done",
            "opening-hours.pdf",
            "file-hours",
            results=[
                {
                    "file": "opening-hours.pdf",
                    "status": "done",
                    "error": None,
                    "document_id": "doc-hours",
                    "chunks": 12,
                    "version": 1,
                }
            ],
        ),
    ]


def default_gaps() -> list[dict[str, Any]]:
    return [
        {
            "id": "gap-sunday",
            "query": "Do you deliver on Sundays?",
            "hits": 4,
            "first_seen": "2026-11-01T01:00:00Z",
            "last_seen": "2026-11-02T01:00:00Z",
            "resolved": False,
        },
        {
            "id": "gap-halal",
            "query": "Is the chicken halal?",
            "hits": 2,
            "first_seen": "2026-11-01T02:00:00Z",
            "last_seen": "2026-11-02T00:00:00Z",
            "resolved": False,
        },
    ]


@dataclass
class Stub:
    """Browser-side answers. `fail` maps a list name to the status and body it answers with."""

    files: list[dict[str, Any]] = field(default_factory=default_files)
    documents: list[dict[str, Any]] = field(default_factory=default_documents)
    jobs: list[dict[str, Any]] = field(default_factory=default_jobs)
    gaps: list[dict[str, Any]] = field(default_factory=default_gaps)
    agent_active: bool = True
    agent_missing: bool = False
    upload_failures: int = 0
    fail: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    reads: dict[str, int] = field(default_factory=lambda: {name: 0 for name in LISTS})
    deletes: list[str] = field(default_factory=list)
    posts: list[str] = field(default_factory=list)
    upload_keys: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        parts = urlsplit("http://x/" + request.url.split("/console/v1/")[1])
        path = parts.path.lstrip("/")
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path in RESOURCES and request.method == "GET":
            name = RESOURCES[path]
            self.reads[name] += 1
            if name in self.fail:
                status, data = self.fail[name]
            else:
                data = {
                    "agents": lambda: [] if self.agent_missing else [make_agent(self.agent_active)],
                    "files": lambda: self.files,
                    "documents": lambda: self.documents,
                    "jobs": lambda: self.jobs,
                    "gaps": lambda: self.gaps,
                }[name]()
        elif path == "kb/uploads" and request.method == "POST":
            self.upload_keys.append(request.headers.get("idempotency-key", ""))
            if self.upload_failures:
                self.upload_failures -= 1
                await request_route.fulfill(status=502, json={"detail": "synthetic upload failure"})
                return
            self.jobs.insert(0, make_job("job-new", "queued", "new-upload.pdf", None, attempts=0))
            status, data = 202, {"job_id": "job-new", "duplicate_request": False}
        elif request.method == "DELETE" and re.fullmatch(r"kb/(files|documents)/[\w-]+", path):
            self.deletes.append(path)
            kind, item = path.split("/")[1:]
            if kind == "files":
                self.files = [row for row in self.files if row["id"] != item]
            else:
                # Deleting a document removes its linked sources too, as the real route does.
                self.documents = [row for row in self.documents if row["id"] != item]
                self.files = [row for row in self.files if row["document_id"] != item]
            status, data = 204, None
        elif request.method == "POST" and re.fullmatch(r"kb/gaps/[\w-]+/resolve", path):
            self.posts.append(path)
            item = path.split("/")[2]
            self.gaps = [row for row in self.gaps if row["id"] != item]
            data = {"status": "resolved", "resolved_id": item}
        elif path == "kb/storage/reconcile" and request.method == "POST":
            self.posts.append(path)
            data = {"removed_files": 2}
        else:
            self.unexpected.append(f"{request.method} {path}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        try:
            if data is None:
                await request_route.fulfill(status=status)
            else:
                await request_route.fulfill(status=status, json=data)
        except Exception:
            pass


@asynccontextmanager
async def knowledge_page(
    browser: Any,
    site: Site,
    stub: Stub,
    *,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    role: str = "tenant_admin",
    wait: str | None = "kb-library",
) -> AsyncIterator[Page]:
    site.upstream.me = make_me(role)
    site.upstream.packages = [{"key": "customer_support", "status": "active"}]
    context = await browser.new_context(viewport={"width": width, "height": height})
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", stub.route)
    await page.goto(f"{site.base}/portal/faq")
    if wait:
        await expect(page.get_by_test_id(wait)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not stub.unexpected, stub.unexpected
    finally:
        await context.close()


async def until(check: Any, message: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, message
        await asyncio.sleep(0.1)


def card(page: Page, item: str) -> Any:
    return page.get_by_test_id(f"kb-card-{item}")


async def open_card(page: Page, item: str) -> Any:
    await page.get_by_test_id(f"kb-card-{item}-toggle").click()
    details = page.get_by_test_id(f"kb-card-{item}-details")
    await expect(details).to_be_visible()
    return details


async def open_fold(page: Page, name: str) -> Any:
    await page.get_by_test_id(f"{name}-toggle").click()
    details = page.get_by_test_id(f"{name}-details")
    await expect(details).to_be_visible()
    return details


async def test_one_failing_list_does_not_blank_the_page(site: Site) -> None:
    async def body(browser: Any) -> None:
        # No file is being read, so the 5-second refresh is off and the read counts stay exact.
        stub = Stub(jobs=[job for job in default_jobs() if job["status"] in ("failed", "done")])
        stub.fail["documents"] = (503, {"detail": "synthetic documents outage"})
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            # Only the library says it failed; the rest of the page is on screen and usable.
            error = page.get_by_test_id("kb-error-documents")
            await expect(error).to_be_visible()
            await expect(error).to_contain_text("synthetic documents outage")
            await expect(page.get_by_test_id("kb-add")).to_be_visible()
            await expect(card(page, "job-failed")).to_be_visible()
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
            for other in ("agents", "files", "jobs", "gaps"):
                await expect(page.get_by_test_id(f"kb-error-{other}")).to_have_count(0)
            # A failed load is never shown as an empty library.
            await expect(page.get_by_text("No ingested documents.")).to_have_count(0)
            await expect(card(page, "doc-hours")).to_have_count(0)

            # Retry reads only that list.
            before = dict(stub.reads)
            del stub.fail["documents"]
            await page.get_by_test_id("kb-retry-documents").click()
            await expect(card(page, "doc-hours")).to_be_visible()
            await expect(error).to_have_count(0)
            assert stub.reads["documents"] == before["documents"] + 1
            for other in ("agents", "files", "jobs", "gaps"):
                assert stub.reads[other] == before[other], (other, stub.reads, before)

            # A list failing later keeps what is on screen and says so quietly.
            stub.fail["gaps"] = (500, {})
            await page.get_by_role("button", name="Refresh knowledge base").click()
            gaps_error = page.get_by_test_id("kb-error-gaps")
            await expect(gaps_error).to_be_visible()
            await expect(gaps_error).to_contain_text("Something went wrong on our side. Try again.")
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
            await expect(card(page, "doc-hours")).to_be_visible()

    await with_browser(body)


async def test_refresh_stops_when_nothing_is_reading(site: Site) -> None:
    async def body(browser: Any) -> None:
        # Nothing queued or running: the page loads once and never polls.
        quiet = Stub(jobs=[job for job in default_jobs() if job["status"] in ("failed", "done")])
        async with knowledge_page(browser, site, quiet) as page:
            await expect(page.get_by_test_id("kb-reading")).to_have_count(0)
            await expect(card(page, "job-failed")).to_be_visible()
            reads = dict(quiet.reads)
            await asyncio.sleep(6.5)
            assert quiet.reads == reads, (quiet.reads, reads)

        # A job is being read: the page polls every 5 seconds, then stops once it is done.
        stub = Stub()
        async with knowledge_page(browser, site, stub) as page:
            await expect(card(page, "job-running")).to_be_visible()
            await expect(
                card(page, "job-running").get_by_test_id("kb-card-job-running-chip")
            ).to_have_text("Reading")
            start = dict(stub.reads)
            await until(
                lambda: stub.reads["jobs"] > start["jobs"], "no poll while a job is being read"
            )
            await until(
                lambda: stub.reads["documents"] > start["documents"], "documents not refreshed"
            )
            # The refresh covers the sources only: assistant details and questions stay as loaded.
            assert (
                stub.reads["agents"] == start["agents"] and stub.reads["gaps"] == start["gaps"]
            ), stub.reads

            for job in stub.jobs:
                if job["status"] in ("queued", "running"):
                    job["status"] = "done"
            seen = stub.reads["jobs"]
            await until(lambda: stub.reads["jobs"] > seen, "poll did not pick up the finished jobs")
            await expect(page.get_by_test_id("kb-reading")).to_have_count(0)
            settled = dict(stub.reads)
            await asyncio.sleep(6.5)
            assert stub.reads == settled, (stub.reads, settled)

    await with_browser(body)


async def test_cards_open_details_with_original_actions(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(browser, site, stub) as page:
            # Plain summaries first: title, type, when and a chip; technical text is behind it.
            hours = card(page, "doc-hours")
            await expect(hours).to_contain_text("Opening hours")
            await expect(hours).to_contain_text("PDF")
            await expect(hours).to_contain_text("2 Nov 2026")
            await expect(page.get_by_test_id("kb-card-doc-hours-chip")).to_have_text("Ready")
            await expect(page.get_by_test_id("kb-card-job-failed-chip")).to_have_text(
                "Needs attention"
            )
            await expect(page.get_by_test_id("kb-card-job-queued-chip")).to_have_text("Reading")
            await expect(
                page.get_by_test_id("kb-library").get_by_role("heading", name="In your library")
            ).to_be_visible()
            await expect(page.get_by_role("heading", name="Being read", exact=True)).to_be_visible()
            await expect(
                page.get_by_role("heading", name="Needs attention", exact=True)
            ).to_be_visible()
            await expect(page.get_by_test_id("kb-card-doc-hours-details")).to_be_hidden()
            await expect(page.get_by_role("link", name="Download original")).to_have_count(0)

            details = await open_card(page, "doc-hours")
            await expect(details).to_contain_text("2 MB")
            await expect(details).to_contain_text("12")
            await expect(details).to_contain_text(
                "Version 1, active, 12 chunks. Model: synthetic-embed-1. Shop / en / doc."
            )
            await expect(details).to_contain_text("Source: opening-hours.pdf")
            await expect(details.get_by_text("Original sources", exact=True)).to_be_visible()
            await expect(details).to_contain_text(
                "application/pdf, 2,097,152 bytes. Document doc-hours"
            )
            link = details.get_by_role("link", name="Download original")
            await expect(link).to_have_attribute(
                "href", re.compile(r"/console/v1/kb/files/file-hours/download")
            )

            # Delete source asks first, then deletes and refreshes the lists.
            await details.get_by_role("button", name="Delete source").click()
            confirm = page.get_by_test_id("kb-confirm")
            await expect(confirm).to_contain_text(
                "Delete opening-hours.pdf? Deleting a source with a linked document"
            )
            await confirm.get_by_role("button", name="Cancel").click()
            await expect(confirm).to_have_count(0)
            assert stub.deletes == []
            await details.get_by_role("button", name="Delete document").click()
            await expect(confirm).to_contain_text("Delete Opening hours?")
            await confirm.get_by_role("button", name="Confirm delete").click()
            await expect(card(page, "doc-hours")).to_have_count(0)
            assert stub.deletes == ["kb/documents/doc-hours"]
            await expect(
                page.get_by_text(
                    "Deleted. Queued work cannot recreate this source or its deleted content."
                )
            ).to_be_visible()

            # A failed job: the error and the original job line sit behind its arrow.
            failed = await open_card(page, "job-failed")
            await expect(failed).to_contain_text("job-failed: failed (attempts: 3)")
            await expect(failed).to_contain_text("Could not read any text in this PDF")
            await expect(failed.get_by_role("link", name="Download original")).to_have_attribute(
                "href", re.compile(r"/kb/files/file-failed/download")
            )
            await expect(failed.get_by_role("button", name="Delete source")).to_be_enabled()
            # The retry: add the file again with the upload control.
            await (
                card(page, "job-failed").get_by_role("button", name="Choose the file again").click()
            )
            await expect(page.locator("#kb-files")).to_be_focused()

            # Files no card shows still have their download and delete.
            loose = await open_fold(page, "kb-other-files")
            await expect(loose).to_contain_text("old-notes.pdf")
            await expect(loose.get_by_role("link", name="Download original")).to_have_count(1)

    await with_browser(body)


async def test_maintenance_disclosure_holds_reconcile_and_embedding_panel(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(browser, site, stub) as page:
            maintenance = page.get_by_test_id("kb-maintenance-details")
            await expect(maintenance).to_be_hidden()
            for text in (
                "Reconcile orphaned storage",
                "Embedding compatibility",
                "Polling every 5 seconds",
            ):
                await expect(page.get_by_text(text, exact=False).first).to_be_hidden()
            await expect(page.get_by_test_id("kb-maintenance")).to_contain_text("Maintenance")

            await open_fold(page, "kb-maintenance")
            await expect(
                maintenance.get_by_role("heading", name="Embedding compatibility")
            ).to_be_visible()
            await expect(maintenance).to_contain_text(
                "Model-compatible KB retrieval; no live TEI probe."
            )
            await expect(maintenance).to_contain_text(
                "Model: synthetic-embed-1. Compatible chunks: 24. Incompatible chunks: 0. "
                "No legacy FAQ files are imported automatically."
            )
            await expect(maintenance).to_contain_text(
                "Polling every 5 seconds while a file is being read. Lists show the latest 50 "
                "documents/gaps, 20 jobs and 100 sources."
            )
            await expect(maintenance).not_to_contain_text("Writes require both KB subscription")
            await maintenance.get_by_role("button", name="Reconcile orphaned storage").click()
            await expect(
                page.get_by_text(
                    "Storage reconciled: 2 orphaned/deleted/partial files removed. "
                    "Active sources are retained."
                )
            ).to_be_visible()
            assert stub.posts == ["kb/storage/reconcile"]

            # The settings fold says whether the knowledge assistant is on.
            settings = await open_fold(page, "kb-settings")
            await expect(settings).to_contain_text("The knowledge assistant is on.")

        # Switched off: uploads are disabled and the original sentence sits in Maintenance.
        off = Stub(agent_active=False)
        async with knowledge_page(browser, site, off) as page:
            await expect(page.locator("#kb-files")).to_be_disabled()
            await expect(page.get_by_test_id("kb-add")).to_contain_text("switched off")
            # The failed card says why choosing the file again is paused, with the same reason.
            paused = page.get_by_test_id("kb-card-job-failed-paused")
            await expect(paused).to_contain_text("switched off")
            await expect(
                card(page, "job-failed").get_by_role("button", name="Choose the file again")
            ).to_be_disabled()
            maintenance = await open_fold(page, "kb-maintenance")
            await expect(maintenance).to_contain_text(
                "Writes require both KB subscription and tenant switch on."
            )
            settings = await open_fold(page, "kb-settings")
            await expect(settings).to_contain_text("The knowledge assistant is off.")

        # Read-only members see the lists and downloads, never the controls.
        async with knowledge_page(browser, site, Stub(), role="read_only") as page:
            await expect(page.get_by_test_id("kb-add")).to_have_count(0)
            await open_card(page, "doc-hours")
            await expect(page.get_by_role("link", name="Download original")).to_have_count(1)
            await expect(page.get_by_role("button", name="Delete source")).to_have_count(0)
            await expect(page.get_by_role("button", name="Delete document")).to_have_count(0)
            await expect(page.get_by_role("button", name="Mark resolved")).to_have_count(0)
            maintenance = await open_fold(page, "kb-maintenance")
            await expect(maintenance).to_contain_text(
                "Read-only access: upload, deletion and gap resolution are unavailable."
            )
            await expect(
                maintenance.get_by_role("button", name="Reconcile orphaned storage")
            ).to_have_count(0)

    await with_browser(body)


async def test_package_inactive_sentence_unchanged(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        for name in ("documents", "jobs", "gaps"):
            stub.fail[name] = (403, {"detail": "package_inactive"})
        async with knowledge_page(browser, site, stub, wait="kb-locked") as page:
            locked = page.get_by_test_id("kb-locked")
            await expect(locked).to_have_text(PACKAGE_SENTENCE)
            await expect(page.get_by_text(PACKAGE_SENTENCE)).to_have_count(1)
            await expect(page.get_by_role("heading", name="Knowledge base")).to_be_visible()
            await expect(page.get_by_test_id("kb-add")).to_have_count(0)

    await with_browser(body)


async def test_upload_queues_a_file_and_shows_it_as_reading(site: Site, tmp_path: Any) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        sample = tmp_path / "new-upload.pdf"
        sample.write_bytes(b"%PDF-1.4 synthetic")
        async with knowledge_page(browser, site, stub) as page:
            add = page.get_by_test_id("kb-add")
            steps = add.get_by_role("list").locator("li")
            await expect(steps).to_have_text(
                ["Add a file", "Your assistant reads it", "It answers from it"]
            )
            await expect(add).to_contain_text(
                "One to three PDF, DOCX or CSV files, at most 10 MB each"
            )
            queue = add.get_by_role("button", name="Queue upload / retry")
            await expect(queue).to_be_disabled()
            await page.locator("#kb-files").set_input_files(str(sample))
            await expect(add).to_contain_text("new-upload.pdf")
            await queue.click()
            await expect(
                page.get_by_text("Queued job job-new. Queued is not yet searchable.")
            ).to_be_visible()
            await expect(card(page, "job-new")).to_be_visible()
            await expect(page.get_by_test_id("kb-card-job-new-chip")).to_have_text("Reading")
            assert len(stub.upload_keys) == 1 and stub.upload_keys[0]

    await with_browser(body)


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


@pytest.mark.parametrize("theme", THEMES)
async def test_knowledge_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        stub.fail["gaps"] = (503, {"detail": "synthetic gaps outage"})
        async with knowledge_page(browser, site, stub, theme=theme) as page:
            await open_card(page, "doc-hours")
            await open_card(page, "job-failed")
            await open_fold(page, "kb-maintenance")
            await open_fold(page, "kb-settings")
            await open_fold(page, "kb-other-files")
            await expect(page.get_by_test_id("kb-error-gaps")).to_be_visible()
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await expect(card(page, "doc-hours")).to_have_attribute("data-open", "true")
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
                outside = await page.evaluate(
                    """() => [...document.querySelectorAll('[data-testid^="kb-"]')].filter(
                        e => e.getBoundingClientRect().right > window.innerWidth + 0.5
                    ).map(e => e.dataset.testid)"""
                )
                assert not outside, (theme, width, outside)

    await with_browser(body)


async def test_first_screen_at_390_leads_with_adding_knowledge(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(browser, site, Stub(), width=390, height=844) as page:
            await expect(page.get_by_role("heading", name="Knowledge base")).to_be_in_viewport()
            await expect(page.get_by_test_id("kb-add")).to_be_in_viewport()
            await expect(page.locator("#kb-files")).to_be_in_viewport()
            # No technical block above the first card: those sit behind Maintenance and Settings.
            for text in (
                "Embedding compatibility",
                "Reconcile orphaned storage",
                "Polling every 5 seconds",
            ):
                await expect(page.get_by_text(text, exact=False).first).to_be_hidden()
            order = await page.evaluate(
                """() => ['kb-add', 'kb-library', 'kb-gaps', 'kb-maintenance', 'kb-settings'].map(
                    id => document.querySelector(`[data-testid="${id}"]`)
                        .getBoundingClientRect().top
                )"""
            )
            assert order == sorted(order), order

    await with_browser(body)


async def test_missing_assistant_row_keeps_the_old_sentence_and_pauses_writes(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(agent_missing=True)
        async with knowledge_page(browser, site, stub) as page:
            await expect(page.locator("#kb-files")).to_be_disabled()
            await expect(page.get_by_test_id("kb-add-unknown")).to_contain_text("not available")
            # The lists still load: only the writes wait.
            await expect(card(page, "doc-hours")).to_be_visible()
            await expect(page.get_by_test_id("kb-error-agents")).to_have_count(0)
            maintenance = await open_fold(page, "kb-maintenance")
            await expect(maintenance).to_contain_text("KB capability metadata is unavailable.")
            await expect(maintenance).not_to_contain_text("We could not load the assistant details")
            await expect(page.get_by_role("button", name="Mark resolved").first).to_be_disabled()

    await with_browser(body)


async def test_figures_say_unknown_when_their_list_failed(site: Site) -> None:
    async def body(browser: Any) -> None:
        # No file is being read, so nothing polls and the failures stay put.
        jobs = [job for job in default_jobs() if job["status"] in ("failed", "done")]
        stub = Stub(jobs=jobs)
        stub.fail["files"] = (503, {"detail": "synthetic files outage"})
        async with knowledge_page(browser, site, stub) as page:
            details = await open_card(page, "doc-hours")
            await expect(details).to_contain_text("Size")
            await expect(details).to_contain_text("Unknown")
            await expect(details).not_to_contain_text("Not stored")
            failed = await open_card(page, "job-failed")
            await expect(failed).to_contain_text("Unknown")
            await expect(failed).not_to_contain_text("Not stored")
            await expect(page.get_by_test_id("kb-error-files")).to_be_visible()

        stub = Stub()
        stub.jobs = [job for job in default_jobs() if job["status"] in ("failed", "done")]
        async with knowledge_page(browser, site, stub) as page:
            # Loaded, nothing matches: the original words stay.
            stub.jobs = []
            await page.get_by_role("button", name="Refresh knowledge base").click()
            await expect(card(page, "job-failed")).to_have_count(0)
            details = await open_card(page, "doc-hours")
            await expect(details).to_contain_text("Not recorded")
            stub.fail["jobs"] = (503, {"detail": "synthetic jobs outage"})
            await page.get_by_role("button", name="Refresh knowledge base").click()
            await expect(page.get_by_test_id("kb-error-jobs")).to_be_visible()
            # The rows already loaded keep their loaded figures.
            await expect(details).to_contain_text("Not recorded")

        stub = Stub()
        stub.fail["jobs"] = (503, {"detail": "synthetic jobs outage"})
        async with knowledge_page(browser, site, stub) as page:
            details = await open_card(page, "doc-hours")
            await expect(details).to_contain_text("Unknown")
            await expect(details).not_to_contain_text("Not recorded")

    await with_browser(body)


async def test_mark_resolved_posts_and_the_question_leaves_the_list(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(browser, site, stub) as page:
            row = page.get_by_test_id("kb-gap-gap-sunday")
            await expect(row).to_contain_text("Do you deliver on Sundays?")
            await expect(row).to_contain_text("4 hits. Last seen:")
            await row.get_by_role("button", name="Mark resolved").click()
            await expect(row).to_have_count(0)
            assert stub.posts == ["kb/gaps/gap-sunday/resolve"], stub.posts
            await expect(page.get_by_test_id("kb-gap-gap-halal")).to_be_visible()

    await with_browser(body)


async def test_upload_failure_keeps_the_retry_message_and_the_same_key(
    site: Site, tmp_path: Any
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(upload_failures=1)
        sample = tmp_path / "retry-me.pdf"
        sample.write_bytes(b"%PDF-1.4 synthetic")
        async with knowledge_page(browser, site, stub) as page:
            await page.locator("#kb-files").set_input_files(str(sample))
            queue = page.get_by_role("button", name="Queue upload / retry")
            await queue.click()
            await expect(
                page.get_by_text(
                    "synthetic upload failure. Retry keeps the same idempotency key; "
                    "do not reselect files for an uncertain result."
                )
            ).to_be_visible()
            # The chosen file stays chosen and the retry sends the same key.
            await expect(page.get_by_test_id("kb-add")).to_contain_text("retry-me.pdf")
            await queue.click()
            await expect(card(page, "job-new")).to_be_visible()
            assert len(stub.upload_keys) == 2 and stub.upload_keys[0] == stub.upload_keys[1]

    await with_browser(body)
