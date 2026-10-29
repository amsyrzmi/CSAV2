import assert from "node:assert/strict";
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

test("avatarPlan: only idle-like states animate, and only when motion is allowed", () => {
  const plan = (state, allowed) => avatarPlan(state, allowed);
  for (const state of ["idle", "working", "wake"]) {
    assert.deepEqual(plan(state, true), { animate: true, dimmed: false, frameSet: "idle" }, state);
    assert.deepEqual(plan(state, false), { animate: false, dimmed: false, frameSet: "idle" }, state);
  }
  for (const state of ["still", "pending"]) {
    for (const allowed of [true, false]) {
      assert.deepEqual(plan(state, allowed), { animate: false, dimmed: true, frameSet: "idle" }, state);
    }
  }
  assert.throws(() => avatarPlan("sleeping", true), /state/i);
});

test("avatarPlan: rest is frame 0, undimmed, and never animates (all motion combinations)", () => {
  for (const allowed of [true, false]) {
    assert.deepEqual(avatarPlan("rest", allowed), { animate: false, dimmed: false, frameSet: "idle" });
  }
  assert.equal(frameSetFor("rest"), "idle");
});

test("working and wake fall back to the idle frames until working frames exist", () => {
  // Task 12 replaces these two expectations when it adds the working frame set.
  assert.equal(frameSetFor("idle"), "idle");
  assert.equal(frameSetFor("working"), "idle");
  assert.equal(frameSetFor("wake"), "idle");
  assert.equal(frameSetFor("still"), "idle");
  assert.equal(frameSetFor("pending"), "idle");
  for (const id of AGENT_IDS) assert.ok(PIXEL_DATA[id].idle, id);
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
function hsl(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  return { s: d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1)), l };
}

/** The undimmed body colour: the most saturated palette entry with lightness 0.3 to 0.7. */
function bodyColour(id) {
  const mids = PIXEL_DATA[id].palette
    .filter((c) => c !== "#15203A" && c !== "#FFFFFF")
    .map((c) => ({ c, ...hsl(c) }))
    .filter((e) => e.l >= 0.3 && e.l <= 0.7);
  assert.ok(mids.length > 0, `${id} has a mid-tone`);
  return mids.reduce((a, b) => (b.s > a.s ? b : a)).c;
}

test("every sprite body reaches 3:1 against the avatar tile in every theme", () => {
  const worst = [];
  for (const theme of ["cobalt", "night", "bureau"]) {
    const tile = tileOf(theme, "--fs-sprite-tile");
    for (const id of AGENT_IDS) {
      const value = ratio(bodyColour(id), tile);
      worst.push(`${theme} ${id} ${value.toFixed(2)}`);
      assert.ok(value >= 3, `${id} on the ${theme} tile ${tile}: ${value.toFixed(2)}:1`);
    }
  }
  if (process.env.SHOW_SPRITE_CONTRAST) console.log(worst.join("\n"));
});
