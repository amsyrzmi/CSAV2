import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

/**
 * app/themes.css is the only place a colour literal may live. Everything the console owns
 * reads var(--fs-*) tokens (or the Tailwind bridge classes built on them), so a theme can
 * change every colour without touching a component.
 *
 * Paths are relative to the web root. A directory ends in "/". Every listed path must exist
 * (a renamed file must be renamed here too); a directory must hold at least one scanned file
 * unless it is listed in MAY_BE_EMPTY.
 */
const OWNED = [
  "components/console/ui.tsx",
  "components/console/fs/",
  "components/console/inbox/",
  "components/AppShell.tsx",
  "components/console/shell/",
  "components/console/ServicesWorkspace.tsx",
  "components/console/RuntimeWorkspace.tsx",
  "components/console/InboxWorkspace.tsx",
  "components/console/SandboxWorkspace.tsx",
  "components/console/chat/",
  "components/console/overview/",
  "components/console/kit/",
  "lib/chips.ts",
  "app/globals.css",
  "app/layout.tsx",
  "app/(admin)/layout.tsx",
  "app/portal/layout.tsx",
  "app/(admin)/page.tsx",
  "app/portal/page.tsx",
  "app/(auth)/login/page.tsx",
  "components/ui/auth-card.tsx",
  "components/ui/FourStacksLogo.tsx",
  "components/console/PasswordPanel.tsx",
  "app/(admin)/password/page.tsx",
  "app/portal/password/page.tsx",
  "app/loading.tsx",
  "app/(admin)/loading.tsx",
  "app/portal/loading.tsx",
  "app/(admin)/chat/page.tsx",
  "app/portal/chat/page.tsx",
];

/** OWNED directories allowed to hold no scanned file. None today. */
const MAY_BE_EMPTY = [];

/**
 * Exempt until converted. An entry must name an OWNED path that still has a literal: a stale
 * entry fails the test so the exemption cannot outlive the work it waited for. None left.
 */
const PENDING = [];

const WEB = new URL("../", import.meta.url).pathname;
const PALETTE =
  "red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone";
const UTILITY = "bg|text|border|ring|outline|from|via|to|fill|stroke|divide|decoration|accent|caret|shadow";

// Every CSS named colour except the keywords that carry no colour of their own.
const NAMED = [
  "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure", "beige", "bisque", "black", "blanchedalmond",
  "blue", "blueviolet", "brown", "burlywood", "cadetblue", "chartreuse", "chocolate", "coral",
  "cornflowerblue", "cornsilk", "crimson", "cyan", "darkblue", "darkcyan", "darkgoldenrod", "darkgray",
  "darkgreen", "darkgrey", "darkkhaki", "darkmagenta", "darkolivegreen", "darkorange", "darkorchid",
  "darkred", "darksalmon", "darkseagreen", "darkslateblue", "darkslategray", "darkslategrey",
  "darkturquoise", "darkviolet", "deeppink", "deepskyblue", "dimgray", "dimgrey", "dodgerblue",
  "firebrick", "floralwhite", "forestgreen", "fuchsia", "gainsboro", "ghostwhite", "gold", "goldenrod",
  "gray", "green", "greenyellow", "grey", "honeydew", "hotpink", "indianred", "indigo", "ivory", "khaki",
  "lavender", "lavenderblush", "lawngreen", "lemonchiffon", "lightblue", "lightcoral", "lightcyan",
  "lightgoldenrodyellow", "lightgray", "lightgreen", "lightgrey", "lightpink", "lightsalmon",
  "lightseagreen", "lightskyblue", "lightslategray", "lightslategrey", "lightsteelblue", "lightyellow",
  "lime", "limegreen", "linen", "magenta", "maroon", "mediumaquamarine", "mediumblue", "mediumorchid",
  "mediumpurple", "mediumseagreen", "mediumslateblue", "mediumspringgreen", "mediumturquoise",
  "mediumvioletred", "midnightblue", "mintcream", "mistyrose", "moccasin", "navajowhite", "navy",
  "oldlace", "olive", "olivedrab", "orange", "orangered", "orchid", "palegoldenrod", "palegreen",
  "paleturquoise", "palevioletred", "papayawhip", "peachpuff", "peru", "pink", "plum", "powderblue",
  "purple", "rebeccapurple", "red", "rosybrown", "royalblue", "saddlebrown", "salmon", "sandybrown",
  "seagreen", "seashell", "sienna", "silver", "skyblue", "slateblue", "slategray", "slategrey", "snow",
  "springgreen", "steelblue", "tan", "teal", "thistle", "tomato", "turquoise", "violet", "wheat", "white",
  "whitesmoke", "yellow", "yellowgreen",
].join("|");
const COLOUR_ATTRS =
  "fill|stroke|color|stopColor|stop-color|floodColor|flood-color|lightingColor|lighting-color|background|bgcolor";

// [what, pattern]. Applied to each line with comments removed.
const RULES = [
  ["hex colour", /#[0-9a-fA-F]{3,8}\b/g],
  ["rgb/rgba/hsl colour", /\b(?:rgba?|hsla?)\(/g],
  ["colour function", /\b(?:oklch|oklab|lab|lch|hwb)\(/gi],
  ["bracketed named colour", new RegExp(`\\b(?:${UTILITY})-\\[(?:color:)?(?:${NAMED})\\]`, "gi")],
  ["named colour attribute", new RegExp(`\\b(?:${COLOUR_ATTRS})=\\{?\\s*["'\`](?:${NAMED})["'\`]`, "gi")],
  ["white utility", new RegExp(`\\b(?:bg|text|border|from|via|to|ring|outline|fill|stroke|divide|shadow)-white\\b`, "g")],
  ["black utility", new RegExp(`\\b(?:bg|text|border|from|via|to|ring|outline|fill|stroke|divide|shadow)-black\\b`, "g")],
  ["fixed palette utility", new RegExp(`\\b(?:${UTILITY})-(?:${PALETTE})-\\d{2,3}\\b`, "g")],
  ["url-encoded hex colour", /%23[0-9a-fA-F]{3,8}\b/g],
  ["named colour in a style", new RegExp(`:\\s*["'\`](?:${NAMED})["'\`]`, "gi")],
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

/**
 * color-mix() is a literal unless every colour it mixes is a token, transparent or currentColor:
 * `color-mix(in srgb, var(--fs-focus) 14%, transparent)` follows the theme, `color-mix(in srgb, red, x)` does not.
 */
function colourMixes(text) {
  const out = [];
  for (const match of text.matchAll(/\bcolor-mix\(/gi)) {
    let depth = 0;
    let end = match.index + match[0].length - 1;
    for (; end < text.length; end += 1) {
      if (text[end] === "(") depth += 1;
      else if (text[end] === ")" && (depth -= 1) === 0) break;
    }
    const inner = text.slice(match.index + match[0].length, end);
    const operands = inner.replace(/var\([^()]*\)/g, "").split(",").slice(1)
      .map((part) => part.replace(/\d+(?:\.\d+)?%/g, "").trim());
    if (operands.some((part) => part && !/^(?:transparent|currentcolor)$/i.test(part))) {
      out.push(text.slice(match.index, end + 1));
    }
  }
  return out;
}

function findLiterals(source, file) {
  const found = [];
  for (const [line, text] of codeLines(source, file)) {
    for (const mix of colourMixes(text)) found.push(`${file}:${line}: colour function ${mix}`);
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
    return out; // missing: the existence test reports it
  }
  for (const item of items) {
    if (item.isDirectory()) out.push(...(await expand(`${entry}${item.name}/`)));
    else if (/\.(tsx?|css)$/.test(item.name)) out.push(`${entry}${item.name}`);
  }
  return out;
}

const covers = (entry, file) => (entry.endsWith("/") ? file.startsWith(entry) : file === entry);
const pending = (file) => PENDING.some((p) => covers(p, file));

async function exists(entry) {
  try {
    await stat(path.join(WEB, entry));
    return true;
  } catch {
    return false;
  }
}

/** Literals per scanned OWNED file, PENDING ignored. */
async function scanOwned() {
  const files = [...new Set((await Promise.all(OWNED.map(expand))).flat())].sort();
  const byFile = new Map();
  for (const file of files) {
    if (file === "app/themes.css") continue;
    byFile.set(file, findLiterals(await readFile(path.join(WEB, file), "utf8"), file));
  }
  return byFile;
}

test("the scanner flags every literal kind it is meant to", () => {
  const sample = [
    'const a = "#183DA5"; const b = "#fff";',
    "c = rgb(11 18 32 / 0.05); d = rgba(0,0,0,.1); e = hsl(10 20% 30%)",
    'className="bg-white text-white border-white from-white to-white ring-white bg-amber-50 text-red-800"',
    'style={{ color: "white" }}',
    'className="bg-[white] text-[red] border-[color:navy]"',
    '<path fill="white" stroke=\'black\' /> <stop stopColor="tomato" />',
    "a = oklch(70% 0.1 200); b = lab(50 20 30); c = lch(50 30 200); d = color-mix(in srgb, red 20%, var(--fs-x))",
    'style={{ background: "rebeccapurple" }}',
  ].join("\n");
  const hits = findLiterals(sample, "x.tsx").join("\n");
  for (const needle of ["#183DA5", "#fff", "rgb(", "rgba(", "hsl(", "bg-white", "text-white", "border-white",
    "from-white", "to-white", "ring-white", "bg-amber-50", "text-red-800", ': "white"',
    "bg-[white]", "text-[red]", "border-[color:navy]", 'fill="white"', "stroke='black'", 'stopColor="tomato"',
    "oklch(", "lab(", "lch(", "color-mix(in srgb, red 20%, var(--fs-x))", ': "rebeccapurple"']) {
    assert.ok(hits.includes(needle), `${needle} not flagged:\n${hits}`);
  }
  assert.deepEqual(findLiterals("a { color: var(--fs-text); fill: currentColor; background: transparent; } /* #fff */", "x.css"), []);
  assert.deepEqual(findLiterals("// #fff and bg-white\n/* rgb(1 2 3) */\nconst x = 'bg-[var(--fs-surface)]'", "x.tsx"), []);
  // Colourless keywords, token brackets, sizes and prop values that merely look like words.
  assert.deepEqual(findLiterals([
    '<path fill="currentColor" stroke="none" /> <svg fill="none" />',
    'className="bg-[transparent] text-[13px] border-[1.5px] bg-[var(--fs-tile)] shadow-[0_1px_2px_var(--fs-shadow)]"',
    'const tone = { kind: "ok" }; <Button color="primary" /> collab(1)',
    "style={{ boxShadow: '0 0 0 4px color-mix(in srgb, var(--fs-focus) 14%, transparent)' }}",
  ].join("\n"), "x.tsx"), []);
});

test("every OWNED path exists, and every OWNED directory holds a scanned file", async () => {
  const problems = [];
  for (const entry of OWNED) {
    if (!(await exists(entry))) problems.push(`${entry}: listed in OWNED but missing`);
    else if (entry.endsWith("/") && !MAY_BE_EMPTY.includes(entry) && (await expand(entry)).length === 0) {
      problems.push(`${entry}: holds no .ts, .tsx or .css file (add it to MAY_BE_EMPTY if that is intended)`);
    }
  }
  assert.deepEqual(problems, []);
});

test("PENDING names only OWNED paths that still hold a literal", async () => {
  const byFile = await scanOwned();
  const stale = [];
  for (const entry of PENDING) {
    const files = [...byFile.keys()].filter((file) => covers(entry, file));
    if (files.length === 0) stale.push(`${entry}: not an OWNED file; remove it from PENDING`);
    else if (files.every((file) => byFile.get(file).length === 0)) {
      stale.push(`${entry}: already uses theme tokens only; remove it from PENDING`);
    }
  }
  assert.deepEqual(stale, []);
});

test("owned files use theme tokens only; app/themes.css is the one place for colour literals", async () => {
  const failures = [];
  for (const [file, found] of await scanOwned()) {
    if (!pending(file)) failures.push(...found);
  }
  assert.deepEqual(failures, [], `colour literals outside app/themes.css:\n${failures.join("\n")}`);
});
