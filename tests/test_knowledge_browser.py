"""Real browser acceptance for the Business knowledge pages (library, add, questions, maintenance).

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
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import FilePayload, Page, expect

from tests import test_services_redesign_browser as services
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
SELECTION_MESSAGE = "Select one to three non-empty PDF, DOCX or CSV files, at most 10 MB each."
QUEUED_TEXT = "Queued job job-new. Queued is not yet searchable."
PACKAGE_SENTENCE = (
    "This area is not part of your organisation's active service packages. "
    "Ask your administrator to activate it in Services."
)
LISTS = ("agents", "files", "documents", "jobs", "gaps")
# The four pages of Business knowledge: (section key, portal path, the testid that shows it loaded).
PAGES = (
    ("library", "/portal/faq", "kb-library"),
    ("add", "/portal/faq/add", "kb-add"),
    ("questions", "/portal/faq/questions", "kb-gaps"),
    ("maintenance", "/portal/faq/maintenance", "kb-maintenance"),
)
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
    original_stored: bool = True,
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
        "original_stored": original_stored,
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
        make_job("job-running", "processing", "returns-policy.docx", "file-running"),
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


def gone_job() -> dict[str, Any]:
    """A failed upload whose stored original is gone: it cannot be tried again."""
    return make_job(
        "job-failed",
        "failed",
        "scanned-menu.pdf",
        "file-failed",
        attempts=3,
        error="Could not read any text in this PDF",
        original_stored=False,
    )


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
    answer_failures: int = 0
    answer_delay: float = 0.0
    answers: list[dict[str, Any]] = field(default_factory=list)
    fail: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    reads: dict[str, int] = field(default_factory=lambda: {name: 0 for name in LISTS})
    storage_cleanup: str = "done"
    deletes: list[str] = field(default_factory=list)
    posts: list[str] = field(default_factory=list)
    upload_keys: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    queries: dict[str, list[dict[str, list[str]]]] = field(default_factory=dict)

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        parts = urlsplit("http://x/" + request.url.split("/console/v1/")[1])
        path = parts.path.lstrip("/")
        status, data = 200, None
        if path == "hr/context" and request.method == "GET":
            # The rail asks for the HR tabs on pages that name a tenant; unused here.
            await request_route.fulfill(status=404, json={"detail": "not stubbed"})
            return
        self.queries.setdefault(path, []).append(parse_qs(parts.query))
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "admin/clients" and request.method == "GET":
            data = [TENANT_PROFILE]
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
        elif path == "kb/documents" and request.method == "POST":
            self.answers.append(request.post_data_json)
            await asyncio.sleep(self.answer_delay)
            if self.answer_failures:
                self.answer_failures -= 1
                await request_route.fulfill(status=503, json={"detail": "synthetic answer failure"})
                return
            status, data = 200, {"status": "ingested"}
        elif request.method == "DELETE" and re.fullmatch(r"kb/(files|documents)/[\w-]+", path):
            self.deletes.append(path)
            kind, item = path.split("/")[1:]
            if kind == "files":
                self.files = [row for row in self.files if row["id"] != item]
            else:
                # Deleting a document removes its linked sources too, as the real route does.
                self.documents = [row for row in self.documents if row["id"] != item]
                self.files = [row for row in self.files if row["document_id"] != item]
            # The real routes close every job whose files are all deleted; those are not listed.
            live = {row["id"] for row in self.files}
            self.jobs = [
                job
                for job in self.jobs
                if any(
                    entry["file_id"] is None or entry["file_id"] in live for entry in job["files"]
                )
            ]
            status, data = 200, {"storage_cleanup": self.storage_cleanup}
        elif request.method == "POST" and path == "kb/ingest-jobs/dismiss-failed":
            self.posts.append(path)
            ended = ("failed", "partial", "cancelled")
            count = sum(job["status"] in ended for job in self.jobs)
            self.jobs = [job for job in self.jobs if job["status"] not in ended]
            data = {"dismissed": count}
        elif request.method == "POST" and re.fullmatch(
            r"kb/ingest-jobs/[\w-]+/(retry|dismiss|cancel)", path
        ):
            self.posts.append(path)
            _, _, item, verb = path.split("/")
            job = next((row for row in self.jobs if row["id"] == item), None)
            if job is None:
                await request_route.fulfill(status=404, json={"detail": "Job not found"})
                return
            if verb == "retry":
                if job["status"] not in ("failed", "partial") or not job["original_stored"]:
                    await request_route.fulfill(status=409, json={"detail": "original_removed"})
                    return
                # No worker has run yet: the old results stay on the row, as in the real route.
                job.update(status="queued", attempts=0, error=None)
                data = job
            elif verb == "cancel":
                if job["status"] != "queued":
                    await request_route.fulfill(status=409, json={"detail": "not queued"})
                    return
                self.jobs = [row for row in self.jobs if row["id"] != item]
                data = {**job, "status": "cancelled"}
            else:
                if job["status"] in ("queued", "processing"):
                    await request_route.fulfill(status=409, json={"detail": "still running"})
                    return
                self.jobs = [row for row in self.jobs if row["id"] != item]
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
    path: str = "/portal/faq",
    rail_expanded: bool = False,
) -> AsyncIterator[Page]:
    site.upstream.me = make_me(role)
    site.upstream.packages = [
        {"key": "customer_support", "status": "active"},
        {"key": "knowledge_base", "status": "active"},
    ]
    context = await browser.new_context(viewport={"width": width, "height": height})
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if rail_expanded:
        cookies.append({"name": "fs-rail", "value": "expanded", "url": site.base})
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", stub.route)
    await page.goto(f"{site.base}{path}")
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
    """Open a card and its Technical details; returns the technical region."""
    await open_plain(page, item)
    await page.get_by_test_id(f"kb-card-{item}-tech-toggle").click()
    details = page.get_by_test_id(f"kb-card-{item}-tech-details")
    await expect(details).to_be_visible()
    return details


async def open_plain(page: Page, item: str) -> Any:
    """Open a card's Details (the plain level only)."""
    if await page.get_by_test_id(f"kb-card-{item}-toggle").get_attribute("aria-expanded") != "true":
        await page.get_by_test_id(f"kb-card-{item}-toggle").click()
    details = page.get_by_test_id(f"kb-card-{item}-details")
    await expect(details).to_be_visible()
    return details


async def open_progress_tech(page: Page, job_id: str) -> Any:
    """Open the Technical details of one upload row on the Add page."""
    await page.get_by_test_id(f"kb-progress-{job_id}-tech-toggle").click()
    details = page.get_by_test_id(f"kb-progress-{job_id}-tech-details")
    await expect(details).to_be_visible()
    return details


DROP_SCRIPT = """(names) => {
    const transfer = new DataTransfer();
    for (const [name, size] of names) {
        transfer.items.add(new File([new Uint8Array(size)], name));
    }
    const target = document.querySelector('[data-testid="kb-drop"]');
    const init = {dataTransfer: transfer, bubbles: true, cancelable: true};
    target.dispatchEvent(new DragEvent('drop', init));
}"""


async def add_ready(page: Page) -> None:
    """Wait until the Add page is hydrated and adding is on (the drop area takes files)."""
    await expect(page.get_by_role("button", name="Choose files")).to_be_enabled(timeout=WAIT)


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
            await expect(card(page, "job-failed")).to_be_visible()
            for other in ("agents", "files", "jobs", "gaps"):
                await expect(page.get_by_test_id(f"kb-error-{other}")).to_have_count(0)
            # A failed load is never shown as an empty library.
            await expect(page.get_by_text("No files yet.")).to_have_count(0)
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
            stub.fail["files"] = (500, {})
            await page.get_by_role("button", name="Refresh business knowledge").click()
            files_error = page.get_by_test_id("kb-error-files")
            await expect(files_error).to_be_visible()
            await expect(files_error).to_contain_text(
                "Something went wrong on our side. Try again."
            )
            await expect(card(page, "doc-hours")).to_be_visible()

        # On the Add page a failing jobs list leaves the upload area where it is.
        failing = Stub()
        failing.fail["jobs"] = (503, {"detail": "synthetic jobs outage"})
        async with knowledge_page(
            browser, site, failing, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await expect(page.get_by_test_id("kb-error-jobs")).to_contain_text(
                "synthetic jobs outage"
            )
            await expect(page.get_by_test_id("kb-add")).to_be_visible()
            await expect(page.locator("#kb-files")).to_be_enabled()

        # On the Questions page a failing gaps list says so and keeps the page.
        gaps_failing = Stub()
        gaps_failing.fail["gaps"] = (503, {"detail": "synthetic gaps outage"})
        async with knowledge_page(
            browser, site, gaps_failing, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            await expect(page.get_by_test_id("kb-error-gaps")).to_contain_text(
                "synthetic gaps outage"
            )
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_have_count(0)
            await expect(page.get_by_text("No unanswered questions right now.")).to_have_count(0)

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
                if job["status"] in ("queued", "processing"):
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
            # The file actions sit at the first Details level, not inside Technical details.
            actions = page.get_by_test_id("kb-card-doc-hours-actions")
            await expect(details.get_by_role("link", name="Download original")).to_have_count(0)
            link = actions.get_by_role("link", name="Download original")
            await expect(link).to_have_attribute(
                "href", re.compile(r"/console/v1/kb/files/file-hours/download")
            )

            # Delete source asks first, then deletes and refreshes the lists.
            await actions.get_by_role("button", name="Delete source").click()
            confirm = page.get_by_test_id("kb-confirm")
            await expect(confirm).to_contain_text(
                "Delete opening-hours.pdf? Deleting a source with a linked document"
            )
            await confirm.get_by_role("button", name="Cancel").click()
            await expect(confirm).to_have_count(0)
            assert stub.deletes == []
            await actions.get_by_role("button", name="Delete document").click()
            await expect(confirm).to_contain_text("Delete Opening hours?")
            await confirm.get_by_role("button", name="Confirm delete").click()
            await expect(card(page, "doc-hours")).to_have_count(0)
            assert stub.deletes == ["kb/documents/doc-hours"]
            await expect(
                page.get_by_text("Deleted. Your assistant no longer uses this file.")
            ).to_be_visible()

            # A failed job: the error and the original job line sit behind its arrow.
            await expect(card(page, "job-failed")).to_contain_text(
                "We couldn't read this file. You can try again or remove it."
            )
            failed = await open_card(page, "job-failed")
            await expect(failed).to_contain_text("job-failed: failed (attempts: 3)")
            await expect(failed).to_contain_text("Could not read any text in this PDF")
            failed_actions = page.get_by_test_id("kb-card-job-failed-actions")
            await expect(
                failed_actions.get_by_role("link", name="Download original")
            ).to_have_attribute("href", re.compile(r"/kb/files/file-failed/download"))
            await expect(failed_actions.get_by_role("button", name="Delete source")).to_be_enabled()
            loose_toggle = page.get_by_test_id("kb-other-files-toggle")
            await loose_toggle.click()

            # Files no card shows still have their download and delete.
            loose = page.get_by_test_id("kb-other-files-details")
            await expect(loose).to_be_visible()
            await expect(loose).to_contain_text("old-notes.pdf")
            await expect(loose).to_contain_text("PDF, 512 bytes. Not read yet")
            await expect(loose.get_by_role("link", name="Download original")).to_have_count(1)

    await with_browser(body)


async def test_maintenance_disclosure_holds_reconcile_and_embedding_panel(site: Site) -> None:
    maintenance_path = "/portal/faq/maintenance"

    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(
            browser, site, stub, path=maintenance_path, wait="kb-maintenance"
        ) as page:
            # Both disclosures are open on arrival: this page is for them.
            maintenance = page.get_by_test_id("kb-maintenance-details")
            await expect(maintenance).to_be_visible()
            await expect(page.get_by_test_id("kb-maintenance-toggle")).to_have_attribute(
                "aria-expanded", "true"
            )
            await expect(page.get_by_test_id("kb-settings-toggle")).to_have_attribute(
                "aria-expanded", "true"
            )
            await expect(page.get_by_test_id("kb-maintenance")).to_contain_text("Maintenance")
            await expect(maintenance).to_contain_text(
                "This page updates by itself while a file is being read."
            )
            # The technical text is word for word, behind Technical details.
            tech = page.get_by_test_id("kb-maintenance-tech-details")
            await expect(tech).to_be_hidden()
            await page.get_by_test_id("kb-maintenance-tech-toggle").click()
            await expect(tech).to_be_visible()
            await expect(
                tech.get_by_role("heading", name="Embedding compatibility")
            ).to_be_visible()
            await expect(tech).to_contain_text("Model-compatible KB retrieval; no live TEI probe.")
            await expect(tech).to_contain_text(
                "Model: synthetic-embed-1. Compatible chunks: 24. Incompatible chunks: 0. "
                "No legacy FAQ files are imported automatically."
            )
            await expect(tech).to_contain_text(
                "Polling every 5 seconds while a file is being read. Lists show the latest 50 "
                "documents/gaps, 20 jobs and 100 sources."
            )
            await expect(maintenance).not_to_contain_text("To add files, the knowledge assistant")
            files_before = stub.reads["files"]
            await maintenance.get_by_role("button", name="Clean up leftover files").click()
            await expect(
                page.get_by_text("Clean-up done: 2 leftover files removed. Files in use were kept.")
            ).to_be_visible()
            assert stub.posts == ["kb/storage/reconcile"]
            await until(
                lambda: stub.reads["files"] > files_before, "files not read after reconcile"
            )

            # The settings say whether the knowledge assistant is on.
            settings = page.get_by_test_id("kb-settings-details")
            await expect(settings).to_contain_text("The knowledge assistant is on.")
            # A disclosure can still be closed by hand.
            await page.get_by_test_id("kb-maintenance-toggle").click()
            await expect(maintenance).to_be_hidden()

        # Switched off: the original sentence sits in Maintenance, uploads are paused on Add.
        off = Stub(agent_active=False)
        async with knowledge_page(
            browser, site, off, path=maintenance_path, wait="kb-maintenance"
        ) as page:
            maintenance = page.get_by_test_id("kb-maintenance-details")
            await expect(maintenance).to_contain_text(
                "To add files, the knowledge assistant must be switched on in Services. "
                "Ask your admin."
            )
            await expect(page.get_by_test_id("kb-settings-details")).to_contain_text(
                "The knowledge assistant is off."
            )
        async with knowledge_page(
            browser, site, off, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await expect(page.locator("#kb-files")).to_be_disabled()
            await expect(page.get_by_test_id("kb-add")).to_contain_text("switched off")
        async with knowledge_page(browser, site, off) as page:
            # The failed card says why trying again is paused; Remove still works.
            paused = page.get_by_test_id("kb-card-job-failed-paused")
            await expect(paused).to_contain_text("switched off")
            await expect(
                card(page, "job-failed").get_by_role("button", name="Try again")
            ).to_be_disabled()
            await expect(
                card(page, "job-failed").get_by_role("button", name="Remove", exact=True)
            ).to_be_enabled()

        # Read-only members see the lists and downloads, never the controls.
        async with knowledge_page(browser, site, Stub(), role="read_only") as page:
            await open_card(page, "doc-hours")
            await expect(page.get_by_role("link", name="Download original")).to_have_count(1)
            await expect(page.get_by_role("button", name="Delete source")).to_have_count(0)
            await expect(page.get_by_role("button", name="Delete document")).to_have_count(0)
        async with knowledge_page(
            browser, site, Stub(), role="read_only", path="/portal/faq/add", wait="kb-page-heading"
        ) as page:
            await expect(page.get_by_test_id("kb-add")).to_have_count(0)
            await expect(page.locator("#kb-files")).to_have_count(0)
        async with knowledge_page(
            browser, site, Stub(), role="read_only", path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
            await expect(page.get_by_role("button", name="Mark resolved")).to_have_count(0)
        async with knowledge_page(
            browser, site, Stub(), role="read_only", path=maintenance_path, wait="kb-maintenance"
        ) as page:
            maintenance = page.get_by_test_id("kb-maintenance-details")
            await expect(maintenance).to_contain_text(
                "Read-only access: upload, deletion and gap resolution are unavailable."
            )
            await expect(
                maintenance.get_by_role("button", name="Clean up leftover files")
            ).to_have_count(0)

    await with_browser(body)


@pytest.mark.parametrize(("section", "path", "_loaded"), PAGES)
async def test_package_inactive_sentence_unchanged(
    site: Site, section: str, path: str, _loaded: str
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        for name in ("documents", "jobs", "gaps", "files"):
            stub.fail[name] = (403, {"detail": "package_inactive"})
        async with knowledge_page(browser, site, stub, wait="kb-locked", path=path) as page:
            locked = page.get_by_test_id("kb-locked")
            await expect(locked).to_have_text(PACKAGE_SENTENCE)
            await expect(page.get_by_text(PACKAGE_SENTENCE)).to_have_count(1)
            await expect(page.get_by_role("heading", level=1)).to_have_text("Business knowledge")
            await expect(page.get_by_test_id("kb-add")).to_have_count(0)
            await expect(
                page.get_by_role("button", name="Refresh business knowledge")
            ).to_be_visible()

    await with_browser(body)


async def test_upload_queues_a_file_and_shows_it_as_reading(site: Site, tmp_path: Any) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        sample = tmp_path / "new-upload.pdf"
        sample.write_bytes(b"%PDF-1.4 synthetic")
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/add", wait="kb-add"
        ) as page:
            add = page.get_by_test_id("kb-add")
            steps = add.get_by_role("list").locator("li")
            await expect(steps).to_have_text(
                ["Add a file", "Your assistant reads it", "It answers from it"]
            )
            await expect(add).to_contain_text(
                "One to three PDF, DOCX or CSV files, at most 10 MB each"
            )
            await expect(add).to_contain_text("No file chosen yet")
            queue = add.get_by_role("button", name="Upload", exact=True)
            await expect(queue).to_be_disabled()
            await page.locator("#kb-files").set_input_files(str(sample))
            await expect(add).to_contain_text("new-upload.pdf")
            await queue.click()
            row = page.get_by_test_id("kb-progress-job-new")
            await expect(row).to_contain_text("new-upload.pdf")
            await expect(page.get_by_test_id("kb-progress-job-new-word")).to_have_text("Waiting")
            assert len(stub.upload_keys) == 1 and stub.upload_keys[0]
            # The reading file shows as a card on the library page.
            await page.get_by_test_id("kb-subnav-library").click()
            await expect(page).to_have_url(re.compile(r"/portal/faq$"), timeout=WAIT)
            await expect(card(page, "job-new")).to_be_visible()
            await expect(page.get_by_test_id("kb-card-job-new-chip")).to_have_text("Reading")

    await with_browser(body)


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


@pytest.mark.parametrize("theme", THEMES)
async def test_knowledge_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def check(page: Page, label: str) -> None:
        for width in WIDTHS:
            await page.set_viewport_size({"width": width, "height": 900})
            measured = await overflow(page)
            assert measured["scroll"] <= measured["inner"], (theme, label, width, measured)
            outside = await page.evaluate(
                """() => [...document.querySelectorAll('[data-testid^="kb-"]')].filter(
                    e => e.getBoundingClientRect().right > window.innerWidth + 0.5
                ).map(e => e.dataset.testid)"""
            )
            assert not outside, (theme, label, width, outside)

    async def body(browser: Any) -> None:
        stub = Stub()
        stub.fail["files"] = (503, {"detail": "synthetic files outage"})
        async with knowledge_page(browser, site, stub, theme=theme) as page:
            await page.get_by_test_id("kb-tech-all").click()
            for item in ("doc-hours", "job-failed"):
                await expect(page.get_by_test_id(f"kb-card-{item}-tech-details")).to_be_visible()
            await open_fold(page, "kb-other-files")
            await expect(page.get_by_test_id("kb-error-files")).to_be_visible()
            await check(page, "library")
        async with knowledge_page(
            browser, site, Stub(), theme=theme, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await expect(page.get_by_test_id("kb-drop")).to_be_visible()
            await add_ready(page)
            await check(page, "add")
            await page.evaluate(
                DROP_SCRIPT, [["a-rather-long-file-name-for-the-delivery-list.pdf", 2048]]
            )
            await expect(page.get_by_test_id("kb-selection")).to_contain_text("a-rather-long")
            await check(page, "add with a file")
        async with knowledge_page(
            browser, site, Stub(), theme=theme, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
            await check(page, "questions")
            await page.get_by_test_id("kb-gap-gap-sunday-answer").click()
            await page.get_by_label("Your answer").fill("We deliver on Sundays. " * 12)
            await expect(page.get_by_test_id("kb-answer-save-gap-sunday")).to_be_enabled()
            await check(page, "questions with an open answer form")
        async with knowledge_page(
            browser,
            site,
            Stub(),
            theme=theme,
            path="/portal/faq/maintenance",
            wait="kb-maintenance",
        ) as page:
            await expect(page.get_by_test_id("kb-maintenance-details")).to_be_visible()
            await check(page, "maintenance")

    await with_browser(body)


async def test_add_page_first_screen_at_390_leads_with_the_upload_area(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(
            browser, site, Stub(), width=390, height=844, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await expect(page.get_by_role("heading", level=1)).to_be_in_viewport()
            await expect(page.get_by_test_id("kb-add")).to_be_in_viewport()
            await expect(page.get_by_test_id("kb-drop")).to_be_in_viewport()
            await expect(page.get_by_role("button", name="Choose files")).to_be_in_viewport()
            # Nothing technical sits above the upload area.
            for text in (
                "Embedding compatibility",
                "Clean up leftover files",
                "Polling every 5 seconds",
            ):
                await expect(page.get_by_text(text, exact=False).first).to_have_count(0)
            order = await page.evaluate(
                """() => ['kb-subnav', 'kb-page-heading', 'kb-add', 'kb-drop'].map(
                    id => document.querySelector(`[data-testid="${id}"]`)
                        .getBoundingClientRect().top
                )"""
            )
            assert order == sorted(order), order

    await with_browser(body)


async def test_missing_assistant_row_keeps_the_old_sentence_and_pauses_writes(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(agent_missing=True)
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await expect(page.locator("#kb-files")).to_be_disabled()
            await expect(page.get_by_test_id("kb-add-unknown")).to_contain_text("not available")
            await expect(page.get_by_test_id("kb-error-agents")).to_have_count(0)
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/maintenance", wait="kb-maintenance"
        ) as page:
            maintenance = page.get_by_test_id("kb-maintenance-details")
            await expect(maintenance).to_contain_text(
                "We couldn't check the knowledge assistant's settings."
            )
            await expect(maintenance).not_to_contain_text("We could not load the assistant details")
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            await expect(page.get_by_role("button", name="Mark resolved").first).to_be_disabled()
        # The lists still load: only the writes wait.
        async with knowledge_page(browser, site, stub) as page:
            await expect(card(page, "doc-hours")).to_be_visible()
            await expect(page.get_by_test_id("kb-error-agents")).to_have_count(0)

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
            await page.get_by_role("button", name="Refresh business knowledge").click()
            await expect(card(page, "job-failed")).to_have_count(0)
            details = await open_card(page, "doc-hours")
            await expect(details).to_contain_text("Not recorded")
            stub.fail["jobs"] = (503, {"detail": "synthetic jobs outage"})
            await page.get_by_role("button", name="Refresh business knowledge").click()
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
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            row = page.get_by_test_id("kb-gap-gap-sunday")
            await expect(row).to_contain_text("Do you deliver on Sundays?")
            await expect(row).to_contain_text("Asked 4 times. Last asked")
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
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await page.locator("#kb-files").set_input_files(str(sample))
            queue = page.get_by_role("button", name="Upload", exact=True)
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
            await expect(page.get_by_test_id("kb-progress-job-new")).to_be_visible()
            details = await open_progress_tech(page, "job-new")
            await expect(details).to_contain_text(QUEUED_TEXT)
            assert len(stub.upload_keys) == 2 and stub.upload_keys[0] == stub.upload_keys[1]

    await with_browser(body)


async def test_status_line_and_add_knowledge_lead_the_page(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(browser, site, Stub()) as page:
            status = page.get_by_test_id("kb-status")
            # Two active documents; one failed upload and two questions need attention.
            await expect(status).to_have_text(
                "Your assistant knows 2 things. 3 need your attention."
            )
            add = page.get_by_test_id("kb-add-knowledge")
            await expect(add).to_have_text("Add knowledge")
            await expect(add).to_have_attribute("href", "/portal/faq/add")
            order = await page.evaluate(
                """() => ['kb-status', 'kb-add-knowledge', 'kb-tech-all', 'kb-ready'].map(
                    id => document.querySelector(`[data-testid="${id}"]`)
                        .getBoundingClientRect().top
                )"""
            )
            assert order == sorted(order), order
            await add.click()
            await expect(page).to_have_url(re.compile(r"/portal/faq/add$"), timeout=WAIT)
            await expect(page.get_by_test_id("kb-add")).to_be_visible()

        # Nothing wrong: the second sentence says so.
        calm = Stub(jobs=[job for job in default_jobs() if job["status"] == "done"], gaps=[])
        async with knowledge_page(browser, site, calm) as page:
            await expect(page.get_by_test_id("kb-status")).to_have_text(
                "Your assistant knows 2 things. Nothing needs your attention."
            )

        # A list that did not load is never written as 0.
        broken = Stub()
        broken.fail["gaps"] = (503, {"detail": "synthetic gaps outage"})
        async with knowledge_page(browser, site, broken) as page:
            await expect(page.get_by_test_id("kb-status")).to_have_text(
                "Your assistant knows 2 things."
            )
        lost = Stub()
        lost.fail["documents"] = (503, {"detail": "synthetic documents outage"})
        async with knowledge_page(browser, site, lost) as page:
            await expect(page.get_by_test_id("kb-status")).to_have_text(
                "We couldn't count everything just now."
            )

        # Members who cannot add are not offered the link.
        async with knowledge_page(browser, site, Stub(), role="read_only") as page:
            await expect(page.get_by_test_id("kb-status")).to_be_visible()
            await expect(page.get_by_test_id("kb-add-knowledge")).to_have_count(0)

    await with_browser(body)


async def test_questions_callout_links_to_the_questions_page(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(browser, site, Stub()) as page:
            callout = page.get_by_test_id("kb-questions-callout")
            await expect(callout).to_contain_text("Questions your assistant couldn't answer: 2")
            link = callout.get_by_role("link", name="Add an answer")
            await expect(link).to_have_attribute("href", "/portal/faq/questions")
            await link.click()
            await expect(page).to_have_url(re.compile(r"/portal/faq/questions$"), timeout=WAIT)
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
        # A member who cannot write is offered no "Add an answer"; the link says what it is.
        async with knowledge_page(browser, site, Stub(), role="read_only") as page:
            callout = page.get_by_test_id("kb-questions-callout")
            await expect(callout.get_by_role("link", name="Add an answer")).to_have_count(0)
            await expect(callout.get_by_role("link", name="See the questions")).to_have_attribute(
                "href", "/portal/faq/questions"
            )
        # Hidden when there are none, and when the list could not be read.
        async with knowledge_page(browser, site, Stub(gaps=[])) as page:
            await expect(page.get_by_test_id("kb-status")).to_be_visible()
            await expect(page.get_by_test_id("kb-questions-callout")).to_have_count(0)
        broken = Stub()
        broken.fail["gaps"] = (503, {"detail": "synthetic gaps outage"})
        async with knowledge_page(browser, site, broken) as page:
            await expect(page.get_by_test_id("kb-status")).to_be_visible()
            await expect(page.get_by_test_id("kb-questions-callout")).to_have_count(0)

    await with_browser(body)


async def test_library_shows_five_then_show_all(site: Site) -> None:
    async def body(browser: Any) -> None:
        documents = [make_document(f"doc-{n}", f"Guide {n}", None, n) for n in range(1, 8)]
        stub = Stub(documents=documents, files=[], jobs=[], gaps=[])
        async with knowledge_page(browser, site, stub) as page:
            ready = page.get_by_test_id("kb-ready")
            shown = ready.locator('[data-testid^="kb-card-doc-"][data-testid$="-title"]')
            await expect(shown).to_have_count(5)
            button = page.get_by_test_id("kb-ready-show-all")
            await expect(button).to_have_text("Show all 7")
            await expect(button).to_have_attribute("aria-expanded", "false")
            controls = await button.get_attribute("aria-controls")
            assert controls and await page.locator(f"#{controls}").count() == 1
            await button.click()
            await expect(shown).to_have_count(7)
            await expect(button).to_have_text("Show fewer")
            await expect(button).to_have_attribute("aria-expanded", "true")
            await button.click()
            await expect(shown).to_have_count(5)

        # Five or fewer: no button.
        few = Stub(documents=documents[:5], files=[], jobs=[], gaps=[])
        async with knowledge_page(browser, site, few) as page:
            await expect(page.get_by_test_id("kb-card-doc-5")).to_be_visible()
            await expect(page.get_by_test_id("kb-ready-show-all")).to_have_count(0)

    await with_browser(body)


async def test_card_has_details_and_technical_details(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(browser, site, Stub()) as page:
            await open_plain(page, "doc-hours")
            rows = page.get_by_test_id("kb-card-doc-hours-plain")
            await expect(rows).to_contain_text("Added")
            await expect(rows).to_contain_text("2 Nov 2026")
            await expect(rows).to_contain_text("Size")
            await expect(rows).to_contain_text("2 MB")
            await expect(rows).to_contain_text("What your assistant can use")
            await expect(rows).to_contain_text("12 parts your assistant can look up")
            # The exact lines wait behind Technical details; the file actions are one level up.
            tech = page.get_by_test_id("kb-card-doc-hours-tech-details")
            await expect(tech).to_be_hidden()
            await expect(page.get_by_test_id("kb-card-doc-hours-tech-toggle")).to_have_attribute(
                "aria-expanded", "false"
            )
            actions = page.get_by_test_id("kb-card-doc-hours-actions")
            await expect(actions.get_by_role("link", name="Download original")).to_have_count(1)
            await expect(actions.get_by_role("button", name="Delete document")).to_be_enabled()
            await expect(actions.get_by_role("button", name="Delete source")).to_be_enabled()
            await page.get_by_test_id("kb-card-doc-hours-tech-toggle").click()
            await expect(tech).to_be_visible()
            await expect(tech).to_contain_text("Version 1, active, 12 chunks.")
            await expect(tech.get_by_role("link", name="Download original")).to_have_count(0)
            await expect(tech.get_by_role("button", name="Delete document")).to_have_count(0)

            reading = page.get_by_test_id("kb-card-job-running-plain")
            await open_plain(page, "job-running")
            await expect(reading).to_contain_text("Nothing yet. It is still reading.")
            await open_plain(page, "job-failed")
            await expect(page.get_by_test_id("kb-card-job-failed-plain")).to_contain_text(
                "Nothing. It could not read this file."
            )

    await with_browser(body)


async def test_show_technical_details_opens_every_card(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(browser, site, Stub()) as page:
            switch = page.get_by_test_id("kb-tech-all")
            await expect(switch).to_have_attribute("aria-pressed", "false")
            await expect(switch).to_have_text("Show technical details")
            await switch.click()
            await expect(switch).to_have_attribute("aria-pressed", "true")
            for item in ("doc-hours", "doc-faq", "job-failed", "job-running", "job-queued"):
                await expect(page.get_by_test_id(f"kb-card-{item}-toggle")).to_have_attribute(
                    "aria-expanded", "true"
                )
                await expect(page.get_by_test_id(f"kb-card-{item}-tech-toggle")).to_have_attribute(
                    "aria-expanded", "true"
                )
            await expect(page.get_by_test_id("kb-card-doc-hours-tech-details")).to_contain_text(
                "Version 1, active, 12 chunks."
            )
            # One card can still be closed by hand, and the switch closes the rest.
            await page.get_by_test_id("kb-card-doc-faq-toggle").click()
            await expect(page.get_by_test_id("kb-card-doc-faq-toggle")).to_have_attribute(
                "aria-expanded", "false"
            )
            await switch.click()
            await expect(switch).to_have_attribute("aria-pressed", "false")
            for item in ("doc-hours", "job-failed"):
                await expect(page.get_by_test_id(f"kb-card-{item}-toggle")).to_have_attribute(
                    "aria-expanded", "false"
                )

    await with_browser(body)


async def test_section_bar_names_the_four_pages(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(browser, site, Stub()) as page:
            await expect(page.get_by_role("heading", level=1)).to_have_text("Business knowledge")
            bar = page.get_by_role("navigation", name="Business knowledge pages")
            await expect(bar.get_by_role("link")).to_have_text(
                [
                    "What it knows",
                    "Add knowledge",
                    "Unanswered questions",
                    "Maintenance and settings",
                ]
            )
            heading = page.get_by_test_id("kb-page-heading")
            await expect(heading).to_have_text("What your assistant knows")
            await expect(page.get_by_test_id("kb-subnav-library")).to_have_attribute(
                "aria-current", "page"
            )
            for key, path, title in (
                ("add", r"/portal/faq/add$", "Add knowledge"),
                ("questions", r"/portal/faq/questions$", "Unanswered questions"),
                ("maintenance", r"/portal/faq/maintenance$", "Maintenance and settings"),
                ("library", r"/portal/faq$", "What your assistant knows"),
            ):
                await page.get_by_test_id(f"kb-subnav-{key}").click()
                await expect(page).to_have_url(re.compile(path), timeout=WAIT)
                await expect(heading).to_have_text(title)
                await expect(heading).to_be_focused()
                await expect(page.get_by_test_id(f"kb-subnav-{key}")).to_have_attribute(
                    "aria-current", "page"
                )

    await with_browser(body)


async def test_choose_the_file_again_links_to_the_add_page(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(browser, site, Stub(jobs=[gone_job()])) as page:
            link = card(page, "job-failed").get_by_role("link", name="Choose the file again")
            await expect(link).to_have_attribute("href", "/portal/faq/add")
            await link.click()
            await expect(page).to_have_url(re.compile(r"/portal/faq/add$"), timeout=WAIT)
            await expect(page.get_by_test_id("kb-add")).to_be_visible()
        # The empty library points at the same page.
        async with knowledge_page(browser, site, Stub(documents=[], jobs=[], files=[])) as page:
            await expect(page.get_by_text("No files yet.")).to_be_visible()
            await expect(page.get_by_test_id("kb-ready")).to_contain_text(
                "Add a file and your assistant will read it."
            )
            await expect(page.get_by_test_id("kb-empty-add-link")).to_have_attribute(
                "href", "/portal/faq/add"
            )

    await with_browser(body)


async def test_a_rail_link_without_a_tenant_drops_the_tenant_on_screen(site: Site) -> None:
    """A soft move from ?client_id=A to the same page without it must not keep A's data."""

    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(
            browser,
            site,
            stub,
            path=f"/knowledge-base/add?client_id={TENANT}",
            role="platform_admin",
            wait="kb-add",
            rail_expanded=True,
        ) as page:
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            await page.get_by_test_id("rail-child-knowledge_base-add").click()
            await expect(page).to_have_url(re.compile(r"/knowledge-base/add$"), timeout=WAIT)
            await expect(page.get_by_text("Select a tenant to continue.")).to_be_visible()
            await expect(page.get_by_test_id("kb-add")).to_have_count(0)
            await expect(page.get_by_label("Tenant")).to_have_value("")
            await expect(page.get_by_test_id("kb-subnav-library")).to_have_attribute(
                "href", "/knowledge-base"
            )
            # Back returns to the first tenant and its page.
            await page.go_back()
            await expect(page).to_have_url(re.compile(rf"client_id={TENANT}$"), timeout=WAIT)
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            await expect(page.get_by_test_id("kb-add")).to_be_visible()

    await with_browser(body)


async def test_knowledge_section_bar_keeps_the_admin_tenant(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(
            browser, site, stub, path="/knowledge-base", role="platform_admin", wait=None
        ) as page:
            await expect(page.get_by_text("Select a tenant to continue.")).to_be_visible(
                timeout=WAIT
            )
            assert "kb/documents" not in stub.queries
            await expect(page.get_by_test_id("kb-subnav-add")).to_have_attribute(
                "href", "/knowledge-base/add"
            )
            select = page.get_by_label("Tenant")
            await expect(select.locator("option")).to_have_count(2)
            await select.select_option(TENANT)
            await expect(page).to_have_url(
                re.compile(rf"/knowledge-base\?client_id={TENANT}$"), timeout=WAIT
            )
            await expect(page.get_by_test_id("kb-library")).to_be_visible()
            assert stub.queries["kb/documents"][-1].get("client_id") == [TENANT], stub.queries
            for key, suffix in (
                ("add", "/add"),
                ("questions", "/questions"),
                ("maintenance", "/maintenance"),
            ):
                await expect(page.get_by_test_id(f"kb-subnav-{key}")).to_have_attribute(
                    "href", f"/knowledge-base{suffix}?client_id={TENANT}"
                )
            await expect(page.get_by_test_id("kb-subnav-library")).to_have_attribute(
                "href", f"/knowledge-base?client_id={TENANT}"
            )
            await page.get_by_test_id("kb-subnav-add").click()
            await expect(page).to_have_url(
                re.compile(rf"/knowledge-base/add\?client_id={TENANT}$"), timeout=WAIT
            )
            await expect(page.get_by_test_id("kb-add")).to_be_visible()
            assert stub.queries["kb/ingest-jobs"][-1].get("client_id") == [TENANT]
            assert stub.queries["agents"][-1].get("client_id") == [TENANT]
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            # A reload keeps the tenant, and the next page too.
            await page.reload()
            await expect(page.get_by_test_id("kb-add")).to_be_visible()
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            assert stub.queries["kb/ingest-jobs"][-1].get("client_id") == [TENANT]
            await page.get_by_test_id("kb-subnav-questions").click()
            await expect(page).to_have_url(
                re.compile(rf"/knowledge-base/questions\?client_id={TENANT}$"), timeout=WAIT
            )
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
            assert stub.queries["kb/gaps"][-1].get("client_id") == [TENANT]
            await page.get_by_test_id("kb-subnav-maintenance").click()
            await expect(page).to_have_url(
                re.compile(rf"/knowledge-base/maintenance\?client_id={TENANT}$"), timeout=WAIT
            )
            await expect(page.get_by_test_id("kb-maintenance")).to_be_visible()
            assert stub.queries["kb/files"][-1].get("client_id") == [TENANT]
            await page.reload()
            await expect(page.get_by_test_id("kb-maintenance")).to_be_visible()
            await expect(page.get_by_label("Tenant")).to_have_value(TENANT)
            assert stub.queries["kb/files"][-1].get("client_id") == [TENANT]
            await expect(page).to_have_url(
                re.compile(rf"/knowledge-base/maintenance\?client_id={TENANT}$")
            )
            # Clearing the choice removes it from the address.
            await page.get_by_label("Tenant").select_option("")
            await expect(page).to_have_url(
                re.compile(r"/knowledge-base/maintenance$"), timeout=WAIT
            )
            await expect(page.get_by_text("Select a tenant to continue.")).to_be_visible()

    await with_browser(body)


async def test_drop_area_replaces_the_raw_file_control(site: Site, tmp_path: Any) -> None:
    async def body(browser: Any) -> None:
        sample = tmp_path / "menu.pdf"
        sample.write_bytes(b"%PDF-1.4 synthetic")
        async with knowledge_page(
            browser, site, Stub(), path="/portal/faq/add", wait="kb-add"
        ) as page:
            drop = page.get_by_test_id("kb-drop")
            await expect(drop).to_be_visible()
            await expect(drop).to_contain_text("Drag files here, or")
            # The native control is still there for the label and the picker, but not visible.
            native = page.locator("#kb-files")
            await expect(native).to_have_attribute("type", "file")
            await expect(native).to_have_attribute("tabindex", "-1")
            box = await native.bounding_box()
            assert box is not None and box["width"] <= 1 and box["height"] <= 1, box
            await expect(
                page.get_by_label("One to three PDF, DOCX or CSV files, at most 10 MB each")
            ).to_have_count(1)
            # The button opens the picker, with the mouse and with the keyboard.
            button = drop.get_by_role("button", name="Choose files")
            async with page.expect_file_chooser() as chooser:
                await button.click()
            await (await chooser.value).set_files(str(sample))
            await expect(page.get_by_test_id("kb-selection")).to_contain_text("menu.pdf")
            await button.focus()
            await expect(button).to_be_focused()
            async with page.expect_file_chooser():
                await page.keyboard.press("Enter")

        # With adding switched off the area is inert.
        async with knowledge_page(
            browser, site, Stub(agent_active=False), path="/portal/faq/add", wait="kb-add"
        ) as page:
            await expect(page.get_by_test_id("kb-add-off")).to_be_visible()
            await expect(page.get_by_role("button", name="Choose files")).to_be_disabled()
            await page.evaluate(DROP_SCRIPT, [["menu.pdf", 10]])
            await expect(page.get_by_test_id("kb-selection")).to_have_text("No file chosen yet")
            await expect(page.get_by_text(SELECTION_MESSAGE)).to_have_count(0)
            # Even then the browser must not open a dropped file in place of the console.
            prevented = await page.evaluate(
                """() => ['dragover', 'drop'].map((type) => {
                    const event = new DragEvent(type, {bubbles: true, cancelable: true});
                    document.querySelector('[data-testid="kb-drop"]').dispatchEvent(event);
                    return event.defaultPrevented;
                })"""
            )
            assert prevented == [True, True], prevented

    await with_browser(body)


async def test_dropped_files_use_the_same_rules_as_the_picker(site: Site, tmp_path: Any) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await add_ready(page)
            message = page.get_by_text(SELECTION_MESSAGE)
            queue = page.get_by_role("button", name="Upload", exact=True)
            # Four files, a .txt and an empty file: the picker's message every time.
            for dropped in (
                [["a.pdf", 5], ["b.pdf", 5], ["c.pdf", 5], ["d.pdf", 5]],
                [["notes.txt", 5]],
                [["empty.pdf", 0]],
            ):
                await page.evaluate(DROP_SCRIPT, dropped)
                await expect(message).to_be_visible()
                await expect(page.get_by_test_id("kb-selection")).to_have_text("No file chosen yet")
                await expect(queue).to_be_disabled()
            # The picker says the same for the same files.
            files = [
                FilePayload(name=f"{n}.pdf", mimeType="application/pdf", buffer=b"x")
                for n in "abcd"
            ]
            await page.locator("#kb-files").set_input_files(files)
            await expect(message).to_be_visible()
            await expect(queue).to_be_disabled()
            # A good drop clears the message and queues like a picked file.
            await page.evaluate(DROP_SCRIPT, [["menu.pdf", 5]])
            await expect(message).to_have_count(0)
            await expect(page.get_by_test_id("kb-selection")).to_contain_text("menu.pdf")
            await expect(queue).to_be_enabled()
            await queue.click()
            await expect(page.get_by_test_id("kb-progress-job-new")).to_contain_text("menu.pdf")
            assert len(stub.upload_keys) == 1

    await with_browser(body)


async def test_progress_moves_waiting_reading_ready_and_stops_polling(
    site: Site, tmp_path: Any
) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(jobs=[], files=[], documents=[])
        sample = tmp_path / "new-upload.pdf"
        sample.write_bytes(b"%PDF-1.4 synthetic")
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await page.locator("#kb-files").set_input_files(str(sample))
            await page.get_by_role("button", name="Upload", exact=True).click()
            word = page.get_by_test_id("kb-progress-job-new-word")
            await expect(word).to_have_text("Waiting")
            for job in stub.jobs:
                job["status"] = "processing"
            await expect(word).to_have_text("Reading", timeout=15_000)
            for job in stub.jobs:
                job["status"] = "done"
            await expect(word).to_have_text("Ready", timeout=15_000)
            settled = dict(stub.reads)
            await asyncio.sleep(6.5)
            assert stub.reads == settled, (stub.reads, settled)

    await with_browser(body)


async def test_upload_message_sits_under_technical_details(site: Site, tmp_path: Any) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        sample = tmp_path / "new-upload.pdf"
        sample.write_bytes(b"%PDF-1.4 synthetic")
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/add", wait="kb-add"
        ) as page:
            await page.locator("#kb-files").set_input_files(str(sample))
            await page.get_by_role("button", name="Upload", exact=True).click()
            await expect(page.get_by_test_id("kb-progress-job-new")).to_be_visible()
            toggle = page.get_by_test_id("kb-progress-job-new-tech-toggle")
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(page.get_by_text(QUEUED_TEXT)).to_be_hidden()
            details = await open_progress_tech(page, "job-new")
            await expect(toggle).to_have_attribute("aria-expanded", "true")
            await expect(details).to_contain_text(QUEUED_TEXT)

    await with_browser(body)


async def test_add_an_answer_saves_a_note_and_offers_mark_resolved(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub()
        async with knowledge_page(
            browser, site, stub, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            row = page.get_by_test_id("kb-gap-gap-sunday")
            await expect(page.get_by_test_id("kb-questions-upload-link")).to_have_attribute(
                "href", "/portal/faq/add"
            )
            opener = page.get_by_test_id("kb-gap-gap-sunday-answer")
            await expect(opener).to_have_attribute("aria-expanded", "false")
            await opener.click()
            await expect(opener).to_have_attribute("aria-expanded", "true")
            await expect(row).to_contain_text("Do you deliver on Sundays?")
            save = page.get_by_test_id("kb-answer-save-gap-sunday")
            await expect(save).to_be_disabled()
            await page.get_by_label("Your answer").fill("  We deliver on Sundays from 10am.  ")
            await expect(save).to_be_enabled()
            await save.click()
            saved = page.get_by_test_id("kb-answer-saved-gap-sunday")
            await expect(saved).to_have_text("Saved. Your assistant can use this answer now.")
            assert stub.answers == [
                {
                    "title": "Do you deliver on Sundays?",
                    "content": "We deliver on Sundays from 10am.",
                }
            ], stub.answers
            # Focus moves to the row's Mark resolved, which still works.
            resolve = row.get_by_role("button", name="Mark resolved")
            await expect(resolve).to_be_focused()
            await resolve.click()
            await expect(row).to_have_count(0)
            assert stub.posts == ["kb/gaps/gap-sunday/resolve"]
            # The other question's form can be cancelled without saving.
            await page.get_by_test_id("kb-gap-gap-halal-answer").click()
            await page.get_by_role("button", name="Cancel").click()
            await expect(page.get_by_test_id("kb-answer-save-gap-halal")).to_have_count(0)
            assert len(stub.answers) == 1

        # A long question is cut to the title limit.
        long_query = "Q" * 300
        gaps = default_gaps()
        gaps[0]["query"] = long_query
        longer = Stub(gaps=gaps)
        async with knowledge_page(
            browser, site, longer, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            await page.get_by_test_id("kb-gap-gap-sunday-answer").click()
            await page.get_by_label("Your answer").fill("Yes.")
            await page.get_by_test_id("kb-answer-save-gap-sunday").click()
            await expect(page.get_by_test_id("kb-answer-saved-gap-sunday")).to_be_visible()
            assert longer.answers == [{"title": "Q" * 255, "content": "Yes."}]

    await with_browser(body)


async def test_save_answer_posts_once_and_never_claims_success_on_error(site: Site) -> None:
    async def body(browser: Any) -> None:
        # A double click posts once.
        slow = Stub(answer_delay=0.8)
        async with knowledge_page(
            browser, site, slow, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            await page.get_by_test_id("kb-gap-gap-sunday-answer").click()
            await page.get_by_label("Your answer").fill("We deliver on Sundays.")
            await page.get_by_test_id("kb-answer-save-gap-sunday").dblclick()
            await expect(page.get_by_test_id("kb-answer-saved-gap-sunday")).to_be_visible()
            await asyncio.sleep(1.0)
            assert len(slow.answers) == 1, slow.answers

        # An error shows the warning, never the saved line, and the answer is kept.
        failing = Stub(answer_failures=1)
        async with knowledge_page(
            browser, site, failing, path="/portal/faq/questions", wait="kb-gaps"
        ) as page:
            await page.get_by_test_id("kb-gap-gap-sunday-answer").click()
            await page.get_by_label("Your answer").fill("We deliver on Sundays.")
            await page.get_by_test_id("kb-answer-save-gap-sunday").click()
            await expect(page.get_by_test_id("kb-answer-error-gap-sunday")).to_have_text(
                "We couldn't save the answer: synthetic answer failure. "
                "Check What it knows before you try again, so it is not added twice."
            )
            await expect(page.get_by_test_id("kb-answer-saved-gap-sunday")).to_have_count(0)
            await expect(page.get_by_label("Your answer")).to_have_value("We deliver on Sundays.")
            # Pressing again after an error is a new, deliberate save.
            await page.get_by_test_id("kb-answer-save-gap-sunday").click()
            await expect(page.get_by_test_id("kb-answer-saved-gap-sunday")).to_be_visible()
            assert len(failing.answers) == 2

    await with_browser(body)


async def test_read_only_sees_no_add_an_answer(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with knowledge_page(
            browser,
            site,
            Stub(),
            role="read_only",
            path="/portal/faq/questions",
            wait="kb-gaps",
        ) as page:
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
            await expect(page.get_by_role("button", name="Add an answer")).to_have_count(0)
        # Adding switched off: the button is not offered either.
        async with knowledge_page(
            browser,
            site,
            Stub(agent_active=False),
            path="/portal/faq/questions",
            wait="kb-gaps",
        ) as page:
            await expect(page.get_by_test_id("kb-gap-gap-sunday")).to_be_visible()
            await expect(page.get_by_role("button", name="Add an answer")).to_have_count(0)

    await with_browser(body)


# --- Failed, waiting and removed uploads: plain notes, Try again, Remove, Cancel, Clear all ---

FAILED_NOTE = "We couldn't read this file. You can try again or remove it."
GONE_NOTE = "The original was removed, so this can't be tried again. Remove it."
REMOVE_CONFIRM = "This takes the message off your list. Nothing your assistant knows changes."
CLEAR_CONFIRM = (
    "This takes every failed upload off your list. Nothing your assistant knows changes."
)
PAUSED_NOTE = "Paused: the knowledge assistant is switched off."


def attention_stub(*, count: int = 1, **kwargs: Any) -> Stub:
    """A tenant whose only uploads are failed ones (no library, nothing reading)."""
    names = [
        ("scanned-menu.pdf", "failed"),
        ("blurry-invoice.pdf", "failed"),
        ("old-prices.pdf", "partial"),
    ]
    files = [make_file(f"file-{i}", name, 300, None) for i, (name, _) in enumerate(names[:count])]
    jobs = [
        make_job(f"job-{i}", status, name, f"file-{i}", attempts=3, error="Could not read any text")
        for i, (name, status) in enumerate(names[:count])
    ]
    return Stub(files=files, documents=[], jobs=jobs, gaps=[], **kwargs)


def button(page: Page, scope: Any, name: str) -> Any:
    return scope.get_by_role("button", name=name, exact=True)


async def test_a_failed_card_shows_the_plain_note_and_try_again_and_remove(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = attention_stub(count=3)
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            failed = card(page, "job-0")
            await expect(failed).to_contain_text(FAILED_NOTE)
            await expect(failed).not_to_contain_text("Add the file again")
            await expect(button(page, failed, "Try again")).to_be_enabled()
            await expect(button(page, failed, "Remove")).to_be_enabled()
            # The original is stored, so there is no "Choose the file again" detour.
            await expect(failed.get_by_text("Choose the file again")).to_have_count(0)
            # A partly read file says what was kept; the same two buttons apply.
            partial = card(page, "job-2")
            await expect(partial).to_contain_text(
                "Some of this file couldn't be read. The rest is in your assistant."
            )
            await expect(button(page, partial, "Try again")).to_be_enabled()
            # The server's own words stay behind Technical details.
            await expect(page.get_by_test_id("kb-card-job-0-tech-details")).to_be_hidden()

    await with_browser(body)


async def test_remove_asks_first_then_the_card_disappears(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = attention_stub(count=1)
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            await button(page, card(page, "job-0"), "Remove").click()
            confirm = page.get_by_test_id("kb-confirm")
            await expect(confirm).to_contain_text(REMOVE_CONFIRM)
            assert stub.posts == []
            await button(page, confirm, "Cancel").click()
            await expect(confirm).to_have_count(0)
            assert stub.posts == []
            await button(page, card(page, "job-0"), "Remove").click()
            await button(page, page.get_by_test_id("kb-confirm"), "Remove").click()
            await expect(card(page, "job-0")).to_have_count(0)
            await expect(page.get_by_test_id("kb-attention")).to_have_count(0)
            assert stub.posts == ["kb/ingest-jobs/job-0/dismiss"]
            await expect(page.get_by_text("Removed from your list.")).to_be_visible()
            await expect(page.get_by_test_id("kb-status")).to_contain_text(
                "Nothing needs your attention"
            )

    await with_browser(body)


async def test_try_again_turns_the_card_into_reading(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = attention_stub(count=1)
        # The old failure stays on the row until a worker runs; the card must not show it.
        stub.jobs[0]["meta"] = {
            "results": [
                {
                    "file": "scanned-menu.pdf",
                    "status": "failed",
                    "error": "stale synthetic failure",
                    "document_id": None,
                    "chunks": None,
                    "version": None,
                }
            ]
        }
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            await button(page, card(page, "job-0"), "Try again").click()
            await expect(page.get_by_test_id("kb-card-job-0-chip")).to_have_text("Reading")
            await expect(page.get_by_test_id("kb-attention")).to_have_count(0)
            await expect(page.get_by_test_id("kb-reading")).to_contain_text("scanned-menu.pdf")
            assert stub.posts == ["kb/ingest-jobs/job-0/retry"]
            await expect(page.get_by_text("Trying again.")).to_be_visible()
            await open_card(page, "job-0")
            await expect(card(page, "job-0")).not_to_contain_text("stale synthetic failure")
            await expect(card(page, "job-0")).to_contain_text("None yet")

    await with_browser(body)


async def test_a_failed_card_whose_original_is_gone_offers_remove_only(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(files=[], documents=[], jobs=[gone_job()])
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            gone = card(page, "job-failed")
            await expect(gone).to_contain_text(GONE_NOTE)
            await expect(button(page, gone, "Try again")).to_have_count(0)
            await expect(button(page, gone, "Remove")).to_be_enabled()
            # The one other way forward is to add the file again, which is a link, not a retry.
            await expect(gone.get_by_role("link", name="Choose the file again")).to_have_attribute(
                "href", "/portal/faq/add"
            )
            await button(page, gone, "Remove").click()
            await button(page, page.get_by_test_id("kb-confirm"), "Remove").click()
            await expect(gone).to_have_count(0)
            assert stub.posts == ["kb/ingest-jobs/job-failed/dismiss"]

    await with_browser(body)


async def test_clear_all_that_failed_removes_every_attention_card(site: Site) -> None:
    async def body(browser: Any) -> None:
        # One failed card has no bulk action: Remove is enough.
        async with knowledge_page(
            browser, site, attention_stub(count=1), wait="kb-attention"
        ) as page:
            await expect(page.get_by_role("button", name="Clear all that failed")).to_have_count(0)

        stub = attention_stub(count=3)
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            clear = page.get_by_role("button", name="Clear all that failed")
            await expect(clear).to_be_enabled()
            await clear.click()
            confirm = page.get_by_test_id("kb-confirm")
            await expect(confirm).to_contain_text(CLEAR_CONFIRM)
            await button(page, confirm, "Cancel").click()
            assert stub.posts == []
            await clear.click()
            await button(page, page.get_by_test_id("kb-confirm"), "Clear all").click()
            await expect(page.get_by_test_id("kb-attention")).to_have_count(0)
            assert stub.posts == ["kb/ingest-jobs/dismiss-failed"]
            await expect(
                page.get_by_text("Removed 3 failed uploads from your list.")
            ).to_be_visible()

    await with_browser(body)


async def test_a_processing_job_shows_reading_not_needs_attention_and_refreshes(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(
            files=[make_file("file-busy", "big-catalogue.pdf", 900, None)],
            documents=[],
            gaps=[],
            jobs=[make_job("job-busy", "processing", "big-catalogue.pdf", "file-busy", attempts=1)],
        )
        async with knowledge_page(browser, site, stub, wait="kb-reading") as page:
            await expect(page.get_by_test_id("kb-card-job-busy-chip")).to_have_text("Reading")
            await expect(page.get_by_test_id("kb-attention")).to_have_count(0)
            await expect(page.get_by_test_id("kb-status")).to_contain_text(
                "Nothing needs your attention"
            )
            # Nothing to retry, remove or cancel while a worker has it.
            for name in ("Try again", "Remove", "Cancel"):
                await expect(button(page, card(page, "job-busy"), name)).to_have_count(0)
            start = stub.reads["jobs"]
            await until(lambda: stub.reads["jobs"] > start, "no poll while a job is processing")
            stub.jobs[0]["status"] = "done"
            await expect(page.get_by_test_id("kb-reading")).to_have_count(0, timeout=15_000)

    await with_browser(body)


async def test_a_queued_job_with_the_agent_off_shows_paused_and_cancel(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(
            files=[make_file("file-wait", "price-list.pdf", 900, None)],
            documents=[],
            jobs=[make_job("job-wait", "queued", "price-list.pdf", "file-wait", attempts=0)],
            agent_active=False,
        )
        async with knowledge_page(browser, site, stub, wait="kb-reading") as page:
            waiting = card(page, "job-wait")
            await expect(waiting).to_contain_text(PAUSED_NOTE)
            # The visible line is the paused one (the raw job state stays behind Technical details).
            await expect(waiting.get_by_text("Waiting to be read")).to_be_hidden()
            await button(page, waiting, "Cancel").click()
            await expect(waiting).to_have_count(0)
            assert stub.posts == ["kb/ingest-jobs/job-wait/cancel"]

        # With the assistant on, the same job is simply waiting.
        stub = Stub(
            files=[make_file("file-wait", "price-list.pdf", 900, None)],
            documents=[],
            jobs=[make_job("job-wait", "queued", "price-list.pdf", "file-wait", attempts=0)],
        )
        async with knowledge_page(browser, site, stub, wait="kb-reading") as page:
            await expect(card(page, "job-wait")).to_contain_text("Waiting to be read")
            await expect(card(page, "job-wait")).not_to_contain_text("Paused")
            await expect(button(page, card(page, "job-wait"), "Cancel")).to_be_enabled()

    await with_browser(body)


async def test_deleting_a_source_makes_no_attention_card(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = attention_stub(count=1)
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            await open_plain(page, "job-0")
            await (
                page.get_by_test_id("kb-card-job-0-actions")
                .get_by_role("button", name="Delete source")
                .click()
            )
            confirm = page.get_by_test_id("kb-confirm")
            # A failed upload has no document: no talk of documents or duplicates.
            await expect(confirm).to_contain_text("Delete scanned-menu.pdf?")
            await expect(confirm).not_to_contain_text("document")
            await expect(confirm).not_to_contain_text("duplicate")
            await button(page, confirm, "Confirm delete").click()
            await expect(page.get_by_test_id("kb-attention")).to_have_count(0)
            await expect(card(page, "job-0")).to_have_count(0)
            assert stub.deletes == ["kb/files/file-0"]
            await expect(page.get_by_text("Deleted.", exact=False).first).to_be_visible()
            await expect(page.get_by_test_id("kb-status")).to_contain_text(
                "Nothing needs your attention"
            )
            await expect(
                page.get_by_text("We'll finish clearing the stored file shortly.")
            ).to_have_count(0)

        # A storage sweep that is still pending is said, not hidden.
        pending = attention_stub(count=1)
        pending.storage_cleanup = "pending"
        async with knowledge_page(browser, site, pending, wait="kb-attention") as page:
            await open_plain(page, "job-0")
            await (
                page.get_by_test_id("kb-card-job-0-actions")
                .get_by_role("button", name="Delete source")
                .click()
            )
            await button(page, page.get_by_test_id("kb-confirm"), "Confirm delete").click()
            await expect(
                page.get_by_text("We'll finish clearing the stored file shortly.")
            ).to_be_visible()

    await with_browser(body)


async def test_delete_still_works_when_the_agent_is_off(site: Site) -> None:
    async def body(browser: Any) -> None:
        stub = Stub(agent_active=False)
        async with knowledge_page(browser, site, stub) as page:
            actions = page.get_by_test_id("kb-card-doc-hours-actions")
            await open_plain(page, "doc-hours")
            await expect(button(page, actions, "Delete document")).to_be_enabled()
            await expect(button(page, actions, "Delete source")).to_be_enabled()
            await button(page, actions, "Delete document").click()
            confirm = page.get_by_test_id("kb-confirm")
            await button(page, confirm, "Confirm delete").click()
            await expect(card(page, "doc-hours")).to_have_count(0)
            assert stub.deletes == ["kb/documents/doc-hours"]
            await expect(
                page.get_by_text("Deleted. Your assistant no longer uses this file.")
            ).to_be_visible()

    await with_browser(body)


async def test_the_attention_flow_works_on_the_admin_twin(site: Site) -> None:
    """The administrator's page shares these components; the tenant rides along on every call."""

    async def body(browser: Any) -> None:
        stub = attention_stub(count=2)
        async with knowledge_page(
            browser,
            site,
            stub,
            path=f"/knowledge-base?client_id={TENANT}",
            role="platform_admin",
            wait="kb-attention",
        ) as page:
            await expect(card(page, "job-0")).to_contain_text(FAILED_NOTE)
            await button(page, card(page, "job-0"), "Try again").click()
            await expect(page.get_by_test_id("kb-card-job-0-chip")).to_have_text("Reading")
            await button(page, card(page, "job-1"), "Remove").click()
            await expect(page.get_by_test_id("kb-confirm")).to_contain_text(REMOVE_CONFIRM)
            await button(page, page.get_by_test_id("kb-confirm"), "Remove").click()
            await expect(card(page, "job-1")).to_have_count(0)
            assert stub.posts == ["kb/ingest-jobs/job-0/retry", "kb/ingest-jobs/job-1/dismiss"]
            for verb in ("retry", "dismiss"):
                assert stub.queries[f"kb/ingest-jobs/job-{0 if verb == 'retry' else 1}/{verb}"][
                    -1
                ].get("client_id") == [TENANT]

    await with_browser(body)


async def test_try_again_on_a_job_whose_original_vanished_says_so_and_refreshes(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        stub = attention_stub(count=1)
        async with knowledge_page(browser, site, stub, wait="kb-attention") as page:
            await expect(button(page, card(page, "job-0"), "Try again")).to_be_enabled()
            # Another tab removed the original after this page loaded; the server now refuses.
            stub.jobs[0]["original_stored"] = False
            reads = stub.reads["jobs"]
            await button(page, card(page, "job-0"), "Try again").click()
            sentence = "The original was removed, so this can't be tried again. Remove it."
            await expect(page.get_by_text(sentence, exact=True).first).to_be_visible()
            await expect(page.get_by_text("original_removed")).to_have_count(0)
            await until(lambda: stub.reads["jobs"] > reads, "the list was not read again")
            # The refreshed card now offers Remove only.
            await expect(button(page, card(page, "job-0"), "Try again")).to_have_count(0)
            assert stub.posts == ["kb/ingest-jobs/job-0/retry"]

    await with_browser(body)
