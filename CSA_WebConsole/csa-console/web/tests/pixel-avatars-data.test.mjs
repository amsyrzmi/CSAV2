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
const dataSource = await readFile(new URL("../lib/pixel-avatars-data.ts", import.meta.url), "utf8");
const data = await import(moduleUrl(dataSource));
const { AGENT_IDS } = await import(moduleUrl(
  await readFile(new URL("../lib/agent-avatars.ts", import.meta.url), "utf8"),
));
const { PIXEL_DATA, PIXEL_GRIDS } = data;

// The hue each agent must keep (degrees); mirrors the design spec, section 6.
const HUE = {
  support: 225, knowledge: 172, orders: 28, tickets: 50, ecommerce: 145,
  invoices: 200, assistant: 268, hr: 330, email: 5, subscriptions: 95, bookings: 305,
};

// Test-only decoder of the documented format (the real one ships with AgentAvatar).
function decodeFrame(frame) {
  return frame.split("/").map((row) => {
    const cells = [];
    const tokens = [...row.matchAll(/([.a-l])(\d+)/g)];
    assert.equal(tokens.map((t) => t[0]).join(""), row, `row is only symbol+count tokens: ${row}`);
    for (const [, symbol, count] of tokens) {
      assert.ok(Number(count) >= 1, "counts are at least 1");
      const value = symbol === "." ? -1 : symbol.charCodeAt(0) - 97;
      for (let i = 0; i < Number(count); i += 1) cells.push(value);
    }
    return cells;
  });
}

function hsl(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  let h;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return { h: (h * 60 + 360) % 360, s, l };
}

test("the grid sizes are fixed", () => {
  assert.deepEqual(PIXEL_GRIDS, { 32: 32, 48: 48 });
});

test("every agent id has data and nothing else does", () => {
  assert.deepEqual(Object.keys(PIXEL_DATA).sort(), [...AGENT_IDS].sort());
});

test("every frame decodes to a full grid of known palette cells", () => {
  for (const id of AGENT_IDS) {
    const { palette, idle } = PIXEL_DATA[id];
    assert.deepEqual(Object.keys(idle).sort(), ["32", "48"], id);
    for (const size of ["32", "48"]) {
      assert.equal(idle[size].length, 4, `${id} ${size} has four idle frames`);
      for (const frame of idle[size]) {
        const rows = decodeFrame(frame);
        const n = PIXEL_GRIDS[size];
        assert.equal(rows.length, n, `${id} ${size} rows`);
        for (const row of rows) {
          assert.equal(row.length, n, `${id} ${size} row width`);
          for (const cell of row) assert.ok(cell >= -1 && cell < palette.length, `${id} cell ${cell}`);
        }
        assert.ok(rows.some((row) => row.some((cell) => cell >= 0)), `${id} ${size} is not blank`);
      }
    }
  }
});

test("palettes are short, upper-case, keep the outline and white eyes, and differ per agent", () => {
  const seen = new Map();
  for (const id of AGENT_IDS) {
    const { palette } = PIXEL_DATA[id];
    assert.ok(palette.length <= 12, `${id} has ${palette.length} entries`);
    assert.equal(new Set(palette).size, palette.length, `${id} palette has duplicates`);
    for (const colour of palette) assert.match(colour, /^#[0-9A-F]{6}$/, id);
    assert.ok(palette.includes("#15203A"), `${id} keeps the outline`);
    assert.ok(palette.includes("#FFFFFF"), `${id} keeps white eyes`);
    const key = [...palette].sort().join(",");
    assert.ok(!seen.has(key), `${id} palette equals ${seen.get(key)}`);
    seen.set(key, id);
  }
});

test("every palette entry is used by some frame", () => {
  for (const id of AGENT_IDS) {
    const { palette, idle, working } = PIXEL_DATA[id];
    const used = new Set();
    for (const frames of [...Object.values(idle), ...Object.values(working)])
      for (const frame of frames) for (const row of decodeFrame(frame)) for (const c of row) used.add(c);
    for (let i = 0; i < palette.length; i += 1) assert.ok(used.has(i), `${id} entry ${i} unused`);
  }
});

test("the four idle frames of every agent animate", () => {
  for (const id of AGENT_IDS) {
    for (const size of ["32", "48"]) {
      const frames = PIXEL_DATA[id].idle[size];
      assert.ok(new Set(frames).size >= 2, `${id} ${size} frames are all identical`);
      // Frame 0 is the rest pose; the loop leaves it and comes back through frames 1 and 3.
      assert.notEqual(frames[1], frames[0], `${id} ${size} frame 1 equals the rest pose`);
      assert.notEqual(frames[3], frames[0], `${id} ${size} frame 3 equals the rest pose`);
    }
  }
});

test("the motion keeps the sprite inside the grid (nothing is cropped)", () => {
  for (const id of AGENT_IDS) {
    for (const size of ["32", "48"]) {
      const counts = PIXEL_DATA[id].idle[size].map((frame) =>
        decodeFrame(frame).flat().filter((c) => c >= 0).length);
      const still = counts[0];
      for (const count of counts) assert.ok(count > still * 0.85, `${id} ${size} loses pixels: ${counts}`);
    }
  }
});

test("each palette carries its agent's hue (most saturated mid-tone within 20 degrees)", () => {
  for (const id of AGENT_IDS) {
    const mids = PIXEL_DATA[id].palette
      .filter((c) => c !== "#15203A" && c !== "#FFFFFF")
      .map((c) => ({ c, ...hsl(c) }))
      .filter((e) => e.l >= 0.3 && e.l <= 0.7);
    assert.ok(mids.length > 0, `${id} has a mid-tone`);
    const top = mids.reduce((a, b) => (b.s > a.s ? b : a));
    const diff = Math.abs(((top.h - HUE[id] + 540) % 360) - 180);
    assert.ok(diff <= 20, `${id}: ${top.c} hue ${top.h.toFixed(0)} vs ${HUE[id]}`);
  }
});

test("the data module stays small and data-only", () => {
  assert.ok(Buffer.byteLength(dataSource) < 150 * 1024, `${Buffer.byteLength(dataSource)} bytes`);
  assert.match(dataSource, /^\/\*\*[\s\S]*?\*\//, "starts with the format header");
  assert.equal(/\bfunction\b|=>/.test(dataSource), false, "no logic in the data module");
});

// ---- Working loop: eight frames on the 48 grid, the creature at work with one small prop ----
const filled = (frame) => decodeFrame(frame).flat().filter((c) => c >= 0).length;

test("every agent has eight 48 x 48 working frames, and only on the 48 grid", () => {
  for (const id of AGENT_IDS) {
    const { palette, working } = PIXEL_DATA[id];
    assert.ok(working, `${id} has working frames`);
    assert.deepEqual(Object.keys(working), ["48"], `${id} working frames exist only for the 48 grid`);
    assert.equal(working["48"].length, 8, `${id} has eight working frames`);
    for (const frame of working["48"]) {
      const rows = decodeFrame(frame);
      assert.equal(rows.length, 48, `${id} working rows`);
      for (const row of rows) {
        assert.equal(row.length, 48, `${id} working row width`);
        for (const cell of row) assert.ok(cell >= -1 && cell < palette.length, `${id} cell ${cell}`);
      }
    }
  }
});

test("the working frames move: neighbours differ, and at least six frames are distinct", () => {
  for (const id of AGENT_IDS) {
    const frames = PIXEL_DATA[id].working["48"];
    for (let i = 0; i < frames.length; i += 1) {
      const next = (i + 1) % frames.length;
      assert.notEqual(frames[i], frames[next], `${id} working frames ${i} and ${next} are equal`);
    }
    assert.ok(new Set(frames).size >= 6, `${id} has only ${new Set(frames).size} distinct frames`);
    assert.ok(!frames.includes(PIXEL_DATA[id].idle["48"][0]), `${id} a working frame is the idle rest`);
  }
});

test("the working frames carry a prop: at least 15 more cells than the rest pose in 6 of 8", () => {
  for (const id of AGENT_IDS) {
    const rest = filled(PIXEL_DATA[id].idle["48"][0]);
    const counts = PIXEL_DATA[id].working["48"].map(filled);
    const withProp = counts.filter((count) => count - rest >= 15).length;
    assert.ok(withProp >= 6, `${id}: rest ${rest}, working ${counts}`);
  }
});

test("the creature is never cropped while it works (each frame keeps 90% of the rest cells)", () => {
  for (const id of AGENT_IDS) {
    const rest = filled(PIXEL_DATA[id].idle["48"][0]);
    for (const count of PIXEL_DATA[id].working["48"].map(filled)) {
      assert.ok(count >= rest * 0.9, `${id}: ${count} cells vs rest ${rest}`);
    }
  }
});
