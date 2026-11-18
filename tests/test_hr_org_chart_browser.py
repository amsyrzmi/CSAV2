"""Real browser checks for the org chart view (HR PR 4, Task 4).

The staged console runs from the copy `test_services_redesign_browser.site` builds, as in
`test_hr_restyle_browser.py`. Playwright answers every browser-side `/console/v1/**` call from
`OrgWorld`: the synthetic HR company with an org chart of its own (12 named people in three
departments, one reporting loop, one person whose manager left, one starting and one leaving
person, a legal entity on some, and a warehouse team of 40). All names and values are synthetic.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from playwright.async_api import Locator, Page, expect

from tests import test_services_redesign_browser as services
from tests.test_hr_access_browser import HR_PACKAGE, OWNER, STAFF
from tests.test_hr_restyle_browser import RestyleWorld
from tests.test_services_browser import TENANT, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_team_browser import THEMES, WIDTHS, overflow

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

CHART = "/portal/hr/people/org-chart"
OPS, WAREHOUSE, FINANCE = (f"88888888-8888-4888-8888-00000000010{n}" for n in (1, 2, 3))
SERBANEKA = "Perniagaan Serbaneka Sdn Bhd"
MAWAR = "Kedai Mawar Enterprise"
TEAM = 40


def eid(number: int) -> str:
    return f"77777777-7777-4777-8777-{number:012d}"


def node(
    number: int,
    name: str,
    title: str | None,
    department: tuple[str, str] | None,
    manager: str | None,
    *,
    status: str = "active",
    start: str | None = "2025-01-06",
    last_day: str | None = None,
    entity: str | None = None,
    outside: bool = False,
) -> dict[str, Any]:
    """One org node as the server sends it: `manager_id` is null for a manager who is not on the
    chart (then `outside` is true) and for everyone in a reporting loop."""
    return {
        "employee_id": eid(number),
        "display_name": name,
        "job_title": title,
        "department_id": department[0] if department else None,
        "department": department[1] if department else None,
        "manager_id": manager,
        "manager_outside": outside,
        "status": status,
        "start_date": start,
        "last_day": last_day,
        "legal_entity_reference": entity,
    }


class OrgWorld(RestyleWorld):
    """The HR company, with the org chart `nodes` and the ids the server reports as `looped`."""

    def __init__(self, entities: bool = True, team: int = TEAM) -> None:
        super().__init__()
        ops, wh, fin = (OPS, "Operations"), (WAREHOUSE, "Warehouse"), (FINANCE, "Finance")
        e1 = SERBANEKA if entities else None
        e2 = MAWAR if entities else None
        self.nodes = [
            node(1, "Nadia Hassan", "Owner", ops, None, entity=e1),
            node(2, "Farid Ismail", "Warehouse supervisor", wh, eid(1), entity=e1),
            node(3, "Lina Wong", "Packer", wh, eid(2), status="starting", start="2026-12-01"),
            node(4, "Hana Zain", "Finance lead", fin, eid(1), entity=e1),
            node(
                5,
                "Rizal Omar",
                "Accountant",
                fin,
                eid(4),
                status="leaving",
                last_day="2026-12-31",
                entity=e2,
            ),
            node(6, "Siti Rahman", "Operations manager", ops, eid(1)),
            node(7, "Aiman Yusof", "Coordinator", ops, eid(6)),
            node(8, "Mei Ling Tan", "Coordinator", ops, eid(6)),
            node(9, "Kumar Raj", "Coordinator", ops, eid(6)),
            node(10, "Edwin Tan", "Picker", wh, None),
            node(11, "Priya Nair", "Picker", wh, None),
            node(12, "=Zul Karim", "Driver", wh, None, outside=True),
        ]
        for n in range(team):
            self.nodes.append(node(100 + n, f"Associate {n:03d}", "Associate", wh, eid(2)))
        self.looped = [eid(10), eid(11)]

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if path == "hr/org-chart" and method == "GET" and params.get("client_id") == TENANT:
            return 200, {"nodes": self.nodes, "looped": self.looped}
        return super().answer(caller, method, path, params, body)


@asynccontextmanager
async def org_page(
    browser: Any,
    site: Site,
    world: OrgWorld,
    *,
    caller: str = OWNER,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
) -> AsyncIterator[Page]:
    """The org chart signed in as `caller` (the HR owner by default) once its heading shows."""
    me = make_me("tenant_admin" if caller == OWNER else "tenant_staff")
    me["user"]["id"] = caller
    site.upstream.me = {**me, "workspace_assistant": False}
    site.upstream.packages = HR_PACKAGE
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        timezone_id="Asia/Kuala_Lumpur",
        accept_downloads=True,
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    # Count window.print() instead of opening the print dialog.
    await page.add_init_script(
        "window.__prints = 0; window.print = () => { window.__prints += 1; };"
    )
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", world.route_for(caller))
    await page.goto(f"{site.base}{CHART}")
    await expect(page.get_by_role("heading", name="Org chart")).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


def tree(page: Page) -> Locator:
    return page.get_by_role("list", name="Org chart")


def row(page: Page, name: str) -> Locator:
    return page.get_by_test_id("org-row").filter(
        has=page.get_by_role("link", name=name, exact=True)
    )


def count_text(page: Page) -> Locator:
    return page.get_by_test_id("org-count")


def branch_button(page: Page, name: str) -> Locator:
    return page.get_by_role(
        "button", name=re.compile(rf"^(Collapse|Expand) {re.escape(name)}'s team")
    )


async def test_every_person_shows_with_department_markers_and_labels(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world) as page:
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")
            # Nobody is dropped: every person is a profile link, loop and orphan included.
            await expect(tree(page).get_by_role("link")).to_have_count(len(world.nodes))
            for item in world.nodes:
                await expect(row(page, item["display_name"])).to_be_visible()

            farid = row(page, "Farid Ismail")
            await expect(farid).to_contain_text("Warehouse supervisor")
            chip = farid.get_by_test_id("org-department")
            await expect(chip).to_have_text("Warehouse")
            assert await chip.get_attribute("data-tint") in {"1", "2", "3", "4", "5", "6"}
            style = await chip.get_attribute("style") or ""
            assert re.search(r"var\(--fs-dept-[1-6]-bg\)", style), style
            assert re.search(r"var\(--fs-dept-[1-6]-text\)", style), style
            # The same department keeps its tint across people.
            tint = await chip.get_attribute("data-tint")
            lina_chip = row(page, "Lina Wong").get_by_test_id("org-department")
            assert await lina_chip.get_attribute("data-tint") == tint

            await expect(row(page, "Lina Wong").get_by_test_id("org-marker")).to_have_text(
                "Starting"
            )
            await expect(row(page, "Rizal Omar").get_by_test_id("org-marker")).to_have_text(
                "Leaving"
            )
            await expect(page.get_by_test_id("org-marker")).to_have_count(2)

            await expect(row(page, "Nadia Hassan")).to_contain_text("No manager")
            await expect(row(page, "=Zul Karim")).to_contain_text("Manager not in the chart")
            await expect(row(page, "Farid Ismail")).not_to_contain_text("No manager")
            await expect(row(page, "Farid Ismail")).not_to_contain_text("Manager not in the chart")
            # Both members of a loop have a null manager from the server; they are not "No manager".
            for name in ("Edwin Tan", "Priya Nair"):
                await expect(row(page, name)).to_contain_text("In a reporting loop")
                await expect(row(page, name)).not_to_contain_text("No manager")
            await expect(row(page, "=Zul Karim")).not_to_contain_text("No manager")
            await expect(row(page, "Nadia Hassan")).not_to_contain_text("In a reporting loop")

            # The loop notice names the pair, for the owner.
            notice = page.get_by_role("alert").filter(has_text="each other's managers in a loop")
            await expect(notice).to_contain_text("Edwin Tan, Priya Nair")

    await with_browser(body)


async def test_loop_notice_is_for_the_owner_only(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        world.people[STAFF]["on"] = ["own_requests", "directory"]
        async with org_page(browser, site, world, caller=STAFF) as page:
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")
            await expect(page.get_by_text("each other's managers in a loop")).to_have_count(0)
            await expect(row(page, "Edwin Tan")).to_contain_text("In a reporting loop")
            await expect(row(page, "Edwin Tan")).to_be_visible()

    await with_browser(body)


async def test_branches_collapse_with_truthful_state_and_keyboard(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world) as page:
            siti = page.get_by_role("button", name="Collapse Siti Rahman's team (3)")
            await expect(siti).to_have_attribute("aria-expanded", "true")
            controlled = await siti.get_attribute("aria-controls")
            assert controlled
            assert await page.locator(f"[id='{controlled}']").count() == 1
            await expect(row(page, "Aiman Yusof")).to_be_visible()

            await siti.click()
            expand = page.get_by_role("button", name="Expand Siti Rahman's team (3)")
            await expect(expand).to_have_attribute("aria-expanded", "false")
            for name in ("Aiman Yusof", "Mei Ling Tan", "Kumar Raj"):
                await expect(row(page, name)).to_be_hidden()
            await expect(row(page, "Siti Rahman")).to_be_visible()
            # The count is the chart, not what is folded away.
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")

            await expand.focus()
            await page.keyboard.press("Enter")
            await expect(branch_button(page, "Siti Rahman")).to_have_attribute(
                "aria-expanded", "true"
            )
            await expect(row(page, "Aiman Yusof")).to_be_visible()
            await expect(branch_button(page, "Siti Rahman")).to_be_focused()
            await page.keyboard.press("Space")
            await expect(branch_button(page, "Siti Rahman")).to_have_attribute(
                "aria-expanded", "false"
            )
            await expect(row(page, "Aiman Yusof")).to_be_hidden()
            await expect(branch_button(page, "Siti Rahman")).to_be_focused()

            # Someone with no reports has no button.
            await expect(branch_button(page, "Lina Wong")).to_have_count(0)

            await page.get_by_role("button", name="Collapse all").click()
            await expect(row(page, "Farid Ismail")).to_be_hidden()
            await expect(row(page, "Nadia Hassan")).to_be_visible()
            await expect(
                tree(page).get_by_role("link").and_(page.locator(":visible"))
            ).to_have_count(4)
            await page.get_by_role("button", name="Expand all").click()
            await expect(
                tree(page).get_by_role("link").and_(page.locator(":visible"))
            ).to_have_count(len(world.nodes))

    await with_browser(body)


async def test_default_view_is_open_two_levels_deep(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        # Nadia > Siti > Aiman > a person two levels below Siti's reports.
        world.nodes.append(node(200, "Deep Report", "Trainee", (OPS, "Operations"), eid(7)))
        async with org_page(browser, site, world) as page:
            await expect(row(page, "Aiman Yusof")).to_be_visible()
            await expect(row(page, "Deep Report")).to_be_hidden()
            await expect(branch_button(page, "Aiman Yusof")).to_have_attribute(
                "aria-expanded", "false"
            )
            await branch_button(page, "Aiman Yusof").click()
            await expect(row(page, "Deep Report")).to_be_visible()
            # Everyone is still counted while a branch is folded.
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")

    await with_browser(body)


async def test_filters_keep_the_manager_chain_and_the_fold_state(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world) as page:
            await page.get_by_role("button", name="Collapse Siti Rahman's team (3)").click()

            await page.get_by_label("Department").select_option(label="Finance")
            await expect(count_text(page)).to_have_text("3 people shown")
            for name in ("Nadia Hassan", "Hana Zain", "Rizal Omar"):
                await expect(row(page, name)).to_be_visible()
            await expect(row(page, "Farid Ismail")).to_have_count(0)

            await page.get_by_label("Department").select_option(label="All departments")
            await page.get_by_label("Search people").fill("rizal")
            await expect(count_text(page)).to_have_text("3 people shown")
            await expect(row(page, "Hana Zain")).to_be_visible()
            await page.get_by_label("Search people").fill("coordinator")
            # A job title matches too, with Siti and Nadia above the three coordinators.
            await expect(count_text(page)).to_have_text("5 people shown")

            await page.get_by_label("Search people").fill("")
            await page.get_by_label("Legal entity").select_option(label=MAWAR)
            await expect(count_text(page)).to_have_text("3 people shown")
            await expect(row(page, "Rizal Omar")).to_be_visible()
            await page.get_by_label("Legal entity").select_option(label="All legal entities")

            # The fold from before the filters is still there.
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")
            await expect(branch_button(page, "Siti Rahman")).to_have_attribute(
                "aria-expanded", "false"
            )
            await expect(row(page, "Aiman Yusof")).to_be_hidden()

    await with_browser(body)


async def test_filtering_opens_branches_without_touching_the_folds(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world) as page:
            visible = tree(page).get_by_role("link").and_(page.locator(":visible"))
            await page.get_by_role("button", name="Collapse all").click()
            await expect(visible).to_have_count(4)

            # A match deep in a folded team is shown with the people above it, and nobody is told
            # "N shown" about people they cannot see.
            await page.get_by_label("Search people").fill("Associate 039")
            await expect(count_text(page)).to_have_text("3 people shown")
            for name in ("Nadia Hassan", "Farid Ismail", "Associate 039"):
                await expect(row(page, name)).to_be_visible()
            await expect(page.get_by_test_id("org-folded")).to_have_count(0)
            await expect(branch_button(page, "Farid Ismail")).to_have_attribute(
                "aria-expanded", "true"
            )

            # A fold chosen while narrowed hides the match, and the live text says so.
            await branch_button(page, "Farid Ismail").click()
            await expect(row(page, "Associate 039")).to_be_hidden()
            live = page.locator("[aria-live=polite]").filter(has=count_text(page))
            await expect(live).to_contain_text("3 people shown (1 inside folded teams)")
            # Changing the filter starts the narrowed view over, so the match comes back.
            await page.get_by_label("Search people").fill("Associate 038")
            await expect(row(page, "Associate 038")).to_be_visible()

            # Clearing brings back the folds from before: only the roots.
            await page.get_by_role("button", name="Clear filters").first.click()
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")
            await expect(visible).to_have_count(4)
            await expect(branch_button(page, "Nadia Hassan")).to_have_attribute(
                "aria-expanded", "false"
            )

    await with_browser(body)


async def test_print_rules_leave_a_page_without_the_chart_alone(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world, theme="night") as page:
            await page.goto(f"{site.base}/portal/hr/people")
            await expect(page.get_by_role("heading", name="People", exact=True)).to_be_visible()
            await page.emulate_media(media="print")
            shown = await page.evaluate(
                """() => ['header', 'aside', 'nav', 'main'].map(selector => {
                    const element = document.querySelector(selector);
                    return [selector, element && getComputedStyle(element).display];
                })"""
            )
            assert all(display not in (None, "none") for _, display in shown), shown
            await expect(page.get_by_role("heading", name="People", exact=True)).to_be_visible()
            # Night stays Night: no page-wide switch to a light scheme.
            scheme = await page.evaluate(
                "() => getComputedStyle(document.documentElement).colorScheme"
            )
            assert scheme == "dark", scheme

    await with_browser(body)


async def test_a_filter_with_no_match_says_so_and_clearing_moves_focus_to_the_result(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world) as page:
            await page.get_by_label("Search people").fill("zzzz")
            await expect(count_text(page)).to_have_text("0 people shown")
            await expect(page.get_by_text("No one matches")).to_be_visible()
            await expect(tree(page).get_by_role("link")).to_have_count(0)
            await page.get_by_role("button", name="Clear filters").last.click()
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")
            await expect(page.get_by_label("Search people")).to_have_value("")
            heading = page.get_by_role("heading", name="Reporting lines")
            await expect(heading).to_be_focused()
            await expect(page.get_by_role("button", name="Clear filters")).to_have_count(0)

    await with_browser(body)


async def test_legal_entity_filter_exists_only_when_there_are_legal_entities(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld(entities=False)
        async with org_page(browser, site, world) as page:
            await expect(page.get_by_label("Department")).to_be_visible()
            await expect(page.get_by_label("Legal entity")).to_have_count(0)
            async with page.expect_download() as download:
                await page.get_by_role("button", name="Download CSV").click()
            header = (
                (await (await download.value).path())
                .read_text(encoding="utf-8-sig")
                .splitlines()[0]
            )
            assert header == "Name,Job title,Department,Manager,Status,Start date,Last day"

    await with_browser(body)


async def test_csv_download_has_header_bom_and_safe_rows(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world) as page:
            async with page.expect_download() as download:
                await page.get_by_role("button", name="Download CSV").click()
            saved = await download.value
            assert saved.suggested_filename == "org-chart.csv"
            raw = (await saved.path()).read_bytes()
            assert raw.startswith(b"\xef\xbb\xbf")
            assert not raw.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
            rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
            assert rows[0] == [
                "Name",
                "Job title",
                "Department",
                "Manager",
                "Status",
                "Start date",
                "Last day",
                "Legal entity",
            ]
            assert len(rows) == len(world.nodes) + 1
            # No row starts with a formula: the name that began with "=" has a leading apostrophe.
            assert all(not line[0].startswith(("=", "+", "-", "@")) for line in rows[1:])
            zul = next(line for line in rows if "Zul Karim" in line[0])
            assert zul[0] == "'=Zul Karim"
            lina = next(line for line in rows if line[0] == "Lina Wong")
            assert lina[:6] == [
                "Lina Wong",
                "Packer",
                "Warehouse",
                "Farid Ismail",
                "starting",
                "2026-12-01",
            ]
            rizal = next(line for line in rows if line[0] == "Rizal Omar")
            assert rizal[6:] == ["2026-12-31", MAWAR]

            # A filtered view downloads what is shown.
            await page.get_by_label("Department").select_option(label="Finance")
            async with page.expect_download() as filtered:
                await page.get_by_role("button", name="Download CSV").click()
            text = (await (await filtered.value).path()).read_text(encoding="utf-8-sig")
            assert len(list(csv.reader(io.StringIO(text)))) == 4

    await with_browser(body)


async def test_print_asks_the_browser_and_shows_the_whole_tree_without_the_console(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        async with org_page(browser, site, world, theme="night") as page:
            await page.get_by_role("button", name="Collapse Siti Rahman's team (3)").click()
            await page.get_by_role("button", name="Print").click()
            assert await page.evaluate("() => window.__prints") == 1

            await page.emulate_media(media="print")
            # The folded team prints; the screen keeps it folded.
            await expect(row(page, "Aiman Yusof")).to_be_visible()
            for name in (
                "Edwin Tan",
                "=Zul Karim",
                "Associate 039",
                "Mei Ling Tan",
                "Kumar Raj",
            ):
                await expect(row(page, name)).to_be_visible()
            hidden = await page.evaluate(
                """() => ['header', 'aside', 'nav', '[role=tablist]', '[data-org-toolbar]']
                    .flatMap(selector => [...document.querySelectorAll(selector)])
                    .filter(element => getComputedStyle(element).display !== 'none'
                        && !element.contains(document.querySelector('[data-org-print]')))
                    .map(element => element.tagName + (element.getAttribute('aria-label') ?? ''))"""
            )
            assert hidden == [], hidden
            ink = await page.evaluate(
                """() => {
                    const rgb = text => text.match(/[\\d.]+/g).map(Number);
                    const sample = document.querySelector('[data-testid=org-row] a');
                    const color = rgb(getComputedStyle(sample).color);
                    let at = sample, background = null;
                    while (at && !background) {
                        const value = getComputedStyle(at).backgroundColor;
                        if (!/rgba\\(.*, 0\\)|transparent/.test(value)) background = rgb(value);
                        at = at.parentElement;
                    }
                    const light = [255, 255, 255];
                    return {color, background: background ?? light};
                }"""
            )
            assert max(ink["color"][:3]) < 100, ink
            assert min(ink["background"][:3]) > 235, ink
            chip_ink = await page.evaluate(
                "() => getComputedStyle("
                "document.querySelector('[data-testid=org-department]')).color"
            )
            assert max(float(n) for n in re.findall(r"[\d.]+", chip_ink)[:3]) < 100, chip_ink
            await page.emulate_media(media="screen")
            await expect(
                page.get_by_role("button", name="Expand Siti Rahman's team (3)")
            ).to_have_attribute("aria-expanded", "false")
            await expect(row(page, "Aiman Yusof")).to_be_hidden()

    await with_browser(body)


async def test_three_hundred_people_in_one_department_render_and_stay_keyboard_reachable(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld(team=300)
        async with org_page(browser, site, world) as page:
            await expect(count_text(page)).to_have_text(f"{len(world.nodes)} people shown")
            await expect(page.get_by_test_id("org-row")).to_have_count(len(world.nodes))
            await page.get_by_label("Department").select_option(label="Warehouse")
            # The 305 warehouse people plus Nadia, who is above Farid.
            await expect(count_text(page)).to_have_text("306 people shown")
            await page.get_by_label("Department").select_option(label="All departments")
            # Within a few Tab presses the tools give way to the tree: the 300 rows of a team do
            # not stand in the way once Collapse all has folded them.
            await page.get_by_role("button", name="Collapse all").focus()
            await page.keyboard.press("Enter")
            await expect(
                tree(page).get_by_role("link").and_(page.locator(":visible"))
            ).to_have_count(4)
            reached = False
            for _ in range(6):
                await page.keyboard.press("Tab")
                nadia = branch_button(page, "Nadia Hassan")
                if await nadia.evaluate("element => element === document.activeElement"):
                    reached = True
                    break
            assert reached
            await page.keyboard.press("Enter")
            await expect(row(page, "Farid Ismail")).to_be_visible()

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_a_deep_tree_fits_every_width_in_every_theme(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = OrgWorld()
        chain = world.nodes[0]["employee_id"]
        for level in range(12):
            number = 300 + level
            name = f"Nurul Aisyah binti Abdul Rahman al-Haj Level {level:02d}"
            world.nodes.append(
                node(
                    number,
                    name,
                    "Pengarah Operasi dan Pembangunan Perniagaan Serantau",
                    (OPS, "Operations and Regional Business Development"),
                    chain,
                )
            )
            chain = eid(number)
        async with org_page(browser, site, world, theme=theme, width=1280) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await page.get_by_role("button", name="Expand all").click()
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
            await expect(
                row(page, "Nurul Aisyah binti Abdul Rahman al-Haj Level 11")
            ).to_be_visible()
            await page.set_viewport_size({"width": 320, "height": 900})

    await with_browser(body)
