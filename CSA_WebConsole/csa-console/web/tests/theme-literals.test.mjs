import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

/**
 * app/themes.css is the only place a colour literal may live. Everything the console owns
 * reads var(--fs-*) tokens (or the Tailwind bridge classes built on them), so a theme can
 * change every colour without touching a component.
 *
 * Paths are relative to the web root. A directory ends in "/". Missing paths are skipped:
 * later tasks add components/console/shell/ and the like.
 */
const OWNED = [
  "components/console/ui.tsx",
  "components/console/fs/",
  "components/console/inbox/",
  "components/AppShell.tsx",
  "components/console/shell/",
  "components/console/ServicesWorkspace.tsx",
  "components/console/RuntimeWorkspace.tsx",
  "app/globals.css",
];

/** Exempt until the task that converts them; each later task removes its entry. */
const PENDING = [
  "components/console/RuntimeWorkspace.tsx", // Task 15
  "components/console/ServicesWorkspace.tsx", // Task 16
];

const WEB = new URL("../", import.meta.url).pathname;
const PALETTE =
  "red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone";
const UTILITY = "bg|text|border|ring|outline|from|via|to|fill|stroke|divide|decoration|accent|caret|shadow";

// [what, pattern]. Applied to each line with comments removed.
const RULES = [
  ["hex colour", /#[0-9a-fA-F]{3,8}\b/g],
  ["rgb/rgba/hsl colour", /\b(?:rgba?|hsla?)\(/g],
  ["white utility", new RegExp(`\\b(?:bg|text|border|from|via|to|ring|outline|fill|stroke|divide|shadow)-white\\b`, "g")],
  ["black utility", new RegExp(`\\b(?:bg|text|border|from|via|to|ring|outline|fill|stroke|divide|shadow)-black\\b`, "g")],
  ["fixed palette utility", new RegExp(`\\b(?:${UTILITY})-(?:${PALETTE})-\\d{2,3}\\b`, "g")],
  ["url-encoded hex colour", /%23[0-9a-fA-F]{3,8}\b/g],
  ["named colour in a style", /:\s*["'](?:white|black)["']/g],
  ["named colour in css", /(?:^|[\s:,(])(?:white|black)(?=[\s;,)!]|$)/g],
];

/** Lines of `source` as [number, text] with comments blanked out. */
function codeLines(source, file) {
  const css = file.endsWith(".css");
  const noBlocks = source.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  return noBlocks.split("\n").map((text, i) => {
    const trimmed = text.trim();
    const comment = !css && (trimmed.startsWith("//") || trimmed.startsWith("*"));
    return [i + 1, comment ? "" : text];
  });
}

function findLiterals(source, file) {
  const found = [];
  for (const [line, text] of codeLines(source, file)) {
    for (const [what, pattern] of RULES) {
      // The css-only rule would match prose in tsx, and the tsx-only rule has no place in css.
      if (what === "named colour in css" && !file.endsWith(".css")) continue;
      for (const match of text.matchAll(pattern)) found.push(`${file}:${line}: ${what} ${match[0].trim()}`);
    }
  }
  return found;
}

async function expand(entry) {
  if (!entry.endsWith("/")) return [entry];
  const out = [];
  let items;
  try {
    items = await readdir(path.join(WEB, entry), { withFileTypes: true });
  } catch {
    return out; // not created yet
  }
  for (const item of items) {
    if (item.isDirectory()) out.push(...(await expand(`${entry}${item.name}/`)));
    else if (/\.(tsx?|css)$/.test(item.name)) out.push(`${entry}${item.name}`);
  }
  return out;
}

const pending = (file) => PENDING.some((p) => (p.endsWith("/") ? file.startsWith(p) : file === p));

test("the scanner flags every literal kind it is meant to", () => {
  const sample = [
    'const a = "#183DA5"; const b = "#fff";',
    "c = rgb(11 18 32 / 0.05); d = rgba(0,0,0,.1); e = hsl(10 20% 30%)",
    'className="bg-white text-white border-white from-white to-white ring-white bg-amber-50 text-red-800"',
    'style={{ color: "white" }}',
  ].join("\n");
  const hits = findLiterals(sample, "x.tsx").join("\n");
  for (const needle of ["#183DA5", "#fff", "rgb(", "rgba(", "hsl(", "bg-white", "text-white", "border-white",
    "from-white", "to-white", "ring-white", "bg-amber-50", "text-red-800", ': "white"']) {
    assert.ok(hits.includes(needle), `${needle} not flagged:\n${hits}`);
  }
  assert.deepEqual(findLiterals("a { color: var(--fs-text); fill: currentColor; background: transparent; } /* #fff */", "x.css"), []);
  assert.deepEqual(findLiterals("// #fff and bg-white\n/* rgb(1 2 3) */\nconst x = 'bg-[var(--fs-surface)]'", "x.tsx"), []);
});

test("owned files use theme tokens only; app/themes.css is the one place for colour literals", async () => {
  const files = [...new Set((await Promise.all(OWNED.map(expand))).flat())].sort();
  const failures = [];
  for (const file of files) {
    if (file === "app/themes.css" || pending(file)) continue;
    let source;
    try {
      source = await readFile(path.join(WEB, file), "utf8");
    } catch {
      continue; // listed but not created yet
    }
    failures.push(...findLiterals(source, file));
  }
  assert.deepEqual(failures, [], `colour literals outside app/themes.css:\n${failures.join("\n")}`);
});
