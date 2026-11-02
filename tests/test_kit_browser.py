"""Real browser checks for the phase 3 shared kit: details row, status chips, page intro and
the date-range filter.

The kit has no page of its own yet, so this file stages a copy of the app (the same disposable
`check-isolated.mjs --context` copy the other browser tests use) and writes a temporary
`app/kit-demo/page.tsx` (and `app/kit-demo-date/page.tsx`) into THAT copy only, plus both paths in
the copy's proxy allowlist.
Nothing here touches the real source tree. The page renders every kit piece; the root layout
supplies the theme (cookie `fs-theme`). No upstream is called, so the API origin is a dead
loopback address. One dev server serves the whole module; each test opens its own browser.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

from tests.test_services_browser import ROOT, WAIT, WEB, free_port

pytestmark = pytest.mark.asyncio

THEMES = ("cobalt", "night", "bureau")
LONG_TOKEN = "Succeeded_" + "x" * 80

DEMO_PAGE = """\"use client\";

import { useState } from "react";
import { DetailsRow, PageIntro, StatusChips } from "@/components/console/kit";

export default function KitDemo() {
  const [clicks, setClicks] = useState(0);
  const [controlled, setControlled] = useState(false);
  const [single, setSingle] = useState<string | string[]>("all");
  const [multi, setMulti] = useState<string | string[]>(["open"]);
  return (
    <main style={{ padding: 16, maxWidth: 720 }}>
      <h1>Kit demo</h1>
      <PageIntro>
        See what your assistants did today and open a row for the technical details.
      </PageIntro>
      <DetailsRow
        testId="demo-row"
        toggleLabel="order 12"
        summary={
          <span data-testid="demo-summary">
            <span data-testid="demo-summary-text">Order 12 was confirmed. </span>
            <a href="#order-12" data-testid="demo-link">View order</a>{" "}
            <button
              type="button"
              data-testid="demo-summary-button"
              onClick={() => setClicks((n) => n + 1)}
            >
              Nudge
            </button>
          </span>
        }
      >
        <span data-testid="demo-details-text">Succeeded #12 agent MYR LONG_TOKEN</span>
      </DetailsRow>
      <DetailsRow
        testId="demo-controlled"
        open={controlled}
        onOpenChange={setControlled}
        summary="Controlled row"
      >
        Controlled details
      </DetailsRow>
      <DetailsRow testId="demo-open" defaultOpen summary="Starts open">
        <span data-testid="demo-open-text">Open details text</span>
      </DetailsRow>
      <div style={{ margin: "12px 0" }}>
        <StatusChips
          testId="demo-single"
          ariaLabel="Single status"
          mode="single"
          value={single}
          onChange={setSingle}
          options={[
            { value: "all", label: "All" },
            { value: "open", label: "Open", count: 3 },
            { value: "done", label: "Done", count: null },
            { value: "failed", label: "Needs attention and a rather long label", count: 0 },
          ]}
        />
      </div>
      <StatusChips
        testId="demo-multi"
        ariaLabel="Multiple status"
        mode="multi"
        value={multi}
        onChange={setMulti}
        options={[
          { value: "open", label: "Open", count: 12 },
          { value: "done", label: "Done", count: 40 },
          { value: "failed", label: "Failed", count: Number.NaN },
        ]}
      />
      <p data-testid="demo-clicks">{clicks}</p>
      <p data-testid="demo-controlled-state">
        {controlled ? "controlled-open" : "controlled-closed"}
      </p>
      <p data-testid="demo-single-value">{JSON.stringify(single)}</p>
      <p data-testid="demo-multi-value">{JSON.stringify(multi)}</p>
    </main>
  );
}
""".replace("LONG_TOKEN", LONG_TOKEN)


DATE_DEMO_PAGE = """\"use client\";

import { useState } from "react";
import { DateRangeFilter } from "@/components/console/kit";
import { describeRange, toInstants, type DayRange } from "@/lib/date-range";

export default function DateDemo() {
  const [range, setRange] = useState<DayRange>({ from: null, to: null });
  const [far, setFar] = useState<DayRange>({ from: null, to: null });
  return (
    <>
      <div style={{ display: "flex", padding: 16 }}>
        <div style={{ marginLeft: "auto" }}>
          <DateRangeFilter testId="demo-far" value={far} onChange={setFar} today="2026-11-02" />
        </div>
      </div>
      <main style={{ padding: 16, maxWidth: 720 }}>
        <h1>Date demo</h1>
        <DateRangeFilter testId="demo-date" value={range} onChange={setRange} today="2026-11-02" />
        <p data-testid="demo-date-value">{JSON.stringify(range)}</p>
        <p data-testid="demo-date-instants">{JSON.stringify(toInstants(range))}</p>
        <p data-testid="demo-date-describe">{describeRange(range)}</p>
        <button type="button" data-testid="demo-date-after">After the filter</button>
        <div style={{ height: 2000 }} />
        {/* Stands in for AssistantDock: fixed bottom-5 right-5 z-40, after the page in <main>. */}
        <div
          data-testid="demo-dock"
          style={{
            position: "fixed", bottom: 20, right: 20, zIndex: 40, width: 72, height: 72,
            borderRadius: 36, background: "currentColor",
          }}
        />
      </main>
    </>
  );
}
"""


class Console:
    def __init__(self, base: str) -> None:
        self.base = base


@pytest.fixture(scope="module")
def console(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    node = shutil.which("node")
    assert node, "Node is required for the kit browser checks"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web first"
    )
    tmp_path = tmp_path_factory.mktemp("kit")
    stage = tmp_path / "web"
    environment = {
        "PATH": os.environ["PATH"],
        "NEXT_TELEMETRY_DISABLED": "1",
        "CSA_CONSOLE_API_ORIGIN": "http://127.0.0.1:9",
    }
    subprocess.run(
        [node, str(WEB / "scripts/check-isolated.mjs"), "--context", str(stage)],
        check=True,
        cwd=ROOT,
        env=environment,
        capture_output=True,
    )
    (stage / "node_modules").symlink_to(WEB / "node_modules", target_is_directory=True)
    # Test-only pages and allowlist entries, in the disposable copy only.
    for name, page_source in (("kit-demo", DEMO_PAGE), ("kit-demo-date", DATE_DEMO_PAGE)):
        (stage / f"app/{name}").mkdir()
        (stage / f"app/{name}/page.tsx").write_text(page_source)
    proxy = (stage / "proxy.ts").read_text()
    assert '  "/portal/email",\n]);' in proxy, "proxy.ts allowlist changed shape"
    (stage / "proxy.ts").write_text(
        proxy.replace(
            '  "/portal/email",\n]);',
            '  "/portal/email",\n  "/kit-demo",\n  "/kit-demo-date",\n]);',
        )
    )
    port = free_port()
    log_path = tmp_path / "frontend.log"
    log = log_path.open("w")
    process = subprocess.Popen(
        [
            node,
            str(WEB / "node_modules/next/dist/bin/next"),
            "dev",
            "--webpack",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=stage,
        env=environment,
        stdout=log,
        stderr=log,
    )
    try:
        with httpx.Client(trust_env=False) as http:
            for _ in range(120):
                assert process.poll() is None, log_path.read_text()
                try:
                    if http.get(f"http://127.0.0.1:{port}/login", timeout=2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                pytest.fail(f"Kit frontend did not start; log: {log_path}")
        yield Console(f"http://127.0.0.1:{port}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


async def open_demo(
    console: Console,
    *,
    theme: str = "cobalt",
    width: int = 1024,
    height: int = 900,
    reduced_motion: bool = False,
    paused: bool = False,
    path: str = "/kit-demo",
    ready: str = "demo-row-toggle",
    timezone_id: str | None = None,
) -> tuple[Any, Any, Page]:
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion="reduce" if reduced_motion else "no-preference",
        timezone_id=timezone_id,
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(WAIT)
    await context.add_cookies(
        [
            {"name": "csa_session", "value": "synthetic-session", "url": console.base},
            {"name": "fs-theme", "value": theme, "url": console.base},
        ]
    )
    if paused:
        # The product-level "Reduce animations" preference (lib/motion.ts PAUSE_KEY).
        await context.add_init_script("window.localStorage.setItem('fs-motion-paused', '1')")
    page = await context.new_page()
    await page.goto(f"{console.base}{path}")
    await expect(page.get_by_test_id(ready)).to_be_visible(timeout=WAIT)
    # Server HTML shows before React attaches; wait so the first click is not lost.
    await page.wait_for_function(
        f"Object.keys(document.querySelector('[data-testid={ready}]'))"
        ".some((key) => key.startsWith('__reactProps'))"
    )
    return playwright, browser, page


async def open_date_demo(
    console: Console, *, theme: str = "cobalt", width: int = 1024, height: int = 900
) -> tuple[Any, Any, Page]:
    # A fixed zone makes the instants below exact: Kuala Lumpur is UTC+8 all year.
    return await open_demo(
        console,
        theme=theme,
        width=width,
        height=height,
        path="/kit-demo-date",
        ready="demo-date",
        timezone_id="Asia/Kuala_Lumpur",
    )


async def close_demo(playwright: Any, browser: Any) -> None:
    await browser.close()
    await playwright.stop()


async def test_details_row_opens_with_keyboard_and_reports_aria_expanded(console: Console) -> None:
    playwright, browser, page = await open_demo(console)
    try:
        toggle = page.get_by_test_id("demo-row-toggle")
        details = page.get_by_test_id("demo-row-details")
        await expect(toggle).to_have_text("Show details for order 12")
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(details).to_be_hidden()
        # A group, not a region: a long list of rows must not create a landmark per row.
        await expect(details).to_have_attribute("role", "group")
        await expect(page.locator("[role=region]")).to_have_count(0)
        region_id = await toggle.get_attribute("aria-controls")
        assert region_id and await details.get_attribute("id") == region_id

        # Reach the button the way a keyboard user does: Tab through the summary controls.
        await page.get_by_test_id("demo-summary-button").focus()
        await page.keyboard.press("Tab")
        await expect(toggle).to_be_focused()
        await page.keyboard.press("Enter")
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        await expect(page.get_by_test_id("demo-details-text")).to_be_visible()
        await expect(toggle.locator("span").first).to_contain_text("Hide details")
        await page.keyboard.press("Space")
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(page.get_by_test_id("demo-details-text")).to_be_hidden()

        # Controlled row: the parent owns the state and hears the change.
        controlled = page.get_by_test_id("demo-controlled-toggle")
        await controlled.focus()
        await page.keyboard.press("Enter")
        await expect(page.get_by_test_id("demo-controlled-state")).to_have_text("controlled-open")
        await expect(controlled).to_have_attribute("aria-expanded", "true")
        await controlled.click()
        await expect(page.get_by_test_id("demo-controlled-state")).to_have_text("controlled-closed")
        await expect(controlled).to_have_attribute("aria-expanded", "false")

        # defaultOpen starts open.
        await expect(page.get_by_test_id("demo-open-toggle")).to_have_attribute(
            "aria-expanded", "true"
        )
        await expect(page.get_by_test_id("demo-open-text")).to_be_visible()
    finally:
        await close_demo(playwright, browser)


async def own_motion(page: Page) -> dict[str, str]:
    """The inline transition the component itself sets (the global CSS cannot mask this)."""
    return await page.evaluate(
        """() => ({
            region: document.querySelector('[data-testid=demo-row-details]').style.transition,
            chevron: document.querySelector('[data-testid=demo-row-toggle] svg').style.transition,
        })"""
    )


async def test_details_row_animates_when_motion_is_allowed(console: Console) -> None:
    # Positive control: without it the "none" checks below could pass for the wrong reason.
    playwright, browser, page = await open_demo(console)
    try:
        await page.get_by_test_id("demo-row-toggle").click()
        await page.wait_for_function(
            "document.querySelector('[data-testid=demo-row-details]').style.transition"
            ".startsWith('grid-template-rows')"
        )
        motion = await own_motion(page)
        assert "grid-template-rows" in motion["region"], motion
        assert "transform" in motion["chevron"], motion
    finally:
        await close_demo(playwright, browser)


@pytest.mark.parametrize("cause", ["os-reduced-motion", "reduce-animations-preference"])
async def test_details_row_sets_no_transition_when_motion_is_off(
    console: Console, cause: str
) -> None:
    playwright, browser, page = await open_demo(
        console,
        reduced_motion=cause == "os-reduced-motion",
        paused=cause == "reduce-animations-preference",
    )
    try:
        expected_pref = "reduce" if cause == "os-reduced-motion" else "no-preference"
        assert await page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches") == (
            expected_pref == "reduce"
        )
        await page.get_by_test_id("demo-row-toggle").click()
        await expect(page.get_by_test_id("demo-details-text")).to_be_visible()
        # The component itself sets no transition, whatever the global CSS also does.
        motion = await own_motion(page)
        assert motion["region"].startswith("none"), motion
        assert motion["chevron"].startswith("none"), motion
        # The row's own parts run no transition (the global button hover fade is not ours).
        assert (
            await page.evaluate(
                """() => document.getAnimations().filter((a) =>
                    a.effect && a.effect.target && a.effect.target.matches(
                        '[data-testid=demo-row-details], [data-testid=demo-row-toggle] svg'
                    )).length"""
            )
            == 0
        )
    finally:
        await close_demo(playwright, browser)


async def test_details_row_keeps_links_inside_summary_clickable(console: Console) -> None:
    playwright, browser, page = await open_demo(console)
    try:
        toggle = page.get_by_test_id("demo-row-toggle")
        await page.get_by_test_id("demo-link").click()
        assert page.url.endswith("#order-12"), page.url
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await page.get_by_test_id("demo-summary-button").click()
        await page.get_by_test_id("demo-summary-button").click()
        await expect(page.get_by_test_id("demo-clicks")).to_have_text("2")
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        # Clicking the plain words of the summary does not toggle either.
        await page.get_by_test_id("demo-summary-text").click()
        await expect(toggle).to_have_attribute("aria-expanded", "false")
    finally:
        await close_demo(playwright, browser)


async def test_status_chips_single_and_multi_press_state(console: Console) -> None:
    playwright, browser, page = await open_demo(console)
    try:
        group = page.get_by_test_id("demo-single")
        await expect(group).to_have_attribute("role", "group")
        await expect(group).to_have_attribute("aria-label", "Single status")
        chip = lambda name: page.get_by_test_id(f"demo-single-{name}")  # noqa: E731
        await expect(chip("all")).to_have_attribute("aria-pressed", "true")
        await expect(chip("open")).to_have_attribute("aria-pressed", "false")
        # Counts: a number only for a finite figure of zero or more.
        await expect(chip("all")).to_have_text("All")
        await expect(chip("open")).to_have_text("Open (3)")
        await expect(chip("done")).to_have_text("Done")
        await expect(chip("failed")).to_contain_text("Needs attention and a rather long label (0)")
        # Pressed state is more than colour: a check mark. The weight is constant, so a pressed
        # chip keeps its width (see the width test below); the weight sits on the inner span.
        await expect(chip("all").locator("svg")).to_have_count(1)
        await expect(chip("open").locator("svg")).to_have_count(0)
        weight = "(el) => getComputedStyle(el.querySelector('span:last-child')).fontWeight"
        assert int(await chip("all").evaluate(weight)) == int(await chip("open").evaluate(weight))
        assert int(await chip("all").evaluate(weight)) >= 600
        # A second cue besides colour and the check: a doubled border (inset ring), same box.
        shadow = "(el) => getComputedStyle(el).boxShadow"
        assert await chip("all").evaluate(shadow) != "none"
        assert await chip("open").evaluate(shadow) == "none"

        await chip("open").click()
        await expect(page.get_by_test_id("demo-single-value")).to_have_text('"open"')
        await expect(chip("open")).to_have_attribute("aria-pressed", "true")
        await expect(chip("all")).to_have_attribute("aria-pressed", "false")
        await chip("open").click()  # single: the chosen chip stays chosen
        await expect(chip("open")).to_have_attribute("aria-pressed", "true")

        multi = lambda name: page.get_by_test_id(f"demo-multi-{name}")  # noqa: E731
        await expect(multi("open")).to_have_attribute("aria-pressed", "true")
        await expect(multi("failed")).to_have_text("Failed")  # NaN count shows no figure
        await multi("done").click()
        await expect(page.get_by_test_id("demo-multi-value")).to_have_text('["open","done"]')
        await multi("open").click()
        await expect(page.get_by_test_id("demo-multi-value")).to_have_text('["done"]')
        await expect(multi("open")).to_have_attribute("aria-pressed", "false")
        await multi("done").click()
        await expect(page.get_by_test_id("demo-multi-value")).to_have_text("[]")
    finally:
        await close_demo(playwright, browser)


async def test_pressing_a_chip_does_not_move_its_neighbours(console: Console) -> None:
    playwright, browser, page = await open_demo(console)
    try:
        boxes = """() => [...document.querySelectorAll('[data-testid^=demo-multi-]')]
            .filter((el) => el.tagName === 'BUTTON')
            .map((el) => {
                const r = el.getBoundingClientRect();
                return [el.dataset.testid, r.left, r.width];
            })"""
        before = await page.evaluate(boxes)
        assert len(before) == 3, before
        await page.get_by_test_id("demo-multi-done").click()
        await expect(page.get_by_test_id("demo-multi-done")).to_have_attribute(
            "aria-pressed", "true"
        )
        await page.get_by_test_id("demo-multi-open").click()
        await expect(page.get_by_test_id("demo-multi-open")).to_have_attribute(
            "aria-pressed", "false"
        )
        after = await page.evaluate(boxes)
        for (name, left, width), (name2, left2, width2) in zip(before, after, strict=True):
            assert name == name2
            assert abs(left - left2) < 0.5 and abs(width - width2) < 0.5, (before, after)
    finally:
        await close_demo(playwright, browser)


@pytest.mark.parametrize("theme", THEMES)
async def test_kit_pieces_do_not_overflow_at_320_in_three_themes(
    console: Console, theme: str
) -> None:
    playwright, browser, page = await open_demo(console, theme=theme, width=320)
    try:
        await expect(page.locator("html")).to_have_attribute("data-theme", theme)
        await page.get_by_test_id("demo-row-toggle").click()
        await expect(page.get_by_test_id("demo-details-text")).to_be_visible()
        measured = await page.evaluate(
            """() => {
                const box = (id) => {
                    const r = document.querySelector(`[data-testid=${id}]`).getBoundingClientRect();
                    return [id, Math.round(r.left), Math.round(r.right)];
                };
                const intro = document.querySelector('[data-testid=page-intro]');
                const line = parseFloat(getComputedStyle(intro).lineHeight);
                return {
                    scroll: document.documentElement.scrollWidth,
                    client: document.documentElement.clientWidth,
                    boxes: ['page-intro', 'demo-row', 'demo-row-toggle', 'demo-details-text',
                            'demo-single', 'demo-multi', 'demo-controlled', 'demo-open'].map(box),
                    chips: [...document.querySelectorAll(
                        '[data-testid^=demo-single-], [data-testid^=demo-multi-]')]
                        .filter((el) => el.tagName === 'BUTTON')
                        .map((el) => {
                            const r = el.getBoundingClientRect();
                            return [el.dataset.testid, Math.round(r.left),
                                    Math.round(r.right), Math.round(r.height)];
                        }),
                    introLines: Math.round(intro.getBoundingClientRect().height / line),
                };
            }"""
        )
        assert measured["scroll"] <= measured["client"] == 320, measured
        for name, left, right in measured["boxes"]:
            assert left >= 0 and right <= 320, (name, measured)
        for name, left, right, height in measured["chips"]:
            assert left >= 0 and right <= 320, (name, measured)
            assert height >= 40, (name, measured)  # touch-friendly even when chips wrap
        assert measured["introLines"] <= 2, measured
    finally:
        await close_demo(playwright, browser)


CONTRAST_JS = """async () => {
    const parse = (value) => {
        const m = value.match(/rgba?\\(([^)]+)\\)/);
        if (!m) throw new Error('unsupported colour ' + value);
        const parts = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
        return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    };
    const over = (top, below) => ({
        r: top.r * top.a + below.r * (1 - top.a),
        g: top.g * top.a + below.g * (1 - top.a),
        b: top.b * top.a + below.b * (1 - top.a),
        a: 1,
    });
    const backdrop = (el) => {
        const own = parse(getComputedStyle(el).backgroundColor);
        if (own.a >= 1) return own;
        const parent = el.parentElement;
        const below = parent ? backdrop(parent) : { r: 255, g: 255, b: 255, a: 1 };
        return own.a === 0 ? below : over(own, below);
    };
    const channel = (v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    const lum = ({ r, g, b }) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    const ratio = (a, b) => {
        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
    };
    const id = (name) => document.querySelector(`[data-testid=${name}]`);
    const text = (name) => {
        const el = id(name);
        const below = backdrop(el);
        return { name, ratio: ratio(over(parse(getComputedStyle(el).color), below), below) };
    };
    const border = (name) => {
        const el = id(name);
        const s = getComputedStyle(el);
        const around = backdrop(el.parentElement);
        return { name: name + ' border', ratio: ratio(parse(s.borderTopColor), around) };
    };
    const chips = ['demo-single-all', 'demo-single-open', 'demo-multi-open', 'demo-multi-done'];
    return {
        texts: ['page-intro', 'demo-summary-text', 'demo-open-text', 'demo-row-toggle',
                ...chips].map(text),
        borders: chips.map(border),
    };
}"""


@pytest.mark.parametrize("theme", THEMES)
async def test_kit_contrast_in_three_themes(console: Console, theme: str) -> None:
    playwright, browser, page = await open_demo(console, theme=theme)
    try:
        await expect(page.locator("html")).to_have_attribute("data-theme", theme)
        measured = await page.evaluate(CONTRAST_JS)
        for item in measured["texts"]:
            assert item["ratio"] >= 4.5, (theme, item)
        for item in measured["borders"]:
            assert item["ratio"] >= 3.0, (theme, item)
        # Keyboard focus ring on a chip and on the details button: drawn, thick enough, 3:1.
        for target in ("demo-single-open", "demo-row-toggle"):
            await page.get_by_test_id(target).focus()
            await page.keyboard.press("Shift+Tab")
            await page.keyboard.press("Tab")
            await expect(page.get_by_test_id(target)).to_be_focused()
            ring = await page.evaluate(
                """(name) => {
                    const el = document.querySelector(`[data-testid=${name}]`);
                    const s = getComputedStyle(el);
                    const parse = (v) => v.match(/[\\d.]+/g).map(Number);
                    const [r, g, b] = parse(s.outlineColor);
                    let host = el.parentElement, bg = null;
                    while (host && !bg) {
                        const c = parse(getComputedStyle(host).backgroundColor);
                        if ((c[3] ?? 1) >= 1) bg = c;
                        host = host.parentElement;
                    }
                    bg = bg ?? [255, 255, 255];
                    const channel = (v) => {
                        v /= 255;
                        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
                    };
                    const lum = (c) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1])
                        + 0.0722 * channel(c[2]);
                    const [hi, lo] = [lum([r, g, b]), lum(bg)].sort((x, y) => y - x);
                    return {
                        style: s.outlineStyle,
                        width: parseFloat(s.outlineWidth),
                        ratio: (hi + 0.05) / (lo + 0.05),
                    };
                }""",
                target,
            )
            assert ring["style"] != "none" and ring["width"] >= 2, (theme, target, ring)
            assert ring["ratio"] >= 3.0, (theme, target, ring)
        # Details open: the technical text keeps 4.5:1 too.
        await page.get_by_test_id("demo-row-toggle").click()
        measured = await page.evaluate(
            CONTRAST_JS.replace("'demo-summary-text',", "'demo-summary-text', 'demo-details-text',")
        )
        for item in measured["texts"]:
            assert item["ratio"] >= 4.5, (theme, item)
    finally:
        await close_demo(playwright, browser)


# ---------------------------------------------------------------------------------------------
# Date-range filter. The demo page pins "today" to 2026-11-02 and the browser zone to Kuala
# Lumpur, so day labels and instants are exact.
# ---------------------------------------------------------------------------------------------


def day(page: Page, iso: str) -> Any:
    return page.get_by_test_id(f"demo-date-day-{iso}")


async def date_value(page: Page) -> str:
    return await page.get_by_test_id("demo-date-value").inner_text()


async def settled(page: Page) -> None:
    """Let the console's global 160 ms colour fade on buttons finish before reading colours."""
    await page.evaluate("Promise.all(document.getAnimations().map((a) => a.finished))")


async def selected_days(page: Page) -> list[str]:
    return await page.evaluate(
        """() => [...document.querySelectorAll('[role=gridcell][aria-selected=true] [data-day]')]
            .map((el) => el.dataset.day)"""
    )


async def test_date_filter_selects_a_range_by_clicks_and_presets(console: Console) -> None:
    playwright, browser, page = await open_date_demo(console)
    try:
        trigger = page.get_by_test_id("demo-date")
        popover = page.get_by_test_id("demo-date-popover")
        await expect(trigger).to_have_text("Date range: Any date")
        await expect(trigger).to_have_attribute("aria-expanded", "false")
        await expect(popover).to_have_count(0)
        await trigger.click()
        await expect(popover).to_be_visible()
        await expect(trigger).to_have_attribute("aria-expanded", "true")
        await expect(page.get_by_test_id("demo-date-grid")).to_have_attribute(
            "aria-label", "November 2026"
        )
        await expect(page.get_by_test_id("demo-date-grid")).to_have_attribute("role", "grid")
        assert await selected_days(page) == []
        await expect(day(page, "2026-11-02")).to_have_attribute("data-today", "true")
        await expect(day(page, "2026-11-02")).to_have_attribute(
            "aria-label", "2 November 2026, today"
        )

        # Two clicks choose a range.
        await day(page, "2026-11-12").click()
        assert await date_value(page) == '{"from":"2026-11-12","to":"2026-11-12"}'
        await expect(trigger).to_have_text("Date range: 12 Nov 2026")
        await day(page, "2026-11-18").click()
        assert await date_value(page) == '{"from":"2026-11-12","to":"2026-11-18"}'
        await expect(trigger).to_have_text("Date range: 12 Nov to 18 Nov 2026")
        assert await selected_days(page) == [f"2026-11-{n:02d}" for n in range(12, 19)]
        # The end is exclusive on the wire: the local start of 19 Nov. Kuala Lumpur is UTC+8, so
        # local midnight is 16:00 UTC on the day before.
        await expect(page.get_by_test_id("demo-date-instants")).to_have_text(
            '{"from":"2026-11-11T16:00:00.000Z","to":"2026-11-18T16:00:00.000Z"}'
        )
        # A third click starts again; an earlier second click swaps the ends.
        await day(page, "2026-11-20").click()
        assert await date_value(page) == '{"from":"2026-11-20","to":"2026-11-20"}'
        await day(page, "2026-11-05").click()
        assert await date_value(page) == '{"from":"2026-11-05","to":"2026-11-20"}'

        # Months change with the buttons and keep the chosen range.
        await page.get_by_test_id("demo-date-next").click()
        await expect(page.get_by_test_id("demo-date-grid")).to_have_attribute(
            "aria-label", "December 2026"
        )
        assert await selected_days(page) == []
        await page.get_by_test_id("demo-date-prev").click()
        await page.get_by_test_id("demo-date-prev").click()
        await expect(page.get_by_test_id("demo-date-grid")).to_have_attribute(
            "aria-label", "October 2026"
        )

        # Presets apply at once and show the end of the range.
        await page.get_by_test_id("demo-date-preset-last7").click()
        assert await date_value(page) == '{"from":"2026-10-27","to":"2026-11-02"}'
        await expect(page.get_by_test_id("demo-date-grid")).to_have_attribute(
            "aria-label", "November 2026"
        )
        assert await selected_days(page) == ["2026-11-01", "2026-11-02"]
        await page.get_by_test_id("demo-date-preset-today").click()
        assert await date_value(page) == '{"from":"2026-11-02","to":"2026-11-02"}'
        await page.get_by_test_id("demo-date-preset-month").click()
        assert await date_value(page) == '{"from":"2026-11-01","to":"2026-11-30"}'
        await expect(page.get_by_test_id("demo-date-instants")).to_have_text(
            '{"from":"2026-10-31T16:00:00.000Z","to":"2026-11-30T16:00:00.000Z"}'
        )
        await page.get_by_test_id("demo-date-preset-last30").click()
        assert await date_value(page) == '{"from":"2026-10-04","to":"2026-11-02"}'
        await page.get_by_test_id("demo-date-preset-clear").click()
        assert await date_value(page) == '{"from":null,"to":null}'
        await expect(trigger).to_have_text("Date range: Any date")
        await expect(page.get_by_test_id("demo-date-instants")).to_have_text(
            '{"from":null,"to":null}'
        )

        # Done closes the popover and hands focus back to the button.
        await page.get_by_test_id("demo-date-done").click()
        await expect(popover).to_have_count(0)
        await expect(trigger).to_be_focused()
        # A click outside closes it too.
        await trigger.click()
        await expect(popover).to_be_visible()
        await page.get_by_test_id("demo-date-value").click()
        await expect(popover).to_have_count(0)
    finally:
        await close_demo(playwright, browser)


async def test_date_filter_keyboard_and_escape_focus_return(console: Console) -> None:
    playwright, browser, page = await open_date_demo(console)
    try:
        trigger = page.get_by_test_id("demo-date")
        popover = page.get_by_test_id("demo-date-popover")
        await trigger.focus()
        await page.keyboard.press("Enter")
        await expect(popover).to_be_visible()
        # Focus lands on today (nothing chosen yet), and only that day is in the tab order.
        await expect(day(page, "2026-11-02")).to_be_focused()
        assert await page.locator("[data-day][tabindex='0']").count() == 1

        async def press(key: str, expected: str) -> None:
            await page.keyboard.press(key)
            await expect(day(page, expected)).to_be_focused()

        await press("ArrowRight", "2026-11-03")
        await press("ArrowDown", "2026-11-10")
        await press("ArrowLeft", "2026-11-09")
        await press("ArrowUp", "2026-11-02")
        await press("End", "2026-11-08")  # Sunday: the week starts on Monday
        await press("Home", "2026-11-02")
        await press("PageDown", "2026-12-02")
        await expect(page.get_by_test_id("demo-date-grid")).to_have_attribute(
            "aria-label", "December 2026"
        )
        await press("PageUp", "2026-11-02")
        await press("ArrowLeft", "2026-11-01")
        await press("ArrowLeft", "2026-10-31")  # crosses into the previous month
        await expect(page.get_by_test_id("demo-date-grid")).to_have_attribute(
            "aria-label", "October 2026"
        )
        await press("ArrowDown", "2026-11-07")
        # Enter selects, a second Enter on a later day completes the range.
        await page.keyboard.press("Enter")
        assert await date_value(page) == '{"from":"2026-11-07","to":"2026-11-07"}'
        await press("ArrowRight", "2026-11-08")
        await page.keyboard.press("Space")
        assert await date_value(page) == '{"from":"2026-11-07","to":"2026-11-08"}'
        assert await selected_days(page) == ["2026-11-07", "2026-11-08"]

        # Escape closes and returns focus to the button; nothing else changes.
        await page.keyboard.press("Escape")
        await expect(popover).to_have_count(0)
        await expect(trigger).to_be_focused()
        assert await date_value(page) == '{"from":"2026-11-07","to":"2026-11-08"}'

        # Reopened, focus starts on the first chosen day. Escape from a preset button works too.
        await page.keyboard.press("Space")
        await expect(day(page, "2026-11-07")).to_be_focused()
        await page.get_by_test_id("demo-date-preset-today").focus()
        await page.keyboard.press("Escape")
        await expect(popover).to_have_count(0)
        await expect(trigger).to_be_focused()
        # Tabbing out of an open popover closes it without taking focus back.
        await page.keyboard.press("Enter")
        await expect(popover).to_be_visible()
        await page.get_by_test_id("demo-date-done").focus()
        await page.keyboard.press("Tab")
        await expect(page.get_by_test_id("demo-date-after")).to_be_focused()
        await expect(popover).to_have_count(0)
    finally:
        await close_demo(playwright, browser)


CONTRAST_HELPERS = CONTRAST_JS[len("async () => {") : CONTRAST_JS.index("    const id = (name)")]
DATE_CONTRAST_JS = (
    "async (probe) => {"
    + CONTRAST_HELPERS
    + """
    const find = (selector) => {
        const el = document.querySelector(selector);
        if (!el) throw new Error('missing ' + selector);
        return el;
    };
    const text = (selector) => {
        const el = find(selector);
        const below = backdrop(el);
        return { selector, ratio: ratio(over(parse(getComputedStyle(el).color), below), below) };
    };
    const border = (selector) => {
        const el = find(selector);
        const around = backdrop(el.parentElement);
        return {
            selector: selector + ' border',
            ratio: ratio(parse(getComputedStyle(el).borderTopColor), around),
        };
    };
    const active = document.activeElement;
    const ring = active && active.tagName === 'BUTTON'
        ? ratio(parse(getComputedStyle(active).outlineColor), backdrop(active.parentElement))
        : null;
    return { texts: probe.texts.map(text), borders: probe.borders.map(border), ring };
}"""
)


@pytest.mark.parametrize("theme", THEMES)
async def test_date_filter_sheet_fits_at_320_in_three_themes(console: Console, theme: str) -> None:
    playwright, browser, page = await open_date_demo(console, theme=theme, width=320)
    try:
        await expect(page.locator("html")).to_have_attribute("data-theme", theme)
        await page.get_by_test_id("demo-date").click()
        await expect(page.get_by_test_id("demo-date-popover")).to_be_visible()
        await day(page, "2026-11-12").click()
        await day(page, "2026-11-18").click()
        measured = await page.evaluate(
            """() => {
                const popover = document.querySelector('[data-testid=demo-date-popover]');
                const box = popover.getBoundingClientRect();
                const inside = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.left >= box.left - 0.5 && r.right <= box.right + 0.5;
                };
                const days = [...popover.querySelectorAll('[data-day]')];
                return {
                    scroll: document.documentElement.scrollWidth,
                    client: document.documentElement.clientWidth,
                    height: window.innerHeight,
                    position: getComputedStyle(popover).position,
                    left: box.left, right: box.right, top: box.top, bottom: box.bottom,
                    popoverScroll: popover.scrollWidth, popoverClient: popover.clientWidth,
                    everythingInside: [...popover.querySelectorAll('button, table, p')]
                        .every(inside),
                    dayCount: days.length,
                    minDayHeight: Math.min(...days.map((el) => el.getBoundingClientRect().height)),
                    minDayWidth: Math.min(...days.map((el) => el.getBoundingClientRect().width)),
                    controlsHeights: [...popover.querySelectorAll('button:not([data-day])')]
                        .map((el) => Math.round(el.getBoundingClientRect().height)),
                };
            }"""
        )
        assert measured["scroll"] <= measured["client"] == 320, measured
        # A bottom sheet: fixed, full width, resting on the bottom edge of the viewport.
        assert measured["position"] == "fixed", measured
        assert measured["left"] == 0 and measured["right"] == 320, measured
        assert measured["bottom"] == measured["height"] and measured["top"] >= 0, measured
        assert measured["popoverScroll"] <= measured["popoverClient"], measured
        assert measured["everythingInside"], measured
        assert measured["dayCount"] == 30, measured
        assert measured["minDayHeight"] >= 40 and measured["minDayWidth"] >= 38, measured
        assert min(measured["controlsHeights"]) >= 40, measured

        # 4.5:1 for words, 3:1 for the boundaries that carry meaning, in every theme.
        probe = {
            "texts": [
                "[data-testid=demo-date]",
                "[data-testid=demo-date-month]",
                "[data-testid=demo-date-summary]",
                "[data-testid=demo-date-popover] th span",
                "[data-testid=demo-date-day-2026-11-05]",  # idle
                "[data-testid=demo-date-day-2026-11-15]",  # inside the range
                "[data-testid=demo-date-day-2026-11-12]",  # the first day
                "[data-testid=demo-date-day-2026-11-18]",  # the last day
                "[data-testid=demo-date-preset-last7]",
                "[data-testid=demo-date-done]",
                "[data-testid=demo-date-prev]",
            ],
            "borders": [
                "[data-testid=demo-date]",
                "[data-testid=demo-date-day-2026-11-15]",
                "[data-testid=demo-date-day-2026-11-12]",
                "[data-testid=demo-date-preset-last7]",
            ],
        }
        await settled(page)
        result = await page.evaluate(DATE_CONTRAST_JS, probe)
        for item in result["texts"]:
            assert item["ratio"] >= 4.5, (theme, item)
        for item in result["borders"]:
            assert item["ratio"] >= 3.0, (theme, item)
        # The keyboard ring on a day is drawn, at least 2px thick.
        await day(page, "2026-11-15").focus()
        await page.keyboard.press("Shift+Tab")
        await page.keyboard.press("Tab")
        await expect(page.locator("[data-day][tabindex='0']")).to_be_focused()
        ring = await page.evaluate(
            """() => {
                const s = getComputedStyle(document.activeElement);
                return { style: s.outlineStyle, width: parseFloat(s.outlineWidth) };
            }"""
        )
        assert ring["style"] != "none" and ring["width"] >= 2, (theme, ring)
        contrast = await page.evaluate(DATE_CONTRAST_JS, {"texts": [], "borders": []})
        assert contrast["ring"] is not None and contrast["ring"] >= 3.0, (theme, contrast)
        # On the sheet, a tap on the dimmed page behind it closes it.
        await page.get_by_test_id("demo-date-scrim").click(position={"x": 160, "y": 20})
        await expect(page.get_by_test_id("demo-date-popover")).to_have_count(0)
        # ...and focus goes back to the button that opened it, not to the page body.
        await expect(page.get_by_test_id("demo-date")).to_be_focused()
    finally:
        await close_demo(playwright, browser)


async def test_date_filter_is_a_positioned_popover_from_640_up(console: Console) -> None:
    playwright, browser, page = await open_date_demo(console, width=1024)
    try:
        trigger = page.get_by_test_id("demo-date")
        await trigger.click()
        popover = page.get_by_test_id("demo-date-popover")
        await expect(popover).to_be_visible()
        measured = await page.evaluate(
            """() => {
                const p = document.querySelector('[data-testid=demo-date-popover]');
                const t = document.querySelector('[data-testid=demo-date]');
                const pr = p.getBoundingClientRect();
                const tr = t.getBoundingClientRect();
                return {
                    position: getComputedStyle(p).position,
                    width: Math.round(pr.width), top: Math.round(pr.top),
                    triggerBottom: Math.round(tr.bottom), left: Math.round(pr.left),
                    triggerLeft: Math.round(tr.left),
                    scroll: document.documentElement.scrollWidth,
                    client: document.documentElement.clientWidth,
                };
            }"""
        )
        assert measured["position"] == "fixed", measured
        assert measured["width"] == 320, measured
        assert measured["top"] > measured["triggerBottom"], measured
        assert measured["left"] == measured["triggerLeft"], measured
        assert measured["scroll"] <= measured["client"], measured
    finally:
        await close_demo(playwright, browser)


async def test_selected_range_visible_without_colour(console: Console) -> None:
    playwright, browser, page = await open_date_demo(console)
    try:
        await page.get_by_test_id("demo-date").click()
        await day(page, "2026-11-12").click()
        await day(page, "2026-11-18").click()
        await settled(page)
        styles = await page.evaluate(
            """() => {
                const read = (iso) => {
                    const el = document.querySelector(`[data-day="${iso}"]`);
                    const s = getComputedStyle(el);
                    return {
                        iso,
                        width: parseFloat(s.borderTopWidth),
                        style: s.borderTopStyle,
                        alpha: (s.borderTopColor.match(/[\\d.]+/g) ?? []).slice(3)[0] ?? '1',
                        selected: el.closest('[role=gridcell]').getAttribute('aria-selected'),
                        underline: getComputedStyle(el.firstElementChild).textDecorationLine,
                        weight: getComputedStyle(el.firstElementChild).fontWeight,
                    };
                };
                return {
                    inside: ['2026-11-12', '2026-11-15', '2026-11-18'].map(read),
                    outside: ['2026-11-11', '2026-11-19', '2026-11-05'].map(read),
                    today: read('2026-11-02'),
                };
            }"""
        )
        for item in styles["inside"]:
            # A solid 2px boundary (an outline shape), not only a different fill.
            assert item["selected"] == "true", item
            assert item["style"] == "solid" and item["width"] >= 2, item
            assert float(item["alpha"]) > 0, item
        for item in styles["outside"]:
            assert item["selected"] == "false", item
            assert float(item["alpha"]) == 0, item  # same box, no visible boundary
        # The ends are heavier than the middle, and today is underlined: words, not hue.
        weights = {i["iso"]: int(i["weight"]) for i in styles["inside"]}
        assert weights["2026-11-12"] > weights["2026-11-15"] < weights["2026-11-18"], weights
        assert "underline" in styles["today"]["underline"], styles["today"]
        # The range is also written out, twice: on the button and under the grid.
        await expect(page.get_by_test_id("demo-date")).to_contain_text("12 Nov to 18 Nov 2026")
        await expect(page.get_by_test_id("demo-date-summary")).to_contain_text(
            "12 Nov to 18 Nov 2026"
        )
    finally:
        await close_demo(playwright, browser)


@pytest.mark.parametrize("theme", THEMES)
async def test_date_sheet_is_not_covered_by_a_fixed_dock(console: Console, theme: str) -> None:
    # AssistantDock is fixed bottom-5 right-5 z-40 and renders after the page; the demo page has
    # a stand-in in the same place. The sheet must sit above it, "Done" included.
    playwright, browser, page = await open_date_demo(console, theme=theme, width=390)
    try:
        await page.get_by_test_id("demo-date").click()
        done = page.get_by_test_id("demo-date-done")
        await expect(done).to_be_visible()
        box = await done.bounding_box()
        assert box is not None
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        probe = await page.evaluate(
            """([x, y]) => {
                const dock = document.querySelector('[data-testid=demo-dock]')
                    .getBoundingClientRect();
                const top = document.elementFromPoint(x, y);
                return {
                    overlapsDock: x >= dock.left && x <= dock.right
                        && y >= dock.top && y <= dock.bottom,
                    topIsDone: !!top && !!top.closest('[data-testid=demo-date-done]'),
                    topIs: top ? (top.dataset.testid || top.tagName) : null,
                    inBody: document.body.contains(
                        document.querySelector('[data-testid=demo-date-popover]')
                    ) && document.querySelector('[data-testid=demo-date-popover]').parentElement
                        === document.body,
                };
            }""",
            [x, y],
        )
        # The test means something only if the dock really sits where "Done" is.
        assert probe["overlapsDock"], probe
        assert probe["topIsDone"], probe
        assert probe["inBody"], probe
        # And a real click lands (Playwright refuses a click another element would intercept).
        await done.click()
        await expect(page.get_by_test_id("demo-date-popover")).to_have_count(0)
        await expect(page.get_by_test_id("demo-date")).to_be_focused()
    finally:
        await close_demo(playwright, browser)


@pytest.mark.parametrize("width", [640, 768, 1024])
async def test_date_popover_flips_at_the_right_edge(console: Console, width: int) -> None:
    playwright, browser, page = await open_date_demo(console, width=width)
    try:
        trigger = page.get_by_test_id("demo-far")
        await trigger.click()
        popover = page.get_by_test_id("demo-far-popover")
        await expect(popover).to_be_visible()
        measured = await page.evaluate(
            """() => {
                const p = document.querySelector('[data-testid=demo-far-popover]')
                    .getBoundingClientRect();
                const t = document.querySelector('[data-testid=demo-far]').getBoundingClientRect();
                return {
                    left: p.left, right: p.right, top: p.top, width: p.width,
                    triggerRight: t.right, triggerBottom: t.bottom,
                    inner: window.innerWidth,
                    scroll: document.documentElement.scrollWidth,
                    client: document.documentElement.clientWidth,
                };
            }"""
        )
        assert measured["left"] >= 0 and measured["right"] <= measured["inner"], measured
        assert measured["right"] <= measured["inner"] - 8 + 0.5, measured  # keeps its margin
        # Flipped to the button's right edge, still below it.
        assert abs(measured["right"] - measured["triggerRight"]) <= 1, measured
        assert measured["top"] >= measured["triggerBottom"], measured
        assert measured["scroll"] <= measured["client"], measured
        # It works from there: a click selects, Escape returns focus to the far button.
        await page.get_by_test_id("demo-far-day-2026-11-12").click()
        await page.keyboard.press("Escape")
        await expect(popover).to_have_count(0)
        await expect(trigger).to_be_focused()
        await expect(trigger).to_contain_text("12 Nov 2026")
    finally:
        await close_demo(playwright, browser)


async def test_date_sheet_locks_page_scroll_below_640_only(console: Console) -> None:
    playwright, browser, page = await open_date_demo(console, width=390)
    try:
        await page.mouse.move(200, 100)
        await page.get_by_test_id("demo-date").click()
        await expect(page.get_by_test_id("demo-date-popover")).to_be_visible()
        assert await page.evaluate("document.body.style.overflow") == "hidden"
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(200)
        assert await page.evaluate("window.scrollY") == 0
        await page.keyboard.press("Escape")
        await expect(page.get_by_test_id("demo-date-popover")).to_have_count(0)
        # Restored on close: nothing left on the body, and the page scrolls again.
        assert await page.evaluate("document.body.style.overflow") == ""
        assert await page.evaluate("document.body.style.paddingRight") == ""
        await page.mouse.wheel(0, 400)
        await page.wait_for_function("window.scrollY > 0")
    finally:
        await close_demo(playwright, browser)

    # From 640 px up the page is not locked, and the popover follows its button on scroll.
    playwright, browser, page = await open_date_demo(console, width=1024)
    try:
        await page.mouse.move(600, 500)
        await page.get_by_test_id("demo-date").click()
        await expect(page.get_by_test_id("demo-date-popover")).to_be_visible()
        assert await page.evaluate("document.body.style.overflow") == ""
        await page.mouse.wheel(0, 120)
        await page.wait_for_function("window.scrollY > 0")
        await page.wait_for_timeout(100)
        gap = await page.evaluate(
            """() => {
                const p = document.querySelector('[data-testid=demo-date-popover]')
                    .getBoundingClientRect();
                const t = document.querySelector('[data-testid=demo-date]').getBoundingClientRect();
                return p.top - t.bottom;
            }"""
        )
        assert abs(gap - 8) <= 1, gap
    finally:
        await close_demo(playwright, browser)


async def test_date_popover_tab_order_across_the_portal(console: Console) -> None:
    playwright, browser, page = await open_date_demo(console)
    try:
        trigger = page.get_by_test_id("demo-date")
        await trigger.click()
        await expect(day(page, "2026-11-02")).to_be_focused()
        # The popover sits at the end of <body>, so the button walks into it and back.
        await trigger.focus()
        await page.keyboard.press("Tab")
        await expect(page.get_by_test_id("demo-date-prev")).to_be_focused()
        await page.keyboard.press("Shift+Tab")
        await expect(trigger).to_be_focused()
        await expect(page.get_by_test_id("demo-date-popover")).to_be_visible()
        # Forward through every control to Done, then out to the next thing after the button.
        await page.keyboard.press("Tab")
        for expected in (
            "next",
            "day-2026-11-02",
            "preset-today",
            "preset-last7",
            "preset-last30",
            "preset-month",
            "preset-clear",
            "done",
        ):
            await page.keyboard.press("Tab")
            await expect(page.get_by_test_id(f"demo-date-{expected}")).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(page.get_by_test_id("demo-date-after")).to_be_focused()
        await expect(page.get_by_test_id("demo-date-popover")).to_have_count(0)
        # A pointer click elsewhere closes it as well.
        await trigger.click()
        await expect(page.get_by_test_id("demo-date-popover")).to_be_visible()
        await page.get_by_test_id("demo-date-describe").click()
        await expect(page.get_by_test_id("demo-date-popover")).to_have_count(0)
    finally:
        await close_demo(playwright, browser)


@pytest.mark.parametrize(
    ("case", "width", "height", "trigger_top"),
    [
        ("below", 1024, 900, 100),
        ("above", 1024, 900, 700),
        ("inside", 640, 500, 250),
        ("short", 740, 340, 160),  # a landscape phone: neither side holds a usable calendar
    ],
)
async def test_date_popover_vertical_placement(
    console: Console, case: str, width: int, height: int, trigger_top: int
) -> None:
    """Below the button with room below; above with more room above; never past the viewport;
    scrolling inside itself when neither side holds the whole calendar."""
    playwright, browser, page = await open_date_demo(console, width=width, height=height)
    try:
        trigger = page.get_by_test_id("demo-date")
        # Room above the button (the demo has none), then scroll it to the wanted height.
        await page.evaluate("document.querySelector('h1').style.marginTop = '800px'")
        await trigger.evaluate(
            "(el, y) => window.scrollBy(0, el.getBoundingClientRect().top - y)", trigger_top
        )
        await page.wait_for_timeout(100)
        await trigger.click()
        popover = page.get_by_test_id("demo-date-popover")
        await expect(popover).to_be_visible()
        measured = await page.evaluate(
            """() => {
                const p = document.querySelector('[data-testid=demo-date-popover]');
                const t = document.querySelector('[data-testid=demo-date]').getBoundingClientRect();
                const r = p.getBoundingClientRect();
                return {
                    top: r.top, bottom: r.bottom, height: r.height,
                    triggerTop: t.top, triggerBottom: t.bottom,
                    max: p.style.getPropertyValue('--fs-date-max'),
                    maxHeight: getComputedStyle(p).maxHeight,
                    scrolls: p.scrollHeight > p.clientHeight + 1,
                    inner: window.innerHeight,
                };
            }"""
        )
        assert measured["top"] >= 0 and measured["bottom"] <= measured["inner"] + 0.5, measured
        assert measured["max"].endswith("px"), measured
        assert measured["height"] <= float(measured["max"][:-2]) + 0.5, measured
        if case == "below":
            assert measured["top"] >= measured["triggerBottom"], measured
            assert not measured["scrolls"], measured
        elif case == "above":
            assert measured["bottom"] <= measured["triggerTop"] + 0.5, measured
            assert not measured["scrolls"], measured
        else:
            assert measured["scrolls"], measured
            if case == "short":
                # Not a sliver beside the button: the calendar takes the viewport, top to bottom.
                assert measured["height"] >= height - 2 * 8 - 1, measured
                assert measured["top"] >= 0, measured
            # Every control stays reachable: the last one scrolls into view and works.
            await page.get_by_test_id("demo-date-done").click()
            await expect(popover).to_have_count(0)
            await expect(trigger).to_be_focused()
    finally:
        await close_demo(playwright, browser)
