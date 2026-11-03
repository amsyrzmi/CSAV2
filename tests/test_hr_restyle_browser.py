"""Real browser checks for the People & HR restyle (phase 4, stage 2).

The real console runs from the staged copy `test_services_redesign_browser.site` builds, as in
`test_team_browser.py`, whose THEMES / WIDTHS / overflow helpers this file reuses. Playwright
answers every browser-side `/console/v1/**` call from `RestyleWorld`, the synthetic HR company of
`test_hr_access_browser.py` (hr/context, hr/people, one profile with personal details and
documents, departments, org chart, the access page) plus the Team page's own calls. Later HR
tasks add their checks to this file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
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
    RecordsWorld,
)
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_team_browser import THEMES, WIDTHS, overflow

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
) -> AsyncIterator[Page]:
    """Open `path` as the tenant admin (the HR owner) and wait until `ready(page)` is visible."""
    site.upstream.me, site.upstream.packages = make_me("tenant_admin"), HR_PACKAGE
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
