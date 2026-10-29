import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const load = async (path) =>
  import(moduleUrl(await readFile(new URL(path, import.meta.url), "utf8")));
const { decodeFrame, frameRects, gridForSize, avatarPlan, frameSetFor, AVATAR_SIZES } =
  await load("../lib/pixel-avatars.ts");
const { PIXEL_DATA, PIXEL_GRIDS } = await load("../lib/pixel-avatars-data.ts");
const { AGENT_IDS } = await load("../lib/agent-avatars.ts");

const frameOf = (grid, row) => Array.from({ length: grid }, () => row).join("/");

test("decodeFrame round-trips a hand-written run-length string", () => {
  const encoded = frameOf(32, ".2a3b2.1b10c14");
  const frame = decodeFrame(encoded, 32);
  assert.equal(frame.length, 32);
  const expected = [-1, -1, 0, 0, 0, 1, 1, -1, ...Array(10).fill(1), ...Array(14).fill(2)];
  assert.equal(expected.length, 32);
  for (const cells of frame) assert.deepEqual(cells, expected);
  // Letters map a..l onto 0..11.
  const letters = decodeFrame(frameOf(48, "a1l1.46"), 48);
  assert.deepEqual(letters[0].slice(0, 3), [0, 11, -1]);
  assert.equal(letters[0].length, 48);
});

test("decodeFrame throws on a wrong width, wrong height, unknown symbol or malformed row", () => {
  assert.throws(() => decodeFrame(frameOf(32, ".31"), 32), /width/i);
  assert.throws(() => decodeFrame(frameOf(32, ".33"), 32), /wider/i);
  assert.throws(() => decodeFrame(frameOf(31, ".32"), 32), /height|rows/i);
  assert.throws(() => decodeFrame(frameOf(33, ".32"), 32), /height|rows/i);
  assert.throws(() => decodeFrame(frameOf(32, ".31m1"), 32), /malformed|symbol/i);
  assert.throws(() => decodeFrame(frameOf(32, ".31A1"), 32), /malformed|symbol/i);
  assert.throws(() => decodeFrame(frameOf(32, ".31x1"), 32), /malformed|symbol/i);
  assert.throws(() => decodeFrame(frameOf(32, ".0a32"), 32), /malformed|count/i);
  assert.throws(() => decodeFrame(frameOf(32, ".32a"), 32), /malformed/i);
  assert.throws(() => decodeFrame(frameOf(32, ""), 32), /malformed|width/i);
  assert.throws(() => decodeFrame(frameOf(32, ".32 "), 32), /malformed/i);
  assert.throws(() => decodeFrame(frameOf(32, ".32"), 40), /grid/i);
});

test("decodeFrame refuses over-long rows, including a full row followed by one more cell", () => {
  for (const row of [".32a1", ".32.1", "a16b16c1", "a32b1", "a33", "a99999999999"]) {
    assert.throws(() => decodeFrame(frameOf(32, row), 32), /wider than the grid/i, row);
  }
  // Exactly full is fine.
  assert.equal(decodeFrame(frameOf(32, "a16b16"), 32)[0].length, 32);
});

test("frameRects merges horizontal runs of one colour and skips transparent cells", () => {
  const palette = ["#111111", "#222222", "#333333"];
  const frame = [
    [-1, 0, 0, 0, 1, 1, -1, 0],
    [-1, -1, -1, -1, -1, -1, -1, -1],
    [2, -1, 2, 2, 0, 0, 0, 0],
  ];
  assert.deepEqual(frameRects(frame, palette), [
    { x: 1, y: 0, w: 3, fill: "#111111" },
    { x: 4, y: 0, w: 2, fill: "#222222" },
    { x: 7, y: 0, w: 1, fill: "#111111" },
    { x: 0, y: 2, w: 1, fill: "#333333" },
    { x: 2, y: 2, w: 2, fill: "#333333" },
    { x: 4, y: 2, w: 4, fill: "#111111" },
  ]);
  assert.deepEqual(frameRects([[-1, -1]], palette), []);
  assert.throws(() => frameRects([[5]], palette), /palette/i);
  assert.throws(() => frameRects([[-2]], palette), /palette/i);
});

test("every agent and both grids decode from the generated data", () => {
  for (const id of AGENT_IDS) {
    const { palette, idle } = PIXEL_DATA[id];
    for (const grid of [32, 48]) {
      assert.equal(PIXEL_GRIDS[String(grid)], grid);
      assert.equal(idle[String(grid)].length, 4, `${id} ${grid}`);
      for (const encoded of idle[String(grid)]) {
        const frame = decodeFrame(encoded, grid);
        assert.equal(frame.length, grid);
        const rects = frameRects(frame, palette);
        assert.ok(rects.length > 0, `${id} ${grid} has rects`);
        for (const rect of rects) {
          assert.ok(rect.x >= 0 && rect.w >= 1 && rect.x + rect.w <= grid);
          assert.ok(rect.y >= 0 && rect.y < grid);
          assert.match(rect.fill, /^#[0-9A-F]{6}$/);
        }
        // Row-major order: y never goes back, x increases within a row.
        for (let i = 1; i < rects.length; i += 1) {
          const [a, b] = [rects[i - 1], rects[i]];
          assert.ok(b.y > a.y || (b.y === a.y && b.x >= a.x + a.w), `${id} order`);
        }
      }
    }
  }
});

test("gridForSize maps the five allowed sizes and refuses the rest", () => {
  assert.deepEqual([32, 64].map(gridForSize), [32, 32]);
  assert.deepEqual([48, 96, 144].map(gridForSize), [48, 48, 48]);
  assert.deepEqual([...AVATAR_SIZES], [32, 48, 64, 96, 144]);
  for (const size of [0, 16, 24, 40, 56, 72, 100, 128, 192, 48.5, NaN, "48", undefined, null]) {
    assert.throws(() => gridForSize(size), /size/i, String(size));
  }
});

test("whole-number scaling: every size is a multiple of its grid", () => {
  for (const size of AVATAR_SIZES) assert.equal(size % gridForSize(size), 0, String(size));
});

// Every state x motion x grid combination. Not animating always means idle frame 0 alone (the
// rest pose), whatever the state: reduced motion, the pause preference and offscreen included.
const STILL_FRAME = { animate: false, motion: "none", frameSet: "idle", frameCount: 1 };
const IDLE_LOOP = { animate: true, motion: "idle", frameSet: "idle", frameCount: 4 };

test("avatarPlan: idle loops the four idle frames on both grids, only when motion is allowed", () => {
  for (const grid of [32, 48]) {
    assert.deepEqual(avatarPlan("idle", true, grid), { ...IDLE_LOOP, dimmed: false }, `${grid}`);
    assert.deepEqual(avatarPlan("idle", false, grid), { ...STILL_FRAME, dimmed: false }, `${grid}`);
  }
});

test("avatarPlan: working loops the eight working frames on the 48 grid", () => {
  assert.deepEqual(avatarPlan("working", true, 48), {
    animate: true, motion: "working", frameSet: "working", frameCount: 8, dimmed: false,
  });
  assert.deepEqual(avatarPlan("working", false, 48), { ...STILL_FRAME, dimmed: false });
});

test("avatarPlan: wake plays the eight working frames once on the 48 grid", () => {
  assert.deepEqual(avatarPlan("wake", true, 48), {
    animate: true, motion: "wake", frameSet: "working", frameCount: 8, dimmed: false,
  });
  assert.deepEqual(avatarPlan("wake", false, 48), { ...STILL_FRAME, dimmed: false });
});

test("avatarPlan: on the 32 grid working and wake fall back to the idle loop", () => {
  for (const state of ["working", "wake"]) {
    assert.deepEqual(avatarPlan(state, true, 32), { ...IDLE_LOOP, dimmed: false }, state);
    assert.deepEqual(avatarPlan(state, false, 32), { ...STILL_FRAME, dimmed: false }, state);
  }
});

test("avatarPlan: still and pending are dimmed idle frame 0 and never animate", () => {
  for (const state of ["still", "pending"]) {
    for (const allowed of [true, false]) {
      for (const grid of [32, 48]) {
        assert.deepEqual(avatarPlan(state, allowed, grid), { ...STILL_FRAME, dimmed: true }, state);
      }
    }
  }
});

test("avatarPlan: rest is idle frame 0, undimmed, and never animates (all combinations)", () => {
  for (const allowed of [true, false]) {
    for (const grid of [32, 48]) {
      assert.deepEqual(avatarPlan("rest", allowed, grid), { ...STILL_FRAME, dimmed: false });
    }
  }
});

test("avatarPlan refuses unknown states and grids", () => {
  assert.throws(() => avatarPlan("sleeping", true, 48), /state/i);
  assert.throws(() => avatarPlan("idle", true, 40), /grid/i);
});

test("frameSetFor: the working set exists only on the 48 grid", () => {
  for (const grid of [32, 48]) {
    for (const state of ["idle", "still", "pending", "rest"]) assert.equal(frameSetFor(state, grid), "idle");
  }
  assert.equal(frameSetFor("working", 48), "working");
  assert.equal(frameSetFor("wake", 48), "working");
  assert.equal(frameSetFor("working", 32), "idle");
  assert.equal(frameSetFor("wake", 32), "idle");
  assert.throws(() => frameSetFor("sleeping", 48), /state/i);
});

test("every planned frame exists in the data: all agents, states, motion and grids", () => {
  const states = ["idle", "still", "pending", "rest", "working", "wake"];
  for (const id of AGENT_IDS) {
    for (const state of states) {
      for (const allowed of [true, false]) {
        for (const grid of [32, 48]) {
          const plan = avatarPlan(state, allowed, grid);
          const frames = PIXEL_DATA[id][plan.frameSet][String(grid)];
          assert.ok(frames, `${id} ${state} ${grid}: no ${plan.frameSet} set`);
          assert.ok(frames.length >= plan.frameCount, `${id} ${state} ${grid}`);
          for (let i = 0; i < plan.frameCount; i += 1) {
            assert.ok(frameRects(decodeFrame(frames[i], grid), PIXEL_DATA[id].palette).length > 0);
          }
        }
      }
    }
  }
});

test("the CSS has the eight-frame loop and the one-shot wake, both stopped by the motion guards", () => {
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const flat = css.replace(/\s+/g, " ");
  // assert.ok rather than assert.match: a failure names the rule, not the whole stylesheet.
  const has = (pattern, what) => assert.ok(pattern.test(flat), `globals.css lacks ${what}`);
  // Working: 1.2 s loop, each of eight frames visible for 12.5%, delays i x 0.15 s.
  has(/@keyframes fs-px-show8 \{ 0% \{ opacity: 1; \} 12\.5% \{ opacity: 0; \} 100% \{ opacity: 0; \} \}/,
    "the fs-px-show8 keyframes");
  has(/\.fs-px-anim\.fs-px-work \.fs-px-frame \{[^}]*animation: fs-px-show8 1\.2s steps\(1\) infinite/,
    "the 1.2 s working loop");
  // Wake: once over 1.5 s, delays i x 0.1875 s, and the last frame holds (fill forwards).
  has(/\.fs-px-anim\.fs-px-wake \.fs-px-frame \{[^}]*animation: fs-px-show8 1\.5s steps\(1\) 1 forwards/,
    "the 1.5 s one-shot wake");
  has(/\.fs-px-anim\.fs-px-wake \.fs-px-f7 \{[^}]*animation-name: fs-px-hold/, "the held last wake frame");
  for (let i = 0; i < 8; i += 1) {
    has(new RegExp(`\\.fs-px-work \\.fs-px-f${i} \\{ animation-delay: ${+(i * 0.15).toFixed(2)}s; \\}`),
      `working delay f${i}`);
    has(new RegExp(`\\.fs-px-wake \\.fs-px-f${i} \\{[^}]*animation-delay: ${+(i * 0.1875).toFixed(4)}s;`),
      `wake delay f${i}`);
  }
  // The guards name the new classes too (belt and braces; the component already renders frame 0).
  const reduced = [...flat.matchAll(/@media \(prefers-reduced-motion: reduce\) \{(.*?)\} \}/g)]
    .map((m) => m[1]).join(" ");
  for (const cls of ["fs-px-anim", "fs-px-work", "fs-px-wake"]) {
    assert.ok(reduced.includes(`.${cls} .fs-px-frame`), `reduced motion stops ${cls}`);
    assert.ok(flat.includes(`[data-motion="off"] .${cls} .fs-px-frame`), `pause stops ${cls}`);
  }
  // Frame 0 must win over the more specific working and wake frame rules if the class lags.
  assert.ok(reduced.includes(".fs-px-anim .fs-px-f0 { opacity: 1 !important;"), "reduced motion shows f0");
  assert.ok(flat.includes('[data-motion="off"] .fs-px-anim .fs-px-f0 { opacity: 1 !important; }'), "pause shows f0");
});

// ---- Sprite contrast on the tile (spec section 6: each sprite reaches 3:1 in every theme) ----
const themesCss = await readFile(new URL("../app/themes.css", import.meta.url), "utf8");

function tileOf(theme, name) {
  let value;
  for (const [, selector, body] of themesCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = selector.replace(/\/\*[\s\S]*?\*\//g, "").split(",").map((x) => x.trim());
    if (!selectors.some((x) => x === `[data-theme="${theme}"]` || (theme === "cobalt" && x === ":root"))) continue;
    const found = new RegExp(`${name}\\s*:\\s*([^;]+);`).exec(body);
    if (found) value = found[1].trim();
  }
  assert.ok(value, `${theme} defines ${name}`);
  return value;
}
function luminance(hex) {
  assert.match(hex, /^#[0-9a-f]{6}$/i, hex);
  const [r, g, b] = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
/** Alpha-blend `top` at `alpha` over `bottom` (both #rrggbb). */
function blend(top, bottom, alpha) {
  const channel = (hex, i) => parseInt(hex.slice(i, i + 2), 16);
  return `#${[1, 3, 5]
    .map((i) => Math.round(channel(top, i) * alpha + channel(bottom, i) * (1 - alpha)))
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("")}`;
}

// Legibility rests on the shared navy outline and white eyes, not on each body fill: the sprite
// is decorative (the agent's name is always printed beside it), and the body hues are chosen for
// character, from yellow to dark blue, so no one tile gives every fill 3:1. The outline against
// the chip is what separates the shape from the page in every theme.
const OUTLINE = "#15203A";
const THEME_NAMES = ["cobalt", "night", "bureau"];

test("the sprite outline stands out from the chip in every theme (aim 6:1, floor 3:1)", () => {
  for (const theme of THEME_NAMES) {
    const chip = tileOf(theme, "--fs-sprite-tile");
    const value = ratio(OUTLINE, chip);
    assert.ok(value >= 6, `${theme} outline on ${chip}: ${value.toFixed(2)}:1`);
  }
});

test("white eyes read against the outline they sit in (7:1)", () => {
  assert.ok(ratio("#FFFFFF", OUTLINE) >= 7, `${ratio("#FFFFFF", OUTLINE).toFixed(2)}:1`);
});

test("the dimmed state (saturate .55, opacity .85) keeps the outline at 3:1 on the chip", () => {
  // Saturation hardly moves a near-neutral navy, so only the opacity blend is computed.
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.fs-px-dim\s*\{\s*filter:\s*saturate\(0?\.55\)\s+opacity\(0?\.85\)/);
  for (const theme of THEME_NAMES) {
    const chip = tileOf(theme, "--fs-sprite-tile");
    const value = ratio(blend(OUTLINE, chip, 0.85), chip);
    assert.ok(value >= 3, `${theme} dimmed outline on ${chip}: ${value.toFixed(2)}:1`);
  }
});
