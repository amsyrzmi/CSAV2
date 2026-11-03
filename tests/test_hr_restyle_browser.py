"""Real browser checks for the People & HR restyle (phase 4, stage 2).

The real console runs from the staged copy `test_services_redesign_browser.site` builds, as in
`test_team_browser.py`, whose THEMES / WIDTHS / overflow helpers this file reuses. Playwright
answers every browser-side `/console/v1/**` call from `RestyleWorld`, the synthetic HR company of
`test_hr_access_browser.py` (hr/context, hr/people, one profile with personal details and
documents, departments, org chart, the access page) plus the Team page's own calls. Later HR
tasks add their checks to this file.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from csa.workspace.contracts import (
    AnswerResult,
    ClarificationOption,
    ClarificationResult,
    ProposalResult,
    WorkspaceThreadOut,
    WorkspaceTurnOut,
)
from playwright.async_api import Page, expect

from tests import test_services_redesign_browser as services
from tests.test_hr_access_browser import (
    DOCUMENT_ID,
    EMPLOYEE,
    FILE_NAME,
    HR_PACKAGE,
    LEAVER,
    MANAGER,
    OWNER,
    PDF,
    STAFF,
    RecordsWorld,
)
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, make_me, make_service
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_team_browser import THEMES, WIDTHS, overflow
from tests.test_workspace_browser import training_proposal

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

PROFILE = f"/portal/hr/people/{EMPLOYEE[LEAVER]}"
DOCUMENT = {
    "document_id": DOCUMENT_ID,
    "kind": "contract",
    "file_name": FILE_NAME,
    "media_type": "application/pdf",
    "byte_size": len(PDF),
    "uploaded_at": "2026-11-02T02:00:00Z",
    "uploaded_by_name": "Nadia Hassan",
}

# The panel of a <dialog> against the theme's raised surface, and its text against that panel.
SURFACE_JS = """(selector) => {
    const parse = (v) => v.match(/[\\d.]+/g).map(Number);
    const channel = (v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    const lum = (c) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
    const ratio = (a, b) => {
        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
    };
    const dialog = [...document.querySelectorAll('dialog')].find((d) => d.open
        && d.matches(selector));
    const background = getComputedStyle(dialog).backgroundColor;
    const probe = document.createElement('div');
    probe.style.background = 'var(--fs-surface-raised)';
    document.body.appendChild(probe);
    const token = getComputedStyle(probe).backgroundColor;
    probe.remove();
    const bg = parse(background);
    const texts = [...dialog.querySelectorAll('h2, p, label')].map((el) => ({
        text: el.textContent.trim().slice(0, 40),
        ratio: ratio(parse(getComputedStyle(el).color), bg),
    }));
    const rect = dialog.getBoundingClientRect();
    return {
        background, token, texts,
        fits: rect.left >= -0.5 && rect.right <= innerWidth + 0.5 && rect.top >= -0.5
            && rect.bottom <= innerHeight + 0.5,
        scrim: getComputedStyle(dialog, '::backdrop').backgroundColor,
    };
}"""


class RestyleWorld(RecordsWorld):
    """The HR company with one document, plus the calls the Team page makes."""

    def __init__(self) -> None:
        super().__init__()
        self.documents = [dict(DOCUMENT)]

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if params.get("client_id") == TENANT and method == "GET":
            if path == "team":
                return 200, [
                    {
                        "user_id": user,
                        "client_id": TENANT,
                        "email": f"{row['name'].split()[0].lower()}@example.test",
                        "display_name": row["name"],
                        "role": "tenant_admin" if user == OWNER else "tenant_staff",
                        "is_enabled": True,
                    }
                    for user, row in self.people.items()
                ]
            if path == "team/hr-owner":
                owner = {"user_id": OWNER, "display_name": "Nadia Hassan", "usable": True}
                return 200, {"available": True, "owner": owner}
            if path == "admin/invites":
                return 200, []
        if path == "services/active" and method == "GET":
            return 200, {"packages": HR_PACKAGE}
        if path == "profile" and method == "GET":
            return 200, TENANT_PROFILE
        return super().answer(caller, method, path, params, body)


@asynccontextmanager
async def hr_page(
    browser: Any,
    site: Site,
    world: RestyleWorld,
    path: str,
    *,
    ready: Any,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    assistant: bool = False,
) -> AsyncIterator[Page]:
    """Open `path` as the tenant admin (the HR owner) and wait until `ready(page)` is visible.

    `assistant` turns the workspace assistant on in `/auth/me`, so the real dock is mounted.
    """
    site.upstream.me = {**make_me("tenant_admin"), "workspace_assistant": assistant}
    site.upstream.packages = HR_PACKAGE
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id="Asia/Kuala_Lumpur"
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", world.route_for(OWNER))
    await page.goto(f"{site.base}{path}")
    await expect(ready(page)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


def profile_tab(page: Page, name: str) -> Any:
    return page.get_by_role("tab", name=name, exact=True)


async def tabs_fit(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """() => {
            const tabs = [...document.querySelectorAll('[role=tab]')];
            return {
                count: tabs.length,
                tops: tabs.map((el) => Math.round(el.getBoundingClientRect().top)),
                out: tabs.filter((el) => {
                    const r = el.getBoundingClientRect();
                    return r.left < -0.5 || r.right > innerWidth + 0.5;
                }).map((el) => el.textContent.trim()),
            };
        }"""
    )


@pytest.mark.parametrize("theme", THEMES)
async def test_profile_tabs_wrap_without_overflow_at_five_widths_in_three_themes(
    site: Site, theme: str
) -> None:
    async def body(browser: Any) -> None:
        world = RestyleWorld()
        async with hr_page(
            browser, site, world, PROFILE, theme=theme, ready=lambda p: profile_tab(p, "Overview")
        ) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            tabs = page.get_by_role("tablist").get_by_role("tab")
            await expect(tabs).to_have_count(5)
            # Arrow keys move between the tabs and select them.
            await profile_tab(page, "Overview").focus()
            await page.keyboard.press("ArrowRight")
            await expect(profile_tab(page, "Personal")).to_be_focused()
            await expect(profile_tab(page, "Personal")).to_have_attribute("aria-selected", "true")
            await page.keyboard.press("ArrowLeft")
            await expect(profile_tab(page, "Overview")).to_have_attribute("aria-selected", "true")
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
                fit = await tabs_fit(page)
                assert fit["count"] == 5 and fit["out"] == [], (theme, width, fit)
                if width == 320:
                    assert len(set(fit["tops"])) > 1, (theme, width, fit)  # the row wrapped

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_remove_document_dialog_uses_theme_surface(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = RestyleWorld()
        async with hr_page(
            browser, site, world, PROFILE, theme=theme, ready=lambda p: profile_tab(p, "Overview")
        ) as page:
            await profile_tab(page, "Documents").click()
            remove = page.get_by_role("button", name=f"Remove {FILE_NAME}")
            await expect(remove).to_be_visible(timeout=WAIT)
            for width in (320, 1280):
                await page.set_viewport_size({"width": width, "height": 700})
                await remove.click()
                dialog = page.get_by_role("dialog", name="Remove this document?")
                await expect(dialog).to_be_visible()
                await expect(dialog).to_contain_text(FILE_NAME)
                await expect(dialog.get_by_role("button", name="Remove document")).to_be_visible()
                measured = await page.evaluate(SURFACE_JS, "*")
                assert measured["background"] == measured["token"], (theme, measured)
                if theme == "night":
                    assert measured["background"] != "rgb(255, 255, 255)", measured
                for item in measured["texts"]:
                    assert item["ratio"] >= 4.5, (theme, item)
                assert measured["fits"], (theme, width, measured)
                assert measured["scrim"] != "rgba(0, 0, 0, 0)", (theme, measured)
                # Cancel closes it and puts focus back on the button that opened it.
                await dialog.get_by_role("button", name="Cancel").click()
                await expect(dialog).to_have_count(0)
                await expect(remove).to_be_focused()
            await remove.click()
            await page.keyboard.press("Escape")
            await expect(page.get_by_role("dialog")).to_have_count(0)
            await expect(remove).to_be_focused()
            # Nothing was removed: the document is still listed and no delete was sent.
            await expect(page.get_by_role("cell", name=FILE_NAME, exact=True)).to_be_visible()
            assert not [c for c in world.calls if c["method"] == "DELETE"], world.calls

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_hr_owner_change_dialog_uses_theme_surface(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = RestyleWorld()
        assert MANAGER in world.people
        async with hr_page(
            browser,
            site,
            world,
            "/portal/team",
            theme=theme,
            ready=lambda p: p.get_by_test_id("hr-owner-panel"),
        ) as page:
            panel = page.get_by_test_id("hr-owner-panel")
            await expect(page.get_by_test_id("hr-owner-name")).to_have_text(
                "HR owner: Nadia Hassan"
            )
            change = panel.get_by_role("button", name="Change", exact=True)
            for width in (320, 1280):
                await page.set_viewport_size({"width": width, "height": 700})
                await change.click()
                dialog = page.get_by_role("dialog", name="Change the HR owner")
                await expect(dialog).to_be_visible()
                await expect(dialog.get_by_label("New HR owner")).to_be_visible()
                measured = await page.evaluate(SURFACE_JS, "*")
                assert measured["background"] == measured["token"], (theme, measured)
                if theme == "night":
                    assert measured["background"] != "rgb(255, 255, 255)", measured
                for item in measured["texts"]:
                    assert item["ratio"] >= 4.5, (theme, item)
                assert measured["fits"], (theme, width, measured)
                measured_overflow = await overflow(page)
                assert measured_overflow["scroll"] <= measured_overflow["inner"], measured_overflow
                await page.keyboard.press("Escape")
                await expect(dialog).to_have_count(0)
                await expect(change).to_be_focused()
            # Cancel also closes it; nothing was moved.
            await change.click()
            await page.get_by_role("dialog").get_by_role("button", name="Cancel").click()
            await expect(page.get_by_role("dialog")).to_have_count(0)
            await expect(change).to_be_focused()
            assert not [c for c in world.calls if c["method"] == "POST"], world.calls

    await with_browser(body)


# ---------------------------------------------------------------------------------------------
# The access page ("Who can do what", People & HR > Settings): theme tokens, and above the dock.
# ---------------------------------------------------------------------------------------------

ACCESS = "/portal/hr/settings"

# The assistant dock's launcher is fixed bottom-right at z-index 40, after the page in the DOM.
DOCK_JS = """() => {
    const pill = document.createElement('button');
    pill.id = 'dummy-dock';
    pill.textContent = 'Assistant';
    pill.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:40;width:132px;height:52px';
    document.body.appendChild(pill);
}"""

# Everything the access page paints that is pure white, and what the theme's tokens resolve to.
WHITE_JS = """() => {
    const title = [...document.querySelectorAll('h2')].find(
        (h) => h.textContent.trim() === 'Who can do what');
    const root = title.parentElement.parentElement;
    const probe = document.createElement('div');
    probe.style.background = 'var(--fs-surface-raised)';
    document.body.appendChild(probe);
    const raised = getComputedStyle(probe).backgroundColor;
    probe.style.background = 'var(--fs-scrim)';
    const scrim = getComputedStyle(probe).backgroundColor;
    probe.remove();
    const white = [root, ...root.querySelectorAll('*')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0
            && getComputedStyle(el).backgroundColor === 'rgb(255, 255, 255)';
    }).map((el) => el.dataset.testid || el.tagName + '.' + String(el.className).slice(0, 60));
    const save = document.querySelector('[data-testid=hr-access-save]');
    const bar = save ? save.parentElement.parentElement : null;
    const dialog = document.querySelector('[role=dialog]');
    return {
        white, raised, scrim,
        bar: bar ? getComputedStyle(bar).backgroundColor : null,
        overlay: dialog ? getComputedStyle(dialog.parentElement).backgroundColor : null,
    };
}"""

# Which buttons of the save bar and of the review dialog sit under the stand-in dock button.
COVERED_JS = """() => {
    const out = [];
    const save = document.querySelector('[data-testid=hr-access-save]');
    const buttons = [
        ...(save ? save.parentElement.parentElement.querySelectorAll('button') : []),
        ...document.querySelectorAll('[role=dialog] button'),
    ];
    for (const el of buttons) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.bottom < 0 || r.top > innerHeight) continue;
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
                    const hit = document.elementFromPoint(x, y);
        if (hit && hit.id === 'dummy-dock') out.push(el.textContent.trim());
    }
    return out;
}"""


def access_ready(page: Page) -> Any:
    return page.get_by_test_id(f"hr-access-row-{STAFF}")


async def make_access_change(page: Page) -> None:
    """Select Lina Wong and turn the directory switch on: one pending change, so the bar shows."""
    await page.get_by_test_id(f"hr-access-row-{STAFF}").get_by_role("button").click()
    panel = page.get_by_test_id("hr-access-panel")
    await panel.get_by_test_id("hr-access-switch-directory").check()
    await expect(page.get_by_test_id("hr-access-save")).to_be_visible()


async def test_access_page_has_no_white_panels_in_night(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser, site, RestyleWorld(), ACCESS, theme="night", ready=access_ready
        ) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", "night")
            await make_access_change(page)
            measured = await page.evaluate(WHITE_JS)
            assert measured["white"] == [], measured
            assert measured["bar"] == measured["raised"], measured
            # The review dialog: its overlay paints the scrim token (dark in Night), not ink.
            await page.get_by_test_id("hr-access-save").click()
            dialog = page.get_by_role("dialog")
            await expect(dialog).to_contain_text("Save 1 change for 1 person?", timeout=WAIT)
            measured = await page.evaluate(WHITE_JS)
            assert measured["white"] == [], measured
            assert measured["overlay"] == measured["scrim"], measured
            await dialog.get_by_role("button", name="Go back").click()
            await expect(dialog).to_have_count(0)

    await with_browser(body)


@pytest.mark.parametrize("width", (320, 390, 1024))
async def test_access_review_dialog_sits_above_the_dock(site: Site, width: int) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser, site, RestyleWorld(), ACCESS, width=width, height=800, ready=access_ready
        ) as page:
            await page.evaluate(DOCK_JS)
            await make_access_change(page)
            await page.get_by_test_id("hr-access-save").click()
            dialog = page.get_by_role("dialog")
            await expect(dialog).to_contain_text("Save 1 change for 1 person?", timeout=WAIT)
            confirm = page.get_by_test_id("hr-access-confirm")
            await expect(confirm).to_be_visible()
            # The overlay stacks above the dock's z-index 40.
            assert (
                await dialog.evaluate("(el) => getComputedStyle(el.parentElement).zIndex") == "50"
            )
            assert await page.evaluate(COVERED_JS) == [], width
            # The point at the middle of the confirm button belongs to the button, not the dock.
            owner = await confirm.evaluate(
                """(el) => {
                    const r = el.getBoundingClientRect();
                    const x = r.left + r.width / 2, y = r.top + r.height / 2;
                    return el.contains(document.elementFromPoint(x, y));
                }"""
            )
            assert owner, width
            await dialog.get_by_role("button", name="Go back").click()
            await expect(dialog).to_have_count(0)

    await with_browser(body)


@pytest.mark.parametrize("width", (320, 390))
async def test_save_bar_button_is_not_under_the_dock_launcher_at_320_and_390(
    site: Site, width: int
) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser, site, RestyleWorld(), ACCESS, width=width, height=700, ready=access_ready
        ) as page:
            await page.evaluate(DOCK_JS)
            await make_access_change(page)
            save = page.get_by_test_id("hr-access-save")
            discard = page.get_by_role("button", name="Discard changes")
            height = await page.evaluate("() => document.documentElement.scrollHeight")
            # From the top, a third of the way, and the very end of the page and of main.
            for fraction in (0, 0.3, 0.6, 1):
                await page.evaluate(f"() => window.scrollTo(0, {int(height * fraction)})")
                await page.evaluate(
                    f"() => {{ const m = document.querySelector('main');"
                    f" m?.scrollTo(0, m.scrollHeight * {fraction}); }}"
                )
                await page.wait_for_timeout(120)
                covered = await page.evaluate(COVERED_JS)
                assert covered == [], (width, fraction, covered)
            # At the end of the page the bar is still reachable, with its button on top.
            await save.scroll_into_view_if_needed()
            await expect(save).to_be_visible()
            await expect(discard).to_be_visible()
            box = await save.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, box

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_access_page_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser, site, RestyleWorld(), ACCESS, theme=theme, ready=access_ready
        ) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await page.evaluate(DOCK_JS)
            await make_access_change(page)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
                outside = await page.evaluate(
                    """() => [...document.querySelectorAll('[data-testid^="hr-access-"]')].filter(
                        (el) => {
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && (r.left < -0.5 || r.right > innerWidth + 0.5);
                        }).map((el) => el.dataset.testid)"""
                )
                assert outside == [], (theme, width, outside)
                white = await page.evaluate(WHITE_JS)
                if theme == "night":
                    assert white["white"] == [], (width, white)
                assert await page.evaluate(COVERED_JS) == [], (theme, width)

    await with_browser(body)


# ---------------------------------------------------------------------------------------------
# The workspace assistant dock and its proposal card: theme tokens, phones, three themes.
# ---------------------------------------------------------------------------------------------

DOCK_THREAD = UUID("33333333-3333-4333-8333-333333333333")
LONG_ANSWER = (
    "Open People & HR, choose People, then Add person. Fill in the name and the job title, "
    "pick a manager if they have one, and save. The new person appears in the list straight "
    "away. A very long reference such as HR-2026-11-03-" + "x" * 60 + " must wrap."
)
DOCK_TEAM_PATH = "/portal/team"


def dock_turns() -> list[dict[str, Any]]:
    """An answer, a proposal card awaiting a decision, then a clarification (the live question)."""
    now = datetime.now(UTC)
    options = [
        ClarificationOption(label="Aina Sofea, Operations Executive", value="aina-sofea"),
        ClarificationOption(label="Aina Rahman, Warehouse Supervisor, Penang", value="aina-rahman"),
    ]
    turns = [
        ("How do I add a person?", AnswerResult(action="answer", markdown=LONG_ANSWER)),
        (
            "Submit my training request",
            ProposalResult(action="proposal", proposal=training_proposal()),
        ),
        (
            "Change Aina's job title",
            ClarificationResult(
                action="clarification", question="Which Aina do you mean?", options=options
            ),
        ),
    ]
    return [
        WorkspaceTurnOut(
            id=index,
            request_key=UUID(f"77777777-7777-4777-8777-77777777777{index}"),
            body=body,
            result=result,
            created_at=now,
        ).model_dump(mode="json")
        for index, (body, result) in enumerate(turns, start=1)
    ]


class DockWorld(RestyleWorld):
    """The restyle company plus one workspace conversation with the three turns above."""

    def __init__(self) -> None:
        super().__init__()
        self.turns = dock_turns()

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if path == "workspace/conversations" and method == "GET":
            thread = WorkspaceThreadOut(id=DOCK_THREAD, sequence=1, created_at=datetime.now(UTC))
            return 200, {"items": [thread.model_dump(mode="json")], "next_cursor": None}
        if path == f"workspace/conversations/{DOCK_THREAD}/turns" and method == "GET":
            return 200, {"items": self.turns, "next_cursor": None}
        if path.startswith("workspace/proposals/") and path.endswith("/cancel"):
            # Leaving the page withdraws open cards; the stub accepts that without a body check.
            return 200, {**self.turns[1]["result"]["proposal"], "status": "cancelled"}
        return super().answer(caller, method, path, params, body)


# Measures the open dock panel: its colours against the theme's tokens, text contrast on the
# surface each line sits on, pure white anywhere, and whether anything leaves the viewport.
DOCK_MEASURE_JS = """() => {
    const parse = (v) => v.match(/[\\d.]+/g).map(Number);
    const channel = (v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    const lum = (c) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
    const ratio = (a, b) => {
        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
    };
    const token = (name, property) => {
        const probe = document.createElement('div');
        probe.style[property] = `var(${name})`;
        document.body.appendChild(probe);
        const value = getComputedStyle(probe)[property];
        probe.remove();
        return value;
    };
    const opaque = (el) => {
        for (; el; el = el.parentElement) {
            const c = parse(getComputedStyle(el).backgroundColor);
            if (c.length === 3 || c[3] > 0.99) return c;
        }
        return [255, 255, 255];
    };
    const panel = document.querySelector('[data-testid=assistant-panel]');
    const style = getComputedStyle(panel);
    const texts = [];
    for (const el of panel.querySelectorAll('*')) {
        if (el.closest('svg') || el.disabled || el.closest('button:disabled')) continue;
        const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
        const box = el.getBoundingClientRect();
        if (!own || box.width === 0) continue;
        texts.push({
            text: el.textContent.trim().slice(0, 40),
            ratio: ratio(parse(getComputedStyle(el).color), opaque(el)),
        });
    }
    const white = [panel, ...panel.querySelectorAll('*')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0
            && getComputedStyle(el).backgroundColor === 'rgb(255, 255, 255)'
            && !el.closest('svg');
    }).map((el) => el.dataset.testid || el.tagName + '.' + String(el.className).slice(0, 50));
    const rect = panel.getBoundingClientRect();
    const scroller = panel.querySelector('[aria-live=polite]');
    const bubble = panel.querySelector('[data-testid=assistant-turn] p');
    return {
        background: style.backgroundColor,
        raised: token('--fs-surface-raised', 'backgroundColor'),
        border: style.borderTopColor,
        borderToken: token('--fs-border', 'color'),
        bubble: bubble ? getComputedStyle(bubble).backgroundColor : null,
        blue: token('--fs-surface-blue', 'backgroundColor'),
        shadow: style.boxShadow,
        texts, white,
        rect: {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
               height: rect.height},
        inner: {width: innerWidth, height: innerHeight},
        sideways: [panel, scroller].filter((el) => el.scrollWidth > el.clientWidth + 0.5).length,
        avatars: [...panel.querySelectorAll('[data-agent=assistant]')].map((el) => ({
            state: el.dataset.state, width: el.getBoundingClientRect().width,
        })),
    };
}"""


async def open_dock(page: Page) -> Any:
    """Open the dock and wait for its three seeded turns; returns the panel."""
    await page.get_by_test_id("assistant-open").click()
    panel = page.get_by_test_id("assistant-panel")
    await expect(panel).to_be_visible()
    await expect(page.get_by_test_id("assistant-turn")).to_have_count(3, timeout=WAIT)
    return panel


def dock_ready(page: Page) -> Any:
    return page.get_by_test_id("assistant-open")


def check_dock_measure(measured: dict[str, Any], theme: str, width: int) -> None:
    where = (theme, width)
    assert measured["background"] == measured["raised"], (where, measured)
    assert measured["border"] == measured["borderToken"], (where, measured)
    assert measured["bubble"] == measured["blue"], (where, measured)
    assert measured["shadow"] != "none", (where, measured)
    assert measured["texts"], where
    for item in measured["texts"]:
        assert item["ratio"] >= 4.5, (where, item)
    if theme == "night":
        assert measured["background"] != "rgb(255, 255, 255)", (where, measured)
        assert measured["white"] == [], (where, measured)
    rect, inner = measured["rect"], measured["inner"]
    assert rect["left"] >= -0.5 and rect["right"] <= inner["width"] + 0.5, (where, measured)
    assert rect["top"] >= -0.5 and rect["bottom"] <= inner["height"] + 0.5, (where, measured)
    assert rect["height"] <= min(0.8 * inner["height"], 640) + 1, (where, measured)
    assert measured["sideways"] == 0, (where, measured)
    # Every reply sits beside the 32 px assistant, resting (nothing is being sent).
    assert [a["state"] for a in measured["avatars"]] == ["rest"] * 3, (where, measured)
    assert all(a["width"] == 32 for a in measured["avatars"]), (where, measured)


async def test_dock_panel_readable_in_night(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser,
            site,
            DockWorld(),
            DOCK_TEAM_PATH,
            theme="night",
            assistant=True,
            ready=dock_ready,
        ) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", "night")
            await open_dock(page)
            measured = await page.evaluate(DOCK_MEASURE_JS)
            check_dock_measure(measured, "night", 1280)
            # The user's question is a tinted bubble; the answer and the clarification chips
            # are on theme surfaces too (no white box inside the night panel).
            assert measured["white"] == [], measured
            await expect(
                page.get_by_role("button", name="Aina Sofea, Operations Executive")
            ).to_be_enabled()

    await with_browser(body)


@pytest.mark.parametrize("width,height", [(320, 568), (390, 664)])
async def test_dock_panel_fits_and_composer_reachable_at_320_and_390(
    site: Site, width: int, height: int
) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser,
            site,
            DockWorld(),
            DOCK_TEAM_PATH,
            width=width,
            height=height,
            assistant=True,
            ready=dock_ready,
        ) as page:
            panel = await open_dock(page)
            measured = await page.evaluate(DOCK_MEASURE_JS)
            check_dock_measure(measured, "cobalt", width)
            composer = page.get_by_test_id("assistant-input")
            send = page.get_by_test_id("assistant-send")
            close = panel.get_by_role("button", name="Close assistant")
            for target in (composer, send, close):
                await expect(target).to_be_in_viewport()
                box = await target.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
            # The turns scroll inside the panel; the composer stays under them and is not covered.
            scroller = panel.locator("[aria-live=polite]")
            await scroller.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
            await expect(composer).to_be_in_viewport()
            owner = await composer.evaluate(
                """(el) => {
                    const r = el.getBoundingClientRect();
                    const x = r.left + r.width / 2, y = r.top + r.height / 2;
                    const hit = document.elementFromPoint(x, y);
                    return el.contains(hit);
                }"""
            )
            assert owner, width
            await composer.fill("Hello")
            await expect(send).to_be_enabled()
            # The close button is at least 44 px square (a touch target) and closes the panel.
            close_box = await close.bounding_box()
            assert close_box and close_box["width"] >= 44 and close_box["height"] >= 44, close_box
            overflowing = await overflow(page)
            assert overflowing["scroll"] <= overflowing["inner"], (width, overflowing)

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_dock_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser,
            site,
            DockWorld(),
            DOCK_TEAM_PATH,
            theme=theme,
            assistant=True,
            ready=dock_ready,
        ) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await open_dock(page)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 700})
                await page.wait_for_timeout(150)
                check_dock_measure(await page.evaluate(DOCK_MEASURE_JS), theme, width)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
            # Closing the panel puts focus back on the launcher.
            await page.get_by_role("button", name="Close assistant").click()
            await expect(page.get_by_test_id("assistant-open")).to_be_focused()

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_proposal_card_uses_theme_surface(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        async with hr_page(
            browser,
            site,
            DockWorld(),
            DOCK_TEAM_PATH,
            width=320,
            height=700,
            theme=theme,
            assistant=True,
            ready=dock_ready,
        ) as page:
            await open_dock(page)
            card = page.get_by_test_id("proposal-card")
            await expect(card).to_have_count(1)
            await expect(card.get_by_test_id("proposal-confirm")).to_be_visible()
            await expect(card.get_by_test_id("proposal-cancel")).to_be_visible()
            measured = await card.evaluate(
                """(el) => {
                    const parse = (v) => v.match(/[\\d.]+/g).map(Number);
                    const channel = (v) => {
                        v /= 255;
                        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
                    };
                    const lum = (c) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1])
                        + 0.0722 * channel(c[2]);
                    const ratio = (a, b) => {
                        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
                        return (hi + 0.05) / (lo + 0.05);
                    };
                    const token = (name, property) => {
                        const probe = document.createElement('div');
                        probe.style[property] = `var(${name})`;
                        document.body.appendChild(probe);
                        const value = getComputedStyle(probe)[property];
                        probe.remove();
                        return value;
                    };
                    const style = getComputedStyle(el);
                    const bg = parse(style.backgroundColor);
                    const texts = [...el.querySelectorAll('h3, p, dt, dd')].map((t) => ({
                        text: t.textContent.trim().slice(0, 40),
                        ratio: ratio(parse(getComputedStyle(t).color), bg),
                    }));
                    return {
                        background: style.backgroundColor,
                        raised: token('--fs-surface-raised', 'backgroundColor'),
                        border: style.borderTopColor,
                        borderToken: token('--fs-border', 'color'),
                        texts,
                        sideways: el.scrollWidth > el.clientWidth + 0.5,
                        inside: el.getBoundingClientRect().right
                            <= document.querySelector('[data-testid=assistant-panel]')
                                .getBoundingClientRect().right + 0.5,
                    };
                }"""
            )
            assert measured["background"] == measured["raised"], (theme, measured)
            assert measured["border"] == measured["borderToken"], (theme, measured)
            if theme == "night":
                assert measured["background"] != "rgb(255, 255, 255)", measured
            for item in measured["texts"]:
                assert item["ratio"] >= 4.5, (theme, item)
            assert not measured["sideways"] and measured["inside"], (theme, measured)

    await with_browser(body)


async def test_dock_shows_the_working_avatar_while_a_message_is_sending(site: Site) -> None:
    async def body(browser: Any) -> None:
        release = asyncio.Event()
        world = DockWorld()
        async with hr_page(
            browser, site, world, DOCK_TEAM_PATH, assistant=True, ready=dock_ready
        ) as page:

            async def slow_turn(route: Any) -> None:
                request = route.request
                if request.method != "POST":
                    await route.fallback()
                    return
                await release.wait()
                sent = request.post_data_json
                turn = WorkspaceTurnOut(
                    id=4,
                    request_key=UUID(sent["request_key"]),
                    body=sent["body"],
                    result=AnswerResult(action="answer", markdown="Done."),
                    created_at=datetime.now(UTC),
                )
                await route.fulfill(status=200, json=turn.model_dump(mode="json"))

            await page.route(
                f"**/console/v1/workspace/conversations/{DOCK_THREAD}/turns*", slow_turn
            )
            try:
                panel = await open_dock(page)
                await expect(page.get_by_test_id("assistant-working")).to_have_count(0)
                await page.get_by_test_id("assistant-input").fill("And then?")
                await page.get_by_test_id("assistant-send").click()
                working = page.get_by_test_id("assistant-working")
                await expect(working).to_be_visible()
                await expect(working).to_contain_text("Sending…")
                avatar = working.locator("[data-agent=assistant]")
                await expect(avatar).to_have_attribute("data-state", "working")
                assert (await avatar.bounding_box() or {})["width"] == 32
                # The Overview chat's own working row is a different element and is not reused.
                await expect(page.get_by_test_id("chat-working")).to_have_count(0)
            finally:
                release.set()
            await expect(working).to_have_count(0, timeout=WAIT)
            await expect(panel.get_by_test_id("assistant-turn")).to_have_count(4)

    await with_browser(body)


# ---------------------------------------------------------------------------------------------
# Confirm steps: Mark as leaving (inline question) and Disconnect number (ConfirmAction).
# ---------------------------------------------------------------------------------------------

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
LEAVING_SENTENCE = (
    "Their access to this company ends after the last day, and you can choose Undo leaving "
    "on their profile until the end of the last day."
)


def next_week() -> Any:
    """A last day a week ahead, in Kuala Lumpur, as the form's date value and as shown."""
    day = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date() + timedelta(days=7)
    return day, f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def leaving_calls(world: RestyleWorld) -> list[dict[str, Any]]:
    return [c for c in world.calls if c["path"] == "hr/operations"]


def mark_as_leaving(page: Page) -> Any:
    return page.get_by_role("button", name="Mark as leaving", exact=True)


async def ask_to_mark_as_leaving(page: Page, last_day: Any) -> None:
    """Open the form, choose the last day and submit it: the question opens, nothing is sent."""
    await mark_as_leaving(page).click()
    await expect(page.get_by_text("Mark Lina Wong as leaving")).to_be_visible(timeout=WAIT)
    await page.locator("input[name='last_day']").fill(last_day.isoformat())
    await mark_as_leaving(page).click()
    await expect(page.get_by_test_id("hr-leaving-confirm-question")).to_be_visible()


async def test_mark_as_leaving_asks_first_and_cancel_sends_nothing(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = RestyleWorld()
        last_day, shown = next_week()
        async with hr_page(
            browser, site, world, PROFILE, ready=lambda p: profile_tab(p, "Overview")
        ) as page:
            await ask_to_mark_as_leaving(page, last_day)
            question = page.get_by_test_id("hr-leaving-confirm-question")
            await expect(question).to_have_text(
                f"Lina Wong will be marked as leaving, last day {shown}. {LEAVING_SENTENCE}"
            )
            cancel = page.get_by_test_id("hr-leaving-cancel")
            await expect(cancel).to_be_focused()
            await expect(page.get_by_test_id("hr-leaving-confirm")).to_have_text(
                "Yes, mark as leaving"
            )
            # The form is locked while it asks, so what is confirmed is what was submitted.
            await expect(page.locator("input[name='last_day']")).to_be_disabled()
            assert leaving_calls(world) == []
            # The question fits a phone.
            await page.set_viewport_size({"width": 320, "height": 700})
            measured = await overflow(page)
            assert measured["scroll"] <= measured["inner"], measured
            confirm = page.get_by_test_id("hr-leaving-confirm")
            await confirm.scroll_into_view_if_needed()
            box = await confirm.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 320, box
            await page.set_viewport_size({"width": 1280, "height": 1000})
            # Cancel closes the question, puts focus on the submit button and sends nothing.
            await cancel.click()
            await expect(question).to_have_count(0)
            await expect(mark_as_leaving(page)).to_be_focused()
            await expect(page.locator("input[name='last_day']")).to_have_value(last_day.isoformat())
            assert leaving_calls(world) == []
            # Escape does the same from the question.
            await mark_as_leaving(page).click()
            await expect(cancel).to_be_focused()
            await page.keyboard.press("Escape")
            await expect(question).to_have_count(0)
            await expect(mark_as_leaving(page)).to_be_focused()
            assert leaving_calls(world) == []
            assert world.last_day is None

    await with_browser(body)


async def test_mark_as_leaving_confirm_sends_once(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = RestyleWorld()
        last_day, _ = next_week()
        async with hr_page(
            browser, site, world, PROFILE, ready=lambda p: profile_tab(p, "Overview")
        ) as page:
            await ask_to_mark_as_leaving(page, last_day)
            assert leaving_calls(world) == []
            # A double click on "Yes, mark as leaving" is one write.
            await page.get_by_test_id("hr-leaving-confirm").dblclick()
            await expect(page.get_by_text("Marked as leaving.")).to_be_visible(timeout=WAIT)
            [leave] = leaving_calls(world)
            assert leave["body"] == {
                "operation": "person.leave",
                "employee_id": EMPLOYEE[LEAVER],
                "expected_version": 3,
                "last_day": last_day.isoformat(),
                "reason": "resigned",
                "reports_new_manager": None,
            }, leave["body"]
            assert leave["key"], "the leave carries an Idempotency-Key"
            assert world.last_day == last_day.isoformat()

    await with_browser(body)


class WhatsAppWorld(RestyleWorld):
    """The restyle company with a connected WhatsApp number that DELETE disconnects."""

    def __init__(self) -> None:
        super().__init__()
        self.connected = True

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if path == "services" and method == "GET":
            return 200, [make_service("customer_support", "Customer Support", "base", "active")]
        if path == "channels/whatsapp" and method == "GET":
            return 200, {
                "connected": self.connected,
                "display_phone": "+60 12-345 6789" if self.connected else None,
                "phone_number_id_last4": "0987" if self.connected else None,
                "waba_id": "208765432109876" if self.connected else None,
                "enabled": self.connected,
                "callback_url": "https://hooks.invalid/channels/whatsapp",
                "connected_at": "2026-10-27T00:00:00Z" if self.connected else None,
            }
        if path == "channels/whatsapp" and method == "DELETE":
            self.connected = False
            return 200, {}
        return super().answer(caller, method, path, params, body)


@pytest.mark.parametrize("width", (320, 1280))
async def test_whatsapp_disconnect_asks_inline_and_cancel_keeps_the_number(
    site: Site, width: int
) -> None:
    async def body(browser: Any) -> None:
        world = WhatsAppWorld()
        async with hr_page(
            browser,
            site,
            world,
            "/portal/services",
            width=width,
            ready=lambda p: p.get_by_test_id("whatsapp-connected"),
        ) as page:
            deletes = lambda: [c for c in world.calls if c["method"] == "DELETE"]  # noqa: E731
            trigger = page.get_by_test_id("whatsapp-disconnect")
            await expect(trigger).to_have_text("Disconnect number")
            await trigger.click()
            ask = page.get_by_test_id("whatsapp-disconnect-ask")
            # No browser dialog: the same question, inline, with Cancel focused.
            await expect(ask).to_contain_text(
                "Disconnect this number? Customers will not be able to message you on WhatsApp."
            )
            await expect(page.get_by_test_id("whatsapp-disconnect-cancel")).to_be_focused()
            measured = await overflow(page)
            assert measured["scroll"] <= measured["inner"], (width, measured)
            # Cancel and Escape keep the number and send nothing; focus returns to the button.
            await page.get_by_test_id("whatsapp-disconnect-cancel").click()
            await expect(ask).to_have_count(0)
            await expect(trigger).to_be_focused()
            await trigger.click()
            await page.keyboard.press("Escape")
            await expect(ask).to_have_count(0)
            await expect(trigger).to_be_focused()
            assert deletes() == [] and world.connected
            await expect(page.get_by_test_id("whatsapp-connected")).to_be_visible()
            # Confirm disconnects it, once.
            await trigger.click()
            await page.get_by_test_id("whatsapp-disconnect-confirm").click()
            await expect(page.get_by_test_id("whatsapp-connected")).to_have_count(0, timeout=WAIT)
            assert len(deletes()) == 1 and not world.connected

    await with_browser(body)


# ---------------------------------------------------------------------------------------------
# HR lists: cards below 768 px, the real table from 768 px up.
# ---------------------------------------------------------------------------------------------

PEOPLE = "/portal/hr/people"
DEPARTMENTS = "/portal/hr/people/departments"
DEPARTMENT_LONG = "Warehouse, Distribution and Cross-Border Operations Department"
DEPARTMENT_IDS = (
    "88888888-8888-4888-8888-000000000001",
    "88888888-8888-4888-8888-000000000002",
)


class ListsWorld(RestyleWorld):
    """The restyle company with two departments, one with a very long name."""

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if path == "hr/departments" and method == "GET" and params.get("client_id") == TENANT:
            return 200, {
                "items": [
                    {
                        "department_id": DEPARTMENT_IDS[0],
                        "name": DEPARTMENT_LONG,
                        "archived": False,
                        "version": 1,
                        "people": 3,
                    },
                    {
                        "department_id": DEPARTMENT_IDS[1],
                        "name": "Finance",
                        "archived": True,
                        "version": 2,
                        "people": 0,
                    },
                ]
            }
        return super().answer(caller, method, path, params, body)


# Each list: (table test id, cards test id, card test id, page path, optional tab to open).
LISTS = {
    "people": ("hr-people-table", "hr-people-cards", "hr-person-card", PEOPLE, None),
    "documents": (
        "hr-documents-table",
        "hr-documents-cards",
        "hr-document-card",
        PROFILE,
        "Documents",
    ),
    "departments": (
        "hr-departments-table",
        "hr-departments-cards",
        "hr-department-card",
        DEPARTMENTS,
        None,
    ),
}

# The display of a list's table and cards, the cards against the theme's tokens, and any card
# that leaves the viewport.
LIST_JS = """([tableId, cardsId, cardId]) => {
    const parse = (v) => v.match(/[\\d.]+/g).map(Number);
    const channel = (v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    const lum = (c) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
    const ratio = (a, b) => {
        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
    };
    const token = (name, property) => {
        const probe = document.createElement('div');
        probe.style[property] = `var(${name})`;
        document.body.appendChild(probe);
        const value = getComputedStyle(probe)[property];
        probe.remove();
        return value;
    };
    const table = document.querySelector(`[data-testid=${tableId}]`);
    const list = document.querySelector(`[data-testid=${cardsId}]`);
    const cards = [...document.querySelectorAll(`[data-testid=${cardId}]`)];
    const shown = cards.filter((el) => el.getBoundingClientRect().width > 0);
    const texts = shown.flatMap((card) => {
        const bg = parse(getComputedStyle(card).backgroundColor);
        return [...card.querySelectorAll('p, dt, dd, a, button')].map((el) => ({
            text: el.textContent.trim().slice(0, 40),
            ratio: ratio(parse(getComputedStyle(el).color), bg),
        }));
    });
    return {
        table: getComputedStyle(table).display,
        list: getComputedStyle(list).display,
        cards: cards.length,
        shown: shown.length,
        outside: shown.filter((el) => {
            const r = el.getBoundingClientRect();
            return r.left < -0.5 || r.right > innerWidth + 0.5;
        }).length,
        sideways: shown.filter((el) => el.scrollWidth > el.clientWidth + 0.5).length,
        backgrounds: [...new Set(shown.map((el) => getComputedStyle(el).backgroundColor))],
        surface: token('--fs-surface', 'backgroundColor'),
        borders: [...new Set(shown.map((el) => getComputedStyle(el).borderTopColor))],
        borderToken: token('--fs-border', 'color'),
        texts,
    };
}"""

LIST_WIDTHS = ((320, False), (390, False), (767, False), (768, True), (1024, True), (1440, True))


@asynccontextmanager
async def open_list(
    browser: Any,
    site: Site,
    name: str,
    world: RestyleWorld,
    *,
    theme: str | None = None,
) -> AsyncIterator[Page]:
    """One HR list page at 1280 px, opened on the list's own tab."""
    table, _, _, path, tab = LISTS[name]
    ready = (lambda p: profile_tab(p, "Documents")) if tab else (lambda p: p.get_by_test_id(table))
    async with hr_page(browser, site, world, path, theme=theme, ready=ready) as page:
        if tab:
            await profile_tab(page, tab).click()
            await expect(page.get_by_test_id(table)).to_be_visible(timeout=WAIT)
        yield page


async def check_list(page: Page, name: str, width: int, table_shown: bool, theme: str) -> None:
    """The table and the cards swap at 768 px; the cards fit and read on the theme's surface."""
    table_id, cards_id, card_id, _, _ = LISTS[name]
    where = (name, theme, width)
    await page.set_viewport_size({"width": width, "height": 900})
    await page.wait_for_timeout(120)
    measured = await page.evaluate(LIST_JS, [table_id, cards_id, card_id])
    if table_shown:
        assert measured["table"] == "table" and measured["list"] == "none", (where, measured)
        assert measured["shown"] == 0, (where, measured)
        await expect(page.get_by_role("table")).to_have_count(1)
    else:
        assert measured["table"] == "none" and measured["list"] != "none", (where, measured)
        assert measured["shown"] == measured["cards"] > 0, (where, measured)
        # Out of the accessibility tree: no table or row below 768 px.
        await expect(page.get_by_role("table")).to_have_count(0)
        await expect(page.get_by_role("row")).to_have_count(0)
        assert measured["outside"] == 0 and measured["sideways"] == 0, (where, measured)
        assert measured["backgrounds"] == [measured["surface"]], (where, measured)
        assert measured["borders"] == [measured["borderToken"]], (where, measured)
        if theme == "night":
            assert measured["backgrounds"] != ["rgb(255, 255, 255)"], (where, measured)
        for item in measured["texts"]:
            assert item["ratio"] >= 4.5, (where, item)
    scrolled = await overflow(page)
    assert scrolled["scroll"] <= scrolled["inner"], (where, scrolled)


async def test_people_list_is_cards_below_768_and_a_table_above(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with open_list(browser, site, "people", ListsWorld()) as page:
            for width, table_shown in LIST_WIDTHS:
                await check_list(page, "people", width, table_shown, "cobalt")
            # A card has the same facts as a row, and its name links to the profile.
            await page.set_viewport_size({"width": 390, "height": 900})
            card = page.get_by_test_id("hr-person-card").filter(has_text="Lina Wong")
            await expect(card).to_have_count(1)
            for label in ("Title", "Department", "Manager", "Status"):
                await expect(card.locator("dt", has_text=label)).to_be_visible()
            await expect(card).to_contain_text("Active")
            await card.get_by_role("link", name="Lina Wong").click()
            await expect(page).to_have_url(re.compile(rf"{PROFILE}$"), timeout=WAIT)

    await with_browser(body)


async def test_documents_list_is_cards_below_768_and_a_table_above(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with open_list(browser, site, "documents", ListsWorld()) as page:
            for width, table_shown in LIST_WIDTHS:
                await check_list(page, "documents", width, table_shown, "cobalt")
            await page.set_viewport_size({"width": 390, "height": 900})
            card = page.get_by_test_id("hr-document-card")
            await expect(card).to_have_count(1)
            await expect(card).to_contain_text(FILE_NAME)
            await expect(card).to_contain_text("Nadia Hassan")
            # The same download link and Remove button, one tap away.
            download = card.get_by_role("link", name=f"Download {FILE_NAME}")
            await expect(download).to_have_attribute("href", re.compile(DOCUMENT_ID))
            await card.get_by_role("button", name=f"Remove {FILE_NAME}").click()
            dialog = page.get_by_role("dialog", name="Remove this document?")
            await expect(dialog).to_be_visible()
            await dialog.get_by_role("button", name="Cancel").click()
            await expect(card.get_by_role("button", name=f"Remove {FILE_NAME}")).to_be_focused()
            # At 1280 the real table is back, with its cells.
            await page.set_viewport_size({"width": 1280, "height": 900})
            await expect(page.get_by_role("cell", name=FILE_NAME, exact=True)).to_be_visible()

    await with_browser(body)


async def test_departments_list_is_cards_below_768_and_a_table_above(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = ListsWorld()
        async with open_list(browser, site, "departments", world) as page:
            for width, table_shown in LIST_WIDTHS:
                await check_list(page, "departments", width, table_shown, "cobalt")
            await page.set_viewport_size({"width": 390, "height": 900})
            cards = page.get_by_test_id("hr-department-card")
            await expect(cards).to_have_count(2)
            first = cards.filter(has_text=DEPARTMENT_LONG)
            await expect(first).to_contain_text("In use")
            await expect(cards.filter(has_text="Finance")).to_contain_text("Archived")
            await expect(
                first.get_by_role("button", name=f"Rename {DEPARTMENT_LONG}")
            ).to_be_visible()
            await expect(
                cards.filter(has_text="Finance").get_by_role("button", name="Unarchive Finance")
            ).to_be_visible()
            # Rename opens its form in the card; Cancel closes it. Nothing was written.
            await first.get_by_role("button", name=f"Rename {DEPARTMENT_LONG}").click()
            name_field = first.locator("input[name='name']")
            await expect(name_field).to_be_visible()
            await expect(name_field).to_have_value(DEPARTMENT_LONG)
            box = await name_field.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, box
            await first.get_by_role("button", name="Cancel").click()
            await expect(name_field).to_have_count(0)
            assert not [c for c in world.calls if c["method"] != "GET"], world.calls

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_hr_lists_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        for name in LISTS:
            async with open_list(browser, site, name, ListsWorld(), theme=theme) as page:
                await expect(page.locator("html")).to_have_attribute("data-theme", theme)
                for width in WIDTHS:
                    await check_list(page, name, width, width >= 768, theme)

    await with_browser(body)
