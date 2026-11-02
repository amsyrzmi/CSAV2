"""Real browser checks for the phase 3 shared kit: details row, status chips and page intro.

The kit has no page of its own yet, so this file stages a copy of the app (the same disposable
`check-isolated.mjs --context` copy the other browser tests use) and writes a temporary
`app/kit-demo/page.tsx` into THAT copy only, plus `/kit-demo` in the copy's proxy allowlist.
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
    # Test-only page and allowlist entry, in the disposable copy only.
    (stage / "app/kit-demo").mkdir()
    (stage / "app/kit-demo/page.tsx").write_text(DEMO_PAGE)
    proxy = (stage / "proxy.ts").read_text()
    assert '  "/portal/email",\n]);' in proxy, "proxy.ts allowlist changed shape"
    (stage / "proxy.ts").write_text(
        proxy.replace('  "/portal/email",\n]);', '  "/portal/email",\n  "/kit-demo",\n]);')
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
    console: Console, *, theme: str = "cobalt", width: int = 1024, reduced_motion: bool = False
) -> tuple[Any, Any, Page]:
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    context = await browser.new_context(
        viewport={"width": width, "height": 900},
        reduced_motion="reduce" if reduced_motion else "no-preference",
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(WAIT)
    await context.add_cookies(
        [
            {"name": "csa_session", "value": "synthetic-session", "url": console.base},
            {"name": "fs-theme", "value": theme, "url": console.base},
        ]
    )
    page = await context.new_page()
    await page.goto(f"{console.base}/kit-demo")
    await expect(page.get_by_test_id("demo-row-toggle")).to_be_visible(timeout=WAIT)
    # Server HTML shows before React attaches; wait so the first click is not lost.
    await page.wait_for_function(
        "Object.keys(document.querySelector('[data-testid=demo-row-toggle]'))"
        ".some((key) => key.startsWith('__reactProps'))"
    )
    return playwright, browser, page


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


async def test_details_row_has_no_height_animation_under_reduced_motion(console: Console) -> None:
    playwright, browser, page = await open_demo(console, reduced_motion=True)
    try:
        region = page.get_by_test_id("demo-row-details")
        # The global reduced-motion rule leaves at most 1e-6 s; the row sets none of its own.
        longest = await region.evaluate(
            "(el) => Math.max("
            "...getComputedStyle(el).transitionDuration.split(',').map(parseFloat))"
        )
        assert longest <= 0.001, longest
        await page.get_by_test_id("demo-row-toggle").click()
        await expect(page.get_by_test_id("demo-details-text")).to_be_visible()
        assert await page.evaluate("document.getAnimations().length") == 0
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
        # Pressed state is more than colour: a check mark and bold words.
        await expect(chip("all").locator("svg")).to_have_count(1)
        await expect(chip("open").locator("svg")).to_have_count(0)
        assert int(await chip("all").evaluate("(el) => getComputedStyle(el).fontWeight")) >= 700
        assert int(await chip("open").evaluate("(el) => getComputedStyle(el).fontWeight")) < 700

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
